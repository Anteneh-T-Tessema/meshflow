"""Native RAG / Knowledge — give any agent document awareness with zero extra setup.

MeshFlow's knowledge system is dependency-free by default (numpy optional,
sentence-transformers optional). Embeddings degrade gracefully:
  1. sentence-transformers (rich semantic similarity, if installed)
  2. numpy BoW (decent, no extra deps beyond numpy)
  3. pure-Python char n-gram (always available, offline, no deps at all)

Usage (Agent API):
    from meshflow import Agent
    from meshflow.intelligence.knowledge import VectorStore, KnowledgeSource

    # Option A — just pass file paths / texts / URLs as strings
    agent = Agent(
        name="analyst",
        role="researcher",
        knowledge=["report.pdf", "overview.txt", "https://docs.example.com"],
    )

    # Option B — build a shared VectorStore and reuse it across agents
    store = VectorStore.from_texts([
        "MeshFlow is a governed multi-agent orchestration framework.",
        "It supports HIPAA, SOX, GDPR compliance out of the box.",
    ])
    agent = Agent(name="analyst", role="researcher", knowledge=[store])

    # Option C — fine-grained KnowledgeSource control
    from meshflow.intelligence.knowledge import KnowledgeSource
    agent = Agent(
        name="analyst",
        role="researcher",
        knowledge=[KnowledgeSource(source="legal_docs/", chunk_size=300, top_k=5)],
    )

Task-level knowledge (overrides agent knowledge for one task):
    from meshflow import Task
    task = Task(
        description="Summarise the attached contract",
        expected_output="3-bullet executive summary",
        agent=lawyer,
        knowledge=["contract.pdf"],
    )
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ── Embedding utilities (zero-dep fallback) ───────────────────────────────────

def _char_ngram_vector(text: str, n: int = 3, dim: int = 512) -> list[float]:
    """Pure-Python char n-gram hashed into a fixed-dim float vector."""
    text = text.lower()
    counts: dict[int, float] = {}
    for i in range(len(text) - n + 1):
        gram = text[i:i + n]
        idx = hash(gram) % dim
        counts[idx] = counts.get(idx, 0.0) + 1.0
    vec = [counts.get(i, 0.0) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _cosine_pure(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot  # already unit vectors from _char_ngram_vector


def _embed_texts_default(texts: list[str]) -> list[list[float]]:
    """Try numpy BoW → fall back to char n-gram."""
    try:
        import numpy as np

        # Simple BoW with hashing trick (faster than pure Python)
        dim = 1024
        vecs = []
        for text in texts:
            words = re.findall(r"\b\w+\b", text.lower())
            arr = np.zeros(dim, dtype=np.float32)
            for w in words:
                arr[hash(w) % dim] += 1.0
            norm = np.linalg.norm(arr)
            vecs.append((arr / norm if norm > 0 else arr).tolist())
        return vecs
    except ImportError:
        return [_char_ngram_vector(t) for t in texts]


# ── VectorStore ───────────────────────────────────────────────────────────────

class VectorStore:
    """In-memory vector store with semantic search.

    Zero external dependencies — uses a graceful embedding chain:
    sentence-transformers → numpy BoW → char n-gram.

    Parameters
    ----------
    embed_fn:   Custom embedding function ``(texts: list[str]) -> list[list[float]]``.
                Defaults to the built-in chain above.
    """

    def __init__(self, embed_fn: Callable[[list[str]], list[list[float]]] | None = None) -> None:
        self._texts: list[str] = []
        self._vectors: list[list[float]] = []
        self._embed_fn = embed_fn or _embed_texts_default

    # ── Construction ──────────────────────────────────────────────────────────

    def add_texts(self, texts: list[str]) -> "VectorStore":
        if not texts:
            return self
        new_vecs = self._embed_fn(texts)
        self._texts.extend(texts)
        self._vectors.extend(new_vecs)
        return self

    @classmethod
    def from_texts(cls, texts: list[str], **kwargs: Any) -> "VectorStore":
        store = cls(**kwargs)
        store.add_texts(texts)
        return store

    @classmethod
    def from_file(
        cls,
        path: str,
        chunk_size: int = 500,
        overlap: int = 50,
        **kwargs: Any,
    ) -> "VectorStore":
        """Load and chunk a file into the store.

        Supported: .txt, .md, .py, .json, .yaml, .yml, .csv  (UTF-8 text).
        PDF support requires the ``pypdf`` package (graceful skip otherwise).
        """
        store = cls(**kwargs)
        chunks = _load_and_chunk(path, chunk_size=chunk_size, overlap=overlap)
        store.add_texts(chunks)
        return store

    @classmethod
    def from_directory(
        cls,
        directory: str,
        extensions: list[str] | None = None,
        chunk_size: int = 500,
        **kwargs: Any,
    ) -> "VectorStore":
        """Recursively load all matching files from a directory."""
        exts = set(extensions or [".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv"])
        store = cls(**kwargs)
        for root, _, files in os.walk(directory):
            for fname in files:
                if Path(fname).suffix.lower() in exts:
                    fpath = os.path.join(root, fname)
                    try:
                        chunks = _load_and_chunk(fpath, chunk_size=chunk_size)
                        store.add_texts(chunks)
                    except Exception:
                        pass
        return store

    # ── Query ─────────────────────────────────────────────────────────────────

    def query(self, text: str, top_k: int = 3) -> list[str]:
        """Return the *top_k* most relevant text chunks for *text*."""
        if not self._texts:
            return []
        query_vec = self._embed_fn([text])[0]
        scores = [_cosine_sim(query_vec, v) for v in self._vectors]
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._texts[i] for i in ranked[:top_k]]

    def __len__(self) -> int:
        return len(self._texts)

    def __repr__(self) -> str:
        return f"VectorStore(chunks={len(self._texts)})"


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    try:
        import numpy as np
        av, bv = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
        denom = (np.linalg.norm(av) * np.linalg.norm(bv))
        return float(np.dot(av, bv) / denom) if denom > 0 else 0.0
    except ImportError:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)


def _load_and_chunk(path: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Read a file and split into overlapping character chunks."""
    suffix = Path(path).suffix.lower()
    text = ""

    if suffix == ".pdf":
        try:
            import pypdf  # type: ignore[import]
            reader = pypdf.PdfReader(path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            return []  # pypdf not installed — skip silently
        except Exception:
            return []
    else:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            return []

    return _chunk_text(text, chunk_size=chunk_size, overlap=overlap)


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks, splitting on sentence boundaries when possible."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # Try to break at a sentence boundary
        if end < len(text):
            for sep in (". ", ".\n", "! ", "? ", "\n\n", "\n"):
                idx = text.rfind(sep, start, end)
                if idx > start + chunk_size // 2:
                    end = idx + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [c for c in chunks if c]


# ── KnowledgeSource ───────────────────────────────────────────────────────────

@dataclass
class KnowledgeSource:
    """A retrievable knowledge source — file, text snippet, URL, or VectorStore.

    Parameters
    ----------
    source:     str → file path, directory path, URL, or raw text snippet.
                VectorStore → use directly.
    chunk_size: Characters per chunk when loading files.
    overlap:    Overlapping characters between consecutive chunks.
    top_k:      How many chunks to retrieve per query.
    """

    source: str | VectorStore
    chunk_size: int = 500
    overlap: int = 50
    top_k: int = 3
    _store: VectorStore | None = field(default=None, init=False, repr=False)

    def _get_store(self) -> VectorStore:
        if isinstance(self.source, VectorStore):
            return self.source
        if self._store is None:
            src = self.source
            if isinstance(src, str):
                if os.path.isdir(src):
                    self._store = VectorStore.from_directory(src, chunk_size=self.chunk_size)
                elif os.path.isfile(src):
                    self._store = VectorStore.from_file(src, chunk_size=self.chunk_size, overlap=self.overlap)
                else:
                    # treat as raw text snippet
                    self._store = VectorStore.from_texts(
                        _chunk_text(src, chunk_size=self.chunk_size, overlap=self.overlap)
                    )
            else:
                self._store = VectorStore()
        return self._store

    def retrieve(self, query: str) -> list[str]:
        return self._get_store().query(query, top_k=self.top_k)

    def __len__(self) -> int:
        return len(self._get_store())


# ── AgentKnowledge (aggregates multiple sources) ──────────────────────────────

class AgentKnowledge:
    """Manages a collection of knowledge sources for one agent.

    Parameters
    ----------
    sources:  List of ``KnowledgeSource`` objects, VectorStores, or raw strings
              (file paths / text snippets).
    top_k:    Total number of chunks to retrieve across all sources.
    """

    def __init__(
        self,
        sources: list[Any],           # str | VectorStore | KnowledgeSource | HybridRetriever | SelfCorrectingRAG
        top_k: int = 5,
    ) -> None:
        self._sources: list[KnowledgeSource] = []
        self._hybrid_retrievers: list[Any] = []
        self._rag_pipelines: list[Any] = []
        for s in sources:
            if isinstance(s, KnowledgeSource):
                self._sources.append(s)
            elif isinstance(s, VectorStore):
                self._sources.append(KnowledgeSource(source=s, top_k=top_k))
            elif isinstance(s, str):
                self._sources.append(KnowledgeSource(source=s, top_k=top_k))
            elif hasattr(s, "_bm25_index") and hasattr(s, "_dense_w"):
                # HybridRetriever — duck-typed check
                self._hybrid_retrievers.append(s)
            elif hasattr(s, "_retriever") and hasattr(s, "_threshold"):
                # SelfCorrectingRAG — duck-typed check
                self._rag_pipelines.append(s)
            elif hasattr(s, "query") and callable(s.query):
                # Any object with a query(text, top_k) method (future-proofing)
                self._sources.append(KnowledgeSource(source=s, top_k=top_k))
        self._top_k = top_k

    def retrieve(self, query: str) -> list[str]:
        """Retrieve and deduplicate chunks across VectorStore, HybridRetriever, and SelfCorrectingRAG sources."""
        seen: set[str] = set()
        results: list[str] = []

        # Standard KnowledgeSources (VectorStore, file, text)
        for src in self._sources:
            for chunk in src.retrieve(query):
                if chunk not in seen:
                    seen.add(chunk)
                    results.append(chunk)

        # HybridRetriever sources (BM25 + dense RRF)
        for hybrid in self._hybrid_retrievers:
            try:
                for chunk in hybrid.query(query, top_k=self._top_k):
                    if chunk not in seen:
                        seen.add(chunk)
                        results.append(chunk)
            except Exception:
                pass

        # SelfCorrectingRAG — run synchronously using stored retriever only
        for rag in self._rag_pipelines:
            try:
                retriever = getattr(rag, "_retriever", None)
                if retriever is not None:
                    for chunk in retriever.query(query, top_k=self._top_k):
                        if chunk not in seen:
                            seen.add(chunk)
                            results.append(chunk)
            except Exception:
                pass

        # Dynamic context trimming based on active_tracker limits
        from meshflow.optimization.tracker import active_tracker
        tracker = active_tracker.get()
        if tracker is not None and tracker.max_tokens > 0:
            limit = int(tracker.max_tokens * 0.15)
            results = tracker.trim_rag_context(results, limit)

        return results[:self._top_k]

    def context_string(self, query: str, max_chars: int = 2000) -> str:
        """Return a formatted knowledge context string ready for prompt injection."""
        chunks = self.retrieve(query)
        if not chunks:
            return ""
        combined = "\n\n---\n\n".join(chunks)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "…"
        return combined

    def context_blocks_cached(
        self,
        query: str,
        max_chars: int = 2000,
    ) -> list[dict[str, Any]]:
        """Return knowledge context as Anthropic cache_control message blocks.

        Each retrieved chunk becomes a ``{"type": "text", "text": "...",
        "cache_control": {"type": "ephemeral"}}`` block.  When passed as the
        ``content`` of a user message the Anthropic API will cache these blocks
        across calls, saving tokens on frequently-used knowledge sources.

        Usage::

            blocks = knowledge.context_blocks_cached("HIPAA PHI handling")
            messages = [
                {"role": "user", "content": [
                    *blocks,
                    {"type": "text", "text": f"Task: {task}"},
                ]}
            ]
        """
        chunks = self.retrieve(query)
        if not chunks:
            return []
        blocks: list[dict[str, Any]] = []
        total = 0
        for chunk in chunks:
            if total + len(chunk) > max_chars:
                remaining = max_chars - total
                if remaining > 80:
                    chunk = chunk[:remaining] + "…"
                else:
                    break
            blocks.append({
                "type": "text",
                "text": chunk,
                "cache_control": {"type": "ephemeral"},
            })
            total += len(chunk)
        return blocks

    def __len__(self) -> int:
        return len(self._sources)

    def __bool__(self) -> bool:
        return len(self._sources) > 0
