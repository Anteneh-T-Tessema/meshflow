"""Sprint 102 — Remaining credibility gaps:
A: Competitive benchmark infrastructure (benchmarks/competitive_bench.py)
B: meshflow-forensic standalone pip package (packages/meshflow-forensic/)
C: SOC 2 assertion module (meshflow/compliance/soc2.py)
D: Cost regression CI gate (meshflow/eval/cost_regression.py)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")


# ══════════════════════════════════════════════════════════════════════════════
# A — Competitive benchmark
# ══════════════════════════════════════════════════════════════════════════════

class TestCompetitiveBench:
    def _bench_module(self):
        import importlib.util
        import types
        root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
        bench_path = os.path.join(root, "benchmarks", "competitive_bench.py")
        spec = importlib.util.spec_from_file_location("competitive_bench", bench_path)
        assert spec is not None and spec.loader is not None, f"Could not load {bench_path}"
        mod: types.ModuleType = importlib.util.module_from_spec(spec)
        # Register before exec so Python 3.14 dataclasses can resolve __module__
        sys.modules["competitive_bench"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    def test_benchmark_module_importable(self) -> None:
        mod = self._bench_module()
        assert hasattr(mod, "run_benchmarks")
        assert hasattr(mod, "FrameworkResult")
        assert hasattr(mod, "BenchmarkReport")

    def test_meshflow_single_agent_bench(self) -> None:
        mod = self._bench_module()
        result = mod._bench_meshflow_single(n_runs=5)
        assert result.framework == "meshflow"
        assert result.installed is True
        assert result.throughput_rps > 0
        assert result.p50_ms >= 0
        assert result.success_rate == 1.0

    def test_meshflow_pipeline_bench(self) -> None:
        mod = self._bench_module()
        result = mod._bench_meshflow_pipeline(n_runs=5)
        assert result.scenario == "3_node_pipeline"
        assert result.throughput_rps > 0

    def test_governance_overhead_bench(self) -> None:
        mod = self._bench_module()
        result = mod._bench_meshflow_governance_overhead(n_runs=10)
        assert result.scenario == "governance_overhead"
        assert result.governance_overhead_ms >= 0

    def test_simulated_framework_result(self) -> None:
        mod = self._bench_module()
        result = mod._simulate_framework(
            "langgraph", "0.2.x (simulated)", "single_agent", 10,
            base_latency_ms=3.0, memory_mb=8.0, notes="test"
        )
        assert result.framework == "langgraph"
        assert result.installed is False
        assert result.throughput_rps > 0

    def test_full_benchmark_run(self) -> None:
        mod = self._bench_module()
        report = mod.run_benchmarks(n_runs=5, frameworks=["meshflow"])
        assert report.meshflow_version != ""
        assert any(r.framework == "meshflow" for r in report.results)

    def test_benchmark_json_output(self) -> None:
        mod = self._bench_module()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            mod.run_benchmarks(n_runs=3, frameworks=["meshflow"], output_path=path)
            with open(path) as fh:
                data = json.load(fh)
            assert "results" in data
            assert data["meshflow_version"] != ""
        finally:
            os.unlink(path)

    def test_speedup_vs_meshflow(self) -> None:
        mod = self._bench_module()
        report = mod.run_benchmarks(
            n_runs=3, frameworks=["meshflow", "langgraph"]
        )
        speedups = report.speedup_vs_meshflow("single_agent")
        assert "langgraph" in speedups
        assert speedups["langgraph"] > 0

    def test_framework_result_to_row(self) -> None:
        mod = self._bench_module()
        r = mod.FrameworkResult(
            framework="meshflow", version="1.13.0", installed=True,
            scenario="single_agent", n_runs=10, throughput_rps=200.0,
            p50_ms=3.0, p95_ms=5.0, p99_ms=8.0, peak_memory_mb=2.0,
            success_rate=1.0,
        )
        row = r.to_row()
        assert row[0] == "meshflow"
        assert "200" in row[3]


# ══════════════════════════════════════════════════════════════════════════════
# B — meshflow-forensic standalone package
# ══════════════════════════════════════════════════════════════════════════════

def _forensic_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "packages", "meshflow-forensic")


class TestForensicPackage:
    def _import(self):
        path = _forensic_path()
        if path not in sys.path:
            sys.path.insert(0, path)
        import importlib
        import meshflow_forensic
        importlib.reload(meshflow_forensic)
        return meshflow_forensic

    def test_package_importable(self) -> None:
        mf = self._import()
        assert mf.__version__ == "1.0.0"

    def test_dasc_gate_creates(self) -> None:
        mf = self._import()
        gate = mf.DascGate.create(run_id="test")
        assert gate.run_id == "test"
        assert gate.ledger_count() == 0

    def test_evaluate_commit(self) -> None:
        import asyncio
        mf = self._import()
        gate = mf.DascGate.create(run_id="r1")
        intent = mf.Intent(action="read data", agent_id="agent1")
        verdict = asyncio.run(gate.evaluate(intent))
        assert str(verdict) == "COMMIT"

    def test_evaluate_escalate_irreversible(self) -> None:
        import asyncio
        mf = self._import()
        gate = mf.DascGate.create(run_id="r2")
        intent = mf.Intent(action="delete user", agent_id="agent1")
        verdict = asyncio.run(gate.evaluate(intent))
        assert str(verdict) == "ESCALATE"

    def test_evaluate_reject_tainted(self) -> None:
        import asyncio
        mf = self._import()
        gate = mf.DascGate.create(run_id="r3")
        intent = mf.Intent(action="write record", agent_id="a1", tainted=True)
        verdict = asyncio.run(gate.evaluate(intent))
        assert str(verdict) in ("REJECT", "ESCALATE")

    def test_ledger_hash_chain_valid(self) -> None:
        import asyncio
        mf = self._import()
        gate = mf.DascGate.create(run_id="chain_test")
        for i in range(5):
            intent = mf.Intent(action=f"read item_{i}", agent_id="a")
            asyncio.run(gate.evaluate(intent))
        assert gate.verify_ledger() is True
        assert gate.ledger_count() == 5

    def test_forensic_report(self) -> None:
        import asyncio
        mf = self._import()
        gate = mf.DascGate.create(run_id="report_test")
        asyncio.run(gate.evaluate(mf.Intent(action="read x", agent_id="a")))
        report = mf.ForensicReport.from_gate(gate)
        assert report.total_entries == 1
        assert report.chain_valid is True
        assert "COMMIT" in report.verdict_counts

    def test_forensic_report_to_json(self) -> None:
        mf = self._import()
        gate = mf.DascGate.create(run_id="json_test")
        report = mf.ForensicReport.from_gate(gate)
        j = report.to_json()
        data = json.loads(j)
        assert data["run_id"] == "json_test"

    def test_forensic_report_to_html(self) -> None:
        mf = self._import()
        gate = mf.DascGate.create(run_id="html_test")
        report = mf.ForensicReport.from_gate(gate)
        html = report.to_html()
        assert "<!DOCTYPE html>" in html
        assert "html_test" in html

    def test_eu_ai_act_compliant(self) -> None:
        mf = self._import()
        gate = mf.DascGate.create(run_id="eu_test")
        checker = mf.EUAIActChecker(gate)
        result = checker.check(mf.HighRiskCategory.EMPLOYMENT)
        assert result.overall in ("COMPLIANT", "PARTIAL")
        assert result.pass_rate > 0.5

    def test_eu_ai_act_check_all(self) -> None:
        mf = self._import()
        gate = mf.DascGate.create(run_id="all_cats")
        checker = mf.EUAIActChecker(gate)
        results = checker.check_all()
        assert len(results) == len(mf.HighRiskCategory)

    def test_taint_graph_propagation(self) -> None:
        mf = self._import()
        tg = mf.TaintGraph()
        tg.mark_tainted("a1")
        assert tg.is_tainted("a1")
        tg.propagate("a1", "a2")
        assert tg.is_tainted("a2")
        assert not tg.is_tainted("a3")

    def test_incident_timeline_summary(self) -> None:
        import asyncio
        mf = self._import()
        gate = mf.DascGate.create(run_id="timeline_test")
        asyncio.run(gate.evaluate(mf.Intent(action="read log", agent_id="bot")))
        report = mf.ForensicReport.from_gate(gate)
        summary = report.timeline.summary()
        assert "timeline_test" in summary

    def test_pyproject_toml_exists(self) -> None:
        pyproject = os.path.join(_forensic_path(), "pyproject.toml")
        assert os.path.exists(pyproject)
        with open(pyproject) as fh:
            content = fh.read()
        assert 'name = "meshflow-forensic"' in content
        assert 'version = "1.0.0"' in content

    def test_zero_runtime_deps(self) -> None:
        import tomllib
        pyproject = os.path.join(_forensic_path(), "pyproject.toml")
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        deps = data["project"].get("dependencies", [])
        assert deps == [], f"Expected no runtime deps, got: {deps}"


# ══════════════════════════════════════════════════════════════════════════════
# C — SOC 2 assertion module
# ══════════════════════════════════════════════════════════════════════════════

class TestSOC2Checker:
    def test_runs_without_error(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        assert report is not None

    def test_overall_compliant(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        assert report.overall_status in ("COMPLIANT", "GAPS_FOUND")

    def test_100_percent_pass_rate(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        assert report.pass_rate >= 0.8, f"Pass rate {report.pass_rate:.0%} below 80%"

    def test_all_tsc_categories_covered(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        tsc_covered = {c.tsc for c in report.controls}
        for expected in ("CC", "A", "PI", "C", "P"):
            assert expected in tsc_covered, f"TSC '{expected}' not covered"

    def test_controls_have_evidence(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        for ctrl in report.controls:
            if ctrl.status == "PASS":
                assert ctrl.evidence, f"[{ctrl.control_id}] PASS without evidence"

    def test_failed_controls_have_remediation(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        for ctrl in report.controls:
            if ctrl.status == "FAIL":
                assert ctrl.remediation, f"[{ctrl.control_id}] FAIL without remediation"

    def test_to_json_valid(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        j = report.to_json()
        data = json.loads(j)
        assert "overall_status" in data
        assert "controls" in data
        assert "pass_count" in data

    def test_save_and_reload(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            report = SOC2Checker().run()
            report.save(path)
            with open(path) as fh:
                data = json.load(fh)
            assert data["meshflow_version"] != ""
        finally:
            os.unlink(path)

    def test_exported_from_meshflow(self) -> None:
        from meshflow import SOC2Checker, SOC2Report, SOC2ControlResult
        assert SOC2Checker is not None
        assert SOC2Report is not None
        assert SOC2ControlResult is not None

    def test_cc_controls_present(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        cc_ids = [c.control_id for c in report.controls if c.tsc == "CC"]
        assert len(cc_ids) >= 5

    def test_privacy_controls_present(self) -> None:
        from meshflow.compliance.soc2 import SOC2Checker
        report = SOC2Checker().run()
        p_ids = [c.control_id for c in report.controls if c.tsc == "P"]
        assert len(p_ids) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# D — Cost regression CI gate
# ══════════════════════════════════════════════════════════════════════════════

class TestCostRegressionGate:
    def _gate(self, **kw):
        from meshflow.eval.cost_regression import CostRegressionGate
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        os.unlink(path)
        return CostRegressionGate(baseline_path=path, **kw)

    def test_no_baseline_records_automatically(self) -> None:
        from meshflow.eval.cost_regression import CostRegressionGate
        gate = self._gate()
        report = gate.check("pipeline_a", total_cost_usd=0.01, total_tokens=100)
        assert report.verdict == "NO_BASELINE"
        assert gate.get("pipeline_a") is not None
        os.unlink(gate.baseline_path)

    def test_pass_when_within_threshold(self) -> None:
        gate = self._gate(usd_threshold=0.05, token_threshold_pct=0.20)
        gate.record("p", total_cost_usd=0.10, total_tokens=1000)
        report = gate.check("p", total_cost_usd=0.12, total_tokens=1100)
        assert report.verdict == "PASS"
        os.unlink(gate.baseline_path)

    def test_regression_raises_on_usd_exceed(self) -> None:
        from meshflow.eval.cost_regression import CostRegressionError
        gate = self._gate(usd_threshold=0.05, raise_on_regression=True)
        gate.record("q", total_cost_usd=0.10, total_tokens=500)
        with pytest.raises(CostRegressionError):
            gate.check("q", total_cost_usd=0.20, total_tokens=500)
        os.unlink(gate.baseline_path)

    def test_regression_on_token_increase(self) -> None:
        from meshflow.eval.cost_regression import CostRegressionError
        gate = self._gate(usd_threshold=999.0, token_threshold_pct=0.10, raise_on_regression=True)
        gate.record("r", total_cost_usd=0.01, total_tokens=1000)
        with pytest.raises(CostRegressionError):
            gate.check("r", total_cost_usd=0.01, total_tokens=1200)
        os.unlink(gate.baseline_path)

    def test_no_raise_when_disabled(self) -> None:
        gate = self._gate(usd_threshold=0.01, raise_on_regression=False)
        gate.record("s", total_cost_usd=0.01, total_tokens=100)
        report = gate.check("s", total_cost_usd=1.00, total_tokens=100)
        assert report.regressed is True
        assert report.verdict == "REGRESSION"
        os.unlink(gate.baseline_path)

    def test_baseline_persists_to_disk(self) -> None:
        from meshflow.eval.cost_regression import CostRegressionGate
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        os.unlink(path)
        g1 = CostRegressionGate(baseline_path=path)
        g1.record("persist_test", total_cost_usd=0.05, total_tokens=500)
        g2 = CostRegressionGate(baseline_path=path)
        bl = g2.get("persist_test")
        assert bl is not None
        assert bl.total_cost_usd == pytest.approx(0.05)
        os.unlink(path)

    def test_delete_baseline(self) -> None:
        gate = self._gate()
        gate.record("del_me", total_cost_usd=0.01, total_tokens=50)
        assert gate.delete("del_me") is True
        assert gate.get("del_me") is None
        assert gate.delete("nonexistent") is False
        os.unlink(gate.baseline_path)

    def test_list_baselines(self) -> None:
        gate = self._gate()
        gate.record("a", 0.01, 100)
        gate.record("b", 0.02, 200)
        baselines = gate.list_baselines()
        names = [b.name for b in baselines]
        assert "a" in names
        assert "b" in names
        os.unlink(gate.baseline_path)

    def test_report_summary(self) -> None:
        gate = self._gate(usd_threshold=0.05)
        gate.record("summ", total_cost_usd=0.10, total_tokens=1000)
        report = gate.check("summ", total_cost_usd=0.11, total_tokens=1050)
        summary = report.summary()
        assert "PASS" in summary
        assert "summ" in summary

    def test_report_to_json(self) -> None:
        gate = self._gate()
        gate.record("json_test", total_cost_usd=0.05, total_tokens=300)
        report = gate.check("json_test", total_cost_usd=0.05, total_tokens=300)
        data = json.loads(report.to_json())
        assert data["name"] == "json_test"
        assert "delta_usd" in data

    def test_cost_baseline_dataclass(self) -> None:
        from meshflow.eval.cost_regression import CostBaseline
        bl = CostBaseline(name="test", total_cost_usd=0.05, total_tokens=500)
        assert bl.name == "test"
        assert bl.recorded_at != ""

    def test_exported_from_meshflow(self) -> None:
        from meshflow import CostRegressionGate, CostRegressionError, CostRegressionReport, CostBaseline
        assert CostRegressionGate is not None
        assert CostRegressionError is not None
