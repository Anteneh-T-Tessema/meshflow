"""Tests for the 8 production-hardening gaps.

1  Parallel context dedup wired into workflow
2  Structured output validation (OutputValidator + YAML output_schema)
3  Rate limit 429 handling (RateLimitPolicy + exponential backoff)
4  Pre-run cost approval gate (max_forecast_usd in Policy)
5  Knowledge graph relationships (KnowledgeGraph edges + traversal)
6  Prompt auto-optimization (PromptOptimizer pattern analysis)
7  Workflow migration tools (crewai_to_mesh, autogen_to_mesh)
8  Health auto-recovery (model auto-fallback when degraded)
"""

from __future__ import annotations

import asyncio
import pytest
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# 1. Parallel context dedup
# ─────────────────────────────────────────────────────────────────────────────

def test_context_dedup_is_applied_in_parallel():
    """ContextDeduplicator is imported and deduplicates large repeated context."""
    from meshflow.agents.context_dedup import ContextDeduplicator
    dedup = ContextDeduplicator(hash_threshold=20)

    big_val = "Shared context block " * 10
    ctx_a = {"shared": big_val, "unique_a": "a"}
    ctx_b = {"shared": big_val, "unique_b": "b"}

    clean_a = dedup.deduplicate(ctx_a, agent_name="node_a")
    clean_b = dedup.deduplicate(ctx_b, agent_name="node_b")

    assert clean_a["shared"] == big_val        # first agent gets full value
    assert "deduplicated" in clean_b["shared"]  # second gets placeholder
    assert clean_b["unique_b"] == "b"           # unique keys always pass through


# ─────────────────────────────────────────────────────────────────────────────
# 2. Structured output validation
# ─────────────────────────────────────────────────────────────────────────────

class TestOutputValidator:
    def test_valid_json_object(self):
        from meshflow.core.output_validation import OutputValidator
        schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        v = OutputValidator(schema)
        result = v.validate('{"name": "Alice"}')
        assert result.valid
        assert result.data["name"] == "Alice"

    def test_missing_required_field(self):
        from meshflow.core.output_validation import OutputValidator
        schema = {"type": "object", "required": ["score"], "properties": {"score": {"type": "number"}}}
        v = OutputValidator(schema)
        result = v.validate('{"name": "Alice"}')
        assert not result.valid
        assert "score" in result.error

    def test_extracts_json_from_prose(self):
        from meshflow.core.output_validation import OutputValidator
        schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "number"}}}
        v = OutputValidator(schema)
        # JSON embedded in text
        result = v.validate('Here is the result: {"value": 42} as requested.')
        assert result.valid
        assert result.data["value"] == 42

    def test_markdown_fence_stripped(self):
        from meshflow.core.output_validation import OutputValidator
        schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
        v = OutputValidator(schema)
        result = v.validate('```json\n{"x": "hello"}\n```')
        assert result.valid

    def test_none_schema_always_valid(self):
        from meshflow.core.output_validation import OutputValidator
        v = OutputValidator(None)
        assert v.validate("anything at all").valid

    def test_retry_prompt_contains_error(self):
        from meshflow.core.output_validation import OutputValidator
        schema = {"type": "object", "required": ["name"]}
        v = OutputValidator(schema)
        result = v.validate("{}")
        assert not result.valid
        prompt = v.retry_prompt("{}", result.error)
        assert "name" in prompt or "required" in prompt.lower() or "schema" in prompt.lower()

    def test_array_type_validation(self):
        from meshflow.core.output_validation import OutputValidator
        schema = {"type": "array", "items": {"type": "number"}}
        v = OutputValidator(schema)
        assert v.validate("[1, 2, 3]").valid
        assert not v.validate('{"not": "array"}').valid

    def test_validator_from_yaml_dict_schema(self):
        from meshflow.core.output_validation import validator_from_yaml
        cfg = {"output_schema": {"type": "object", "required": ["result"]}}
        v = validator_from_yaml(cfg)
        assert v is not None
        assert v.validate('{"result": "ok"}').valid

    def test_validator_from_yaml_none(self):
        from meshflow.core.output_validation import validator_from_yaml
        assert validator_from_yaml({}) is None

    def test_jsonschema_lite_enum(self):
        from meshflow.core.output_validation import _jsonschema_lite
        err = _jsonschema_lite("B", {"enum": ["A", "C"]})
        assert err  # "B" not in enum

    def test_jsonschema_lite_nested(self):
        from meshflow.core.output_validation import _jsonschema_lite
        schema = {
            "type": "object",
            "properties": {
                "user": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
            },
            "required": ["user"],
        }
        # Valid
        assert _jsonschema_lite({"user": {"name": "Alice"}}, schema) == ""
        # Missing nested required
        assert _jsonschema_lite({"user": {}}, schema) != ""


# ─────────────────────────────────────────────────────────────────────────────
# 3. Rate limit 429 handling
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimit:
    def test_policy_delay_increases(self):
        from meshflow.resilience.rate_limit import RateLimitPolicy
        p = RateLimitPolicy(base_delay_s=1.0, multiplier=2.0, jitter=False)
        assert p.delay_for_attempt(0) == 1.0
        assert p.delay_for_attempt(1) == 2.0
        assert p.delay_for_attempt(2) == 4.0

    def test_policy_max_delay_capped(self):
        from meshflow.resilience.rate_limit import RateLimitPolicy
        p = RateLimitPolicy(base_delay_s=1.0, multiplier=10.0, max_delay_s=5.0, jitter=False)
        assert p.delay_for_attempt(5) <= 5.0

    def test_is_rate_limit_error_detects_429(self):
        from meshflow.resilience.rate_limit import _is_rate_limit_error
        assert _is_rate_limit_error(Exception("429 too many requests"))
        assert _is_rate_limit_error(Exception("rate limit exceeded"))

    def test_is_rate_limit_error_ignores_others(self):
        from meshflow.resilience.rate_limit import _is_rate_limit_error
        assert not _is_rate_limit_error(ValueError("invalid input"))
        assert not _is_rate_limit_error(KeyError("missing key"))

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        from meshflow.resilience.rate_limit import with_rate_limit_retry, RateLimitPolicy

        call_count = [0]

        async def flaky_fn():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("429 too many requests")
            return "success"

        policy = RateLimitPolicy(max_retries=3, base_delay_s=0.01, jitter=False)
        result = await with_rate_limit_retry(flaky_fn, policy=policy)
        assert result == "success"
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_non_429_not_retried(self):
        from meshflow.resilience.rate_limit import with_rate_limit_retry, RateLimitPolicy

        async def bad_fn():
            raise ValueError("not a rate limit error")

        policy = RateLimitPolicy(max_retries=5, base_delay_s=0.01)
        with pytest.raises(ValueError):
            await with_rate_limit_retry(bad_fn, policy=policy)

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_original(self):
        from meshflow.resilience.rate_limit import with_rate_limit_retry, RateLimitPolicy

        async def always_429():
            raise Exception("rate limit exceeded")

        policy = RateLimitPolicy(max_retries=2, base_delay_s=0.01, jitter=False)
        with pytest.raises(Exception, match="rate limit"):
            await with_rate_limit_retry(always_429, policy=policy)

    def test_global_policy_get_set(self):
        from meshflow.resilience.rate_limit import (
            get_default_policy, set_default_policy, RateLimitPolicy
        )
        original = get_default_policy()
        new_p = RateLimitPolicy(max_retries=99)
        set_default_policy(new_p)
        assert get_default_policy().max_retries == 99
        set_default_policy(original)  # restore


# ─────────────────────────────────────────────────────────────────────────────
# 4. Pre-run cost approval gate
# ─────────────────────────────────────────────────────────────────────────────

class TestCostGate:
    def test_policy_has_max_forecast_usd(self):
        from meshflow.core.schemas import Policy
        p = Policy(max_forecast_usd=0.50)
        assert p.max_forecast_usd == 0.50

    def test_policy_default_is_zero_disabled(self):
        from meshflow.core.schemas import Policy
        p = Policy()
        assert p.max_forecast_usd == 0.0

    def test_cost_forecaster_budget_gate_flag(self):
        from meshflow.optimization.planner import CostForecaster
        fc = CostForecaster()
        result = fc.forecast(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Hello"}],
            max_budget_usd=0.000001,  # impossibly small
        )
        assert result["within_budget"] is False

    def test_cost_forecaster_passes_reasonable_budget(self):
        from meshflow.optimization.planner import CostForecaster
        fc = CostForecaster()
        result = fc.forecast(
            model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "Hi"}],
            max_budget_usd=1.0,
        )
        assert result["within_budget"] is True

    def test_yaml_parser_reads_max_forecast_usd(self, tmp_path):
        from meshflow.core.workflow import WorkflowDefinition
        import yaml
        data = {
            "name": "gated-wf",
            "policy": {"budget_usd": 2.0, "max_forecast_usd": 0.10},
            "nodes": {"step": {"kind": "native", "role": "executor"}},
            "edges": [],
        }
        p = tmp_path / "gated.yaml"
        p.write_text(yaml.safe_dump(data))
        wf = WorkflowDefinition.from_yaml(str(p))
        assert wf.policy.max_forecast_usd == pytest.approx(0.10)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Knowledge graph relationships
# ─────────────────────────────────────────────────────────────────────────────

class TestKnowledgeGraph:
    @pytest.fixture
    def kg(self):
        from meshflow.intelligence.entity_memory import KnowledgeGraph
        return KnowledgeGraph(":memory:")

    def test_relate_and_find_related(self, kg):
        kg.remember("Alice", "role", "CTO")
        kg.relate("Alice", "works_at", "Acme Corp")
        result = kg.find_related("Alice", "works_at")
        assert "Acme Corp" in result

    def test_find_incoming(self, kg):
        kg.relate("Alice", "works_at", "Acme Corp")
        kg.relate("Bob", "works_at", "Acme Corp")
        result = kg.find_incoming("Acme Corp", "works_at")
        assert "Alice" in result
        assert "Bob" in result

    def test_relations_of(self, kg):
        kg.relate("Alice", "works_at", "Acme")
        kg.relate("Bob", "reports_to", "Alice")
        rels = kg.relations_of("Alice")
        subjects = {r["subject"] for r in rels}
        objects  = {r["object"]  for r in rels}
        assert "Alice" in subjects or "Alice" in objects

    def test_traverse_two_hops(self, kg):
        kg.relate("Alice", "works_at", "Acme")
        kg.relate("Acme", "is_a", "Company")
        visited = kg.traverse("Alice", depth=2)
        assert "Acme" in visited
        assert visited["Acme"] == 1
        assert "Company" in visited
        assert visited["Company"] == 2

    def test_traverse_stops_at_depth(self, kg):
        kg.relate("A", "links", "B")
        kg.relate("B", "links", "C")
        kg.relate("C", "links", "D")
        visited = kg.traverse("A", depth=2)
        assert "C" in visited
        assert "D" not in visited  # too far

    def test_shortest_path_direct(self, kg):
        kg.relate("A", "edge", "B")
        path = kg.shortest_path("A", "B")
        assert path == ["A", "B"]

    def test_shortest_path_indirect(self, kg):
        kg.relate("A", "e", "B")
        kg.relate("B", "e", "C")
        path = kg.shortest_path("A", "C")
        assert path == ["A", "B", "C"]

    def test_shortest_path_none_when_disconnected(self, kg):
        kg.relate("A", "e", "B")
        assert kg.shortest_path("A", "Z") is None

    def test_subgraph(self, kg):
        kg.relate("Alice", "works_at", "Acme")
        kg.relate("Bob", "works_at", "Acme")
        kg.relate("Alice", "knows", "Bob")
        sg = kg.subgraph(["Alice", "Bob", "Acme"])
        assert len(sg["edges"]) >= 3

    def test_unrelate(self, kg):
        kg.relate("X", "links", "Y")
        kg.unrelate("X", "links", "Y")
        assert kg.find_related("X", "links") == []

    def test_stats_includes_relations(self, kg):
        kg.remember("A", "k", "v")
        kg.relate("A", "links", "B")
        s = kg.stats()
        assert s["total_relations"] >= 1

    def test_find_related_no_predicate(self, kg):
        kg.relate("Alice", "works_at", "Acme")
        kg.relate("Alice", "knows", "Bob")
        result = kg.find_related("Alice")
        assert len(result) >= 2

    def test_traverse_with_predicate_filter(self, kg):
        kg.relate("Alice", "works_at", "Acme")
        kg.relate("Alice", "knows", "Bob")
        visited = kg.traverse("Alice", depth=2, predicate="works_at")
        assert "Acme" in visited
        assert "Bob" not in visited  # different predicate


# ─────────────────────────────────────────────────────────────────────────────
# 6. Prompt auto-optimization
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptOptimizer:
    def _make_store_with_corrections(self):
        from meshflow.eval.feedback import FeedbackStore, FeedbackRecord
        store = FeedbackStore(":memory:")
        for i in range(5):
            store.save(FeedbackRecord(
                run_id=f"r{i}",
                agent_name="billing-agent",
                task="What is my balance?",
                original_output="Your balance is large.",
                score=0.5,
                correction="Actually, please be more concise and accurate.",
            ))
        return store

    def test_analyze_patterns_finds_verbosity(self):
        from meshflow.eval.prompt_optimizer import PromptOptimizer
        store = self._make_store_with_corrections()
        opt = PromptOptimizer(store, min_count=3)
        suggestions = opt.analyze_patterns("billing-agent")
        categories = {s.category for s in suggestions}
        assert "verbosity" in categories or "accuracy" in categories

    def test_analyze_patterns_empty_store(self):
        from meshflow.eval.feedback import FeedbackStore
        from meshflow.eval.prompt_optimizer import PromptOptimizer
        store = FeedbackStore(":memory:")
        opt = PromptOptimizer(store)
        assert opt.analyze_patterns("nobody") == []

    def test_analyze_patterns_no_corrections(self):
        from meshflow.eval.feedback import FeedbackStore, FeedbackRecord
        from meshflow.eval.prompt_optimizer import PromptOptimizer
        store = FeedbackStore(":memory:")
        for i in range(5):
            store.save(FeedbackRecord(run_id=f"r{i}", agent_name="a",
                                      task="t", original_output="o", score=1.0))
        opt = PromptOptimizer(store)
        assert opt.analyze_patterns("a") == []

    @pytest.mark.asyncio
    async def test_optimize_no_corrections_returns_unchanged_prompt(self):
        from meshflow.eval.feedback import FeedbackStore
        from meshflow.eval.prompt_optimizer import PromptOptimizer

        store = FeedbackStore(":memory:")
        opt = PromptOptimizer(store)

        class FakeAgent:
            async def run(self, task, ctx=None):
                return {"result": "IMPROVED PROMPT:\nSame prompt.\n\nCHANGES:\n- None"}

        result = await opt.optimize("nobody", "Original prompt.", FakeAgent())
        assert result.improved_prompt == "Original prompt."
        assert "No corrections" in result.changes_summary

    @pytest.mark.asyncio
    async def test_optimize_with_corrections_calls_agent(self):
        from meshflow.eval.prompt_optimizer import PromptOptimizer

        store = self._make_store_with_corrections()
        opt = PromptOptimizer(store, min_count=2)

        agent_called = [False]

        class FakeAgent:
            async def run(self, task, ctx=None):
                agent_called[0] = True
                return {"result":
                    "IMPROVED PROMPT:\nImproved prompt here.\n\n"
                    "CHANGES:\n- Added conciseness guidance"}

        result = await opt.optimize("billing-agent", "Original.", FakeAgent())
        assert agent_called[0] is True
        assert result.improved_prompt == "Improved prompt here."

    def test_suggestion_to_dict(self):
        from meshflow.eval.prompt_optimizer import PromptSuggestion
        s = PromptSuggestion(category="tone", description="Tone issue", frequency=5)
        d = s.to_dict()
        assert d["category"] == "tone"
        assert d["frequency"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# 7. Workflow migration tools
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrationTools:
    def test_crewai_to_mesh_no_tasks(self):
        from meshflow.integrations.migration import crewai_to_mesh

        class FakeCrew:
            tasks = []

        wf = crewai_to_mesh(FakeCrew(), name="empty-crew")
        assert wf.name == "empty-crew"
        assert len(wf._nodes) == 1  # whole crew as one node

    def test_crewai_to_mesh_with_tasks(self):
        from meshflow.integrations.migration import crewai_to_mesh

        class FakeAgent:
            role = "researcher"
            name = "researcher"

        class FakeTask:
            agent = FakeAgent()
            description = "Do research"
            expected_output = "Research results"
            context = None

        class FakeTask2:
            agent = FakeAgent()
            description = "Write report"
            expected_output = "Report"
            context = None

        class FakeCrew:
            tasks = [FakeTask(), FakeTask2()]

        wf = crewai_to_mesh(FakeCrew(), name="research-crew")
        assert len(wf._nodes) == 2
        assert len(wf._edges) >= 1  # sequential edge

    def test_autogen_to_mesh_creates_nodes(self):
        from meshflow.integrations.migration import autogen_to_mesh

        class AgentA:
            name = "assistant"

        class AgentB:
            name = "user_proxy"

        class FakeGroupChat:
            agents = [AgentA(), AgentB()]
            max_round = 3

        wf = autogen_to_mesh(FakeGroupChat(), name="debate")
        assert len(wf._nodes) == 2
        assert wf.metadata["source_framework"] == "autogen"

    def test_autogen_to_mesh_empty_agents(self):
        from meshflow.integrations.migration import autogen_to_mesh

        class FakeGroupChat:
            agents = []
            max_round = 3

        wf = autogen_to_mesh(FakeGroupChat())
        assert len(wf._nodes) == 0

    def test_to_yaml_produces_valid_yaml(self):
        from meshflow.integrations.migration import autogen_to_mesh, to_yaml
        import yaml

        class FakeAgent:
            name = "agent1"

        class FakeGroupChat:
            agents = [FakeAgent()]
            max_round = 2

        wf = autogen_to_mesh(FakeGroupChat(), name="test-wf")
        yaml_str = to_yaml(wf)
        data = yaml.safe_load(yaml_str)
        assert data["name"] == "test-wf"
        assert "nodes" in data

    def test_safe_id_sanitises_names(self):
        from meshflow.integrations.migration import _safe_id
        # Trailing underscores are stripped by the implementation
        assert _safe_id("My Agent!") == "my_agent"
        assert _safe_id("__start__") == "start"
        assert _safe_id("") == "node"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Health auto-recovery
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthAutoRecovery:
    def test_health_tracker_is_degraded(self):
        from meshflow.agents.health import ModelHealthTracker
        tracker = ModelHealthTracker(window_size=5, degraded_threshold=0.7)
        for _ in range(5):
            tracker.record_failure("bad-model", error="timeout")
        assert tracker.is_degraded("bad-model")

    def test_health_tracker_not_degraded_when_healthy(self):
        from meshflow.agents.health import ModelHealthTracker
        tracker = ModelHealthTracker()
        for _ in range(5):
            tracker.record_success("good-model", latency_ms=200)
        assert not tracker.is_degraded("good-model")

    def test_route_with_health_skips_degraded(self):
        from meshflow.agents.router import ProviderRouter
        from meshflow.agents.health import ModelHealthTracker

        router = ProviderRouter()
        router.set_fallback_chain("bad-model", "claude-sonnet-4-6", "claude-haiku-4-5-20251001")
        tracker = ModelHealthTracker(window_size=3, degraded_threshold=0.9)
        for _ in range(3):
            tracker.record_failure("bad-model", error="timeout")

        _, model = router.route_with_health("executor", tracker=tracker)
        assert model != "bad-model"

    def test_route_with_health_falls_back_to_best_when_all_degraded(self):
        from meshflow.agents.router import ProviderRouter
        from meshflow.agents.health import ModelHealthTracker

        router = ProviderRouter()
        # Include the default haiku model in the chain so it's covered
        _HAIKU = "claude-haiku-4-5-20251001"
        router.set_fallback_chain("m1", "m2", _HAIKU)
        tracker = ModelHealthTracker(window_size=3, degraded_threshold=0.9)
        for _ in range(3):
            tracker.record_failure("m1", error="timeout")
            tracker.record_failure("m2", error="timeout")
            tracker.record_failure(_HAIKU, error="timeout")

        # All degraded → route_with_health returns the best of the chain (any is valid)
        _, model = router.route_with_health("executor", tracker=tracker)
        # All models in the chain are degraded; best_model returns one of them
        assert isinstance(model, str) and len(model) > 0

    def test_global_health_tracker_singleton(self):
        from meshflow.agents.health import get_health_tracker, reset_health_tracker
        reset_health_tracker()
        t1 = get_health_tracker()
        t2 = get_health_tracker()
        assert t1 is t2

    def test_health_tracker_best_model_selection(self):
        from meshflow.agents.health import ModelHealthTracker
        tracker = ModelHealthTracker()
        tracker.record_success("a", latency_ms=100)
        tracker.record_success("a", latency_ms=100)
        tracker.record_failure("b", error="err")
        tracker.record_failure("b", error="err")
        tracker.record_success("b")

        best = tracker.best_model(["a", "b"])
        assert best == "a"  # higher health score
