"""Sprint 47 — CronScheduler: background thread that fires due schedules.

The scheduler runs a polling loop (default: every 30 seconds) and dispatches
A2A tasks to registered agents when a schedule becomes due.

Usage::

    from meshflow.scheduler import CronScheduler, ScheduledTask

    scheduler = CronScheduler(store=ScheduleStore("/data/schedules.db"))

    task = scheduler.add(ScheduledTask(
        name="daily-report",
        agent_name="billing-agent",
        cron="0 9 * * 1-5",
        task_payload="Generate the daily billing report.",
    ))

    scheduler.start()   # background thread
    ...
    scheduler.stop()

Or use it as a context manager::

    with CronScheduler() as sched:
        sched.add(...)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .cron import CronExpression
from .store import ScheduleStore, ScheduledTask, ScheduleRun

logger = logging.getLogger(__name__)


# Dispatch callback: receives a ScheduledTask, returns an optional task_id string
DispatchFn = Callable[[ScheduledTask], Optional[str]]


def _default_dispatch(task: ScheduledTask) -> Optional[str]:
    """No-op dispatcher used when no A2A client is wired up."""
    logger.info("Cron fired: schedule=%s agent=%s payload=%r",
                task.schedule_id, task.agent_name, task.task_payload[:80])
    return None


class CronScheduler:
    """Background cron scheduler that drives :class:`ScheduleStore`.

    Parameters
    ----------
    store:    Backing store (defaults to in-memory).
    dispatch: Callable that receives a due :class:`ScheduledTask` and returns
              an optional task_id. Defaults to a logger-only no-op.
    poll_s:   Polling interval in seconds (default 30).
    """

    def __init__(
        self,
        store: Optional[ScheduleStore] = None,
        dispatch: Optional[DispatchFn] = None,
        poll_s: float = 30.0,
    ) -> None:
        self._store    = store or ScheduleStore()
        self._dispatch = dispatch or _default_dispatch
        self._poll_s   = poll_s
        self._thread:  Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

    # ── Schedule management ───────────────────────────────────────────────────

    def add(self, task: ScheduledTask) -> ScheduledTask:
        """Register a schedule and compute its first ``next_fire_at``."""
        expr = CronExpression(task.cron)
        task.next_fire_at = expr.next_after(time.time())
        return self._store.add(task)

    def remove(self, schedule_id: str) -> bool:
        return self._store.delete(schedule_id)

    def enable(self, schedule_id: str, enabled: bool = True) -> bool:
        return self._store.enable(schedule_id, enabled)

    def list(self, agent_name: str = "", enabled_only: bool = False) -> list[ScheduledTask]:
        return self._store.list(agent_name=agent_name, enabled_only=enabled_only)

    def get(self, schedule_id: str) -> Optional[ScheduledTask]:
        return self._store.get(schedule_id)

    # ── Background loop ───────────────────────────────────────────────────────

    def _tick(self, now: Optional[float] = None) -> list[ScheduleRun]:
        """Process all due schedules. Returns list of ScheduleRuns created."""
        ts  = now if now is not None else time.time()
        due = self._store.due(ts)
        runs: list[ScheduleRun] = []
        for task in due:
            try:
                task_id = self._dispatch(task)
            except Exception as exc:
                logger.error("Dispatch error for schedule %s: %s", task.schedule_id, exc)
                task_id = None
            try:
                expr      = CronExpression(task.cron)
                next_fire = expr.next_after(ts)
                run       = self._store.record_fire(task.schedule_id, next_fire, task_id or "")
                runs.append(run)
            except Exception as exc:
                logger.error("Failed to record fire for schedule %s: %s", task.schedule_id, exc)
        return runs

    def _run(self) -> None:
        logger.info("CronScheduler started (poll_s=%s)", self._poll_s)
        while not self._stop_evt.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.error("CronScheduler tick error: %s", exc)
            self._stop_evt.wait(self._poll_s)
        logger.info("CronScheduler stopped")

    def start(self) -> None:
        """Start the background polling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="meshflow-scheduler")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the polling thread to stop and wait for it."""
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "CronScheduler":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
