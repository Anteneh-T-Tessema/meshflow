"""Sprint 52 — Pluggable embedding providers for semantic memory.

Provider hierarchy (auto-selected best available)
-------------------------------------------------
HashEmbeddingProvider          — char n-gram hashing trick; zero deps; always
                                  available; 256-dim unit vectors.
SentenceTransformerProvider    — wraps ``sentence-transformers``; 384-dim
                                  all-MiniLM-L6-v2 embeddings when installed.

Usage
-----
    from meshflow.intelligence.embedding import get_embedding_provider, embed_text

    provider = get_embedding_provider()            # auto-select
    vecs = provider.embed(["hello world", "hi"])   # list[list[float]]
    sim  = cosine_similarity(vecs[0], vecs[1])
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from typing import Optional


# ── ABC ───────────────────────────────────────────────────────────────────────

class EmbeddingProvider(ABC):
    """Abstract base for text embedding backends.

    All implementations produce L2-normalised floating-point vectors of a
    fixed dimension so that cosine similarity == dot product.
    """

    @property
    @abstractmethod
    def dim(self) -> int:
        """Embedding dimensionality."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one unit-length vector per input text."""


# ── Math helpers ──────────────────────────────────────────────────────────────

def _l2_norm(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in vec))
    if mag == 0.0:
        return vec[:]
    return [x / mag for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [-1, 1] between two pre-normalised vectors."""
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))


# ── HashEmbeddingProvider ─────────────────────────────────────────────────────

class HashEmbeddingProvider(EmbeddingProvider):
    """Character n-gram hashing trick — zero external dependencies.

    Extracts character n-grams (n = 2, 3, 4) from text, hashes each into
    a bucket in ``[0, dim)``, accumulates counts, and L2-normalises.

    Quality is lower than sentence-transformers but sufficient for
    keyword-adjacent retrieval and is deterministic and instant.

    Parameters
    ----------
    dim:   Vector dimensionality (default 256).  Must be a positive integer.
    ngram: Maximum n-gram size (default 4).
    """

    def __init__(self, dim: int = 256, ngram: int = 4) -> None:
        if dim < 1:
            raise ValueError("dim must be ≥ 1")
        self._dim   = dim
        self._ngram = ngram

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"hash-ngram-{self._dim}d"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        normalised = re.sub(r"\s+", " ", text.lower().strip())
        # Add word-level unigrams with higher weight
        for token in normalised.split():
            idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dim
            vec[idx] += 2.0
        # Add character n-grams
        for n in range(2, self._ngram + 1):
            for i in range(len(normalised) - n + 1):
                gram = normalised[i : i + n]
                idx  = int(hashlib.md5(gram.encode()).hexdigest(), 16) % self._dim
                vec[idx] += 1.0
        return _l2_norm(vec)


# ── SentenceTransformerProvider ───────────────────────────────────────────────

class SentenceTransformerProvider(EmbeddingProvider):
    """Wraps ``sentence-transformers`` for high-quality semantic embeddings.

    Parameters
    ----------
    model_name:
        HuggingFace model ID.  Default ``"all-MiniLM-L6-v2"`` (384 dims,
        fast, good quality).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            ) from exc
        self._model      = SentenceTransformer(model_name)
        self._model_name = model_name
        self._dim_val: Optional[int] = None

    @property
    def dim(self) -> int:
        if self._dim_val is None:
            sample = self._model.encode(["probe"], convert_to_numpy=True)
            self._dim_val = int(sample.shape[1])
        return self._dim_val

    @property
    def name(self) -> str:
        return f"sentence-transformers/{self._model_name}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]


# ── Auto-selection ────────────────────────────────────────────────────────────

_default_provider: Optional[EmbeddingProvider] = None


def get_embedding_provider(prefer: str = "auto") -> EmbeddingProvider:
    """Return the best available embedding provider.

    Parameters
    ----------
    prefer:
        ``"auto"``  — try sentence-transformers, fall back to hash.
        ``"hash"``  — always use ``HashEmbeddingProvider``.
        ``"st"``    — always use ``SentenceTransformerProvider`` (raises if absent).
    """
    global _default_provider

    if prefer == "hash":
        return HashEmbeddingProvider()

    if prefer == "st":
        return SentenceTransformerProvider()

    if prefer == "auto":
        if _default_provider is None:
            try:
                _default_provider = SentenceTransformerProvider()
            except ImportError:
                _default_provider = HashEmbeddingProvider()
        return _default_provider

    raise ValueError(f"Unknown prefer value: {prefer!r}")


def reset_embedding_provider() -> None:
    """Reset the cached default provider (test helper)."""
    global _default_provider
    _default_provider = None


def embed_text(text: str, provider: Optional[EmbeddingProvider] = None) -> list[float]:
    """Convenience function — embed a single string."""
    p = provider or get_embedding_provider()
    return p.embed([text])[0]
