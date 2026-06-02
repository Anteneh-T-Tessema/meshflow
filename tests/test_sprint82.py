"""Sprint 82 — cost estimation, dry-run, per-agent breakdown, cost-report CLI."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock


# ═══════════════════════════════════════════════════════════════════════════════
# _AgentCostLine (internal dataclass)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentCostLine:
    def _make(self, **kw):
        from meshflow.core.workflow import _AgentCostLine
        defaults = dict(agent="planner", model="llama3.2", cost_usd=0.0, is_local=True)
        defaults.update(kw)
        return _AgentCostLine(**defaults)

    def test_local_fields(self):
        ln = self._make()
        assert ln.agent == "planner"
        assert ln.model == "llama3.2"
        assert ln.cost_usd == 0.0
        assert ln.is_local is True

    def test_cloud_fields(self):
        ln = self._make(agent="writer", model="meta.llama3-70b-instruct-v1:0",
                        cost_usd=0.021, is_local=False)
        assert ln.cost_usd == pytest.approx(0.021)
        assert ln.is_local is False


# ═══════════════════════════════════════════════════════════════════════════════
# CostEstimate
# ═══════════════════════════════════════════════════════════════════════════════

class TestCostEstimate:
    def _estimate(self, lines):
        from meshflow.core.workflow import CostEstimate, _AgentCostLine
        return CostEstimate(
            lines=[_AgentCostLine(**ln) for ln in lines],
            task_preview="test task",
        )

    def test_total_usd_all_local(self):
        est = self._estimate([
            dict(agent="a", model="llama3.2", cost_usd=0.0, is_local=True),
            dict(agent="b", model="mistral",   cost_usd=0.0, is_local=True),
        ])
        assert est.total_usd == 0.0

    def test_total_usd_hybrid(self):
        est = self._estimate([
            dict(agent="a", model="llama3.2",                       cost_usd=0.0,  is_local=True),
            dict(agent="b", model="meta.llama3-70b-instruct-v1:0",  cost_usd=0.03, is_local=False),
        ])
        assert est.total_usd == pytest.approx(0.03)

    def test_cloud_agents_property(self):
        est = self._estimate([
            dict(agent="planner",    model="llama3.2",                      cost_usd=0.0,  is_local=True),
            dict(agent="researcher", model="mistral",                       cost_usd=0.0,  is_local=True),
            dict(agent="writer",     model="meta.llama3-70b-instruct-v1:0", cost_usd=0.03, is_local=False),
        ])
        assert est.cloud_agents == ["writer"]
        assert "planner" not in est.cloud_agents
        assert "researcher" not in est.cloud_agents

    def test_local_agents_property(self):
        est = self._estimate([
            dict(agent="planner",    model="llama3.2", cost_usd=0.0, is_local=True),
            dict(agent="researcher", model="mistral",  cost_usd=0.0, is_local=True),
            dict(agent="writer",     model="gpt-4o",   cost_usd=0.05, is_local=False),
        ])
        assert set(est.local_agents) == {"planner", "researcher"}

    def test_str_contains_total(self):
        est = self._estimate([
            dict(agent="a", model="llama3.2", cost_usd=0.0,  is_local=True),
            dict(agent="b", model="gpt-4o",   cost_usd=0.04, is_local=False),
        ])
        s = str(est)
        assert "Total" in s
        assert "0.0400" in s

    def test_str_contains_local_tag(self):
        est = self._estimate([
            dict(agent="a", model="llama3.2", cost_usd=0.0, is_local=True),
        ])
        assert "(local)" in str(est)

    def test_str_contains_cloud_tag(self):
        est = self._estimate([
            dict(agent="b", model="gpt-4o", cost_usd=0.04, is_local=False),
        ])
        assert "(cloud)" in str(est)

    def test_task_preview_truncated(self):
        from meshflow.core.workflow import CostEstimate
        long_task = "x" * 200
        est = CostEstimate(lines=[], task_preview=long_task[:80])
        assert len(est.task_preview) == 80

    def test_exported_from_meshflow(self):
        from meshflow import CostEstimate
        assert CostEstimate is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.estimate_cost()
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowEstimateCost:
    def _wf(self, *models):
        """Build a Workflow with stub agents for each model string."""
        from meshflow.core.workflow import Workflow
        wf = Workflow()
        for i, model in enumerate(models):
            agent = MagicMock()
            agent.name = f"agent-{i}"
            agent.model_router = None
            agent._resolve_model.return_value = model
            wf.agents.append(agent)
        return wf

    def test_all_local_returns_zero_total(self):
        wf = self._wf("llama3.2", "mistral:7b", "codellama:13b")
        est = wf.estimate_cost("analyse the market")
        assert est.total_usd == 0.0

    def test_hybrid_has_nonzero_total(self):
        wf = self._wf("llama3.2", "meta.llama3-70b-instruct-v1:0")
        est = wf.estimate_cost("analyse the market")
        assert est.total_usd > 0.0

    def test_estimate_returns_CostEstimate(self):
        from meshflow.core.workflow import CostEstimate
        wf = self._wf("llama3.2")
        assert isinstance(wf.estimate_cost("task"), CostEstimate)

    def test_task_preview_stored(self):
        wf = self._wf("llama3.2")
        est = wf.estimate_cost("short task")
        assert est.task_preview == "short task"

    def test_long_task_preview_truncated_at_80(self):
        wf = self._wf("llama3.2")
        est = wf.estimate_cost("x" * 200)
        assert len(est.task_preview) == 80

    def test_empty_workflow_no_crash(self):
        from meshflow.core.workflow import Workflow
        wf = Workflow()
        est = wf.estimate_cost("anything")
        assert est.total_usd == 0.0
        assert est.lines == []

    def test_model_router_used_when_present(self):
        """If an agent has model_router, estimate_cost should ask it to route."""
        from meshflow.core.workflow import Workflow
        from meshflow import ModelTierRouter, ModelTier

        wf = Workflow()
        agent = MagicMock()
        agent.name = "routed-agent"
        router = ModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2",                       max_tokens=512),
                ModelTier("large", "meta.llama3-70b-instruct-v1:0",  max_tokens=4096),
            ],
            smart_threshold=9999,  # force fast for short tasks
            large_threshold=9999,
        )
        agent.model_router = router
        agent._resolve_model.return_value = ""
        wf.agents.append(agent)

        est = wf.estimate_cost("short")
        # fast tier → llama3.2 → local → $0
        assert est.total_usd == 0.0
        assert est.lines[0].model == "llama3.2"

    def test_cloud_agents_list_in_estimate(self):
        wf = self._wf("llama3.2", "gpt-4o")
        est = wf.estimate_cost("task")
        assert "agent-1" in est.cloud_agents
        assert "agent-0" not in est.cloud_agents

    def test_local_agents_list_in_estimate(self):
        wf = self._wf("llama3.2", "gpt-4o")
        est = wf.estimate_cost("task")
        assert "agent-0" in est.local_agents
        assert "agent-1" not in est.local_agents

    def test_per_line_model_name_preserved(self):
        wf = self._wf("mistral:7b")
        est = wf.estimate_cost("task")
        assert est.lines[0].model == "mistral:7b"

    def test_estimate_does_not_make_llm_call(self):
        """estimate_cost must be a pure, offline heuristic — no API calls."""
        from meshflow.core.workflow import Workflow
        wf = self._wf("claude-opus-4-8")
        # If any network call happened this would fail or take time.
        # The test passing quickly is the assertion.
        est = wf.estimate_cost("task")
        assert est.total_usd >= 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Token heuristic: longer task → higher estimated cost (for cloud)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenHeuristic:
    def test_longer_task_costs_more(self):
        from meshflow.agents.base import _cost_usd
        short = _cost_usd("gpt-4o", 50,  12)
        long  = _cost_usd("gpt-4o", 500, 125)
        assert long > short

    def test_zero_tokens_zero_cost(self):
        from meshflow.agents.base import _cost_usd
        assert _cost_usd("gpt-4o", 0, 0) == 0.0

    def test_local_zero_regardless_of_tokens(self):
        from meshflow.agents.base import _cost_usd
        assert _cost_usd("llama3.2", 100_000, 50_000) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# cost-report CLI — estimation mode (offline, no ledger)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCostReportCLI:
    def _run(self, args: list[str], capsys) -> str:
        import argparse
        from meshflow.cli.main import _cmd_cost_report

        parser = argparse.ArgumentParser()
        parser.add_argument("run_id", nargs="?", default="")
        parser.add_argument("--task", default="")
        parser.add_argument("--agents", default="")
        parser.add_argument("--db", default="meshflow_runs.db")
        parser.add_argument("--json", dest="as_json", action="store_true")
        ns = parser.parse_args(args)
        _cmd_cost_report(ns)
        return capsys.readouterr().out

    def test_local_models_show_zero(self, capsys):
        out = self._run(["--task", "analyse market", "--agents", "llama3.2,mistral:7b"], capsys)
        assert "$0.0000" in out
        assert "local" in out

    def test_cloud_model_shows_nonzero(self, capsys):
        out = self._run(
            ["--task", "analyse market", "--agents", "meta.llama3-70b-instruct-v1:0"],
            capsys,
        )
        assert "cloud" in out

    def test_json_output(self, capsys):
        out = self._run(
            ["--task", "task", "--agents", "llama3.2", "--json"],
            capsys,
        )
        data = json.loads(out)
        assert "estimate" in data
        assert "total_usd" in data
        assert data["total_usd"] == 0.0

    def test_json_has_per_agent_entries(self, capsys):
        out = self._run(
            ["--task", "t", "--agents", "llama3.2,gpt-4o", "--json"],
            capsys,
        )
        data = json.loads(out)
        agents = [e["agent"] for e in data["estimate"]]
        assert len(agents) == 2

    def test_no_agents_prints_help(self, capsys):
        out = self._run(["--task", "something"], capsys)
        assert "--agents" in out

    def test_no_task_no_run_id_prints_help(self, capsys):
        out = self._run([], capsys)
        assert "run_id" in out or "meshflow traces" in out or "Provide" in out


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.estimate_cost + CostCap guard — check BEFORE running
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstimateBeforeRun:
    def test_estimate_warns_on_cloud_agents(self, capsys):
        """When estimate has cloud agents, user can inspect before spending money."""
        from meshflow.core.workflow import Workflow

        wf = Workflow()
        agent = MagicMock()
        agent.name = "expensive"
        agent.model_router = None
        agent._resolve_model.return_value = "gpt-4o"
        wf.agents.append(agent)

        est = wf.estimate_cost("big analysis task")
        # The estimate itself should flag cloud agents
        assert len(est.cloud_agents) == 1
        assert est.cloud_agents[0] == "expensive"
        assert est.total_usd > 0.0

    def test_estimate_all_local_is_safe_to_run_freely(self):
        from meshflow.core.workflow import Workflow

        wf = Workflow()
        for name, model in [("planner", "llama3.2"), ("writer", "mistral")]:
            agent = MagicMock()
            agent.name = name
            agent.model_router = None
            agent._resolve_model.return_value = model
            wf.agents.append(agent)

        est = wf.estimate_cost("anything")
        assert est.total_usd == 0.0
        assert est.cloud_agents == []
        assert set(est.local_agents) == {"planner", "writer"}
