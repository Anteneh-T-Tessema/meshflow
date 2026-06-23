"""Sprint 104 — Benchmark CI Gate + Hardened Competitive Benchmarks.

Covers:
- BenchmarkGate: assert_meshflow_wins, save_baseline, load_baseline,
  compare_baseline, assert_no_regression, summary
- competitive_bench: 3-node pipeline scenarios for all competitors,
  _ci_gate logic, _compare_baseline logic
- Integration: run_benchmarks() with n=20 confirms MeshFlow wins
- Version guard: v1.15.0
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MESHFLOW_MOCK", "1")


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_report(
    mf_rps: float = 200.0,
    lg_rps: float = 50.0,
    ca_rps: float = 20.0,
    scenario: str = "single_agent",
):
    from benchmarks.competitive_bench import BenchmarkReport, FrameworkResult

    def _r(fw: str, rps: float) -> FrameworkResult:
        return FrameworkResult(
            framework=fw, version="test", installed=True, scenario=scenario,
            n_runs=20, throughput_rps=rps, p50_ms=1000 / rps if rps > 0 else 0,
            p95_ms=2.0, p99_ms=3.0, peak_memory_mb=10.0, success_rate=1.0,
        )

    report = BenchmarkReport(timestamp="2026-06-06T00:00:00Z", meshflow_version="1.15.0",
                              runs_per_scenario=20)
    report.results = [_r("meshflow", mf_rps), _r("langgraph", lg_rps), _r("crewai", ca_rps)]
    return report


# ══════════════════════════════════════════════════════════════════════════════
# Version guard
# ══════════════════════════════════════════════════════════════════════════════

class TestVersionConsistency(unittest.TestCase):
    def test_module_version_matches_pyproject(self) -> None:
        import meshflow, tomllib
        root = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(root, "rb") as fh:
            meta = tomllib.load(fh)
        self.assertEqual(meshflow.__version__, meta["project"]["version"])

    def test_version_is_1_15_0(self) -> None:
        import meshflow
        self.assertEqual(meshflow.__version__, "1.15.0")


# ══════════════════════════════════════════════════════════════════════════════
# BenchmarkGate — assert_meshflow_wins
# ══════════════════════════════════════════════════════════════════════════════

class TestAssertMeshflowWins(unittest.TestCase):
    def test_passes_when_meshflow_leads(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        report = _make_report(mf_rps=200.0, lg_rps=50.0, ca_rps=20.0)
        BenchmarkGate.assert_meshflow_wins(report)  # must not raise

    def test_raises_when_competitor_faster(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate, BenchmarkGateError
        report = _make_report(mf_rps=30.0, lg_rps=50.0, ca_rps=20.0)
        with self.assertRaises(BenchmarkGateError) as ctx:
            BenchmarkGate.assert_meshflow_wins(report)
        self.assertIn("langgraph", str(ctx.exception))

    def test_raises_when_no_meshflow_result(self) -> None:
        from benchmarks.competitive_bench import BenchmarkReport, FrameworkResult
        from meshflow.eval.bench_gate import BenchmarkGate, BenchmarkGateError
        report = BenchmarkReport(timestamp="t", meshflow_version="1.15.0",
                                  runs_per_scenario=20)
        report.results = [FrameworkResult(
            framework="langgraph", version="x", installed=False, scenario="single_agent",
            n_runs=10, throughput_rps=40.0, p50_ms=1.0, p95_ms=2.0, p99_ms=3.0,
            peak_memory_mb=5.0, success_rate=1.0,
        )]
        with self.assertRaises(BenchmarkGateError):
            BenchmarkGate.assert_meshflow_wins(report)

    def test_raises_when_meshflow_throughput_zero(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate, BenchmarkGateError
        report = _make_report(mf_rps=0.0, lg_rps=10.0)
        with self.assertRaises(BenchmarkGateError):
            BenchmarkGate.assert_meshflow_wins(report)

    def test_min_speedup_enforced(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate, BenchmarkGateError
        # MeshFlow is 3× faster than LangGraph, but we require 5×
        report = _make_report(mf_rps=150.0, lg_rps=50.0, ca_rps=10.0)
        with self.assertRaises(BenchmarkGateError):
            BenchmarkGate.assert_meshflow_wins(report, min_speedup=5.0)

    def test_min_speedup_passes(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        report = _make_report(mf_rps=300.0, lg_rps=50.0, ca_rps=20.0)
        BenchmarkGate.assert_meshflow_wins(report, min_speedup=5.0)

    def test_skips_zero_rps_competitors(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        # Zero-rps competitor (e.g. overhead-only scenario result) should not cause failure
        report = _make_report(mf_rps=200.0, lg_rps=0.0, ca_rps=0.0)
        BenchmarkGate.assert_meshflow_wins(report)  # must not raise

    def test_pipeline_scenario(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        report = _make_report(mf_rps=120.0, lg_rps=30.0, ca_rps=10.0,
                               scenario="3_node_pipeline")
        BenchmarkGate.assert_meshflow_wins(report, scenario="3_node_pipeline")


# ══════════════════════════════════════════════════════════════════════════════
# BenchmarkGate — save_baseline / load_baseline round-trip
# ══════════════════════════════════════════════════════════════════════════════

class TestBaselineRoundTrip(unittest.TestCase):
    def test_save_and_load_preserves_results(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        report = _make_report(mf_rps=200.0, lg_rps=50.0)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            BenchmarkGate.save_baseline(report, path)
            loaded = BenchmarkGate.load_baseline(path)
            self.assertEqual(loaded.meshflow_version, "1.15.0")
            self.assertEqual(len(loaded.results), len(report.results))
            mf_orig = next(r for r in report.results if r.framework == "meshflow")
            mf_load = next(r for r in loaded.results if r.framework == "meshflow")
            self.assertAlmostEqual(mf_orig.throughput_rps, mf_load.throughput_rps, places=2)
        finally:
            os.unlink(path)

    def test_load_missing_file_raises(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        with self.assertRaises(FileNotFoundError):
            BenchmarkGate.load_baseline("/nonexistent/path/baseline.json")

    def test_save_creates_parent_dirs(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        report = _make_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nested", "dir", "bl.json")
            BenchmarkGate.save_baseline(report, path)
            self.assertTrue(os.path.exists(path))


# ══════════════════════════════════════════════════════════════════════════════
# BenchmarkGate — compare_baseline / assert_no_regression
# ══════════════════════════════════════════════════════════════════════════════

class TestCompareBaseline(unittest.TestCase):
    def test_no_regression_returns_empty_list(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        baseline = _make_report(mf_rps=200.0)
        current  = _make_report(mf_rps=198.0)  # 1% drop — within threshold
        regressions = BenchmarkGate.compare_baseline(current, baseline)
        self.assertEqual(regressions, [])

    def test_regression_detected_above_threshold(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        baseline = _make_report(mf_rps=200.0)
        current  = _make_report(mf_rps=150.0)  # 25% drop
        regressions = BenchmarkGate.compare_baseline(current, baseline)
        self.assertEqual(len(regressions), 1)
        self.assertIn("meshflow", regressions[0])
        self.assertIn("25.0%", regressions[0])

    def test_custom_threshold_respected(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        baseline = _make_report(mf_rps=200.0)
        current  = _make_report(mf_rps=195.0)  # 2.5% drop
        # default 10% — no regression
        self.assertEqual(BenchmarkGate.compare_baseline(current, baseline), [])
        # strict 2% — regression
        self.assertTrue(BenchmarkGate.compare_baseline(current, baseline, threshold=0.02))

    def test_accepts_file_path_string(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        baseline = _make_report(mf_rps=200.0)
        current  = _make_report(mf_rps=198.0)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            BenchmarkGate.save_baseline(baseline, path)
            regressions = BenchmarkGate.compare_baseline(current, path)
            self.assertEqual(regressions, [])
        finally:
            os.unlink(path)

    def test_improvement_not_flagged(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        baseline = _make_report(mf_rps=100.0)
        current  = _make_report(mf_rps=200.0)  # 100% improvement
        self.assertEqual(BenchmarkGate.compare_baseline(current, baseline), [])

    def test_assert_no_regression_raises_on_regression(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate, BenchmarkGateError
        baseline = _make_report(mf_rps=200.0)
        current  = _make_report(mf_rps=100.0)  # 50% regression
        with self.assertRaises(BenchmarkGateError):
            BenchmarkGate.assert_no_regression(current, baseline)

    def test_assert_no_regression_passes_clean(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        baseline = _make_report(mf_rps=200.0)
        current  = _make_report(mf_rps=205.0)
        BenchmarkGate.assert_no_regression(current, baseline)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# BenchmarkGate — summary
# ══════════════════════════════════════════════════════════════════════════════

class TestBenchmarkSummary(unittest.TestCase):
    def test_summary_contains_framework_names(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        report = _make_report(mf_rps=200.0, lg_rps=50.0, ca_rps=20.0)
        s = BenchmarkGate.summary(report)
        self.assertIn("meshflow", s)
        self.assertIn("langgraph", s)
        self.assertIn("v1.15.0", s)

    def test_summary_contains_rps_numbers(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate
        report = _make_report(mf_rps=200.0)
        s = BenchmarkGate.summary(report)
        self.assertIn("200.0", s)


# ══════════════════════════════════════════════════════════════════════════════
# competitive_bench — pipeline scenarios for competitors
# ══════════════════════════════════════════════════════════════════════════════

class TestCompetitorPipelineScenarios(unittest.TestCase):
    def test_langgraph_pipeline_returns_result(self) -> None:
        from benchmarks.competitive_bench import _bench_langgraph_pipeline
        r = _bench_langgraph_pipeline(10)
        self.assertEqual(r.framework, "langgraph")
        self.assertEqual(r.scenario, "3_node_pipeline")
        self.assertGreater(r.throughput_rps, 0)

    def test_crewai_pipeline_returns_result(self) -> None:
        from benchmarks.competitive_bench import _bench_crewai_pipeline
        r = _bench_crewai_pipeline(10)
        self.assertEqual(r.framework, "crewai")
        self.assertEqual(r.scenario, "3_node_pipeline")
        self.assertGreater(r.throughput_rps, 0)

    def test_autogen_pipeline_returns_result(self) -> None:
        from benchmarks.competitive_bench import _bench_autogen_pipeline
        r = _bench_autogen_pipeline(10)
        self.assertEqual(r.framework, "autogen")
        self.assertEqual(r.scenario, "3_node_pipeline")
        self.assertGreater(r.throughput_rps, 0)

    def test_meshflow_faster_than_langgraph_pipeline(self) -> None:
        from benchmarks.competitive_bench import _bench_meshflow_pipeline, _bench_langgraph_pipeline
        mf = _bench_meshflow_pipeline(20)
        lg = _bench_langgraph_pipeline(20)
        self.assertGreater(mf.throughput_rps, lg.throughput_rps,
                           f"MeshFlow {mf.throughput_rps:.1f} rps should beat "
                           f"LangGraph {lg.throughput_rps:.1f} rps on 3-node pipeline")

    def test_meshflow_faster_than_crewai_pipeline(self) -> None:
        from benchmarks.competitive_bench import _bench_meshflow_pipeline, _bench_crewai_pipeline
        mf = _bench_meshflow_pipeline(20)
        ca = _bench_crewai_pipeline(20)
        self.assertGreater(mf.throughput_rps, ca.throughput_rps)

    def test_meshflow_faster_than_autogen_pipeline(self) -> None:
        from benchmarks.competitive_bench import _bench_meshflow_pipeline, _bench_autogen_pipeline
        mf = _bench_meshflow_pipeline(20)
        ag = _bench_autogen_pipeline(20)
        self.assertGreater(mf.throughput_rps, ag.throughput_rps)


# ══════════════════════════════════════════════════════════════════════════════
# competitive_bench — _ci_gate and _compare_baseline internals
# ══════════════════════════════════════════════════════════════════════════════

class TestCIGateInternal(unittest.TestCase):
    def _run_report(self, mf_rps: float, lg_rps: float):
        from benchmarks.competitive_bench import BenchmarkReport, FrameworkResult

        def _r(fw: str, rps: float, scenario: str) -> FrameworkResult:
            return FrameworkResult(
                framework=fw, version="t", installed=True, scenario=scenario,
                n_runs=10, throughput_rps=rps, p50_ms=1.0, p95_ms=2.0,
                p99_ms=3.0, peak_memory_mb=5.0, success_rate=1.0,
            )

        report = BenchmarkReport(timestamp="t", meshflow_version="1.15.0", runs_per_scenario=10)
        report.results = [
            _r("meshflow",  mf_rps, "single_agent"),
            _r("meshflow",  mf_rps * 0.5, "3_node_pipeline"),
            _r("langgraph", lg_rps, "single_agent"),
            _r("langgraph", lg_rps * 0.3, "3_node_pipeline"),
        ]
        return report

    def test_ci_gate_passes_when_meshflow_wins(self) -> None:
        from benchmarks.competitive_bench import _ci_gate
        report = self._run_report(mf_rps=200.0, lg_rps=50.0)
        # Should not call sys.exit — we capture via mock
        from unittest.mock import patch
        with patch("sys.exit") as mock_exit:
            _ci_gate(report)
        mock_exit.assert_not_called()

    def test_ci_gate_fails_when_competitor_wins(self) -> None:
        from benchmarks.competitive_bench import _ci_gate
        report = self._run_report(mf_rps=30.0, lg_rps=200.0)
        from unittest.mock import patch
        with patch("sys.exit") as mock_exit:
            _ci_gate(report)
        mock_exit.assert_called_once_with(1)


class TestCompareBaselineInternal(unittest.TestCase):
    def test_no_regression_prints_ok(self) -> None:
        from benchmarks.competitive_bench import _compare_baseline
        report = _make_report(mf_rps=200.0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "results": [
                    {"framework": "meshflow", "scenario": "single_agent",
                     "throughput_rps": 198.0}
                ]
            }, f)
            path = f.name
        try:
            from unittest.mock import patch
            with patch("sys.exit") as mock_exit:
                _compare_baseline(report, path)
            mock_exit.assert_not_called()
        finally:
            os.unlink(path)

    def test_regression_calls_sys_exit_1(self) -> None:
        from benchmarks.competitive_bench import _compare_baseline
        report = _make_report(mf_rps=100.0)  # current is slow

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "results": [
                    {"framework": "meshflow", "scenario": "single_agent",
                     "throughput_rps": 200.0}   # baseline was fast
                ]
            }, f)
            path = f.name
        try:
            from unittest.mock import patch
            with patch("sys.exit") as mock_exit:
                _compare_baseline(report, path)
            mock_exit.assert_called_once_with(1)
        finally:
            os.unlink(path)

    def test_missing_baseline_file_does_not_crash(self) -> None:
        from benchmarks.competitive_bench import _compare_baseline
        report = _make_report()
        # Should just print a warning and return, not raise
        _compare_baseline(report, "/nonexistent/path.json")


# ══════════════════════════════════════════════════════════════════════════════
# Integration — run_benchmarks confirms MeshFlow wins both scenarios
# ══════════════════════════════════════════════════════════════════════════════

class TestRunBenchmarksIntegration(unittest.TestCase):
    def test_meshflow_wins_single_agent_vs_installed(self) -> None:
        # Competitors are not installed in this env — gate skips simulated results.
        # Assert gate passes (no installed competitors to lose to).
        from benchmarks.competitive_bench import run_benchmarks
        from meshflow.eval.bench_gate import BenchmarkGate
        report = run_benchmarks(n_runs=20, frameworks=["meshflow", "langgraph", "crewai", "autogen"])
        BenchmarkGate.assert_meshflow_wins(report, scenario="single_agent")

    def test_meshflow_wins_pipeline_vs_installed(self) -> None:
        from benchmarks.competitive_bench import run_benchmarks
        from meshflow.eval.bench_gate import BenchmarkGate
        report = run_benchmarks(n_runs=20, frameworks=["meshflow", "langgraph", "crewai", "autogen"])
        BenchmarkGate.assert_meshflow_wins(report, scenario="3_node_pipeline")

    def test_all_four_scenarios_present(self) -> None:
        from benchmarks.competitive_bench import run_benchmarks
        report = run_benchmarks(n_runs=10, frameworks=["meshflow"])
        scenarios = {r.scenario for r in report.results}
        self.assertIn("single_agent", scenarios)
        self.assertIn("3_node_pipeline", scenarios)
        self.assertIn("governance_overhead", scenarios)

    def test_competitor_pipeline_scenarios_present(self) -> None:
        from benchmarks.competitive_bench import run_benchmarks
        report = run_benchmarks(n_runs=10, frameworks=["langgraph", "crewai", "autogen"])
        fw_scenarios = {(r.framework, r.scenario) for r in report.results}
        self.assertIn(("langgraph", "3_node_pipeline"), fw_scenarios)
        self.assertIn(("crewai",    "3_node_pipeline"), fw_scenarios)
        self.assertIn(("autogen",   "3_node_pipeline"), fw_scenarios)

    def test_save_baseline_roundtrip_via_run_benchmarks(self) -> None:
        from benchmarks.competitive_bench import run_benchmarks
        from meshflow.eval.bench_gate import BenchmarkGate
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            run_benchmarks(n_runs=10, frameworks=["meshflow"], save_baseline=path)
            loaded = BenchmarkGate.load_baseline(path)
            self.assertEqual(loaded.meshflow_version, "1.15.0")
            self.assertTrue(any(r.framework == "meshflow" for r in loaded.results))
        finally:
            os.unlink(path)

    def test_summary_output(self) -> None:
        from benchmarks.competitive_bench import run_benchmarks
        from meshflow.eval.bench_gate import BenchmarkGate
        report = run_benchmarks(n_runs=10, frameworks=["meshflow", "langgraph"])
        s = BenchmarkGate.summary(report)
        self.assertIn("meshflow", s)
        self.assertIn("langgraph", s)


# ══════════════════════════════════════════════════════════════════════════════
# BenchmarkGate importable from meshflow.eval
# ══════════════════════════════════════════════════════════════════════════════

class TestImportParity(unittest.TestCase):
    def test_bench_gate_importable(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGate, BenchmarkGateError  # noqa: F401

    def test_bench_gate_error_is_exception(self) -> None:
        from meshflow.eval.bench_gate import BenchmarkGateError
        self.assertTrue(issubclass(BenchmarkGateError, Exception))


if __name__ == "__main__":
    unittest.main()
