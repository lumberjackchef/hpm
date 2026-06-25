"""Frontmatter parsing, slug generation, and path helpers for the wiki."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from .. import config

MAX_SLUG_LENGTH = 200

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

    "My Project: Payments API v2" -> "my-project-payments-api-v2"

    Capped at *MAX_SLUG_LENGTH* (200) characters.
    """
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:MAX_SLUG_LENGTH]


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
    """Generate YAML frontmatter for a wiki page.

    Values are sanitised: newlines stripped from titles, tags/sources
    with commas or brackets are handled correctly by the bracket-array
    format.
    """
    now = date.today().isoformat()
    lines = ["---"]
    # Strip newlines from titles to prevent YAML breakage
    safe_title = title.replace("\n", " ").replace("\r", " ").strip()
    lines.append(f"title: {safe_title}")
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


# ── Atomic file write ──────────────────────────────────────────────────────


def atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via temp file + rename.

    On POSIX, ``os.rename()`` is atomic.  This prevents partial/corrupt
    files from crashes or power loss during write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        # Clean up temp file on error
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Contested pages index ─────────────────────────────────────────────────


def contested_index_path() -> Path:
    """Path to the cached JSON index of contested wiki pages.

    Format: ``{"slugs": {"payment-decision": "Payment Decision"}}``
    Updated on compile, sync, and lint --fix.
    """
    return config.WIKI_DIR / "contested.json"


def write_contested_index(pages: list[tuple[str, str, bool, list[str]]]) -> None:
    """Write a JSON index of contested pages.

    Each entry: ``(slug, title, is_contested, contradictions)``.
    Only contested pages are written to the cache.
    """
    contested = {
        slug: {"title": title, "contradictions": contradictions}
        for slug, title, is_contested, contradictions in pages
        if is_contested
    }
    atomic_write(contested_index_path(), json.dumps(contested, indent=2))


def read_contested_index() -> dict[str, dict[str, Any]]:
    """Read the cached contested-pages index.

    Returns ``{slug: {"title": ..., "contradictions": [...]}}`` or
    empty dict if the file does not exist.
    """
    path = contested_index_path()
    if not path.exists():
        return {}
    try:
        data: dict[str, dict[str, Any]] = json.loads(path.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return {}


# ── Rebuild index (shared by compile.py and lint.py) ────────────────────────


PageInfo = tuple[str, str, str, bool, list[str]]
# (slug, title, page_type, is_contested, contradictions)


def collect_page_info() -> list[PageInfo]:
    """Scan all wiki subdirs and return metadata for every page.

    Returns list of ``(slug, title, page_type, is_contested, contradictions)``.
    """
    pages: list[PageInfo] = []
    for page_type, subdir_fn in SUBDIRS.items():
        subdir = subdir_fn()
        if not subdir.exists():
            continue
        for fpath in sorted(subdir.iterdir()):
            if fpath.suffix != ".md" or fpath.is_symlink():
                continue
            text = fpath.read_text()
            meta, _ = parse_frontmatter(text)
            slug = fpath.stem
            title = str(meta.get("title", slug))
            contested = meta.get("contested") in (True, "true")
            contradictions = meta.get("contradictions", [])
            if isinstance(contradictions, str):
                contradictions = [contradictions]
            pages.append((slug, title, page_type, contested, contradictions))
    return pages


def rebuild_index(pages: list[PageInfo] | None = None) -> None:
    """Regenerate index.md and contested.json from all wiki pages.

    If *pages* is None, it is collected from the filesystem.
    """
    if pages is None:
        pages = collect_page_info()

    # Index
    lines = ["# Wiki Index\n"]

    # Group by type for headings
    by_type: dict[str, list[tuple[str, str]]] = {}
    for slug, title, page_type, _, _ in pages:
        by_type.setdefault(page_type, []).append((slug, title))

    for page_type in ("entity", "concept", "comparison", "query"):
        entries = by_type.get(page_type)
        if not entries:
            continue
        heading = page_type[0].upper() + page_type[1:] + "s"
        lines.append(f"## {heading}\n")
        for slug, title in sorted(entries):
            rel = f"{page_type}s/{slug}.md"
            lines.append(f"- [{title}]({rel})")
        lines.append("")

    atomic_write(index_path(), "\n".join(lines))

    # Contested index
    contested_data = [(s, t, c, co) for s, t, _, c, co in pages]
    write_contested_index(contested_data)
