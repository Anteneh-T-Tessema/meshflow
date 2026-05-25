"""Sprint 48 — SQLite-backed policy store for rate limit CLI persistence.

This is a thin persistence layer on top of RateLimitStore.  The in-memory
RateLimitStore holds the active sliding windows; this layer saves and loads
policy definitions to/from SQLite so CLI changes survive restarts.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .window import RateLimitPolicy


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_limit_policies (
    key          TEXT PRIMARY KEY,
    max_requests INTEGER NOT NULL DEFAULT 0,
    max_tokens   INTEGER NOT NULL DEFAULT 0,
    window_s     REAL    NOT NULL DEFAULT 60.0,
    warn_at      REAL    NOT NULL DEFAULT 0.80
);
"""


class RateLimitPolicyDB:
    """SQLite-backed persistence for :class:`RateLimitPolicy` definitions."""

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

    def save(self, key: str, policy: RateLimitPolicy) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO rate_limit_policies"
            " (key, max_requests, max_tokens, window_s, warn_at) VALUES (?,?,?,?,?)",
            (key, policy.max_requests, policy.max_tokens, policy.window_s, policy.warn_at),
        )
        conn.commit()

    def load(self, key: str) -> Optional[RateLimitPolicy]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM rate_limit_policies WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        return RateLimitPolicy(
            max_requests=row["max_requests"],
            max_tokens=row["max_tokens"],
            window_s=row["window_s"],
            warn_at=row["warn_at"],
        )

    def delete(self, key: str) -> bool:
        conn = self._connect()
        cur = conn.execute("DELETE FROM rate_limit_policies WHERE key = ?", (key,))
        conn.commit()
        return cur.rowcount > 0

    def list(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM rate_limit_policies ORDER BY key"
        ).fetchall()
        return [
            {
                "key": r["key"],
                "max_requests": r["max_requests"],
                "max_tokens": r["max_tokens"],
                "window_s": r["window_s"],
                "warn_at": r["warn_at"],
            }
            for r in rows
        ]

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM rate_limit_policies").fetchone()[0]
