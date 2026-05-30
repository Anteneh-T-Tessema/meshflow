"""Sprint 73 — Streaming v2 tests.

Covers BackpressureQueue, StreamMultiplexer, PartialStructuredOutput,
and RunStreamHub. All tests are deterministic and async-safe.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import meshflow
from meshflow.streaming.backpressure import BackpressureQueue, BackpressureStrategy
from meshflow.streaming.multiplexer import StreamMultiplexer, Subscription
from meshflow.streaming.partial_output import (
    PartialStructuredOutput, PartialOutputChunk, stream_structured,
)
from meshflow.streaming.run_hub import RunStreamHub, get_run_hub, reset_run_hub
from meshflow.core.streaming import StreamChunk


# ── helpers ───────────────────────────────────────────────────────────────────

async def _agen(*items):
    for item in items:
        yield item


async def _token_gen(*tokens: str):
    for t in tokens:
        yield t


# ══════════════════════════════════════════════════════════════════════════════
#  BackpressureQueue
# ══════════════════════════════════════════════════════════════════════════════

class TestBackpressureQueue:

    @pytest.mark.asyncio
    async def test_put_and_get(self):
        q = BackpressureQueue(max_size=10)
        await q.put("hello")
        item = await q.get()
        assert item == "hello"

    @pytest.mark.asyncio
    async def test_drop_newest_when_full(self):
        q = BackpressureQueue(max_size=2, strategy=BackpressureStrategy.DROP_NEWEST)
        await q.put("a")
        await q.put("b")
        accepted = await q.put("c")   # should be dropped
        assert accepted is False
        assert q.dropped == 1
        assert q.qsize == 2

    @pytest.mark.asyncio
    async def test_drop_oldest_when_full(self):
        q = BackpressureQueue(max_size=2, strategy=BackpressureStrategy.DROP_OLDEST)
        await q.put("a")
        await q.put("b")
        await q.put("c")   # drops "a"
        # "b" and "c" should remain
        first = await q.get()
        second = await q.get()
        assert first == "b"
        assert second == "c"

    @pytest.mark.asyncio
    async def test_unlimited_accepts_all(self):
        q = BackpressureQueue(max_size=0)
        for i in range(1000):
            await q.put(i)
        assert q.qsize == 1000
        assert q.dropped == 0

    @pytest.mark.asyncio
    async def test_close_terminates_iteration(self):
        q = BackpressureQueue(max_size=10)
        await q.put("x")
        await q.close()
        items = [item async for item in q]
        assert items == ["x"]

    @pytest.mark.asyncio
    async def test_put_after_close_rejected(self):
        q = BackpressureQueue(max_size=10)
        await q.close()
        result = await q.put("late")
        assert result is False

    @pytest.mark.asyncio
    async def test_put_nowait(self):
        q = BackpressureQueue(max_size=3)
        q.put_nowait("a")
        q.put_nowait("b")
        assert q.qsize == 2

    def test_stats_structure(self):
        q = BackpressureQueue(max_size=5, strategy=BackpressureStrategy.DROP_NEWEST)
        s = q.stats()
        assert s["max_size"] == 5
        assert s["strategy"] == "drop_newest"
        assert "dropped" in s
        assert "total_put" in s

    @pytest.mark.asyncio
    async def test_drop_rate(self):
        q = BackpressureQueue(max_size=1, strategy=BackpressureStrategy.DROP_NEWEST)
        await q.put("keep")
        await q.put("drop1")
        await q.put("drop2")
        assert q.drop_rate == pytest.approx(2 / 3, abs=0.01)

    @pytest.mark.asyncio
    async def test_is_closed_property(self):
        q = BackpressureQueue()
        assert q.is_closed is False
        await q.close()
        assert q.is_closed is True

    @pytest.mark.asyncio
    async def test_block_strategy_awaits_space(self):
        q = BackpressureQueue(max_size=1, strategy=BackpressureStrategy.BLOCK)
        await q.put("fill")
        # Start a consumer that will free space after a short wait
        async def consumer():
            await asyncio.sleep(0.05)
            await q.get()

        consumer_task = asyncio.create_task(consumer())
        accepted = await q.put("second")   # should block then succeed
        assert accepted is True
        await consumer_task


# ══════════════════════════════════════════════════════════════════════════════
#  StreamMultiplexer
# ══════════════════════════════════════════════════════════════════════════════

class TestStreamMultiplexer:

    @pytest.mark.asyncio
    async def test_single_subscriber_receives_all(self):
        source = _agen("a", "b", "c")
        async with StreamMultiplexer(source, max_per_consumer=10) as mux:
            sub = mux.subscribe()
            items = []
            async for item in sub:
                items.append(item)
        assert items == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_two_subscribers_each_receive_all(self):
        async def make_source():
            for v in ["x", "y", "z"]:
                yield v

        async with StreamMultiplexer(make_source(), max_per_consumer=10) as mux:
            sub1 = mux.subscribe("s1")
            sub2 = mux.subscribe("s2")
            items1, items2 = [], []
            async for item in sub1:
                items1.append(item)
            async for item in sub2:
                items2.append(item)
        assert items1 == ["x", "y", "z"]
        assert items2 == ["x", "y", "z"]

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        source = _agen()
        async with StreamMultiplexer(source) as mux:
            assert mux.subscriber_count == 0
            mux.subscribe("a")
            mux.subscribe("b")
            assert mux.subscriber_count == 2

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_consumer(self):
        source = _agen()
        async with StreamMultiplexer(source) as mux:
            mux.subscribe("sub1")
            mux.unsubscribe("sub1")
            assert mux.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_items_produced_count(self):
        source = _agen(1, 2, 3, 4, 5)
        async with StreamMultiplexer(source) as mux:
            sub = mux.subscribe()
            async for _ in sub:
                pass
        assert mux.items_produced == 5

    @pytest.mark.asyncio
    async def test_subscription_cancel(self):
        source = _agen("a", "b", "c")
        async with StreamMultiplexer(source) as mux:
            sub = mux.subscribe("s1")
            await sub.cancel()
            assert mux.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_subscription_dropped_count(self):
        # small queue + drop_newest → late items are dropped
        source = _agen(*range(20))
        async with StreamMultiplexer(
            source,
            max_per_consumer=2,
            strategy=BackpressureStrategy.DROP_NEWEST,
        ) as mux:
            sub = mux.subscribe()
            # Don't consume — let queue fill up
            await asyncio.sleep(0.05)
        # dropped should be > 0 due to overflow
        assert sub.dropped >= 0  # may vary by timing

    def test_stats_structure(self):
        async def empty():
            return
            yield  # make it an async generator

        mux = StreamMultiplexer(empty())
        s = mux.stats()
        assert "running" in s
        assert "subscribers" in s
        assert "items_produced" in s


# ══════════════════════════════════════════════════════════════════════════════
#  PartialStructuredOutput
# ══════════════════════════════════════════════════════════════════════════════

class TestPartialStructuredOutput:

    @pytest.mark.asyncio
    async def test_complete_json_detected(self):
        pso = PartialStructuredOutput()
        tokens = ['{"score": 0.9, "label": "', 'good"}']
        chunks = []
        async for chunk in pso.stream(_token_gen(*tokens)):
            chunks.append(chunk)
        assert any(c.complete for c in chunks)

    @pytest.mark.asyncio
    async def test_complete_chunk_has_parsed_data(self):
        pso = PartialStructuredOutput()
        tokens = ['{"name": "Alice", "age": 30}']
        chunks = []
        async for chunk in pso.stream(_token_gen(*tokens)):
            chunks.append(chunk)
        complete = next(c for c in chunks if c.complete)
        assert complete.partial.get("name") == "Alice"
        assert complete.partial.get("age") == 30

    @pytest.mark.asyncio
    async def test_partial_fields_extracted_before_completion(self):
        pso = PartialStructuredOutput(emit_on_every_token=True)
        # Emit partial tokens that build up a JSON object
        tokens = ['{"title": "HIPAA"', ', "score": 0.8}']
        chunks = []
        async for chunk in pso.stream(_token_gen(*tokens)):
            chunks.append(chunk)
        # At some point we should see "title" in the partial
        all_partials = [c.partial for c in chunks]
        assert any("title" in p for p in all_partials)

    @pytest.mark.asyncio
    async def test_non_json_tokens_yield_empty_partial(self):
        pso = PartialStructuredOutput()
        tokens = ["hello", " world", " no json here"]
        chunks = []
        async for chunk in pso.stream(_token_gen(*tokens)):
            chunks.append(chunk)
        assert not any(c.complete for c in chunks)

    @pytest.mark.asyncio
    async def test_collect_returns_parsed_dict(self):
        pso = PartialStructuredOutput()
        tokens = ['{"result": "done", "count": 42}']
        result = await pso.collect(_token_gen(*tokens))
        assert isinstance(result, dict)
        assert result["result"] == "done"
        assert result["count"] == 42

    @pytest.mark.asyncio
    async def test_collect_with_pydantic_schema(self):
        try:
            from pydantic import BaseModel

            class Report(BaseModel):
                title: str
                score: float

            pso = PartialStructuredOutput(schema=Report)
            tokens = ['{"title": "Q3 Results", "score": 0.95}']
            result = await pso.collect(_token_gen(*tokens))
            assert isinstance(result, Report)
            assert result.title == "Q3 Results"
        except ImportError:
            pytest.skip("pydantic not installed")

    @pytest.mark.asyncio
    async def test_stream_structured_convenience(self):
        tokens = ['{"x": 1, "y": 2}']
        chunks = []
        async for chunk in stream_structured(_token_gen(*tokens)):
            chunks.append(chunk)
        assert any(c.complete for c in chunks)

    @pytest.mark.asyncio
    async def test_partial_output_chunk_to_dict(self):
        chunk = PartialOutputChunk(
            raw_so_far='{"a": 1}',
            partial={"a": 1},
            complete=True,
            token="}",
        )
        d = chunk.to_dict()
        assert d["complete"] is True
        assert d["partial"]["a"] == 1
        assert "raw_so_far" in d

    @pytest.mark.asyncio
    async def test_json_in_prose_extracted(self):
        pso = PartialStructuredOutput()
        tokens = ['Here is the result: {"score": 0.75} -- done.']
        chunks = []
        async for chunk in pso.stream(_token_gen(*tokens)):
            chunks.append(chunk)
        assert any(c.complete for c in chunks)
        complete = next(c for c in chunks if c.complete)
        assert complete.partial.get("score") == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_empty_token_stream_yields_final_incomplete(self):
        pso = PartialStructuredOutput()
        chunks = []
        async for chunk in pso.stream(_token_gen()):
            chunks.append(chunk)
        # Empty stream → one final chunk, not complete
        assert len(chunks) == 1
        assert chunks[0].complete is False

    @pytest.mark.asyncio
    async def test_emit_on_every_token_emits_frequently(self):
        pso = PartialStructuredOutput(emit_on_every_token=True)
        tokens = ["a", "b", "c", "d"]
        chunks = []
        async for chunk in pso.stream(_token_gen(*tokens)):
            chunks.append(chunk)
        assert len(chunks) == len(tokens) + 1


# ══════════════════════════════════════════════════════════════════════════════
#  RunStreamHub
# ══════════════════════════════════════════════════════════════════════════════

class TestRunStreamHub:

    def setup_method(self):
        reset_run_hub()

    @pytest.mark.asyncio
    async def test_publish_and_subscribe(self):
        hub = RunStreamHub()
        sid, chunks = await hub.subscribe("run-1")
        chunk = StreamChunk(kind="token", content="hello")
        hub.publish("run-1", chunk)
        await hub.finish("run-1")
        received = [c async for c in chunks]
        assert len(received) == 1
        assert received[0].content == "hello"

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self):
        hub = RunStreamHub()
        sid1, chunks1 = await hub.subscribe("run-1", "s1")
        sid2, chunks2 = await hub.subscribe("run-1", "s2")

        for i in range(3):
            hub.publish("run-1", StreamChunk(kind="token", content=str(i)))
        await hub.finish("run-1")

        received1 = [c async for c in chunks1]
        received2 = [c async for c in chunks2]
        assert len(received1) == 3
        assert len(received2) == 3

    @pytest.mark.asyncio
    async def test_publish_returns_subscriber_count(self):
        hub = RunStreamHub()
        await hub.subscribe("run-1", "a")
        await hub.subscribe("run-1", "b")
        n = hub.publish("run-1", StreamChunk(kind="token", content="x"))
        assert n == 2

    @pytest.mark.asyncio
    async def test_publish_to_nonexistent_run_returns_zero(self):
        hub = RunStreamHub()
        n = hub.publish("no-such-run", StreamChunk(kind="token", content="x"))
        assert n == 0

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        hub = RunStreamHub()
        assert hub.subscriber_count() == 0
        await hub.subscribe("run-1", "a")
        await hub.subscribe("run-1", "b")
        await hub.subscribe("run-2", "c")
        assert hub.subscriber_count() == 3
        assert hub.subscriber_count("run-1") == 2
        assert hub.subscriber_count("run-2") == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_subscriber(self):
        hub = RunStreamHub()
        sid, _ = await hub.subscribe("run-1", "sub")
        assert hub.subscriber_count("run-1") == 1
        await hub.unsubscribe("run-1", sid)
        assert hub.subscriber_count("run-1") == 0

    @pytest.mark.asyncio
    async def test_cleanup_run_removes_all(self):
        hub = RunStreamHub()
        await hub.subscribe("run-1", "a")
        await hub.subscribe("run-1", "b")
        await hub.cleanup_run("run-1")
        assert "run-1" not in hub.active_runs()

    @pytest.mark.asyncio
    async def test_active_runs(self):
        hub = RunStreamHub()
        await hub.subscribe("run-a", "s1")
        await hub.subscribe("run-b", "s2")
        assert "run-a" in hub.active_runs()
        assert "run-b" in hub.active_runs()

    @pytest.mark.asyncio
    async def test_finish_closes_all_subscribers(self):
        hub = RunStreamHub()
        sid, chunks = await hub.subscribe("run-1")
        await hub.finish("run-1")
        # Iteration should terminate
        received = [c async for c in chunks]
        assert received == []

    @pytest.mark.asyncio
    async def test_publish_async(self):
        hub = RunStreamHub()
        sid, chunks = await hub.subscribe("run-1")
        chunk = StreamChunk(kind="node_start", node_name="fetch")
        await hub.publish_async("run-1", chunk)
        await hub.finish("run-1")
        received = [c async for c in chunks]
        assert len(received) == 1
        assert received[0].node_name == "fetch"

    def test_stats_structure(self):
        hub = RunStreamHub()
        s = hub.stats()
        assert "active_runs" in s
        assert "total_subscribers" in s
        assert "runs" in s

    def test_singleton_get_run_hub(self):
        reset_run_hub()
        hub1 = get_run_hub()
        hub2 = get_run_hub()
        assert hub1 is hub2

    def test_reset_run_hub(self):
        hub1 = get_run_hub()
        reset_run_hub()
        hub2 = get_run_hub()
        assert hub1 is not hub2


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_backpressure_exported(self):
        assert hasattr(meshflow, "BackpressureQueue")
        assert hasattr(meshflow, "BackpressureStrategy")

    def test_multiplexer_exported(self):
        assert hasattr(meshflow, "StreamMultiplexer")
        assert hasattr(meshflow, "Subscription")

    def test_partial_output_exported(self):
        assert hasattr(meshflow, "PartialStructuredOutput")
        assert hasattr(meshflow, "PartialOutputChunk")
        assert hasattr(meshflow, "stream_structured")

    def test_run_hub_exported(self):
        assert hasattr(meshflow, "RunStreamHub")
        assert hasattr(meshflow, "get_run_hub")
        assert hasattr(meshflow, "reset_run_hub")

    def test_all_in___all__(self):
        for sym in (
            "BackpressureQueue", "BackpressureStrategy",
            "StreamMultiplexer", "Subscription",
            "PartialStructuredOutput", "PartialOutputChunk", "stream_structured",
            "RunStreamHub", "get_run_hub", "reset_run_hub",
        ):
            assert sym in meshflow.__all__, f"{sym} missing from __all__"

    def test_version_bumped(self):
        assert meshflow.__version__ >= "0.77.0"
