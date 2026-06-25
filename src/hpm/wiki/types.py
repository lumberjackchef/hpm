"""Frontmatter parsing, slug generation, and path helpers for the wiki."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from .. import config

# ── Paths ──────────────────────────────────────────────────────────────────


def entities_dir() -> Path:
    return config.WIKI_DIR / "entities"


def concepts_dir() -> Path:
    return config.WIKI_DIR / "concepts"


def comparisons_dir() -> Path:
    return config.WIKI_DIR / "comparisons"


def queries_dir() -> Path:
    return config.WIKI_DIR / "queries"


SUBDIRS = {
    "entity": entities_dir,
    "concept": concepts_dir,
    "comparison": comparisons_dir,
    "query": queries_dir,
}


def subdir_for(page_type: str) -> Path:
    fn = SUBDIRS.get(page_type, concepts_dir)
    return fn()


def index_path() -> Path:
    return config.WIKI_DIR / "index.md"


def schema_path() -> Path:
    return config.WIKI_DIR / "SCHEMA.md"


def log_path() -> Path:
    return config.WIKI_DIR / "log.md"


# ── Slug ────────────────────────────────────────────────────────────────────


def slugify(title: str) -> str:
    """Convert a title to a filesystem-safe slug.

    "My Project: Payments API v2" → "my-project-payments-api-v2"
    """
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


# ── Frontmatter ────────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-like frontmatter from a wiki page.

    Returns ``(metadata, body)`` where metadata is a flat dict of
    frontmatter fields.  This is a minimal parser — it handles the subset
    of frontmatter the wiki uses (no nested structures, no lists).
    """
    text = text.lstrip("\n")
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text

    block = text[4:end]
    body = text[end + 5 :]

    meta: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()

        # Parse simple YAML scalars
        if val.startswith("[") and val.endswith("]"):
            # Simple bracket-array like [memory:abc, memory:def]
            inner = val[1:-1]
            meta[key] = [
                item.strip().strip('"').strip("'")
                for item in inner.split(",") if item.strip()
            ]
        elif val.lower() in ("true", "false"):
            meta[key] = val.lower() == "true"
        elif val and val[0].isdigit() and "-" in val:
            # Try date
            try:
                meta[key] = date.fromisoformat(val)
            except ValueError:
                meta[key] = val
        else:
            meta[key] = val

    return meta, body


def make_frontmatter(
    *,
    title: str,
    page_type: str,
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    confidence: str = "medium",
    contested: bool = False,
    contradictions: list[str] | None = None,
) -> str:
    """Generate YAML frontmatter for a wiki page."""
    now = date.today().isoformat()
    lines = ["---"]
    lines.append(f"title: {title}")
    lines.append(f"created: {now}")
    lines.append(f"updated: {now}")
    lines.append(f"type: {page_type}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    if sources:
        lines.append(f"sources: [{', '.join(sources)}]")
    lines.append(f"confidence: {confidence}")
    lines.append(f"contested: {'true' if contested else 'false'}")
    if contradictions:
        lines.append(f"contradictions: [{', '.join(contradictions)}]")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ── SCHEMA.md template ────────────────────────────────────────────────────


SCHEMA_TEMPLATE = """# Wiki Schema

Auto-generated on {date}.  Every new page must conform to these conventions.

## Tag Taxonomy

Tags are free-form strings in the format ``category:value``.

Common categories:
- ``project:`` — the software project (jarvis, hpm, salfa, silk, ...)
- ``topic:`` — the subject domain (payments, deployment, security, ...)
- ``client:`` — the client or stakeholder
- ``domain:`` — technical domain (ml, infra, backend, frontend, ...)

## Page Thresholds

- Max 200 lines per page. Pages exceeding this should be split.
- Archive pages to ``_archive/`` when stale > 90 days.

## Required Frontmatter

Every page must have: title, created, updated, type, confidence, contested.

## Type Taxonomy

| Type | Directory | Description |
|------|-----------|-------------|
| entity | entities/ | People, products, projects, organizations |
| concept | concepts/ | Architectures, techniques, domains |
| comparison | comparisons/ | Side-by-side trade-off analyses |
| query | queries/ | Filed query results worth keeping |
"""


def generate_schema() -> str:
    return SCHEMA_TEMPLATE.format(date=date.today().isoformat())
