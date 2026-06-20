"""Cross-encoder reranker for Tier 2 recall.

Loads the model transiently on query, re-ranks candidates, then unloads.
Peak memory: ~200 MB while loaded, freed after each query.
"""

from __future__ import annotations

import logging
from typing import Any

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

# Default model — lightweight cross-encoder, good balance of speed/quality
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# How many candidates to feed the reranker (top-N from hybrid search)
RERANK_CANDIDATES = 10

# How many to keep after reranking
RERANK_KEEP = 5

# Singleton management
_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    """Load the cross-encoder model (lazy, transient)."""
    global _reranker
    if _reranker is None:
        logger.info("loading reranker model: %s", RERANKER_MODEL)
        _reranker = CrossEncoder(RERANKER_MODEL, device="cpu")
        logger.info("reranker loaded")
    return _reranker


def unload() -> None:
    """Unload the reranker model, freeing memory."""
    global _reranker
    _reranker = None
    logger.info("reranker unloaded")


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    keep: int = RERANK_KEEP,
) -> list[dict[str, Any]]:
    """Re-rank candidate memories by relevance to *query*.

    Args:
        query: The original user query.
        candidates: List of memory entries (from hybrid search).
        keep: How many top results to return.

    Returns:
        Candidates sorted by relevance (most relevant first), each with
        a ``rerank_score`` field added.
    """
    if not candidates:
        return []

    model = _get_reranker()
    pairs: list[tuple[str, str]] = [(query, str(c["content"])) for c in candidates]
    scores = model.predict(pairs, show_progress_bar=False)  # type: ignore[arg-type]

    for i, c in enumerate(candidates):
        c["rerank_score"] = float(scores[i])

    ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)[:keep]
    logger.debug(
        "reranked %d candidates → kept %d (top score: %.4f)",
        len(candidates),
        len(ranked),
        ranked[0]["rerank_score"] if ranked else 0,
    )
    return ranked
