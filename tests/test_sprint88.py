"""Sprint 88 — astream(), astream_multimodal(), SSE/NDJSON helpers, async_stream_collect."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.astream()
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowAstream:
    def _wf(self, n=1):
        from meshflow import Workflow, Agent
        wf = Workflow()
        for i in range(n):
            wf.add(Agent(f"agent-{i}"))
        return wf

    def _collect(self, coro):
        return asyncio.run(coro)

    async def _chunks(self, wf, task="task"):
        return [c async for c in wf.astream(task)]

    def test_astream_yields_chunks(self):
        wf = self._wf()
        chunks = asyncio.run(self._chunks(wf))
        assert len(chunks) > 0

    def test_astream_has_done_chunk(self):
        wf = self._wf()
        chunks = asyncio.run(self._chunks(wf))
        assert any(c.kind == "done" for c in chunks)

    def test_astream_has_node_start_and_end(self):
        wf = self._wf()
        chunks = asyncio.run(self._chunks(wf))
        kinds = {c.kind for c in chunks}
        assert "node_start" in kinds
        assert "node_end" in kinds

    def test_astream_multi_agent_all_visited(self):
        wf = self._wf(n=3)
        chunks = asyncio.run(self._chunks(wf))
        starts = [c for c in chunks if c.kind == "node_start"]
        assert len(starts) == 3

    def test_astream_with_router_emits_routing(self):
        from meshflow import Workflow, Agent, AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        router = AdaptiveModelTierRouter(
            tiers=[ModelTier("fast", "llama3.2")],
            exploration_rate=0.0,
            store=RouterOutcomeStore(path=":memory:"),
        )
        wf = Workflow()
        wf.add(Agent("r", model_router=router))

        async def _run():
            return [c async for c in wf.astream("short task")]

        chunks = asyncio.run(_run())
        routing = [c for c in chunks if c.is_routing]
        assert len(routing) >= 1
        assert routing[0].metadata["model"] == "llama3.2"

    def test_astream_node_names_match_agents(self):
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("alice"))
        wf.add(Agent("bob"))
        chunks = asyncio.run(self._chunks(wf))
        starts = {c.node_name for c in chunks if c.kind == "node_start"}
        assert "alice" in starts
        assert "bob" in starts

    def test_astream_exported_on_workflow(self):
        from meshflow import Workflow
        wf = Workflow()
        assert hasattr(wf, "astream")

    def test_astream_is_async_generator(self):
        import inspect
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("a"))
        gen = wf.astream("task")
        assert inspect.isasyncgen(gen)
        asyncio.run(gen.aclose())

    def test_astream_and_stream_same_chunk_kinds(self):
        """astream and stream produce the same kinds of chunks."""
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("a"))

        async def _async_kinds():
            return {c.kind async for c in wf.astream("task")}

        async_kinds = asyncio.run(_async_kinds())
        sync_kinds = {c.kind for c in wf.stream("task")}
        assert async_kinds == sync_kinds


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.astream_multimodal()
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowAstreamMultimodal:
    def test_astream_multimodal_completes(self):
        from meshflow import Workflow, Agent, ImageInput
        wf = Workflow()
        wf.add(Agent("analyst"))
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")

        async def _run():
            return [c async for c in wf.astream_multimodal("Describe.", [img])]

        chunks = asyncio.run(_run())
        assert isinstance(chunks, list)
        assert any(c.kind == "done" for c in chunks)

    def test_astream_multimodal_empty_inputs(self):
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("analyst"))

        async def _run():
            return [c async for c in wf.astream_multimodal("task", [])]

        chunks = asyncio.run(_run())
        assert isinstance(chunks, list)

    def test_astream_multimodal_is_async_generator(self):
        import inspect
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("a"))
        gen = wf.astream_multimodal("task", [])
        assert inspect.isasyncgen(gen)
        asyncio.run(gen.aclose())


# ═══════════════════════════════════════════════════════════════════════════════
# async_stream_collect()
# ═══════════════════════════════════════════════════════════════════════════════

class TestAsyncStreamCollect:
    def _make_async_stream(self, chunks):
        from meshflow import StreamChunk
        async def _gen():
            for c in chunks:
                yield c
        return _gen()

    def test_collects_tokens(self):
        from meshflow import StreamChunk
        from meshflow import async_stream_collect

        async def _run():
            return await async_stream_collect(self._make_async_stream([
                StreamChunk(kind="node_start"),
                StreamChunk(kind="token", content="Hello"),
                StreamChunk(kind="token", content=" world"),
                StreamChunk(kind="done"),
            ]))

        result = asyncio.run(_run())
        assert result == "Hello world"

    def test_ignores_non_token(self):
        from meshflow import StreamChunk, async_stream_collect

        async def _run():
            return await async_stream_collect(self._make_async_stream([
                StreamChunk(kind="routing", metadata={"tier": "fast"}),
                StreamChunk(kind="token", content="abc"),
                StreamChunk(kind="node_end"),
                StreamChunk(kind="done"),
            ]))

        assert asyncio.run(_run()) == "abc"

    def test_empty_stream(self):
        from meshflow import async_stream_collect

        async def _run():
            async def _empty():
                return
                yield  # make it an async generator

            return await async_stream_collect(_empty())

        assert asyncio.run(_run()) == ""

    def test_from_workflow_astream(self):
        from meshflow import Workflow, Agent, async_stream_collect
        wf = Workflow()
        wf.add(Agent("a"))

        async def _run():
            return await async_stream_collect(wf.astream("task"))

        result = asyncio.run(_run())
        assert isinstance(result, str)

    def test_exported_from_meshflow(self):
        from meshflow import async_stream_collect
        assert callable(async_stream_collect)


# ═══════════════════════════════════════════════════════════════════════════════
# stream_to_sse()
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamToSSE:
    def test_token_chunk_has_data_line(self):
        from meshflow import StreamChunk, stream_to_sse
        chunk = StreamChunk(kind="token", content="Hello", node_name="a")
        sse = stream_to_sse(chunk)
        assert "data: " in sse
        payload = json.loads(sse.split("data: ")[1].split("\n")[0])
        assert payload["content"] == "Hello"
        assert payload["kind"] == "token"

    def test_non_token_chunk_has_event_line(self):
        from meshflow import StreamChunk, stream_to_sse
        chunk = StreamChunk(kind="done")
        sse = stream_to_sse(chunk)
        assert "event: done" in sse

    def test_token_chunk_no_event_line(self):
        from meshflow import StreamChunk, stream_to_sse
        chunk = StreamChunk(kind="token", content="x")
        sse = stream_to_sse(chunk)
        assert "event:" not in sse

    def test_ends_with_double_newline(self):
        from meshflow import StreamChunk, stream_to_sse
        chunk = StreamChunk(kind="token", content="x")
        sse = stream_to_sse(chunk)
        assert sse.endswith("\n\n") or sse.endswith("\n")

    def test_routing_chunk_has_metadata(self):
        from meshflow import StreamChunk, stream_to_sse
        chunk = StreamChunk(kind="routing", node_name="a",
                            metadata={"tier": "fast", "model": "llama3.2"})
        sse = stream_to_sse(chunk)
        assert "routing" in sse
        data_line = next(l for l in sse.splitlines() if l.startswith("data:"))
        payload = json.loads(data_line[6:])
        assert payload["metadata"]["tier"] == "fast"

    def test_valid_json_in_data(self):
        from meshflow import StreamChunk, stream_to_sse
        chunk = StreamChunk(kind="token", content='He said "hello"')
        sse = stream_to_sse(chunk)
        data_line = next(l for l in sse.splitlines() if l.startswith("data:"))
        payload = json.loads(data_line[6:])
        assert payload["content"] == 'He said "hello"'

    def test_exported_from_meshflow(self):
        from meshflow import stream_to_sse
        assert callable(stream_to_sse)


# ═══════════════════════════════════════════════════════════════════════════════
# stream_to_ndjson()
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamToNDJSON:
    def test_returns_json_line(self):
        from meshflow import StreamChunk, stream_to_ndjson
        chunk = StreamChunk(kind="token", content="Hi", node_name="a")
        line = stream_to_ndjson(chunk)
        assert line.endswith("\n")
        payload = json.loads(line)
        assert payload["kind"] == "token"
        assert payload["content"] == "Hi"

    def test_metadata_included(self):
        from meshflow import StreamChunk, stream_to_ndjson
        chunk = StreamChunk(kind="routing", metadata={"tier": "large"})
        payload = json.loads(stream_to_ndjson(chunk))
        assert payload["metadata"]["tier"] == "large"

    def test_exported_from_meshflow(self):
        from meshflow import stream_to_ndjson
        assert callable(stream_to_ndjson)


# ═══════════════════════════════════════════════════════════════════════════════
# chunks_to_sse() async generator
# ═══════════════════════════════════════════════════════════════════════════════

class TestChunksToSSE:
    def _source(self, chunks):
        from meshflow import StreamChunk
        async def _gen():
            for c in chunks:
                yield c
        return _gen()

    def test_yields_sse_strings(self):
        from meshflow import StreamChunk, chunks_to_sse

        async def _run():
            chunks = [
                StreamChunk(kind="token", content="Hi"),
                StreamChunk(kind="done"),
            ]
            return [s async for s in chunks_to_sse(self._source(chunks))]

        results = asyncio.run(_run())
        assert len(results) == 2
        for r in results:
            assert isinstance(r, str)
            assert "data:" in r

    def test_routing_excluded_when_flag_false(self):
        from meshflow import StreamChunk, chunks_to_sse

        async def _run():
            chunks = [
                StreamChunk(kind="routing", metadata={"tier": "fast"}),
                StreamChunk(kind="token", content="x"),
            ]
            return [s async for s in chunks_to_sse(self._source(chunks), include_routing=False)]

        results = asyncio.run(_run())
        assert len(results) == 1  # routing excluded

    def test_routing_included_by_default(self):
        from meshflow import StreamChunk, chunks_to_sse

        async def _run():
            chunks = [
                StreamChunk(kind="routing", metadata={"tier": "fast"}),
                StreamChunk(kind="token", content="x"),
            ]
            return [s async for s in chunks_to_sse(self._source(chunks))]

        results = asyncio.run(_run())
        assert len(results) == 2

    def test_exported_from_meshflow(self):
        from meshflow import chunks_to_sse
        assert callable(chunks_to_sse)


# ═══════════════════════════════════════════════════════════════════════════════
# chunks_to_ndjson() async generator
# ═══════════════════════════════════════════════════════════════════════════════

class TestChunksToNDJSON:
    def test_yields_ndjson_lines(self):
        from meshflow import StreamChunk, chunks_to_ndjson

        async def _run():
            async def _src():
                yield StreamChunk(kind="token", content="x")
                yield StreamChunk(kind="done")
            return [s async for s in chunks_to_ndjson(_src())]

        results = asyncio.run(_run())
        assert len(results) == 2
        for r in results:
            payload = json.loads(r)
            assert "kind" in payload

    def test_exported_from_meshflow(self):
        from meshflow import chunks_to_ndjson
        assert callable(chunks_to_ndjson)


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI integration pattern (no FastAPI import required)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFastAPIPattern:
    """Verify the pattern works end-to-end without importing FastAPI."""

    def test_sse_streaming_body_from_workflow(self):
        """Simulate what a FastAPI SSE endpoint body generator would do."""
        from meshflow import Workflow, Agent
        from meshflow import chunks_to_sse

        wf = Workflow()
        wf.add(Agent("writer"))

        async def _sse_body():
            return [s async for s in chunks_to_sse(wf.astream("Write a haiku"))]

        lines = asyncio.run(_sse_body())
        assert len(lines) > 0
        # Every line should be parseable as SSE
        for line in lines:
            data_lines = [l for l in line.splitlines() if l.startswith("data:")]
            for dl in data_lines:
                json.loads(dl[6:])  # must not raise

    def test_ndjson_streaming_body_from_workflow(self):
        from meshflow import Workflow, Agent, chunks_to_ndjson

        wf = Workflow()
        wf.add(Agent("a"))

        async def _body():
            return [s async for s in chunks_to_ndjson(wf.astream("task"))]

        lines = asyncio.run(_body())
        for line in lines:
            json.loads(line)  # each line must be valid JSON
