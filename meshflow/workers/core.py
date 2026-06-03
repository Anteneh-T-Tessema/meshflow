"""Core durable worker implementation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ── Job status ────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    RETRYING  = "retrying"
    DEAD      = "dead"          # exhausted all retries


# ── Job record ────────────────────────────────────────────────────────────────

@dataclass
class JobRecord:
    """Mutable state object for one job execution."""

    job_id:       str
    task_name:    str
    args:         list[Any]
    kwargs:       dict[str, Any]
    status:       JobStatus = JobStatus.QUEUED
    retries:      int       = 0
    max_retries:  int       = 3
    result:       Any       = None
    error:        str | None = None
    created_at:   float     = field(default_factory=time.time)
    started_at:   float | None = None
    finished_at:  float | None = None
    scheduled_for: float | None = None    # epoch timestamp for cron-triggered jobs

    @property
    def duration_ms(self) -> int:
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at) * 1000)
        return 0

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.DEAD)


# ── Job stores ────────────────────────────────────────────────────────────────

class InMemoryJobStore:
    """Thread-safe in-memory job store."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def put(self, job: JobRecord) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def next_queued(self) -> JobRecord | None:
        with self._lock:
            now = time.time()
            for j in self._jobs.values():
                if j.status == JobStatus.QUEUED:
                    if j.scheduled_for is None or j.scheduled_for <= now:
                        return j
        return None

    def list_all(self) -> list[JobRecord]:
        with self._lock:
            return list(self._jobs.values())

    def stats(self) -> "WorkerStats":
        with self._lock:
            jobs = list(self._jobs.values())
        by_status = {}
        for j in jobs:
            by_status[j.status] = by_status.get(j.status, 0) + 1
        return WorkerStats(
            queued=by_status.get(JobStatus.QUEUED, 0),
            running=by_status.get(JobStatus.RUNNING, 0),
            completed=by_status.get(JobStatus.COMPLETED, 0),
            failed=by_status.get(JobStatus.FAILED, 0) + by_status.get(JobStatus.DEAD, 0),
            total=len(jobs),
        )


class SQLiteJobStore:
    """Persistent SQLite-backed job store.

    Survives process restarts — incomplete jobs are re-queued automatically.
    """

    def __init__(self, path: str = "meshflow_workers.db") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init()

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._conn
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    args TEXT DEFAULT '[]',
                    kwargs TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'queued',
                    retries INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    result TEXT,
                    error TEXT,
                    created_at REAL,
                    started_at REAL,
                    finished_at REAL,
                    scheduled_for REAL
                )
            """)
            # Re-queue jobs that were RUNNING when the process crashed
            conn.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            conn.commit()

    def put(self, job: JobRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status, retries=excluded.retries,
                    result=excluded.result, error=excluded.error,
                    started_at=excluded.started_at, finished_at=excluded.finished_at
            """, (
                job.job_id, job.task_name,
                json.dumps(job.args), json.dumps(job.kwargs),
                job.status.value, job.retries, job.max_retries,
                json.dumps(job.result) if job.result is not None else None,
                job.error, job.created_at, job.started_at, job.finished_at,
                job.scheduled_for,
            ))
            conn.commit()

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def next_queued(self) -> JobRecord | None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' AND (scheduled_for IS NULL OR scheduled_for <= ?) "
                "ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_all(self) -> list[JobRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_record(r) for r in rows]

    def stats(self) -> "WorkerStats":
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM jobs GROUP BY status"
            ).fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        return WorkerStats(
            queued=by_status.get("queued", 0),
            running=by_status.get("running", 0),
            completed=by_status.get("completed", 0),
            failed=by_status.get("failed", 0) + by_status.get("dead", 0),
            total=sum(by_status.values()),
        )

    @staticmethod
    def _row_to_record(row: Any) -> JobRecord:
        r = dict(row)
        return JobRecord(
            job_id=r["job_id"],
            task_name=r["task_name"],
            args=json.loads(r["args"] or "[]"),
            kwargs=json.loads(r["kwargs"] or "{}"),
            status=JobStatus(r["status"]),
            retries=r["retries"],
            max_retries=r["max_retries"],
            result=json.loads(r["result"]) if r["result"] else None,
            error=r["error"],
            created_at=r["created_at"] or time.time(),
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            scheduled_for=r["scheduled_for"],
        )


# ── Worker stats ──────────────────────────────────────────────────────────────

@dataclass
class WorkerStats:
    queued:    int = 0
    running:   int = 0
    completed: int = 0
    failed:    int = 0
    total:     int = 0

    def __str__(self) -> str:
        return (f"WorkerStats(queued={self.queued}, running={self.running}, "
                f"completed={self.completed}, failed={self.failed}, total={self.total})")


# ── @durable_task ─────────────────────────────────────────────────────────────

class DurableTask:
    """A decorated async function that runs as a retryable durable job.

    Produced by the :func:`durable_task` decorator.
    """

    def __init__(
        self,
        fn: Callable,
        max_retries: int,
        backoff_s: float,
        timeout_s: float | None,
    ) -> None:
        self._fn         = fn
        self.name        = fn.__name__
        self.max_retries = max_retries
        self.backoff_s   = backoff_s
        self.timeout_s   = timeout_s
        import functools
        functools.update_wrapper(self, fn)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return await self._fn(*args, **kwargs)

    async def enqueue(
        self,
        *args: Any,
        daemon: "WorkerDaemon | None" = None,
        job_id: str | None = None,
        scheduled_for: float | None = None,
        **kwargs: Any,
    ) -> JobRecord:
        """Enqueue this task on *daemon* and return the :class:`JobRecord`."""
        target = daemon or _default_daemon
        if target is None:
            raise RuntimeError(
                "No WorkerDaemon available. Pass daemon= or call WorkerDaemon.set_default()."
            )
        return await target.enqueue(
            self.name,
            list(args),
            kwargs,
            max_retries=self.max_retries,
            job_id=job_id,
            scheduled_for=scheduled_for,
        )

    def __repr__(self) -> str:
        return f"DurableTask({self.name!r}, max_retries={self.max_retries})"


def durable_task(
    fn: Callable | None = None,
    *,
    max_retries: int = 3,
    backoff_s: float = 1.0,
    timeout_s: float | None = None,
) -> Any:
    """Decorator that marks an async function as a durable, retryable job.

    Usage::

        @durable_task
        async def process(item: str) -> str: ...

        @durable_task(max_retries=5, backoff_s=2.0, timeout_s=300)
        async def long_job(data: dict) -> dict: ...
    """
    if fn is not None:
        return DurableTask(fn, max_retries=max_retries, backoff_s=backoff_s, timeout_s=timeout_s)

    def _wrap(f: Callable) -> DurableTask:
        return DurableTask(f, max_retries=max_retries, backoff_s=backoff_s, timeout_s=timeout_s)

    return _wrap


# ── WorkerDaemon ──────────────────────────────────────────────────────────────

_default_daemon: "WorkerDaemon | None" = None


class WorkerDaemon:
    """Async worker daemon that executes durable jobs from a job store.

    Parameters
    ----------
    concurrency:
        Maximum number of jobs running simultaneously.
    store:
        Job persistence backend.  Defaults to :class:`InMemoryJobStore`.
    poll_interval_s:
        How often to check the store for new jobs (seconds).
    cloud:
        Optional :class:`~meshflow.cloud.MeshFlowCloud` for reporting job
        status to the meshflow.dev dashboard.
    """

    def __init__(
        self,
        concurrency: int = 4,
        store: InMemoryJobStore | SQLiteJobStore | None = None,
        poll_interval_s: float = 0.5,
        cloud: Any = None,
    ) -> None:
        self._concurrency    = concurrency
        self._store          = store or InMemoryJobStore()
        self._poll           = poll_interval_s
        self._cloud          = cloud
        self._registry: dict[str, DurableTask] = {}
        self._running        = False
        self._active: set[str] = set()

    def register(self, task: DurableTask) -> None:
        """Register a :class:`DurableTask` with this daemon."""
        self._registry[task.name] = task

    def set_default(self) -> "WorkerDaemon":
        """Set this daemon as the module-level default."""
        global _default_daemon
        _default_daemon = self
        return self

    async def enqueue(
        self,
        task_name: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        max_retries: int = 3,
        job_id: str | None = None,
        scheduled_for: float | None = None,
    ) -> JobRecord:
        """Add a job to the queue and return the :class:`JobRecord`."""
        job = JobRecord(
            job_id=job_id or str(uuid.uuid4()),
            task_name=task_name,
            args=args or [],
            kwargs=kwargs or {},
            max_retries=max_retries,
            scheduled_for=scheduled_for,
        )
        self._store.put(job)
        return job

    async def run(self, until_empty: bool = False) -> None:
        """Start the daemon event loop.

        Parameters
        ----------
        until_empty:
            If True, stop automatically when the queue is empty.
            Useful for tests and one-shot batch runs.
        """
        self._running = True
        sem = asyncio.Semaphore(self._concurrency)

        while self._running:
            job = self._store.next_queued()
            if job is None:
                if until_empty and not self._active:
                    break
                await asyncio.sleep(self._poll)
                continue

            # Mark as running before acquiring semaphore to avoid double-pick
            job.status    = JobStatus.RUNNING
            job.started_at = time.time()
            self._store.put(job)
            self._active.add(job.job_id)

            asyncio.create_task(self._execute(job, sem))

        self._running = False

    async def _execute(self, job: JobRecord, sem: asyncio.Semaphore) -> None:
        async with sem:
            task = self._registry.get(job.task_name)
            try:
                if task is None:
                    raise RuntimeError(f"Unknown task '{job.task_name}'")

                # Timeout wrapper
                coro = task(*job.args, **job.kwargs)
                if task.timeout_s:
                    result = await asyncio.wait_for(coro, timeout=task.timeout_s)
                else:
                    result = await coro

                job.result      = result
                job.status      = JobStatus.COMPLETED
                job.finished_at = time.time()

            except Exception as exc:
                job.error   = str(exc)
                backoff_s   = getattr(task, "backoff_s", 1.0) if task else 1.0
                if job.retries < job.max_retries:
                    job.retries += 1
                    job.status   = JobStatus.QUEUED
                    job.started_at = None
                    job.scheduled_for = time.time() + backoff_s * (2 ** (job.retries - 1))
                else:
                    job.status      = JobStatus.DEAD
                    job.finished_at = time.time()

            finally:
                self._store.put(job)
                self._active.discard(job.job_id)

                # Report to cloud dashboard
                if self._cloud is not None and job.is_terminal:
                    try:
                        self._cloud.report_worker_job(
                            job_id=job.job_id,
                            workflow_name=job.task_name,
                            status=job.status.value,
                            retries=job.retries,
                            max_retries=job.max_retries,
                            duration_ms=job.duration_ms,
                            error_msg=job.error,
                        )
                    except Exception:
                        pass

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> WorkerStats:
        return self._store.stats()


# ── CronTrigger ───────────────────────────────────────────────────────────────

@dataclass
class _CronEntry:
    name: str
    cron: str
    kwargs: dict[str, Any]
    max_retries: int


def _next_cron_run(cron_expr: str, after: float | None = None) -> float:
    """Return the next epoch timestamp for a POSIX cron expression.

    Supports the 5-field format: ``minute hour day-of-month month day-of-week``.
    Uses ``croniter`` when available; falls back to a simple +60s approximation.
    """
    base = after or time.time()
    try:
        from croniter import croniter  # type: ignore[import]
        return croniter(cron_expr, base).get_next(float)
    except ImportError:
        return base + 60.0  # no croniter — run every minute as fallback


class CronTrigger:
    """POSIX-cron scheduler that enqueues durable jobs on a schedule.

    Parameters
    ----------
    daemon:
        The :class:`WorkerDaemon` to enqueue jobs on.

    Usage::

        cron = CronTrigger(daemon)
        cron.add("daily_report", "0 8 * * 1-5", kwargs={"topic": "AI"})
        await cron.start()   # runs until cancelled
    """

    def __init__(self, daemon: WorkerDaemon) -> None:
        self._daemon  = daemon
        self._entries: list[_CronEntry] = []
        self._running = False

    def add(
        self,
        task_name: str,
        cron_expr: str,
        *,
        kwargs: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> "CronTrigger":
        self._entries.append(
            _CronEntry(name=task_name, cron=cron_expr, kwargs=kwargs or {}, max_retries=max_retries)
        )
        return self

    async def start(self) -> None:
        """Start the cron scheduler (runs indefinitely until :meth:`stop`)."""
        self._running = True
        next_runs = {e.name: _next_cron_run(e.cron) for e in self._entries}

        while self._running:
            now = time.time()
            for entry in self._entries:
                due = next_runs.get(entry.name, 0)
                if now >= due:
                    job_id = f"{entry.name}_{int(now)}"
                    await self._daemon.enqueue(
                        entry.name,
                        kwargs=entry.kwargs,
                        max_retries=entry.max_retries,
                        job_id=job_id,
                        scheduled_for=due,
                    )
                    next_runs[entry.name] = _next_cron_run(entry.cron, after=now)
            await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._running = False
