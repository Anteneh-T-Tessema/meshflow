"""Sprint 35 — Self-healing orchestration."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.agents.healing import HealingPolicy, HealingStrategy, HealingResult, run_with_healing


# ── HealingPolicy ─────────────────────────────────────────────────────────────

class TestHealingPolicy:
    def test_defaults(self):
        p = HealingPolicy()
        assert p.confidence_threshold == 0.5
        assert p.max_retries == 3
        assert HealingStrategy.retry_same in p.strategies

    def test_is_passing_normal_result(self):
        p = HealingPolicy(confidence_threshold=0.7)
        result = {"stated_confidence": 0.9, "blocked": False}
        assert p.is_passing(result) is True

    def test_is_passing_low_confidence(self):
        p = HealingPolicy(confidence_threshold=0.7)
        result = {"stated_confidence": 0.5, "blocked": False}
        assert p.is_passing(result) is False

    def test_is_passing_blocked(self):
        p = HealingPolicy()
        result = {"stated_confidence": 0.9, "blocked": True}
        assert p.is_passing(result) is False

    def test_is_passing_error(self):
        p = HealingPolicy()
        result = {"stated_confidence": 0.9, "blocked": False, "error": "timeout"}
        assert p.is_passing(result) is False

    def test_is_passing_missing_confidence_defaults_to_1(self):
        p = HealingPolicy(confidence_threshold=0.5)
        result = {"blocked": False}
        assert p.is_passing(result) is True

    def test_custom_strategies(self):
        p = HealingPolicy(strategies=[HealingStrategy.retry_same])
        assert len(p.strategies) == 1
        assert p.strategies[0] == HealingStrategy.retry_same

    def test_fallback_models_default(self):
        p = HealingPolicy()
        assert len(p.fallback_models) >= 1


# ── HealingResult ─────────────────────────────────────────────────────────────

class TestHealingResult:
    def _result(self) -> dict:
        return {"result": "answer", "tokens": 10, "cost_usd": 0.001,
                "stated_confidence": 0.9, "blocked": False, "guardrail_results": []}

    def test_getitem(self):
        hr = HealingResult(result=self._result())
        assert hr["result"] == "answer"

    def test_get_with_default(self):
        hr = HealingResult(result=self._result())
        assert hr.get("missing", "default") == "default"

    def test_contains(self):
        hr = HealingResult(result=self._result())
        assert "result" in hr
        assert "nonexistent" not in hr

    def test_to_dict_includes_healing_fields(self):
        hr = HealingResult(
            result=self._result(),
            attempts=2,
            strategies_tried=["retry_same"],
            healed=True,
        )
        d = hr.to_dict()
        assert d["healed"] is True
        assert d["healing_attempts"] == 2
        assert "retry_same" in d["healing_strategies_tried"]

    def test_to_dict_preserves_result(self):
        hr = HealingResult(result=self._result())
        d = hr.to_dict()
        assert d["result"] == "answer"


# ── run_with_healing ──────────────────────────────────────────────────────────

class TestRunWithHealing:
    @pytest.mark.asyncio
    async def test_passes_on_first_try(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="heal-agent", role="executor")
        policy = HealingPolicy(confidence_threshold=0.0)  # always pass
        hr = await run_with_healing(agent, "task", policy=policy)
        assert isinstance(hr, HealingResult)
        assert hr.attempts == 1
        assert not hr.healed

    @pytest.mark.asyncio
    async def test_retry_same_fires_on_low_confidence(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="low-conf-agent", role="executor")
        # Set threshold above what EchoProvider returns (which defaults to 1.0)
        # We can't easily force low confidence with EchoProvider, so we test
        # that strategies_tried is non-empty when threshold is unreachably high
        policy = HealingPolicy(
            confidence_threshold=999.0,  # nothing will ever pass
            strategies=[HealingStrategy.retry_same],
            max_retries=2,
        )
        hr = await run_with_healing(agent, "task", policy=policy)
        assert hr.attempts >= 2
        assert "retry_same" in hr.strategies_tried
        assert not hr.healed

    @pytest.mark.asyncio
    async def test_retry_different_model_fires(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="dm-agent", role="executor")
        policy = HealingPolicy(
            confidence_threshold=999.0,
            strategies=[HealingStrategy.retry_different_model],
            fallback_models=["claude-haiku-4-5-20251001"],
            max_retries=2,
        )
        hr = await run_with_healing(agent, "task", policy=policy)
        assert "retry_different_model" in hr.strategies_tried

    @pytest.mark.asyncio
    async def test_escalate_to_supervisor_fires(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="esc-agent", role="executor")
        policy = HealingPolicy(
            confidence_threshold=999.0,
            strategies=[HealingStrategy.escalate_to_supervisor],
            fallback_models=["claude-haiku-4-5-20251001"],
            max_retries=2,
        )
        hr = await run_with_healing(agent, "task", policy=policy)
        assert "escalate_to_supervisor" in hr.strategies_tried

    @pytest.mark.asyncio
    async def test_agent_run_with_healing_method(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        policy = HealingPolicy(confidence_threshold=0.0)
        agent = Agent(name="method-agent", role="executor", healing=policy)
        hr = await agent.run_with_healing("task")
        assert isinstance(hr, HealingResult)

    @pytest.mark.asyncio
    async def test_healing_respects_max_retries(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="max-retry-agent", role="executor")
        policy = HealingPolicy(
            confidence_threshold=999.0,
            strategies=[HealingStrategy.retry_same, HealingStrategy.retry_same],
            max_retries=2,
        )
        hr = await run_with_healing(agent, "task", policy=policy)
        # Total attempts = 1 initial + at most max_retries
        assert hr.attempts <= policy.max_retries + 1

    @pytest.mark.asyncio
    async def test_returns_best_result_when_all_fail(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="best-agent", role="executor")
        policy = HealingPolicy(
            confidence_threshold=999.0,
            strategies=[HealingStrategy.retry_same],
            max_retries=1,
        )
        hr = await run_with_healing(agent, "task", policy=policy)
        assert hr.result is not None
        assert "result" in hr.result


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.agents.healing import (
            HealingPolicy, HealingStrategy, HealingResult, run_with_healing
        )
        assert all(x is not None for x in [HealingPolicy, HealingStrategy, HealingResult, run_with_healing])

    def test_agent_has_healing_field(self):
        import dataclasses
        from meshflow.agents.builder import Agent
        fields = {f.name for f in dataclasses.fields(Agent)}
        assert "healing" in fields

    def test_agent_has_run_with_healing(self):
        from meshflow.agents.builder import Agent
        assert callable(Agent.run_with_healing)
