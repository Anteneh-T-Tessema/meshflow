"""Distributed task execution — multi-process/multi-node agent pool (shared gap).

Provides a ``DistributedWorker`` that polls a shared task queue (SQLite for
local dev, Redis for production) and executes agent tasks in parallel.
``DistributedPool`` is the client-side counterpart: submit tasks and await
results.

Local dev (SQLite — zero extra deps)::

    from meshflow import Agent
    from meshflow.runtime.distributed import DistributedPool

    pool = DistributedPool()                         # SQLite backend by default
    agent = Agent(name="analyst", role="researcher")

    handle = await pool.submit("analyst", "Summarise Q3 earnings")
    result = await pool.result(handle, agent=agent)
    print(result["output"])

Start a worker process::

    # In a separate terminal / container
    meshflow worker start --queue meshflow_tasks.db --concurrency 4

Redis backend (optional — pip install meshflow[redis])::

    pool = DistributedPool(queue_url="redis://localhost:6379/0")

"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Task record ───────────────────────────────────────────────────────────────

@dataclass
class TaskHandle:
    task_id: str
    agent_name: str
    submitted_at: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return self.task_id


@dataclass
class TaskRecord:
    task_id: str
    agent_name: str
    task: str
    status: str          # pending | running | done | failed
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# ── SQLite queue backend ───────────────────────────────────────────────────────

class _SQLiteQueue:
    def __init__(self, path: str = "meshflow_tasks.db") -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS distributed_tasks (
                task_id    TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                task       TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending',
                result     TEXT NOT NULL DEFAULT '{}',
                error      TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()

    def push(self, task_id: str, agent_name: str, task: str) -> None:
        now = time.time()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO distributed_tasks
                    (task_id, agent_name, task, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (task_id, agent_name, task, now, now),
            )
            conn.commit()

    def claim(self) -> TaskRecord | None:
        """Atomically claim one pending task, marking it running. Returns None if queue empty."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM distributed_tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE distributed_tasks SET status='running', updated_at=? WHERE task_id=?",
                (time.time(), row["task_id"]),
            )
            conn.commit()
        return TaskRecord(
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            task=row["task"],
            status="running",
        )

    def complete(self, task_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE distributed_tasks SET status='done', result=?, updated_at=? WHERE task_id=?",
                (json.dumps(result), time.time(), task_id),
            )
            conn.commit()

    def fail(self, task_id: str, error: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE distributed_tasks SET status='failed', error=?, updated_at=? WHERE task_id=?",
                (error, time.time(), task_id),
            )
            conn.commit()

    def fetch(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM distributed_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return TaskRecord(
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            task=row["task"],
            status=row["status"],
            result=json.loads(row["result"]),
            error=row["error"],
        )

    def pending_count(self) -> int:
        with self._lock:
            conn = self._connect()
            return conn.execute(
                "SELECT COUNT(*) FROM distributed_tasks WHERE status='pending'"
            ).fetchone()[0]

    def list_tasks(self, status: str = "", limit: int = 50) -> list[TaskRecord]:
        with self._lock:
            conn = self._connect()
            if status:
                rows = conn.execute(
                    "SELECT * FROM distributed_tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM distributed_tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            TaskRecord(
                task_id=r["task_id"],
                agent_name=r["agent_name"],
                task=r["task"],
                status=r["status"],
                result=json.loads(r["result"]),
                error=r["error"],
            )
            for r in rows
        ]


# ── Redis backend (optional) ──────────────────────────────────────────────────

class _RedisQueue:
    """Redis-backed queue.  Requires ``redis-py``: pip install meshflow[redis]."""

    _PENDING_KEY = "meshflow:tasks:pending"
    _TASKS_KEY   = "meshflow:tasks:data:{task_id}"

    def __init__(self, url: str) -> None:
        try:
            import redis  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "Redis backend requires redis-py: pip install 'meshflow[redis]'"
            ) from exc
        self._r = redis.from_url(url, decode_responses=True)

    def push(self, task_id: str, agent_name: str, task: str) -> None:
        now = time.time()
        data = json.dumps({
            "task_id": task_id,
            "agent_name": agent_name,
            "task": task,
            "status": "pending",
            "result": {},
            "error": "",
            "created_at": now,
            "updated_at": now,
        })
        key = self._TASKS_KEY.format(task_id=task_id)
        self._r.set(key, data)
        self._r.lpush(self._PENDING_KEY, task_id)

    def claim(self) -> TaskRecord | None:
        task_id = self._r.rpop(self._PENDING_KEY)
        if task_id is None:
            return None
        key = self._TASKS_KEY.format(task_id=task_id)
        raw = self._r.get(key)
        if raw is None:
            return None
        d = json.loads(raw)
        d["status"] = "running"
        d["updated_at"] = time.time()
        self._r.set(key, json.dumps(d))
        return TaskRecord(
            task_id=d["task_id"],
            agent_name=d["agent_name"],
            task=d["task"],
            status="running",
        )

    def complete(self, task_id: str, result: dict[str, Any]) -> None:
        key = self._TASKS_KEY.format(task_id=task_id)
        raw = self._r.get(key)
        if raw:
            d = json.loads(raw)
            d.update(status="done", result=result, updated_at=time.time())
            self._r.set(key, json.dumps(d))

    def fail(self, task_id: str, error: str) -> None:
        key = self._TASKS_KEY.format(task_id=task_id)
        raw = self._r.get(key)
        if raw:
            d = json.loads(raw)
            d.update(status="failed", error=error, updated_at=time.time())
            self._r.set(key, json.dumps(d))

    def fetch(self, task_id: str) -> TaskRecord | None:
        key = self._TASKS_KEY.format(task_id=task_id)
        raw = self._r.get(key)
        if raw is None:
            return None
        d = json.loads(raw)
        return TaskRecord(
            task_id=d["task_id"],
            agent_name=d["agent_name"],
            task=d["task"],
            status=d["status"],
            result=d.get("result", {}),
            error=d.get("error", ""),
        )

    def pending_count(self) -> int:
        return self._r.llen(self._PENDING_KEY)


# ── DistributedWorker ─────────────────────────────────────────────────────────

class DistributedWorker:
    """Worker process that pulls tasks from a shared queue and executes them.

    Parameters
    ----------
    queue_url:
        ``"sqlite://path.db"`` (default) or ``"redis://host:port/db"``.
    concurrency:
        Maximum parallel agent executions.
    agent_factory:
        Callable ``(agent_name: str) -> Agent`` — builds the agent for a task.
        If ``None``, creates a default ``Agent(name=agent_name, role="executor")``.
    poll_interval:
        Seconds between queue polls when idle.
    """

    def __init__(
        self,
        queue_url: str = "sqlite://meshflow_tasks.db",
        *,
        concurrency: int = 4,
        agent_factory: Any = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._queue = _make_queue(queue_url)
        self._concurrency = concurrency
        self._agent_factory = agent_factory
        self._poll_interval = poll_interval
        self._running = False

    async def start(self) -> None:
        """Start the worker loop (runs until stop() or KeyboardInterrupt)."""
        self._running = True
        sem = asyncio.Semaphore(self._concurrency)

        async def _process_task(record: TaskRecord) -> None:
            async with sem:
                try:
                    agent = self._build_agent(record.agent_name)
                    result = await agent.run(record.task)
                    self._queue.complete(record.task_id, result)
                except Exception as exc:
                    self._queue.fail(record.task_id, str(exc))

        while self._running:
            record = self._queue.claim()
            if record is None:
                await asyncio.sleep(self._poll_interval)
                continue
            asyncio.create_task(_process_task(record))

    def stop(self) -> None:
        self._running = False

    def _build_agent(self, agent_name: str) -> Any:
        if self._agent_factory is not None:
            return self._agent_factory(agent_name)
        from meshflow.agents.builder import Agent
        return Agent(name=agent_name, role="executor")


# ── DistributedPool ───────────────────────────────────────────────────────────

class DistributedPool:
    """Client-side interface for a distributed task queue.

    Parameters
    ----------
    queue_url:
        Same URL format as ``DistributedWorker``.
    poll_interval:
        Seconds between status checks in ``result()``.
    timeout:
        Max seconds to wait for a result before raising ``TimeoutError``.
    """

    def __init__(
        self,
        queue_url: str = "sqlite://meshflow_tasks.db",
        *,
        poll_interval: float = 0.5,
        timeout: float = 300.0,
    ) -> None:
        self._queue = _make_queue(queue_url)
        self._poll_interval = poll_interval
        self._timeout = timeout

    async def submit(self, agent_name: str, task: str) -> TaskHandle:
        """Enqueue a task and return its handle immediately."""
        task_id = str(uuid.uuid4())
        self._queue.push(task_id, agent_name, task)
        return TaskHandle(task_id=task_id, agent_name=agent_name)

    async def result(
        self,
        handle: TaskHandle,
        agent: Any = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Wait for a task to complete and return its result.

        If *agent* is supplied and the worker process is running in the same
        event loop (dev mode), the task is executed inline.
        """
        deadline = time.monotonic() + (timeout or self._timeout)

        # Dev-mode: execute inline if an agent is supplied
        record = self._queue.fetch(handle.task_id)
        if agent is not None and record and record.status == "pending":
            try:
                res = await agent.run(record.task)
                self._queue.complete(handle.task_id, res)
                return res
            except Exception as exc:
                self._queue.fail(handle.task_id, str(exc))
                raise

        while time.monotonic() < deadline:
            rec = self._queue.fetch(handle.task_id)
            if rec is None:
                raise ValueError(f"Task {handle.task_id!r} not found in queue")
            if rec.status == "done":
                return rec.result
            if rec.status == "failed":
                raise RuntimeError(f"Task {handle.task_id!r} failed: {rec.error}")
            await asyncio.sleep(self._poll_interval)

        raise TimeoutError(f"Task {handle.task_id!r} did not complete within {timeout or self._timeout}s")

    def list_tasks(self, status: str = "", limit: int = 50) -> list[TaskRecord]:
        return self._queue.list_tasks(status=status, limit=limit)

    def pending_count(self) -> int:
        return self._queue.pending_count()


# ── Factory ────────────────────────────────────────────────────────────────────

def _make_queue(url: str) -> Any:
    if url.startswith("redis://") or url.startswith("rediss://"):
        return _RedisQueue(url)
    path = url.removeprefix("sqlite://") if url.startswith("sqlite://") else url
    return _SQLiteQueue(path)


__all__ = ["DistributedWorker", "DistributedPool", "TaskHandle", "TaskRecord"]
