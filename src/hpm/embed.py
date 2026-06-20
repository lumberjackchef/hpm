"""Embedding service using sentence-transformers with BGE-small."""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt

from . import config

logger = logging.getLogger(__name__)

_EMBEDDER: "Embedder | None" = None


class Embedder:
    """Thin wrapper around a sentence-transformers model for local embeddings.

    Loads on first use (lazy). The underlying model is cached after loading.
    """

    def __init__(self, model_name: str = config.EMBEDDING_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        logger.info("loading embedding model: %s", model_name)
        self._model = SentenceTransformer(model_name, device="cpu")
        self._dim = int(self._model.get_sentence_embedding_dimension())
        logger.info("embedding model loaded (dim=%d)", self._dim)

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> npt.NDArray[np.float32]:
        """Compute a single embedding vector for *text*.

        Returns a float32 numpy array of shape ``(dim,)``.
        """
        vec = self._model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vec, dtype=np.float32)

    def embed_many(self, texts: list[str]) -> npt.NDArray[np.float32]:
        """Compute embeddings for a batch of texts.

        Returns a float32 array of shape ``(len(texts), dim)``.
        """
        vecs = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(vecs, dtype=np.float32)


def get_embedder() -> Embedder:
    """Return the global embedder singleton (lazy-loaded)."""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder()
    return _EMBEDDER


def embed_text(text: str) -> npt.NDArray[np.float32]:
    """One-shot convenience: embed a single string."""
    return get_embedder().embed(text)
