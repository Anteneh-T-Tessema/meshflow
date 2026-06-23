"""BenchmarkGate — programmatic CI gate for competitive benchmark results.

Integrates with benchmarks/competitive_bench.py to:
- Assert MeshFlow wins on throughput vs. all installed/simulated competitors
- Save and load JSON baselines for regression detection
- Compare current results against a saved baseline

Usage in pytest::

    from benchmarks.competitive_bench import run_benchmarks
    from meshflow.eval.bench_gate import BenchmarkGate

    def test_meshflow_wins_single_agent():
        report = run_benchmarks(n_runs=20, frameworks=["meshflow", "langgraph", "crewai"])
        BenchmarkGate.assert_meshflow_wins(report, scenario="single_agent")

Usage in CI (shell)::

    python benchmarks/competitive_bench.py --ci --runs 50
    # exits 1 if MeshFlow loses any scenario
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from benchmarks.competitive_bench import BenchmarkReport, FrameworkResult


class BenchmarkGateError(Exception):
    """Raised when MeshFlow fails to meet a benchmark threshold."""


class BenchmarkGate:
    """Static helpers for enforcing benchmark quality gates."""

    # Maximum allowed throughput regression vs. a saved baseline (10 %).
    DEFAULT_REGRESSION_THRESHOLD = 0.10

    @staticmethod
    def assert_meshflow_wins(
        report: "BenchmarkReport",
        scenario: str = "single_agent",
        *,
        min_speedup: float = 1.0,
    ) -> None:
        """Assert MeshFlow has the highest throughput in *scenario*.

        Parameters
        ----------
        report:
            Result of :func:`benchmarks.competitive_bench.run_benchmarks`.
        scenario:
            Scenario key — ``"single_agent"``, ``"3_node_pipeline"``, or
            ``"governance_overhead"``.
        min_speedup:
            Minimum ratio MeshFlow must achieve over each competitor.
            Default 1.0 means MeshFlow must be at least as fast.

        Raises
        ------
        BenchmarkGateError
            If any competitor meets or exceeds MeshFlow throughput.
        """
        mf = next(
            (r for r in report.results if r.framework == "meshflow" and r.scenario == scenario),
            None,
        )
        if mf is None:
            raise BenchmarkGateError(
                f"No MeshFlow result found for scenario '{scenario}'. "
                "Did you include 'meshflow' in the frameworks list?"
            )
        if mf.throughput_rps == 0:
            raise BenchmarkGateError(
                f"MeshFlow throughput is 0 for scenario '{scenario}'."
            )

        failures: list[str] = []
        for r in report.results:
            if r.framework == "meshflow" or r.scenario != scenario:
                continue
            if r.throughput_rps <= 0 or not r.installed:
                continue
            ratio = mf.throughput_rps / r.throughput_rps
            if ratio < min_speedup:
                failures.append(
                    f"{r.framework}: MeshFlow {mf.throughput_rps:.1f} rps vs "
                    f"{r.framework} {r.throughput_rps:.1f} rps "
                    f"(ratio {ratio:.2f} < required {min_speedup:.2f})"
                )

        if failures:
            raise BenchmarkGateError(
                f"MeshFlow benchmark gate FAILED for scenario '{scenario}':\n"
                + "\n".join(f"  • {f}" for f in failures)
            )

    @staticmethod
    def save_baseline(report: "BenchmarkReport", path: str) -> None:
        """Serialize *report* to a JSON file at *path*.

        Creates parent directories as needed.
        """
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = {
            "timestamp": report.timestamp,
            "meshflow_version": report.meshflow_version,
            "runs_per_scenario": report.runs_per_scenario,
            "results": [asdict(r) for r in report.results],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    @staticmethod
    def load_baseline(path: str) -> "BenchmarkReport":
        """Load a previously saved baseline JSON and return a :class:`BenchmarkReport`.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        """
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks"))
        from benchmarks.competitive_bench import BenchmarkReport, FrameworkResult  # type: ignore[import]

        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        results = [FrameworkResult(**r) for r in data.get("results", [])]
        return BenchmarkReport(
            timestamp=data["timestamp"],
            meshflow_version=data["meshflow_version"],
            runs_per_scenario=data.get("runs_per_scenario", 0),
            results=results,
        )

    @staticmethod
    def compare_baseline(
        current: "BenchmarkReport",
        baseline: "BenchmarkReport | str",
        *,
        threshold: float = DEFAULT_REGRESSION_THRESHOLD,
    ) -> list[str]:
        """Compare *current* results against *baseline* and return regression messages.

        A regression is detected when current throughput drops by more than
        *threshold* fraction below the baseline (e.g. 0.10 = 10 % degradation).

        Parameters
        ----------
        current:
            Freshly collected :class:`BenchmarkReport`.
        baseline:
            Either a previously saved :class:`BenchmarkReport` or a file path
            to load from.
        threshold:
            Allowed fractional throughput degradation before flagging a regression.

        Returns
        -------
        list[str]
            Human-readable regression messages.  Empty list means no regressions.
        """
        if isinstance(baseline, str):
            baseline = BenchmarkGate.load_baseline(baseline)

        # Build lookup: (framework, scenario) → throughput_rps
        baseline_map: dict[tuple[str, str], float] = {
            (r.framework, r.scenario): r.throughput_rps
            for r in baseline.results
            if r.throughput_rps > 0
        }

        regressions: list[str] = []
        for r in current.results:
            key = (r.framework, r.scenario)
            base_rps = baseline_map.get(key)
            if base_rps is None or base_rps <= 0 or r.throughput_rps <= 0:
                continue
            drop = (base_rps - r.throughput_rps) / base_rps
            if drop > threshold:
                regressions.append(
                    f"{r.framework}/{r.scenario}: {r.throughput_rps:.1f} rps vs "
                    f"baseline {base_rps:.1f} rps "
                    f"({drop * 100:.1f}% regression — threshold {threshold * 100:.0f}%)"
                )

        return regressions

    @staticmethod
    def assert_no_regression(
        current: "BenchmarkReport",
        baseline: "BenchmarkReport | str",
        *,
        threshold: float = DEFAULT_REGRESSION_THRESHOLD,
    ) -> None:
        """Assert no throughput regressions vs. *baseline*.

        Raises
        ------
        BenchmarkGateError
            On any regression exceeding *threshold*.
        """
        regressions = BenchmarkGate.compare_baseline(current, baseline, threshold=threshold)
        if regressions:
            raise BenchmarkGateError(
                "Performance regressions detected:\n"
                + "\n".join(f"  • {r}" for r in regressions)
            )

    @staticmethod
    def summary(report: "BenchmarkReport") -> str:
        """Return a one-paragraph human-readable benchmark summary."""
        lines: list[str] = [
            f"MeshFlow v{report.meshflow_version} Benchmark Summary",
            f"Scenarios: {report.runs_per_scenario} runs each",
            "",
        ]
        for scenario in ("single_agent", "3_node_pipeline", "governance_overhead"):
            scenario_results = [r for r in report.results if r.scenario == scenario]
            if not scenario_results:
                continue
            lines.append(f"  [{scenario}]")
            for r in sorted(scenario_results, key=lambda x: -x.throughput_rps):
                if r.throughput_rps > 0:
                    lines.append(f"    {r.framework:12s}  {r.throughput_rps:>8.1f} rps  p50={r.p50_ms:.1f}ms")
                else:
                    lines.append(f"    {r.framework:12s}  (overhead-only scenario)")
            lines.append("")
        return "\n".join(lines)
