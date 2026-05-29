"""WorkflowSweep — run a workflow N times with different inputs and compare results.

Closes the "parameter sweep" gap: given a WorkflowDefinition and a grid of
input combinations, runs all variants (concurrently or sequentially) and returns
a structured comparison table that can feed into ParetoAnalyzer.

Usage::

    from meshflow.eval.sweep import WorkflowSweep, SweepGrid

    sweep = WorkflowSweep(
        workflow_yaml="pipeline.yaml",
        grid=SweepGrid(
            task=["Summarise Q3 earnings", "Summarise Q4 earnings"],
            model=["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        ),
        concurrency=4,
        ledger_db="sweep_runs.db",
    )
    results = await sweep.run()
    print(results.comparison_table())

    # Feed into Pareto
    from meshflow.eval.pareto import ModelBenchmark, ParetoAnalyzer
    bench = results.to_benchmark()
    frontier = ParetoAnalyzer(bench).pareto_frontier()
"""

from __future__ import annotations

import asyncio
import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── SweepGrid ─────────────────────────────────────────────────────────────────

@dataclass
class SweepGrid:
    """Defines the parameter space to sweep over.

    Each keyword argument is a list of values.  The sweep runs all
    combinations (Cartesian product).

    Example::

        grid = SweepGrid(
            task=["Summarise Q3", "Summarise Q4"],
            model=["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        )
        # → 4 combinations

    Special keys:
    - ``task``: overrides the workflow task string for that variant.
    - ``model``: swaps every native node's model to this value.
    """

    def __init__(self, **kwargs: list[Any]) -> None:
        self._params: dict[str, list[Any]] = kwargs

    def combinations(self) -> list[dict[str, Any]]:
        """Return all (param → value) dicts in the grid."""
        if not self._params:
            return [{}]
        keys = list(self._params.keys())
        values = [self._params[k] for k in keys]
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    def __len__(self) -> int:
        n = 1
        for v in self._params.values():
            n *= len(v)
        return n


# ── SweepVariantResult ────────────────────────────────────────────────────────

@dataclass
class SweepVariantResult:
    """Result for one grid combination."""

    variant_id: str
    params: dict[str, Any]
    output: str
    completed: bool
    total_tokens: int
    total_cost_usd: float
    duration_s: float
    pass_rate: float = 1.0      # 0.0 if blocked, 1.0 if completed
    error: str = ""
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id":    self.variant_id,
            "params":        self.params,
            "output":        self.output[:200],
            "completed":     self.completed,
            "total_tokens":  self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "duration_s":    round(self.duration_s, 3),
            "pass_rate":     self.pass_rate,
            "error":         self.error,
            "run_id":        self.run_id,
        }


# ── SweepResults ──────────────────────────────────────────────────────────────

@dataclass
class SweepResults:
    """Aggregated results across all sweep variants."""

    grid: SweepGrid
    variants: list[SweepVariantResult] = field(default_factory=list)
    total_duration_s: float = 0.0

    def best_by_quality(self) -> SweepVariantResult | None:
        if not self.variants:
            return None
        return max(self.variants, key=lambda v: v.pass_rate)

    def best_by_cost(self) -> SweepVariantResult | None:
        if not self.variants:
            return None
        completed = [v for v in self.variants if v.completed]
        return min(completed, key=lambda v: v.total_cost_usd) if completed else None

    def comparison_table(self, *, ascii: bool = False) -> str:
        """Render a side-by-side parameter comparison table."""
        sep = "-" if ascii else "─"
        if not self.variants:
            return "(no results)"

        param_keys = list(self.variants[0].params.keys()) if self.variants else []
        param_cols = "  ".join(f"{k:<14}" for k in param_keys)
        header = f"  {param_cols}  {'Tokens':>8}  {'Cost USD':>10}  {'Time':>6}  {'Pass':>5}  Status"
        divider = sep * max(len(header), 60)

        lines = [header, divider]
        for v in sorted(self.variants, key=lambda x: -x.pass_rate):
            param_vals = "  ".join(f"{str(v.params.get(k,''))[:14]:<14}" for k in param_keys)
            status = "OK" if v.completed else f"ERR:{v.error[:20]}"
            lines.append(
                f"  {param_vals}  {v.total_tokens:>8,}  {v.total_cost_usd:>10.5f}  "
                f"{v.duration_s:>5.1f}s  {v.pass_rate:>5.1%}  {status}"
            )
        lines.append(divider)
        lines.append(f"  Total variants: {len(self.variants)}  "
                     f"Wall time: {self.total_duration_s:.1f}s")
        return "\n".join(lines)

    def to_benchmark(self) -> Any:
        """Convert to a ModelBenchmark for Pareto analysis."""
        from meshflow.eval.pareto import ModelBenchmark
        bench = ModelBenchmark()
        for v in self.variants:
            model = v.params.get("model", f"variant_{v.variant_id[:6]}")
            bench.add_run(
                model=str(model),
                tokens=v.total_tokens,
                cost_usd=v.total_cost_usd,
                pass_rate=v.pass_rate,
                run_id=v.run_id,
                metadata=v.to_dict(),
            )
        return bench

    def to_list(self) -> list[dict[str, Any]]:
        return [v.to_dict() for v in self.variants]


# ── WorkflowSweep ─────────────────────────────────────────────────────────────

class WorkflowSweep:
    """Run a workflow across a parameter grid and collect comparative results.

    Parameters
    ----------
    workflow_yaml:   Path to the workflow YAML.
    grid:            :class:`SweepGrid` defining parameter combinations.
    task:            Default task string (overridden per variant if ``task`` is in grid).
    concurrency:     Max parallel variant executions.
    ledger_db:       SQLite ledger path for run storage.
    node_registry:   Optional node registry for YAML loading.
    """

    def __init__(
        self,
        workflow_yaml: str,
        grid: SweepGrid,
        *,
        task: str = "Execute workflow",
        concurrency: int = 4,
        ledger_db: str = "meshflow_sweep.db",
        node_registry: dict[str, Any] | None = None,
    ) -> None:
        self._yaml = workflow_yaml
        self._grid = grid
        self._default_task = task
        self._concurrency = concurrency
        self._ledger_db = ledger_db
        self._registry = node_registry

    async def run(self, *, progress_callback: Any = None) -> SweepResults:
        """Execute all grid combinations and return aggregated results."""
        combos = self._grid.combinations()
        sem = asyncio.Semaphore(self._concurrency)
        t0 = time.monotonic()
        results: list[SweepVariantResult] = []

        async def _run_one(combo: dict[str, Any]) -> SweepVariantResult:
            async with sem:
                return await self._run_variant(combo)

        variant_tasks = [_run_one(combo) for combo in combos]
        completed_results = await asyncio.gather(*variant_tasks, return_exceptions=True)

        for combo, result in zip(combos, completed_results):
            if isinstance(result, Exception):
                results.append(SweepVariantResult(
                    variant_id=uuid.uuid4().hex[:8],
                    params=combo,
                    output="",
                    completed=False,
                    total_tokens=0,
                    total_cost_usd=0.0,
                    duration_s=0.0,
                    pass_rate=0.0,
                    error=str(result),
                ))
            else:
                results.append(result)
            if progress_callback:
                progress_callback(len(results), len(combos))

        return SweepResults(
            grid=self._grid,
            variants=results,
            total_duration_s=round(time.monotonic() - t0, 2),
        )

    async def _run_variant(self, params: dict[str, Any]) -> SweepVariantResult:
        from meshflow.core.workflow import WorkflowDefinition
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.schemas import Policy

        variant_id = uuid.uuid4().hex[:8]
        run_id = f"sweep_{variant_id}"
        task = str(params.get("task", self._default_task))

        # Load workflow fresh per variant to avoid shared state
        wf = WorkflowDefinition.from_yaml(self._yaml, self._registry)

        # Apply model override if present
        model_override = params.get("model", "")
        if model_override:
            _patch_workflow_models(wf, str(model_override))

        # Apply any policy overrides
        policy_override = params.get("policy", None)
        if policy_override:
            wf.policy = policy_override

        ledger = ReplayLedger(self._ledger_db)
        runtime = StepRuntime(policy=wf.policy, run_id=run_id, ledger=ledger)

        t0 = time.monotonic()
        try:
            result = await wf.run(
                task=task,
                runtime=runtime,
                context={k: v for k, v in params.items()
                         if k not in ("task", "model", "policy")},
            )
            duration = round(time.monotonic() - t0, 3)
            pass_rate = 1.0 if result.completed else 0.0

            return SweepVariantResult(
                variant_id=variant_id,
                params=params,
                output=result.output,
                completed=result.completed,
                total_tokens=result.total_tokens,
                total_cost_usd=result.total_cost_usd,
                duration_s=duration,
                pass_rate=pass_rate,
                run_id=run_id,
            )
        except Exception as exc:
            duration = round(time.monotonic() - t0, 3)
            return SweepVariantResult(
                variant_id=variant_id,
                params=params,
                output="",
                completed=False,
                total_tokens=0,
                total_cost_usd=0.0,
                duration_s=duration,
                pass_rate=0.0,
                error=str(exc),
                run_id=run_id,
            )


def _patch_workflow_models(wf: Any, model: str) -> None:
    """Swap every native node's model in *wf* to *model*."""
    for node in wf._nodes.values():
        if node.kind.value == "native" and node._runner is not None:
            closure = getattr(node._runner, "__closure__", None)
            if closure is None:
                continue
            for cell in closure:
                try:
                    val = cell.cell_contents
                    if hasattr(val, "config") and hasattr(val.config, "model"):
                        val.config.model = model
                except ValueError:
                    pass


__all__ = ["WorkflowSweep", "SweepGrid", "SweepResults", "SweepVariantResult"]
