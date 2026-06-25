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

from . import config

T = TypeVar("T")


# WAL + busy timeout from the immutable architecture (req #5)
_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=1000;
PRAGMA foreign_keys=ON;
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '["hermes"]',
    session_id TEXT,
    timestamp TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    decay_score REAL NOT NULL DEFAULT 1.0,
    access_scope TEXT NOT NULL DEFAULT 'all',
    last_accessed TEXT
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


_DEFAULT_DB_PATH = config.HPM_DIR / "memories.db"
_LEGACY_DB_PATH = config._LEGACY_HPM_DIR / "memories.db"


def _default_db_path() -> str:
    """Return the path to the memories database, ensuring parent dir exists.

    Prefers ``~/.hpm/memories.db`` (canonical). Falls back to the legacy
    ``~/.hermes/memories/memories.db`` location if that already exists.
    """
    env_path = os.environ.get("HPM_DB_PATH")
    if env_path:
        Path(env_path).parent.mkdir(parents=True, exist_ok=True)
        return env_path

    # Canonical location
    canonical = config.HPM_DIR / "memories.db"
    legacy = config._LEGACY_HPM_DIR / "memories.db"

    if legacy.exists():
        return str(legacy)

    canonical.parent.mkdir(parents=True, exist_ok=True)
    return str(canonical)


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
    """Create the schema if it doesn't exist, and migrate legacy data."""
    conn.executescript(_SCHEMA)
    conn.executescript(_FTS_TRIGGERS)
    migrate_v1(conn)
    conn.commit()


def serialize_vector(vec: npt.NDArray[np.float32]) -> bytes:
    """Pack a numpy float32 array into the binary format sqlite-vec expects."""
    return sqlite_vec.serialize_float32(vec)  # type: ignore[no-any-return]


# ── Write retry ──────────────────────────────────────────────────────────
# SQLite busy_timeout=1000ms gives SQLite internal time to wait before
# returning SQLITE_BUSY. Python with_retry handles the full retry cycle
# with exponential backoff: 100ms → 200ms → 400ms → 800ms.

_MAX_RETRIES = 5
_BASE_DELAY_MS = 100


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


# ── Dedup & Conflict Resolution ───────────────────────────────────────────

DEDUP_THRESHOLD = 0.85  # cosine similarity threshold for dedup


def merge_or_insert(
    conn: "sqlite3.Connection",
    content: str,
    embedding: npt.NDArray[np.float32],
    source: str = "hermes",
    session_id: str | None = None,
    tags: list[str] | None = None,
    access_scope: str = "all",
) -> str:
    """Insert a memory entry with dedup and conflict resolution.

    Checks for a near neighbor (cosine > 0.85) before inserting:
    - No match → insert new entry.
    - Match found, content is semantically similar → merge (update timestamp,
      append source to source array, update tags).
    - Match found, content differs significantly → insert as new entry fresh.

    Returns the UUID ``id`` of the active entry.
    """
    # Search for near neighbor
    near = query_vector(conn, embedding, limit=1)
    if near and near[0].get("distance", 1.0) < (1.0 - DEDUP_THRESHOLD):
        return _merge_existing(
            conn, near[0], content, embedding, source, tags,
            session_id=session_id, access_scope=access_scope,
        )

    # No match — normal insert
    return _insert_new(
        conn, content, embedding, source, session_id, tags, access_scope,
    )


def _merge_existing(
    conn: "sqlite3.Connection",
    existing: dict[str, Any],
    content: str,
    embedding: npt.NDArray[np.float32],
    source: str,
    tags: list[str] | None,
    session_id: str | None = None,
    access_scope: str | None = None,
) -> str:
    """Merge new content into an existing near-duplicate entry."""
    existing_id = existing["id"]
    existing_sources = _parse_source_array(existing.get("source", "[]"))
    existing_tags = existing.get("tags", []) or []

    # Combine sources (avoid dupes)
    if source not in existing_sources:
        existing_sources.append(source)
    sources_json = json.dumps(existing_sources)

    # Combine tags
    combined_tags = list(dict.fromkeys(existing_tags + (tags or [])))
    tags_json = json.dumps(combined_tags)

    now = _now()

    with_retry(lambda: conn.execute(
        "UPDATE memories SET source = ?, timestamp = ?, tags = ?, "
        "content = ?, decay_score = 1.0, session_id = COALESCE(?, session_id) "
        "WHERE id = ?",
        (sources_json, now, tags_json, content, session_id, existing_id),
    ))

    # Update vector and FTS5 (triggers handle FTS)
    rid = conn.execute(
        "SELECT rowid FROM memories WHERE id = ?", (existing_id,)
    ).fetchone()["rowid"]
    vec_bytes = serialize_vector(embedding)
    with_retry(lambda: conn.execute(
        "UPDATE memories_vec SET embedding = ? WHERE rowid = ?",
        (vec_bytes, rid),
    ))

    with_retry(lambda: conn.commit())
    return existing_id  # type: ignore[no-any-return]


def _insert_new(
    conn: "sqlite3.Connection",
    content: str,
    embedding: npt.NDArray[np.float32],
    source: str,
    session_id: str | None,
    tags: list[str] | None,
    access_scope: str,
) -> str:
    """Insert a brand-new memory entry."""
    mem_id = str(uuid.uuid4())
    ts = _now()
    sources_json = json.dumps([source])
    tags_json = json.dumps(tags or [])

    with_retry(lambda: conn.execute(
        "INSERT INTO memories (id, content, source, session_id, timestamp, tags, "
        "access_scope, last_accessed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (mem_id, content, sources_json, session_id, ts, tags_json, access_scope, ts),
    ))

    rowid = conn.execute("SELECT rowid FROM memories WHERE id = ?", (mem_id,)).fetchone()["rowid"]
    vec_bytes = serialize_vector(embedding)

    with_retry(lambda: conn.execute(
        "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
        (rowid, vec_bytes),
    ))

    with_retry(lambda: conn.commit())
    return mem_id


def _parse_source_array(source_val: str | list[str]) -> list[str]:
    """Parse the source field, which may be a JSON array string or a plain string."""
    if isinstance(source_val, list):
        return source_val
    try:
        parsed = json.loads(source_val)
        return parsed if isinstance(parsed, list) else [source_val]
    except (json.JSONDecodeError, TypeError):
        # Legacy: single-source string
        return [source_val] if source_val else []


# ── Legacy compatibility ──────────────────────────────────────────────────


def insert_memory(
    conn: "sqlite3.Connection",
    content: str,
    embedding: npt.NDArray[np.float32],
    source: str = "hermes",
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Insert a memory entry, deduping if a near neighbor exists.

    This is the primary entry point for captures. Delegates to
    ``merge_or_insert`` for dedup and conflict resolution.

    Returns the UUID ``id`` of the (possibly merged) entry.
    """
    return merge_or_insert(
        conn=conn,
        content=content,
        embedding=embedding,
        source=source,
        session_id=session_id,
        tags=tags,
    )


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

    results = [_row_to_dict(r) for r in rows]
    for r in results:
        reinforce(conn, r["id"])
    conn.commit()
    return results


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
    if not fts_query:
        return []
    rows = with_retry(lambda: conn.execute(
        "SELECT m.rowid, m.*, fts.rank FROM memories_fts fts "
        "JOIN memories m ON m.rowid = fts.rowid "
        "WHERE fts.content MATCH ? "
        "ORDER BY fts.rank "
        "LIMIT ?",
        (fts_query, limit),
    ).fetchall())

    results = [_row_to_dict(r) for r in rows]
    for r in results:
        reinforce(conn, r["id"])
    conn.commit()
    return results


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
    # Parse source from JSON array (or legacy single string)
    if "source" in d:
        d["source"] = _parse_source_array(d["source"])
    return d


def _to_fts5_query(user_query: str) -> str:
    """Convert a plain-text query to a minimal FTS5-safe form.

    Strips FTS5 special characters and escapes double-quotes, then wraps
    each term in quotes for AND-based matching.
    """
    import re
    # Strip FTS5 control characters: () * ^ : + -
    cleaned = re.sub(r"[()*^:+\-]", " ", user_query)
    # Remove keyword operators and escape embedded quotes
    terms = []
    for t in cleaned.strip().split():
        t_upper = t.upper()
        if t_upper in ("NOT", "OR", "AND", "NEAR"):
            continue
        t = t.replace('"', '""')
        if t:
            terms.append(t)
    if not terms:
        return ""
    return " AND ".join(f'"{t}"' for t in terms)


# ── Migration ────────────────────────────────────────────────────────────


def migrate_v1(conn: "sqlite3.Connection") -> None:
    """Migrate legacy entries (v1 schema) to the new schema (v2).

    v1 had ``source`` as a plain string. v2 stores it as a JSON array.
    Adds ``access_scope`` and ``last_accessed`` columns if missing.
    """
    # Add new columns if they don't exist (safe for ALTER TABLE)
    for col, col_type in [
        ("access_scope", "TEXT DEFAULT 'all'"),
        ("last_accessed", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Migrate legacy source values from plain string to JSON array
    rows = conn.execute(
        "SELECT rowid, source FROM memories WHERE source NOT LIKE '[%' AND source IS NOT NULL"
    ).fetchall()
    for row in rows:
        new_source = json.dumps([row["source"]])
        conn.execute("UPDATE memories SET source = ? WHERE rowid = ?", (new_source, row["rowid"]))

    if rows:
        conn.commit()





# ── Decay & Reinforcement ─────────────────────────────────────────────────


HALF_LIFE_HOURS = 168  # 1 week
EVICTION_THRESHOLD = 0.25


def reinforce(conn: "sqlite3.Connection", mem_id: str) -> None:
    """Reset decay_score to 1.0 and update last_accessed for a memory entry.

    Called automatically when a memory is retrieved in a query.
    """
    now = _now()
    with_retry(lambda: conn.execute(
        "UPDATE memories SET decay_score = 1.0, last_accessed = ? WHERE id = ?",
        (now, mem_id),
    ))
    with_retry(lambda: conn.commit())


def compute_decay_score(
    decay_score: float,
    last_accessed: str | None,
    half_life: float = HALF_LIFE_HOURS,
) -> float:
    """Compute the current decay score using the exponential decay formula.

    ``score = 0.5 ^ (hours_since_update / half_life)``
    """
    if not last_accessed:
        return decay_score

    try:
        last = datetime.fromisoformat(last_accessed)
        delta = datetime.now(timezone.utc) - last
        hours = delta.total_seconds() / 3600.0
    except (ValueError, TypeError):
        return decay_score

    if hours <= 0:
        return 1.0

    return float(decay_score * (0.5 ** (hours / half_life)))


def run_decay(
    conn: "sqlite3.Connection",
    half_life: float = HALF_LIFE_HOURS,
) -> int:
    """Compute and update decay scores for all active entries.

    Returns the number of entries updated.
    """
    rows = conn.execute(
        "SELECT id, decay_score, last_accessed FROM memories "
        ""
    ).fetchall()
    updated = 0
    for row in rows:
        new_score = compute_decay_score(
            row["decay_score"], row["last_accessed"], half_life,
        )
        if new_score != row["decay_score"]:
            conn.execute(
                "UPDATE memories SET decay_score = ? WHERE id = ?",
                (new_score, row["id"]),
            )
            updated += 1
    if updated:
        with_retry(lambda: conn.commit())
    return updated


def store_stats(conn: "sqlite3.Connection") -> dict[str, Any]:
    """Return summary statistics about the memory store."""
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    oldest = conn.execute(
        "SELECT MIN(timestamp) FROM memories"
    ).fetchone()[0]
    newest = conn.execute(
        "SELECT MAX(timestamp) FROM memories"
    ).fetchone()[0]
    low_score = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE decay_score < ?",
        (EVICTION_THRESHOLD,),
    ).fetchone()[0]
    distinct_sources = conn.execute(
        "SELECT DISTINCT source FROM memories"
    ).fetchall()
    sources: set[str] = set()
    for row in distinct_sources:
        for s in _parse_source_array(row["source"]):
            sources.add(s)

    return {
        "total": total,
        "oldest": oldest or "—",
        "newest": newest or "—",
        "entries_below_eviction": low_score,
        "sources": sorted(sources),
    }


def query_recent(
    conn: "sqlite3.Connection",
    hours: int = 24,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch memories from the last N hours."""
    import datetime as _dt

    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT * FROM memories WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
        (cutoff_str, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]
