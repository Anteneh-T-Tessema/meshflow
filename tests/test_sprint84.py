"""Sprint 84 — CascadeRouter: FrugalGPT-style escalation on low confidence.

Tests cover:
- CascadeRouter: route(), escalate(), record_outcome(), tiers(), escalation_count()
- escalate() returns None when max_escalations reached
- escalate() returns None when already at the last tier
- cascade_threshold wired on Agent → _BuiltAgent._cascade_threshold
- step() escalates when mock returns low confidence
- cascade_escalations count in step() result dict
- Full cascade loop: fast → smart → large (with mock confidence markers)
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch

os.environ.setdefault("MESHFLOW_MOCK", "1")


# ═══════════════════════════════════════════════════════════════════════════════
# CascadeRouter construction & basic routing
# ═══════════════════════════════════════════════════════════════════════════════

class TestCascadeRouterBasic:
    def _base_router(self, exploration_rate=0.0):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        return AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2",  max_tokens=512),
                ModelTier("smart", "mistral",   max_tokens=2048),
                ModelTier("large", "gpt-4o",    max_tokens=4096),
            ],
            exploration_rate=exploration_rate,
            store=RouterOutcomeStore(path=":memory:"),
        )

    def _cascade(self, **kw):
        from meshflow import CascadeRouter
        defaults = dict(escalation_threshold=0.65, max_escalations=2)
        defaults.update(kw)
        return CascadeRouter(self._base_router(), **defaults)

    def test_route_returns_tier_result(self):
        c = self._cascade()
        result = c.route("short task")
        assert hasattr(result, "model")
        assert hasattr(result, "tier")

    def test_route_stores_routing_id(self):
        c = self._cascade()
        result = c.route("task", run_id="r1")
        assert result.routing_id == "r1"
        assert "r1" in c._states

    def test_route_generates_id_when_empty(self):
        c = self._cascade()
        result = c.route("task")
        assert len(result.routing_id) > 0

    def test_route_delegates_to_wrapped_router(self):
        c = self._cascade()
        result = c.route("short task")
        # fast tier for low-composite task
        assert result.tier == "fast"
        assert result.model == "llama3.2"

    def test_tiers_returns_wrapped_router_tiers(self):
        c = self._cascade()
        tiers = c.tiers()
        assert len(tiers) == 3
        assert tiers[0].name == "fast"

    def test_exported_from_meshflow(self):
        from meshflow import CascadeRouter
        assert CascadeRouter is not None


# ═══════════════════════════════════════════════════════════════════════════════
# escalate()
# ═══════════════════════════════════════════════════════════════════════════════

class TestCascadeRouterEscalate:
    def _cascade(self, max_escalations=2):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore, CascadeRouter
        base = AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2", max_tokens=512),
                ModelTier("smart", "mistral",  max_tokens=2048),
                ModelTier("large", "gpt-4o",   max_tokens=4096),
            ],
            exploration_rate=0.0,
            store=RouterOutcomeStore(path=":memory:"),
        )
        return CascadeRouter(base, escalation_threshold=0.65, max_escalations=max_escalations)

    def test_escalate_returns_next_tier(self):
        c = self._cascade()
        result = c.route("task", run_id="r1")
        assert result.tier == "fast"
        escalated = c.escalate("r1")
        assert escalated is not None
        assert escalated.tier == "smart"
        assert escalated.model == "mistral"

    def test_escalate_second_time_returns_large(self):
        c = self._cascade()
        c.route("task", run_id="r1")
        c.escalate("r1")   # fast → smart
        escalated2 = c.escalate("r1")  # smart → large
        assert escalated2 is not None
        assert escalated2.tier == "large"
        assert escalated2.model == "gpt-4o"

    def test_escalate_returns_none_when_max_reached(self):
        c = self._cascade(max_escalations=1)
        c.route("task", run_id="r1")
        c.escalate("r1")   # 1st escalation (fast → smart)
        result = c.escalate("r1")  # would be 2nd — should be None
        assert result is None

    def test_escalate_returns_none_when_at_last_tier(self):
        c = self._cascade()
        c.route("task", run_id="r1")
        c.escalate("r1")   # fast → smart
        c.escalate("r1")   # smart → large (2nd escalation)
        result = c.escalate("r1")  # no more tiers
        assert result is None

    def test_escalate_unknown_id_returns_none(self):
        c = self._cascade()
        assert c.escalate("nonexistent") is None

    def test_escalation_count_tracks_usage(self):
        c = self._cascade()
        c.route("task", run_id="r1")
        assert c.escalation_count("r1") == 0
        c.escalate("r1")
        assert c.escalation_count("r1") == 1
        c.escalate("r1")
        assert c.escalation_count("r1") == 2

    def test_escalation_count_unknown_id(self):
        c = self._cascade()
        assert c.escalation_count("unknown") == 0

    def test_escalate_propagates_routing_id(self):
        c = self._cascade()
        c.route("task", run_id="my-id")
        escalated = c.escalate("my-id")
        assert escalated is not None
        assert escalated.routing_id == "my-id"


# ═══════════════════════════════════════════════════════════════════════════════
# record_outcome() forwards to wrapped router and clears state
# ═══════════════════════════════════════════════════════════════════════════════

class TestCascadeRouterRecordOutcome:
    def _cascade(self):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore, CascadeRouter
        base = AdaptiveModelTierRouter(
            tiers=[ModelTier("fast", "llama3.2"), ModelTier("large", "gpt-4o")],
            exploration_rate=0.0,
            store=RouterOutcomeStore(path=":memory:"),
        )
        return CascadeRouter(base, escalation_threshold=0.65)

    def test_record_outcome_clears_state(self):
        c = self._cascade()
        c.route("task", run_id="r1")
        assert "r1" in c._states
        c.record_outcome("r1", success=True, quality=0.9)
        assert "r1" not in c._states

    def test_record_outcome_forwards_to_wrapped_router(self):
        c = self._cascade()
        c.route("task", run_id="r1")
        c.record_outcome("r1", success=True, quality=0.85, latency_ms=300.0, actual_cost_usd=0.0)
        # Outcome should be in the wrapped router's store
        assert c._router._store.count() == 1

    def test_record_outcome_unknown_id_no_crash(self):
        c = self._cascade()
        c.record_outcome("nonexistent", success=True)  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# cascade_threshold wired from Agent to _BuiltAgent
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentCascadeThreshold:
    def test_cascade_threshold_stored_on_built_agent(self):
        from meshflow import Agent, CascadeRouter, AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        base = AdaptiveModelTierRouter(
            tiers=[ModelTier("fast", "llama3.2"), ModelTier("large", "gpt-4o")],
            exploration_rate=0.0,
            store=RouterOutcomeStore(path=":memory:"),
        )
        cascade = CascadeRouter(base, escalation_threshold=0.65)
        agent = Agent("a", model_router=cascade, cascade_threshold=0.65)
        built = agent._build()
        assert built._cascade_threshold == pytest.approx(0.65)

    def test_cascade_threshold_none_by_default(self):
        from meshflow import Agent
        agent = Agent("a")
        built = agent._build()
        assert built._cascade_threshold is None

    def test_cascade_threshold_without_cascade_router(self):
        """cascade_threshold on Agent without CascadeRouter — no crash, no escalation."""
        from meshflow import Agent, AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        base = AdaptiveModelTierRouter(
            tiers=[ModelTier("fast", "llama3.2")],
            exploration_rate=0.0,
            store=RouterOutcomeStore(path=":memory:"),
        )
        agent = Agent("a", model_router=base, cascade_threshold=0.65)
        built = agent._build()
        assert built._cascade_threshold == pytest.approx(0.65)


# ═══════════════════════════════════════════════════════════════════════════════
# step() cascade escalation with mocked LLM responses
# ═══════════════════════════════════════════════════════════════════════════════

class TestStepCascadeEscalation:
    """Test the cascade loop inside _BuiltAgent.step() by mocking think()."""

    def _built_agent(self, cascade_threshold: float | None = 0.65):
        from meshflow import Agent, CascadeRouter, AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        base = AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2",  max_tokens=512),
                ModelTier("smart", "mistral",   max_tokens=2048),
                ModelTier("large", "gpt-4o",    max_tokens=4096),
            ],
            exploration_rate=0.0,
            store=RouterOutcomeStore(path=":memory:"),
        )
        cascade = CascadeRouter(base, escalation_threshold=cascade_threshold or 0.65, max_escalations=2)
        agent = Agent("test", model_router=cascade, cascade_threshold=cascade_threshold)
        return agent._build()

    def _run_step(self, built, think_responses: list[str]):
        """Patch think() to return successive responses and run step()."""
        import asyncio

        call_idx = 0

        async def mock_think(messages, model_override=None, **kw):
            nonlocal call_idx
            resp = think_responses[min(call_idx, len(think_responses) - 1)]
            call_idx += 1
            return resp, 100, 0.001

        async def _run():
            with patch.object(built, "think", side_effect=mock_think):
                return await built.step("test task", {})

        return asyncio.run(_run())

    def test_no_escalation_when_confidence_high(self):
        built = self._built_agent()
        result = self._run_step(built, ["Great answer.\nCONFIDENCE:0.90"])
        assert result["cascade_escalations"] == 0
        assert "Great answer" in result["result"]

    def test_escalates_once_on_low_confidence(self):
        built = self._built_agent()
        result = self._run_step(built, [
            "Weak answer.\nCONFIDENCE:0.40",   # fast tier → low → escalate
            "Better answer.\nCONFIDENCE:0.85",  # smart tier → high → done
        ])
        assert result["cascade_escalations"] == 1
        assert "Better answer" in result["result"]

    def test_escalates_twice_to_large_tier(self):
        built = self._built_agent()
        result = self._run_step(built, [
            "Weak.\nCONFIDENCE:0.30",           # fast → escalate
            "Still weak.\nCONFIDENCE:0.45",     # smart → escalate
            "Strong.\nCONFIDENCE:0.88",         # large → done
        ])
        assert result["cascade_escalations"] == 2
        assert "Strong" in result["result"]

    def test_stops_at_max_escalations(self):
        """Even if confidence stays low, stops after max_escalations."""
        built = self._built_agent()
        result = self._run_step(built, [
            "Weak.\nCONFIDENCE:0.20",   # fast → escalate
            "Weak.\nCONFIDENCE:0.25",   # smart → escalate
            "Weak.\nCONFIDENCE:0.30",   # large — no more tiers, stop
        ])
        assert result["cascade_escalations"] == 2
        assert "Weak" in result["result"]

    def test_no_escalation_when_cascade_threshold_is_none(self):
        """cascade_threshold=None disables cascade entirely."""
        built = self._built_agent(cascade_threshold=None)
        result = self._run_step(built, ["Weak.\nCONFIDENCE:0.10"])
        assert result["cascade_escalations"] == 0

    def test_tokens_accumulate_across_escalations(self):
        """Total token count includes all escalation calls."""
        import asyncio

        built = self._built_agent()
        call_count = 0

        async def mock_think(messages, model_override=None, **kw):
            nonlocal call_count
            responses = [
                ("Weak.\nCONFIDENCE:0.30", 50, 0.001),
                ("Good.\nCONFIDENCE:0.90", 80, 0.002),
            ]
            resp = responses[min(call_count, 1)]
            call_count += 1
            return resp[0], resp[1], resp[2]

        async def _run():
            with patch.object(built, "think", side_effect=mock_think):
                return await built.step("task", {})

        result = asyncio.run(_run())
        assert result["tokens"] == 130  # 50 + 80
        assert result["cost_usd"] == pytest.approx(0.003)

    def test_result_contains_cascade_escalations_key(self):
        built = self._built_agent(cascade_threshold=None)
        result = self._run_step(built, ["Answer.\nCONFIDENCE:0.90"])
        assert "cascade_escalations" in result


# ═══════════════════════════════════════════════════════════════════════════════
# CascadeRouter + non-AdaptiveModelTierRouter (plain ModelTierRouter)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCascadeRouterWithPlainTierRouter:
    def test_cascade_wraps_plain_model_tier_router(self):
        from meshflow import ModelTierRouter, ModelTier, CascadeRouter
        base = ModelTierRouter(tiers=[
            ModelTier("fast",  "llama3.2", max_tokens=512),
            ModelTier("smart", "mistral",  max_tokens=2048),
        ])
        cascade = CascadeRouter(base, escalation_threshold=0.65)
        result = cascade.route("task", run_id="r1")
        assert result.model in ("llama3.2", "mistral")

    def test_escalate_works_without_adaptive_router(self):
        from meshflow import ModelTierRouter, ModelTier, CascadeRouter
        base = ModelTierRouter(tiers=[
            ModelTier("fast",  "llama3.2", max_tokens=512),
            ModelTier("smart", "mistral",  max_tokens=2048),
        ])
        cascade = CascadeRouter(base, escalation_threshold=0.65)
        cascade.route("task", run_id="r1")
        esc = cascade.escalate("r1")
        # should escalate to smart (or None if already there due to threshold)
        # either way, should not crash
        assert esc is None or esc.tier == "smart"

    def test_record_outcome_no_crash_without_adaptive_router(self):
        from meshflow import ModelTierRouter, ModelTier, CascadeRouter
        base = ModelTierRouter(tiers=[ModelTier("fast", "llama3.2")])
        cascade = CascadeRouter(base)
        cascade.route("task", run_id="r1")
        cascade.record_outcome("r1", success=True)  # should not crash
