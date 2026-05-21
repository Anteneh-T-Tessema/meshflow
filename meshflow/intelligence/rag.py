"""L4.1 — RAG Integration: hybrid retrieval with RAGAS evaluation and corrective loop.

Every retrieved chunk is typed as a dasc-core Evidence object so the IFC taint
check automatically evaluates source trust before allowing downstream use.

Key design decisions:
- Hybrid retrieval: vector (semantic) + BM25 (keyword) + reciprocal rank fusion
- RAGAS-style quality scoring on every retrieval call
- Bounded corrective loop (max 2 retries) when faithfulness < threshold
- Web-sourced content is always trust_level="untrusted" — blocks Tier 3+ actions
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any

from meshflow.core.schemas import Evidence, RAGResult


@dataclass
class Chunk:
    """A single retrieved text chunk with provenance."""
    text: str
    source: str
    source_type: str      # "internal", "web", "database", "document"
    score: float = 0.0
    chunk_id: str = ""

    def to_evidence(self) -> Evidence:
        trust = "trusted" if self.source_type in ("internal", "database") else "untrusted"
        return Evidence(
            content=self.text,
            source=self.source,
            trust_level=trust,
            source_hash=hashlib.sha256(self.text.encode()).hexdigest()[:16],
        )


@dataclass
class ChunkStore:
    """In-memory chunk store — swap for a vector DB in production."""
    chunks: list[Chunk] = field(default_factory=list)

    def add(self, text: str, source: str, source_type: str = "internal") -> None:
        cid = hashlib.md5(text.encode()).hexdigest()[:8]
        self.chunks.append(Chunk(text=text, source=source, source_type=source_type, chunk_id=cid))

    def __len__(self) -> int:
        return len(self.chunks)


class VectorRetriever:
    """TF-IDF based semantic retrieval — use a real embedding model in production."""

    def retrieve(self, query: str, chunks: list[Chunk], top_k: int = 5) -> list[Chunk]:
        if not chunks:
            return []
        q_words = set(query.lower().split())
        scored = []
        for chunk in chunks:
            c_words = chunk.text.lower().split()
            c_set = set(c_words)
            # Cosine-like overlap
            overlap = len(q_words & c_set)
            idf_boost = math.log1p(len(c_words))
            score = overlap * idf_boost / max(math.sqrt(len(q_words) * len(c_set)), 1)
            scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, chunk in scored[:top_k]:
            chunk.score = score
            results.append(chunk)
        return results


class BM25Retriever:
    """BM25 keyword retrieval — k1=1.5, b=0.75 (standard params)."""

    K1 = 1.5
    B  = 0.75

    def retrieve(self, query: str, chunks: list[Chunk], top_k: int = 5) -> list[Chunk]:
        if not chunks:
            return []
        q_terms = query.lower().split()
        corpus = [c.text.lower().split() for c in chunks]
        avgdl = sum(len(d) for d in corpus) / max(len(corpus), 1)

        scored = []
        for i, (doc, chunk) in enumerate(zip(corpus, chunks)):
            score = 0.0
            doc_len = len(doc)
            for term in q_terms:
                tf = doc.count(term)
                if tf == 0:
                    continue
                idf = math.log((len(corpus) + 1) / (sum(1 for d in corpus if term in d) + 0.5))
                tf_norm = (tf * (self.K1 + 1)) / (
                    tf + self.K1 * (1 - self.B + self.B * doc_len / avgdl)
                )
                score += idf * tf_norm
            scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, chunk in scored[:top_k]:
            chunk.score = score
            results.append(chunk)
        return results


class ReciprocakRankFusion:
    """Merge vector and BM25 results via Reciprocal Rank Fusion (RRF, k=60)."""

    K = 60

    def fuse(
        self,
        vector_results: list[Chunk],
        bm25_results: list[Chunk],
    ) -> list[Chunk]:
        scores: dict[str, float] = {}
        chunks: dict[str, Chunk] = {}

        for rank, chunk in enumerate(vector_results):
            cid = chunk.chunk_id or id(chunk)
            key = str(cid)
            scores[key] = scores.get(key, 0) + 1 / (self.K + rank + 1)
            chunks[key] = chunk

        for rank, chunk in enumerate(bm25_results):
            cid = chunk.chunk_id or id(chunk)
            key = str(cid)
            scores[key] = scores.get(key, 0) + 1 / (self.K + rank + 1)
            chunks[key] = chunk

        fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for key, score in fused:
            chunk = chunks[key]
            chunk.score = score
            results.append(chunk)
        return results


class RAGASEvaluator:
    """RAGAS-style quality metrics without an external LLM call.

    Uses token overlap as a proxy for faithfulness and relevance.
    In production: replace with actual LLM-based RAGAS evaluation.
    """

    def faithfulness(self, answer: str, chunks: list[Chunk]) -> float:
        if not chunks or not answer:
            return 0.0
        answer_words = set(answer.lower().split())
        context_words = set()
        for chunk in chunks:
            context_words.update(chunk.text.lower().split())
        if not answer_words:
            return 0.0
        return len(answer_words & context_words) / len(answer_words)

    def answer_relevance(self, query: str, answer: str) -> float:
        if not query or not answer:
            return 0.0
        q_words = set(query.lower().split())
        a_words = set(answer.lower().split())
        union = q_words | a_words
        return len(q_words & a_words) / max(len(union), 1)

    def context_precision(self, query: str, chunks: list[Chunk]) -> float:
        if not chunks:
            return 0.0
        q_words = set(query.lower().split())
        relevant = sum(
            1 for c in chunks
            if len(q_words & set(c.text.lower().split())) > 0
        )
        return relevant / len(chunks)


class RAGPipeline:
    """Full hybrid RAG pipeline with evaluation and corrective loop.

    Every retrieved chunk is returned as an Evidence object, so the dasc-gate
    IFC taint check works automatically — web sources are untrusted by default.
    """

    FAITHFULNESS_THRESHOLD = 0.40
    MAX_CORRECTIVE_RETRIES = 2

    def __init__(self, store: ChunkStore | None = None) -> None:
        self._store = store or ChunkStore()
        self._vector = VectorRetriever()
        self._bm25 = BM25Retriever()
        self._fusion = ReciprocakRankFusion()
        self._evaluator = RAGASEvaluator()

    def add_document(self, text: str, source: str, source_type: str = "internal") -> None:
        self._store.add(text, source, source_type)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        answer: str = "",
    ) -> RAGResult:
        start = time.monotonic()
        chunks = self._store.chunks

        if not chunks:
            return RAGResult(
                query=query,
                chunks=[],
                retrieval_score=0.0,
                latency_ms=0.0,
            )

        # Hybrid retrieval
        vector_hits = self._vector.retrieve(query, chunks, top_k=top_k * 2)
        bm25_hits   = self._bm25.retrieve(query, chunks, top_k=top_k * 2)
        fused       = self._fusion.fuse(vector_hits, bm25_hits)[:top_k]

        # RAGAS evaluation
        faithfulness     = self._evaluator.faithfulness(answer, fused)
        answer_relevance = self._evaluator.answer_relevance(query, answer)
        context_precision = self._evaluator.context_precision(query, fused)

        # Corrective loop — if faithfulness too low, broaden search window.
        # search_k expands for retrieval but the final result is always capped at top_k.
        corrective = False
        search_k = top_k
        if faithfulness < self.FAITHFULNESS_THRESHOLD and len(chunks) > search_k:
            for _ in range(self.MAX_CORRECTIVE_RETRIES):
                search_k = min(search_k * 2, len(chunks))
                wider = self._fusion.fuse(
                    self._vector.retrieve(query, chunks, search_k),
                    self._bm25.retrieve(query, chunks, search_k),
                )
                new_faith = self._evaluator.faithfulness(answer, wider[:top_k])
                if new_faith >= self.FAITHFULNESS_THRESHOLD:
                    fused = wider[:top_k]
                    faithfulness = new_faith
                    corrective = True
                    break

        evidence = [chunk.to_evidence() for chunk in fused]
        latency = (time.monotonic() - start) * 1000

        return RAGResult(
            query=query,
            chunks=evidence,
            retrieval_score=faithfulness,
            answer_relevance=answer_relevance,
            context_precision=context_precision,
            corrective_applied=corrective,
            latency_ms=latency,
        )

    def context_string(self, result: RAGResult) -> str:
        return "\n\n".join(e.content for e in result.chunks)

    def store_size(self) -> int:
        return len(self._store)
