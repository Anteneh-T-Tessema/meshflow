"""Sprint 54 — Time-series metric store for agent observability.

MetricPoint  — a single (agent, metric, value, timestamp) sample.
MetricStore  — SQLite time-series backend with aggregation helpers.

Usage
-----
    from meshflow.alerting.metrics import MetricPoint, MetricStore

    store = MetricStore(":memory:")
    store.record("billing-agent", "latency_ms", 142.3)
    store.record("billing-agent", "error_rate", 0.04)

    # Query last 60 s
    pts = store.query("billing-agent", "latency_ms", window_s=60)
    avg = store.aggregate("billing-agent", "latency_ms", window_s=60, fn="mean")
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


_DDL = """
CREATE TABLE IF NOT EXISTS metric_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    value       REAL    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mp_agent_metric_ts
    ON metric_points(agent_name, metric, ts);
CREATE INDEX IF NOT EXISTS idx_mp_ts
    ON metric_points(ts);
"""


@dataclass
class MetricPoint:
    agent_name: str
    metric:     str
    value:      float
    ts:         float

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "metric":     self.metric,
            "value":      self.value,
            "ts":         self.ts,
        }


class MetricStore:
    """SQLite-backed time-series store for agent metrics.

    Parameters
    ----------
    db_path:        Filesystem path or ``":memory:"``.
    retention_s:    Automatically purge points older than this many seconds
                    when ``prune()`` is called.  Default 7 days.
    """

    def __init__(
        self,
        db_path: str = "meshflow_metrics.db",
        retention_s: float = 7 * 86400,
    ) -> None:
        self._db_path     = db_path
        self._retention_s = retention_s
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None
        self._ensure_schema()

    # ── Connection ─────────────────────────────────────────────────────────────

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

    # ── Write ──────────────────────────────────────────────────────────────────

    def record(
        self,
        agent_name: str,
        metric: str,
        value: float,
        ts: Optional[float] = None,
    ) -> MetricPoint:
        """Insert a single metric sample.  Returns the stored point."""
        point = MetricPoint(agent_name, metric, value, ts if ts is not None else time.time())
        con = self._conn()
        con.execute(
            "INSERT INTO metric_points (agent_name, metric, value, ts) VALUES (?, ?, ?, ?)",
            (point.agent_name, point.metric, point.value, point.ts),
        )
        con.commit()
        return point

    def record_batch(self, points: list[MetricPoint]) -> None:
        """Insert multiple points in a single transaction."""
        if not points:
            return
        con = self._conn()
        con.executemany(
            "INSERT INTO metric_points (agent_name, metric, value, ts) VALUES (?, ?, ?, ?)",
            [(p.agent_name, p.metric, p.value, p.ts) for p in points],
        )
        con.commit()

    # ── Query ──────────────────────────────────────────────────────────────────

    def query(
        self,
        agent_name: str,
        metric: str,
        window_s: float = 60.0,
        now: Optional[float] = None,
    ) -> list[MetricPoint]:
        """Return all points in [now - window_s, now]."""
        ts_now = now if now is not None else time.time()
        rows = self._conn().execute(
            """
            SELECT agent_name, metric, value, ts
            FROM metric_points
            WHERE agent_name=? AND metric=? AND ts >= ? AND ts <= ?
            ORDER BY ts ASC
            """,
            (agent_name, metric, ts_now - window_s, ts_now),
        ).fetchall()
        return [MetricPoint(r["agent_name"], r["metric"], r["value"], r["ts"]) for r in rows]

    def latest(
        self,
        agent_name: str,
        metric: str,
    ) -> Optional[MetricPoint]:
        """Return the most-recent point for (agent, metric), or None."""
        row = self._conn().execute(
            """
            SELECT agent_name, metric, value, ts
            FROM metric_points
            WHERE agent_name=? AND metric=?
            ORDER BY ts DESC LIMIT 1
            """,
            (agent_name, metric),
        ).fetchone()
        return MetricPoint(row["agent_name"], row["metric"], row["value"], row["ts"]) if row else None

    def aggregate(
        self,
        agent_name: str,
        metric: str,
        window_s: float = 60.0,
        fn: str = "mean",
        now: Optional[float] = None,
    ) -> Optional[float]:
        """Compute an aggregate over [now - window_s, now].

        fn options: ``"mean"``, ``"max"``, ``"min"``, ``"sum"``, ``"count"``.
        Returns ``None`` if no points in window.
        """
        points = self.query(agent_name, metric, window_s=window_s, now=now)
        if not points:
            return None
        values = [p.value for p in points]
        agg_fn: dict[str, Callable[[list[float]], float]] = {
            "mean":  lambda v: sum(v) / len(v),
            "max":   max,
            "min":   min,
            "sum":   sum,
            "count": lambda v: float(len(v)),
        }
        if fn not in agg_fn:
            raise ValueError(f"Unknown aggregate function: {fn!r}. Choose from {list(agg_fn)}")
        return agg_fn[fn](values)

    def agents(self) -> list[str]:
        """Return all distinct agent names that have recorded points."""
        rows = self._conn().execute(
            "SELECT DISTINCT agent_name FROM metric_points ORDER BY agent_name"
        ).fetchall()
        return [r["agent_name"] for r in rows]

    def metrics_for(self, agent_name: str) -> list[str]:
        """Return all distinct metric names for a given agent."""
        rows = self._conn().execute(
            "SELECT DISTINCT metric FROM metric_points WHERE agent_name=? ORDER BY metric",
            (agent_name,),
        ).fetchall()
        return [r["metric"] for r in rows]

    def count(self, agent_name: Optional[str] = None, metric: Optional[str] = None) -> int:
        """Return total point count, optionally filtered by agent and/or metric."""
        if agent_name and metric:
            return self._conn().execute(
                "SELECT COUNT(*) FROM metric_points WHERE agent_name=? AND metric=?",
                (agent_name, metric),
            ).fetchone()[0]
        if agent_name:
            return self._conn().execute(
                "SELECT COUNT(*) FROM metric_points WHERE agent_name=?",
                (agent_name,),
            ).fetchone()[0]
        return self._conn().execute("SELECT COUNT(*) FROM metric_points").fetchone()[0]

    # ── Maintenance ────────────────────────────────────────────────────────────

    def prune(self, now: Optional[float] = None) -> int:
        """Delete points older than retention_s.  Returns number deleted."""
        cutoff = (now if now is not None else time.time()) - self._retention_s
        con = self._conn()
        cur = con.execute("DELETE FROM metric_points WHERE ts < ?", (cutoff,))
        con.commit()
        return cur.rowcount

    def clear(self, agent_name: Optional[str] = None) -> int:
        """Delete all points (or all for a specific agent).  Returns count deleted."""
        con = self._conn()
        if agent_name:
            cur = con.execute("DELETE FROM metric_points WHERE agent_name=?", (agent_name,))
        else:
            cur = con.execute("DELETE FROM metric_points")
        con.commit()
        return cur.rowcount
