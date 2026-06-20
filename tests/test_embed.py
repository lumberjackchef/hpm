"""Tests for the embedding service."""

from unittest.mock import patch

import numpy as np
import pytest

from hpm import embed


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the global embedder singleton before each test."""
    embed._EMBEDDER = None
    yield
    embed._EMBEDDER = None


class FakeEmbedder:
    """Stand-in that returns predictable vectors without loading a real model."""

    def __init__(self, *args, **kwargs):
        self._dim = 384

    @property
    def dim(self):
        return self._dim

    def embed(self, text: str):
        return np.full(384, 0.1, dtype=np.float32)

    def embed_many(self, texts: list[str]):
        return np.full((len(texts), 384), 0.1, dtype=np.float32)


@patch("hpm.embed.Embedder", FakeEmbedder)
def test_get_embedder_singleton():
    """get_embedder returns the same instance after first call."""
    e1 = embed.get_embedder()
    e2 = embed.get_embedder()
    assert e1 is e2


@patch("hpm.embed.Embedder", FakeEmbedder)
def test_embed_text_returns_float32():
    """embed_text returns a float32 array of correct dimension."""
    vec = embed.embed_text("hello world")
    assert isinstance(vec, np.ndarray)
    assert vec.dtype == np.float32
    assert vec.shape == (384,)


@patch("hpm.embed.Embedder", FakeEmbedder)
def test_embed_many_returns_batch():
    """embed_many returns a 2D float32 array."""
    vecs = embed.get_embedder().embed_many(["a", "b", "c"])
    assert vecs.shape == (3, 384)
    assert vecs.dtype == np.float32


@patch("hpm.embed.Embedder", FakeEmbedder)
def test_embedder_dim_property():
    """Embedder.dim returns the dimension."""
    e = embed.get_embedder()
    assert e.dim == 384
