"""CLI subcommands for hpm."""

from __future__ import annotations

import logging
import sys

import click

from . import config, daily, embed, summarize
from . import db as db_module

logger = logging.getLogger(__name__)


@click.command()
@click.argument("text")
@click.option("--tags", "-t", multiple=True, help="Tags to attach (e.g. project:jarvis)")
@click.option("--session-id", help="Source session ID for traceability")
@click.option("--no-summarize", is_flag=True, help="Skip LLM summarization, store raw text")
def capture(text: str, tags: tuple[str, ...], session_id: str | None, no_summarize: bool) -> None:
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
    """Search memory with hybrid semantic + keyword retrieval."""
    try:
        conn = db_module.get_connection()
        db_module.init_db(conn)

        results = []

        if mode in ("vector", "hybrid"):
            click.echo("embedding query...", err=True)
            query_vec = embed.embed_text(query)
            results = db_module.query_vector(conn, query_vec, limit=limit)

        if mode in ("keyword", "hybrid") and not results:
            results = db_module.query_keyword(conn, query, limit=limit)

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
            score = row.get("distance", row.get("rank", 0))
            click.echo(f"\n[{i}] (score: {score:.4f})")
            click.echo(f"    {row['content']}")
            click.echo(f"    id: {row['id']}  source: {row['source']}")
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
