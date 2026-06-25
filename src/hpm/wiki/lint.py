"""``hpm wiki lint`` — health checks for the wiki."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import click

from .. import config
from . import types as wiki_types


def cmd_lint(fix: bool = False) -> list[dict[str, str]]:
    """Run all wiki health checks.

    Returns a list of issue dicts: {"severity": ..., "message": ...}.
    If *fix* is True, auto-fixable issues are corrected.
    """
    issues: list[dict[str, str]] = []
    root = config.WIKI_DIR

    if not root.exists():
        return [{"severity": "error",
                 "message": f"Wiki not found at {root}. Run `hpm wiki init` first."}]

    pages: list[tuple[str, str, Path]] = []  # (type, slug, path)

    # Collect all pages
    for page_type, subdir_fn in wiki_types.SUBDIRS.items():
        subdir = subdir_fn()
        if not subdir.exists():
            continue
        for fpath in sorted(subdir.iterdir()):
            if fpath.suffix != ".md" or fpath.is_symlink():
                continue
            pages.append((page_type, fpath.stem, fpath))

    if not pages:
        issues.append({"severity": "info",
                        "message": "No wiki pages yet. Run `hpm wiki compile <topic>`."})
        return issues

    # --- Read all page content ---
    page_data: dict[str, dict[str, Any]] = {}
    for page_type, slug, fpath in pages:
        text = fpath.read_text()
        meta, body = wiki_types.parse_frontmatter(text)
        page_data[slug] = {
            "type": page_type,
            "path": fpath,
            "meta": meta,
            "body": body,
            "text": text,
        }

    # 1. Frontmatter validation
    required_fields = ["title", "type", "confidence", "contested", "created", "updated"]
    for slug, data in page_data.items():
        meta = data["meta"]
        for field in required_fields:
            if field not in meta or meta[field] in (None, ""):
                issues.append({
                    "severity": "warning",
                    "message": f"[{slug}] Missing frontmatter field: {field}",
                })

        if meta.get("created"):
            try:
                date.fromisoformat(str(meta["created"]))
            except (ValueError, TypeError):
                issues.append({
                    "severity": "warning",
                    "message": f"[{slug}] Invalid date in frontmatter: created={meta['created']}",
                })

    # 2. Index completeness
    index_path = wiki_types.index_path()
    if index_path.exists():
        index_text = index_path.read_text()
        indexed_slugs: set[str] = set()
        for line in index_text.splitlines():
            m = re.match(r"^- \[.+?\]\(.+?/(.+?)\.md\)", line)
            if m:
                indexed_slugs.add(m.group(1))

        for slug in page_data:
            if slug not in indexed_slugs:
                issues.append({
                    "severity": "warning",
                    "message": f"[{slug}] Page not in index.md",
                })

        # Stale index entries
        for m in re.finditer(r"\(.+?/(.+?)\.md\)", index_text):
            indexed_slug = m.group(1)
            if indexed_slug not in page_data:
                issues.append({
                    "severity": "warning",
                    "message": f"[{indexed_slug}] Stale entry in index.md — page does not exist",
                })
    else:
        issues.append({"severity": "warning", "message": "No index.md found. Run `hpm wiki init`."})

    # 3. Wikilink graph — single-pass build
    # {slug -> {linked_to: set of slugs, links_to: set of slugs}}
    wikilink_graph: dict[str, dict[str, set[str]]] = {}
    for slug in page_data:
        wikilink_graph[slug] = {"linked_to": set(), "links_to": set()}

    for slug, data in page_data.items():
        for link_match in re.finditer(r"\[\[(.+?)\]\]", data["text"]):
            link_text = link_match.group(1)
            # Try direct slug match first; fall back to slugified version
            target = link_text
            if target not in page_data:
                target = wiki_types.slugify(link_text)
            if target in page_data:
                wikilink_graph[slug]["links_to"].add(target)
                wikilink_graph[target]["linked_to"].add(slug)
            else:
                issues.append({
                    "severity": "warning",
                    "message": (
                        f"[{slug}] Broken [[wikilink]] to"
                        f" '{link_text}' — page not found"
                    ),
                })

    # Orphan pages (zero inbound wikilinks from other pages)
    for slug in page_data:
        if not wikilink_graph[slug]["linked_to"]:
            issues.append({
                "severity": "info",
                "message": f"[{slug}] Orphan page — no inbound [[wikilinks]]",
            })

    # 4. Contradictions
    for slug, data in page_data.items():
        meta = data["meta"]
        if meta.get("contested") is True or meta.get("contested") == "true":
            contradictions = meta.get("contradictions", [])
            if contradictions:
                issues.append({
                    "severity": "warning",
                    "message": (
                        f"[{slug}] Contested page — conflicts with:"
                        f" {', '.join(contradictions)}"
                    ),
                })
            else:
                issues.append({
                    "severity": "info",
                    "message": f"[{slug}] Marked as contested (no contradictions listed)",
                })

    # 5. Low confidence
    for slug, data in page_data.items():
        if str(data["meta"].get("confidence", "")).lower() == "low":
            issues.append({
                "severity": "info",
                "message": f"[{slug}] Low confidence page — may need review",
            })

    # 6. Large pages
    for slug, data in page_data.items():
        line_count = data["text"].count("\n") + 1
        if line_count > 200:
            issues.append({
                "severity": "info",
                "message": f"[{slug}] Large page ({line_count} lines) — consider splitting",
            })

    # 7. Stale content (updated > 90 days from now)
    for slug, data in page_data.items():
        updated = data["meta"].get("updated", "")
        if updated:
            try:
                updated_date = date.fromisoformat(str(updated)[:10])
                delta = date.today() - updated_date
                if delta.days > 90:
                    issues.append({
                        "severity": "info",
                        "message": f"[{slug}] Stale — last updated {delta.days} days ago",
                    })
            except (ValueError, TypeError):
                pass

    # --- Auto-fix: regenerate index and contested index ---
    if fix:
        wiki_types.rebuild_index()
        issues.append({"severity": "info", "message": "index.md and contested.json regenerated."})

    return issues


@click.command(name="lint")
@click.option("--fix", is_flag=True, help="Auto-fix fixable issues (regenerate index)")
def lint_cli(fix: bool) -> None:
    """Check wiki health — orphans, broken links, stale pages, contradictions."""
    try:
        issues = cmd_lint(fix=fix)
        if not issues:
            click.echo("Wiki is healthy — no issues found.")
            return

        for iss in issues:
            prefix = {
                "error": "❌",
                "warning": "⚠ ",
                "info": " ℹ",
            }.get(iss["severity"], " •")
            click.echo(f"{prefix} {iss['message']}")
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
