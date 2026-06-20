"""Tests for dedup, conflict resolution, and schema migration."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from hpm import db


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_db(c)
    yield c
    c.close()


class TestDedup:
    def test_dedup_merges_similar_entries(self, conn):
        """Inserting near-identical content twice merges into one entry."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        id1 = db.insert_memory(
            conn, content="The sky is blue on a clear day", embedding=vec,
            source="hermes",
        )
        # Second insert with same embedding (cosine distance ~0)
        id2 = db.insert_memory(
            conn, content="The sky appears blue during daytime", embedding=vec,
            source="pi",
        )
        # Should return the same ID (merged)
        assert id1 == id2

        # Source array should now include both agents
        row = db.get_memory_by_id(conn, id1)
        assert row is not None
        assert "hermes" in row["source"]
        assert "pi" in row["source"]

    def test_dedup_distinct_entries(self, conn):
        """Different content with different embeddings creates separate entries."""
        vec1 = np.array([0.1] * 384, dtype=np.float32)
        vec2 = np.array([0.9] * 384, dtype=np.float32)
        id1 = db.insert_memory(conn, content="Weather in Austin", embedding=vec1)
        id2 = db.insert_memory(conn, content="Payment processor decision", embedding=vec2)
        assert id1 != id2

    def test_dedup_tags_merged(self, conn):
        """Tags are combined when entries are merged."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        db.insert_memory(
            conn, content="Test content", embedding=vec,
            tags=["project:a", "topic:test"],
        )
        db.insert_memory(
            conn, content="Test content variant", embedding=vec,
            tags=["project:b", "priority:high"],
        )
        rows = db.query_vector(conn, vec, limit=5)
        assert len(rows) == 1
        tags = rows[0]["tags"]
        assert "project:a" in tags
        assert "project:b" in tags
        assert "priority:high" in tags


class TestMigration:
    def test_migrate_v1_legacy_source(self):
        """migrate_v1 converts legacy single-string source to JSON array."""
        c = db.get_connection(":memory:")
        # Create v1-style schema manually
        c.execute(
            "CREATE TABLE memories ("
            "id TEXT PRIMARY KEY, content TEXT, source TEXT NOT NULL DEFAULT 'hermes', "
            "session_id TEXT, timestamp TEXT, tags TEXT, decay_score REAL"
            ")"
        )
        c.execute(
            "INSERT INTO memories (id, content, source, timestamp) "
            "VALUES ('a', 'test', 'hermes', '2026-01-01T00:00:00Z')"
        )
        db.migrate_v1(c)

        row = c.execute("SELECT source FROM memories WHERE id = 'a'").fetchone()
        assert json.loads(row[0]) == ["hermes"]
        c.close()

    def test_migrate_v1_idempotent(self, conn):
        """migrate_v1 is safe to run on an already-migrated database."""
        # conn already has the v2 schema via init_db
        db.migrate_v1(conn)  # should not raise
        db.migrate_v1(conn)  # twice should also be fine


class TestSuperseded:
    def test_get_superseded_returns_none_initially(self, conn):
        """get_superseded_entries returns empty when nothing is superseded."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        db.insert_memory(conn, content="Test", embedding=vec)
        assert db.get_superseded_entries(conn) == []

    def test_merge_does_not_create_superseded(self, conn):
        """Merging a near-duplicate updates the existing entry, no superseded record."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        db.merge_or_insert(conn, "Original content", embedding=vec, source="hermes")
        db.merge_or_insert(conn, "Original content variant", embedding=vec, source="pi")
        assert db.get_superseded_entries(conn) == []
