"""SQLite-backed background task queue for MeshFlow.

TaskQueue   — push / pop / cancel / status over an async SQLite store
QueueWorker — asyncio worker pool that processes queued tasks concurrently

Design:
  - Tasks are durable: stored in SQLite with full audit fields
  - Concurrency is bounded: at most ``concurrency`` tasks run at once
  - Cancel is advisory: pending tasks cancel immediately; running tasks finish
  - Workers are crash-safe: tasks left in "running" state on start are re-queued

Usage::

    queue  = TaskQueue("meshflow_queue.db")
    task_id = await queue.push({"workflow": "review.yaml", "task": "summarise"})
    worker  = QueueWorker(queue, concurrency=4)
    await worker.run()
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Awaitable


class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskItem:
    task_id:     str
    payload:     dict[str, Any]
    status:      TaskStatus = TaskStatus.PENDING
    priority:    int        = 0          # higher = processed sooner
    created_at:  float      = field(default_factory=time.time)
    started_at:  float      = 0.0
    finished_at: float      = 0.0
    result:      dict[str, Any] = field(default_factory=dict)
    error:       str        = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_row(row: tuple[Any, ...]) -> "TaskItem":
        (task_id, payload_json, status, priority,
         created_at, started_at, finished_at, result_json, error) = row
        return TaskItem(
            task_id=task_id,
            payload=json.loads(payload_json),
            status=TaskStatus(status),
            priority=priority,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            result=json.loads(result_json) if result_json else {},
            error=error or "",
        )


# ── SQL constants ─────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS task_queue (
    task_id     TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    priority    INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    started_at  REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0,
    result      TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_tq_status_priority "
    "ON task_queue (status, priority DESC, created_at ASC)"
)

_SELECT_COLS = (
    "task_id, payload, status, priority, created_at, started_at, finished_at, result, error"
)


# ── Synchronous SQLite backend (thread-pool wrapped) ─────────────────────────

class _AsyncSQLite:
    """asyncio-friendly wrapper around synchronous sqlite3."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def open(self) -> None:
        loop = asyncio.get_event_loop()
        self._loop = loop
        self._conn = await loop.run_in_executor(None, self._sync_open)

    def _sync_open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_IDX)
        conn.execute(
            "UPDATE task_queue SET status='pending', started_at=0 WHERE status='running'"
        )
        conn.commit()
        return conn

    async def execute(self, sql: str, params: tuple = ()) -> None:
        loop = asyncio.get_event_loop()
        conn = self._conn
        await loop.run_in_executor(None, lambda: conn.execute(sql, params))

    async def commit(self) -> None:
        loop = asyncio.get_event_loop()
        conn = self._conn
        await loop.run_in_executor(None, conn.commit)

    async def execute_commit(self, sql: str, params: tuple = ()) -> None:
        """Execute and commit in a single executor call (avoids two round-trips)."""
        loop = asyncio.get_event_loop()
        conn = self._conn

        def _run() -> None:
            conn.execute(sql, params)
            conn.commit()

        await loop.run_in_executor(None, _run)

    async def fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        loop = asyncio.get_event_loop()
        conn = self._conn
        row = await loop.run_in_executor(
            None, lambda: conn.execute(sql, params).fetchone()
        )
        if row is None:
            return None
        return tuple(row)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        loop = asyncio.get_event_loop()
        conn = self._conn
        rows = await loop.run_in_executor(
            None, lambda: conn.execute(sql, params).fetchall()
        )
        return [tuple(r) for r in rows]

    async def close(self) -> None:
        if self._conn is not None:
            loop = asyncio.get_event_loop()
            conn = self._conn
            await loop.run_in_executor(None, conn.close)
            self._conn = None


# ── TaskQueue ─────────────────────────────────────────────────────────────────

class TaskQueue:
    """Async SQLite-backed task queue.

    Parameters
    ----------
    db_path:
        SQLite file path (use ``:memory:`` for ephemeral in-process queues).
    """

    def __init__(self, db_path: str = "meshflow_queue.db") -> None:
        self._db_path = db_path
        self._db: _AsyncSQLite | None = None
        self._lock = asyncio.Lock()

    async def _db_conn(self) -> _AsyncSQLite:
        if self._db is None:
            db = _AsyncSQLite(self._db_path)
            await db.open()
            self._db = db
        return self._db

    async def push(
        self,
        payload: dict[str, Any],
        priority: int = 0,
        task_id: str = "",
    ) -> str:
        """Enqueue a new task. Returns the task_id."""
        tid = task_id or str(uuid.uuid4())
        db = await self._db_conn()
        async with self._lock:
            await db.execute_commit(
                "INSERT INTO task_queue (task_id, payload, status, priority, created_at) "
                "VALUES (?, ?, 'pending', ?, ?)",
                (tid, json.dumps(payload), priority, time.time()),
            )
        return tid

    async def pop(self) -> TaskItem | None:
        """Claim the highest-priority pending task (set to running). Returns None if empty."""
        db = await self._db_conn()
        async with self._lock:
            row = await db.fetchone(
                f"SELECT {_SELECT_COLS} FROM task_queue "
                "WHERE status='pending' "
                "ORDER BY priority DESC, created_at ASC LIMIT 1"
            )
            if row is None:
                return None
            tid = row[0]
            await db.execute_commit(
                "UPDATE task_queue SET status='running', started_at=? WHERE task_id=?",
                (time.time(), tid),
            )
            updated = await db.fetchone(
                f"SELECT {_SELECT_COLS} FROM task_queue WHERE task_id=?", (tid,)
            )
        return TaskItem.from_row(updated) if updated else None

    async def complete(self, task_id: str, result: dict[str, Any]) -> None:
        db = await self._db_conn()
        async with self._lock:
            await db.execute_commit(
                "UPDATE task_queue SET status='done', finished_at=?, result=? WHERE task_id=?",
                (time.time(), json.dumps(result), task_id),
            )

    async def fail(self, task_id: str, error: str) -> None:
        db = await self._db_conn()
        async with self._lock:
            await db.execute_commit(
                "UPDATE task_queue SET status='failed', finished_at=?, error=? WHERE task_id=?",
                (time.time(), error, task_id),
            )

    async def cancel(self, task_id: str) -> bool:
        """Cancel a pending task. Running tasks cannot be cancelled. Returns True if cancelled."""
        db = await self._db_conn()
        async with self._lock:
            await db.execute_commit(
                "UPDATE task_queue SET status='cancelled', finished_at=? "
                "WHERE task_id=? AND status='pending'",
                (time.time(), task_id),
            )
            row = await db.fetchone(
                "SELECT status FROM task_queue WHERE task_id=?", (task_id,)
            )
        if row is None:
            return False
        return row[0] == TaskStatus.CANCELLED.value

    async def get(self, task_id: str) -> TaskItem | None:
        db = await self._db_conn()
        row = await db.fetchone(
            f"SELECT {_SELECT_COLS} FROM task_queue WHERE task_id=?", (task_id,)
        )
        return TaskItem.from_row(row) if row else None

    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        limit: int = 50,
    ) -> list[TaskItem]:
        db = await self._db_conn()
        if status:
            rows = await db.fetchall(
                f"SELECT {_SELECT_COLS} FROM task_queue "
                "WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status.value, limit),
            )
        else:
            rows = await db.fetchall(
                f"SELECT {_SELECT_COLS} FROM task_queue ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [TaskItem.from_row(r) for r in rows]

    async def stats(self) -> dict[str, int]:
        db = await self._db_conn()
        rows = await db.fetchall(
            "SELECT status, COUNT(*) FROM task_queue GROUP BY status"
        )
        counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for status_val, count in rows:
            counts[status_val] = count
        return counts

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


# ── QueueWorker ───────────────────────────────────────────────────────────────

Handler = Callable[[TaskItem], Awaitable[dict[str, Any]]]


async def _default_handler(item: TaskItem) -> dict[str, Any]:
    """Default handler: execute a workflow YAML payload through MeshFlow."""
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.executor import WorkflowExecutor

    wf_path = item.payload.get("workflow", "")
    task    = item.payload.get("task", "")
    if not wf_path:
        raise ValueError("payload must contain 'workflow' key (path to YAML)")

    wf = WorkflowDefinition.from_yaml(wf_path)
    executor = WorkflowExecutor(wf)
    result = await executor.run(task or wf.task)
    return {"status": "done", "output": str(result)}


class QueueWorker:
    """Async worker pool that processes tasks from a TaskQueue.

    Parameters
    ----------
    queue:
        The TaskQueue to drain.
    concurrency:
        Max simultaneous in-flight tasks (default 4).
    handler:
        Async callable ``(TaskItem) -> dict``. Defaults to workflow executor.
    poll_interval:
        Seconds between queue polls when idle (default 1.0).
    """

    def __init__(
        self,
        queue: TaskQueue,
        concurrency: int = 4,
        handler: Handler | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._queue = queue
        self._concurrency = concurrency
        self._handler = handler or _default_handler
        self._poll_interval = poll_interval
        self._running = False
        self._semaphore: asyncio.Semaphore | None = None
        self._processed = 0
        self._failed = 0

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Start processing tasks until ``stop_event`` is set or Ctrl-C."""
        self._running = True
        self._semaphore = asyncio.Semaphore(self._concurrency)
        stop = stop_event or asyncio.Event()

        tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

        while not stop.is_set():
            item = await self._queue.pop()
            if item is None:
                await asyncio.sleep(self._poll_interval)
                continue

            t = asyncio.create_task(self._process(item))
            tasks.add(t)
            t.add_done_callback(tasks.discard)

        # Drain in-flight tasks
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._running = False

    async def _process(self, item: TaskItem) -> None:
        async with self._semaphore:  # type: ignore[arg-type]
            try:
                result = await self._handler(item)
                await self._queue.complete(item.task_id, result)
                self._processed += 1
            except Exception as exc:
                await self._queue.fail(item.task_id, str(exc))
                self._failed += 1

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "concurrency": self._concurrency,
            "processed": self._processed,
            "failed": self._failed,
        }
