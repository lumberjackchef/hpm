"""Daily markdown log writer.

Appends captured memories to ``~/.hermes/memories/daily/YYYY-MM-DD.md``
as a plain-text backup and audit trail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import config


def append_to_daily_log(
    content: str,
    source: str = "hermes",
    session_id: str | None = None,
    tags: list[str] | None = None,
    log_dir: str | None = None,
) -> str:
    """Append a memory entry to today's daily log file.

    Returns the file path of the log.
    """
    log_dir_path = _resolve_log_dir(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = log_dir_path / f"{today}.md"

    tags_str = ", ".join(tags) if tags else ""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    session_str = f" (session: {session_id})" if session_id else ""

    entry_parts = [
        f"- **[{ts}]** {content}",
    ]
    if tags_str:
        entry_parts.append(f"  tags: {tags_str}")
    if session_str:
        entry_parts.append(f"  source: {source}{session_str}")

    entry = "\n".join(entry_parts) + "\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)

    return str(log_path)


def _resolve_log_dir(override: str | None = None) -> Path:
    """Return the daily log directory path."""
    if override:
        return Path(override)
    env_path = config.DAILY_LOG
    # If it's a file path, use its parent
    p = Path(env_path)
    if p.suffix == ".md" or not p.exists() and p.suffix:
        return p.parent
    if not p.suffix:
        return p
    return p.parent
