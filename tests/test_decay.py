"""Tests for decay computation, reinforcement, and spot-check."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest

from hpm import db as db_module
from hpm import decay


@pytest.fixture
def conn():
    c = db_module.get_connection(":memory:")
    db_module.init_db(c)
    yield c
    c.close()


def _hours_ago(hours: int) -> str:
    """Return an ISO timestamp *hours* in the past."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


class TestComputeDecayScore:
    def test_no_last_accessed_returns_as_is(self):
        """When last_accessed is None, the score is returned unchanged."""
        assert db_module.compute_decay_score(0.8, None) == 0.8

    def test_recent_access_returns_high_score(self):
        """Access within the last minute barely decays."""
        score = db_module.compute_decay_score(1.0, _hours_ago(0), half_life=168)
        assert score > 0.99

    def test_one_week_decay_halves_score(self):
        """After one half-life (168h) without reinforcement, score halves."""
        score = db_module.compute_decay_score(1.0, _hours_ago(168), half_life=168)
        assert 0.45 < score < 0.55, f"expected ~0.5, got {score}"

    def test_two_weeks_decay_quarters_score(self):
        """After two half-lives (336h), score quarters."""
        score = db_module.compute_decay_score(1.0, _hours_ago(336), half_life=168)
        assert 0.20 < score < 0.30, f"expected ~0.25, got {score}"

    def test_future_timestamp_returns_1(self):
        """A future last_accessed resets score to 1.0."""
        score = db_module.compute_decay_score(0.5, _hours_ago(-1))
        assert score == 1.0

    def test_invalid_timestamp_returns_as_is(self):
        """An unparseable timestamp returns the score unchanged."""
        score = db_module.compute_decay_score(0.5, "not-a-timestamp")
        assert score == 0.5

    def test_custom_half_life(self):
        """Custom half-life shortens or lengthens decay."""
        hour_ago = _hours_ago(1)

        short = db_module.compute_decay_score(1.0, hour_ago, half_life=1)
        assert 0.4 < short < 0.6, f"expected ~0.5, got {short}"

        long = db_module.compute_decay_score(1.0, hour_ago, half_life=8760)
        assert long > short


class TestReinforce:
    def test_sets_score_to_one(self, conn):
        """reinforce resets decay_score to 1.0."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        mid = db_module.insert_memory(conn, "test", vec)
        # Manually lower the score
        conn.execute("UPDATE memories SET decay_score = 0.3 WHERE id = ?", (mid,))
        conn.commit()
        # Reinforce
        db_module.reinforce(conn, mid)
        row = db_module.get_memory_by_id(conn, mid)
        assert row["decay_score"] == 1.0

    def test_updates_last_accessed(self, conn):
        """reinforce updates last_accessed to a recent timestamp."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        mid = db_module.insert_memory(conn, "test", vec)
        db_module.reinforce(conn, mid)
        row = db_module.get_memory_by_id(conn, mid)
        assert row["last_accessed"] is not None
        # Should be a recent ISO timestamp
        ts = datetime.fromisoformat(row["last_accessed"])
        assert abs((datetime.now(timezone.utc) - ts).total_seconds()) < 10


class TestRunDecay:
    def test_returns_zero_on_empty_store(self, conn):
        """run_decay on an empty store returns 0."""
        assert db_module.run_decay(conn) == 0

    def test_updates_scores(self, conn):
        """run_decay lowers scores for old entries."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        mid = db_module.insert_memory(conn, "old entry", vec)
        # Manually set last_accessed way back
        old_ts = "2020-01-01T00:00:00Z"
        conn.execute(
            "UPDATE memories SET last_accessed = ?, decay_score = 1.0 WHERE id = ?",
            (old_ts, mid),
        )
        conn.commit()

        updated = db_module.run_decay(conn, half_life=168)
        assert updated >= 1

        row = db_module.get_memory_by_id(conn, mid)
        assert row is not None
        assert row["decay_score"] < 1.0

    def test_skips_superseded_entries(self, conn):
        """run_decay only processes active entries (superseded_by IS NULL)."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        mid = db_module.insert_memory(conn, "active", vec)
        # Mark as superseded
        conn.execute("UPDATE memories SET superseded_by = 'other-id' WHERE id = ?", (mid,))
        conn.commit()

        updated = db_module.run_decay(conn)
        assert updated == 0


class TestSpotCheck:
    def test_empty_store_returns_empty(self, conn):
        """run_spot_check on empty store returns empty list."""
        result = decay.run_spot_check(conn)
        assert result == []

    def test_skips_when_no_llm_key(self, conn):
        """Missing API key logs warning and returns entries unchanged."""
        vec = np.array([0.1] * 384, dtype=np.float32)
        db_module.insert_memory(conn, "test entry", vec)

        with patch("hpm.decay.llm.complete") as mock_complete:
            mock_complete.side_effect = ValueError("No API key configured")
            result = decay.run_spot_check(conn)
        # Should return entries without modification
        assert len(result) == 1
        assert result[0]["content"] == "test entry"

    def test_adjusts_scores_from_ratings(self, conn):
        """LLM ratings are applied as score adjustments."""
        id1 = db_module.insert_memory(conn, "entry1", np.array([0.1] * 384, dtype=np.float32))
        id2 = db_module.insert_memory(conn, "entry2", np.array([0.9] * 384, dtype=np.float32))
        # Both entries need low scores to be included in the spot-check
        conn.execute("UPDATE memories SET decay_score = 0.3 WHERE id = ?", (id1,))
        conn.execute("UPDATE memories SET decay_score = 0.4 WHERE id = ?", (id2,))
        conn.commit()

        feedback = "1: VALID - still accurate\n2: STALE - outdated"
        with patch("hpm.decay.llm.complete", return_value=feedback):
            result = decay.run_spot_check(conn)

        # Entry 1 was VALID → +0.05
        assert result[0]["decay_score"] == pytest.approx(0.35, abs=0.01)
        # Entry 2 was STALE → -0.3
        assert result[1]["decay_score"] == pytest.approx(0.1, abs=0.01)
