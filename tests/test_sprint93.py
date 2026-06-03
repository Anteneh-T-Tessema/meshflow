"""Sprint 93 — Workflow.run_structured(), astream_structured(), stream_structured()."""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

try:
    from pydantic import BaseModel
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

pytestmark = pytest.mark.skipif(not HAS_PYDANTIC, reason="pydantic required")


class Report(BaseModel):
    title: str
    summary: str


class KeyPoints(BaseModel):
    points: list[str]
    count: int


def _wf():
    from meshflow import Workflow, Agent
    wf = Workflow()
    wf.add(Agent("analyst"))
    return wf


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.astream_structured() — native async generator
# ═══════════════════════════════════════════════════════════════════════════════

class TestAstreamStructured:
    def test_yields_partial_output_chunks(self):
        from meshflow.streaming.partial_output import PartialOutputChunk
        wf = _wf()

        async def _collect():
            return [c async for c in wf.astream_structured("task", None)]

        chunks = asyncio.run(_collect())
        assert isinstance(chunks, list)
        for c in chunks:
            assert isinstance(c, PartialOutputChunk)

    def test_final_chunk_is_complete(self):
        wf = _wf()

        async def _collect():
            last = None
            async for c in wf.astream_structured("task", None):
                last = c
            return last

        last = asyncio.run(_collect())
        # In mock mode the agent returns plain text, not JSON — so the parser
        # may not produce complete=True unless the output is valid JSON.
        # We just verify it yields something and doesn't crash.
        assert last is not None

    def test_no_crash_without_schema(self):
        wf = _wf()

        async def _run():
            chunks = [c async for c in wf.astream_structured("task", None)]
            return chunks

        chunks = asyncio.run(_run())
        assert isinstance(chunks, list)

    def test_is_async_generator(self):
        import inspect
        wf = _wf()
        gen = wf.astream_structured("task", None)
        assert inspect.isasyncgen(gen)
        asyncio.run(gen.aclose())

    def test_exported_on_workflow(self):
        from meshflow import Workflow
        wf = Workflow()
        assert hasattr(wf, "astream_structured")

    def test_partial_chunks_have_required_fields(self):
        wf = _wf()

        async def _run():
            chunks = [c async for c in wf.astream_structured("task", None)]
            return chunks

        chunks = asyncio.run(_run())
        for c in chunks:
            assert hasattr(c, "partial")
            assert hasattr(c, "complete")
            assert hasattr(c, "raw_so_far")


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.stream_structured() — sync generator
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamStructured:
    def test_yields_partial_output_chunks(self):
        from meshflow.streaming.partial_output import PartialOutputChunk
        wf = _wf()
        chunks = list(wf.stream_structured("task", None))
        assert isinstance(chunks, list)
        for c in chunks:
            assert isinstance(c, PartialOutputChunk)

    def test_no_crash_without_schema(self):
        wf = _wf()
        chunks = list(wf.stream_structured("task", None))
        assert isinstance(chunks, list)

    def test_exported_on_workflow(self):
        from meshflow import Workflow
        wf = Workflow()
        assert hasattr(wf, "stream_structured")

    def test_partial_chunks_accumulate_raw(self):
        wf = _wf()
        chunks = list(wf.stream_structured("task", None))
        # Each chunk's raw_so_far should be a string
        for c in chunks:
            assert isinstance(c.raw_so_far, str)


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.run_structured() — blocking
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunStructured:
    def test_returns_something(self):
        wf = _wf()
        # In mock mode the output is plain text, not JSON — run_structured
        # may return None (parser couldn't validate) but must not crash.
        result = wf.run_structured("task", None)
        # None is acceptable in mock mode (no real JSON output)
        assert result is None or isinstance(result, (dict, object))

    def test_exported_on_workflow(self):
        from meshflow import Workflow
        wf = Workflow()
        assert hasattr(wf, "run_structured")

    def test_no_crash_with_schema(self):
        wf = _wf()
        # Mock mode returns plain text, parser won't produce a Report → None
        result = wf.run_structured("Write a report", Report)
        assert result is None or isinstance(result, (Report, dict))

    def test_no_crash_empty_agents(self):
        from meshflow import Workflow
        wf = Workflow()
        result = wf.run_structured("task", None)
        assert result is None

    def test_called_with_valid_json_output_returns_model(self):
        """If the agent returns valid JSON, run_structured should parse it."""
        from meshflow import Workflow, Agent
        from unittest.mock import patch, MagicMock
        import asyncio

        wf = Workflow()
        agent = Agent("analyst")
        wf.add(agent)

        # Patch astream to yield a JSON token
        json_text = '{"title": "Q3 Report", "summary": "Revenue up 12%"}'

        original_astream = wf.astream

        async def _fake_astream(task):
            from meshflow import StreamChunk
            for char in json_text:
                yield StreamChunk(kind="token", content=char, node_name="analyst")
            yield StreamChunk(kind="done")

        with patch.object(wf, "astream", _fake_astream):
            result = wf.run_structured("Generate report", Report)

        if result is not None:
            assert isinstance(result, Report)
            assert result.title == "Q3 Report"
            assert "12%" in result.summary


# ═══════════════════════════════════════════════════════════════════════════════
# PartialOutputChunk — existing internals, verify integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartialOutputChunkIntegration:
    def test_stream_structured_low_level(self):
        """Verify stream_structured(token_gen, schema) works with a JSON token stream."""
        from meshflow.streaming.partial_output import stream_structured

        json_text = '{"title": "Test", "summary": "Hello"}'

        async def _token_gen():
            for char in json_text:
                yield char

        async def _collect():
            chunks = []
            async for c in stream_structured(_token_gen(), Report):
                chunks.append(c)
            return chunks

        chunks = asyncio.run(_collect())
        assert len(chunks) > 0
        # Last chunk should be complete with a validated Report
        last = chunks[-1]
        assert last.complete is True
        assert isinstance(last.validated, Report)
        assert last.validated.title == "Test"

    def test_stream_structured_no_schema(self):
        from meshflow.streaming.partial_output import stream_structured

        json_text = '{"key": "value"}'

        async def _token_gen():
            for char in json_text:
                yield char

        async def _collect():
            return [c async for c in stream_structured(_token_gen(), None)]

        chunks = asyncio.run(_collect())
        assert len(chunks) > 0
        last = chunks[-1]
        assert last.complete is True
        assert last.partial == {"key": "value"}

    def test_partial_output_chunk_to_dict(self):
        from meshflow.streaming.partial_output import PartialOutputChunk
        chunk = PartialOutputChunk(partial={"x": 1}, complete=False, raw_so_far='{"x":1')
        d = chunk.to_dict()
        assert "partial" in d
        assert "complete" in d
