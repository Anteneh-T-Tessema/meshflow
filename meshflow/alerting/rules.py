"""Sprint 54 — Alert rules, fired-alert records, and their SQLite stores.

AlertRule    — declarative threshold rule evaluated against a metric window.
AlertRecord  — a single firing of a rule (with deduplication / auto-resolve).
AlertRuleStore  — CRUD for rules.
AlertStore      — CRUD for fired alerts.

Operators: ``gt``, ``lt``, ``gte``, ``lte``, ``eq``
Aggregate functions (passed through to MetricStore): ``mean``, ``max``, ``min``,
``sum``, ``count``

Usage
-----
    from meshflow.alerting.rules import AlertRule, AlertRuleStore, AlertStore

    rule_store = AlertRuleStore(":memory:")
    alert_store = AlertStore(":memory:")

    rule = rule_store.add(
        name="high-latency",
        agent_name="billing-agent",
        metric="latency_ms",
        operator="gt",
        threshold=500.0,
        window_s=60.0,
        agg_fn="mean",
    )
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional


# ── DDL ───────────────────────────────────────────────────────────────────────

_RULES_DDL = """
CREATE TABLE IF NOT EXISTS alert_rules (
    rule_id     TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    agent_name  TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    operator    TEXT    NOT NULL,
    threshold   REAL    NOT NULL,
    window_s    REAL    NOT NULL DEFAULT 60.0,
    agg_fn      TEXT    NOT NULL DEFAULT 'mean',
    webhook_url TEXT    NOT NULL DEFAULT '',
    webhook_secret TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ar_agent
    ON alert_rules(agent_name, metric);
"""

_ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS alert_records (
    alert_id    TEXT    PRIMARY KEY,
    rule_id     TEXT    NOT NULL,
    rule_name   TEXT    NOT NULL,
    agent_name  TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    value       REAL    NOT NULL,
    threshold   REAL    NOT NULL,
    operator    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'firing',
    fired_at    REAL    NOT NULL,
    resolved_at REAL,
    acked_at    REAL,
    acked_by    TEXT,
    message     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alrt_rule_status
    ON alert_records(rule_id, status);
CREATE INDEX IF NOT EXISTS idx_alrt_agent
    ON alert_records(agent_name, fired_at);
"""

# Valid operators
_OPERATORS = frozenset({"gt", "lt", "gte", "lte", "eq"})
# Valid aggregate functions
_AGG_FNS = frozenset({"mean", "max", "min", "sum", "count"})


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class AlertRule:
    rule_id:        str
    name:           str
    agent_name:     str
    metric:         str
    operator:       str   # gt | lt | gte | lte | eq
    threshold:      float
    window_s:       float
    agg_fn:         str   # mean | max | min | sum | count
    webhook_url:    str
    webhook_secret: str
    enabled:        bool
    created_at:     float

    def evaluate(self, value: float) -> bool:
        """Return True if *value* breaches this rule."""
        ops = {
            "gt":  value >  self.threshold,
            "lt":  value <  self.threshold,
            "gte": value >= self.threshold,
            "lte": value <= self.threshold,
            "eq":  abs(value - self.threshold) < 1e-9,
        }
        return ops[self.operator]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id":        self.rule_id,
            "name":           self.name,
            "agent_name":     self.agent_name,
            "metric":         self.metric,
            "operator":       self.operator,
            "threshold":      self.threshold,
            "window_s":       self.window_s,
            "agg_fn":         self.agg_fn,
            "webhook_url":    self.webhook_url,
            "enabled":        self.enabled,
            "created_at":     self.created_at,
        }


@dataclass
class AlertRecord:
    alert_id:    str
    rule_id:     str
    rule_name:   str
    agent_name:  str
    metric:      str
    value:       float
    threshold:   float
    operator:    str
    status:      str    # firing | resolved | acked
    fired_at:    float
    resolved_at: Optional[float]
    acked_at:    Optional[float]
    acked_by:    Optional[str]
    message:     str

    @property
    def is_firing(self) -> bool:
        return self.status == "firing"

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def is_acked(self) -> bool:
        return self.status == "acked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id":    self.alert_id,
            "rule_id":     self.rule_id,
            "rule_name":   self.rule_name,
            "agent_name":  self.agent_name,
            "metric":      self.metric,
            "value":       self.value,
            "threshold":   self.threshold,
            "operator":    self.operator,
            "status":      self.status,
            "fired_at":    self.fired_at,
            "resolved_at": self.resolved_at,
            "acked_at":    self.acked_at,
            "acked_by":    self.acked_by,
            "message":     self.message,
        }


# ── AlertRuleStore ────────────────────────────────────────────────────────────

class AlertRuleStore:
    """SQLite-backed CRUD store for :class:`AlertRule` objects."""

    def __init__(self, db_path: str = "meshflow_alerts.db") -> None:
        self._db_path = db_path
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
        con.executescript(_RULES_DDL)
        con.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    def add(
        self,
        name: str,
        agent_name: str,
        metric: str,
        operator: str,
        threshold: float,
        window_s: float = 60.0,
        agg_fn: str = "mean",
        webhook_url: str = "",
        webhook_secret: str = "",
        enabled: bool = True,
    ) -> AlertRule:
        if operator not in _OPERATORS:
            raise ValueError(f"Unknown operator {operator!r}. Choose from {sorted(_OPERATORS)}")
        if agg_fn not in _AGG_FNS:
            raise ValueError(f"Unknown agg_fn {agg_fn!r}. Choose from {sorted(_AGG_FNS)}")
        rule = AlertRule(
            rule_id=str(uuid.uuid4()),
            name=name,
            agent_name=agent_name,
            metric=metric,
            operator=operator,
            threshold=threshold,
            window_s=window_s,
            agg_fn=agg_fn,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            enabled=enabled,
            created_at=time.time(),
        )
        self._conn().execute(
            """
            INSERT INTO alert_rules
                (rule_id, name, agent_name, metric, operator, threshold,
                 window_s, agg_fn, webhook_url, webhook_secret, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule.rule_id, rule.name, rule.agent_name, rule.metric,
                rule.operator, rule.threshold, rule.window_s, rule.agg_fn,
                rule.webhook_url, rule.webhook_secret, int(rule.enabled),
                rule.created_at,
            ),
        )
        self._conn().commit()
        return rule

    def enable(self, rule_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE alert_rules SET enabled=1 WHERE rule_id=?", (rule_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def disable(self, rule_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE alert_rules SET enabled=0 WHERE rule_id=?", (rule_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def delete(self, rule_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM alert_rules WHERE rule_id=?", (rule_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    # ── Query ──────────────────────────────────────────────────────────────────

    def get(self, rule_id: str) -> Optional[AlertRule]:
        row = self._conn().execute(
            "SELECT * FROM alert_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_rules(
        self,
        agent_name: str = "",
        enabled_only: bool = False,
        limit: int = 100,
    ) -> list[AlertRule]:
        sql = "SELECT * FROM alert_rules WHERE 1=1"
        params: list[Any] = []
        if agent_name:
            sql += " AND agent_name=?"
            params.append(agent_name)
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn().execute(sql, params).fetchall()
        return [self._from_row(r) for r in rows]

    def count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AlertRule:
        d = dict(row)
        return AlertRule(
            rule_id=d["rule_id"],
            name=d["name"],
            agent_name=d["agent_name"],
            metric=d["metric"],
            operator=d["operator"],
            threshold=d["threshold"],
            window_s=d["window_s"],
            agg_fn=d["agg_fn"],
            webhook_url=d["webhook_url"],
            webhook_secret=d["webhook_secret"],
            enabled=bool(d["enabled"]),
            created_at=d["created_at"],
        )


# ── AlertStore ────────────────────────────────────────────────────────────────

class AlertStore:
    """SQLite-backed CRUD store for fired :class:`AlertRecord` objects."""

    def __init__(self, db_path: str = "meshflow_alerts.db") -> None:
        self._db_path = db_path
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
        con.executescript(_ALERTS_DDL)
        con.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    def fire(
        self,
        rule: AlertRule,
        value: float,
        message: str = "",
    ) -> AlertRecord:
        """Create a new firing alert record."""
        record = AlertRecord(
            alert_id=str(uuid.uuid4()),
            rule_id=rule.rule_id,
            rule_name=rule.name,
            agent_name=rule.agent_name,
            metric=rule.metric,
            value=value,
            threshold=rule.threshold,
            operator=rule.operator,
            status="firing",
            fired_at=time.time(),
            resolved_at=None,
            acked_at=None,
            acked_by=None,
            message=message or f"{rule.agent_name}.{rule.metric} {rule.operator} {rule.threshold} (value={value:.4g})",
        )
        self._conn().execute(
            """
            INSERT INTO alert_records
                (alert_id, rule_id, rule_name, agent_name, metric, value,
                 threshold, operator, status, fired_at, resolved_at,
                 acked_at, acked_by, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.alert_id, record.rule_id, record.rule_name,
                record.agent_name, record.metric, record.value,
                record.threshold, record.operator, record.status,
                record.fired_at, record.resolved_at,
                record.acked_at, record.acked_by, record.message,
            ),
        )
        self._conn().commit()
        return record

    def resolve(self, alert_id: str) -> bool:
        now = time.time()
        cur = self._conn().execute(
            "UPDATE alert_records SET status='resolved', resolved_at=? WHERE alert_id=? AND status='firing'",
            (now, alert_id),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def ack(self, alert_id: str, acked_by: str = "") -> bool:
        now = time.time()
        cur = self._conn().execute(
            """
            UPDATE alert_records
            SET status='acked', acked_at=?, acked_by=?
            WHERE alert_id=? AND status IN ('firing', 'resolved')
            """,
            (now, acked_by, alert_id),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def resolve_for_rule(self, rule_id: str) -> int:
        """Resolve all firing alerts for a rule (used when rule condition clears)."""
        now = time.time()
        cur = self._conn().execute(
            "UPDATE alert_records SET status='resolved', resolved_at=? WHERE rule_id=? AND status='firing'",
            (now, rule_id),
        )
        self._conn().commit()
        return cur.rowcount

    # ── Query ──────────────────────────────────────────────────────────────────

    def get(self, alert_id: str) -> Optional[AlertRecord]:
        row = self._conn().execute(
            "SELECT * FROM alert_records WHERE alert_id=?", (alert_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def firing(self, agent_name: str = "", rule_id: str = "") -> list[AlertRecord]:
        sql = "SELECT * FROM alert_records WHERE status='firing'"
        params: list[Any] = []
        if agent_name:
            sql += " AND agent_name=?"
            params.append(agent_name)
        if rule_id:
            sql += " AND rule_id=?"
            params.append(rule_id)
        sql += " ORDER BY fired_at DESC"
        return [self._from_row(r) for r in self._conn().execute(sql, params).fetchall()]

    def list_alerts(
        self,
        status: str = "",
        agent_name: str = "",
        limit: int = 100,
    ) -> list[AlertRecord]:
        sql = "SELECT * FROM alert_records WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if agent_name:
            sql += " AND agent_name=?"
            params.append(agent_name)
        sql += " ORDER BY fired_at DESC LIMIT ?"
        params.append(limit)
        return [self._from_row(r) for r in self._conn().execute(sql, params).fetchall()]

    def counts(self) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) as n FROM alert_records GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def has_firing(self, rule_id: str) -> bool:
        n = self._conn().execute(
            "SELECT COUNT(*) FROM alert_records WHERE rule_id=? AND status='firing'",
            (rule_id,),
        ).fetchone()[0]
        return n > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AlertRecord:
        d = dict(row)
        return AlertRecord(
            alert_id=d["alert_id"],
            rule_id=d["rule_id"],
            rule_name=d["rule_name"],
            agent_name=d["agent_name"],
            metric=d["metric"],
            value=d["value"],
            threshold=d["threshold"],
            operator=d["operator"],
            status=d["status"],
            fired_at=d["fired_at"],
            resolved_at=d["resolved_at"],
            acked_at=d["acked_at"],
            acked_by=d["acked_by"],
            message=d["message"],
        )
