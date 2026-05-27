"""Sprint 63 — Policy-as-Code Engine.

Declarative YAML/dict rules evaluated deterministically at every agent call.
Complements dasc-gate: dasc-gate handles risk tiers; PolicyEngine handles
business rules (HIPAA, SOX, GDPR, custom).

PolicyCondition — a single field-op-value predicate.
PolicyRule      — a named rule with conditions + action (allow/deny/log/alert).
PolicyEngine    — loads rules, evaluates context, returns PolicyDecision.
PolicyLoader    — parse rules from YAML or dict.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PolicyAction(str, Enum):
    ALLOW = "allow"
    DENY  = "deny"
    LOG   = "log"
    ALERT = "alert"


class ConditionOp(str, Enum):
    EQ       = "eq"
    NEQ      = "neq"
    IN       = "in"
    NOT_IN   = "not_in"
    GT       = "gt"
    LT       = "lt"
    GTE      = "gte"
    LTE      = "lte"
    CONTAINS = "contains"
    EXISTS   = "exists"


_DDL = """
CREATE TABLE IF NOT EXISTS policy_rules (
    rule_id     TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL DEFAULT '',
    framework   TEXT    NOT NULL DEFAULT 'custom',
    priority    INTEGER NOT NULL DEFAULT 0,
    action      TEXT    NOT NULL,
    conditions  TEXT    NOT NULL DEFAULT '[]',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pr_framework ON policy_rules(framework);
CREATE INDEX IF NOT EXISTS idx_pr_enabled   ON policy_rules(enabled, priority DESC);

CREATE TABLE IF NOT EXISTS policy_decisions (
    decision_id TEXT    PRIMARY KEY,
    rule_id     TEXT,
    rule_name   TEXT    NOT NULL DEFAULT '',
    action      TEXT    NOT NULL,
    context_key TEXT    NOT NULL DEFAULT '',
    reason      TEXT    NOT NULL DEFAULT '',
    ts          REAL    NOT NULL
);
"""


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PolicyCondition:
    field:  str
    op:     ConditionOp
    value:  Any

    def evaluate(self, context: dict[str, Any]) -> bool:
        ctx_val = context.get(self.field)
        op = self.op
        cv = self.value
        if op == ConditionOp.EXISTS:
            return self.field in context
        if ctx_val is None:
            return False
        try:
            if op == ConditionOp.EQ:
                return str(ctx_val) == str(cv)
            if op == ConditionOp.NEQ:
                return str(ctx_val) != str(cv)
            if op == ConditionOp.IN:
                members = [m.strip() for m in str(cv).split(",")]
                return str(ctx_val) in members
            if op == ConditionOp.NOT_IN:
                members = [m.strip() for m in str(cv).split(",")]
                return str(ctx_val) not in members
            if op == ConditionOp.CONTAINS:
                return str(cv) in str(ctx_val)
            if op == ConditionOp.GT:
                return float(ctx_val) > float(cv)
            if op == ConditionOp.LT:
                return float(ctx_val) < float(cv)
            if op == ConditionOp.GTE:
                return float(ctx_val) >= float(cv)
            if op == ConditionOp.LTE:
                return float(ctx_val) <= float(cv)
        except (ValueError, TypeError):
            return False
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op.value, "value": self.value}


@dataclass
class PolicyRule:
    rule_id:     str
    name:        str
    action:      PolicyAction
    conditions:  list[PolicyCondition]
    description: str       = ""
    framework:   str       = "custom"
    priority:    int       = 0
    enabled:     bool      = True
    created_at:  float     = field(default_factory=time.time)

    def matches(self, context: dict[str, Any]) -> bool:
        """All conditions must match (AND logic)."""
        return all(c.evaluate(context) for c in self.conditions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id":     self.rule_id,
            "name":        self.name,
            "action":      self.action.value,
            "conditions":  [c.to_dict() for c in self.conditions],
            "description": self.description,
            "framework":   self.framework,
            "priority":    self.priority,
            "enabled":     self.enabled,
            "created_at":  self.created_at,
        }


@dataclass
class PolicyDecision:
    action:    PolicyAction
    rule_name: str
    reason:    str
    matched:   bool

    @property
    def is_allowed(self) -> bool:
        return self.action != PolicyAction.DENY

    def to_dict(self) -> dict[str, Any]:
        return {
            "action":    self.action.value,
            "rule_name": self.rule_name,
            "reason":    self.reason,
            "matched":   self.matched,
        }


# ── PolicyStore ───────────────────────────────────────────────────────────────

class PolicyStore:
    """SQLite-backed store for policy rules and decision audit."""

    def __init__(self, db_path: str = "meshflow_policy.db") -> None:
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
        self._conn().executescript(_DDL)
        self._conn().commit()

    def add_rule(self, rule: PolicyRule) -> PolicyRule:
        import json
        self._conn().execute(
            """INSERT INTO policy_rules
               (rule_id,name,description,framework,priority,action,conditions,enabled,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                rule.rule_id, rule.name, rule.description, rule.framework,
                rule.priority, rule.action.value,
                json.dumps([c.to_dict() for c in rule.conditions]),
                int(rule.enabled), rule.created_at,
            ),
        )
        self._conn().commit()
        return rule

    def get_rule(self, rule_id: str) -> Optional[PolicyRule]:
        row = self._conn().execute(
            "SELECT * FROM policy_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        return self._rule_from_row(row) if row else None

    def get_by_name(self, name: str) -> Optional[PolicyRule]:
        row = self._conn().execute(
            "SELECT * FROM policy_rules WHERE name=?", (name,)
        ).fetchone()
        return self._rule_from_row(row) if row else None

    def list_rules(self, framework: str = "", enabled_only: bool = False) -> list[PolicyRule]:
        where, params = [], []
        if framework:
            where.append("framework=?"); params.append(framework)
        if enabled_only:
            where.append("enabled=1")
        q = "SELECT * FROM policy_rules"
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY priority DESC, created_at ASC"
        rows = self._conn().execute(q, params).fetchall()
        return [self._rule_from_row(r) for r in rows]

    def enable_rule(self, rule_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE policy_rules SET enabled=1 WHERE rule_id=?", (rule_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def disable_rule(self, rule_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE policy_rules SET enabled=0 WHERE rule_id=?", (rule_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def delete_rule(self, rule_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM policy_rules WHERE rule_id=?", (rule_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def count(self, enabled_only: bool = False) -> int:
        if enabled_only:
            return self._conn().execute(
                "SELECT COUNT(*) FROM policy_rules WHERE enabled=1"
            ).fetchone()[0]
        return self._conn().execute("SELECT COUNT(*) FROM policy_rules").fetchone()[0]

    def log_decision(self, decision: PolicyDecision, rule_id: str = "", context_key: str = "") -> None:
        self._conn().execute(
            """INSERT INTO policy_decisions
               (decision_id,rule_id,rule_name,action,context_key,reason,ts)
               VALUES (?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), rule_id, decision.rule_name,
             decision.action.value, context_key, decision.reason, time.time()),
        )
        self._conn().commit()

    def decision_log(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT * FROM policy_decisions ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _rule_from_row(row: sqlite3.Row) -> PolicyRule:
        import json
        d = dict(row)
        raw_conditions = json.loads(d["conditions"])
        conditions = [
            PolicyCondition(
                field=c["field"],
                op=ConditionOp(c["op"]),
                value=c["value"],
            )
            for c in raw_conditions
        ]
        return PolicyRule(
            rule_id=d["rule_id"],
            name=d["name"],
            action=PolicyAction(d["action"]),
            conditions=conditions,
            description=d["description"],
            framework=d["framework"],
            priority=d["priority"],
            enabled=bool(d["enabled"]),
            created_at=d["created_at"],
        )


# ── PolicyEngine ──────────────────────────────────────────────────────────────

class PolicyEngine:
    """Evaluate context against all enabled rules; first DENY wins, then first ALLOW."""

    def __init__(self, store: PolicyStore, audit: bool = True) -> None:
        self._store = store
        self._audit = audit

    def evaluate(
        self,
        context: dict[str, Any],
        framework: str = "",
        context_key: str = "",
    ) -> PolicyDecision:
        rules = self._store.list_rules(framework=framework, enabled_only=True)
        for rule in rules:
            if rule.matches(context):
                decision = PolicyDecision(
                    action=rule.action,
                    rule_name=rule.name,
                    reason=f"matched rule '{rule.name}' ({rule.framework})",
                    matched=True,
                )
                if self._audit:
                    self._store.log_decision(decision, rule_id=rule.rule_id, context_key=context_key)
                return decision
        decision = PolicyDecision(
            action=PolicyAction.ALLOW,
            rule_name="",
            reason="no rules matched — default allow",
            matched=False,
        )
        if self._audit:
            self._store.log_decision(decision, context_key=context_key)
        return decision

    def is_allowed(self, context: dict[str, Any], framework: str = "") -> bool:
        return self.evaluate(context, framework=framework).is_allowed

    def add_rule(
        self,
        name: str,
        action: PolicyAction,
        conditions: list[tuple[str, str, Any]],
        framework: str = "custom",
        priority: int = 0,
        description: str = "",
    ) -> PolicyRule:
        rule = PolicyRule(
            rule_id=str(uuid.uuid4()),
            name=name,
            action=action,
            conditions=[PolicyCondition(f, ConditionOp(op), v) for f, op, v in conditions],
            framework=framework,
            priority=priority,
            description=description,
        )
        return self._store.add_rule(rule)


# ── PolicyLoader ──────────────────────────────────────────────────────────────

class PolicyLoader:
    """Load policy rules from a Python dict or YAML string."""

    @staticmethod
    def from_dict(data: dict[str, Any], store: PolicyStore) -> list[PolicyRule]:
        rules = []
        for r in data.get("rules", []):
            conditions = [
                PolicyCondition(c["field"], ConditionOp(c["op"]), c["value"])
                for c in r.get("conditions", [])
            ]
            rule = PolicyRule(
                rule_id=r.get("rule_id", str(uuid.uuid4())),
                name=r["name"],
                action=PolicyAction(r["action"]),
                conditions=conditions,
                description=r.get("description", ""),
                framework=r.get("framework", "custom"),
                priority=r.get("priority", 0),
            )
            store.add_rule(rule)
            rules.append(rule)
        return rules

    @staticmethod
    def from_yaml(yaml_str: str, store: PolicyStore) -> list[PolicyRule]:
        try:
            import yaml
            data = yaml.safe_load(yaml_str)
        except ImportError:
            import json
            data = json.loads(yaml_str)
        return PolicyLoader.from_dict(data or {}, store)
