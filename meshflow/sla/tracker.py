"""Sprint 64 — Agent SLA Tracker.

Track p50/p95/p99 latency per agent, define SLA contracts, detect breaches.

SLAContract    — defines acceptable latency thresholds for an agent.
LatencyRecord  — a single observed request latency.
SLAStore       — SQLite-backed store for contracts and observations.
SLATracker     — record latencies, compute percentiles, detect breaches.
SLABreach      — a recorded contract violation.
"""

from __future__ import annotations

import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

_DDL = """
CREATE TABLE IF NOT EXISTS sla_contracts (
    contract_id TEXT    PRIMARY KEY,
    agent_name  TEXT    NOT NULL UNIQUE,
    p50_ms      REAL    NOT NULL,
    p95_ms      REAL    NOT NULL,
    p99_ms      REAL    NOT NULL,
    error_rate  REAL    NOT NULL DEFAULT 0.05,
    window_s    REAL    NOT NULL DEFAULT 3600.0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sla_observations (
    obs_id      TEXT    PRIMARY KEY,
    agent_name  TEXT    NOT NULL,
    latency_ms  REAL    NOT NULL,
    success     INTEGER NOT NULL DEFAULT 1,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_so_agent ON sla_observations(agent_name, ts DESC);

CREATE TABLE IF NOT EXISTS sla_breaches (
    breach_id   TEXT    PRIMARY KEY,
    contract_id TEXT    NOT NULL,
    agent_name  TEXT    NOT NULL,
    breach_type TEXT    NOT NULL,
    observed    REAL    NOT NULL,
    threshold   REAL    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sb_agent ON sla_breaches(agent_name, ts DESC);
"""


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class SLAContract:
    contract_id: str
    agent_name:  str
    p50_ms:      float
    p95_ms:      float
    p99_ms:      float
    error_rate:  float
    window_s:    float
    enabled:     bool
    created_at:  float

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "agent_name":  self.agent_name,
            "p50_ms":      self.p50_ms,
            "p95_ms":      self.p95_ms,
            "p99_ms":      self.p99_ms,
            "error_rate":  self.error_rate,
            "window_s":    self.window_s,
            "enabled":     self.enabled,
            "created_at":  self.created_at,
        }


@dataclass
class LatencyRecord:
    obs_id:     str
    agent_name: str
    latency_ms: float
    success:    bool
    ts:         float

    def to_dict(self) -> dict[str, Any]:
        return {
            "obs_id":     self.obs_id,
            "agent_name": self.agent_name,
            "latency_ms": self.latency_ms,
            "success":    self.success,
            "ts":         self.ts,
        }


@dataclass
class SLAStats:
    agent_name:  str
    total:       int
    p50_ms:      float
    p95_ms:      float
    p99_ms:      float
    avg_ms:      float
    error_rate:  float
    window_s:    float

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "total":      self.total,
            "p50_ms":     round(self.p50_ms, 2),
            "p95_ms":     round(self.p95_ms, 2),
            "p99_ms":     round(self.p99_ms, 2),
            "avg_ms":     round(self.avg_ms, 2),
            "error_rate": round(self.error_rate, 4),
            "window_s":   self.window_s,
        }


@dataclass
class SLABreach:
    breach_id:   str
    contract_id: str
    agent_name:  str
    breach_type: str
    observed:    float
    threshold:   float
    ts:          float

    def to_dict(self) -> dict[str, Any]:
        return {
            "breach_id":   self.breach_id,
            "contract_id": self.contract_id,
            "agent_name":  self.agent_name,
            "breach_type": self.breach_type,
            "observed":    round(self.observed, 2),
            "threshold":   round(self.threshold, 2),
            "ts":          self.ts,
        }


# ── Percentile helper ─────────────────────────────────────────────────────────

def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = (p / 100) * (len(sorted_v) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_v) - 1)
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * (idx - lo)


# ── SLAStore ──────────────────────────────────────────────────────────────────

class SLAStore:
    """SQLite-backed SLA contract and observation store."""

    def __init__(self, db_path: str = "meshflow_sla.db") -> None:
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

    def define_contract(
        self,
        agent_name: str,
        p50_ms: float,
        p95_ms: float,
        p99_ms: float,
        error_rate: float = 0.05,
        window_s: float = 3600.0,
    ) -> SLAContract:
        if not (p50_ms <= p95_ms <= p99_ms):
            raise ValueError("SLA thresholds must satisfy p50 ≤ p95 ≤ p99")
        if not (0.0 <= error_rate <= 1.0):
            raise ValueError("error_rate must be 0–1")
        contract = SLAContract(
            contract_id=str(uuid.uuid4()),
            agent_name=agent_name,
            p50_ms=p50_ms, p95_ms=p95_ms, p99_ms=p99_ms,
            error_rate=error_rate, window_s=window_s,
            enabled=True, created_at=time.time(),
        )
        self._conn().execute(
            """INSERT OR REPLACE INTO sla_contracts
               (contract_id,agent_name,p50_ms,p95_ms,p99_ms,error_rate,window_s,enabled,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (contract.contract_id, contract.agent_name, contract.p50_ms,
             contract.p95_ms, contract.p99_ms, contract.error_rate,
             contract.window_s, 1, contract.created_at),
        )
        self._conn().commit()
        return contract

    def get_contract(self, agent_name: str) -> Optional[SLAContract]:
        row = self._conn().execute(
            "SELECT * FROM sla_contracts WHERE agent_name=?", (agent_name,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        return SLAContract(
            contract_id=d["contract_id"], agent_name=d["agent_name"],
            p50_ms=d["p50_ms"], p95_ms=d["p95_ms"], p99_ms=d["p99_ms"],
            error_rate=d["error_rate"], window_s=d["window_s"],
            enabled=bool(d["enabled"]), created_at=d["created_at"],
        )

    def list_contracts(self) -> list[SLAContract]:
        rows = self._conn().execute("SELECT * FROM sla_contracts ORDER BY agent_name").fetchall()
        return [
            SLAContract(
                contract_id=d["contract_id"], agent_name=d["agent_name"],
                p50_ms=d["p50_ms"], p95_ms=d["p95_ms"], p99_ms=d["p99_ms"],
                error_rate=d["error_rate"], window_s=d["window_s"],
                enabled=bool(d["enabled"]), created_at=d["created_at"],
            )
            for d in (dict(r) for r in rows)
        ]

    def record_observation(
        self,
        agent_name: str,
        latency_ms: float,
        success: bool = True,
        ts: Optional[float] = None,
    ) -> LatencyRecord:
        obs = LatencyRecord(
            obs_id=str(uuid.uuid4()),
            agent_name=agent_name,
            latency_ms=latency_ms,
            success=success,
            ts=ts if ts is not None else time.time(),
        )
        self._conn().execute(
            "INSERT INTO sla_observations (obs_id,agent_name,latency_ms,success,ts) VALUES (?,?,?,?,?)",
            (obs.obs_id, obs.agent_name, obs.latency_ms, int(obs.success), obs.ts),
        )
        self._conn().commit()
        return obs

    def observations(
        self,
        agent_name: str,
        window_s: float = 3600.0,
        now: Optional[float] = None,
    ) -> list[LatencyRecord]:
        cutoff = (now or time.time()) - window_s
        rows = self._conn().execute(
            "SELECT * FROM sla_observations WHERE agent_name=? AND ts>=? ORDER BY ts DESC",
            (agent_name, cutoff),
        ).fetchall()
        return [
            LatencyRecord(
                obs_id=r["obs_id"], agent_name=r["agent_name"],
                latency_ms=r["latency_ms"], success=bool(r["success"]), ts=r["ts"],
            )
            for r in rows
        ]

    def save_breach(self, breach: SLABreach) -> None:
        self._conn().execute(
            """INSERT INTO sla_breaches
               (breach_id,contract_id,agent_name,breach_type,observed,threshold,ts)
               VALUES (?,?,?,?,?,?,?)""",
            (breach.breach_id, breach.contract_id, breach.agent_name,
             breach.breach_type, breach.observed, breach.threshold, breach.ts),
        )
        self._conn().commit()

    def list_breaches(self, agent_name: str = "", limit: int = 50) -> list[SLABreach]:
        if agent_name:
            rows = self._conn().execute(
                "SELECT * FROM sla_breaches WHERE agent_name=? ORDER BY ts DESC LIMIT ?",
                (agent_name, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM sla_breaches ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            SLABreach(
                breach_id=r["breach_id"], contract_id=r["contract_id"],
                agent_name=r["agent_name"], breach_type=r["breach_type"],
                observed=r["observed"], threshold=r["threshold"], ts=r["ts"],
            )
            for r in rows
        ]


# ── SLATracker ────────────────────────────────────────────────────────────────

class SLATracker:
    """Record observations, compute stats, detect SLA breaches."""

    def __init__(self, store: SLAStore) -> None:
        self._store = store

    def record(
        self,
        agent_name: str,
        latency_ms: float,
        success: bool = True,
        now: Optional[float] = None,
    ) -> tuple[LatencyRecord, list[SLABreach]]:
        obs = self._store.record_observation(agent_name, latency_ms, success, now)
        breaches = self._check_breaches(agent_name, now)
        return obs, breaches

    def stats(self, agent_name: str, window_s: float = 3600.0, now: Optional[float] = None) -> SLAStats:
        obs = self._store.observations(agent_name, window_s=window_s, now=now)
        if not obs:
            return SLAStats(agent_name, 0, 0.0, 0.0, 0.0, 0.0, 0.0, window_s)
        latencies = [o.latency_ms for o in obs]
        errors = sum(1 for o in obs if not o.success)
        return SLAStats(
            agent_name=agent_name,
            total=len(obs),
            p50_ms=_percentile(latencies, 50),
            p95_ms=_percentile(latencies, 95),
            p99_ms=_percentile(latencies, 99),
            avg_ms=sum(latencies) / len(latencies),
            error_rate=errors / len(obs),
            window_s=window_s,
        )

    def _check_breaches(self, agent_name: str, now: Optional[float] = None) -> list[SLABreach]:
        contract = self._store.get_contract(agent_name)
        if contract is None or not contract.enabled:
            return []
        s = self.stats(agent_name, window_s=contract.window_s, now=now)
        if s.total < 10:
            return []
        breaches: list[SLABreach] = []
        checks = [
            ("p50", s.p50_ms, contract.p50_ms),
            ("p95", s.p95_ms, contract.p95_ms),
            ("p99", s.p99_ms, contract.p99_ms),
            ("error_rate", s.error_rate, contract.error_rate),
        ]
        for breach_type, observed, threshold in checks:
            if observed > threshold:
                breach = SLABreach(
                    breach_id=str(uuid.uuid4()),
                    contract_id=contract.contract_id,
                    agent_name=agent_name,
                    breach_type=breach_type,
                    observed=observed,
                    threshold=threshold,
                    ts=now or time.time(),
                )
                self._store.save_breach(breach)
                breaches.append(breach)
        return breaches
