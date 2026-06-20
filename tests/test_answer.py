"""Tests for the cited-answer synthesis."""

from unittest.mock import patch

import pytest

from hpm import answer


def test_synthesize_empty_results():
    """Returns 'I don't know' for empty results."""
    result = answer.synthesize_answer("anything", [])
    assert "don't know" in result.lower()


def test_synthesize_with_results():
    """Sends results as context and returns the API response."""
    mock_response = "The answer is 42.\n\nConfidence: High"

    with patch("hpm.answer.llm.complete", return_value=mock_response):
        results = [
            {"id": "abc", "content": "The meaning of life is 42", "timestamp": "2026-06-20T10:00:00Z"},
        ]
        answer_text = answer.synthesize_answer("what is the meaning of life?", results)

    assert "answer is 42" in answer_text
    assert "Confidence" in answer_text


def test_synthesize_includes_memory_context():
    """The memory entries are included in the prompt."""
    with patch("hpm.answer.llm.complete", return_value="Answer.") as mock:
        results = [
            {"id": "mem-1", "content": "Test content", "timestamp": "2026-01-01T00:00:00Z"},
        ]
        answer.synthesize_answer("test query", results)

    user_content = mock.call_args[1]["messages"][0]["content"]
    assert "mem-1" in user_content
    assert "Test content" in user_content
    assert "test query" in user_content
