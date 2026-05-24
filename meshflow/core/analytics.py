"""Workflow run analytics — cost trends, latency percentiles, quality drift.

WorkflowAnalytics reads directly from ReplayLedger and produces time-series
and aggregated metrics useful for FinOps dashboards, compliance reporting,
and capacity planning.

Usage::

    from meshflow.core.analytics import WorkflowAnalytics
    from meshflow.core.ledger import ReplayLedger

    ledger = ReplayLedger("meshflow_runs.db")
    analytics = WorkflowAnalytics(ledger)

    report = await analytics.full_report(n_runs=20)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshflow.core.ledger import ReplayLedger


@dataclass
class RunSummary:
    run_id: str
    total_steps: int
    blocked_steps: int
    total_cost_usd: float
    total_tokens: int
    total_carbon_gco2: float
    avg_uncertainty: float
    p95_latency_ms: float
    blocked_rate: float   # 0.0 – 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_steps": self.total_steps,
            "blocked_steps": self.blocked_steps,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "total_carbon_gco2": round(self.total_carbon_gco2, 6),
            "avg_uncertainty": round(self.avg_uncertainty, 4),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "blocked_rate": round(self.blocked_rate, 4),
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, int(pct / 100 * len(s)) - 1)
    return s[min(idx, len(s) - 1)]


class WorkflowAnalytics:
    """Post-hoc analytics over a ReplayLedger.

    All methods are async because ReplayLedger uses async SQLite.
    Designed to be called from the dashboard or CLI on demand — not on the
    hot path.
    """

    def __init__(self, ledger: "ReplayLedger") -> None:
        self._ledger = ledger

    async def _load_runs(self, n: int) -> list[RunSummary]:
        """Load the last ``n`` runs from the ledger and build RunSummary objects."""
        run_ids = await self._ledger.list_runs()
        run_ids = run_ids[-n:]
        summaries: list[RunSummary] = []
        for rid in run_ids:
            steps = await self._ledger.get_run(rid) or []
            if not steps:
                continue
            costs = [s.get("cost_usd", 0.0) if isinstance(s, dict) else s.cost_usd for s in steps]
            tokens = [s.get("tokens_used", 0) if isinstance(s, dict) else s.tokens_used for s in steps]
            carbon = [s.get("carbon_gco2", 0.0) if isinstance(s, dict) else s.carbon_gco2 for s in steps]
            uncertainty = [s.get("uncertainty", 0.0) if isinstance(s, dict) else s.uncertainty for s in steps]
            latency = sorted([s.get("duration_ms", 0.0) if isinstance(s, dict) else s.duration_ms for s in steps])
            blocked = [s for s in steps if (s.get("blocked") if isinstance(s, dict) else s.blocked)]
            summaries.append(RunSummary(
                run_id=rid,
                total_steps=len(steps),
                blocked_steps=len(blocked),
                total_cost_usd=sum(costs),
                total_tokens=sum(tokens),
                total_carbon_gco2=sum(carbon),
                avg_uncertainty=statistics.mean(uncertainty) if uncertainty else 0.0,
                p95_latency_ms=_percentile(latency, 95),
                blocked_rate=len(blocked) / len(steps) if steps else 0.0,
            ))
        return summaries

    async def cost_trend(self, n: int = 20) -> list[dict[str, Any]]:
        """Return per-run cost for the last ``n`` runs."""
        summaries = await self._load_runs(n)
        return [
            {"run_id": s.run_id[:12], "cost_usd": round(s.total_cost_usd, 6)}
            for s in summaries
        ]

    async def latency_percentiles(self, n: int = 20) -> dict[str, Any]:
        """Return p50/p95/p99 of per-run p95 latency over the last ``n`` runs."""
        summaries = await self._load_runs(n)
        vals = [s.p95_latency_ms for s in summaries]
        return {
            "runs_analysed": len(summaries),
            "p50_run_p95_ms": _percentile(vals, 50),
            "p95_run_p95_ms": _percentile(vals, 95),
            "p99_run_p95_ms": _percentile(vals, 99),
            "mean_run_p95_ms": round(statistics.mean(vals), 2) if vals else 0.0,
        }

    async def blocked_rate(self, n: int = 20) -> dict[str, Any]:
        """Return aggregate blocked-step rate over the last ``n`` runs."""
        summaries = await self._load_runs(n)
        if not summaries:
            return {"blocked_rate": 0.0, "total_steps": 0, "blocked_steps": 0, "runs": 0}
        total_steps = sum(s.total_steps for s in summaries)
        total_blocked = sum(s.blocked_steps for s in summaries)
        rates = [s.blocked_rate for s in summaries]
        return {
            "blocked_rate": round(total_blocked / total_steps, 4) if total_steps else 0.0,
            "total_steps": total_steps,
            "blocked_steps": total_blocked,
            "runs": len(summaries),
            "max_run_blocked_rate": round(max(rates), 4) if rates else 0.0,
        }

    async def quality_drift(self, n: int = 20) -> dict[str, Any]:
        """Return uncertainty trend — rising uncertainty may indicate quality drift."""
        summaries = await self._load_runs(n)
        vals = [s.avg_uncertainty for s in summaries]
        if not vals:
            return {"trend": "stable", "first_half_avg": 0.0, "second_half_avg": 0.0, "delta": 0.0}
        mid = len(vals) // 2 or 1
        first_half = statistics.mean(vals[:mid]) if vals[:mid] else 0.0
        second_half = statistics.mean(vals[mid:]) if vals[mid:] else 0.0
        delta = second_half - first_half
        trend = "degrading" if delta > 0.05 else ("improving" if delta < -0.05 else "stable")
        return {
            "trend": trend,
            "first_half_avg": round(first_half, 4),
            "second_half_avg": round(second_half, 4),
            "delta": round(delta, 4),
        }

    async def carbon_trend(self, n: int = 20) -> list[dict[str, Any]]:
        """Return per-run carbon footprint for the last ``n`` runs."""
        summaries = await self._load_runs(n)
        return [
            {"run_id": s.run_id[:12], "carbon_gco2": round(s.total_carbon_gco2, 6)}
            for s in summaries
        ]

    async def top_costly_nodes(self, n_runs: int = 50, top_n: int = 10) -> list[dict[str, Any]]:
        """Return the most expensive nodes by total cost across the last ``n_runs`` runs."""
        run_ids = await self._ledger.list_runs()
        run_ids = run_ids[-n_runs:]
        node_cost: dict[str, float] = {}
        node_calls: dict[str, int] = {}
        for rid in run_ids:
            steps = await self._ledger.get_run(rid) or []
            for s in steps:
                nid = s.get("node_id") if isinstance(s, dict) else s.node_id
                cost = s.get("cost_usd", 0.0) if isinstance(s, dict) else s.cost_usd
                node_cost[nid] = node_cost.get(nid, 0.0) + cost
                node_calls[nid] = node_calls.get(nid, 0) + 1
        ranked = sorted(node_cost.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {
                "node_id": nid,
                "total_cost_usd": round(cost, 6),
                "call_count": node_calls.get(nid, 0),
                "avg_cost_usd": round(cost / max(node_calls.get(nid, 1), 1), 6),
            }
            for nid, cost in ranked
        ]

    async def full_report(self, n_runs: int = 20) -> dict[str, Any]:
        """Return a composite analytics report (all dimensions)."""
        summaries = await self._load_runs(n_runs)
        return {
            "runs_analysed": len(summaries),
            "cost_trend": [{"run_id": s.run_id[:12], "cost_usd": round(s.total_cost_usd, 6)} for s in summaries],
            "latency": await self.latency_percentiles(n_runs),
            "blocked": await self.blocked_rate(n_runs),
            "quality": await self.quality_drift(n_runs),
            "top_costly_nodes": await self.top_costly_nodes(n_runs),
            "total_cost_usd": round(sum(s.total_cost_usd for s in summaries), 6),
            "total_tokens": sum(s.total_tokens for s in summaries),
            "total_carbon_gco2": round(sum(s.total_carbon_gco2 for s in summaries), 6),
        }
