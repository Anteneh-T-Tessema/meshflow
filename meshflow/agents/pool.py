"""AgentPool — a governed, auto-scaling pool of agents backed by an asyncio work queue.

Usage:
    from meshflow.agents.pool import AgentPool

    researcher = Agent(name="r1", role="researcher")
    executor   = Agent(name="e1", role="executor")

    async with AgentPool(agents=[researcher, executor], concurrency=4) as pool:
        result   = await pool.submit("Summarise AI research")
        results  = await pool.map(["task A", "task B", "task C"])
        print(pool.stats)

    # Or manually:
    pool = AgentPool(agents=[...], concurrency=8, policy="regulated")
    await pool.start()
    result = await pool.submit("Do something", context={"k": "v"})
    await pool.stop()

AgentPool tracks aggregate cost/token spend across all dispatched tasks and
exposes a ``PoolStats`` snapshot for dashboards and the ``GET /pool/status``
server endpoint.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Task item ─────────────────────────────────────────────────────────────────


@dataclass
class _PoolTask:
    """Internal work-queue item."""

    task_id: str
    input: str
    context: dict[str, Any]
    policy: Any  # Policy | str | None
    future: asyncio.Future[Any]
    submitted_at: float = field(default_factory=time.monotonic)


# ── Stats ─────────────────────────────────────────────────────────────────────


@dataclass
class PoolStats:
    """Live snapshot of an AgentPool's counters and queue depth."""

    pool_name: str
    concurrency: int
    agent_count: int
    active_workers: int
    queued: int
    total_submitted: int
    total_completed: int
    total_failed: int
    total_cost_usd: float
    total_tokens: int
    uptime_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_name": self.pool_name,
            "concurrency": self.concurrency,
            "agent_count": self.agent_count,
            "active_workers": self.active_workers,
            "queued": self.queued,
            "total_submitted": self.total_submitted,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "uptime_s": round(self.uptime_s, 1),
        }


# ── Pool ──────────────────────────────────────────────────────────────────────


class AgentPool:
    """A governed, bounded pool of MeshFlow agents driven by an asyncio queue.

    Parameters
    ----------
    agents      : One or more Agent objects.  The pool round-robins across them.
    concurrency : Maximum number of tasks executing simultaneously.
    policy      : Default policy applied to submitted tasks (overridable per task).
    name        : Display name shown in PoolStats and server endpoint.
    """

    def __init__(
        self,
        agents: list[Any],
        concurrency: int = 4,
        policy: Any = None,
        name: str = "default",
    ) -> None:
        if not agents:
            raise ValueError("AgentPool requires at least one agent.")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1.")

        self._agents = list(agents)
        self._concurrency = concurrency
        self._policy = policy
        self._name = name

        self._queue: asyncio.Queue[_PoolTask | None] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._sem = asyncio.Semaphore(concurrency)
        self._started = False
        self._start_time: float = 0.0

        # Counters (updated under asyncio — no lock needed)
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._active = 0
        self._total_cost_usd: float = 0.0
        self._total_tokens: int = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the worker loop.  Call once before submit()."""
        if self._started:
            return
        self._started = True
        self._start_time = time.monotonic()
        for i in range(self._concurrency):
            t = asyncio.create_task(self._worker(i), name=f"pool-{self._name}-worker-{i}")
            self._workers.append(t)

    async def stop(self) -> None:
        """Drain pending tasks and shut down all workers gracefully."""
        for _ in self._workers:
            await self._queue.put(None)  # poison pill
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._started = False

    async def __aenter__(self) -> "AgentPool":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ── Submission ────────────────────────────────────────────────────────────

    async def submit(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        policy: Any = None,
    ) -> Any:
        """Submit a single task and await its result.

        Parameters
        ----------
        task    : The natural-language task string.
        context : Optional context dict forwarded to the agent.
        policy  : Per-task policy override (falls back to pool-level policy).

        Returns
        -------
        The agent's result object (whatever agent.run() returns).
        """
        if not self._started:
            await self.start()

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        item = _PoolTask(
            task_id=str(uuid.uuid4()),
            input=task,
            context=context or {},
            policy=policy or self._policy,
            future=fut,
        )
        self._submitted += 1
        await self._queue.put(item)
        return await fut

    async def map(
        self,
        tasks: list[str],
        context: dict[str, Any] | None = None,
        policy: Any = None,
    ) -> list[Any]:
        """Submit multiple tasks and wait for all to complete (preserving order).

        Parameters
        ----------
        tasks   : List of task strings to dispatch concurrently.
        context : Shared context dict for every task.
        policy  : Shared per-task policy override.

        Returns
        -------
        List of results in the same order as *tasks*.
        """
        if not self._started:
            await self.start()

        futures = [
            asyncio.ensure_future(self.submit(t, context=context, policy=policy))
            for t in tasks
        ]
        return list(await asyncio.gather(*futures, return_exceptions=True))

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> PoolStats:
        return PoolStats(
            pool_name=self._name,
            concurrency=self._concurrency,
            agent_count=len(self._agents),
            active_workers=self._active,
            queued=self._queue.qsize(),
            total_submitted=self._submitted,
            total_completed=self._completed,
            total_failed=self._failed,
            total_cost_usd=self._total_cost_usd,
            total_tokens=self._total_tokens,
            uptime_s=time.monotonic() - self._start_time if self._start_time else 0.0,
        )

    # ── Worker ────────────────────────────────────────────────────────────────

    async def _worker(self, worker_id: int) -> None:
        """Long-running coroutine: pick tasks from the queue and execute them."""
        agent_idx = worker_id % len(self._agents)

        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                break

            self._active += 1
            agent = self._agents[agent_idx % len(self._agents)]
            agent_idx += 1

            try:
                result = await self._run_task(agent, item)
                self._completed += 1
                # Accumulate cost/tokens if the result carries them
                self._total_cost_usd += _extract_float(result, "total_cost_usd", "cost_usd")
                self._total_tokens += _extract_int(result, "total_tokens", "tokens")
                if not item.future.done():
                    item.future.set_result(result)
            except Exception as exc:
                self._failed += 1
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._active -= 1
                self._queue.task_done()

    async def _run_task(self, agent: Any, item: _PoolTask) -> Any:
        """Dispatch a single task to an agent, honouring per-task policy."""
        run_kwargs: dict[str, Any] = {}
        if item.context:
            run_kwargs["context"] = item.context
        policy = item.policy or self._policy
        if policy is not None:
            run_kwargs["policy"] = policy

        # Agent.run() signature: run(task, **kwargs) or run(task, context, policy)
        try:
            return await agent.run(item.input, **run_kwargs)
        except TypeError:
            # Fallback: plain run(task)
            return await agent.run(item.input)


# ── Global pool registry (for server /pool/status endpoint) ──────────────────

_POOL_REGISTRY: dict[str, "AgentPool"] = {}


def register_pool(pool: AgentPool) -> None:
    """Register a pool so the server can expose its stats."""
    _POOL_REGISTRY[pool._name] = pool


def deregister_pool(name: str) -> None:
    _POOL_REGISTRY.pop(name, None)


def all_pool_stats() -> list[dict[str, Any]]:
    """Return PoolStats.to_dict() for every registered pool."""
    return [p.stats.to_dict() for p in _POOL_REGISTRY.values()]


# ── helpers ───────────────────────────────────────────────────────────────────


def _extract_float(obj: Any, *keys: str) -> float:
    for k in keys:
        v = getattr(obj, k, None) if not isinstance(obj, dict) else obj.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _extract_int(obj: Any, *keys: str) -> int:
    for k in keys:
        v = getattr(obj, k, None) if not isinstance(obj, dict) else obj.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return 0
