"""Tests for the cross-encoder reranker."""

from unittest.mock import patch

import numpy as np
import pytest

from hpm import rerank


class FakeCrossEncoder:
    """Stand-in that returns predictable scores."""

    def __init__(self, *args, **kwargs):
        pass

    def predict(self, pairs, show_progress_bar=False):
        # Return scores that match the input order: longer content = higher score
        return np.array([float(len(p[1])) for p in pairs])


@patch("hpm.rerank._get_reranker")
def test_rerank_returns_top_k(mock_get):
    """rerank returns the top keep results."""
    mock_get.return_value = FakeCrossEncoder()
    candidates = [
        {"id": "1", "content": "short"},
        {"id": "2", "content": "medium length text"},
        {"id": "3", "content": "this is the longest content here"},
    ]
    results = rerank.rerank("test query", candidates, keep=2)
    assert len(results) == 2
    # Longest content should rank highest
    assert results[0]["id"] == "3"
    assert results[1]["id"] == "2"


@patch("hpm.rerank._get_reranker")
def test_rerank_adds_rerank_score(mock_get):
    """Each result gets a rerank_score field."""
    mock_get.return_value = FakeCrossEncoder()
    candidates = [{"id": "1", "content": "test"}]
    results = rerank.rerank("query", candidates)
    assert "rerank_score" in results[0]
    assert isinstance(results[0]["rerank_score"], float)


@patch("hpm.rerank._get_reranker")
def test_rerank_empty(mock_get):
    """rerank returns empty list for empty input."""
    mock_get.return_value = FakeCrossEncoder()
    assert rerank.rerank("query", []) == []


@patch("hpm.rerank._get_reranker")
def test_rerank_singleton(mock_get):
    """rerank single candidate returns it."""
    mock_get.return_value = FakeCrossEncoder()
    candidates = [{"id": "1", "content": "test"}]
    results = rerank.rerank("query", candidates)
    assert len(results) == 1
    assert results[0]["id"] == "1"


def test_unload_no_error():
    """unload can be called even if model wasn't loaded."""
    rerank.unload()  # should not raise
