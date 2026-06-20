"""Tests for the daily log writer."""

import tempfile
from pathlib import Path

import pytest

from hpm import daily


@pytest.fixture
def tmp_log_dir():
    """Temporarily override the daily log directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def test_append_to_daily_log_creates_file(tmp_log_dir):
    """append_to_daily_log creates the daily markdown file."""
    file_path = daily.append_to_daily_log(
        "Test memory entry",
        source="hermes",
        session_id="sess-1",
        tags=["project:jarvis"],
        log_dir=str(tmp_log_dir),
    )

    # Inject the override
    # Actually, the function reads from config.DAILY_LOG which is set from env.
    # Let me just verify the file was created by checking the return path.
    assert file_path is not None
    p = Path(file_path)
    assert p.exists()
    content = p.read_text()
    assert "Test memory entry" in content
    assert "hermes" in content
    assert "project:jarvis" in content


def test_append_to_daily_log_multiple_entries(tmp_log_dir):
    """Multiple calls append to the same file."""
    p1 = daily.append_to_daily_log("First", tags=[], log_dir=str(tmp_log_dir))
    p2 = daily.append_to_daily_log("Second", tags=[], log_dir=str(tmp_log_dir))
    assert p1 == p2
    content = Path(p1).read_text()
    assert content.count("- **") == 2
