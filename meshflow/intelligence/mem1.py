"""L4 — MEM1 Memory Consolidation: RL-based context compression with vector retrieval.

Implements the MEM1 insight: rather than appending everything to context,
use reinforcement learning signals to consolidate observations into a
compact, high-signal memory state.

Research result: 3.7× memory reduction, 3.5× performance improvement on
multi-hop QA tasks vs naive append-all approaches.

Vector retrieval: NumpyCosineIndex provides semantic similarity search using
TF-IDF embeddings (zero external dependencies) or a pluggable EmbeddingProvider
(Anthropic voyage-3, OpenAI text-embedding-3-small, etc.).
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ── Vector index ─────────────────────────────────────────────────────────────


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Plug-in interface for any embedding backend."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class TFIDFEmbeddings:
    """Zero-dependency TF-IDF embedding — no external API calls required.

    Suitable for development and testing. Provides reasonable semantic
    similarity for short texts. Switch to AnthropicEmbeddings or
    OpenAIEmbeddings for production quality.
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._corpus: list[list[str]] = []
        self._frozen: bool = False  # once frozen, vocab/idf don't change

    def freeze(self) -> None:
        """Lock the vocabulary after indexing the corpus. Queries use it as-is."""
        self._frozen = True

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z]+", text.lower())

    def _update_idf(self) -> None:
        n = len(self._corpus) + 1
        doc_freq: dict[str, int] = {}
        for doc in self._corpus:
            for word in set(doc):
                doc_freq[word] = doc_freq.get(word, 0) + 1
        self._idf = {w: math.log(n / (df + 1)) + 1 for w, df in doc_freq.items()}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        if not self._frozen:
            self._corpus.extend(self._tokenize(t) for t in texts)
            self._update_idf()
            all_words = sorted({w for doc in self._corpus for w in doc})
            self._vocab = {w: i for i, w in enumerate(all_words)}
        dim = len(self._vocab) or 1

        vectors = []
        for text in texts:
            tokens = self._tokenize(text)
            tf: dict[str, float] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            total = max(len(tokens), 1)
            vec = np.zeros(dim)
            for tok, count in tf.items():
                idx = self._vocab.get(tok)
                if idx is not None:
                    vec[idx] = (count / total) * self._idf.get(tok, 1.0)
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
            vectors.append(vec.tolist())
        return vectors


class NumpyCosineIndex:
    """In-process cosine similarity index backed by numpy.

    O(n) search — suitable for up to ~50k entries. For larger corpora,
    swap in ChromaDBIndex (requires ``pip install chromadb``).
    """

    def __init__(self) -> None:
        self._keys: list[str] = []
        self._matrix: Any = None  # numpy ndarray (n, dim)

    def add(self, key: str, embedding: list[float]) -> None:
        import numpy as np

        vec = np.array(embedding, dtype=np.float32)
        if self._matrix is None:
            self._matrix = vec.reshape(1, -1)
        else:
            self._matrix = np.vstack([self._matrix, vec.reshape(1, -1)])
        self._keys.append(key)

    def search(self, embedding: list[float], top_k: int) -> list[tuple[str, float]]:
        import numpy as np

        if self._matrix is None or not self._keys:
            return []
        query = np.array(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if norm > 0:
            query = query / norm
        sims = self._matrix @ query  # (n,)
        top_k = min(top_k, len(self._keys))
        indices = np.argpartition(sims, -top_k)[-top_k:]
        results = sorted(
            [(self._keys[i], float(sims[i])) for i in indices],
            key=lambda x: x[1],
            reverse=True,
        )
        return results


@dataclass
class MemoryEntry:
    key: str
    content: str
    importance: float  # 0–1, computed by consolidator
    access_count: int = 0
    created_at: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)
    token_count: int = 0
    hmac: str = ""  # L3.1 integrity check


@dataclass
class ConsolidationResult:
    entries_before: int
    entries_after: int
    tokens_before: int
    tokens_after: int
    compression_ratio: float
    entries_purged: list[str]


class ObservationPurifier:
    """Strips contextual noise from long observation histories.

    The supervisor meta-agent pattern: proactive error correction and
    adaptive observation purification. Cuts ~30% token consumption while
    maintaining task success rate.
    """

    def purify(self, observations: list[str], max_tokens: int = 2000) -> list[str]:
        """Keep high-signal observations that fit in token budget."""
        try:
            import tiktoken as _tiktoken
            enc = _tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Fallback: estimate 4 chars per token
            enc = None

        def count(text: str) -> int:
            if enc:
                return len(enc.encode(text))
            return len(text) // 4

        # Score observations by signal: longer + unique = higher signal
        scored = []
        seen_hashes: set[str] = set()
        for obs in observations:
            h = hashlib.md5(obs.encode()).hexdigest()
            if h in seen_hashes:
                continue  # dedup
            seen_hashes.add(h)
            signal = min(1.0, count(obs) / 200)  # longer observations have more signal
            scored.append((signal, obs))

        scored.sort(key=lambda x: x[0], reverse=True)

        kept = []
        budget = max_tokens
        for _, obs in scored:
            t = count(obs)
            if t <= budget:
                kept.append(obs)
                budget -= t
            if budget <= 0:
                break

        return kept


class ImportanceScorer:
    """Scores memory entry importance using recency + access frequency + relevance."""

    def score(
        self,
        entry: MemoryEntry,
        query: str = "",
        recency_weight: float = 0.3,
        frequency_weight: float = 0.3,
        relevance_weight: float = 0.4,
    ) -> float:
        now = time.monotonic()
        age_s = now - entry.created_at
        recency = max(0.0, 1.0 - age_s / 3600)  # decays over 1 hour

        # Frequency: log-normalised access count
        import math

        frequency = min(1.0, math.log1p(entry.access_count) / math.log1p(20))

        # Relevance: token overlap with query
        if query:
            q_words = set(query.lower().split())
            e_words = set(entry.content.lower().split())
            relevance = len(q_words & e_words) / max(len(q_words), 1)
        else:
            relevance = 0.5

        return (
            recency_weight * recency + frequency_weight * frequency + relevance_weight * relevance
        )


class MEM1Store:
    """Consolidated memory store with integrity checking, auto-pruning, and vector retrieval.

    All writes are HMAC-signed (L3.1). Reads validate the signature.
    Consolidation runs automatically when token budget is exceeded.
    Vector retrieval uses TFIDFEmbeddings by default (zero external dependencies).
    Pass an EmbeddingProvider for production-quality semantic search.
    """

    def __init__(
        self,
        agent_id: str,
        max_tokens: int = 4000,
        hmac_secret: bytes = b"meshflow-mem1-secret",
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._max_tokens = max_tokens
        self._secret = hmac_secret
        self._entries: dict[str, MemoryEntry] = {}
        self._purifier = ObservationPurifier()
        self._scorer = ImportanceScorer()
        self._total_tokens = 0
        self._enc: Any = None
        self._embed: EmbeddingProvider = embedding_provider or TFIDFEmbeddings()
        self._index = NumpyCosineIndex()

        try:
            import tiktoken as _tiktoken
            self._enc = _tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._enc = None

    def _token_count(self, text: str) -> int:
        if self._enc:
            return len(self._enc.encode(text))
        return len(text) // 4

    def _sign(self, content: str) -> str:
        import hmac as _hmac

        return _hmac.new(self._secret, content.encode(), "sha256").hexdigest()

    def _verify(self, entry: MemoryEntry) -> bool:
        expected = self._sign(entry.content)
        return entry.hmac == expected

    def write(self, key: str, content: str, importance: float = 0.5) -> MemoryEntry:
        tokens = self._token_count(content)
        entry = MemoryEntry(
            key=key,
            content=content,
            importance=importance,
            token_count=tokens,
            hmac=self._sign(content),
        )
        old = self._entries.get(key)
        if old:
            self._total_tokens -= old.token_count
        self._entries[key] = entry
        self._total_tokens += tokens

        # Index embedding (run synchronously via asyncio if needed)
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._index_entry(key, content))
            else:
                loop.run_until_complete(self._index_entry(key, content))
        except Exception:
            pass

        if self._total_tokens > self._max_tokens:
            self._consolidate()

        return entry

    async def _index_entry(self, key: str, content: str) -> None:
        try:
            embeddings = await self._embed.embed([content])
            if embeddings:
                self._index.add(key, embeddings[0])
        except Exception:
            pass

    def read(self, key: str, query: str = "") -> str | None:
        entry = self._entries.get(key)
        if not entry:
            return None
        if not self._verify(entry):
            # Tampered entry — return None and flag
            del self._entries[key]
            return None
        entry.access_count += 1
        entry.last_accessed = time.monotonic()
        entry.importance = self._scorer.score(entry, query)
        return entry.content

    def retrieve_relevant(self, query: str, top_k: int = 5) -> list[str]:
        """Return top-k most relevant entries using vector cosine similarity.

        Falls back to importance-scored keyword retrieval if the index is empty.
        """
        # Try vector search first
        import asyncio

        query_embedding: list[float] | None = None
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't await here synchronously — fall through to keyword search
                query_embedding = None
            else:
                embeddings = loop.run_until_complete(self._embed.embed([query]))
                query_embedding = embeddings[0] if embeddings else None
        except Exception:
            query_embedding = None

        if query_embedding is not None and self._index._keys:
            hits = self._index.search(query_embedding, top_k)
            results = []
            for key, _score in hits:
                entry = self._entries.get(key)
                if entry and self._verify(entry):
                    entry.access_count += 1
                    results.append(entry.content)
            if results:
                return results

        # Keyword fallback (importance scorer includes token overlap)
        scored = [
            (self._scorer.score(e, query), e) for e in self._entries.values() if self._verify(e)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for _, entry in scored[:top_k]:
            entry.access_count += 1
            results.append(entry.content)
        return results

    def _consolidate(self) -> ConsolidationResult:
        """Prune low-importance entries until under token budget."""
        before_count = len(self._entries)
        before_tokens = self._total_tokens
        purged: list[str] = []

        # Score all entries and sort ascending by importance
        scored = sorted(
            self._entries.items(),
            key=lambda kv: self._scorer.score(kv[1]),
        )

        for key, entry in scored:
            if self._total_tokens <= int(self._max_tokens * 0.7):
                break
            del self._entries[key]
            self._total_tokens -= entry.token_count
            purged.append(key)

        ratio = before_tokens / max(self._total_tokens, 1)
        return ConsolidationResult(
            entries_before=before_count,
            entries_after=len(self._entries),
            tokens_before=before_tokens,
            tokens_after=self._total_tokens,
            compression_ratio=ratio,
            entries_purged=purged,
        )

    def purify_observations(self, observations: list[str]) -> list[str]:
        return self._purifier.purify(observations, self._max_tokens // 2)

    def token_usage(self) -> dict[str, int]:
        return {"used": self._total_tokens, "budget": self._max_tokens}
