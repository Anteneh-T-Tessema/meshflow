"""Tests for meshflow.core.worker_pool — WorkerPool, WorkerPoolConfig, MemoryQueueBackend."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from meshflow.core.worker_pool import (
    MemoryQueueBackend,
    WorkerPool,
    WorkerPoolConfig,
    _QueueTask,
)


# ── WorkerPoolConfig defaults ──────────────────────────────────────────────────


def test_worker_pool_config_defaults() -> None:
    """WorkerPoolConfig should have sensible out-of-the-box defaults."""
    cfg = WorkerPoolConfig()
    assert cfg.backend == "memory"
    assert cfg.concurrency == 4
    assert cfg.max_queue_size == 1000
    assert cfg.executor_backend == "memory"
    # redis_url should either be empty or come from the environment variable
    expected_url = os.environ.get("MESHFLOW_REDIS_URL", "")
    assert cfg.redis_url == expected_url


def test_worker_pool_config_custom() -> None:
    """Custom values are stored correctly."""
    cfg = WorkerPoolConfig(
        backend="redis",
        redis_url="redis://localhost:6379/1",
        concurrency=16,
        max_queue_size=500,
        executor_backend="sqlite",
    )
    assert cfg.backend == "redis"
    assert cfg.redis_url == "redis://localhost:6379/1"
    assert cfg.concurrency == 16
    assert cfg.max_queue_size == 500
    assert cfg.executor_backend == "sqlite"


# ── WorkerPool instantiation ───────────────────────────────────────────────────


def test_worker_pool_instantiation_default() -> None:
    """WorkerPool should be constructable with no arguments."""
    pool = WorkerPool()
    assert not pool.is_running
    assert pool.pending == 0
    assert "memory" in repr(pool)
    assert "concurrency=4" in repr(pool)


def test_worker_pool_instantiation_custom_config() -> None:
    """WorkerPool should reflect a custom WorkerPoolConfig."""
    cfg = WorkerPoolConfig(concurrency=8, max_queue_size=200)
    pool = WorkerPool(cfg)
    assert not pool.is_running
    assert "concurrency=8" in repr(pool)


def test_worker_pool_submit_raises_before_start() -> None:
    """submit() must raise RuntimeError when the pool has not been started."""
    pool = WorkerPool()
    with pytest.raises(RuntimeError, match="not running"):
        pool.submit("workflow.yaml", {"task": "hello"})


# ── submit() returns a run_id string ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_returns_run_id_string() -> None:
    """submit() should return a non-empty string run_id after the pool is started."""
    pool = WorkerPool(WorkerPoolConfig(concurrency=1))
    await pool.start()
    try:
        run_id = pool.submit("workflow.yaml", {"task": "test task"})
        assert isinstance(run_id, str)
        assert len(run_id) > 0
    finally:
        await pool.stop()


@pytest.mark.asyncio
async def test_submit_uses_provided_run_id() -> None:
    """submit() should use the caller-supplied run_id when given."""
    pool = WorkerPool(WorkerPoolConfig(concurrency=1))
    await pool.start()
    try:
        custom_id = "my-stable-run-id-" + str(uuid.uuid4())
        returned_id = pool.submit("workflow.yaml", {"task": "x"}, run_id=custom_id)
        assert returned_id == custom_id
    finally:
        await pool.stop()


@pytest.mark.asyncio
async def test_submit_generates_unique_ids() -> None:
    """Successive submit() calls without an explicit run_id produce distinct ids."""
    pool = WorkerPool(WorkerPoolConfig(concurrency=2))
    await pool.start()
    try:
        ids = {pool.submit("workflow.yaml", {"task": str(i)}) for i in range(10)}
        assert len(ids) == 10
    finally:
        await pool.stop()


# ── MemoryQueueBackend enqueue / dequeue ──────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_queue_enqueue_dequeue_roundtrip() -> None:
    """MemoryQueueBackend should preserve task data through enqueue/dequeue."""
    backend = MemoryQueueBackend(max_size=10)
    task = _QueueTask(
        run_id="test-run-1",
        workflow_yaml_path="wf.yaml",
        input={"task": "hello world"},
    )
    await backend.enqueue(task)
    assert backend.qsize == 1

    retrieved = await asyncio.wait_for(backend.dequeue(), timeout=1.0)
    assert retrieved.run_id == "test-run-1"
    assert retrieved.workflow_yaml_path == "wf.yaml"
    assert retrieved.input == {"task": "hello world"}
    assert backend.qsize == 0


@pytest.mark.asyncio
async def test_memory_queue_fifo_ordering() -> None:
    """MemoryQueueBackend should dequeue tasks in FIFO order."""
    backend = MemoryQueueBackend(max_size=5)
    for i in range(3):
        await backend.enqueue(
            _QueueTask(run_id=str(i), workflow_yaml_path="wf.yaml", input={})
        )

    for expected_id in ("0", "1", "2"):
        task = await asyncio.wait_for(backend.dequeue(), timeout=1.0)
        assert task.run_id == expected_id
        backend.task_done()


@pytest.mark.asyncio
async def test_memory_queue_task_done_unblocks_join() -> None:
    """task_done() should allow asyncio.Queue.join() to complete."""
    backend = MemoryQueueBackend(max_size=5)
    task = _QueueTask(run_id="x", workflow_yaml_path="wf.yaml", input={})
    await backend.enqueue(task)
    await asyncio.wait_for(backend.dequeue(), timeout=1.0)
    backend.task_done()
    # If task_done works correctly, join() should return immediately
    await asyncio.wait_for(backend._q.join(), timeout=1.0)


# ── results() polling ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_results_returns_none_for_unknown_run_id() -> None:
    """results() should return None for a run_id that has never been submitted."""
    pool = WorkerPool()
    await pool.start()
    try:
        assert pool.results("nonexistent-run-id") is None
    finally:
        await pool.stop()


# ── Lifecycle: start / stop ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_sets_is_running() -> None:
    """is_running should be True after start() and False after stop()."""
    pool = WorkerPool(WorkerPoolConfig(concurrency=2))
    assert not pool.is_running
    await pool.start()
    assert pool.is_running
    await pool.stop()
    assert not pool.is_running


@pytest.mark.asyncio
async def test_double_start_is_safe() -> None:
    """Calling start() twice should not create duplicate workers."""
    pool = WorkerPool(WorkerPoolConfig(concurrency=2))
    await pool.start()
    worker_count_before = len(pool._worker_tasks)
    await pool.start()  # second call — should be a no-op
    assert len(pool._worker_tasks) == worker_count_before
    await pool.stop()


@pytest.mark.asyncio
async def test_stop_on_idle_pool_is_safe() -> None:
    """stop() on a never-started pool should be a no-op."""
    pool = WorkerPool()
    await pool.stop()  # should not raise
    assert not pool.is_running
