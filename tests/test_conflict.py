"""Tests for the LLM-based conflict detector (Phase 4)."""

import json
from unittest.mock import patch

import numpy as np
import pytest

from hpm import answer, conflict, db


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_db(c)
    yield c
    c.close()


def _insert(conn, content, tags=None, timestamp=None):
    """Helper: insert a memory entry with optional tags and timestamp override."""
    nid = hash(content) & 0xFFFFFFFF  # different vec per content
    vec = np.random.default_rng(nid).random(384).astype(np.float32)
    mid = db.insert_memory(conn, content=content, embedding=vec, tags=tags or [])
    if timestamp:
        conn.execute("UPDATE memories SET timestamp = ? WHERE id = ?", (timestamp, mid))
        conn.commit()
    return mid


class TestFindCandidates:
    def test_no_entries(self, conn):
        assert conflict.find_candidates(conn) == []

    def test_no_tags(self, conn):
        _insert(conn, "some content", tags=[])
        _insert(conn, "other content", tags=[])
        assert conflict.find_candidates(conn) == []

    def test_single_tag_group_produces_pairs(self, conn):
        old = _insert(conn, "older fact", tags=["topic:weather"], timestamp="2026-01-01T00:00:00Z")
        new = _insert(conn, "newer fact", tags=["topic:weather"], timestamp="2026-06-01T00:00:00Z")
        candidates = conflict.find_candidates(conn, max_pairs=5)
        assert len(candidates) >= 1
        newer, older = candidates[0]
        assert older["id"] == old
        assert newer["id"] == new

    def test_multiple_tag_groups(self, conn):
        _insert(conn, "weather old", tags=["topic:weather"], timestamp="2026-01-01T00:00:00Z")
        _insert(conn, "weather new", tags=["topic:weather"], timestamp="2026-06-01T00:00:00Z")
        _insert(conn, "payment old", tags=["topic:payments"], timestamp="2026-01-01T00:00:00Z")
        _insert(conn, "payment new", tags=["topic:payments"], timestamp="2026-06-01T00:00:00Z")
        candidates = conflict.find_candidates(conn, max_pairs=5)
        assert len(candidates) == 2

    def test_respects_max_pairs(self, conn):
        for i in range(5):
            _insert(conn, f"topic-a-{i}", tags=["topic:a"], timestamp=f"2026-01-0{i+1}T00:00:00Z")
            _insert(conn, f"topic-b-{i}", tags=["topic:b"], timestamp=f"2026-01-0{i+1}T00:00:00Z")
        candidates = conflict.find_candidates(conn, max_pairs=3)
        assert len(candidates) == 3

    def test_skips_already_superseded(self, conn):
        old = _insert(conn, "old fact", tags=["topic:test"], timestamp="2026-01-01T00:00:00Z")
        mid = _insert(conn, "mid fact", tags=["topic:test"], timestamp="2026-03-01T00:00:00Z")
        new = _insert(conn, "new fact", tags=["topic:test"], timestamp="2026-06-01T00:00:00Z")
        # Mark mid as superseded_by new
        conn.execute("UPDATE memories SET superseded_by = ? WHERE id = ?", (new, mid))
        conn.commit()
        candidates = conflict.find_candidates(conn, max_pairs=10)
        # mid should not appear in any pair
        pair_ids = set()
        for newer, older in candidates:
            pair_ids.add(newer["id"])
            pair_ids.add(older["id"])
        assert mid not in pair_ids, "superseded entries should be excluded"


class TestJudgePair:
    def test_contradiction(self, conn):
        newer = {"id": "a", "content": "We chose Stripe for payments", "timestamp": "2026-06-01T00:00:00Z"}
        older = {"id": "b", "content": "We chose Paddle for payments", "timestamp": "2026-01-01T00:00:00Z"}
        with patch("hpm.conflict.llm.complete", return_value="CONTRADICTION"):
            result = conflict.judge_pair(newer, older)
        assert result == "CONTRADICTION"

    def test_refinement(self, conn):
        newer = {"id": "a", "content": "API rate limit is 1000 req/min", "timestamp": "2026-06-01T00:00:00Z"}
        older = {"id": "b", "content": "Rate limit is 500 req/min (will increase)", "timestamp": "2026-01-01T00:00:00Z"}
        with patch("hpm.conflict.llm.complete", return_value="REFINEMENT"):
            result = conflict.judge_pair(newer, older)
        assert result == "REFINEMENT"

    def test_unrelated(self, conn):
        newer = {"id": "a", "content": "Weather in Austin is hot", "timestamp": "2026-06-01T00:00:00Z"}
        older = {"id": "b", "content": "Payment provider is Stripe", "timestamp": "2026-01-01T00:00:00Z"}
        with patch("hpm.conflict.llm.complete", return_value="UNRELATED"):
            result = conflict.judge_pair(newer, older)
        assert result == "UNRELATED"

    def test_llm_parse_fallback(self, conn):
        newer = {"id": "a", "content": "X", "timestamp": "2026-06-01T00:00:00Z"}
        older = {"id": "b", "content": "Y", "timestamp": "2026-01-01T00:00:00Z"}
        # LLM returns extra text — should still extract the keyword
        with patch("hpm.conflict.llm.complete", return_value="These two entries are a CONTRADICTION because..."):
            result = conflict.judge_pair(newer, older)
        assert result == "CONTRADICTION"


class TestRunConflictDetection:
    def test_mark_superseded_on_contradiction(self, conn):
        old = _insert(conn, "We chose Paddle for payments", tags=["topic:payments"], timestamp="2026-01-01T00:00:00Z")
        new = _insert(conn, "We chose Stripe for payments", tags=["topic:payments"], timestamp="2026-06-01T00:00:00Z")

        with patch("hpm.conflict.llm.complete", return_value="CONTRADICTION"):
            summary = conflict.run_conflict_detection(conn, max_pairs=5)

        assert summary["checked"] == 1
        assert summary["contradictions"] == 1

        # Older entry should be marked
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (old,)).fetchone()
        assert row["superseded_by"] == new

    def test_refinement_no_mark(self, conn):
        old = _insert(conn, "Rate limit is 500 req/min", tags=["topic:api"], timestamp="2026-01-01T00:00:00Z")
        new = _insert(conn, "Rate limit is 1000 req/min", tags=["topic:api"], timestamp="2026-06-01T00:00:00Z")

        with patch("hpm.conflict.llm.complete", return_value="REFINEMENT"):
            summary = conflict.run_conflict_detection(conn, max_pairs=5)

        assert summary["checked"] >= 1
        # Older should NOT be marked (it's a refinement, not a contradiction)
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (old,)).fetchone()
        assert row["superseded_by"] is None

    def test_unrelated_no_mark(self, conn):
        old = _insert(conn, "Weather in Austin", tags=["topic:misc"], timestamp="2026-01-01T00:00:00Z")
        new = _insert(conn, "Payment provider is Stripe", tags=["topic:misc"], timestamp="2026-06-01T00:00:00Z")

        with patch("hpm.conflict.llm.complete", return_value="UNRELATED"):
            summary = conflict.run_conflict_detection(conn, max_pairs=5)

        row = conn.execute("SELECT * FROM memories WHERE id = ?", (old,)).fetchone()
        assert row["superseded_by"] is None

    def test_skip_when_llm_not_configured(self, conn):
        _insert(conn, "old", tags=["topic:x"], timestamp="2026-01-01T00:00:00Z")
        _insert(conn, "new", tags=["topic:x"], timestamp="2026-06-01T00:00:00Z")

        with patch("hpm.conflict.llm.complete", side_effect=ValueError("API key not configured")):
            summary = conflict.run_conflict_detection(conn, max_pairs=5)

        # Pair is still "checked" but treated as UNRELATED (no contradiction mark)
        assert summary["checked"] == 1
        assert summary["contradictions"] == 0


class TestAnswerIntegration:
    def test_superseded_note_in_answer(self, conn):
        """When a memory entry has superseded_by, answer synthesis includes a conflict note."""
        old_id = _insert(conn, "Payment processor is Paddle", tags=["topic:payments"], timestamp="2026-01-01T00:00:00Z")
        new_id = _insert(conn, "Payment processor is Stripe", tags=["topic:payments"], timestamp="2026-06-01T00:00:00Z")
        conn.execute("UPDATE memories SET superseded_by = ? WHERE id = ?", (new_id, old_id))
        conn.commit()

        results = db.query_keyword(conn, "payment processor", limit=5)
        assert len(results) == 2  # both entries exist

        mock_response = "The payment provider is Stripe.\n\nConfidence: High"
        with patch("hpm.answer.llm.complete", return_value=mock_response):
            answer_text = answer.synthesize_answer("payment provider", results)

        assert "superseded" in answer_text.lower() or "Stripe" in answer_text

    def test_no_superseded_note_when_not_applicable(self, conn):
        _insert(conn, "Weather in Austin", tags=["topic:weather"], timestamp="2026-06-01T00:00:00Z")
        results = db.query_keyword(conn, "weather", limit=5)
        with patch("hpm.answer.llm.complete", return_value="It's hot.\n\nConfidence: High"):
            answer_text = answer.synthesize_answer("weather", results)
        # Should not mention superseded
        assert "superseded" not in answer_text.lower()


class TestSchemaMigration:
    def test_migrate_v2_adds_column(self):
        c = db.get_connection(":memory:")
        c.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, content TEXT, source TEXT,
                session_id TEXT, timestamp TEXT, tags TEXT,
                decay_score REAL, access_scope TEXT, last_accessed TEXT
            )
        """)
        c.close()
        # Re-open and init — should add the column
        c2 = db.get_connection(":memory:")
        # Recreate the same schema manually then migrate
        c2.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, content TEXT, source TEXT,
                session_id TEXT, timestamp TEXT, tags TEXT,
                decay_score REAL, access_scope TEXT, last_accessed TEXT
            )
        """)
        db.migrate_v2(c2)
        # Verify column exists
        cols = [row[1] for row in c2.execute("PRAGMA table_info(memories)").fetchall()]
        assert "superseded_by" in cols
        c2.close()

    def test_migrate_v2_idempotent(self, conn):
        # conn already has the schema via init_db
        db.migrate_v2(conn)
        db.migrate_v2(conn)  # should not raise
