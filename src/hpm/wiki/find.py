"""``hpm wiki find`` — scan index, read a page, fall through to answer."""

from __future__ import annotations

import re

import click

from . import types as wiki_types


def cmd_find(query: str) -> str:
    """Look up a topic in the compiled wiki.

    Scans the index for matching topics, then returns the page content.
    Falls through to the vector pipeline if no wiki page matches.
    """
    idx_path = wiki_types.index_path()
    if not idx_path.exists():
        return _fallback(query)

    index_text = idx_path.read_text()

    # Search index for matching entries (keyword match on word boundaries)
    matches: list[tuple[str, str, str]] = []  # (slug, title, page_type)
    query_words = [w.lower() for w in query.split() if len(w) > 1]
    if not query_words:
        return _fallback(query)

    for line in index_text.splitlines():
        # Match markdown links in the index: [Title](entities/slug.md)
        m = re.match(r"^- \[(.+?)\]\((.+?)/(.+?)\.md\)", line)
        if m:
            title = m.group(1).lower()
            if all(w in title for w in query_words):
                page_type = m.group(2).rstrip("s")  # "entities" -> "entity"
                slug = m.group(3)
                matches.append((slug, m.group(1), page_type))

    if not matches:
        return _fallback(query)

    # Return the best match (first by appearance in index)
    slug, title, page_type = matches[0]
    subdir = wiki_types.subdir_for(page_type)
    page_path = subdir / f"{slug}.md"

    if not page_path.exists():
        return _fallback(query)

    content = page_path.read_text()
    meta, body = wiki_types.parse_frontmatter(content)

    # Build a compact result
    confidence = meta.get("confidence", "medium")
    updated = meta.get("updated", "unknown")
    tags = meta.get("tags", [])
    tags_str = f" [{', '.join(tags)}]" if tags else ""

    return (
        f"## {meta.get('title', title)}\n"
        f"*Confidence: {confidence}  |  Updated: {updated}{tags_str}*\n\n"
        f"{body.strip()}\n\n"
        f"---\n"
        f"_Source: wiki page `{page_path}`.  For more detail, use `hpm answer \"{query}\"`._"
    )


def _fallback(query: str) -> str:
    """Fall through to the full recall pipeline."""

    # We can't call the click command directly from here, so we return a
    # special marker that the caller can use.
    return f"__WIKI_FALLTHROUGH__:{query}"


@click.command(name="find")
@click.argument("query")
def find_cli(query: str) -> None:
    """Search the wiki for a topic. Falls back to memory search if not found."""
    try:
        result = cmd_find(query)
        if result.startswith("__WIKI_FALLTHROUGH__:"):
            actual_query = result[len("__WIKI_FALLTHROUGH__:"):]
            from ..cli import answer as answer_cmd
            ctx = click.get_current_context()
            ctx.invoke(answer_cmd, query=actual_query, limit=5, no_rerank=False)
        else:
            click.echo(result)
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
