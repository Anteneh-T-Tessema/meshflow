"""Durable Workers — long-running, retry-safe agent job execution.

Provides:

* :func:`durable_task` — decorator that makes any async function a retryable,
  crash-safe unit of work.
* :class:`WorkerDaemon` — asyncio-based worker that pulls jobs from a queue
  and executes them with automatic retry and exponential back-off.
* :class:`CronTrigger` — POSIX-cron scheduler that enqueues jobs on a schedule.
* :class:`JobRecord` — lightweight job state object (status, retries, result).

Usage::

    from meshflow.workers import durable_task, WorkerDaemon, CronTrigger

    @durable_task(max_retries=3, backoff_s=2.0)
    async def daily_report(topic: str) -> str:
        return await research_workflow.run(topic)

    # Start the daemon (blocks until Ctrl-C)
    daemon = WorkerDaemon(concurrency=4)
    daemon.register(daily_report)
    await daemon.run()

    # Schedule via cron
    cron = CronTrigger(daemon)
    cron.add("daily_report", "0 9 * * 1-5", kwargs={"topic": "AI governance"})
    await cron.start()
"""

from meshflow.workers.core import (
    durable_task,
    DurableTask,
    WorkerDaemon,
    CronTrigger,
    JobRecord,
    JobStatus,
    WorkerStats,
    InMemoryJobStore,
    SQLiteJobStore,
)

__all__ = [
    "durable_task",
    "DurableTask",
    "WorkerDaemon",
    "CronTrigger",
    "JobRecord",
    "JobStatus",
    "WorkerStats",
    "InMemoryJobStore",
    "SQLiteJobStore",
]
