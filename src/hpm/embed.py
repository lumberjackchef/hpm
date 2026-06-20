"""Embedding service using fastembed (ONNX) for BGE-small.

Approximately 50x faster startup than sentence-transformers/PyTorch —
loads the model in ~0.1s (cached) instead of ~5.6s, and computes
embeddings in ~3ms instead of ~20ms.

Drop-in compatible: same model (BAAI/bge-small-en-v1.5), same 384d
vectors with negligible numerical difference (< 0.001 max diff).
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt

from . import config

logger = logging.getLogger(__name__)

_EMBEDDER: "Embedder | None" = None


class Embedder:
    """Thin wrapper around a fastembed model for local embeddings.

    Loads on first use (lazy). Underlying model is cached in memory
    after loading (~0.1s second call onwards).
    """

    def __init__(self, model_name: str = config.EMBEDDING_MODEL) -> None:
        from fastembed import TextEmbedding

        logger.info("loading embedding model: %s", model_name)
        self._model = TextEmbedding(model_name)
        # Get dimension by embedding a short string
        sample = next(self._model.embed(""))  # type: ignore[call-overload]
        self._dim = len(sample)
        logger.info("embedding model loaded (dim=%d)", self._dim)

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> npt.NDArray[np.float32]:
        """Compute a single embedding vector for *text*.

        Returns a float32 numpy array of shape ``(dim,)``.
        """
        vec = next(self._model.embed(text))  # type: ignore[call-overload]
        return np.asarray(vec, dtype=np.float32)

    def embed_many(self, texts: list[str]) -> npt.NDArray[np.float32]:
        """Compute embeddings for a batch of texts.

        Returns a float32 array of shape ``(len(texts), dim)``.
        """
        vecs = [np.asarray(v, dtype=np.float32) for v in self._model.embed(texts)]
        return np.array(vecs, dtype=np.float32)


def get_embedder() -> Embedder:
    """Return the global embedder singleton (lazy-loaded)."""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder()
    return _EMBEDDER


def embed_text(text: str) -> npt.NDArray[np.float32]:
    """One-shot convenience: embed a single string."""
    return get_embedder().embed(text)
