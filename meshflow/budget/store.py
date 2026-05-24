"""Sprint 45 — Cost budgets and quota enforcement.

BudgetStore: SQLite-backed cumulative spend tracking with period windows.
BudgetAccount: hard-cap definition (per-agent / per-team, daily/weekly/monthly/total).
BudgetCheckResult: pre-flight gate result returned before each agent call.

Usage::

    from meshflow.budget.store import BudgetAccount, BudgetStore

    store = BudgetStore(":memory:")

    # Define a $5/day budget for the billing agent
    store.create(BudgetAccount(
        account_id="billing-daily",
        agent_name="billing-agent",
        period="daily",
        limit_usd=5.00,
    ))

    # Pre-flight: can billing-agent make another call?
    result = store.check("billing-daily")
    if not result.allowed:
        raise RuntimeError(result.reason)

    # Post-call: debit actual spend
    store.record_spend("billing-daily", cost_usd=0.12, tokens=1_200)

    # Inspect
    print(store.summary("billing-daily"))
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Period helpers ─────────────────────────────────────────────────────────────

VALID_PERIODS = ("daily", "weekly", "monthly", "total")


def period_key(period: str, ts: float | None = None) -> str:
    """Return the current window key for *period*.

    daily   → "2026-05-24"
    weekly  → "2026-W21"
    monthly → "2026-05"
    total   → "all"
    """
    if period == "total":
        return "all"
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    if period == "weekly":
        return dt.strftime("%Y-W%W")
    if period == "monthly":
        return dt.strftime("%Y-%m")
    return "all"


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class BudgetAccount:
    """A budget cap applied to an agent or team over a time window.

    Attributes
    ----------
    account_id:   Unique identifier (auto-generated if empty).
    name:         Human-readable label.
    agent_name:   Agent this cap applies to.  Empty = team-level.
    team:         Team this cap applies to.  Empty = agent-level only.
    period:       ``"daily"`` | ``"weekly"`` | ``"monthly"`` | ``"total"``.
    limit_usd:    Hard cap in USD (0 = no USD cap).
    limit_tokens: Hard cap in tokens (0 = no token cap).
    created_at:   Unix timestamp (auto-set).
    """

    account_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    agent_name: str = ""
    team: str = ""
    period: str = "daily"
    limit_usd: float = 0.0
    limit_tokens: int = 0
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.period not in VALID_PERIODS:
            raise ValueError(f"period must be one of {VALID_PERIODS}, got {self.period!r}")
        if not self.name:
            self.name = self.account_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":   self.account_id,
            "name":         self.name,
            "agent_name":   self.agent_name,
            "team":         self.team,
            "period":       self.period,
            "limit_usd":    self.limit_usd,
            "limit_tokens": self.limit_tokens,
            "created_at":   self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BudgetAccount":
        return cls(
            account_id=d.get("account_id", ""),
            name=d.get("name", ""),
            agent_name=d.get("agent_name", ""),
            team=d.get("team", ""),
            period=d.get("period", "daily"),
            limit_usd=d.get("limit_usd", 0.0),
            limit_tokens=d.get("limit_tokens", 0),
            created_at=d.get("created_at", time.time()),
        )


@dataclass
class BudgetSpend:
    """Accumulated spend for one account in one period window."""

    account_id: str
    period_key: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    call_count: int = 0
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":  self.account_id,
            "period_key":  self.period_key,
            "tokens_used": self.tokens_used,
            "cost_usd":    round(self.cost_usd, 8),
            "call_count":  self.call_count,
            "updated_at":  self.updated_at,
        }


@dataclass
class BudgetCheckResult:
    """Result of a pre-flight budget gate.

    Attributes
    ----------
    allowed:          True if the call may proceed.
    account_id:       Which account was checked.
    period_key:       The current window key.
    spent_usd:        USD consumed so far this period.
    remaining_usd:    USD remaining (None if no USD cap).
    spent_tokens:     Tokens consumed so far this period.
    remaining_tokens: Tokens remaining (None if no token cap).
    percent_used:     Fraction of the most-constrained cap used (0–1).
    reason:           Why the call was blocked (empty if allowed).
    """

    allowed: bool
    account_id: str
    period_key: str
    spent_usd: float = 0.0
    remaining_usd: float | None = None
    spent_tokens: int = 0
    remaining_tokens: int | None = None
    percent_used: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed":           self.allowed,
            "account_id":        self.account_id,
            "period_key":        self.period_key,
            "spent_usd":         round(self.spent_usd, 8),
            "remaining_usd":     round(self.remaining_usd, 8) if self.remaining_usd is not None else None,
            "spent_tokens":      self.spent_tokens,
            "remaining_tokens":  self.remaining_tokens,
            "percent_used":      round(self.percent_used, 4),
            "reason":            self.reason,
        }


# ── BudgetStore ────────────────────────────────────────────────────────────────

class BudgetStore:
    """SQLite-backed budget store.

    Parameters
    ----------
    path:  SQLite file path.  Use ``":memory:"`` for in-process (tests).

    Schema
    ------
    accounts(account_id PK, data JSON, agent_name, team, period, limit_usd,
             limit_tokens, created_at)
    spend(account_id, period_key, tokens_used, cost_usd, call_count, updated_at)
         PRIMARY KEY (account_id, period_key)
    """

    def __init__(self, path: str = "meshflow_budgets.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._conn
        return sqlite3.connect(self._path, check_same_thread=False)

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id   TEXT PRIMARY KEY,
                data         TEXT NOT NULL,
                agent_name   TEXT NOT NULL DEFAULT '',
                team         TEXT NOT NULL DEFAULT '',
                period       TEXT NOT NULL DEFAULT 'daily',
                limit_usd    REAL NOT NULL DEFAULT 0,
                limit_tokens INTEGER NOT NULL DEFAULT 0,
                created_at   REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS spend (
                account_id   TEXT NOT NULL,
                period_key   TEXT NOT NULL,
                tokens_used  INTEGER NOT NULL DEFAULT 0,
                cost_usd     REAL NOT NULL DEFAULT 0,
                call_count   INTEGER NOT NULL DEFAULT 0,
                updated_at   REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (account_id, period_key)
            );
        """)
        conn.commit()

    # ── Account CRUD ───────────────────────────────────────────────────────────

    def create(self, account: BudgetAccount) -> None:
        """Persist a budget account.  Overwrites if account_id already exists."""
        conn = self._connect()
        conn.execute(
            """INSERT INTO accounts
               (account_id, data, agent_name, team, period, limit_usd, limit_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                   data=excluded.data,
                   agent_name=excluded.agent_name,
                   team=excluded.team,
                   period=excluded.period,
                   limit_usd=excluded.limit_usd,
                   limit_tokens=excluded.limit_tokens""",
            (
                account.account_id,
                json.dumps(account.to_dict()),
                account.agent_name,
                account.team,
                account.period,
                account.limit_usd,
                account.limit_tokens,
                account.created_at,
            ),
        )
        conn.commit()

    def get(self, account_id: str) -> BudgetAccount | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT data FROM accounts WHERE account_id=?", (account_id,)
        ).fetchone()
        if row is None:
            return None
        return BudgetAccount.from_dict(json.loads(row[0]))

    def delete(self, account_id: str) -> bool:
        conn = self._connect()
        cur = conn.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM spend WHERE account_id=?", (account_id,))
        conn.commit()
        return cur.rowcount > 0

    def list(
        self,
        *,
        agent_name: str = "",
        team: str = "",
        period: str = "",
    ) -> list[BudgetAccount]:
        conn = self._connect()
        sql = "SELECT data FROM accounts WHERE 1=1"
        params: list[Any] = []
        if agent_name:
            sql += " AND agent_name=?"
            params.append(agent_name)
        if team:
            sql += " AND team=?"
            params.append(team)
        if period:
            sql += " AND period=?"
            params.append(period)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [BudgetAccount.from_dict(json.loads(r[0])) for r in rows]

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]

    # ── Spend tracking ─────────────────────────────────────────────────────────

    def record_spend(
        self,
        account_id: str,
        *,
        cost_usd: float = 0.0,
        tokens: int = 0,
    ) -> BudgetSpend:
        """Debit *cost_usd* and *tokens* from *account_id* in the current window."""
        account = self.get(account_id)
        if account is None:
            raise KeyError(f"Budget account {account_id!r} not found")
        pk = period_key(account.period)
        now = time.time()
        conn = self._connect()
        conn.execute(
            """INSERT INTO spend (account_id, period_key, tokens_used, cost_usd, call_count, updated_at)
               VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT(account_id, period_key) DO UPDATE SET
                   tokens_used = tokens_used + excluded.tokens_used,
                   cost_usd    = cost_usd    + excluded.cost_usd,
                   call_count  = call_count  + 1,
                   updated_at  = excluded.updated_at""",
            (account_id, pk, tokens, cost_usd, now),
        )
        conn.commit()
        return self.get_spend(account_id, pk) or BudgetSpend(account_id=account_id, period_key=pk)

    def get_spend(self, account_id: str, pk: str) -> BudgetSpend | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT tokens_used, cost_usd, call_count, updated_at FROM spend WHERE account_id=? AND period_key=?",
            (account_id, pk),
        ).fetchone()
        if row is None:
            return None
        return BudgetSpend(
            account_id=account_id,
            period_key=pk,
            tokens_used=row[0],
            cost_usd=row[1],
            call_count=row[2],
            updated_at=row[3],
        )

    def current_spend(self, account_id: str) -> BudgetSpend:
        """Return spend for the current window (zero if none recorded yet)."""
        account = self.get(account_id)
        if account is None:
            raise KeyError(f"Budget account {account_id!r} not found")
        pk = period_key(account.period)
        return self.get_spend(account_id, pk) or BudgetSpend(account_id=account_id, period_key=pk)

    def reset_spend(self, account_id: str, pk: str | None = None) -> None:
        """Zero out spend for *account_id* (current window if pk omitted)."""
        conn = self._connect()
        if pk is None:
            account = self.get(account_id)
            if account is None:
                return
            pk = period_key(account.period)
        conn.execute(
            "DELETE FROM spend WHERE account_id=? AND period_key=?",
            (account_id, pk),
        )
        conn.commit()

    # ── Budget gate ────────────────────────────────────────────────────────────

    def check(self, account_id: str) -> BudgetCheckResult:
        """Pre-flight gate: is *account_id* within budget for the current window?"""
        account = self.get(account_id)
        if account is None:
            return BudgetCheckResult(
                allowed=False,
                account_id=account_id,
                period_key="",
                reason=f"Budget account {account_id!r} not found",
            )
        pk = period_key(account.period)
        spend = self.get_spend(account_id, pk) or BudgetSpend(account_id=account_id, period_key=pk)

        remaining_usd: float | None = None
        remaining_tokens: int | None = None
        percent_used = 0.0

        if account.limit_usd > 0:
            remaining_usd = max(0.0, account.limit_usd - spend.cost_usd)
            pct = spend.cost_usd / account.limit_usd
            percent_used = max(percent_used, pct)
            if spend.cost_usd >= account.limit_usd:
                return BudgetCheckResult(
                    allowed=False,
                    account_id=account_id,
                    period_key=pk,
                    spent_usd=spend.cost_usd,
                    remaining_usd=0.0,
                    spent_tokens=spend.tokens_used,
                    remaining_tokens=remaining_tokens,
                    percent_used=min(pct, 1.0),
                    reason=(
                        f"USD budget exhausted: spent ${spend.cost_usd:.4f} "
                        f"of ${account.limit_usd:.4f} ({account.period})"
                    ),
                )

        if account.limit_tokens > 0:
            remaining_tokens = max(0, account.limit_tokens - spend.tokens_used)
            pct = spend.tokens_used / account.limit_tokens
            percent_used = max(percent_used, pct)
            if spend.tokens_used >= account.limit_tokens:
                return BudgetCheckResult(
                    allowed=False,
                    account_id=account_id,
                    period_key=pk,
                    spent_usd=spend.cost_usd,
                    remaining_usd=remaining_usd,
                    spent_tokens=spend.tokens_used,
                    remaining_tokens=0,
                    percent_used=min(pct, 1.0),
                    reason=(
                        f"Token budget exhausted: used {spend.tokens_used:,} "
                        f"of {account.limit_tokens:,} ({account.period})"
                    ),
                )

        return BudgetCheckResult(
            allowed=True,
            account_id=account_id,
            period_key=pk,
            spent_usd=spend.cost_usd,
            remaining_usd=remaining_usd,
            spent_tokens=spend.tokens_used,
            remaining_tokens=remaining_tokens,
            percent_used=min(percent_used, 1.0),
        )

    def check_agent(self, agent_name: str) -> list[BudgetCheckResult]:
        """Check all accounts for *agent_name*.  Returns one result per account."""
        accounts = self.list(agent_name=agent_name)
        return [self.check(a.account_id) for a in accounts]

    def is_agent_allowed(self, agent_name: str) -> tuple[bool, str]:
        """Return (allowed, reason) across all accounts for *agent_name*.

        Blocked if *any* account is exhausted.
        """
        for result in self.check_agent(agent_name):
            if not result.allowed:
                return False, result.reason
        return True, ""

    # ── Summary ────────────────────────────────────────────────────────────────

    def summary(self, account_id: str) -> dict[str, Any]:
        account = self.get(account_id)
        if account is None:
            return {"error": f"account {account_id!r} not found"}
        result = self.check(account_id)
        spend = self.current_spend(account_id)
        return {
            "account_id":   account_id,
            "name":         account.name,
            "agent_name":   account.agent_name,
            "team":         account.team,
            "period":       account.period,
            "period_key":   result.period_key,
            "limit_usd":    account.limit_usd,
            "limit_tokens": account.limit_tokens,
            "spent_usd":    round(spend.cost_usd, 6),
            "spent_tokens": spend.tokens_used,
            "call_count":   spend.call_count,
            "percent_used": round(result.percent_used, 4),
            "allowed":      result.allowed,
            "remaining_usd":    round(result.remaining_usd, 6) if result.remaining_usd is not None else None,
            "remaining_tokens": result.remaining_tokens,
        }


# ── Module-level default store ─────────────────────────────────────────────────

_default: BudgetStore | None = None


def get_budget_store(path: str = "") -> BudgetStore:
    """Return the module-level default BudgetStore (lazy-init)."""
    global _default
    if _default is None:
        import os
        p = path or os.getenv("MESHFLOW_BUDGET_PATH", "meshflow_budgets.db")
        _default = BudgetStore(p)
    return _default


def reset_budget_store() -> None:
    global _default
    _default = None


__all__ = [
    "BudgetAccount", "BudgetSpend", "BudgetCheckResult",
    "BudgetStore", "get_budget_store", "reset_budget_store",
    "period_key", "VALID_PERIODS",
]
