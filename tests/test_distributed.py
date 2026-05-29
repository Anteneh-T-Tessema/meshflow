"""Sprint 70 — Distributed task execution tests."""

from __future__ import annotations

import asyncio
import pytest

from meshflow.runtime.distributed import (
    DistributedPool,
    DistributedWorker,
    TaskHandle,
    TaskRecord,
    _SQLiteQueue,
)


# ── _SQLiteQueue ──────────────────────────────────────────────────────────────


def test_queue_push_and_claim():
    q = _SQLiteQueue(":memory:")
    q.push("t1", "analyst", "Summarise Q3")
    rec = q.claim()
    assert rec is not None
    assert rec.task_id == "t1"
    assert rec.agent_name == "analyst"
    assert rec.status == "running"


def test_queue_claim_empty_returns_none():
    q = _SQLiteQueue(":memory:")
    assert q.claim() is None


def test_queue_complete():
    q = _SQLiteQueue(":memory:")
    q.push("t1", "a", "task")
    q.claim()
    q.complete("t1", {"result": "done"})
    rec = q.fetch("t1")
    assert rec is not None
    assert rec.status == "done"
    assert rec.result["result"] == "done"


def test_queue_fail():
    q = _SQLiteQueue(":memory:")
    q.push("t1", "a", "task")
    q.claim()
    q.fail("t1", "some error")
    rec = q.fetch("t1")
    assert rec is not None
    assert rec.status == "failed"
    assert "some error" in rec.error


def test_queue_pending_count():
    q = _SQLiteQueue(":memory:")
    q.push("t1", "a", "t")
    q.push("t2", "a", "t")
    assert q.pending_count() == 2
    q.claim()
    assert q.pending_count() == 1


def test_queue_list_tasks():
    q = _SQLiteQueue(":memory:")
    q.push("t1", "a", "task one")
    q.push("t2", "b", "task two")
    tasks = q.list_tasks(limit=10)
    assert len(tasks) == 2


# ── TaskHandle ────────────────────────────────────────────────────────────────


def test_task_handle_str():
    h = TaskHandle(task_id="abc-123", agent_name="analyst")
    assert str(h) == "abc-123"


# ── DistributedPool ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pool_submit_returns_handle():
    pool = DistributedPool(queue_url=":memory:")
    handle = await pool.submit("analyst", "Do research")
    assert isinstance(handle, TaskHandle)
    assert handle.agent_name == "analyst"


@pytest.mark.asyncio
async def test_pool_result_with_inline_agent():
    class FakeAgent:
        async def run(self, task, context=None):
            return {"result": f"done: {task}", "agent_name": "fake"}

    pool = DistributedPool(queue_url=":memory:", timeout=5.0)
    handle = await pool.submit("fake", "hello")

    # With agent= supplied the pool executes inline
    result = await pool.result(handle, agent=FakeAgent())
    assert result["result"] == "done: hello"


@pytest.mark.asyncio
async def test_pool_result_timeout():
    pool = DistributedPool(queue_url=":memory:", poll_interval=0.05, timeout=0.2)
    handle = await pool.submit("nobody", "will never run")
    with pytest.raises(TimeoutError):
        await pool.result(handle)


@pytest.mark.asyncio
async def test_pool_result_failed_task():
    pool = DistributedPool(queue_url=":memory:")
    handle = await pool.submit("agent", "task")

    # Mark the task as failed externally
    pool._queue.claim()
    pool._queue.fail(handle.task_id, "deliberate failure")

    with pytest.raises(RuntimeError, match="deliberate failure"):
        await pool.result(handle, timeout=1.0)


# ── DistributedWorker integration ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_processes_task():
    processed = []

    class EchoAgent:
        async def run(self, task, context=None):
            processed.append(task)
            return {"result": f"echo:{task}"}

    pool = DistributedPool(queue_url=":memory:")
    handle = await pool.submit("echo", "ping")

    worker = DistributedWorker(
        queue_url=":memory:",
        concurrency=1,
        agent_factory=lambda name: EchoAgent(),
        poll_interval=0.05,
    )
    # Share the same underlying queue
    worker._queue = pool._queue

    # Run worker for one iteration
    task_rec = pool._queue.claim()
    assert task_rec is not None
    agent = EchoAgent()
    res = await agent.run(task_rec.task)
    pool._queue.complete(task_rec.task_id, res)

    final = pool._queue.fetch(handle.task_id)
    assert final is not None
    assert final.status == "done"
    assert "echo:ping" in final.result.get("result", "")
