"""SQLite-backed persistence for circuit breaker state.

Allows circuit breaker state to survive server restarts.  The store persists
the high-level state (OPEN/CLOSED/HALF_OPEN), opened_at timestamp, and
lifetime counters.  The in-memory sliding-window failure deque is always
re-initialised fresh (its entries are ephemeral within a single process).

Supports the standard ``:memory:`` connection-caching pattern used throughout
this codebase.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from .breaker import CircuitBreakerState


@dataclass
class CircuitBreakerRecord:
    name:             str
    state:            CircuitBreakerState
    opened_at:        Optional[float]
    total_calls:      int
    total_failures:   int
    total_successes:  int
    total_rejected:   int
    updated_at:       float


_DDL = """
CREATE TABLE IF NOT EXISTS circuit_breakers (
    name            TEXT PRIMARY KEY,
    state           TEXT NOT NULL DEFAULT 'closed',
    opened_at       REAL,
    total_calls     INTEGER NOT NULL DEFAULT 0,
    total_failures  INTEGER NOT NULL DEFAULT 0,
    total_successes INTEGER NOT NULL DEFAULT 0,
    total_rejected  INTEGER NOT NULL DEFAULT 0,
    updated_at      REAL NOT NULL
);
"""


class CircuitBreakerStore:
    """Persist circuit breaker snapshots to SQLite.

    Parameters
    ----------
    db_path:
        Filesystem path or ``":memory:"`` for an in-process store.
    """

    def __init__(self, db_path: str = "meshflow_circuits.db") -> None:
        self._db_path = db_path
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None
        self._ensure_schema()

    # ── Connection ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.execute(_DDL)
        con.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def save(self, record: CircuitBreakerRecord) -> None:
        con = self._conn()
        con.execute(
            """
            INSERT INTO circuit_breakers
                (name, state, opened_at, total_calls, total_failures,
                 total_successes, total_rejected, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                state           = excluded.state,
                opened_at       = excluded.opened_at,
                total_calls     = excluded.total_calls,
                total_failures  = excluded.total_failures,
                total_successes = excluded.total_successes,
                total_rejected  = excluded.total_rejected,
                updated_at      = excluded.updated_at
            """,
            (
                record.name,
                record.state.value,
                record.opened_at,
                record.total_calls,
                record.total_failures,
                record.total_successes,
                record.total_rejected,
                record.updated_at,
            ),
        )
        con.commit()

    def load(self, name: str) -> Optional[CircuitBreakerRecord]:
        con = self._conn()
        row = con.execute(
            "SELECT * FROM circuit_breakers WHERE name = ?", (name,)
        ).fetchone()
        return self._from_row(dict(row)) if row else None

    def list(self) -> list[CircuitBreakerRecord]:
        con = self._conn()
        rows = con.execute(
            "SELECT * FROM circuit_breakers ORDER BY name"
        ).fetchall()
        return [self._from_row(dict(r)) for r in rows]

    def delete(self, name: str) -> bool:
        con = self._conn()
        cur = con.execute("DELETE FROM circuit_breakers WHERE name = ?", (name,))
        con.commit()
        return cur.rowcount > 0

    def delete_all(self) -> int:
        con = self._conn()
        cur = con.execute("DELETE FROM circuit_breakers")
        con.commit()
        return cur.rowcount

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _from_row(d: dict) -> CircuitBreakerRecord:
        return CircuitBreakerRecord(
            name=d["name"],
            state=CircuitBreakerState(d["state"]),
            opened_at=d["opened_at"],
            total_calls=d["total_calls"],
            total_failures=d["total_failures"],
            total_successes=d["total_successes"],
            total_rejected=d["total_rejected"],
            updated_at=d["updated_at"],
        )
