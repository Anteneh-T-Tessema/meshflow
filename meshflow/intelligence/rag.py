"""Semantic RAG (Retrieval-Augmented Generation) pipeline.

DocumentStore ingests, chunks, and indexes documents using the same
NumpyCosineIndex / EmbeddingProvider abstraction as MEM1Store, so the
entire system can share one embedding provider and zero-dependency
TF-IDF embeddings work out of the box.

Usage::

    from meshflow.intelligence.rag import DocumentStore, RAGNode

    store = DocumentStore()
    await store.ingest(["Patient presented with...", "Lab results show..."])

    # As a standalone retriever
    chunks = await store.retrieve("What were the lab results?", top_k=3)

    # As a MeshNode inside a WorkflowDefinition
    rag_node = RAGNode(store=store, node_id="rag", top_k=5)
    wf.add_node(rag_node)
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.intelligence.mem1 import EmbeddingProvider, NumpyCosineIndex, TFIDFEmbeddings


@dataclass
class RetrievedChunk:
    text: str
    doc_id: str
    chunk_index: int
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _chunk_fixed(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into fixed-size word-count chunks with overlap."""
    words = text.split()
    chunks: list[str] = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + chunk_size]))
        i += chunk_size - overlap
    return chunks or [text]


def _chunk_sentences(text: str, max_chars: int = 1000) -> list[str]:
    """Split on sentence boundaries, merging short sentences."""
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in raw:
        if len(current) + len(sentence) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current += (" " if current else "") + sentence
    if current:
        chunks.append(current.strip())
    return chunks or [text]


class DocumentStore:
    """Embed, index, and retrieve documents with semantic similarity.

    Chunking strategy: "fixed" (word-count windows) or "sentence" (sentence boundaries).
    EmbeddingProvider: TFIDFEmbeddings by default (zero external dependencies).
    Pass AnthropicEmbeddings or OpenAIEmbeddings for production-quality retrieval.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        chunk_strategy: str = "fixed",
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> None:
        self._embed: EmbeddingProvider = embedding_provider or TFIDFEmbeddings()
        self._chunk_strategy = chunk_strategy
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._index = NumpyCosineIndex()
        self._chunks: dict[str, RetrievedChunk] = {}

    def _split(self, text: str) -> list[str]:
        if self._chunk_strategy == "sentence":
            return _chunk_sentences(text)
        return _chunk_fixed(text, self._chunk_size, self._chunk_overlap)

    async def ingest(
        self,
        docs: list[str],
        metadata: list[dict[str, Any]] | None = None,
    ) -> int:
        """Chunk, embed, and index documents. Returns number of chunks indexed."""
        meta = metadata or [{}] * len(docs)
        all_chunks: list[tuple[str, str, int, dict[str, Any]]] = []

        for doc_idx, (doc, m) in enumerate(zip(docs, meta)):
            doc_id = m.get("doc_id", f"doc_{doc_idx}")
            for chunk_idx, chunk_text in enumerate(self._split(doc)):
                key = f"{doc_id}__chunk_{chunk_idx}"
                all_chunks.append((key, chunk_text, chunk_idx, {**m, "doc_id": doc_id}))

        if not all_chunks:
            return 0

        texts = [c[1] for c in all_chunks]
        embeddings = await self._embed.embed(texts)

        for (key, text, chunk_idx, m), emb in zip(all_chunks, embeddings):
            doc_id = m.get("doc_id", "doc_0")
            self._chunks[key] = RetrievedChunk(
                text=text,
                doc_id=doc_id,
                chunk_index=chunk_idx,
                score=0.0,
                metadata=m,
            )
            self._index.add(key, emb)

        # Freeze vocabulary so query embeddings use the same dimension as stored vectors.
        if hasattr(self._embed, "freeze"):
            self._embed.freeze()

        return len(all_chunks)

    async def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        """Return top-k chunks most semantically similar to the query."""
        if not self._chunks:
            return []
        embeddings = await self._embed.embed([query])
        if not embeddings:
            return []
        hits = self._index.search(embeddings[0], top_k)
        results: list[RetrievedChunk] = []
        for key, score in hits:
            chunk = self._chunks.get(key)
            if chunk:
                results.append(dataclasses.replace(chunk, score=score))
        return results

    async def retrieve_text(self, query: str, top_k: int = 5) -> str:
        """Retrieve and join chunk texts as a formatted context block."""
        chunks = await self.retrieve(query, top_k)
        if not chunks:
            return ""
        parts = [
            f"[Source: {c.doc_id}, chunk {c.chunk_index}, score={c.score:.2f}]\n{c.text}"
            for c in chunks
        ]
        return "\n\n---\n\n".join(parts)


class RAGNode(MeshNode):
    """A MeshNode that retrieves relevant context and prepends it to the task.

    Plugs into any WorkflowDefinition. The enriched task is passed as output
    content so downstream nodes receive it in the workflow context.

    Example::

        store = DocumentStore()
        await store.ingest(contract_texts)

        wf.add_node(RAGNode(store=store, node_id="retriever", top_k=5))
        wf.add_node(reviewer_node)
        wf.add_edge("retriever", "reviewer")
    """

    def __init__(
        self,
        store: DocumentStore,
        node_id: str = "",
        top_k: int = 5,
        context_prefix: str = "Retrieved context:\n",
    ) -> None:
        super().__init__(
            id=node_id or f"rag_{uuid.uuid4().hex[:6]}",
            kind=NodeKind.PYTHON,
            capabilities=["retrieve"],
        )
        self._store = store
        self._top_k = top_k
        self._prefix = context_prefix

    async def run(self, node_input: NodeInput) -> NodeOutput:
        context_text = await self._store.retrieve_text(node_input.task, self._top_k)
        if context_text:
            enriched = f"{self._prefix}\n{context_text}\n\n---\n\nQuery: {node_input.task}"
        else:
            enriched = node_input.task
        return NodeOutput(
            content=enriched,
            confidence=1.0,
            structured={"rag_context": context_text, "original_task": node_input.task},
        )


# ── Synchronous facade ────────────────────────────────────────────────────────


@dataclass
class Evidence:
    """A retrieved chunk annotated with a trust level based on its source."""

    content: str
    source: str
    score: float
    trust_level: str  # "trusted" | "untrusted" | "external"


@dataclass
class RAGResult:
    chunks: list[Evidence] = field(default_factory=list)
    context_precision: float = 0.0

    @property
    def context(self) -> str:
        return "\n\n".join(c.content for c in self.chunks)


class RAGPipeline:
    """Synchronous façade over DocumentStore for use in non-async contexts and tests.

    Documents are buffered and batch-ingested on the first retrieve() call so
    that the TF-IDF embedding vocabulary is built from the full corpus at once,
    guaranteeing consistent vector dimensions across all documents.

    Trust levels: "trusted" for internal sources, "untrusted" for web URLs,
    "external" for everything else.
    """

    def __init__(self) -> None:
        self._source_types: dict[str, str] = {}  # doc_id → source_type
        self._pending: list[tuple[str, str, str]] = []  # (text, doc_id, source)
        self._store: DocumentStore | None = None
        self._dirty = False

    def add_document(
        self,
        text: str,
        source: str,
        source_type: str = "internal",
    ) -> None:
        self._source_types[source] = source_type
        self._pending.append((text, source, source))
        self._dirty = True

    def _build_store(self) -> DocumentStore:
        store = DocumentStore()
        if self._pending:
            texts = [t for t, _, _ in self._pending]
            meta = [{"doc_id": doc_id, "source": src} for _, doc_id, src in self._pending]
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(store.ingest(texts, meta))
            finally:
                loop.close()
        self._dirty = False
        return store

    def retrieve(self, query: str, top_k: int = 5) -> RAGResult:
        if self._dirty or self._store is None:
            self._store = self._build_store()

        loop = asyncio.new_event_loop()
        try:
            chunks = loop.run_until_complete(self._store.retrieve(query, top_k))
        finally:
            loop.close()

        evidence: list[Evidence] = []
        for chunk in chunks:
            source = chunk.metadata.get("source", chunk.doc_id)
            st = self._source_types.get(chunk.doc_id, "internal")
            if st == "web" or str(source).startswith("http"):
                trust = "untrusted"
            elif st == "internal":
                trust = "trusted"
            else:
                trust = "external"
            evidence.append(
                Evidence(
                    content=chunk.text,
                    source=source,
                    score=chunk.score,
                    trust_level=trust,
                )
            )

        precision = sum(e.score for e in evidence) / len(evidence) if evidence else 0.0
        return RAGResult(chunks=evidence, context_precision=precision)
