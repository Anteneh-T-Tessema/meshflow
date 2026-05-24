"""Sprint 47 — SQLite-backed schedule store for cron-triggered agent tasks."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    """A persistent cron-triggered task definition."""

    schedule_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    agent_name: str = ""
    cron: str = "* * * * *"
    task_payload: str = ""          # content sent to the agent
    enabled: bool = True
    last_fired_at: float = 0.0
    next_fire_at: float = 0.0
    fire_count: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metadata"] = json.dumps(self.metadata)
        return d

    @staticmethod
    def from_row(row: sqlite3.Row) -> "ScheduledTask":
        d = dict(row)
        meta_raw = d.pop("metadata", "{}")
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except (ValueError, TypeError):
            meta = {}
        # SQLite stores booleans as integers
        if "enabled" in d:
            d["enabled"] = bool(d["enabled"])
        return ScheduledTask(**{k: v for k, v in d.items() if k != "metadata"}, metadata=meta)


@dataclass
class ScheduleRun:
    """Record of one cron-triggered execution."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    schedule_id: str = ""
    fired_at: float = field(default_factory=time.time)
    task_id: str = ""           # A2ATask id returned by the agent server (if any)
    status: str = "pending"     # pending | dispatched | failed
    error: str = ""


# ── Store ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    agent_name    TEXT NOT NULL,
    cron          TEXT NOT NULL,
    task_payload  TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_fired_at REAL NOT NULL DEFAULT 0,
    next_fire_at  REAL NOT NULL DEFAULT 0,
    fire_count    INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    metadata      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    run_id       TEXT PRIMARY KEY,
    schedule_id  TEXT NOT NULL,
    fired_at     REAL NOT NULL,
    task_id      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    error        TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id)
);
"""


class ScheduleStore:
    """SQLite-backed store for :class:`ScheduledTask` and :class:`ScheduleRun`."""

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        if path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        else:
            self._conn = None  # type: ignore[assignment]

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            return self._conn
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn

    # ── Schedule CRUD ─────────────────────────────────────────────────────────

    def add(self, task: ScheduledTask) -> ScheduledTask:
        conn = self._connect()
        row = task.to_dict()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        conn.execute(
            f"INSERT INTO schedules ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        conn.commit()
        return task

    def get(self, schedule_id: str) -> Optional[ScheduledTask]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM schedules WHERE schedule_id = ?", (schedule_id,)
        ).fetchone()
        return ScheduledTask.from_row(row) if row else None

    def delete(self, schedule_id: str) -> bool:
        conn = self._connect()
        cur = conn.execute("DELETE FROM schedules WHERE schedule_id = ?", (schedule_id,))
        conn.commit()
        return cur.rowcount > 0

    def enable(self, schedule_id: str, enabled: bool = True) -> bool:
        conn = self._connect()
        cur = conn.execute(
            "UPDATE schedules SET enabled = ? WHERE schedule_id = ?",
            (1 if enabled else 0, schedule_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def list(
        self,
        agent_name: str = "",
        enabled_only: bool = False,
        limit: int = 500,
    ) -> list[ScheduledTask]:
        conn = self._connect()
        clauses, params = [], []
        if agent_name:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM schedules {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [ScheduledTask.from_row(r) for r in rows]

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]

    # ── Firing state ──────────────────────────────────────────────────────────

    def record_fire(
        self, schedule_id: str, next_fire_at: float, task_id: str = ""
    ) -> ScheduleRun:
        conn = self._connect()
        now = time.time()
        run = ScheduleRun(
            schedule_id=schedule_id,
            fired_at=now,
            task_id=task_id,
            status="dispatched" if task_id else "pending",
        )
        conn.execute(
            "INSERT INTO schedule_runs (run_id, schedule_id, fired_at, task_id, status, error)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run.run_id, run.schedule_id, run.fired_at, run.task_id, run.status, run.error),
        )
        conn.execute(
            "UPDATE schedules SET last_fired_at = ?, next_fire_at = ?, fire_count = fire_count + 1"
            " WHERE schedule_id = ?",
            (now, next_fire_at, schedule_id),
        )
        conn.commit()
        return run

    def due(self, now: Optional[float] = None) -> list[ScheduledTask]:
        """Return all enabled schedules whose ``next_fire_at`` is <= *now*."""
        ts = now if now is not None else time.time()
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM schedules WHERE enabled = 1 AND next_fire_at <= ? AND next_fire_at > 0",
            (ts,),
        ).fetchall()
        return [ScheduledTask.from_row(r) for r in rows]

    def runs(self, schedule_id: str, limit: int = 50) -> list[ScheduleRun]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY fired_at DESC LIMIT ?",
            (schedule_id, limit),
        ).fetchall()
        return [
            ScheduleRun(
                run_id=r["run_id"],
                schedule_id=r["schedule_id"],
                fired_at=r["fired_at"],
                task_id=r["task_id"],
                status=r["status"],
                error=r["error"],
            )
            for r in rows
        ]
