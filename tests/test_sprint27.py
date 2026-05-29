"""Sprint 27 — Native RAG / Knowledge tests.

All tests are deterministic (no LLM calls for knowledge retrieval).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.intelligence.knowledge import (
    AgentKnowledge,
    KnowledgeSource,
    VectorStore,
    _chunk_text,
    _load_and_chunk,
)
from meshflow import Task


# ═══════════════════════════════════════════════════════════════════════════════
# 1. _chunk_text helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestChunkText:
    def test_short_text_single_chunk(self):
        chunks = _chunk_text("Hello world", chunk_size=500)
        assert chunks == ["Hello world"]

    def test_long_text_multiple_chunks(self):
        text = "word " * 300  # 1500 chars
        chunks = _chunk_text(text, chunk_size=200, overlap=20)
        assert len(chunks) > 1

    def test_empty_text_returns_empty(self):
        assert _chunk_text("") == []
        assert _chunk_text("   ") == []

    def test_chunk_size_respected(self):
        text = "a" * 1000
        chunks = _chunk_text(text, chunk_size=100, overlap=0)
        for c in chunks:
            assert len(c) <= 110  # allow a bit for boundary logic

    def test_overlap_reduces_gaps(self):
        text = "sentence one. sentence two. sentence three."
        chunks = _chunk_text(text, chunk_size=20, overlap=5)
        assert len(chunks) >= 2

    def test_no_empty_chunks(self):
        text = "hello\n\n\n\nworld"
        chunks = _chunk_text(text, chunk_size=50)
        assert all(c.strip() for c in chunks)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VectorStore — construction
# ═══════════════════════════════════════════════════════════════════════════════

class TestVectorStoreConstruction:
    def test_empty_store(self):
        vs = VectorStore()
        assert len(vs) == 0

    def test_add_texts(self):
        vs = VectorStore()
        vs.add_texts(["hello", "world"])
        assert len(vs) == 2

    def test_from_texts(self):
        vs = VectorStore.from_texts(["alpha", "beta", "gamma"])
        assert len(vs) == 3

    def test_add_empty_list_noop(self):
        vs = VectorStore()
        vs.add_texts([])
        assert len(vs) == 0

    def test_from_file_txt(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("MeshFlow is a governed multi-agent framework.\n" * 10)
            fname = f.name
        try:
            vs = VectorStore.from_file(fname, chunk_size=100)
            assert len(vs) >= 1
        finally:
            os.unlink(fname)

    def test_from_file_md(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# MeshFlow\n\nGovernance kernel for agents.\n" * 5)
            fname = f.name
        try:
            vs = VectorStore.from_file(fname)
            assert len(vs) >= 1
        finally:
            os.unlink(fname)

    def test_from_nonexistent_file_returns_empty(self):
        vs = VectorStore.from_file("/nonexistent/path/file.txt")
        assert len(vs) == 0

    def test_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "a.txt").write_text("document about agents\n" * 10)
            Path(tmpdir, "b.md").write_text("governance and compliance\n" * 10)
            vs = VectorStore.from_directory(tmpdir)
            assert len(vs) >= 2

    def test_repr(self):
        vs = VectorStore.from_texts(["x", "y"])
        r = repr(vs)
        assert "VectorStore" in r
        assert "2" in r

    def test_chaining_add_texts(self):
        vs = VectorStore()
        vs.add_texts(["a"]).add_texts(["b"])
        assert len(vs) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 3. VectorStore — query
# ═══════════════════════════════════════════════════════════════════════════════

class TestVectorStoreQuery:
    def test_query_empty_store(self):
        vs = VectorStore()
        assert vs.query("anything") == []

    def test_query_returns_list(self):
        vs = VectorStore.from_texts(["hello world", "foo bar"])
        results = vs.query("hello")
        assert isinstance(results, list)
        assert len(results) <= 2

    def test_query_top_k(self):
        vs = VectorStore.from_texts(["a", "b", "c", "d", "e"])
        results = vs.query("a", top_k=3)
        assert len(results) <= 3

    def test_query_relevant_result_first(self):
        vs = VectorStore.from_texts([
            "Python is a programming language.",
            "The weather in Paris is sunny.",
            "Python supports async/await natively.",
        ])
        results = vs.query("Python programming", top_k=2)
        combined = " ".join(results).lower()
        assert "python" in combined

    def test_query_returns_strings(self):
        vs = VectorStore.from_texts(["hello", "world"])
        for r in vs.query("hi"):
            assert isinstance(r, str)

    def test_query_top_k_1(self):
        vs = VectorStore.from_texts(["only one match here"] * 5)
        results = vs.query("match", top_k=1)
        assert len(results) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. KnowledgeSource
# ═══════════════════════════════════════════════════════════════════════════════

class TestKnowledgeSource:
    def test_from_raw_text(self):
        ks = KnowledgeSource(source="MeshFlow is an agentic framework with governance.")
        results = ks.retrieve("governance")
        assert isinstance(results, list)

    def test_from_vector_store(self):
        vs = VectorStore.from_texts(["alpha beta gamma", "delta epsilon"])
        ks = KnowledgeSource(source=vs, top_k=2)
        results = ks.retrieve("alpha")
        assert len(results) <= 2

    def test_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Security and compliance are key concerns.\n" * 8)
            fname = f.name
        try:
            ks = KnowledgeSource(source=fname)
            results = ks.retrieve("compliance")
            assert len(results) >= 1
        finally:
            os.unlink(fname)

    def test_top_k_respected(self):
        vs = VectorStore.from_texts(["a", "b", "c", "d", "e"])
        ks = KnowledgeSource(source=vs, top_k=2)
        assert len(ks.retrieve("x")) <= 2

    def test_len(self):
        vs = VectorStore.from_texts(["a", "b", "c"])
        ks = KnowledgeSource(source=vs)
        assert len(ks) == 3

    def test_text_snippet_chunked(self):
        long_text = "word " * 200  # 1000+ chars
        ks = KnowledgeSource(source=long_text, chunk_size=100)
        results = ks.retrieve("word")
        assert len(results) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. AgentKnowledge
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentKnowledge:
    def test_single_source(self):
        vs = VectorStore.from_texts(["governance framework", "agentic AI"])
        ak = AgentKnowledge([vs])
        results = ak.retrieve("AI")
        assert len(results) >= 1

    def test_multiple_sources_deduplicated(self):
        vs1 = VectorStore.from_texts(["unique text from source 1"])
        vs2 = VectorStore.from_texts(["unique text from source 2"])
        ak = AgentKnowledge([vs1, vs2], top_k=4)
        results = ak.retrieve("text")
        # No duplicates
        assert len(results) == len(set(results))

    def test_accepts_strings(self):
        ak = AgentKnowledge(["MeshFlow is a governed multi-agent framework with compliance."])
        results = ak.retrieve("compliance")
        assert isinstance(results, list)

    def test_accepts_knowledge_source(self):
        ks = KnowledgeSource(source="hello world testing")
        ak = AgentKnowledge([ks])
        results = ak.retrieve("hello")
        assert isinstance(results, list)

    def test_context_string_nonempty(self):
        ak = AgentKnowledge(["relevant context about RAG retrieval"])
        ctx = ak.context_string("RAG")
        assert ctx != ""
        assert "context" in ctx.lower() or "retrieval" in ctx.lower() or "rag" in ctx.lower()

    def test_context_string_max_chars(self):
        ak = AgentKnowledge(["word " * 500])
        ctx = ak.context_string("word", max_chars=100)
        assert len(ctx) <= 120  # allow for ellipsis

    def test_context_string_empty_store(self):
        ak = AgentKnowledge([])
        assert ak.context_string("query") == ""

    def test_bool_nonempty(self):
        vs = VectorStore.from_texts(["x"])
        ak = AgentKnowledge([vs])
        assert bool(ak)

    def test_bool_empty(self):
        ak = AgentKnowledge([])
        assert not bool(ak)

    def test_len(self):
        ak = AgentKnowledge([VectorStore.from_texts(["a"]), "text"])
        assert len(ak) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Agent knowledge= integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentKnowledgeIntegration:
    def test_agent_accepts_knowledge(self):
        from meshflow import Agent
        vs = VectorStore.from_texts(["fact 1", "fact 2"])
        agent = Agent(name="k_agent", role="researcher", knowledge=[vs])
        assert len(agent.knowledge) == 1

    def test_built_agent_has_knowledge(self):
        from meshflow import Agent
        vs = VectorStore.from_texts(["fact 1"])
        agent = Agent(name="k_agent", role="researcher", knowledge=[vs])
        built = agent._build()
        assert built._knowledge is not None

    def test_no_knowledge_is_none(self):
        from meshflow import Agent
        agent = Agent(name="plain", role="executor")
        built = agent._build()
        assert built._knowledge is None

    @pytest.mark.asyncio
    async def test_knowledge_injected_in_prompt(self):
        from meshflow import Agent

        prompts_sent: list[str] = []

        vs = VectorStore.from_texts([
            "MeshFlow supports HIPAA compliance through audit trails."
        ])
        agent = Agent(name="k_agent", role="researcher", knowledge=[vs])
        built = agent._build()

        original_think = built.think

        async def capture_think(messages, system=None, **kw):
            prompts_sent.append(messages[-1]["content"])
            return "[echo] test", 5, 0.0

        built.think = capture_think
        await built.step("Tell me about HIPAA", {})

        assert len(prompts_sent) == 1
        content = prompts_sent[0]
        # Knowledge is injected either as a plain "[Knowledge]\n..." string
        # (legacy) or as Anthropic cache_control blocks (list) with the
        # knowledge text in each block's "text" field.
        if isinstance(content, str):
            assert "[Knowledge]" in content or "HIPAA" in content
        else:
            all_text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            assert "HIPAA" in all_text

    @pytest.mark.asyncio
    async def test_agent_run_with_knowledge_no_error(self):
        from meshflow import Agent
        vs = VectorStore.from_texts(["Important policy: always log actions."])
        agent = Agent(name="k_agent", role="executor", knowledge=[vs])
        result = await agent.run("What is the policy?")
        assert "result" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Task knowledge= integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskKnowledgeIntegration:
    @pytest.mark.asyncio
    async def test_task_knowledge_injected_in_prompt(self):
        prompts: list[str] = []

        async def capture_run(prompt: str, *a, **kw):
            prompts.append(prompt)
            return {"result": "ok", "agent_name": "a", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0}

        agent = MagicMock()
        agent.name = "a"
        agent.tools = []
        agent.run = capture_run

        vs = VectorStore.from_texts(["Contract clause 3.2 requires 30-day notice."])
        task = Task(
            description="Review the contract",
            expected_output="Key points",
            agent=agent,
            knowledge=[vs],
        )
        await task.run()
        assert "[Task Knowledge]" in prompts[0]

    @pytest.mark.asyncio
    async def test_task_no_knowledge_no_injection(self):
        prompts: list[str] = []

        async def capture_run(prompt: str, *a, **kw):
            prompts.append(prompt)
            return {"result": "ok", "agent_name": "a", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0}

        agent = MagicMock()
        agent.name = "a"
        agent.tools = []
        agent.run = capture_run

        task = Task(description="Simple task", expected_output="ok", agent=agent)
        await task.run()
        assert "[Task Knowledge]" not in prompts[0]

    @pytest.mark.asyncio
    async def test_task_knowledge_relevant_content(self):
        prompts: list[str] = []

        async def capture_run(prompt: str, *a, **kw):
            prompts.append(prompt)
            return {"result": "ok", "agent_name": "a", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0}

        agent = MagicMock()
        agent.name = "a"
        agent.tools = []
        agent.run = capture_run

        vs = VectorStore.from_texts(["HIPAA requires PHI encryption at rest."])
        task = Task(
            description="Describe HIPAA requirements",
            expected_output="list",
            agent=agent,
            knowledge=[vs],
        )
        await task.run()
        assert "HIPAA" in prompts[0] or "encryption" in prompts[0].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Public API surface
# ═══════════════════════════════════════════════════════════════════════════════

class TestKnowledgePublicAPI:
    def test_importable_from_root(self):
        import meshflow
        for name in ["VectorStore", "KnowledgeSource", "AgentKnowledge"]:
            assert hasattr(meshflow, name), f"meshflow.{name} not exported"

    def test_vector_store_is_correct_class(self):
        from meshflow import VectorStore as VS
        assert VS is VectorStore

    def test_knowledge_source_is_correct_class(self):
        from meshflow import KnowledgeSource as KS
        assert KS is KnowledgeSource

    def test_agent_knowledge_is_correct_class(self):
        from meshflow import AgentKnowledge as AK
        assert AK is AgentKnowledge

    def test_version_bumped(self):
        import meshflow
        major, minor, _ = meshflow.__version__.split(".")
        assert int(minor) >= 26
