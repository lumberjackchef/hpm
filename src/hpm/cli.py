"""CLI subcommands for hpm."""

from __future__ import annotations

import os
import sys
import webbrowser
from typing import Any

import click

from . import answer as answer_module
from . import config, daily, summarize
from . import dashboard as dashboard_module
from . import db as db_module
from . import decay as decay_module
from . import sidecar as sidecar_module


@click.command()
@click.argument("text")
@click.option("--tags", "-t", multiple=True, help="Tags to attach (e.g. project:jarvis)")
@click.option("--session-id", help="Source session ID for traceability")
@click.option("--no-summarize", is_flag=True, help="Skip LLM summarization, store raw text")
def capture(text: str, tags: tuple[str, ...], session_id: str | None, no_summarize: bool) -> None:
    from . import embed
    """Capture a conversation turn: summarize, embed, and store."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)

        if no_summarize:
            content = text.strip()
        else:
            click.echo("summarizing...", err=True)
            content = summarize.summarize_turn(text)

        click.echo("embedding...", err=True)
        vector = embed.embed_text(content)

        click.echo("storing...", err=True)
        mem_id = db_module.insert_memory(
            conn,
            content=content,
            embedding=vector,
            source=config.SOURCE,
            session_id=session_id,
            tags=list(tags) if tags else None,
        )

        daily.append_to_daily_log(
            content=content,
            source=config.SOURCE,
            session_id=session_id,
            tags=list(tags) if tags else None,
        )

        click.echo(f"captured: {mem_id}")
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, show_default=True, help="Max results")
@click.option("--tags", "-t", multiple=True, help="Filter by tags")
@click.option("--mode", type=click.Choice(["hybrid", "vector", "keyword"]), default="vector",
              show_default=True, help="Search mode")
def query(query: str, limit: int, tags: tuple[str, ...], mode: str) -> None:
    from . import embed
    """Search memory with hybrid semantic + keyword retrieval."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)

        results: list[dict[str, Any]] = []

        if mode == "vector":
            click.echo("embedding query...", err=True)
            query_vec = embed.embed_text(query)
            results = db_module.query_vector(conn, query_vec, limit=limit)
        elif mode == "keyword":
            results = db_module.query_keyword(conn, query, limit=limit)
        elif mode == "hybrid":
            click.echo("embedding query...", err=True)
            query_vec = embed.embed_text(query)
            results = db_module.query_hybrid(conn, query, query_vec, limit=limit)

        # Apply tag filter client-side for now
        if tags:
            results = [
                r for r in results
                if any(t in r.get("tags", []) for t in tags)
            ]

        if not results:
            click.echo("no results found")
            return

        for i, row in enumerate(results, 1):
            raw = row.get("distance", row.get("rank", None))
            if raw is not None:
                if "distance" in row and raw > 0:
                    score = 1.0 - raw  # normalize distance to [0,1]
                elif raw < 0:
                    score = 1.0 + raw  # normalize negative rank to [0,1]
                else:
                    score = raw
            else:
                score = row.get("_combined", 0)
            click.echo(f"\n[{i}] (score: {score:.4f})")
            click.echo(f"    {row['content']}")
            src = row['source']
            source_display = ', '.join(src) if isinstance(src, list) else src
            click.echo(f"    id: {row['id']}  source: {source_display}")
            if row.get("tags"):
                click.echo(f"    tags: {', '.join(row['tags'])}")
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.command()
@click.argument("fact")
@click.option("--tags", "-t", multiple=True, help="Tags to attach (e.g. project:jarvis)")
@click.option("--session-id", help="Source session ID for traceability")
def save(fact: str, tags: tuple[str, ...], session_id: str | None) -> None:
    from . import embed
    """Save an explicit fact to memory (skips summarization)."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)

        click.echo("embedding...", err=True)
        vector = embed.embed_text(fact)

        mem_id = db_module.insert_memory(
            conn,
            content=fact.strip(),
            embedding=vector,
            source=config.SOURCE,
            session_id=session_id,
            tags=list(tags) if tags else None,
        )

        daily.append_to_daily_log(
            content=fact.strip(),
            source=config.SOURCE,
            session_id=session_id,
            tags=list(tags) if tags else None,
        )

        click.echo(f"saved: {mem_id}")
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.command()
@click.option("--once", is_flag=True, help="Run one poll cycle and exit")
@click.option("--poll-interval", default=5.0, show_default=True, help="Seconds between polls")
def sidecar(once: bool, poll_interval: float) -> None:
    """Run the Hermes state.db poller for auto-capture."""
    click.echo("starting sidecar (auto-capture daemon)")
    sidecar_module.run_sidecar(once=once, poll_interval=poll_interval)


@click.command()
@click.argument("query")
@click.option("--limit", "-l", default=5, show_default=True, help="Max results")
@click.option("--no-rerank", is_flag=True, help="Skip the cross-encoder reranker pass")
def answer(query: str, limit: int, no_rerank: bool) -> None:
    from . import embed, rerank  # lazy: heavy imports (fastembed + sentence-transformers)

    """Full recall pipeline: hybrid search → reranker → cited answer."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)

        # Tier 1: hybrid search
        click.echo("searching...", err=True)
        query_vec = embed.embed_text(query)
        results = db_module.query_hybrid(conn, query, query_vec, limit=rerank.RERANK_CANDIDATES)

        if not results:
            click.echo("I don't know based on available memories.")
            return

        # Tier 2: reranker
        if not no_rerank:
            click.echo("reranking...", err=True)
            results = rerank.rerank(query, results, keep=limit)
            rerank.unload()

        # Tier 3: cited-answer synthesis
        click.echo("synthesizing answer...", err=True)
        cited = answer_module.synthesize_answer(query, results)
        click.echo("")
        click.echo(cited)
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.command()
@click.option("--run", "do_run", is_flag=True, help="Run decay computation")
@click.option("--spot-check", is_flag=True, help="LLM spot-check on lowest-scoring entries")
def decay(do_run: bool, spot_check: bool) -> None:
    """Run memory decay evaluator and spot-check."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)

        if do_run:
            click.echo("computing decay scores...", err=True)
            updated = db_module.run_decay(conn)
            click.echo(f"updated {updated} entries", err=True)

        if spot_check:
            click.echo("running spot-check...", err=True)
            checked = decay_module.run_spot_check(conn)
            click.echo(f"checked {len(checked)} low-scoring entries", err=True)
            for e in checked:
                adj = e.get("_rating_adjustment", 0)
                if adj:
                    click.echo(
                        f"  {e['id'][:8]} score={e['decay_score']:.2f} "
                        f"(adj: {adj:+.2f})", err=True
                    )

        if not do_run and not spot_check:
            stats = db_module.store_stats(conn)
            click.echo(f"total: {stats['total']}")
            click.echo(f"oldest: {stats['oldest']}  newest: {stats['newest']}")
            click.echo(f"below eviction: {stats['entries_below_eviction']}")
            click.echo(f"sources: {', '.join(stats['sources'])}")

        conn.close()
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.command()
def status() -> None:
    """Show memory store statistics."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)
        stats = db_module.store_stats(conn)
        click.echo(f"Total entries:   {stats['total']}")
        click.echo(f"Below eviction:  {stats['entries_below_eviction']}")
        click.echo(f"Oldest:          {stats['oldest']}")
        click.echo(f"Newest:          {stats['newest']}")
        click.echo(f"Sources:         {', '.join(stats['sources'])}")
        conn.close()
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.command()
@click.option("--output", "-o", default="~/.hpm/dashboard.html",
              show_default=True, help="Output path for the HTML dashboard")
def dashboard(output: str) -> None:
    """Generate a self-contained HTML memory dashboard."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)
        path = dashboard_module.generate(conn, output_path=output)
        conn.close()
        click.echo(f"Dashboard: {path}")
        webbrowser.open(f"file://{path}")
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


@click.command()
def setup() -> None:
    """Interactively configure hpm (provider, API keys, etc.)."""
    try:
        click.echo()
        click.echo("╭─ hpm setup ──────────────────────────────────╮")
        click.echo("│                                              │")
        click.echo("│  This will configure your LLM provider and   │")
        click.echo("│  API key in ~/.hpm/.env.                     │")
        click.echo("│                                              │")
        click.echo("╰──────────────────────────────────────────────╯")
        click.echo()

        providers = {
            "opencode": {
                "label": "OpenCode Go",
                "key_var": "OPENCODE_GO_API_KEY",
                "default_model": "minimax-m2.5",
                "desc": "Best if you already have an OpenCode account.",
            },
            "anthropic": {
                "label": "Anthropic (Claude)",
                "key_var": "ANTHROPIC_API_KEY",
                "default_model": "claude-sonnet-4-20250514",
                "desc": "Native Claude API. Best for Claude Code users.",
            },
            "openai": {
                "label": "OpenAI",
                "key_var": "OPENAI_API_KEY",
                "default_model": "gpt-4o-mini",
                "desc": "Direct OpenAI API.",
            },
            "openrouter": {
                "label": "OpenRouter",
                "key_var": "OPENROUTER_API_KEY",
                "default_model": "anthropic/claude-sonnet-4",
                "desc": "Multi-provider proxy, one key for any model.",
            },
        }
        choices = list(providers.keys())
        default_idx = choices.index(config.LLM_PROVIDER) + 1

        click.echo("Select an LLM provider:")
        for i, p in enumerate(choices, 1):
            info = providers[p]
            click.echo(f"  {i}) {info['label']}")
            click.echo(f"     {info['desc']}")

        choice = click.prompt(
            f"\nProvider [1-{len(choices)}]",
            type=click.IntRange(1, len(choices)),
            default=default_idx,
            show_default=False,
        )
        provider = choices[choice - 1]
        info = providers[provider]

        # API key
        existing = os.environ.get(info["key_var"], "")
        masked = f"{existing[:4]}***" if existing else ""
        prompt_text = f"API key ({info['key_var']})"
        if masked:
            prompt_text += f" [{masked}]"
        api_key = click.prompt(prompt_text, default="", show_default=False)
        if not api_key:
            api_key = existing or ""

        # Model override
        model = click.prompt(
            f"Model [enter for {info['default_model']}]",
            default="",
            show_default=False,
        )
        if not model:
            model = ""

        config.write_env(
            HPM_LLM_PROVIDER=provider,
            **{info["key_var"]: api_key},
            HPM_LLM_MODEL=model if model else "",
        )

        click.echo()
        click.echo("  ✓ Configured!")
        click.echo(f"    Provider: {info['label']}")
        click.echo(f"    Model:    {model or info['default_model']}")
        click.echo("    Config:   ~/.hpm/.env")
        click.echo()
        click.echo("  Next: run `hpm status` to verify")
        click.echo()
    except click.Abort:
        click.echo("\n  Setup cancelled.")
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
