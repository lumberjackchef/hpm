"""Tests for the summarization client."""

from unittest.mock import patch

import httpx
import pytest

from hpm import summarize


def test_summarize_turn_missing_api_key():
    """Raises ValueError when API key is not set."""
    with pytest.raises(ValueError, match="OPENCODE_GO_API_KEY"):
        summarize.summarize_turn("some text", api_key="")


@patch("hpm.summarize.httpx.post")
def test_summarize_turn_success(mock_post):
    """Successfully returns the summary content."""
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "choices": [{"message": {"content": "- Did something\n- Decided X"}}]
    }

    result = summarize.summarize_turn(
        "Hello world",
        api_key="test-key",
        base_url="https://fake.example.com/v1",
    )
    assert result == "- Did something\n- Decided X"
    mock_post.assert_called_once()


@patch("hpm.summarize.httpx.post")
def test_summarize_turn_raises_on_http_error(mock_post):
    """Raises httpx.HTTPError on failure."""
    mock_post.side_effect = httpx.HTTPStatusError(
        "400 error", request=httpx.Request("POST", "http://fake"), response=httpx.Response(400)
    )
    with pytest.raises(httpx.HTTPStatusError):
        summarize.summarize_turn("test", api_key="key", base_url="http://fake/v1")


@patch("hpm.summarize.httpx.post")
def test_summarize_turn_sends_expected_payload(mock_post):
    """Sends the correct request body and headers."""
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "choices": [{"message": {"content": "- summary"}}]
    }

    summarize.summarize_turn("my turn text", api_key="sk-abc", base_url="https://test.url/v1")

    args, kwargs = mock_post.call_args
    assert args[0] == "https://test.url/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-abc"
    assert kwargs["json"]["model"] == "minimax-m2.5"
    assert kwargs["json"]["messages"][1]["content"] == "my turn text"
