"""Tests for the summarization client."""

from unittest.mock import patch

import pytest

from hpm import summarize


def test_summarize_turn_success():
    """Successfully returns the summary content."""
    with patch("hpm.summarize.llm.complete", return_value="- Did something\n- Decided X"):
        result = summarize.summarize_turn("Hello world")
    assert result == "- Did something\n- Decided X"


def test_summarize_turn_sends_turn_text():
    """Sends the turn text as the user message."""
    with patch("hpm.summarize.llm.complete", return_value="- summary") as mock:
        summarize.summarize_turn("my turn text")
    mock.assert_called_once()
    assert mock.call_args[1]["messages"][0]["content"] == "my turn text"
    assert mock.call_args[1]["max_tokens"] == 256
    assert mock.call_args[1]["temperature"] == 0.3
