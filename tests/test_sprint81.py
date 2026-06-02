"""Sprint 81 — mixed-model pipelines: local/cloud cost attribution, ModelTierRouter."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ═══════════════════════════════════════════════════════════════════════════════
# model_is_local
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelIsLocal:
    def _check(self, model: str, expected: bool) -> None:
        from meshflow import model_is_local
        assert model_is_local(model) is expected, f"model_is_local({model!r}) should be {expected}"

    def test_llama_is_local(self):         self._check("llama3.2", True)
    def test_llama3_tag_is_local(self):    self._check("llama3.2:latest", True)
    def test_mistral_is_local(self):       self._check("mistral", True)
    def test_mistral_7b_is_local(self):    self._check("mistral:7b", True)
    def test_ollama_prefix_is_local(self): self._check("ollama/llama3.2", True)
    def test_ollama_colon_is_local(self):  self._check("ollama:llama3.2", True)
    def test_gemma_is_local(self):         self._check("gemma2", True)
    def test_phi_is_local(self):           self._check("phi3", True)
    def test_qwen_is_local(self):          self._check("qwen2.5", True)
    def test_deepseek_is_local(self):      self._check("deepseek-coder", True)
    def test_codellama_is_local(self):     self._check("codellama:13b", True)
    def test_localhost_port_is_local(self):self._check("http://localhost:11434/v1", True)

    def test_claude_is_not_local(self):         self._check("claude-opus-4-8", False)
    def test_claude_haiku_is_not_local(self):   self._check("claude-haiku-4-5-20251001", False)
    def test_gpt4o_is_not_local(self):          self._check("gpt-4o", False)
    def test_bedrock_is_not_local(self):        self._check("meta.llama3-70b-instruct-v1:0", False)
    def test_gemini_is_not_local(self):         self._check("gemini-2.0-flash", False)
    def test_empty_is_not_local(self):          self._check("", False)

    def test_case_insensitive(self):
        from meshflow import model_is_local
        assert model_is_local("LLAMA3.2") is True
        assert model_is_local("Mistral:7B") is True


# ═══════════════════════════════════════════════════════════════════════════════
# Zero cost for local models via _cost_usd
# ═══════════════════════════════════════════════════════════════════════════════

class TestLocalModelZeroCost:
    def test_llama_cost_is_zero(self):
        from meshflow.agents.base import _cost_usd
        assert _cost_usd("llama3.2", 10000, 5000) == 0.0

    def test_mistral_cost_is_zero(self):
        from meshflow.agents.base import _cost_usd
        assert _cost_usd("mistral:7b", 10000, 5000) == 0.0

    def test_cloud_model_has_cost(self):
        from meshflow.agents.base import _cost_usd
        cost = _cost_usd("claude-haiku-4-5", 1000, 500)
        assert cost > 0.0

    def test_gpt4o_has_cost(self):
        from meshflow.agents.base import _cost_usd
        assert _cost_usd("gpt-4o", 1000, 500) > 0.0

    def test_bedrock_llama70b_has_cost(self):
        from meshflow.agents.base import _cost_usd
        # Bedrock model name — not a local prefix match
        assert _cost_usd("meta.llama3-70b-instruct-v1:0", 1000, 500) > 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# RunResult — agent_costs and cloud_agents fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunResultCostFields:
    def _result(self, **kw):
        from meshflow.core.schemas import RunResult, RunStatus
        defaults = dict(
            run_id="r", status=RunStatus.COMPLETED, output="",
            agent_states={}, total_cost_usd=0, total_tokens=0,
            total_carbon_g=0, duration_s=0, checkpoints=[],
            ledger_entries=0, trace_id="t",
        )
        defaults.update(kw)
        return RunResult(**defaults)

    def test_agent_costs_default_empty(self):
        assert self._result().agent_costs == {}

    def test_cloud_agents_default_empty(self):
        assert self._result().cloud_agents == []

    def test_agent_costs_set(self):
        r = self._result(agent_costs={"planner": 0.0, "writer": 0.045})
        assert r.agent_costs["planner"] == 0.0
        assert r.agent_costs["writer"] == pytest.approx(0.045)

    def test_cloud_agents_set(self):
        r = self._result(cloud_agents=["writer"])
        assert "writer" in r.cloud_agents


# ═══════════════════════════════════════════════════════════════════════════════
# StepOutcome — model_used and is_cloud fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestStepOutcomeModelFields:
    def test_defaults(self):
        from meshflow.core.executor import StepOutcome
        o = StepOutcome(ok=True, data={}, agent_id="a", role="executor")
        assert o.model_used == ""
        assert o.is_cloud is False

    def test_local_model(self):
        from meshflow.core.executor import StepOutcome
        o = StepOutcome(ok=True, data={}, agent_id="a", role="executor",
                        model_used="llama3.2", is_cloud=False)
        assert o.is_cloud is False

    def test_cloud_model(self):
        from meshflow.core.executor import StepOutcome
        o = StepOutcome(ok=True, data={}, agent_id="a", role="executor",
                        model_used="meta.llama3-70b-instruct-v1:0", is_cloud=True)
        assert o.is_cloud is True


# ═══════════════════════════════════════════════════════════════════════════════
# ModelTier
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelTier:
    def test_fields(self):
        from meshflow import ModelTier
        t = ModelTier("fast", "llama3.2", max_tokens=512)
        assert t.name == "fast"
        assert t.model == "llama3.2"
        assert t.max_tokens == 512

    def test_default_max_tokens(self):
        from meshflow import ModelTier
        t = ModelTier("smart", "mistral")
        assert t.max_tokens == 2048


# ═══════════════════════════════════════════════════════════════════════════════
# ModelTierRouter
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelTierRouter:
    def _router(self):
        from meshflow import ModelTierRouter, ModelTier
        return ModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2",   max_tokens=512),
                ModelTier("smart", "mistral",    max_tokens=2048),
                ModelTier("large", "meta.llama3-70b-instruct-v1:0", max_tokens=4096),
            ],
            smart_threshold=300,
            large_threshold=800,
        )

    def test_short_task_gets_fast_tier(self):
        r = self._router()
        result = r.route("short task")
        assert result.model == "llama3.2"
        assert result.tier == "fast"

    def test_medium_task_gets_smart_tier(self):
        r = self._router()
        result = r.route("x" * 350)
        assert result.model == "mistral"
        assert result.tier == "smart"

    def test_long_task_gets_large_tier(self):
        r = self._router()
        result = r.route("x" * 850)
        assert result.model == "meta.llama3-70b-instruct-v1:0"
        assert result.tier == "large"

    def test_tools_bump_tier(self):
        r = self._router()
        # short text but 3 tools → 100*3=300, hits smart threshold
        result = r.route("short", tools=["t1", "t2", "t3"])
        assert result.tier in ("smart", "large")

    def test_result_has_model_attr(self):
        r = self._router()
        result = r.route("task")
        assert hasattr(result, "model")

    def test_preset_local_all_local(self):
        from meshflow import ModelTierRouter, model_is_local
        r = ModelTierRouter(tiers=ModelTierRouter.PRESET_LOCAL)
        for _ in range(3):
            # vary task length to hit all tiers
            task = "x" * (_ * 500)
            result = r.route(task)
            assert model_is_local(result.model), f"Tier {result.tier} should be local, got {result.model}"

    def test_preset_hybrid_bedrock(self):
        from meshflow import ModelTierRouter, model_is_local
        r = ModelTierRouter(tiers=ModelTierRouter.PRESET_HYBRID_BEDROCK)
        fast_r = r.route("short")
        assert model_is_local(fast_r.model)   # llama3.2 = local
        large_r = r.route("x" * 1000)
        assert not model_is_local(large_r.model)  # Bedrock = cloud

    def test_single_tier_always_routes_there(self):
        from meshflow import ModelTierRouter, ModelTier
        r = ModelTierRouter(tiers=[ModelTier("only", "llama3.2")])
        assert r.route("anything").model == "llama3.2"
        assert r.route("x" * 9999).model == "llama3.2"

    def test_tiers_returns_copy(self):
        r = self._router()
        tiers = r.tiers()
        assert len(tiers) == 3
        tiers.clear()
        assert len(r.tiers()) == 3  # original unchanged

    def test_exported_from_meshflow(self):
        from meshflow import ModelTierRouter, ModelTier
        assert ModelTierRouter is not None
        assert ModelTier is not None

    def test_model_is_local_exported(self):
        from meshflow import model_is_local
        assert callable(model_is_local)


# ═══════════════════════════════════════════════════════════════════════════════
# Mixed-model integration: CostCap is $0 for all-local, meaningful for hybrid
# ═══════════════════════════════════════════════════════════════════════════════

class TestMixedModelCostCap:
    def test_local_pipeline_zero_cost(self):
        """Full local pipeline should accumulate $0.00 cost."""
        from meshflow.agents.base import _cost_usd
        models = ["llama3.2", "mistral:7b", "codellama:13b"]
        total = sum(_cost_usd(m, 5000, 2000) for m in models)
        assert total == 0.0

    def test_hybrid_pipeline_cost_only_from_cloud(self):
        """Local agents contribute $0; cost comes only from the cloud agent."""
        from meshflow.agents.base import _cost_usd
        local_cost  = _cost_usd("llama3.2", 5000, 2000)
        local_cost += _cost_usd("mistral",   5000, 2000)
        cloud_cost  = _cost_usd("meta.llama3-70b-instruct-v1:0", 5000, 2000)
        assert local_cost == 0.0
        assert cloud_cost > 0.0

    def test_cost_cap_blocks_expensive_cloud_call(self):
        """Budget tracker should raise when cloud cost exceeds cap."""
        from meshflow.optimization.tracker import OptimizationTracker, BudgetExceededError
        tracker = OptimizationTracker(max_cost_usd=0.001, action="fail")
        tracker.add_usage(tokens=0, cost_usd=0.0)     # local agent — fine
        with pytest.raises(BudgetExceededError):
            tracker.add_usage(tokens=100_000, cost_usd=0.05)  # cloud 70B — blocked
