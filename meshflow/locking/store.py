"""Sprint 55 — SQLite-backed distributed lock store."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional


_DDL = """
CREATE TABLE IF NOT EXISTS distributed_locks (
    resource_id  TEXT    PRIMARY KEY,
    lock_id      TEXT    NOT NULL,
    owner        TEXT    NOT NULL,
    acquired_at  REAL    NOT NULL,
    expires_at   REAL    NOT NULL,
    ttl_s        REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dl_expires
    ON distributed_locks(expires_at);
"""


@dataclass
class LockRecord:
    resource_id: str
    lock_id:     str
    owner:       str
    acquired_at: float
    expires_at:  float
    ttl_s:       float

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "lock_id":     self.lock_id,
            "owner":       self.owner,
            "acquired_at": self.acquired_at,
            "expires_at":  self.expires_at,
            "ttl_s":       self.ttl_s,
            "remaining_s": self.remaining_s,
        }


class LockStore:
    """SQLite-backed lock store with TTL-based expiry.

    All operations treat expired locks as absent — they are silently cleared
    before each read/write so the store never returns stale entries.
    """

    def __init__(self, db_path: str = "meshflow_locks.db") -> None:
        self._db_path = db_path
        self._mutex   = threading.Lock()
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    def _purge_expired(self, con: sqlite3.Connection, now: float) -> None:
        con.execute("DELETE FROM distributed_locks WHERE expires_at <= ?", (now,))

    # ── Acquire ────────────────────────────────────────────────────────────────

    def try_acquire(
        self,
        resource_id: str,
        owner: str,
        ttl_s: float = 30.0,
        now: Optional[float] = None,
    ) -> Optional[LockRecord]:
        """Try to acquire the lock.  Returns the record on success, None if held."""
        ts = now if now is not None else time.time()
        with self._mutex:
            con = self._conn()
            # Only expire the target resource — avoid side-effecting unrelated locks
            con.execute(
                "DELETE FROM distributed_locks WHERE resource_id=? AND expires_at <= ?",
                (resource_id, ts),
            )
            existing = con.execute(
                "SELECT * FROM distributed_locks WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if existing is not None:
                return None  # lock is held (and not expired)

            record = LockRecord(
                resource_id=resource_id,
                lock_id=str(uuid.uuid4()),
                owner=owner,
                acquired_at=ts,
                expires_at=ts + ttl_s,
                ttl_s=ttl_s,
            )
            try:
                con.execute(
                    """
                    INSERT INTO distributed_locks
                        (resource_id, lock_id, owner, acquired_at, expires_at, ttl_s)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (record.resource_id, record.lock_id, record.owner,
                     record.acquired_at, record.expires_at, record.ttl_s),
                )
                con.commit()
            except sqlite3.IntegrityError:
                return None
            return record

    # ── Release ────────────────────────────────────────────────────────────────

    def release(self, resource_id: str, owner: str) -> bool:
        """Release a lock only if the caller is the owner."""
        con = self._conn()
        cur = con.execute(
            "DELETE FROM distributed_locks WHERE resource_id=? AND owner=?",
            (resource_id, owner),
        )
        con.commit()
        return cur.rowcount > 0

    def force_release(self, resource_id: str) -> bool:
        """Release a lock regardless of owner (admin operation)."""
        con = self._conn()
        cur = con.execute(
            "DELETE FROM distributed_locks WHERE resource_id=?", (resource_id,)
        )
        con.commit()
        return cur.rowcount > 0

    # ── Extend ─────────────────────────────────────────────────────────────────

    def extend(
        self,
        resource_id: str,
        owner: str,
        additional_s: float,
        now: Optional[float] = None,
    ) -> bool:
        """Extend the TTL of an owned lock.  Returns True if successful."""
        ts = now if now is not None else time.time()
        con = self._conn()
        cur = con.execute(
            """
            UPDATE distributed_locks
            SET expires_at = expires_at + ?,
                ttl_s      = ttl_s + ?
            WHERE resource_id=? AND owner=? AND expires_at > ?
            """,
            (additional_s, additional_s, resource_id, owner, ts),
        )
        con.commit()
        return cur.rowcount > 0

    # ── Query ──────────────────────────────────────────────────────────────────

    def get(self, resource_id: str, now: Optional[float] = None) -> Optional[LockRecord]:
        ts = now if now is not None else time.time()
        con = self._conn()
        self._purge_expired(con, ts)
        con.commit()
        row = con.execute(
            "SELECT * FROM distributed_locks WHERE resource_id=?", (resource_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_locks(
        self,
        active_only: bool = True,
        now: Optional[float] = None,
    ) -> list[LockRecord]:
        ts = now if now is not None else time.time()
        con = self._conn()
        if active_only:
            self._purge_expired(con, ts)
            con.commit()
        rows = con.execute(
            "SELECT * FROM distributed_locks ORDER BY acquired_at ASC"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def is_locked(self, resource_id: str, now: Optional[float] = None) -> bool:
        return self.get(resource_id, now=now) is not None

    def count(self, active_only: bool = True, now: Optional[float] = None) -> int:
        return len(self.list_locks(active_only=active_only, now=now))

    def purge_expired(self, now: Optional[float] = None) -> int:
        ts = now if now is not None else time.time()
        con = self._conn()
        cur = con.execute("DELETE FROM distributed_locks WHERE expires_at <= ?", (ts,))
        con.commit()
        return cur.rowcount

    @staticmethod
    def _from_row(row: sqlite3.Row) -> LockRecord:
        d = dict(row)
        return LockRecord(
            resource_id=d["resource_id"],
            lock_id=d["lock_id"],
            owner=d["owner"],
            acquired_at=d["acquired_at"],
            expires_at=d["expires_at"],
            ttl_s=d["ttl_s"],
        )
