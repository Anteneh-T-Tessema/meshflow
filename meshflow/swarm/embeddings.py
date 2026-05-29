"""Semantic text embeddings for SwarmTRM — layered fallback design.

Priority chain (first available wins):
  1. sentence-transformers (real semantic embeddings, ~80M param model)
  2. numpy BoW + random projection (sparse semantic signal, zero extra deps if numpy present)
  3. Char n-gram hashing (deterministic, subword-level similarity, always available)

The fallback char n-gram embedder is *semantically meaningful* unlike the prior
hash-seeded torch.randn approach — texts sharing n-grams (words, prefixes, suffixes)
produce similar vectors. It is repeatable across runs and processes.

Usage (via engine.py)::

    from meshflow.swarm.embeddings import get_embedder
    embedder = get_embedder(dim=768)
    vec = embedder.embed("HIPAA minimum necessary disclosure")  # np.ndarray shape (768,)
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Any


# ── Char n-gram embedder (zero-dep fallback) ──────────────────────────────────

class CharNgramEmbedder:
    """Char n-gram hashing embedder.

    Produces vectors in ℝ^dim where each dimension accumulates hashed n-gram
    weights. Similar texts share n-grams → similar vectors.  No external deps.

    Properties:
    - Deterministic: same text → same vector across processes.
    - Similarity-preserving: texts sharing words/subwords get closer vectors.
    - Unit norm: cosine similarity is well-defined.
    """

    def __init__(self, dim: int = 768, ngram_sizes: tuple[int, ...] = (2, 3, 4)) -> None:
        self.dim = dim
        self.ngram_sizes = ngram_sizes

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _ngrams(self, text: str) -> list[str]:
        tokens = self._tokenize(text)
        grams: list[str] = []
        for token in tokens:
            padded = f"<{token}>"
            for n in self.ngram_sizes:
                grams += [padded[i:i + n] for i in range(len(padded) - n + 1)]
        return grams

    def embed(self, text: str) -> "list[float]":
        vec = [0.0] * self.dim
        grams = self._ngrams(text)
        if not grams:
            return vec
        for gram in grams:
            # Two independent hashes → two bucket positions for spread
            h1 = int(hashlib.md5(gram.encode()).hexdigest(), 16)
            h2 = int(hashlib.sha1(gram.encode()).hexdigest(), 16)
            idx1 = h1 % self.dim
            idx2 = h2 % self.dim
            sign1 = 1.0 if (h1 >> 32) & 1 else -1.0
            sign2 = 1.0 if (h2 >> 32) & 1 else -1.0
            vec[idx1] += sign1
            vec[idx2] += sign2
        # L2-normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


# ── Numpy BoW + random projection (medium quality) ────────────────────────────

class NumpyBowEmbedder:
    """Bag-of-words with random projection to *dim* using numpy.

    Vocabulary is built incrementally from seen texts.  Random projection matrix
    is seeded deterministically so embeddings are stable across runs.
    """

    def __init__(self, dim: int = 768, vocab_limit: int = 8192, seed: int = 42) -> None:
        import numpy as np
        self.dim = dim
        self.vocab_limit = vocab_limit
        self._vocab: dict[str, int] = {}
        rng = np.random.default_rng(seed)
        self._proj = rng.standard_normal((vocab_limit, dim)).astype("float32")
        # Normalize projection columns
        norms = np.linalg.norm(self._proj, axis=0, keepdims=True)
        norms[norms == 0] = 1.0
        self._proj /= norms

    def _words(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _vocab_idx(self, word: str) -> int:
        if word not in self._vocab:
            if len(self._vocab) < self.vocab_limit:
                self._vocab[word] = len(self._vocab)
            else:
                # Hash to existing slot when vocab is full
                return int(hashlib.md5(word.encode()).hexdigest(), 16) % self.vocab_limit
        return self._vocab[word]

    def embed(self, text: str) -> "Any":  # np.ndarray
        import numpy as np
        words = self._words(text)
        bow = np.zeros(self.vocab_limit, dtype="float32")
        for w in words:
            bow[self._vocab_idx(w)] += 1.0
        norm = np.linalg.norm(bow)
        if norm > 0:
            bow /= norm
        vec = bow @ self._proj  # shape (dim,)
        vnorm = np.linalg.norm(vec)
        return vec / vnorm if vnorm > 0 else vec


# ── SentenceTransformer embedder (highest quality) ────────────────────────────

class SentenceTransformerEmbedder:
    """Wraps sentence-transformers for real semantic embeddings.

    Model is loaded lazily and cached. Requires ``pip install sentence-transformers``.
    Falls back gracefully if not available.
    """

    _DEFAULT_MODEL = "all-MiniLM-L6-v2"  # 22M params, 384-dim, fast

    def __init__(self, dim: int = 768, model_name: str = _DEFAULT_MODEL) -> None:
        self.dim = dim
        self._model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, text: str) -> "Any":  # np.ndarray
        import numpy as np
        model = self._load()
        raw = model.encode([text], normalize_embeddings=True)[0]
        if len(raw) == self.dim:
            return raw
        # Project to target dim if mismatch
        rng = np.random.default_rng(0)
        proj = rng.standard_normal((len(raw), self.dim)).astype("float32")
        proj /= np.linalg.norm(proj, axis=0, keepdims=True).clip(1e-8)
        vec = raw @ proj
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec


# ── Factory ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=4)
def get_embedder(dim: int = 768) -> Any:
    """Return the best available embedder for *dim*-dimensional vectors.

    Cached: one embedder instance per dim per process.
    """
    # Tier 1: sentence-transformers
    try:
        import sentence_transformers  # noqa: F401
        emb = SentenceTransformerEmbedder(dim=dim)
        emb._load()  # verify it actually loads
        # Verify that numpy conversion works inside torch/sentence-transformers
        _ = emb.embed("test")
        return emb
    except Exception:
        pass

    # Tier 2: numpy BoW projection
    try:
        import numpy  # noqa: F401
        return NumpyBowEmbedder(dim=dim)
    except ImportError:
        pass

    # Tier 3: char n-gram hashing (zero deps, always available)
    return CharNgramEmbedder(dim=dim)


def embed_text(text: str, dim: int = 768) -> list[float]:
    """Convenience function: embed text and return a plain list of floats."""
    emb = get_embedder(dim)
    result = emb.embed(text)
    if hasattr(result, "tolist"):
        return result.tolist()
    return list(result)


__all__ = [
    "CharNgramEmbedder",
    "NumpyBowEmbedder",
    "SentenceTransformerEmbedder",
    "get_embedder",
    "embed_text",
]
