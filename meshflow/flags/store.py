"""Sprint 59 — Feature Flags.

Flag definitions with targeting rules and rollout percentages let teams
progressively enable new agent behaviours — without re-deploying.

FlagDefinition  — a named flag (bool/string/number) with default value.
FlagRule        — per-flag targeting rule: evaluate context, return value.
FlagStore       — SQLite CRUD for definitions and rules.
FlagEvaluator   — evaluate a flag for a given context dict.

Usage
-----
    from meshflow.flags.store import FlagStore, FlagEvaluator

    store = FlagStore(":memory:")
    flag = store.define("new-billing-ui", "bool", False)

    store.add_rule(
        flag_id=flag.flag_id,
        condition_key="agent_name",
        condition_op="eq",
        condition_value="billing-agent",
        return_value=True,
    )

    evaluator = FlagEvaluator(store)
    value = evaluator.evaluate("new-billing-ui", {"agent_name": "billing-agent"})
    # True
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Union

FlagValue = Union[bool, int, float, str]

_VALID_TYPES = frozenset({"bool", "string", "number"})
_VALID_OPS   = frozenset({"eq", "neq", "in", "gt", "lt", "gte", "lte", "contains"})

_DDL = """
CREATE TABLE IF NOT EXISTS flag_definitions (
    flag_id      TEXT    PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    flag_type    TEXT    NOT NULL,
    default_val  TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    rollout_pct  REAL    NOT NULL DEFAULT 100.0,
    created_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fd_name ON flag_definitions(name);

CREATE TABLE IF NOT EXISTS flag_rules (
    rule_id         TEXT    PRIMARY KEY,
    flag_id         TEXT    NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 0,
    condition_key   TEXT    NOT NULL,
    condition_op    TEXT    NOT NULL,
    condition_value TEXT    NOT NULL,
    return_value    TEXT    NOT NULL,
    created_at      REAL    NOT NULL,
    FOREIGN KEY (flag_id) REFERENCES flag_definitions(flag_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fr_flag ON flag_rules(flag_id, priority DESC);
"""


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class FlagDefinition:
    flag_id:     str
    name:        str
    flag_type:   str
    default_val: FlagValue
    description: str
    enabled:     bool
    rollout_pct: float
    created_at:  float

    @property
    def is_enabled(self) -> bool:
        return self.enabled

    def to_dict(self) -> dict[str, Any]:
        return {
            "flag_id":     self.flag_id,
            "name":        self.name,
            "flag_type":   self.flag_type,
            "default_val": self.default_val,
            "description": self.description,
            "enabled":     self.enabled,
            "rollout_pct": self.rollout_pct,
            "created_at":  self.created_at,
        }


@dataclass
class FlagRule:
    rule_id:         str
    flag_id:         str
    priority:        int
    condition_key:   str
    condition_op:    str
    condition_value: Any
    return_value:    FlagValue
    created_at:      float

    def matches(self, context: dict[str, Any]) -> bool:
        """Return True if this rule's condition applies to *context*."""
        ctx_val = context.get(self.condition_key)
        if ctx_val is None:
            return False
        op = self.condition_op
        cv = self.condition_value
        try:
            if op == "eq":
                return str(ctx_val) == str(cv)
            if op == "neq":
                return str(ctx_val) != str(cv)
            if op == "in":
                members = [m.strip() for m in str(cv).split(",")]
                return str(ctx_val) in members
            if op == "contains":
                return str(cv) in str(ctx_val)
            if op == "gt":
                return float(ctx_val) > float(cv)
            if op == "lt":
                return float(ctx_val) < float(cv)
            if op == "gte":
                return float(ctx_val) >= float(cv)
            if op == "lte":
                return float(ctx_val) <= float(cv)
        except (ValueError, TypeError):
            return False
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id":         self.rule_id,
            "flag_id":         self.flag_id,
            "priority":        self.priority,
            "condition_key":   self.condition_key,
            "condition_op":    self.condition_op,
            "condition_value": self.condition_value,
            "return_value":    self.return_value,
            "created_at":      self.created_at,
        }


# ── FlagStore ─────────────────────────────────────────────────────────────────

class FlagStore:
    """SQLite-backed store for feature flag definitions and rules."""

    def __init__(self, db_path: str = "meshflow_flags.db") -> None:
        self._db_path = db_path
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys=ON")
        else:
            self._mem_conn = None
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    # ── serialise / deserialise flag values ───────────────────────────────────

    @staticmethod
    def _encode(value: FlagValue) -> str:
        return json.dumps(value)

    @staticmethod
    def _decode(raw: str, flag_type: str) -> FlagValue:
        val = json.loads(raw)
        if flag_type == "bool":
            return bool(val)
        if flag_type == "number":
            return float(val)
        return str(val)

    # ── Flag definitions ──────────────────────────────────────────────────────

    def define(
        self,
        name: str,
        flag_type: str = "bool",
        default_value: FlagValue = False,
        description: str = "",
        rollout_pct: float = 100.0,
    ) -> FlagDefinition:
        if flag_type not in _VALID_TYPES:
            raise ValueError(f"flag_type must be one of {sorted(_VALID_TYPES)}, got {flag_type!r}")
        if not (0.0 <= rollout_pct <= 100.0):
            raise ValueError(f"rollout_pct must be 0–100, got {rollout_pct}")
        flag = FlagDefinition(
            flag_id=str(uuid.uuid4()),
            name=name,
            flag_type=flag_type,
            default_val=default_value,
            description=description,
            enabled=True,
            rollout_pct=rollout_pct,
            created_at=time.time(),
        )
        self._conn().execute(
            """
            INSERT INTO flag_definitions
                (flag_id, name, flag_type, default_val, description,
                 enabled, rollout_pct, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flag.flag_id, flag.name, flag.flag_type,
                self._encode(flag.default_val), flag.description,
                int(flag.enabled), flag.rollout_pct, flag.created_at,
            ),
        )
        self._conn().commit()
        return flag

    def get(self, flag_id: str) -> Optional[FlagDefinition]:
        row = self._conn().execute(
            "SELECT * FROM flag_definitions WHERE flag_id=?", (flag_id,)
        ).fetchone()
        return self._flag_from_row(row) if row else None

    def get_by_name(self, name: str) -> Optional[FlagDefinition]:
        row = self._conn().execute(
            "SELECT * FROM flag_definitions WHERE name=?", (name,)
        ).fetchone()
        return self._flag_from_row(row) if row else None

    def list_flags(self, enabled_only: bool = False) -> list[FlagDefinition]:
        if enabled_only:
            rows = self._conn().execute(
                "SELECT * FROM flag_definitions WHERE enabled=1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM flag_definitions ORDER BY created_at DESC"
            ).fetchall()
        return [self._flag_from_row(r) for r in rows]

    def enable(self, flag_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE flag_definitions SET enabled=1 WHERE flag_id=?", (flag_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def disable(self, flag_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE flag_definitions SET enabled=0 WHERE flag_id=?", (flag_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def set_rollout(self, flag_id: str, rollout_pct: float) -> bool:
        if not (0.0 <= rollout_pct <= 100.0):
            raise ValueError(f"rollout_pct must be 0–100, got {rollout_pct}")
        cur = self._conn().execute(
            "UPDATE flag_definitions SET rollout_pct=? WHERE flag_id=?",
            (rollout_pct, flag_id),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def delete(self, flag_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM flag_definitions WHERE flag_id=?", (flag_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def count(self, enabled_only: bool = False) -> int:
        if enabled_only:
            return self._conn().execute(
                "SELECT COUNT(*) FROM flag_definitions WHERE enabled=1"
            ).fetchone()[0]
        return self._conn().execute(
            "SELECT COUNT(*) FROM flag_definitions"
        ).fetchone()[0]

    # ── Rules ─────────────────────────────────────────────────────────────────

    def add_rule(
        self,
        flag_id: str,
        condition_key: str,
        condition_op: str,
        condition_value: Any,
        return_value: FlagValue,
        priority: int = 0,
    ) -> FlagRule:
        if condition_op not in _VALID_OPS:
            raise ValueError(f"condition_op must be one of {sorted(_VALID_OPS)}")
        if self.get(flag_id) is None:
            raise ValueError(f"Flag {flag_id!r} not found")
        rule = FlagRule(
            rule_id=str(uuid.uuid4()),
            flag_id=flag_id,
            priority=priority,
            condition_key=condition_key,
            condition_op=condition_op,
            condition_value=condition_value,
            return_value=return_value,
            created_at=time.time(),
        )
        self._conn().execute(
            """
            INSERT INTO flag_rules
                (rule_id, flag_id, priority, condition_key, condition_op,
                 condition_value, return_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule.rule_id, rule.flag_id, rule.priority,
                rule.condition_key, rule.condition_op,
                self._encode(rule.condition_value),
                self._encode(rule.return_value),
                rule.created_at,
            ),
        )
        self._conn().commit()
        return rule

    def list_rules(self, flag_id: str) -> list[FlagRule]:
        rows = self._conn().execute(
            "SELECT * FROM flag_rules WHERE flag_id=? ORDER BY priority DESC, created_at ASC",
            (flag_id,),
        ).fetchall()
        return [self._rule_from_row(r) for r in rows]

    def delete_rule(self, rule_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM flag_rules WHERE rule_id=?", (rule_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def rule_count(self, flag_id: str = "") -> int:
        if flag_id:
            return self._conn().execute(
                "SELECT COUNT(*) FROM flag_rules WHERE flag_id=?", (flag_id,)
            ).fetchone()[0]
        return self._conn().execute("SELECT COUNT(*) FROM flag_rules").fetchone()[0]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _flag_from_row(self, row: sqlite3.Row) -> FlagDefinition:
        d = dict(row)
        return FlagDefinition(
            flag_id=d["flag_id"],
            name=d["name"],
            flag_type=d["flag_type"],
            default_val=self._decode(d["default_val"], d["flag_type"]),
            description=d["description"],
            enabled=bool(d["enabled"]),
            rollout_pct=d["rollout_pct"],
            created_at=d["created_at"],
        )

    def _rule_from_row(self, row: sqlite3.Row) -> FlagRule:
        d = dict(row)
        flag = self.get(d["flag_id"])
        flag_type = flag.flag_type if flag else "string"
        return FlagRule(
            rule_id=d["rule_id"],
            flag_id=d["flag_id"],
            priority=d["priority"],
            condition_key=d["condition_key"],
            condition_op=d["condition_op"],
            condition_value=json.loads(d["condition_value"]),
            return_value=self._decode(d["return_value"], flag_type),
            created_at=d["created_at"],
        )


# ── FlagEvaluator ─────────────────────────────────────────────────────────────

class FlagEvaluator:
    """Evaluate feature flags for a given context.

    Evaluation order
    ----------------
    1. If the flag is disabled → return the default value.
    2. Rollout gate: hash ``context.get("entity_id", "")`` deterministically
       against ``rollout_pct``; if outside the bucket → return default.
    3. Walk rules in descending priority order; first matching rule wins.
    4. No rule matched → return the flag's default value.
    """

    def __init__(self, store: FlagStore) -> None:
        self._store = store

    def evaluate(self, flag_name: str, context: Optional[dict[str, Any]] = None) -> Any:
        ctx = context or {}
        flag = self._store.get_by_name(flag_name)
        if flag is None:
            raise KeyError(f"Flag {flag_name!r} not found")
        if not flag.enabled:
            return flag.default_val
        if not self._in_rollout(flag, ctx):
            return flag.default_val
        for rule in self._store.list_rules(flag.flag_id):
            if rule.matches(ctx):
                return rule.return_value
        return flag.default_val

    def evaluate_all(self, context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        ctx = context or {}
        return {
            f.name: self.evaluate(f.name, ctx)
            for f in self._store.list_flags()
        }

    def is_enabled(self, flag_name: str, context: Optional[dict[str, Any]] = None) -> bool:
        val = self.evaluate(flag_name, context)
        return bool(val)

    @staticmethod
    def _in_rollout(flag: FlagDefinition, context: dict[str, Any]) -> bool:
        if flag.rollout_pct >= 100.0:
            return True
        if flag.rollout_pct <= 0.0:
            return False
        entity = str(context.get("entity_id", context.get("agent_name", "")))
        bucket = (
            int(hashlib.md5(f"{flag.flag_id}:{entity}".encode()).hexdigest(), 16) % 100
        )
        return bucket < flag.rollout_pct
