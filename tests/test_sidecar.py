"""Tests for the sidecar module."""

import json
import tempfile
from pathlib import Path

import pytest

from hpm import sidecar


@pytest.fixture
def tmp_cursor(tmp_path: Path) -> Path:
    """Return a temporary path for the cursor file."""
    return tmp_path / ".sidecar-cursor.json"


def test_load_cursor_nonexistent(monkeypatch, tmp_cursor):
    """Returns empty dict when cursor file doesn't exist."""
    monkeypatch.setattr(sidecar, "CURSOR_FILE", tmp_cursor)
    assert sidecar._load_cursor() == {}


def test_load_cursor_valid(monkeypatch, tmp_cursor):
    """Returns parsed cursor data."""
    tmp_cursor.write_text(json.dumps({"_global": 42, "sess-1": 10}))
    monkeypatch.setattr(sidecar, "CURSOR_FILE", tmp_cursor)
    assert sidecar._load_cursor() == {"_global": 42, "sess-1": 10}


def test_load_cursor_corrupt(monkeypatch, tmp_cursor):
    """Returns empty dict on corrupted JSON."""
    tmp_cursor.write_text("not json")
    monkeypatch.setattr(sidecar, "CURSOR_FILE", tmp_cursor)
    assert sidecar._load_cursor() == {}


def test_save_cursor(monkeypatch, tmp_cursor):
    """Persists cursor to JSON file."""
    monkeypatch.setattr(sidecar, "CURSOR_FILE", tmp_cursor)
    sidecar._save_cursor({"_global": 99})
    assert json.loads(tmp_cursor.read_text()) == {"_global": 99}


def test_build_turns_no_pending():
    """No turns when there's no assistant response after user messages."""
    messages = [
        {"session_id": "s1", "role": "user", "content": "hello", "session_title": "Test"},
    ]
    assert sidecar.build_turns(messages, {}) == []


def test_build_turns_user_assistant_pair():
    """Creates a turn from a user→assistant pair."""
    messages = [
        {"session_id": "s1", "role": "user", "content": "hello", "session_title": "Test"},
        {"session_id": "s1", "role": "assistant", "content": "hi there", "session_title": "Test"},
    ]
    turns = sidecar.build_turns(messages, {})
    assert len(turns) == 1
    sess_id, title, user_content, assistant_content = turns[0]
    assert sess_id == "s1"
    assert title == "Test"
    assert user_content == "hello"
    assert assistant_content == "hi there"


def test_build_turns_multiple_sessions():
    """Handles interleaved messages from different sessions."""
    messages = [
        {"session_id": "s1", "role": "user", "content": "q1", "session_title": ""},
        {"session_id": "s2", "role": "user", "content": "q2", "session_title": ""},
        {"session_id": "s1", "role": "assistant", "content": "a1", "session_title": ""},
        {"session_id": "s2", "role": "assistant", "content": "a2", "session_title": ""},
    ]
    turns = sidecar.build_turns(messages, {})
    assert len(turns) == 2


def test_build_turns_skips_empty_content():
    """Skips user or assistant messages with empty content."""
    messages = [
        {"session_id": "s1", "role": "user", "content": "real question", "session_title": ""},
        {"session_id": "s1", "role": "assistant", "content": "", "session_title": ""},
        {"session_id": "s1", "role": "assistant", "content": "actual answer", "session_title": ""},
    ]
    turns = sidecar.build_turns(messages, {})
    # The assistant response with content should match with the pending user
    assert len(turns) == 1
    assert turns[0][3] == "actual answer"
