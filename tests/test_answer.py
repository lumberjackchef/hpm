"""Tests for the cited-answer synthesis."""

from unittest.mock import patch

import httpx
import pytest

from hpm import answer


def test_synthesize_empty_results():
    """Returns 'I don't know' for empty results."""
    result = answer.synthesize_answer("anything", [], api_key="test")
    assert "don't know" in result.lower()


def test_synthesize_missing_api_key():
    """Raises ValueError when API key is not set."""
    with pytest.raises(ValueError, match="OPENCODE_GO_API_KEY"):
        answer.synthesize_answer("query", [{"id": "1", "content": "test", "timestamp": "now"}])


@patch("hpm.answer.httpx.post")
def test_synthesize_with_results(mock_post):
    """Sends results as context and returns the API response."""
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "choices": [{"message": {"content": "The answer is 42.\n\nConfidence: High"}}]
    }

    results = [
        {"id": "abc", "content": "The meaning of life is 42", "timestamp": "2026-06-20T10:00:00Z"},
    ]

    answer_text = answer.synthesize_answer(
        "what is the meaning of life?",
        results,
        api_key="sk-test",
        base_url="https://fake.example.com/v1",
    )
    assert "answer is 42" in answer_text
    assert "Confidence" in answer_text


@patch("hpm.answer.httpx.post")
def test_synthesize_includes_memory_context(mock_post):
    """The memory entries are included in the prompt."""
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "choices": [{"message": {"content": "Answer."}}]
    }

    results = [
        {"id": "mem-1", "content": "Test content", "timestamp": "2026-01-01T00:00:00Z"},
    ]

    answer.synthesize_answer(
        "test query", results, api_key="sk-test", base_url="https://fake.example.com/v1"
    )

    args, kwargs = mock_post.call_args
    prompt = kwargs["json"]["messages"][1]["content"]
    assert "mem-1" in prompt
    assert "Test content" in prompt
    assert "test query" in prompt


@patch("hpm.answer.httpx.post")
def test_synthesize_raises_on_http_error(mock_post):
    """Raises on API failure."""
    mock_post.side_effect = httpx.HTTPStatusError(
        "500 error", request=httpx.Request("POST", "http://fake"), response=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        answer.synthesize_answer(
            "q", [{"id": "1", "content": "test", "timestamp": "now"}],
            api_key="key", base_url="http://fake/v1",
        )
