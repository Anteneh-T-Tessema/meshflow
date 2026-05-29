"""Cost/quality Pareto analysis and cross-model benchmarking.

Closes the analytics gap: given multiple eval runs (different models, prompts,
or configs), compute the Pareto-efficient frontier and produce a side-by-side
comparison table.

Usage::

    from meshflow.eval.pareto import ModelBenchmark, ParetoAnalyzer

    # Record runs
    bench = ModelBenchmark()
    bench.add_run("claude-opus-4-7",          tokens=8200, cost_usd=0.123, pass_rate=0.96)
    bench.add_run("claude-sonnet-4-6",         tokens=6100, cost_usd=0.024, pass_rate=0.92)
    bench.add_run("claude-haiku-4-5-20251001", tokens=4800, cost_usd=0.004, pass_rate=0.81)

    analyzer = ParetoAnalyzer(bench)
    frontier = analyzer.pareto_frontier()
    print(analyzer.comparison_table())

    # From eval baseline files
    bench2 = ModelBenchmark.from_baseline_files({
        "opus":   "baselines/opus.json",
        "sonnet": "baselines/sonnet.json",
        "haiku":  "baselines/haiku.json",
    })
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Run record ────────────────────────────────────────────────────────────────

@dataclass
class BenchmarkRun:
    model: str
    tokens: int
    cost_usd: float
    pass_rate: float       # 0.0 – 1.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    run_id: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def cost_per_point(self) -> float:
        """USD per pass-rate point (lower = better)."""
        return self.cost_usd / max(self.pass_rate, 0.001)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model":           self.model,
            "tokens":          self.tokens,
            "cost_usd":        round(self.cost_usd, 6),
            "pass_rate":       round(self.pass_rate, 4),
            "latency_p50_ms":  round(self.latency_p50_ms, 1),
            "latency_p95_ms":  round(self.latency_p95_ms, 1),
            "cost_per_point":  round(self.cost_per_point, 6),
            "run_id":          self.run_id,
        }


# ── ModelBenchmark ────────────────────────────────────────────────────────────

class ModelBenchmark:
    """Collection of benchmark runs, one per model configuration."""

    def __init__(self) -> None:
        self._runs: list[BenchmarkRun] = []

    def add_run(
        self,
        model: str,
        tokens: int,
        cost_usd: float,
        pass_rate: float,
        **kwargs: Any,
    ) -> BenchmarkRun:
        run = BenchmarkRun(model=model, tokens=tokens, cost_usd=cost_usd, pass_rate=pass_rate, **kwargs)
        self._runs.append(run)
        return run

    def runs(self) -> list[BenchmarkRun]:
        return list(self._runs)

    @classmethod
    def from_baseline_files(cls, paths: dict[str, str]) -> "ModelBenchmark":
        """Load benchmark data from {model_name: baseline_json_path} dict."""
        bench = cls()
        for model, path in paths.items():
            p = Path(path)
            if not p.exists():
                continue
            with open(p) as fh:
                data = json.load(fh)
            rate = float(data.get("pass_rate", 1.0))
            if rate > 1.5:
                rate /= 100.0
            bench.add_run(
                model=model,
                tokens=int(data.get("total_tokens", data.get("tokens", 0))),
                cost_usd=float(data.get("total_cost_usd", data.get("cost_usd", 0.0))),
                pass_rate=rate,
                latency_p50_ms=float(data.get("latency_p50_ms", 0.0)),
                latency_p95_ms=float(data.get("latency_p95_ms", 0.0)),
                run_id=data.get("run_id", ""),
                metadata=data,
            )
        return bench

    @classmethod
    def from_ledger(cls, ledger_db: str = "meshflow_runs.db", limit: int = 100) -> "ModelBenchmark":
        """Build a benchmark from the replay ledger (most recent *limit* runs)."""
        import asyncio
        from meshflow.core.ledger import ReplayLedger

        async def _load() -> list[dict[str, Any]]:
            ledger = ReplayLedger(ledger_db)
            return await ledger.list_runs(limit=limit)

        runs_raw = asyncio.run(_load())
        bench = cls()
        for r in runs_raw:
            bench.add_run(
                model=r.get("model", "unknown"),
                tokens=int(r.get("total_tokens", 0)),
                cost_usd=float(r.get("total_cost_usd", 0.0)),
                pass_rate=1.0 - float(r.get("blocked_rate", 0.0)),
                run_id=r.get("run_id", ""),
            )
        return bench


# ── ParetoAnalyzer ────────────────────────────────────────────────────────────

class ParetoAnalyzer:
    """Compute the Pareto-efficient frontier and comparison statistics.

    Pareto criterion: a run A dominates B if
        cost_usd(A) ≤ cost_usd(B)  AND  pass_rate(A) ≥ pass_rate(B)
    with strict inequality in at least one dimension.
    """

    def __init__(self, benchmark: ModelBenchmark) -> None:
        self._bench = benchmark

    def pareto_frontier(self) -> list[BenchmarkRun]:
        """Return the subset of runs not dominated by any other run."""
        runs = self._bench.runs()
        if not runs:
            return []

        frontier: list[BenchmarkRun] = []
        for candidate in runs:
            dominated = False
            for other in runs:
                if other is candidate:
                    continue
                if (other.cost_usd <= candidate.cost_usd
                        and other.pass_rate >= candidate.pass_rate
                        and (other.cost_usd < candidate.cost_usd
                             or other.pass_rate > candidate.pass_rate)):
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)

        frontier.sort(key=lambda r: r.cost_usd)
        return frontier

    def best_by_quality(self) -> BenchmarkRun | None:
        runs = self._bench.runs()
        return max(runs, key=lambda r: r.pass_rate) if runs else None

    def best_by_cost(self) -> BenchmarkRun | None:
        runs = self._bench.runs()
        return min(runs, key=lambda r: r.cost_usd) if runs else None

    def best_value(self) -> BenchmarkRun | None:
        """Return the run with the best pass_rate-per-dollar."""
        runs = self._bench.runs()
        if not runs:
            return None
        return min(runs, key=lambda r: r.cost_per_point)

    def comparison_table(self, *, ascii: bool = False) -> str:
        """Render a side-by-side model comparison table."""
        runs = sorted(self._bench.runs(), key=lambda r: r.cost_usd)
        frontier_ids = {id(r) for r in self.pareto_frontier()}

        sep = "-" if ascii else "─"
        col = 18

        header = (
            f"{'Model':<{col}} {'Tokens':>8} {'Cost USD':>10} {'Pass Rate':>10} "
            f"{'$/point':>10} {'Pareto':>7}"
        )
        divider = sep * len(header)

        lines = [header, divider]
        for r in runs:
            on_frontier = "* " if id(r) in frontier_ids else "  "
            lines.append(
                f"{r.model:<{col}} {r.tokens:>8,} {r.cost_usd:>10.5f} {r.pass_rate:>10.1%} "
                f"{r.cost_per_point:>10.5f} {on_frontier:>7}"
            )
        lines.append(divider)
        lines.append("  * = Pareto-efficient (not dominated on cost AND quality)")
        return "\n".join(lines)

    def sensitivity(self, model_a: str, model_b: str) -> dict[str, Any]:
        """Compute cost/quality tradeoff between two named models."""
        runs_by_model: dict[str, BenchmarkRun] = {r.model: r for r in self._bench.runs()}
        a = runs_by_model.get(model_a)
        b = runs_by_model.get(model_b)
        if a is None or b is None:
            return {"error": f"Model(s) not found. Available: {list(runs_by_model)}"}

        cost_diff = b.cost_usd - a.cost_usd
        rate_diff = b.pass_rate - a.pass_rate
        return {
            "model_a":          model_a,
            "model_b":          model_b,
            "cost_delta_usd":   round(cost_diff, 6),
            "pass_rate_delta":  round(rate_diff, 4),
            "cost_pct_change":  round(cost_diff / max(a.cost_usd, 1e-9), 4),
            "quality_pct_change": round(rate_diff, 4),
            "recommended":      model_b if rate_diff / max(abs(cost_diff), 1e-9) > 10 else model_a,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs": [r.to_dict() for r in self._bench.runs()],
            "pareto_frontier": [r.to_dict() for r in self.pareto_frontier()],
            "best_quality":    self.best_by_quality().to_dict() if self.best_by_quality() else None,
            "best_cost":       self.best_by_cost().to_dict() if self.best_by_cost() else None,
            "best_value":      self.best_value().to_dict() if self.best_value() else None,
        }


__all__ = ["ModelBenchmark", "BenchmarkRun", "ParetoAnalyzer"]
