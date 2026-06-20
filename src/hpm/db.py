"""Database layer for hpm.

Handles sqlite-vec schema, WAL mode, write retry with exponential backoff,
and the core CRUD operations for memory entries.
"""

import json
import os
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
import numpy.typing as npt
import sqlite_vec

T = TypeVar("T")

_HERMES_MEMORIES_DIR = Path.home() / ".hermes" / "memories"
_DEFAULT_DB_PATH = _HERMES_MEMORIES_DIR / "memories.db"

# WAL + busy timeout from the immutable architecture (req #5)
_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'hermes',
    session_id TEXT,
    timestamp TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    decay_score REAL NOT NULL DEFAULT 1.0
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(
    embedding float[384]
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content=memories,
    content_rowid=rowid
);
"""

# Triggers to keep FTS5 in sync with the memories table
_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


def _now() -> str:
    """Return ISO-8601 timestamp string in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_db_path() -> str:
    """Return the path to the memories database, ensuring parent dir exists."""
    db_path = os.environ.get("HPM_DB_PATH", str(_DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Create a new SQLite connection with sqlite-vec loaded and WAL mode.

    The returned connection has the ``vec0`` virtual table module registered
    and WAL journal mode enabled.
    """
    path = db_path or _default_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    _apply_pragmas(conn)
    return conn


def _apply_pragmas(conn: "sqlite3.Connection") -> None:
    for stmt in _PRAGMAS.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)


def init_db(conn: "sqlite3.Connection") -> None:
    """Create the schema if it doesn't exist."""
    conn.executescript(_SCHEMA)
    conn.executescript(_FTS_TRIGGERS)
    conn.commit()


def serialize_vector(vec: npt.NDArray[np.float32]) -> bytes:
    """Pack a numpy float32 array into the binary format sqlite-vec expects."""
    return bytes(sqlite_vec.serialize_float32(vec))


# ── Write retry ──────────────────────────────────────────────────────────

_MAX_RETRIES = 3
_BASE_DELAY_MS = 50


def with_retry(fn: Callable[[], T]) -> T:
    """Execute *fn* with exponential backoff on SQLITE_BUSY.

    Usage::

        with_retry(lambda: conn.execute("INSERT INTO ...", params))
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "busy" not in str(exc).lower():
                raise
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                delay = _BASE_DELAY_MS * (2**attempt) / 1000.0
                time.sleep(delay)
    raise sqlite3.OperationalError(
        f"SQLITE_BUSY after {_MAX_RETRIES} retries"
    ) from last_exc


# ── CRUD ─────────────────────────────────────────────────────────────────

def insert_memory(
    conn: "sqlite3.Connection",
    content: str,
    embedding: npt.NDArray[np.float32],
    source: str = "hermes",
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Insert a memory entry with its embedding vector.

    Returns the UUID ``id`` of the new row.
    """
    mem_id = str(uuid.uuid4())
    ts = _now()
    tags_json = json.dumps(tags or [])

    with_retry(lambda: conn.execute(
        "INSERT INTO memories (id, content, source, session_id, timestamp, tags) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mem_id, content, source, session_id, ts, tags_json),
    ))

    rowid = conn.execute("SELECT rowid FROM memories WHERE id = ?", (mem_id,)).fetchone()["rowid"]
    vec_bytes = serialize_vector(embedding)

    with_retry(lambda: conn.execute(
        "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
        (rowid, vec_bytes),
    ))

    conn.commit()
    return mem_id


def query_vector(
    conn: "sqlite3.Connection",
    query_embedding: npt.NDArray[np.float32],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Run a vector similarity search, returning top-*limit* results.

    Each result includes all ``memories`` columns plus ``distance``.
    """
    vec_bytes = serialize_vector(query_embedding)
    rows = with_retry(lambda: conn.execute(
        "SELECT m.rowid, m.*, v.distance FROM memories_vec v "
        "JOIN memories m ON m.rowid = v.rowid "
        "WHERE v.embedding MATCH ? AND v.k = ? "
        "ORDER BY v.distance",
        (vec_bytes, limit),
    ).fetchall())

    return [_row_to_dict(r) for r in rows]


def query_keyword(
    conn: "sqlite3.Connection",
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Run BM25 keyword search via FTS5, returning top-*limit* results.

    Each result includes all ``memories`` columns plus ``rank``.
    """
    # FTS5 query syntax: escape double-quotes, wrap phrases
    fts_query = _to_fts5_query(query)
    rows = with_retry(lambda: conn.execute(
        "SELECT m.rowid, m.*, fts.rank FROM memories_fts fts "
        "JOIN memories m ON m.rowid = fts.rowid "
        "WHERE fts.content MATCH ? "
        "ORDER BY fts.rank "
        "LIMIT ?",
        (fts_query, limit),
    ).fetchall())

    return [_row_to_dict(r) for r in rows]


def query_hybrid(
    conn: "sqlite3.Connection",
    query: str,
    query_embedding: npt.NDArray[np.float32],
    limit: int = 10,
    *,
    vector_weight: float = 0.7,
    fetch_size: int = 20,
) -> list[dict[str, Any]]:
    """Fuse vector similarity and BM25 keyword scores into a unified ranking.

    Runs both searches independently, normalizes scores to [0, 1], merges
    with *vector_weight* controlling the blend (keyword gets 1 - vector_weight),
    and returns deduplicated results sorted by combined score descending.

    Args:
        vector_weight: How much to weight the vector score (0.0 = keyword only,
                       1.0 = vector only). Default 0.7.
        fetch_size: How many candidates to fetch from each search before fusion.
    """
    # 1. Run both searches
    vec_results = query_vector(conn, query_embedding, limit=fetch_size)
    kw_results = query_keyword(conn, query, limit=fetch_size)

    # 2. Normalize scores to [0, 1] where 1 = best
    #    vec distance: 0 = identical, so sim = 1 - (distance / max_distance)
    if vec_results:
        max_dist = max(r.get("distance", 0) for r in vec_results) or 1.0
        for r in vec_results:
            r["_vec_score"] = 1.0 - (r.get("distance", 0) / max_dist)

    #    FTS5 rank: most negative = best match. Normalize by min rank.
    if kw_results:
        min_rank = min(r.get("rank", 0) for r in kw_results)
        # If all ranks are equal (or 0), give them 0.5
        if min_rank < 0:
            for r in kw_results:
                r["_kw_score"] = 1.0 - (r.get("rank", 0) / min_rank)
        else:
            for r in kw_results:
                r["_kw_score"] = 0.5

    # 3. Merge by rowid with weighted score
    merged: dict[int, dict[str, Any]] = {}
    for r in vec_results:
        rowid = r["rowid"]
        r["_combined"] = vector_weight * r.pop("_vec_score", 0)
        r["distance"] = None  # clear raw scores from output
        merged[rowid] = r

    for r in kw_results:
        rowid = r["rowid"]
        kw_score = r.pop("_kw_score", 0)
        if rowid in merged:
            merged[rowid]["_combined"] += (1 - vector_weight) * kw_score
        else:
            r["_combined"] = (1 - vector_weight) * kw_score
            r["rank"] = None
            merged[rowid] = r

    # 4. Sort by combined score descending, trim to limit
    sorted_results = sorted(
        merged.values(), key=lambda x: x["_combined"], reverse=True
    )[:limit]

    # Clean up internal score fields
    for r in sorted_results:
        r.pop("_combined", None)
        r.pop("distance", None)
        r.pop("rank", None)

    return sorted_results


def get_memory_by_id(conn: "sqlite3.Connection", mem_id: str) -> dict[str, Any] | None:
    """Fetch a single memory entry by its UUID."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: "sqlite3.Row") -> dict[str, Any]:
    d = dict(row)
    # Parse tags from JSON
    if isinstance(d.get("tags"), str):
        d["tags"] = json.loads(d["tags"])
    return d


def _to_fts5_query(user_query: str) -> str:
    """Convert a plain-text query to a minimal FTS5-safe form.

    Handles simple phrases and escapes problematic characters.
    """
    # Wrap each word as a prefix term for partial matching
    terms = user_query.strip().split()
    if not terms:
        return ""
    # Use AND between terms for precision
    return " AND ".join(f"\"{t}\"" for t in terms if t)
