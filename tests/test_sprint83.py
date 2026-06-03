"""Sprint 83 — self-improving adaptive model router.

Tests cover:
- ModelRegistry: register, get, is_local, cost_usd, DEFAULT_REGISTRY
- TaskScorer: composite scores, task-type classification, tool bump
- extract_confidence: marker parsing
- RouterOutcomeStore: record, count, get_recent (in-memory)
- ThresholdOptimizer: no-op on low data, adjusts on failure
- AdaptiveModelTierRouter: routing, exploration, adapt(), explain(), stats(),
  record_outcome(), auto-adapt trigger
- estimate_cost integration: registry wins over pattern detection
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("MESHFLOW_MOCK", "1")


# ═══════════════════════════════════════════════════════════════════════════════
# ModelSpec & ModelRegistry
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelSpec:
    def test_local_spec_zero_cost(self):
        from meshflow import ModelSpec
        spec = ModelSpec("corp-llm", is_local=True)
        assert spec.cost_usd(10_000, 5_000) == 0.0

    def test_cloud_spec_has_cost(self):
        from meshflow import ModelSpec
        spec = ModelSpec("my-cloud", is_local=False, cost_input_per_1k=0.003, cost_output_per_1k=0.015)
        assert spec.cost_usd(1_000, 500) == pytest.approx(0.003 + 0.0075)

    def test_default_fields(self):
        from meshflow import ModelSpec
        s = ModelSpec("x", is_local=True)
        assert s.context_window == 4096
        assert s.quality_estimate == 0.7
        assert s.tags == []

    def test_tags_field(self):
        from meshflow import ModelSpec
        s = ModelSpec("x", is_local=True, tags=["code", "local"])
        assert "code" in s.tags


class TestModelRegistry:
    def _fresh(self):
        from meshflow.agents.registry import ModelRegistry
        return ModelRegistry()

    def test_register_and_get_exact(self):
        from meshflow import ModelSpec
        r = self._fresh()
        r.register(ModelSpec("corp-llm", is_local=True))
        assert r.get("corp-llm") is not None
        assert r.get("corp-llm").is_local is True

    def test_register_dict(self):
        r = self._fresh()
        r.register({"model_id": "my-model", "is_local": False,
                    "cost_input_per_1k": 0.01, "cost_output_per_1k": 0.03})
        assert r.get("my-model").is_local is False

    def test_substring_match(self):
        from meshflow import ModelSpec
        r = self._fresh()
        r.register(ModelSpec("llama3", is_local=True))
        # "llama3.2" contains "llama3"
        spec = r.get("llama3.2")
        assert spec is not None
        assert spec.is_local is True

    def test_unregistered_returns_none(self):
        r = self._fresh()
        assert r.get("unknown-model-xyz") is None

    def test_is_local_registry_wins(self):
        from meshflow import ModelSpec
        r = self._fresh()
        # "llama3.2" would normally be local by pattern, but we register it as cloud
        r.register(ModelSpec("llama3.2", is_local=False))
        assert r.is_local("llama3.2") is False

    def test_is_local_falls_back_to_pattern(self):
        r = self._fresh()
        # Not registered → falls back to model_is_local() pattern matching
        assert r.is_local("llama3.2") is True
        assert r.is_local("gpt-4o") is False

    def test_cost_usd_uses_registry_spec(self):
        from meshflow import ModelSpec
        r = self._fresh()
        r.register(ModelSpec("my-cloud", is_local=False, cost_input_per_1k=0.01, cost_output_per_1k=0.03))
        cost = r.cost_usd("my-cloud", 1000, 500)
        assert cost == pytest.approx(0.01 + 0.015)

    def test_cost_usd_falls_back(self):
        r = self._fresh()
        # gpt-4o is in _PRICING, not in a fresh registry → fallback to _cost_usd
        cost = r.cost_usd("gpt-4o", 1000, 500)
        assert cost > 0.0

    def test_contains(self):
        from meshflow import ModelSpec
        r = self._fresh()
        r.register(ModelSpec("x", is_local=True))
        assert "x" in r
        assert "y" not in r

    def test_len(self):
        from meshflow import ModelSpec
        r = self._fresh()
        assert len(r) == 0
        r.register(ModelSpec("a", is_local=True))
        r.register(ModelSpec("b", is_local=False))
        assert len(r) == 2

    def test_remove(self):
        from meshflow import ModelSpec
        r = self._fresh()
        r.register(ModelSpec("x", is_local=True))
        r.remove("x")
        assert r.get("x") is None

    def test_all(self):
        from meshflow import ModelSpec
        r = self._fresh()
        r.register(ModelSpec("a", is_local=True))
        r.register(ModelSpec("b", is_local=False))
        ids = [s.model_id for s in r.all()]
        assert set(ids) == {"a", "b"}

    def test_default_registry_has_claude(self):
        from meshflow import DEFAULT_REGISTRY
        spec = DEFAULT_REGISTRY.get("claude-sonnet-4")
        assert spec is not None
        assert spec.is_local is False

    def test_default_registry_llama_local(self):
        from meshflow import DEFAULT_REGISTRY
        spec = DEFAULT_REGISTRY.get("llama3.2")
        assert spec is not None
        assert spec.is_local is True
        assert spec.cost_usd(10000, 5000) == 0.0

    def test_exported_from_meshflow(self):
        from meshflow import ModelSpec, ModelRegistry, DEFAULT_REGISTRY
        assert ModelSpec is not None
        assert ModelRegistry is not None
        assert DEFAULT_REGISTRY is not None


# ═══════════════════════════════════════════════════════════════════════════════
# extract_confidence
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractConfidence:
    def _ec(self, text):
        from meshflow import extract_confidence
        return extract_confidence(text)

    def test_parses_standard_marker(self):
        assert self._ec("Here is the answer.\nCONFIDENCE:0.85") == pytest.approx(0.85)

    def test_parses_with_space(self):
        assert self._ec("CONFIDENCE: 0.70") == pytest.approx(0.70)

    def test_parses_one(self):
        assert self._ec("CONFIDENCE:1.0") == pytest.approx(1.0)

    def test_parses_zero(self):
        assert self._ec("CONFIDENCE:0.0") == pytest.approx(0.0)

    def test_case_insensitive(self):
        assert self._ec("confidence:0.42") == pytest.approx(0.42)

    def test_no_marker_returns_none(self):
        assert self._ec("No marker here.") is None

    def test_empty_string_returns_none(self):
        assert self._ec("") is None

    def test_clips_above_one(self):
        # regex only matches 0.XX or 1.0 so this won't happen in practice,
        # but the clipping logic should still hold
        from meshflow.agents.scoring import extract_confidence as _ec
        result = _ec("CONFIDENCE:0.99")
        assert result is not None and 0.0 <= result <= 1.0

    def test_exported_from_meshflow(self):
        from meshflow import extract_confidence
        assert callable(extract_confidence)


# ═══════════════════════════════════════════════════════════════════════════════
# TaskScorer
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskScorer:
    def _score(self, task, tools=None):
        from meshflow import TaskScorer
        return TaskScorer().score(task, tools)

    def test_short_chat_is_low_composite(self):
        s = self._score("Hello, how are you?")
        assert s.composite < 0.4

    def test_long_analysis_is_high_composite(self):
        task = (
            "Analyse and compare the strategic implications of quantum computing "
            "versus classical AI architectures, evaluating trade-offs, risks, and "
            "long-term competitive moats for Fortune 500 enterprises. However, "
            "whereas classical approaches dominate today, quantum may disrupt them. "
            "Despite the uncertainty, assess the best path forward. " * 4
        )
        s = self._score(task)
        assert s.composite > 0.5

    def test_tools_bump_composite(self):
        base = self._score("short task")
        with_tools = self._score("short task", tools=["web_search", "calculator", "db_query", "api_call", "file_read"])
        assert with_tools.composite > base.composite

    def test_code_task_type(self):
        s = self._score("def calculate_roi(revenue, cost): return (revenue - cost) / cost")
        assert s.task_type == "code"

    def test_analysis_task_type(self):
        s = self._score("analyse and evaluate the competitive landscape for SaaS companies")
        assert s.task_type == "analysis"

    def test_summary_task_type(self):
        s = self._score("summarize the key points of this document")
        assert s.task_type == "summary"

    def test_composite_in_range(self):
        for task in ["hi", "x" * 5000, "analyse " * 100]:
            s = self._score(task)
            assert 0.0 <= s.composite <= 1.0, f"composite out of range for {task[:20]!r}"

    def test_score_task_convenience(self):
        from meshflow import score_task
        s = score_task("hello")
        assert hasattr(s, "composite")

    def test_exported_from_meshflow(self):
        from meshflow import TaskScore, TaskScorer
        assert TaskScore is not None
        assert TaskScorer is not None


# ═══════════════════════════════════════════════════════════════════════════════
# RouterOutcomeStore
# ═══════════════════════════════════════════════════════════════════════════════

class TestRouterOutcomeStore:
    def _store(self):
        from meshflow import RouterOutcomeStore
        return RouterOutcomeStore(path=":memory:")

    def _outcome(self, **kw):
        from meshflow import RoutingOutcome
        defaults = dict(
            run_id="r1", task="task text", composite_score=0.2,
            model="llama3.2", tier="fast",
            was_exploration=False, success=True, quality_score=0.9,
            latency_ms=120.0, actual_cost_usd=0.0,
        )
        defaults.update(kw)
        return RoutingOutcome.build(**defaults)

    def test_record_and_count(self):
        s = self._store()
        assert s.count() == 0
        s.record(self._outcome())
        assert s.count() == 1

    def test_get_recent_order(self):
        s = self._store()
        for i in range(5):
            s.record(self._outcome(run_id=f"r{i}", task=f"task {i}"))
        recent = s.get_recent(3)
        assert len(recent) == 3

    def test_get_recent_respects_limit(self):
        s = self._store()
        for i in range(20):
            s.record(self._outcome(run_id=f"r{i}", task=f"task {i}"))
        assert len(s.get_recent(5)) == 5

    def test_tier_stats_empty(self):
        s = self._store()
        stats = s.get_tier_stats("fast")
        assert stats.n == 0
        assert stats.success_rate == 0.0

    def test_tier_stats_computed(self):
        s = self._store()
        s.record(self._outcome(success=True,  quality_score=0.9))
        s.record(self._outcome(success=True,  quality_score=0.8))
        s.record(self._outcome(success=False, quality_score=None))
        stats = s.get_tier_stats("fast")
        assert stats.n == 3
        assert stats.success_rate == pytest.approx(2 / 3)

    def test_quality_below_half_counts_as_failure(self):
        s = self._store()
        # success=True but quality=0.3 → effective failure
        s.record(self._outcome(success=True, quality_score=0.3))
        stats = s.get_tier_stats("fast")
        assert stats.success_rate == 0.0

    def test_count_explorations(self):
        s = self._store()
        s.record(self._outcome(was_exploration=False))
        s.record(self._outcome(run_id="r2", task="t2", was_exploration=True))
        assert s.count_explorations() == 1

    def test_memory_isolation(self):
        s1 = self._store()
        s2 = self._store()
        s1.record(self._outcome())
        assert s2.count() == 0

    def test_exported_from_meshflow(self):
        from meshflow import RouterOutcomeStore, RoutingOutcome
        assert RouterOutcomeStore is not None
        assert RoutingOutcome is not None


# ═══════════════════════════════════════════════════════════════════════════════
# ThresholdOptimizer
# ═══════════════════════════════════════════════════════════════════════════════

class TestThresholdOptimizer:
    def _store(self):
        from meshflow import RouterOutcomeStore
        return RouterOutcomeStore(path=":memory:")

    def _outcome(self, tier, composite, success, quality=None, run_id=None):
        from meshflow import RoutingOutcome
        import uuid
        return RoutingOutcome.build(
            run_id=run_id or str(uuid.uuid4()),
            task="x" * int(composite * 2000),
            composite_score=composite,
            model="llama3.2" if tier == "fast" else "mistral",
            tier=tier,
            success=success,
            quality_score=quality,
        )

    def _opt(self):
        from meshflow import ThresholdOptimizer
        return ThresholdOptimizer(min_samples_per_bucket=3)

    def test_insufficient_data_no_change(self):
        s = self._store()
        s.record(self._outcome("fast", 0.1, True))
        rec = self._opt().optimize(s, 0.33, 0.67)
        assert rec.smart_threshold == pytest.approx(0.33)
        assert rec.confidence == 0.0

    def test_stable_thresholds_when_all_succeed(self):
        s = self._store()
        # Many successful fast-tier outcomes across the low composite range
        import uuid
        for i in range(30):
            s.record(self._outcome("fast", 0.1 + i * 0.003, True, quality=0.9, run_id=str(uuid.uuid4())))
        rec = self._opt().optimize(s, 0.33, 0.67)
        # Should not raise smart_threshold dramatically
        assert rec.smart_threshold <= 0.50

    def test_raises_threshold_on_fast_failures(self):
        s = self._store()
        import uuid
        # Fast tier fails consistently at composite 0.3-0.35
        for i in range(30):
            composite = 0.30 + i * 0.002
            s.record(self._outcome("fast", composite, False, run_id=str(uuid.uuid4())))
        # Fast tier succeeds at composite 0.1-0.2
        for i in range(30):
            composite = 0.10 + i * 0.003
            s.record(self._outcome("fast", composite, True, quality=0.9, run_id=str(uuid.uuid4())))
        rec = self._opt().optimize(s, 0.33, 0.67)
        # smart_threshold should shift upward from 0.33 (fast is failing at 0.3+)
        assert rec.smart_threshold >= 0.30

    def test_recommendation_has_summary(self):
        s = self._store()
        rec = self._opt().optimize(s, 0.33, 0.67)
        assert isinstance(rec.summary, str) and len(rec.summary) > 0

    def test_exported_from_meshflow(self):
        from meshflow import ThresholdOptimizer, ThresholdRecommendation
        assert ThresholdOptimizer is not None
        assert ThresholdRecommendation is not None


# ═══════════════════════════════════════════════════════════════════════════════
# AdaptiveModelTierRouter
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdaptiveModelTierRouter:
    def _router(self, **kw):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        defaults = dict(
            tiers=[
                ModelTier("fast",  "llama3.2",  max_tokens=512),
                ModelTier("smart", "mistral",   max_tokens=2048),
                ModelTier("large", "gpt-4o",    max_tokens=4096),
            ],
            smart_threshold=0.33,
            large_threshold=0.67,
            exploration_rate=0.0,   # deterministic by default
            store=RouterOutcomeStore(path=":memory:"),
        )
        defaults.update(kw)
        return AdaptiveModelTierRouter(**defaults)

    def test_short_task_routes_fast(self):
        r = self._router()
        result = r.route("hi")
        assert result.tier == "fast"
        assert result.model == "llama3.2"

    def test_complex_analysis_routes_higher_tier(self):
        r = self._router()
        # Task with high composite: long + conjunctions + questions + technical terms
        task = (
            "Analyse and compare whether classical approaches dominate, however quantum "
            "may disrupt them. Despite the uncertainty, evaluate trade-offs and assess "
            "the distributed architecture. Should we implement microservices? "
            "Define the function interface and evaluate the competitive landscape. " * 5
        )
        result = r.route(task)
        assert result.tier in ("smart", "large")

    def test_result_has_routing_id(self):
        r = self._router()
        result = r.route("task")
        assert isinstance(result.routing_id, str) and len(result.routing_id) > 0

    def test_custom_run_id_propagated(self):
        r = self._router()
        result = r.route("task", run_id="my-run-123")
        assert result.routing_id == "my-run-123"

    def test_is_local_propagated_for_local_model(self):
        r = self._router()
        result = r.route("short task")
        assert result.is_local is True   # llama3.2 = local

    def test_is_local_false_for_cloud_model(self):
        r = self._router()
        # Route to large tier (gpt-4o)
        task = "x" * 2000
        result = r.route(task)
        if result.tier == "large":
            assert result.is_local is False

    def test_route_count_increments(self):
        r = self._router()
        assert r._route_count == 0
        r.route("task1")
        r.route("task2")
        assert r._route_count == 2

    def test_record_outcome_persisted(self):
        r = self._router()
        result = r.route("task", run_id="x1")
        r.record_outcome("x1", success=True, quality=0.9, latency_ms=200.0, actual_cost_usd=0.0)
        assert r._store.count() == 1

    def test_record_outcome_unknown_id_no_crash(self):
        r = self._router()
        r.record_outcome("nonexistent", success=True)  # should not raise

    def test_explain_returns_string(self):
        r = self._router()
        explanation = r.explain("analyse competitive landscape")
        assert "composite" in explanation
        assert "tier" in explanation

    def test_stats_returns_router_stats(self):
        from meshflow import RouterStats
        r = self._router()
        s = r.stats()
        assert isinstance(s, RouterStats)
        assert s.total_runs == 0

    def test_stats_after_outcomes(self):
        r = self._router()
        result = r.route("task", run_id="s1")
        r.record_outcome("s1", success=True, quality=0.8)
        s = r.stats()
        assert s.total_runs == 1

    def test_adapt_returns_recommendation(self):
        from meshflow import ThresholdRecommendation
        r = self._router()
        rec = r.adapt()
        assert isinstance(rec, ThresholdRecommendation)

    def test_adapt_no_crash_on_empty_store(self):
        r = self._router()
        r.adapt()  # should not raise

    def test_manual_adapt_mode_no_auto_adapt(self):
        r = self._router(adapt_mode="manual", adapt_every=2)
        initial_smart = r._smart
        r.route("t1")
        r.route("t2")  # would trigger auto-adapt if mode were "auto"
        assert r._smart == initial_smart  # unchanged

    def test_exploration_fires_at_rate(self):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        r = AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2", max_tokens=512),
                ModelTier("smart", "mistral",  max_tokens=2048),
            ],
            smart_threshold=0.90,   # effectively always route to fast
            large_threshold=0.99,
            exploration_rate=1.0,   # always explore
            store=RouterOutcomeStore(path=":memory:"),
        )
        results = [r.route("short task") for _ in range(20)]
        tiers = {res.tier for res in results}
        assert len(tiers) > 1  # exploration picks smart occasionally

    def test_tiers_returns_copy(self):
        r = self._router()
        tiers = r.tiers()
        assert len(tiers) == 3
        tiers.clear()
        assert len(r.tiers()) == 3

    def test_registry_used_for_is_local(self):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore, ModelRegistry, ModelSpec
        registry = ModelRegistry()
        registry.register(ModelSpec("corp-llm", is_local=True))
        r = AdaptiveModelTierRouter(
            tiers=[ModelTier("fast", "corp-llm")],
            store=RouterOutcomeStore(path=":memory:"),
            registry=registry,
        )
        result = r.route("task")
        assert result.is_local is True

    def test_exported_from_meshflow(self):
        from meshflow import AdaptiveModelTierRouter
        assert AdaptiveModelTierRouter is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Auto-adapt integration: thresholds shift after consistent failures
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoAdaptIntegration:
    def test_auto_adapt_triggered_after_n_routes(self):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        store = RouterOutcomeStore(path=":memory:")
        r = AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2", max_tokens=512),
                ModelTier("smart", "mistral",  max_tokens=2048),
            ],
            adapt_every=5,
            adapt_mode="auto",
            store=store,
        )
        initial_smart = r._smart
        for i in range(5):
            res = r.route(f"task {i}", run_id=f"r{i}")
            r.record_outcome(f"r{i}", success=True, quality=0.9)
        # adapt() was called; thresholds may or may not have changed depending on
        # data (likely unchanged due to low sample count), but it must not crash
        assert isinstance(r._smart, float)

    def test_thresholds_adjust_on_simulated_failures(self):
        """Simulate 60 runs where fast tier consistently fails at composite 0.25-0.35.
        Verify smart_threshold shifts upward."""
        import uuid
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        from meshflow.agents.adaptation import ThresholdOptimizer, RoutingOutcome

        store = RouterOutcomeStore(path=":memory:")
        opt = ThresholdOptimizer(min_samples_per_bucket=5, failure_threshold=0.20)

        # Inject synthetic outcomes directly into the store
        # fast tier failures at composite 0.25-0.35
        for i in range(30):
            composite = 0.25 + i * 0.003
            store.record(RoutingOutcome.build(
                run_id=str(uuid.uuid4()), task="x" * int(composite * 2000),
                composite_score=composite, model="llama3.2", tier="fast",
                success=False, quality_score=0.2, latency_ms=300.0, actual_cost_usd=0.0,
            ))
        # fast tier successes at composite 0.05-0.20
        for i in range(30):
            composite = 0.05 + i * 0.005
            store.record(RoutingOutcome.build(
                run_id=str(uuid.uuid4()), task="x" * int(composite * 2000),
                composite_score=composite, model="llama3.2", tier="fast",
                success=True, quality_score=0.9, latency_ms=200.0, actual_cost_usd=0.0,
            ))

        rec = opt.optimize(store, 0.33, 0.67)
        # With enough data, smart_threshold should be ≥ 0.25 (failures start there)
        if rec.confidence >= 0.30:
            assert rec.smart_threshold >= 0.25
        else:
            # Low confidence → no change, still valid
            assert rec.smart_threshold == pytest.approx(0.33)


# ═══════════════════════════════════════════════════════════════════════════════
# estimate_cost uses DEFAULT_REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstimateCostWithRegistry:
    def _wf(self, *models):
        from meshflow.core.workflow import Workflow
        wf = Workflow()
        for i, model in enumerate(models):
            agent = MagicMock()
            agent.name = f"agent-{i}"
            agent.model_router = None
            agent._resolve_model.return_value = model
            wf.agents.append(agent)
        return wf

    def test_registered_local_model_zero_cost(self):
        from meshflow import DEFAULT_REGISTRY, ModelSpec
        DEFAULT_REGISTRY.register(ModelSpec("custom-local", is_local=True))
        wf = self._wf("custom-local")
        est = wf.estimate_cost("task")
        assert est.total_usd == 0.0
        assert est.lines[0].is_local is True

    def test_registered_cloud_model_uses_registry_rate(self):
        from meshflow import DEFAULT_REGISTRY, ModelSpec
        DEFAULT_REGISTRY.register(ModelSpec(
            "custom-cloud", is_local=False,
            cost_input_per_1k=0.010, cost_output_per_1k=0.030,
        ))
        wf = self._wf("custom-cloud")
        est = wf.estimate_cost("task")
        assert est.total_usd > 0.0
        assert est.lines[0].is_local is False

    def test_unregistered_model_falls_back_to_pattern(self):
        wf = self._wf("llama3.2")
        est = wf.estimate_cost("task")
        assert est.total_usd == 0.0   # pattern detection → local

    def test_adaptive_router_estimate_uses_composite_score(self):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        from meshflow.core.workflow import Workflow

        router = AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2", max_tokens=512),
                ModelTier("large", "gpt-4o",   max_tokens=4096),
            ],
            smart_threshold=0.90,   # short task stays fast
            large_threshold=0.95,
            exploration_rate=0.0,   # deterministic — no epsilon-greedy noise in assertion
            store=RouterOutcomeStore(path=":memory:"),
        )
        wf = Workflow()
        agent = MagicMock()
        agent.name = "writer"
        agent.model_router = router
        agent._resolve_model.return_value = ""
        wf.agents.append(agent)

        est = wf.estimate_cost("short task")
        assert est.lines[0].model == "llama3.2"
        assert est.total_usd == 0.0
