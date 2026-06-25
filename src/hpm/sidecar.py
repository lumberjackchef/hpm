"""Hermes state.db poller — auto-capture sidecar.

Designed to run as a background daemon. Polls ``~/.hermes/state.db`` every few
seconds, detects new messages, and captures them to the hpm memory store.

Tracks its position with a simple JSON cursor file so it survives restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import config, daily, summarize
from . import db as db_module

logger = logging.getLogger(__name__)

# Default paths
STATE_DB = Path.home() / ".hermes" / "state.db"
CURSOR_FILE = config.HPM_DIR / ".sidecar-cursor.json"

POLL_INTERVAL = 5.0  # seconds between polls

# ── Cursor management ────────────────────────────────────────────────────


def _load_cursor() -> dict[str, int]:
    """Load the last-seen message ID per session from the cursor file."""
    if CURSOR_FILE.exists():
        try:
            data: dict[str, int] = json.loads(CURSOR_FILE.read_text())
            return data
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cursor(cursor: dict[str, int]) -> None:
    """Persist the cursor."""
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))


# ── State DB reading ─────────────────────────────────────────────────────


def get_state_connection() -> sqlite3.Connection:
    """Open a read-only connection to the Hermes state database."""
    conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_latest_messages(
    conn: sqlite3.Connection,
    cursor: dict[str, int],
) -> list[dict[str, Any]]:
    """Fetch new user+assistant message pairs per-session since the last cursor.

    Tracks cursor per-session so fast sessions don't skip messages from
    slower sessions. Returns messages ordered by session, then by timestamp.
    """
    all_results: list[dict[str, Any]] = []
    sessions = conn.execute(
        "SELECT id, title FROM sessions WHERE active = 1"
    ).fetchall()

    for sess in sessions:
        sid = sess["id"]
        last_id = cursor.get(sid, 0)
        rows = conn.execute(
            """SELECT m.id, m.session_id, m.role, m.content, m.timestamp,
                      ? as session_title
               FROM messages m
               WHERE m.session_id = ?
                 AND m.id > ?
                 AND m.role IN ('user', 'assistant')
                 AND m.active = 1
               ORDER BY m.timestamp""",
            (sess["title"], sid, last_id),
        ).fetchall()
        all_results.extend(dict(r) for r in rows)

    return all_results


def build_turns(
    messages: list[dict[str, Any]],
    cursor: dict[str, int],
) -> list[tuple[str, str, str, str]]:
    """Group messages into user→assistant turns.

    Returns list of ``(session_id, session_title, user_content, assistant_content)``.
    """
    turns: list[tuple[str, str, str, str]] = []
    pending: dict[str, dict[str, Any]] = {}

    for msg in messages:
        sess = msg["session_id"]
        role = msg["role"]
        content = (msg.get("content") or "").strip()

        if role == "user" and content:
            pending[sess] = msg
        elif role == "assistant" and content and sess in pending:
            user_msg = pending.pop(sess)
            title = msg.get("session_title") or ""
            turns.append((sess, title, user_msg["content"], content))

    return turns


# ── Capture pipeline ─────────────────────────────────────────────────────


from . import embed  # noqa: E402


def capture_turn_to_memory(
    user_content: str,
    assistant_content: str,
    session_id: str,
    session_title: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> str | None:
    """Summarize and store a single conversation turn.

    Args:
        conn: Optional shared connection. If omitted, opens and closes one.
    """
    turn_text = f"User: {user_content}\n\nAssistant: {assistant_content}"
    if len(turn_text.strip()) < 20:
        logger.debug("skipping short turn (session=%s)", session_id)
        return None

    close_on_exit = conn is None
    local_conn = conn or db_module.get_connection()

    try:
        if close_on_exit:
            db_module.init_db(local_conn)

        logger.info("summarizing turn for session=%s", session_id)
        summary = summarize.summarize_turn(turn_text)

        logger.info("embedding...")
        vector = embed.embed_text(summary)

        tags = []
        if session_title:
            tags.append(f"session:{session_title.replace(' ', '-')}")

        mem_id = db_module.insert_memory(
            local_conn,
            content=summary,
            embedding=vector,
            source=config.SOURCE,
            session_id=session_id,
            tags=tags or None,
        )

        daily.append_to_daily_log(
            content=summary,
            source=config.SOURCE,
            session_id=session_id,
            tags=tags or None,
        )

        logger.info("captured %s for session %s", mem_id, session_id)
        return mem_id

    except Exception:
        logger.exception("capture failed for session %s", session_id)
        return None
    finally:
        if close_on_exit:
            local_conn.close()


# ── Main loop ────────────────────────────────────────────────────────────


def run_sidecar(
    once: bool = False,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Run the sidecar poll loop.

    Args:
        once: If True, poll once and exit (useful for testing).
        poll_interval: Seconds between polls.
    """
    provider_keys = {
        "opencode": config.OPENGINE_API_KEY,
        "anthropic": config.ANTHROPIC_API_KEY,
        "openai": config.OPENAI_API_KEY,
        "openrouter": config.OPENROUTER_API_KEY,
    }
    active_key = provider_keys.get(config.LLM_PROVIDER, config.OPENGINE_API_KEY)
    if not active_key:
        logger.warning(
            "No API key configured for provider %r"
            " — summarization will fail.",
            config.LLM_PROVIDER,
        )

    cursor = _load_cursor()
    logger.info("sidecar starting (cursor: %s)", cursor)

    # Phase C: Log wiki index summary on start
    _log_wiki_summary()

    while True:
        try:
            _poll_once(cursor)
        except Exception:
            logger.exception("poll cycle failed")

        if once:
            break
        time.sleep(poll_interval)


def _log_wiki_summary() -> None:
    """Log a compact summary of what the wiki covers."""
    from .wiki import types as wiki_types  # lazy: wiki is optional for sidecar

    wiki_dir = config.WIKI_DIR
    index_file = wiki_dir / "index.md"
    if not index_file.exists():
        logger.info("wiki: not initialized (run `hpm wiki init`)")
        return

    page_count = 0
    for _, subdir_fn in wiki_types.SUBDIRS.items():
        subdir = subdir_fn()
        if subdir.exists():
            page_count += len(list(subdir.glob("*.md")))

    # Read index for topic summary
    topics = []
    try:
        import re
        for line in index_file.read_text().splitlines():
            m = re.match(r"^- \[(.+?)\]", line)
            if m:
                topics.append(m.group(1))
    except OSError:
        pass

    if topics:
        logger.info("wiki: %d pages covering: %s", page_count, ", ".join(topics[:10]))
        if len(topics) > 10:
            logger.info("wiki: ... and %d more topics", len(topics) - 10)
    else:
        logger.info("wiki: %d pages, run `hpm wiki lint` for details", page_count)


def _poll_once(cursor: dict[str, int]) -> None:
    """Execute a single poll cycle. Uses one DB connection for all captures."""
    if not STATE_DB.exists():
        logger.debug("state.db not found at %s", STATE_DB)
        return

    state_conn = get_state_connection()
    try:
        messages = get_latest_messages(state_conn, cursor)
        if not messages:
            return

        turns = build_turns(messages, cursor)
        if not turns:
            return

        # Use a single hpm connection for all captures in this cycle
        hpm_conn = db_module.get_connection()
        db_module.init_db(hpm_conn)
        try:
            for sess_id, title, user_content, assistant_content in turns:
                capture_turn_to_memory(
                    user_content, assistant_content, sess_id, title, conn=hpm_conn
                )
        finally:
            hpm_conn.close()

        # Update per-session cursor positions
        for m in messages:
            sid = m["session_id"]
            mid = m["id"]
            if cursor.get(sid, 0) < mid:
                cursor[sid] = mid
        _save_cursor(cursor)
    finally:
        state_conn.close()
