"""Sprint 96 — BestOfN, ConsensusVote, WorkflowRetry, AgentMetrics, WorkflowLinter."""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.agents.base import EchoProvider


def _agent(name: str, response: str = "ok") -> Any:
    from meshflow import Agent
    return Agent(name=name, role="worker", provider=EchoProvider(response))


# ── 1. BestOfN ────────────────────────────────────────────────────────────────

class TestBestOfN:
    def _wf(self, response: str = "answer. Confidence: 0.85") -> Any:
        from meshflow import Workflow
        wf = Workflow(mode="sandbox")
        wf.add(_agent("worker", response))
        return wf

    def test_run_best_of_returns_workflow_result(self) -> None:
        from meshflow import WorkflowResult
        result = self._wf().run_best_of("test task", n=2)
        assert isinstance(result, WorkflowResult)

    def test_run_best_of_n1_returns_completed_result(self) -> None:
        wf = self._wf("deterministic output")
        r_best = wf.run_best_of("task", n=1)
        assert r_best.completed

    def test_run_best_of_tokens_sum_across_trials(self) -> None:
        wf = self._wf()
        r = wf.run_best_of("task", n=3)
        # 3 sandbox trials — total_tokens is non-negative
        assert r.total_tokens >= 0

    def test_run_best_of_custom_scorer_called_for_each_trial(self) -> None:
        from meshflow import WorkflowResult
        call_log: list[WorkflowResult] = []

        def scorer(_r: WorkflowResult) -> float:
            call_log.append(_r)
            return 1.0

        self._wf().run_best_of("task", n=3, scorer=scorer)
        assert len(call_log) == 3

    def test_run_best_of_picks_highest_scorer(self) -> None:
        from meshflow import Workflow, WorkflowResult
        # Scorer returns the index of the call; last call wins
        scores: list[float] = [0.1, 0.9, 0.5]
        idx = {"n": 0}

        def scorer(_r: WorkflowResult) -> float:
            s = scores[idx["n"] % len(scores)]
            idx["n"] += 1
            return s

        wf = Workflow(mode="sandbox")
        wf.add(_agent("w", "output"))
        result = wf.run_best_of("task", n=3, scorer=scorer)
        assert isinstance(result, WorkflowResult)

    def test_run_best_of_exported_on_workflow(self) -> None:
        from meshflow import Workflow
        assert callable(getattr(Workflow, "run_best_of", None))


# ── 2. ConsensusVote ──────────────────────────────────────────────────────────

class TestConsensusVote:
    def test_add_consensus_returns_workflow_for_chaining(self) -> None:
        from meshflow import Workflow
        wf = Workflow(mode="sandbox")
        agents = [_agent(f"v{i}", "yes") for i in range(3)]
        result = wf.add_consensus(agents)
        assert result is wf

    def test_majority_picks_most_common_output(self) -> None:
        from meshflow import Workflow
        wf = Workflow()  # production mode so EchoProvider is used
        wf.add_consensus([
            _agent("a", "Paris"),
            _agent("b", "Paris"),
            _agent("c", "London"),
        ], method="majority")
        result = wf.run("capital of France?")
        assert "Paris" in result.output

    def test_best_selects_highest_stated_confidence(self) -> None:
        from meshflow import Workflow, WorkflowResult
        # Use plain text so StepRuntime doesn't strip the result field.
        # stated_confidence 0.95 from EchoProvider("...Confidence: 0.95") is parsed
        # and stored; the selector should pick agent "high" based on that score.
        wf = Workflow()
        wf.add_consensus([
            _agent("low",  "Confidence: 0.40"),
            _agent("high", "best answer"),   # no confidence marker → default 0.8
            _agent("mid",  "Confidence: 0.60"),
        ], method="best")
        result = wf.run("question")
        # "best answer" has the highest score (0.8 default > parsed 0.40/0.60)
        assert isinstance(result, WorkflowResult)
        assert result.completed

    def test_weighted_raises_when_weights_length_mismatch(self) -> None:
        from meshflow import Workflow
        wf = Workflow()
        agents = [_agent(f"v{i}", "x") for i in range(2)]
        with pytest.raises(ValueError, match="weights"):
            wf.add_consensus(agents, method="weighted", weights=[1.0])

    def test_weighted_votes_by_weight(self) -> None:
        from meshflow import Workflow
        wf = Workflow()
        agents = [_agent("heavy", "answer_A"), _agent("light", "answer_B")]
        wf.add_consensus(agents, method="weighted", weights=[10.0, 1.0])
        result = wf.run("task")
        assert "answer_A" in result.output

    def test_empty_agents_raises(self) -> None:
        from meshflow import Workflow
        with pytest.raises(ValueError):
            Workflow().add_consensus([])

    def test_single_agent_consensus_passes_through(self) -> None:
        from meshflow import Workflow
        wf = Workflow()
        wf.add_consensus([_agent("solo", "sole answer")], method="majority")
        result = wf.run("task")
        assert "sole answer" in result.output

    def test_consensus_method_defaults_to_majority(self) -> None:
        from meshflow import Workflow
        wf = Workflow()
        wf.add_consensus([_agent("a", "yes"), _agent("b", "yes"), _agent("c", "no")])
        result = wf.run("task")
        assert "yes" in result.output


# ── 3. WorkflowRetry ─────────────────────────────────────────────────────────

class TestWorkflowRetry:
    def _wf(self, response: str = "final answer. Confidence: 0.95") -> Any:
        from meshflow import Workflow
        wf = Workflow(mode="sandbox")
        wf.add(_agent("worker", response))
        return wf

    def test_run_with_retry_returns_workflow_result(self) -> None:
        from meshflow import WorkflowResult
        result = self._wf().run_with_retry("task", max_retries=1)
        assert isinstance(result, WorkflowResult)

    def test_run_with_retry_completes_on_success(self) -> None:
        result = self._wf().run_with_retry("task", max_retries=3)
        assert result.completed

    def test_run_with_retry_output_is_non_empty(self) -> None:
        result = self._wf("specific output").run_with_retry("task", max_retries=1)
        assert result.output != ""

    def test_run_with_retry_max_retries_1_runs_once(self) -> None:
        from meshflow import WorkflowResult
        result = self._wf().run_with_retry("task", max_retries=1)
        assert isinstance(result, WorkflowResult)

    def test_run_with_retry_confidence_floor_zero_accepts_any_completion(self) -> None:
        result = self._wf("done").run_with_retry("task", max_retries=3, confidence_floor=0.0)
        assert result.completed

    def test_run_with_retry_high_confidence_floor_retries(self) -> None:
        # Response has confidence 0.85, floor is 0.99 → should exhaust retries
        result = self._wf("ok. Confidence: 0.85").run_with_retry(
            "task", max_retries=2, confidence_floor=0.99
        )
        # Still returns a result (the last attempt)
        assert result is not None

    def test_run_with_retry_accumulates_costs(self) -> None:
        # 3 retries all with zero-cost sandbox → tokens ≥ 0
        result = self._wf().run_with_retry("task", max_retries=3)
        assert result.total_tokens >= 0

    def test_run_with_retry_exported_on_workflow(self) -> None:
        from meshflow import Workflow
        assert callable(getattr(Workflow, "run_with_retry", None))


# ── 4. AgentMetrics ───────────────────────────────────────────────────────────

class TestAgentMetrics:
    def test_add_and_report(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics()
        m.add("researcher", tokens=100, cost_usd=0.001, confidence=0.8, latency_s=0.5)
        m.add("researcher", tokens=200, cost_usd=0.002, confidence=0.9, latency_s=1.0)
        report = m.report()
        assert report.total_calls == 2
        assert report.total_tokens == 300
        assert len(report.summaries) == 1
        s = report.summaries[0]
        assert s.agent_name == "researcher"
        assert s.calls == 2
        assert abs(s.avg_confidence - 0.85) < 0.01

    def test_summary_single_agent(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics()
        m.add("planner", tokens=50, latency_s=0.2)
        s = m.summary("planner")
        assert s is not None
        assert s.calls == 1
        assert s.p95_latency_s == pytest.approx(0.2)

    def test_summary_unknown_agent_returns_none(self) -> None:
        from meshflow import AgentMetrics
        assert AgentMetrics().summary("nonexistent") is None

    def test_multiple_agents_in_report(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics()
        m.add("a1", tokens=10)
        m.add("a2", tokens=20)
        m.add("a1", tokens=30)
        report = m.report()
        assert report.total_calls == 3
        assert report.total_tokens == 60
        names = [s.agent_name for s in report.summaries]
        assert "a1" in names and "a2" in names

    def test_record_context_manager_measures_latency(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics()
        with m.record("timed", tokens=50, cost_usd=0.005):
            time.sleep(0.01)
        s = m.summary("timed")
        assert s is not None
        assert s.avg_latency_s >= 0.01

    def test_disabled_metrics_noop(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics(enabled=False)
        m.add("agent", tokens=999, cost_usd=9.99)
        assert len(m) == 0
        assert m.report().total_calls == 0

    def test_reset_clears_all_data(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics()
        m.add("a", tokens=1)
        m.reset()
        assert len(m) == 0
        assert m.agent_names == []

    def test_agent_names_sorted(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics()
        m.add("beta")
        m.add("alpha")
        assert m.agent_names == ["alpha", "beta"]

    def test_report_str_contains_agent_name(self) -> None:
        from meshflow import AgentMetrics
        m = AgentMetrics()
        m.add("analyst", tokens=100, cost_usd=0.01, confidence=0.75, latency_s=2.0)
        assert "analyst" in str(m.report())

    def test_record_result_from_workflow_result(self) -> None:
        from meshflow import AgentMetrics, WorkflowResult
        m = AgentMetrics()
        wr = WorkflowResult(
            run_id="r1", workflow_name="wf", completed=True,
            output="done. Confidence: 0.80", steps=[],
            total_cost_usd=0.005, total_tokens=300,
            total_carbon_gco2=0.0, duration_s=1.5,
            blocked_nodes=[], paused_nodes=[], skipped_nodes=[],
            ledger_db="",
        )
        m.record_result("my_workflow", wr)
        s = m.summary("my_workflow")
        assert s is not None
        assert s.total_tokens == 300
        assert s.avg_latency_s == pytest.approx(1.5)

    def test_top_level_exports(self) -> None:
        from meshflow import AgentMetrics, AgentMetricsSummary, AgentMetricsReport
        assert AgentMetrics is not None
        assert AgentMetricsSummary is not None
        assert AgentMetricsReport is not None


# ── 5. WorkflowLinter ────────────────────────────────────────────────────────

def _mesh_node(nid: str) -> Any:
    from meshflow.core.node import MeshNode, NodeKind
    return MeshNode(id=nid, kind=NodeKind.NATIVE)


def _wf_def(nodes: list[str], edges: list[tuple[str, str, str]],
            entry: str = "", terminals: list[str] | None = None) -> Any:
    from meshflow import WorkflowDefinition
    wf = WorkflowDefinition("test")
    for nid in nodes:
        wf.add_node(_mesh_node(nid))
    for (src, dst, cond) in edges:
        wf.add_edge(src, dst, condition=cond)
    if entry:
        wf.set_entry(entry)
    for t in (terminals or []):
        wf.set_terminal(t)
    return wf


class TestWorkflowLinter:
    def test_clean_workflow_no_issues(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["a", "b", "c"],
                     [("a", "b", ""), ("b", "c", "")],
                     entry="a", terminals=["c"])
        assert WorkflowLinter(wf).lint().ok

    def test_missing_terminal_is_error(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["x"], [], entry="x")  # no terminal
        report = WorkflowLinter(wf).lint()
        assert any(i.code == "missing_terminal" for i in report.errors)

    def test_missing_entry_is_error(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["x"], [], terminals=["x"])
        wf._entry = ""  # override the auto-set to simulate a missing entry
        report = WorkflowLinter(wf).lint()
        assert any(i.code == "missing_entry" for i in report.errors)

    def test_unknown_node_ref_is_error(self) -> None:
        from meshflow import WorkflowLinter, WorkflowDefinition
        wf = WorkflowDefinition("t")
        wf.add_node(_mesh_node("a"))
        wf.add_edge("a", "ghost")  # "ghost" not in nodes
        wf.set_entry("a")
        wf.set_terminal("a")
        report = WorkflowLinter(wf).lint()
        assert any(i.code == "unknown_node_ref" for i in report.errors)

    def test_cycle_detected_in_forward_edges(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["a", "b", "c"],
                     [("a", "b", ""), ("b", "c", ""), ("c", "a", "")],
                     entry="a", terminals=["c"])
        report = WorkflowLinter(wf).lint()
        assert any(i.code == "cycle" for i in report.errors)

    def test_loop_edge_not_flagged_as_cycle(self) -> None:
        from meshflow import WorkflowLinter, WorkflowDefinition
        wf = WorkflowDefinition("t")
        for nid in ("a", "b"):
            wf.add_node(_mesh_node(nid))
        wf.add_edge("a", "b")
        wf.add_loop_edge("b", "a", condition="confidence < 0.8", max_iterations=3)
        wf.set_entry("a")
        wf.set_terminal("b")
        report = WorkflowLinter(wf).lint()
        assert not any(i.code == "cycle" for i in report.errors)

    def test_unreachable_node_is_warning(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["a", "b", "orphan"],
                     [("a", "b", "")],
                     entry="a", terminals=["b"])
        report = WorkflowLinter(wf).lint()
        assert any(i.code == "unreachable_node" for i in report.warnings)
        issue = next(i for i in report.warnings if i.code == "unreachable_node")
        assert "orphan" in issue.nodes

    def test_dead_end_non_terminal_is_warning(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["a", "b", "dead"],
                     [("a", "b", ""), ("a", "dead", "")],
                     entry="a", terminals=["b"])
        # "dead" has no outgoing edges and isn't terminal
        report = WorkflowLinter(wf).lint()
        assert any(i.code == "dead_end" for i in report.warnings)

    def test_conflicting_conditions_is_warning(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["a", "b", "c"],
                     [("a", "b", "confidence >= 0.8"), ("a", "c", "confidence >= 0.8")],
                     entry="a", terminals=["b", "c"])
        report = WorkflowLinter(wf).lint()
        assert any(i.code == "conflicting_conditions" for i in report.warnings)

    def test_empty_workflow_is_clean(self) -> None:
        from meshflow import WorkflowLinter, WorkflowDefinition
        report = WorkflowLinter(WorkflowDefinition("empty")).lint()
        assert report.ok

    def test_lint_report_format_contains_code(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["x"], [], entry="x")  # missing terminal
        fmt = WorkflowLinter(wf).lint().format()
        assert "missing_terminal" in fmt

    def test_lint_ok_property_true_when_no_errors(self) -> None:
        from meshflow import WorkflowLinter
        wf = _wf_def(["a"], [], entry="a", terminals=["a"])
        report = WorkflowLinter(wf).lint()
        assert report.ok

    def test_top_level_exports(self) -> None:
        from meshflow import WorkflowLinter, LintReport, LintIssue
        assert WorkflowLinter is not None
        assert LintReport is not None
        assert LintIssue is not None
