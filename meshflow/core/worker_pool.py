"""WorkerPool — horizontal-scaling worker pool for DurableWorkflowExecutor.

Closes the single-process gap vs Flowise / n8n by running workflow tasks from
a shared queue with configurable concurrency.  Supports an in-memory
asyncio.Queue backend (zero deps, default) and an optional Redis backend for
multi-process / multi-host deployments.

Usage — in-memory (default)::

    import asyncio
    from meshflow.core.worker_pool import WorkerPool, WorkerPoolConfig

    async def main():
        pool = WorkerPool(WorkerPoolConfig(concurrency=8))
        await pool.start()

        run_id = pool.submit("path/to/workflow.yaml", {"topic": "AI safety"})
        # ... do other work ...
        result = pool.results(run_id)   # None until done
        await pool.stop()

    asyncio.run(main())

Usage — Redis-backed (distributed, multi-process)::

    config = WorkerPoolConfig(
        backend="redis",
        redis_url="redis://my-redis:6379/0",
        concurrency=16,
    )
    pool = WorkerPool(config)
    await pool.start()
    run_id = pool.submit("workflow.yaml", {"q": "summarise report"})

The pool converts the ``workflow_yaml_path`` string into a fully-hydrated
``WorkflowDefinition`` (via ``meshflow.core.config.load``) and executes it
inside a ``DurableWorkflowExecutor`` on the ``"memory"`` checkpoint backend
(fast, no I/O for short-lived pool tasks — override via ``executor_backend``
on the config if you need cross-process durability).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from meshflow.core.durable import DurableWorkflowExecutor


# ── Queue task envelope ────────────────────────────────────────────────────────


@dataclass
class _QueueTask:
    """Internal envelope placed on the queue."""

    run_id: str
    workflow_yaml_path: str
    input: dict[str, Any]


# ── Queue backends ─────────────────────────────────────────────────────────────


class MemoryQueueBackend:
    """Pure-asyncio in-process queue — zero extra dependencies.

    Parameters
    ----------
    max_size:
        Maximum number of pending tasks.  0 means unbounded.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._q: asyncio.Queue[_QueueTask] = asyncio.Queue(maxsize=max_size)

    async def enqueue(self, task: _QueueTask) -> None:
        """Put *task* on the queue (blocks if the queue is full)."""
        await self._q.put(task)

    async def dequeue(self) -> _QueueTask:
        """Wait for and return the next task."""
        return await self._q.get()

    def task_done(self) -> None:
        """Signal that a previously dequeued task has been processed."""
        self._q.task_done()

    @property
    def qsize(self) -> int:
        """Current number of pending items."""
        return self._q.qsize()


class RedisQueueBackend:
    """Redis-backed queue using LPUSH (producer) / BLPOP (consumer).

    Enables multiple worker processes to share a single queue, which is the
    primary horizontal-scaling path beyond the in-memory backend.

    Requires: ``pip install redis``

    Parameters
    ----------
    redis_url:
        Connection URL, e.g. ``redis://localhost:6379/0``.  Falls back to
        the ``MESHFLOW_REDIS_URL`` environment variable, then
        ``redis://localhost:6379/0``.
    queue_key:
        Redis list key used as the queue (default: ``"meshflow:worker_queue"``).
    blpop_timeout:
        Seconds to block on BLPOP before re-checking (default: 2).
    """

    def __init__(
        self,
        redis_url: str = "",
        queue_key: str = "meshflow:worker_queue",
        blpop_timeout: int = 2,
    ) -> None:
        self._url = redis_url or os.environ.get(
            "MESHFLOW_REDIS_URL", "redis://localhost:6379/0"
        )
        self._key = queue_key
        self._blpop_timeout = blpop_timeout
        self._client: Any = None

    # ------------------------------------------------------------------
    # Lazy connection — imported on first use so redis is truly optional
    # ------------------------------------------------------------------

    def _conn(self) -> Any:
        if self._client is None:
            try:
                import redis  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "RedisQueueBackend requires the redis package: "
                    "pip install redis"
                ) from exc
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    # asyncio-friendly wrappers run the blocking redis calls in a thread
    # executor so they don't stall the event loop.

    async def enqueue(self, task: _QueueTask) -> None:
        """Serialize *task* to JSON and LPUSH onto the Redis list."""
        payload = json.dumps(
            {
                "run_id": task.run_id,
                "workflow_yaml_path": task.workflow_yaml_path,
                "input": task.input,
            }
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._conn().rpush, self._key, payload)

    async def dequeue(self) -> _QueueTask:
        """BLPOP-wait for the next task and return a deserialized ``_QueueTask``."""
        loop = asyncio.get_running_loop()
        while True:
            result = await loop.run_in_executor(
                None, self._conn().blpop, self._key, self._blpop_timeout
            )
            if result is not None:
                _, payload = result
                data = json.loads(payload)
                return _QueueTask(
                    run_id=data["run_id"],
                    workflow_yaml_path=data["workflow_yaml_path"],
                    input=data["input"],
                )
            # blpop timed out — loop and try again (allows clean shutdown)

    def task_done(self) -> None:
        """No-op — Redis has no equivalent tracking; acknowledgement is implicit."""

    @property
    def qsize(self) -> int:
        """Approximate number of items in the Redis list."""
        try:
            return int(self._conn().llen(self._key))
        except Exception:
            return -1


# ── Configuration ──────────────────────────────────────────────────────────────


@dataclass
class WorkerPoolConfig:
    """Configuration for a :class:`WorkerPool`.

    Parameters
    ----------
    backend:
        Queue backend to use: ``"memory"`` (default) or ``"redis"``.
    redis_url:
        Redis connection URL. Ignored when ``backend="memory"``.  Defaults to
        the ``MESHFLOW_REDIS_URL`` environment variable.
    concurrency:
        Maximum number of workflows executed in parallel (default: 4).
    max_queue_size:
        Maximum number of pending tasks in the queue (default: 1000).
        Ignored by the Redis backend.
    executor_backend:
        Checkpoint backend passed to each ``DurableWorkflowExecutor``
        (default: ``"memory"`` — no checkpoint I/O, suitable for transient pool runs).
    """

    backend: str = "memory"
    redis_url: str = field(default_factory=lambda: os.environ.get("MESHFLOW_REDIS_URL", ""))
    concurrency: int = 4
    max_queue_size: int = 1000
    executor_backend: str = "memory"


# ── WorkerPool ─────────────────────────────────────────────────────────────────


class WorkerPool:
    """Queue-based worker pool for concurrent durable workflow execution.

    Each call to :meth:`submit` enqueues a workflow task that will be picked
    up by one of the pool's concurrency worker coroutines and executed via
    :class:`~meshflow.core.durable.DurableWorkflowExecutor`.

    Results are stored in an in-process dict (``_results``) keyed by
    ``run_id`` and can be polled with :meth:`results`.

    Parameters
    ----------
    config:
        Pool configuration.  Defaults to :class:`WorkerPoolConfig` with
        in-memory queue and concurrency of 4.

    Example::

        pool = WorkerPool()
        await pool.start()
        run_id = pool.submit("workflow.yaml", {"topic": "climate"})
        await asyncio.sleep(0)          # yield so workers can run
        result = pool.results(run_id)   # WorkflowResult | None
        await pool.stop()
    """

    def __init__(self, config: WorkerPoolConfig | None = None) -> None:
        self._config = config or WorkerPoolConfig()
        self._queue: MemoryQueueBackend | RedisQueueBackend = self._build_backend()
        self._results: dict[str, Any] = {}  # run_id -> WorkflowResult | Exception
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_backend(self) -> MemoryQueueBackend | RedisQueueBackend:
        if self._config.backend == "redis":
            return RedisQueueBackend(
                redis_url=self._config.redis_url,
            )
        return MemoryQueueBackend(max_size=self._config.max_queue_size)

    async def _worker(self, worker_id: int) -> None:
        """Single worker coroutine — loops pulling tasks until stopped."""
        while not self._stop_event.is_set():
            try:
                # Use wait_for so the worker can react to stop_event promptly
                task = await asyncio.wait_for(
                    self._queue.dequeue(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except Exception:
                # Unexpected backend error — back off briefly then retry
                await asyncio.sleep(0.5)
                continue

            run_id = task.run_id
            try:
                result = await self._execute(task)
                self._results[run_id] = result
            except Exception as exc:
                self._results[run_id] = exc
            finally:
                try:
                    self._queue.task_done()
                except Exception:
                    pass

    async def _execute(self, task: _QueueTask) -> Any:
        """Load the workflow YAML and run it through DurableWorkflowExecutor."""
        from meshflow.core.config import load as load_workflow
        from meshflow.core.mesh import Mesh

        wf = load_workflow(task.workflow_yaml_path)
        executor = DurableWorkflowExecutor(
            run_id=task.run_id,
            backend=self._config.executor_backend,
        )
        mesh = Mesh()
        # The WorkflowDefinition.run signature accepts a task string; pass
        # the serialised input dict as the task prompt if no dedicated key.
        task_prompt = task.input.get("task") or task.input.get("prompt") or json.dumps(task.input)
        return await executor.run(wf, task=task_prompt, mesh=mesh, context=task.input)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spin up worker coroutines.  Must be called inside a running event loop.

        Calling :meth:`start` on an already-running pool is a no-op.
        """
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._worker_tasks = [
            asyncio.create_task(
                self._worker(i), name=f"meshflow-worker-{i}"
            )
            for i in range(self._config.concurrency)
        ]

    async def stop(self, timeout: float = 10.0) -> None:
        """Signal all workers to stop and wait for them to finish.

        Parameters
        ----------
        timeout:
            Seconds to wait for graceful shutdown before cancelling tasks.
        """
        if not self._running:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._worker_tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            for t in self._worker_tasks:
                t.cancel()
        self._worker_tasks = []
        self._running = False

    def submit(
        self,
        workflow_yaml_path: str,
        input: dict[str, Any],
        run_id: str | None = None,
    ) -> str:
        """Enqueue a workflow task and return its ``run_id``.

        Parameters
        ----------
        workflow_yaml_path:
            Path to the workflow YAML file (absolute or relative).
        input:
            Input dict passed as context to the workflow.  Include a ``"task"``
            key for the natural-language prompt, or the whole dict is JSON-
            serialised and used as the prompt.
        run_id:
            Optional stable identifier.  A UUID4 is generated if omitted.

        Returns
        -------
        str
            The ``run_id`` that can be passed to :meth:`results`.

        Raises
        ------
        RuntimeError
            If the pool has not been started yet.
        """
        if not self._running:
            raise RuntimeError(
                "WorkerPool is not running. Call `await pool.start()` first."
            )
        _run_id = run_id or str(uuid.uuid4())
        task = _QueueTask(
            run_id=_run_id,
            workflow_yaml_path=workflow_yaml_path,
            input=input,
        )
        # Schedule the enqueue coroutine on the running loop without blocking
        loop = asyncio.get_event_loop()
        loop.create_task(self._queue.enqueue(task))
        return _run_id

    def results(self, run_id: str) -> Any:
        """Poll for a workflow result.

        Returns ``None`` if the workflow has not finished yet.  Returns a
        :class:`~meshflow.core.workflow.WorkflowResult` on success or the
        raised ``Exception`` if the workflow failed.

        Parameters
        ----------
        run_id:
            The identifier returned by :meth:`submit`.
        """
        return self._results.get(run_id)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def pending(self) -> int:
        """Approximate number of tasks waiting in the queue."""
        return self._queue.qsize

    @property
    def is_running(self) -> bool:
        """True if the pool has been started and not yet stopped."""
        return self._running

    def __repr__(self) -> str:
        return (
            f"WorkerPool(backend={self._config.backend!r}, "
            f"concurrency={self._config.concurrency}, "
            f"running={self._running})"
        )


__all__ = [
    "WorkerPool",
    "WorkerPoolConfig",
    "MemoryQueueBackend",
    "RedisQueueBackend",
]
