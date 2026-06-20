"""Tests for the hpm database layer."""

import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest

from hpm import db


@pytest.fixture
def conn():
    """Create a fresh in-memory database for each test."""
    c = db.get_connection(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_schema_creation(conn):
    """Verify the schema tables exist after init."""
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "memories" in tables
    assert "memories_vec" in tables
    assert "memories_fts" in tables


def test_wal_mode():
    """Verify WAL mode is enabled (requires file-backed DB)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        c = db.get_connection(db_path)
        db.init_db(c)
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        c.close()
        assert mode == "wal"
    finally:
        Path(db_path).unlink(missing_ok=True)
        wal = db_path + "-wal"
        shm = db_path + "-shm"
        Path(wal).unlink(missing_ok=True)
        Path(shm).unlink(missing_ok=True)


def test_busy_timeout(conn):
    """Verify busy timeout is set."""
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 5000


def test_insert_and_vector_query(conn):
    """Insert a memory and find it via vector similarity."""
    vec = np.array([0.1] * 384, dtype=np.float32)
    mem_id = db.insert_memory(
        conn,
        content="Test memory about jarvis travel",
        embedding=vec,
        source="hermes",
        session_id="test-session-1",
        tags=["project:jarvis", "topic:testing"],
    )
    assert mem_id is not None
    assert isinstance(mem_id, str)

    # Verify it's stored
    row = db.get_memory_by_id(conn, mem_id)
    assert row is not None
    assert row["content"] == "Test memory about jarvis travel"
    assert row["source"] == "hermes"
    assert row["session_id"] == "test-session-1"
    assert row["tags"] == ["project:jarvis", "topic:testing"]
    assert 0.0 <= row["decay_score"] <= 1.0


def test_vector_search_returns_closest(conn):
    """Query vector search returns the most similar memory."""
    # Insert two memories with different embeddings
    db.insert_memory(
        conn,
        content="Captain America stuff",
        embedding=np.array([1.0] * 384, dtype=np.float32),
        tags=["topic:marvel"],
    )
    db.insert_memory(
        conn,
        content="Iron Man stuff",
        embedding=np.array([-1.0] * 384, dtype=np.float32),
        tags=["topic:marvel"],
    )

    # Query with a vector close to Captain America
    query_vec = np.array([0.95] * 384, dtype=np.float32)
    results = db.query_vector(conn, query_vec, limit=2)

    assert len(results) >= 1
    # The vector closest to [0.95...] should be [1.0...] (Captain America)
    assert results[0]["content"] == "Captain America stuff"
    assert results[0]["distance"] <= results[1]["distance"]


def test_keyword_search(conn):
    """FTS5 keyword search returns matching memories."""
    db.insert_memory(
        conn,
        content="Discussed Japanese market entry with Paddle",
        embedding=np.array([0.1] * 384, dtype=np.float32),
        tags=["project:jarvis"],
    )
    db.insert_memory(
        conn,
        content="Uncle Red's nursery planting schedule for spring",
        embedding=np.array([0.2] * 384, dtype=np.float32),
        tags=["project:nursery"],
    )

    results = db.query_keyword(conn, "Japanese Paddle", limit=5)
    assert len(results) >= 1
    assert any("Japanese" in r["content"] for r in results)


def test_with_retry_success():
    """with_retry succeeds on first try."""
    result = db.with_retry(lambda: 42)
    assert result == 42


def test_get_memory_by_id_not_found(conn):
    """get_memory_by_id returns None for missing ID."""
    assert db.get_memory_by_id(conn, "nonexistent-uuid") is None


def test_default_db_path():
    """Default DB path uses HPM_DB_PATH env var when set."""
    import os

    os.environ["HPM_DB_PATH"] = "/tmp/test-hpm/memories.db"
    try:
        path = db._default_db_path()
        assert path == "/tmp/test-hpm/memories.db"
        # Ensure parent dir was created
        assert Path("/tmp/test-hpm").exists()
    finally:
        del os.environ["HPM_DB_PATH"]
        # Clean up
        import shutil

        shutil.rmtree("/tmp/test-hpm", ignore_errors=True)


def test_hybrid_search_returns_results(conn):
    """Hybrid search returns deduplicated results from both vector and keyword."""
    db.insert_memory(
        conn,
        content="Discussed Japanese market entry with Paddle",
        embedding=np.array([0.1] * 384, dtype=np.float32),
        tags=["project:jarvis"],
    )
    db.insert_memory(
        conn,
        content="Uncle Red's nursery planting schedule for spring",
        embedding=np.array([0.2] * 384, dtype=np.float32),
        tags=["project:nursery"],
    )

    query_vec = np.array([0.15] * 384, dtype=np.float32)
    results = db.query_hybrid(conn, "Japanese Paddle", query_vec, limit=5)

    assert len(results) >= 1
    contents = [r["content"] for r in results]
    assert any("Japanese" in c for c in contents)


def test_hybrid_search_vector_weight(conn):
    """Vector weight=0 gives keyword-only, weight=1 gives vector-only."""
    db.insert_memory(
        conn,
        content="Python programming language guide",
        embedding=np.array([0.9] * 384, dtype=np.float32),
    )
    db.insert_memory(
        conn,
        content="Snake species identification",
        embedding=np.array([0.1] * 384, dtype=np.float32),
    )

    query_vec = np.array([0.85] * 384, dtype=np.float32)

    # Keyword search: "Python" matches both "Python programming" and not "Snake"
    kw_results = db.query_hybrid(conn, "Python", query_vec, limit=5, vector_weight=0.0)
    kw_contents = [r["content"] for r in kw_results]
    assert any("Python" in c for c in kw_contents)

    # Vector search: [0.85] is closer to [0.9] than [0.1]
    vec_results = db.query_hybrid(conn, "Python", query_vec, limit=5, vector_weight=1.0)
    assert "Python" in vec_results[0]["content"]
