"""Durable atomic request reservations shared by processes on one SQLite host."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
import sqlite3
from typing import Any, Iterator
import uuid


_SCALE = 1_000_000_000


def _money(value: float, *, limit: bool = False) -> int:
    if not math.isfinite(value) or value < 0:
        raise ValueError("Budget amounts must be finite and non-negative")
    return int(
        (Decimal(str(value)) * _SCALE).to_integral_value(
            rounding=ROUND_FLOOR if limit else ROUND_CEILING
        )
    )


class ReservationStore:
    """One immutable cap with durable holds and idempotent settlement.

    All workers sharing a scope must use the same limits. Holds never expire
    automatically: a crashed worker may have dispatched a billable request.
    """

    def __init__(self, path: str, scope: str, limit_usd: float, limit_tokens: int) -> None:
        if not scope or not path or path == ":memory:":
            raise ValueError("Shared reservations require a file path and explicit scope")
        if not isinstance(limit_tokens, int) or limit_tokens < 0:
            raise ValueError("Token limits must be non-negative integers")
        self.path, self.scope = path, scope
        money = _money(limit_usd, limit=True)
        with self._transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS reservation_accounts (
                scope TEXT PRIMARY KEY, limit_nusd INTEGER NOT NULL, limit_tokens INTEGER NOT NULL,
                spent_nusd INTEGER NOT NULL DEFAULT 0, spent_tokens INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS request_reservations (
                reservation_id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                held_nusd INTEGER NOT NULL, held_tokens INTEGER NOT NULL,
                status TEXT NOT NULL, actual_nusd INTEGER, actual_tokens INTEGER)""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS reservations_scope_status ON request_reservations(scope,status)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO reservation_accounts(scope,limit_nusd,limit_tokens) VALUES(?,?,?)",
                (scope, money, limit_tokens),
            )
            row = conn.execute(
                "SELECT limit_nusd,limit_tokens FROM reservation_accounts WHERE scope=?", (scope,)
            ).fetchone()
            if tuple(row) != (money, limit_tokens):
                raise ValueError("Shared budget scope already exists with different limits")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reserve(self, usd: float, tokens: int) -> str:
        from meshflow.core.policy import BudgetExceededError

        money = _money(usd)
        if not isinstance(tokens, int) or tokens < 0:
            raise ValueError("Token reservations must be non-negative integers")
        key = str(uuid.uuid4())
        with self._transaction() as conn:
            account = conn.execute(
                "SELECT limit_nusd,limit_tokens,spent_nusd,spent_tokens FROM reservation_accounts WHERE scope=?",
                (self.scope,),
            ).fetchone()
            held = conn.execute(
                "SELECT COALESCE(SUM(held_nusd),0),COALESCE(SUM(held_tokens),0) FROM request_reservations WHERE scope=? AND status='pending'",
                (self.scope,),
            ).fetchone()
            if (
                account[2] + held[0] + money > account[0]
                or account[3] + held[1] + tokens > account[1]
            ):
                raise BudgetExceededError("Shared budget has insufficient unreserved capacity")
            conn.execute(
                "INSERT INTO request_reservations VALUES(?,?,?,?, 'pending',NULL,NULL)",
                (key, self.scope, money, tokens),
            )
        return key

    def settle(self, key: str, usd: float | None = None, tokens: int | None = None) -> None:
        from meshflow.core.policy import BudgetExceededError

        invalid = False
        exceeded = False
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT held_nusd,held_tokens,status,actual_nusd,actual_tokens FROM request_reservations WHERE reservation_id=? AND scope=?",
                (key, self.scope),
            ).fetchone()
            if row is None:
                raise KeyError(key)
            try:
                money = row[0] if usd is None else _money(usd)
                actual_tokens = row[1] if tokens is None else tokens
                if not isinstance(actual_tokens, int) or actual_tokens < 0:
                    raise ValueError("Invalid token usage")
            except (ValueError, TypeError):
                money, actual_tokens, invalid = row[0], row[1], True
            if row[2] == "settled":
                if (row[3], row[4]) != (money, actual_tokens):
                    raise ValueError("Reservation already settled with different usage")
                return
            if row[2] != "pending":
                raise ValueError("Cannot settle a released reservation")
            conn.execute(
                "UPDATE reservation_accounts SET spent_nusd=spent_nusd+?,spent_tokens=spent_tokens+? WHERE scope=?",
                (money, actual_tokens, self.scope),
            )
            conn.execute(
                "UPDATE request_reservations SET status='settled',actual_nusd=?,actual_tokens=? WHERE reservation_id=?",
                (money, actual_tokens, key),
            )
            exceeded = money > row[0] or actual_tokens > row[1]
        if invalid:
            raise ValueError("Invalid usage; full reservation retained as spend")
        if exceeded:
            raise BudgetExceededError("Provider exceeded its shared request bound")

    def release(self, key: str) -> None:
        """Release only after confirming that no billable dispatch occurred."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT status FROM request_reservations WHERE reservation_id=? AND scope=?",
                (key, self.scope),
            ).fetchone()
            if row is None:
                raise KeyError(key)
            if row[0] == "settled":
                raise ValueError("Cannot release settled spend")
            conn.execute(
                "UPDATE request_reservations SET status='released' WHERE reservation_id=?", (key,)
            )

    def pending(self) -> list[dict[str, Any]]:
        """List holds for explicit reconciliation; never release them on a timer."""
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT reservation_id,held_nusd,held_tokens FROM request_reservations WHERE scope=? AND status='pending' ORDER BY rowid",
                (self.scope,),
            ).fetchall()
        return [
            {"reservation_id": row[0], "reserved_usd": row[1] / _SCALE, "reserved_tokens": row[2]}
            for row in rows
        ]

    def summary(self) -> dict[str, Any]:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT limit_nusd,limit_tokens,spent_nusd,spent_tokens FROM reservation_accounts WHERE scope=?",
                (self.scope,),
            ).fetchone()
            held = conn.execute(
                "SELECT COALESCE(SUM(held_nusd),0),COALESCE(SUM(held_tokens),0),COUNT(*) FROM request_reservations WHERE scope=? AND status='pending'",
                (self.scope,),
            ).fetchone()
        return dict(
            scope=self.scope,
            limit_usd=row[0] / _SCALE,
            limit_tokens=row[1],
            spent_usd=row[2] / _SCALE,
            spent_tokens=row[3],
            reserved_usd=held[0] / _SCALE,
            reserved_tokens=held[1],
            pending=held[2],
        )
