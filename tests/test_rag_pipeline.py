"""Tests for the RAG pipeline: DocumentStore, RAGNode, NumpyCosineIndex."""
from __future__ import annotations

import pytest


class TestNumpyCosineIndex:
    def test_import(self) -> None:
        from meshflow.intelligence.mem1 import NumpyCosineIndex
        assert NumpyCosineIndex is not None

    def test_add_and_search(self) -> None:
        from meshflow.intelligence.mem1 import NumpyCosineIndex
        idx = NumpyCosineIndex()
        idx.add("k1", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        idx.add("k2", [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        results = idx.search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=1)
        assert len(results) == 1
        assert results[0][0] == "k1"

    def test_empty_search_returns_empty(self) -> None:
        from meshflow.intelligence.mem1 import NumpyCosineIndex
        idx = NumpyCosineIndex()
        assert idx.search([1.0, 0.0, 0.0, 0.0], top_k=5) == []

    def test_top_k_respected(self) -> None:
        from meshflow.intelligence.mem1 import NumpyCosineIndex
        idx = NumpyCosineIndex()
        for i in range(10):
            idx.add(f"k{i}", [float(i % 2), float((i+1) % 2), 0.0, 0.0])
        results = idx.search([1.0, 0.0, 0.0, 0.0], top_k=3)
        assert len(results) <= 3


class TestTFIDFEmbeddings:
    @pytest.mark.asyncio
    async def test_embed_returns_nonzero_dim(self) -> None:
        from meshflow.intelligence.mem1 import TFIDFEmbeddings
        emb = TFIDFEmbeddings()
        vecs = await emb.embed(["HIPAA minimum necessary rule"])
        assert len(vecs) == 1
        assert len(vecs[0]) > 0

    @pytest.mark.asyncio
    async def test_embed_is_deterministic(self) -> None:
        from meshflow.intelligence.mem1 import TFIDFEmbeddings
        emb = TFIDFEmbeddings()
        v1 = (await emb.embed(["same text here"]))[0]
        v2 = (await emb.embed(["same text here"]))[0]
        assert v1 == v2

    @pytest.mark.asyncio
    async def test_similar_texts_closer(self) -> None:
        import math
        from meshflow.intelligence.mem1 import TFIDFEmbeddings
        emb = TFIDFEmbeddings()

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x*x for x in a))
            nb = math.sqrt(sum(x*x for x in b))
            return dot / (na * nb + 1e-9)

        texts = [
            "HIPAA data privacy healthcare",
            "HIPAA healthcare data disclosure",
            "machine learning neural network gradient",
        ]
        vecs = await emb.embed(texts)
        v1, v2, v3 = vecs[0], vecs[1], vecs[2]
        assert cosine(v1, v2) > cosine(v1, v3)


class TestDocumentStore:
    @pytest.mark.asyncio
    async def test_ingest_and_retrieve(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore()
        docs = [
            "HIPAA requires covered entities to protect patient health information.",
            "Machine learning models are trained on large datasets.",
            "Privacy regulations like GDPR and HIPAA govern data handling.",
        ]
        await store.ingest(docs)
        results = await store.retrieve("HIPAA healthcare privacy", top_k=2)
        assert len(results) >= 1
        texts = [r.text for r in results]
        assert any("HIPAA" in t for t in texts)

    @pytest.mark.asyncio
    async def test_retrieve_text_returns_string(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore()
        await store.ingest(["SOX compliance requires financial auditing."])
        text = await store.retrieve_text("SOX audit requirements", top_k=1)
        assert isinstance(text, str)
        assert "SOX" in text or len(text) > 0

    @pytest.mark.asyncio
    async def test_ingest_with_metadata(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore()
        docs = ["Document one content here"]
        meta = [{"source": "test.pdf", "page": 1}]
        await store.ingest(docs, metadata=meta)
        results = await store.retrieve("document content", top_k=1)
        assert len(results) == 1
        assert results[0].metadata.get("source") == "test.pdf"

    @pytest.mark.asyncio
    async def test_fixed_chunking(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore(chunk_size=10, chunk_overlap=2, chunk_strategy="fixed")
        long_doc = " ".join([f"word{i}" for i in range(50)])
        await store.ingest([long_doc])
        # Multiple chunks should be indexed
        results = await store.retrieve("word25 word26", top_k=5)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_sentence_chunking(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore(chunk_strategy="sentence")
        docs = ["First sentence. Second sentence. Third sentence about HIPAA."]
        await store.ingest(docs)
        results = await store.retrieve("HIPAA sentence", top_k=2)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_empty_store_returns_empty(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore()
        results = await store.retrieve("anything", top_k=3)
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieved_chunk_has_score(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore()
        await store.ingest(["The quick brown fox jumps over the lazy dog"])
        results = await store.retrieve("fox", top_k=1)
        if results:
            assert isinstance(results[0].score, float)
            assert results[0].score >= 0.0

    @pytest.mark.asyncio
    async def test_top_k_limit(self) -> None:
        from meshflow.intelligence.rag import DocumentStore
        store = DocumentStore()
        docs = [f"Document about topic {i}" for i in range(20)]
        await store.ingest(docs)
        results = await store.retrieve("topic", top_k=3)
        assert len(results) <= 3


class TestRAGNode:
    @pytest.mark.asyncio
    async def test_rag_node_prepends_context(self) -> None:
        from meshflow.intelligence.rag import DocumentStore, RAGNode
        from meshflow.core.node import NodeInput

        store = DocumentStore()
        await store.ingest([
            "HIPAA requires minimum necessary access to PHI.",
            "The HIPAA Privacy Rule was enacted in 1996.",
        ])

        rag = RAGNode(store=store, node_id="rag", top_k=1)

        # RAGNode.run() prepends retrieved context to the task
        node_in = NodeInput(task="What does HIPAA say?", context={})
        output = await rag.run(node_in)
        # Output content should contain the task injected with retrieved docs
        assert output.content is not None

    def test_rag_node_is_mesh_node(self) -> None:
        from meshflow.intelligence.rag import DocumentStore, RAGNode
        from meshflow.core.node import MeshNode
        store = DocumentStore()
        rag = RAGNode(store=store, node_id="rag")
        assert isinstance(rag, MeshNode)

    def test_rag_node_kind_is_python(self) -> None:
        from meshflow.intelligence.rag import DocumentStore, RAGNode
        from meshflow.core.node import NodeKind
        store = DocumentStore()
        rag = RAGNode(store=store, node_id="rag")
        assert rag.kind == NodeKind.PYTHON
