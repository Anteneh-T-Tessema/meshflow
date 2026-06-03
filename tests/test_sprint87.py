"""Sprint 87 — Workflow.stream(), routing events, stream_collect(), stream_multimodal."""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ═══════════════════════════════════════════════════════════════════════════════
# StreamChunk — new routing kind and properties
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamChunkRouting:
    def _routing(self, **meta):
        from meshflow import StreamChunk
        return StreamChunk(kind="routing", node_name="agent", metadata=meta)

    def test_is_routing_true(self):
        chunk = self._routing(tier="fast", model="llama3.2")
        assert chunk.is_routing is True

    def test_is_routing_false_for_token(self):
        from meshflow import StreamChunk
        chunk = StreamChunk(kind="token", content="hello")
        assert chunk.is_routing is False

    def test_is_cascade_escalation_true(self):
        chunk = self._routing(cascade_escalation=True, tier="smart", model="mistral")
        assert chunk.is_cascade_escalation is True

    def test_is_cascade_escalation_false_for_initial(self):
        chunk = self._routing(cascade_escalation=False, tier="fast", model="llama3.2")
        assert chunk.is_cascade_escalation is False

    def test_is_cascade_escalation_false_for_non_routing(self):
        from meshflow import StreamChunk
        chunk = StreamChunk(kind="token", content="x")
        assert chunk.is_cascade_escalation is False

    def test_repr_routing(self):
        chunk = self._routing(tier="fast", model="llama3.2")
        r = repr(chunk)
        assert "routing" in r
        assert "fast" in r

    def test_repr_token(self):
        from meshflow import StreamChunk
        chunk = StreamChunk(kind="token", content="hello")
        assert "hello" in repr(chunk)

    def test_metadata_preserved(self):
        chunk = self._routing(tier="large", model="gpt-4o", is_local=False)
        assert chunk.metadata["tier"] == "large"
        assert chunk.metadata["model"] == "gpt-4o"
        assert chunk.metadata["is_local"] is False

    def test_exported_from_meshflow(self):
        from meshflow import StreamChunk
        assert StreamChunk is not None


# ═══════════════════════════════════════════════════════════════════════════════
# stream_collect()
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamCollect:
    def _make_stream(self, chunks):
        """Yield StreamChunks from a list — simulates a sync generator."""
        from meshflow import StreamChunk
        def _gen():
            for c in chunks:
                yield c
        return _gen()

    def test_collects_token_content(self):
        from meshflow import StreamChunk, stream_collect
        chunks = [
            StreamChunk(kind="node_start", node_name="a"),
            StreamChunk(kind="token", content="Hello"),
            StreamChunk(kind="token", content=" world"),
            StreamChunk(kind="done"),
        ]
        result = stream_collect(self._make_stream(chunks))
        assert result == "Hello world"

    def test_ignores_non_token_chunks(self):
        from meshflow import StreamChunk, stream_collect
        chunks = [
            StreamChunk(kind="routing", metadata={"tier": "fast"}),
            StreamChunk(kind="token", content="abc"),
            StreamChunk(kind="node_end", content="abc"),
            StreamChunk(kind="done"),
        ]
        assert stream_collect(self._make_stream(chunks)) == "abc"

    def test_empty_stream_returns_empty(self):
        from meshflow import stream_collect
        assert stream_collect(iter([])) == ""

    def test_exported_from_meshflow(self):
        from meshflow import stream_collect
        assert callable(stream_collect)


# ═══════════════════════════════════════════════════════════════════════════════
# routing_events() async filter
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoutingEvents:
    def test_yields_only_routing_chunks(self):
        import asyncio
        from meshflow import StreamChunk, routing_events

        async def _source():
            yield StreamChunk(kind="token", content="x")
            yield StreamChunk(kind="routing", metadata={"tier": "fast"})
            yield StreamChunk(kind="node_end")
            yield StreamChunk(kind="routing", metadata={"tier": "smart", "cascade_escalation": True})

        async def _collect():
            return [c async for c in routing_events(_source())]

        result = asyncio.run(_collect())
        assert len(result) == 2
        assert all(c.is_routing for c in result)
        assert result[1].is_cascade_escalation is True

    def test_exported_from_meshflow(self):
        from meshflow import routing_events
        assert callable(routing_events)


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.stream()
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowStream:
    def _wf(self, n_agents=1):
        from meshflow import Workflow, Agent
        wf = Workflow()
        for i in range(n_agents):
            wf.add(Agent(f"agent-{i}"))
        return wf

    def test_stream_yields_chunks(self):
        wf = self._wf()
        chunks = list(wf.stream("hello"))
        assert len(chunks) > 0

    def test_stream_has_done_chunk(self):
        from meshflow import StreamChunk
        wf = self._wf()
        chunks = list(wf.stream("task"))
        kinds = {c.kind for c in chunks}
        assert "done" in kinds

    def test_stream_has_node_start_and_end(self):
        wf = self._wf()
        chunks = list(wf.stream("task"))
        kinds = {c.kind for c in chunks}
        assert "node_start" in kinds
        assert "node_end" in kinds

    def test_stream_yields_tokens(self):
        from meshflow import StreamChunk
        wf = self._wf()
        token_chunks = [c for c in wf.stream("task") if c.is_token]
        # In mock mode there may or may not be tokens, but no crash
        assert isinstance(token_chunks, list)

    def test_stream_multi_agent_visits_all(self):
        wf = self._wf(n_agents=3)
        chunks = list(wf.stream("task"))
        node_starts = [c for c in chunks if c.kind == "node_start"]
        assert len(node_starts) == 3

    def test_stream_with_model_router_emits_routing_chunk(self):
        from meshflow import Workflow, Agent, AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        store = RouterOutcomeStore(path=":memory:")
        router = AdaptiveModelTierRouter(
            tiers=[ModelTier("fast", "llama3.2")],
            exploration_rate=0.0,
            store=store,
        )
        wf = Workflow()
        wf.add(Agent("routed", model_router=router))
        chunks = list(wf.stream("short task"))
        routing_chunks = [c for c in chunks if c.is_routing]
        assert len(routing_chunks) >= 1
        rc = routing_chunks[0]
        assert rc.metadata.get("model") == "llama3.2"
        assert rc.metadata.get("tier") == "fast"

    def test_stream_collect_from_workflow(self):
        from meshflow import stream_collect
        wf = self._wf()
        text = stream_collect(wf.stream("write something"))
        assert isinstance(text, str)

    def test_stream_empty_workflow_completes(self):
        from meshflow import Workflow
        wf = Workflow()
        chunks = list(wf.stream("task"))
        # No agents → just a done chunk or nothing
        assert isinstance(chunks, list)

    def test_stream_chunk_node_names_match_agents(self):
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("alice"))
        wf.add(Agent("bob"))
        chunks = list(wf.stream("task"))
        starts = {c.node_name for c in chunks if c.kind == "node_start"}
        assert "alice" in starts
        assert "bob" in starts


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.stream_multimodal()
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowStreamMultimodal:
    def test_stream_multimodal_completes(self):
        from meshflow import Workflow, Agent, ImageInput
        wf = Workflow()
        wf.add(Agent("analyst"))
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        chunks = list(wf.stream_multimodal("Describe.", [img]))
        assert isinstance(chunks, list)

    def test_stream_multimodal_has_done(self):
        from meshflow import Workflow, Agent, ImageInput
        wf = Workflow()
        wf.add(Agent("analyst"))
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        chunks = list(wf.stream_multimodal("Describe.", [img]))
        kinds = {c.kind for c in chunks}
        assert "done" in kinds

    def test_stream_multimodal_empty_inputs(self):
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("analyst"))
        chunks = list(wf.stream_multimodal("Text only.", []))
        assert isinstance(chunks, list)

    def test_stream_multimodal_collect(self):
        from meshflow import Workflow, Agent, ImageInput, stream_collect
        wf = Workflow()
        wf.add(Agent("analyst"))
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        text = stream_collect(wf.stream_multimodal("Describe.", [img]))
        assert isinstance(text, str)


# ═══════════════════════════════════════════════════════════════════════════════
# StreamChunk.is_token, is_done, is_routing on all kinds
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamChunkProperties:
    def test_all_kinds_accepted(self):
        from meshflow import StreamChunk
        for kind in ("token", "node_start", "node_end", "task_start", "task_end", "done", "error", "routing"):
            chunk = StreamChunk(kind=kind)  # type: ignore[arg-type]
            assert isinstance(chunk, StreamChunk)

    def test_is_token_only_for_token(self):
        from meshflow import StreamChunk
        assert StreamChunk(kind="token", content="x").is_token is True
        assert StreamChunk(kind="routing").is_token is False
        assert StreamChunk(kind="done").is_token is False

    def test_is_done_only_for_done(self):
        from meshflow import StreamChunk
        assert StreamChunk(kind="done").is_done is True
        assert StreamChunk(kind="token", content="x").is_done is False
