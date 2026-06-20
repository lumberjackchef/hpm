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
    """Fetch new user+assistant message pairs since the last cursor position.

    Returns messages ordered by session, then by timestamp.
    Only returns messages where the session has an active assistant response.
    """
    last_id = max(cursor.values()) if cursor else 0
    rows = conn.execute(
        """SELECT m.id, m.session_id, m.role, m.content, m.timestamp,
                  s.title as session_title
           FROM messages m
           JOIN sessions s ON s.id = m.session_id
           WHERE m.id > ?
             AND m.role IN ('user', 'assistant')
             AND m.active = 1
           ORDER BY m.session_id, m.timestamp""",
        (last_id,),
    ).fetchall()

    return [dict(r) for r in rows]


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
    if not config.OPENGINE_API_KEY:
        logger.warning(
            "OPENCODE_GO_API_KEY not set — summarization will fail. "
            "Set it in your environment before starting the sidecar."
        )

    cursor = _load_cursor()
    logger.info("sidecar starting (cursor: %s)", cursor)

    while True:
        try:
            _poll_once(cursor)
        except Exception:
            logger.exception("poll cycle failed")

        if once:
            break
        time.sleep(poll_interval)


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

        # Update cursor to the highest message ID seen
        max_id = max(m["id"] for m in messages)
        cursor["_global"] = max_id
        _save_cursor(cursor)
    finally:
        state_conn.close()
