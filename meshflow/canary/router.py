"""Sprint 58 — Canary Agent Router.

Route a configurable fraction of traffic to a new agent version (canary),
track per-cohort outcomes, and auto-promote or auto-rollback based on thresholds.

CanaryConfig   — experiment definition (split, thresholds, status).
CanaryOutcome  — single request outcome record (cohort, success, latency_ms).
CanaryStats    — per-cohort aggregates (total, successes, errors, avg_latency).
CanaryStore    — SQLite CRUD for configs and outcomes.
CanaryRouter   — traffic routing + outcome recording + promotion/rollback logic.

Usage
-----
    from meshflow.canary.router import CanaryStore, CanaryRouter

    store  = CanaryStore(":memory:")
    router = CanaryRouter(store)

    exp = store.create_experiment(
        name="billing-v2",
        stable_agent="billing-v1",
        canary_agent="billing-v2",
        split=0.1,
    )

    cohort = router.route(exp.experiment_id)          # "stable" or "canary"
    router.record_outcome(exp.experiment_id, cohort, success=True, latency_ms=120)

    if router.should_promote(exp.experiment_id):
        router.promote(exp.experiment_id)
"""

from __future__ import annotations

import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional


# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS canary_experiments (
    experiment_id       TEXT    PRIMARY KEY,
    name                TEXT    NOT NULL UNIQUE,
    stable_agent        TEXT    NOT NULL,
    canary_agent        TEXT    NOT NULL,
    split               REAL    NOT NULL DEFAULT 0.1,
    min_requests        INTEGER NOT NULL DEFAULT 10,
    promote_threshold   REAL    NOT NULL DEFAULT 0.95,
    rollback_threshold  REAL    NOT NULL DEFAULT 0.80,
    status              TEXT    NOT NULL DEFAULT 'active',
    created_at          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ce_name   ON canary_experiments(name);
CREATE INDEX IF NOT EXISTS idx_ce_status ON canary_experiments(status);

CREATE TABLE IF NOT EXISTS canary_outcomes (
    outcome_id      TEXT    PRIMARY KEY,
    experiment_id   TEXT    NOT NULL,
    cohort          TEXT    NOT NULL,
    success         INTEGER NOT NULL,
    latency_ms      REAL    NOT NULL DEFAULT 0.0,
    ts              REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_co_exp_cohort ON canary_outcomes(experiment_id, cohort);
"""

_VALID_STATUSES = frozenset({"active", "promoted", "rolled_back", "paused"})


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class CanaryConfig:
    experiment_id:      str
    name:               str
    stable_agent:       str
    canary_agent:       str
    split:              float
    min_requests:       int
    promote_threshold:  float
    rollback_threshold: float
    status:             str
    created_at:         float

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id":      self.experiment_id,
            "name":               self.name,
            "stable_agent":       self.stable_agent,
            "canary_agent":       self.canary_agent,
            "split":              self.split,
            "min_requests":       self.min_requests,
            "promote_threshold":  self.promote_threshold,
            "rollback_threshold": self.rollback_threshold,
            "status":             self.status,
            "created_at":         self.created_at,
        }


@dataclass
class CanaryOutcome:
    outcome_id:    str
    experiment_id: str
    cohort:        str
    success:       bool
    latency_ms:    float
    ts:            float

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id":    self.outcome_id,
            "experiment_id": self.experiment_id,
            "cohort":        self.cohort,
            "success":       self.success,
            "latency_ms":    self.latency_ms,
            "ts":            self.ts,
        }


@dataclass
class CanaryStats:
    cohort:       str
    total:        int
    successes:    int
    errors:       int
    success_rate: float
    error_rate:   float
    avg_latency:  float

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort":       self.cohort,
            "total":        self.total,
            "successes":    self.successes,
            "errors":       self.errors,
            "success_rate": self.success_rate,
            "error_rate":   self.error_rate,
            "avg_latency":  self.avg_latency,
        }


# ── CanaryStore ───────────────────────────────────────────────────────────────

class CanaryStore:
    """SQLite-backed store for canary experiments and outcomes."""

    def __init__(self, db_path: str = "meshflow_canary.db") -> None:
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
        con.executescript(_DDL)
        con.commit()

    # ── Experiments ────────────────────────────────────────────────────────────

    def create_experiment(
        self,
        name: str,
        stable_agent: str,
        canary_agent: str,
        split: float = 0.1,
        min_requests: int = 10,
        promote_threshold: float = 0.95,
        rollback_threshold: float = 0.80,
    ) -> CanaryConfig:
        if not (0.0 <= split <= 1.0):
            raise ValueError(f"split must be between 0 and 1, got {split}")
        if promote_threshold < rollback_threshold:
            raise ValueError("promote_threshold must be >= rollback_threshold")
        config = CanaryConfig(
            experiment_id=str(uuid.uuid4()),
            name=name,
            stable_agent=stable_agent,
            canary_agent=canary_agent,
            split=split,
            min_requests=min_requests,
            promote_threshold=promote_threshold,
            rollback_threshold=rollback_threshold,
            status="active",
            created_at=time.time(),
        )
        self._conn().execute(
            """
            INSERT INTO canary_experiments
                (experiment_id, name, stable_agent, canary_agent, split,
                 min_requests, promote_threshold, rollback_threshold,
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config.experiment_id, config.name, config.stable_agent,
                config.canary_agent, config.split, config.min_requests,
                config.promote_threshold, config.rollback_threshold,
                config.status, config.created_at,
            ),
        )
        self._conn().commit()
        return config

    def get_experiment(self, experiment_id: str) -> Optional[CanaryConfig]:
        row = self._conn().execute(
            "SELECT * FROM canary_experiments WHERE experiment_id=?", (experiment_id,)
        ).fetchone()
        return self._config_from_row(row) if row else None

    def get_by_name(self, name: str) -> Optional[CanaryConfig]:
        row = self._conn().execute(
            "SELECT * FROM canary_experiments WHERE name=?", (name,)
        ).fetchone()
        return self._config_from_row(row) if row else None

    def list_experiments(self, status: str = "") -> list[CanaryConfig]:
        if status:
            rows = self._conn().execute(
                "SELECT * FROM canary_experiments WHERE status=? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM canary_experiments ORDER BY created_at DESC"
            ).fetchall()
        return [self._config_from_row(r) for r in rows]

    def update_status(self, experiment_id: str, status: str) -> bool:
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}")
        cur = self._conn().execute(
            "UPDATE canary_experiments SET status=? WHERE experiment_id=?",
            (status, experiment_id),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def delete_experiment(self, experiment_id: str) -> bool:
        con = self._conn()
        con.execute("DELETE FROM canary_outcomes WHERE experiment_id=?", (experiment_id,))
        cur = con.execute(
            "DELETE FROM canary_experiments WHERE experiment_id=?", (experiment_id,)
        )
        con.commit()
        return cur.rowcount > 0

    # ── Outcomes ───────────────────────────────────────────────────────────────

    def record_outcome(
        self,
        experiment_id: str,
        cohort: str,
        success: bool,
        latency_ms: float = 0.0,
        ts: Optional[float] = None,
    ) -> CanaryOutcome:
        outcome = CanaryOutcome(
            outcome_id=str(uuid.uuid4()),
            experiment_id=experiment_id,
            cohort=cohort,
            success=success,
            latency_ms=latency_ms,
            ts=ts if ts is not None else time.time(),
        )
        self._conn().execute(
            """
            INSERT INTO canary_outcomes
                (outcome_id, experiment_id, cohort, success, latency_ms, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (outcome.outcome_id, outcome.experiment_id, outcome.cohort,
             int(outcome.success), outcome.latency_ms, outcome.ts),
        )
        self._conn().commit()
        return outcome

    def cohort_stats(self, experiment_id: str, cohort: str) -> CanaryStats:
        rows = self._conn().execute(
            "SELECT success, latency_ms FROM canary_outcomes WHERE experiment_id=? AND cohort=?",
            (experiment_id, cohort),
        ).fetchall()
        total = len(rows)
        if total == 0:
            return CanaryStats(cohort, 0, 0, 0, 0.0, 0.0, 0.0)
        successes = sum(1 for r in rows if r["success"])
        errors    = total - successes
        avg_lat   = sum(r["latency_ms"] for r in rows) / total
        return CanaryStats(
            cohort=cohort,
            total=total,
            successes=successes,
            errors=errors,
            success_rate=successes / total,
            error_rate=errors / total,
            avg_latency=avg_lat,
        )

    def outcome_count(self, experiment_id: str, cohort: str = "") -> int:
        if cohort:
            return self._conn().execute(
                "SELECT COUNT(*) FROM canary_outcomes WHERE experiment_id=? AND cohort=?",
                (experiment_id, cohort),
            ).fetchone()[0]
        return self._conn().execute(
            "SELECT COUNT(*) FROM canary_outcomes WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()[0]

    @staticmethod
    def _config_from_row(row: sqlite3.Row) -> CanaryConfig:
        d = dict(row)
        return CanaryConfig(
            experiment_id=d["experiment_id"],
            name=d["name"],
            stable_agent=d["stable_agent"],
            canary_agent=d["canary_agent"],
            split=d["split"],
            min_requests=d["min_requests"],
            promote_threshold=d["promote_threshold"],
            rollback_threshold=d["rollback_threshold"],
            status=d["status"],
            created_at=d["created_at"],
        )


# ── CanaryRouter ──────────────────────────────────────────────────────────────

class CanaryRouter:
    """Traffic routing + outcome tracking + automatic promotion / rollback.

    Parameters
    ----------
    store:  ``CanaryStore`` instance.
    seed:   Optional random seed (for deterministic tests).
    """

    def __init__(self, store: CanaryStore, seed: Optional[int] = None) -> None:
        self._store = store
        self._rng   = random.Random(seed)

    def route(self, experiment_id: str) -> str:
        """Return ``"canary"`` with probability *split*, else ``"stable"``.

        Returns ``"stable"`` immediately if the experiment is not active.
        """
        config = self._store.get_experiment(experiment_id)
        if config is None or not config.is_active:
            return "stable"
        return "canary" if self._rng.random() < config.split else "stable"

    def record_outcome(
        self,
        experiment_id: str,
        cohort: str,
        success: bool,
        latency_ms: float = 0.0,
    ) -> CanaryOutcome:
        return self._store.record_outcome(experiment_id, cohort, success, latency_ms)

    def stats(self, experiment_id: str) -> dict[str, CanaryStats]:
        return {
            "stable": self._store.cohort_stats(experiment_id, "stable"),
            "canary": self._store.cohort_stats(experiment_id, "canary"),
        }

    def should_promote(self, experiment_id: str) -> bool:
        """True if canary has enough data and meets the promote threshold."""
        config = self._store.get_experiment(experiment_id)
        if config is None or not config.is_active:
            return False
        canary_stats = self._store.cohort_stats(experiment_id, "canary")
        if canary_stats.total < config.min_requests:
            return False
        return canary_stats.success_rate >= config.promote_threshold

    def should_rollback(self, experiment_id: str) -> bool:
        """True if canary has enough data and falls below the rollback threshold."""
        config = self._store.get_experiment(experiment_id)
        if config is None or not config.is_active:
            return False
        canary_stats = self._store.cohort_stats(experiment_id, "canary")
        if canary_stats.total < config.min_requests:
            return False
        return canary_stats.success_rate < config.rollback_threshold

    def promote(self, experiment_id: str) -> bool:
        return self._store.update_status(experiment_id, "promoted")

    def rollback(self, experiment_id: str) -> bool:
        return self._store.update_status(experiment_id, "rolled_back")

    def pause(self, experiment_id: str) -> bool:
        return self._store.update_status(experiment_id, "paused")
