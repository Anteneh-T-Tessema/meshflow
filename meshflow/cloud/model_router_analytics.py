"""ModelRouter analytics — routing decision aggregation and cost savings reporting.

Reads StepRecord metadata emitted by ModelRouter.route() and aggregates:
  - Tier distribution (nano / small / medium / large)
  - Actual cost vs projected always-large cost (savings)
  - Per-run routing history
  - Cross-session memory usage patterns (Working / Episodic / BM25 / Procedural)

Exposed via:
  - Python: ``RouterAnalytics(db).summary(n=50)``
  - HTTP:   GET /api/analytics/model-router  (added to TraceServer)
  - CLI:    meshflow analytics model-router  (see meshflow/cli/main.py)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Any

_LARGE_MODEL_COST_PER_TOKEN = 15e-6  # $0.015 / 1K tokens (claude-opus-4-8 rough avg)
_DEFAULT_TIERS = {"nano", "small", "medium", "large"}


@dataclass
class TierStats:
    tier: str
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class RouterSummary:
    runs_analysed: int
    total_steps: int
    routed_steps: int           # steps where routing metadata is present
    tier_distribution: dict[str, TierStats]
    actual_cost_usd: float
    always_large_cost_usd: float
    savings_usd: float
    savings_pct: float
    top_models: list[dict[str, Any]]      # [{model, calls, tokens, cost_usd}]
    cost_trend: list[dict[str, Any]]      # per-run [{run_id, cost_usd, routed_steps}]
    memory_usage: dict[str, int]          # {tier_name: hit_count} — cross-session

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tier_distribution"] = {
            k: asdict(v) for k, v in self.tier_distribution.items()
        }
        return d


class RouterAnalytics:
    """Aggregate ModelRouter routing decisions from the ReplayLedger.

    Parameters
    ----------
    db:         Path to the MeshFlow SQLite ledger (or connection string for PG).
    n_runs:     How many recent runs to include.
    """

    def __init__(self, db: str = "meshflow_runs.db", n_runs: int = 50) -> None:
        self._db = db
        self._n_runs = n_runs

    def summary(self) -> RouterSummary:
        """Synchronous wrapper — safe to call from sync code or HTTP handlers."""
        return asyncio.run(self._summary_async())

    async def _summary_async(self) -> RouterSummary:
        from meshflow.core.ledger import ReplayLedger

        ledger = ReplayLedger(self._db)
        run_ids: list[str] = await ledger.list_runs()
        run_ids = run_ids[: self._n_runs]

        tier_map: dict[str, TierStats] = {t: TierStats(tier=t) for t in _DEFAULT_TIERS}
        model_map: dict[str, dict[str, Any]] = {}
        cost_trend: list[dict[str, Any]] = []
        memory_hits: dict[str, int] = defaultdict(int)

        total_steps = 0
        routed_steps = 0
        actual_cost = 0.0

        for run_id in run_ids:
            try:
                records = await ledger.get_run(run_id)
            except Exception:
                continue

            run_cost = 0.0
            run_routed = 0

            for rec in records:
                total_steps += 1
                meta: dict[str, Any] = rec.get("metadata", {}) if isinstance(rec, dict) else {}
                cost = float(rec.get("cost_usd", 0) if isinstance(rec, dict) else getattr(rec, "cost_usd", 0))
                tokens = int(rec.get("tokens_used", 0) if isinstance(rec, dict) else getattr(rec, "tokens_used", 0))
                run_cost += cost
                actual_cost += cost

                # ── Routing metadata (set by ModelRouter when wired into StepRuntime) ──
                tier = meta.get("model_tier")
                model_name = meta.get("model_used") or meta.get("model")

                if tier:
                    routed_steps += 1
                    run_routed += 1
                    if tier not in tier_map:
                        tier_map[tier] = TierStats(tier=tier)
                    tier_map[tier].calls += 1
                    tier_map[tier].tokens += tokens
                    tier_map[tier].cost_usd += cost

                if model_name:
                    if model_name not in model_map:
                        model_map[model_name] = {"model": model_name, "calls": 0, "tokens": 0, "cost_usd": 0.0}
                    model_map[model_name]["calls"] += 1
                    model_map[model_name]["tokens"] += tokens
                    model_map[model_name]["cost_usd"] += cost

                # ── Cross-session memory hit tracking ──
                for mem_tier in ("working", "episodic", "bm25", "procedural"):
                    hits = meta.get(f"memory_{mem_tier}_hits", 0)
                    if hits:
                        memory_hits[mem_tier] += int(hits)

            cost_trend.append({
                "run_id": run_id,
                "cost_usd": round(run_cost, 6),
                "routed_steps": run_routed,
            })

        # Savings: what would it have cost if everything used the large model?
        total_tokens = sum(t.tokens for t in tier_map.values())
        always_large = total_tokens * _LARGE_MODEL_COST_PER_TOKEN
        savings = max(0.0, always_large - actual_cost)
        savings_pct = savings / always_large if always_large > 0 else 0.0

        top_models = sorted(model_map.values(), key=lambda m: m["cost_usd"], reverse=True)[:10]
        for m in top_models:
            m["cost_usd"] = round(m["cost_usd"], 6)

        return RouterSummary(
            runs_analysed=len(run_ids),
            total_steps=total_steps,
            routed_steps=routed_steps,
            tier_distribution=tier_map,
            actual_cost_usd=round(actual_cost, 6),
            always_large_cost_usd=round(always_large, 6),
            savings_usd=round(savings, 6),
            savings_pct=round(savings_pct, 4),
            top_models=top_models,
            cost_trend=cost_trend,
            memory_usage=dict(memory_hits),
        )


__all__ = ["RouterAnalytics", "RouterSummary", "TierStats"]
