"""``hpm wiki compile`` — run Tier 3 pipeline, write a wiki page."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

import click

from .. import answer as answer_module
from .. import db as db_module
from .. import embed, llm
from . import types as wiki_types

logger = logging.getLogger(__name__)

COMPILE_SYSTEM_PROMPT = """You are a knowledge compiler for a personal AI agent memory wiki.

You receive:
- A user's topic/query
- A set of relevant memory entries (with timestamps, sources, tags)
- A synthesized answer about that topic (if available)

Write a structured wiki page in Markdown with YAML frontmatter.
The frontmatter must include: title, created, updated, type, tags, sources, confidence, contested.

Guidelines:
- Frontmatter: type should be "entity", "concept", "comparison", or "query"
- Tags: mirror the memory tags where applicable
- Confidence: "high" if multiple corroborating sources, "medium" if single source,
- Body: use sections, bullet points, and [[wikilinks]] to other concepts
- Be concise but thorough — this is a durable reference
- If the memories contain contradictions, set contested: true and note both positions

Output ONLY the wiki page content (frontmatter + body). No explanations."""

MERGE_SYSTEM_PROMPT = """You are updating an existing wiki page with new information.

You receive:
- The existing wiki page (frontmatter + body)
- New memory entries that may add to or contradict the existing content

Update the page:
1. If new info adds detail → incorporate it, update 'updated' date
2. If new info contradicts existing content → note both positions with dates
   and sources, set contested: true, add to contradictions list
3. If new info is redundant → leave the page unchanged
4. Keep the frontmatter confidence field accurate

Output the FULL updated wiki page (frontmatter + body). No explanations."""


def _read_existing_page(slug: str) -> tuple[dict[str, str] | None, str, Path]:
    """Find an existing wiki page by slug.

    Returns ``(metadata, full_content, path)`` or ``(None, '', path)``.
    """
    for _, subdir_fn in wiki_types.SUBDIRS.items():
        candidate = subdir_fn() / f"{slug}.md"
        if candidate.exists():
            text = candidate.read_text()
            meta, body = wiki_types.parse_frontmatter(text)
            return meta, text, candidate
    return None, "", Path()


def _build_page_from_memories(
    query: str, results: list[dict[str, Any]], existing: str | None = None
) -> str:
    """Use the LLM to compile or merge a wiki page from memory results."""
    system = MERGE_SYSTEM_PROMPT if existing else COMPILE_SYSTEM_PROMPT

    # Format memory context
    memory_lines: list[str] = []
    for r in results:
        ts = r.get("timestamp", "")
        src = r.get("source", "")
        tags = r.get("tags", [])
        tags_str = f" [{', '.join(tags)}]" if tags else ""
        memory_lines.append(f"- [{ts}] source={src}{tags_str}: {r['content']}")
    memory_context = "\n".join(memory_lines)

    if existing:
        user_msg = f"""Topic: {query}

Existing wiki page:
{existing}

New memories to incorporate:
{memory_context}"""
    else:
        user_msg = f"""Topic: {query}

Relevant memories:
{memory_context}"""

    return llm.complete(
        messages=[{"role": "user", "content": user_msg}],
        system=system,
        max_tokens=1024,
        temperature=0.3,
    )


def cmd_compile(query: str, tags: list[str] | None = None, force: bool = False) -> str:
    """Run the full recall pipeline and compile a wiki page.

    Returns the path to the written page or a status message.
    """
    from .. import rerank  # lazy: heavy import

    slug = wiki_types.slugify(query)

    # Check existing page
    existing_meta, existing_content, existing_path = _read_existing_page(slug)
    if existing_content and not force:
        return (
            f"Page already exists at {existing_path}. "
            f"Use --force to recompile."
        )

    # Run Tier 1-3 pipeline for source memories
    conn = db_module.get_connection()
    db_module.init_db(conn)

    try:
        query_vec = embed.embed_text(query)
        results = db_module.query_hybrid(conn, query, query_vec, limit=20)

        if not results and not force:
            return (
                "No relevant memories found for this topic. "
                "Use --force to create a page anyway."
            )

        # Rerank
        if results:
            results = rerank.rerank(query, results, keep=10)
        rerank.unload()

        # Also get a synthesized answer for context (discarded — we use the
        # raw memory entries for compilation, but the answer shows the LLM
        # what a good response looks like)
        if results:
            answer_module.synthesize_answer(query, results)


        # Compile the page
        existing = existing_content if force else None
        page_content = _build_page_from_memories(query, results, existing=existing)

        # Determine page type from frontmatter, default to concept
        meta, body = wiki_types.parse_frontmatter(page_content)
        page_type = meta.get("type", "concept")
        title = meta.get("title", query)

        subdir = wiki_types.subdir_for(str(page_type))
        subdir.mkdir(parents=True, exist_ok=True)
        page_path = subdir / f"{slug}.md"

        # Ensure proper closing frontmatter
        if not page_content.startswith("---"):
            page_content = wiki_types.make_frontmatter(
                title=title,
                page_type=str(page_type),
                sources=[f"memory:{r.get('id', '')[:8]}" for r in results[:5]] if results else None,
            ) + page_content

        page_path.write_text(page_content)
        _update_index()
        _append_log(f"Compiled page '{title}' ({slug}) type={page_type} at {page_path}")

        return f"Wiki page written: {page_path}"

    finally:
        conn.close()


def _update_index() -> None:
    """Regenerate index.md from all wiki pages."""
    from .. import config as cfg

    index = cfg.WIKI_DIR / "index.md"
    lines = ["# Wiki Index\n"]

    for page_type, subdir_fn in wiki_types.SUBDIRS.items():
        subdir = subdir_fn()
        if not subdir.exists():
            continue
        entries: list[tuple[str, str]] = []  # (slug, title)
        for fpath in sorted(subdir.iterdir()):
            if fpath.suffix != ".md":
                continue
            text = fpath.read_text()
            meta, _ = wiki_types.parse_frontmatter(text)
            title = meta.get("title", fpath.stem)
            slug = fpath.stem
            entries.append((slug, title))

        if entries:
            heading = page_type[0].upper() + page_type[1:] + "s"
            lines.append(f"## {heading}\n")
            for slug, title in entries:
                rel = f"{page_type}s/{slug}.md"
                lines.append(f"- [{title}]({rel})")
            lines.append("")

    index.write_text("\n".join(lines))


def _append_log(entry: str) -> None:
    """Append a line to log.md."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with wiki_types.log_path().open("a") as f:
        f.write(f"- {now} — {entry}\n")


@click.command(name="compile")
@click.argument("query")
@click.option("--tags", "-t", multiple=True, help="Filter memories by tags")
@click.option("--force", is_flag=True, help="Recompile even if page exists")
def compile_cli(query: str, tags: tuple[str, ...], force: bool) -> None:
    """Compile a wiki page from the memory store on a topic."""
    try:
        result = cmd_compile(query, tags=list(tags) if tags else None, force=force)
        click.echo(result)
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
