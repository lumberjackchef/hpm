"""``hpm wiki sync`` — batch compilation from recent memories."""

from __future__ import annotations

import logging

import click

from .. import db as db_module
from .. import llm
from . import compile as wiki_compile
from . import types as wiki_types

logger = logging.getLogger(__name__)

SYNC_CLUSTER_SYSTEM_PROMPT = """You are a memory clustering assistant.

Given a list of recent memory entries (each with truncated content at 200
characters, tags, and timestamp), group them into topics suitable for wiki
compilation. Each topic should be a distinct subject that would benefit from
its own wiki page.

Output a JSON array of objects:
[
  {"topic": "brief topic name", "entry_indices": [0, 3, 5]},
  {"topic": "another topic", "entry_indices": [1, 2, 7]}
]

Rules:
- Only include entries that share a common topic (2+ related entries).
- The topic name should be concise (3-8 words).
- Exclude isolated entries that don't cluster with anything.
- Output ONLY the JSON array — no explanation."""


def cmd_sync(hours: int = 24, dry_run: bool = False) -> str:
    """Scan recent memories, cluster by topic, compile wiki pages.

    Args:
        hours: How far back to scan memories.
        dry_run: If True, report what would be done without writing.

    Returns:
        A summary of what was compiled (or would be compiled).
    """
    conn = db_module.get_connection()
    db_module.init_db(conn)

    try:
        memories = db_module.query_recent(conn, hours=hours, limit=200)
        if not memories:
            return "No recent memories found."

        # Build prompt for clustering
        lines = []
        for i, m in enumerate(memories):
            content = m.get("content", "")[:200]
            tags = m.get("tags", [])
            ts = m.get("timestamp", "")
            tags_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"[{i}] ts={ts}{tags_str}: {content}")

        prompt = "\n\n".join(lines)

        try:
            cluster_json = llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system=SYNC_CLUSTER_SYSTEM_PROMPT,
                max_tokens=1024,
                temperature=0.1,
            )
        except ValueError as exc:
            if "API key" in str(exc):
                return ("LLM provider not configured. "
                        "Run `hpm setup` first.")
            raise

        # Parse the JSON array
        import json as _json

        cluster_json = cluster_json.strip()
        # Strip markdown fences if present
        if cluster_json.startswith("```"):
            cluster_json = cluster_json.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            clusters = _json.loads(cluster_json)
        except _json.JSONDecodeError:
            return f"Failed to parse clustering result:\n{cluster_json[:500]}"

        if not clusters:
            return "No clusterable topics found in recent memories."

        # Compile a wiki page for each cluster
        compiled: list[str] = []
        for cluster in clusters:
            topic = cluster.get("topic", "")
            indices = cluster.get("entry_indices", [])
            if not topic or not indices:
                continue

            # Build slug for existing page check
            slug = wiki_types.slugify(topic)

            # Check if page already exists
            existing_meta, existing_content, existing_path = (
                wiki_compile.read_existing_page(slug)
            )

            if dry_run:
                status = "update" if existing_content else "create"
                compiled.append(f"  [{status}] {topic} ({len(indices)} entries)")
                continue

            if existing_content:
                compiled.append(
                    f"  [SKIP] {topic} — page exists (use --force to recompile)"
                )
                continue

            # Gather memory entries for this cluster
            cluster_mems = [memories[i] for i in indices if i < len(memories)]
            if not cluster_mems:
                continue

            # Run compile pipeline for this topic
            try:
                result = wiki_compile.cmd_compile(topic, force=True)
                compiled.append(f"  [OK] {topic} — {result}")
            except Exception as exc:
                compiled.append(f"  [ERR] {topic} — {exc}")

        if dry_run:
            header = f"Wiki sync dry-run (last {hours}h):\n"
        else:
            header = f"Wiki sync complete (last {hours}h):\n"

        return header + "\n".join(compiled) if compiled else header + "  Nothing to compile."

    finally:
        conn.close()


@click.command(name="sync")
@click.option("--hours", default=24, show_default=True, help="How far back to scan memories")
@click.option("--dry-run", is_flag=True, help="Report what would be compiled without writing")
def sync_cli(hours: int, dry_run: bool) -> None:
    """Batch-compile wiki pages from recent memories."""
    try:
        result = cmd_sync(hours=hours, dry_run=dry_run)
        click.echo(result)
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
