"""Sprint 74 — Gap closure tests.

Covers every item promoted from RED/ORANGE to GREEN:
  1.  Public API exports (__all__)
  2.  Cloud managed identity providers (AzureIdentityProvider, BedrockIAMProvider, VertexAIProvider)
  3.  Marketplace HTTP registry (MarketplaceClient + MarketplaceServer round-trip)
  4.  Docker isolation code interpreter (flag wiring & graceful fail)
  5.  Agent debate loops (DebatePanel)
  6.  Dynamic model switching (AdaptiveAgent)
  7.  Early exit guardrail (EarlyExitAgent)
  8.  Parallel context dedup (ContextDeduplicator)
  9.  Model sizing + token budget planner (TokenBudgetPlanner, ModelSizingAdvisor)
  10. Time-travel RewindEngine (struct + db path)
  11. Cost/Quality Pareto (ParetoAnalyzer)
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import tempfile
import time
import unittest
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Public API exports
# ─────────────────────────────────────────────────────────────────────────────

class TestPublicAPIExports(unittest.TestCase):

    _EXPECTED = [
        "AdaptiveAgent", "DebatePanel", "DebateNode", "DebateResult",
        "EarlyExitAgent", "ContextDeduplicator",
        "TokenBudgetPlanner", "ModelSizingAdvisor",
        "RewindEngine", "RewindResult", "StepSnapshot",
        "ParetoAnalyzer", "ModelBenchmark", "BenchmarkRun",
        "AzureIdentityProvider", "BedrockIAMProvider", "VertexAIProvider",
        "AgentTemplate", "TemplateRegistry", "MarketplaceClient", "MarketplaceServer",
    ]

    def test_all_symbols_in_dunder_all(self) -> None:
        import meshflow
        missing = [s for s in self._EXPECTED if s not in meshflow.__all__]
        self.assertEqual(missing, [], f"Missing from __all__: {missing}")

    def test_all_symbols_importable(self) -> None:
        import meshflow
        for name in self._EXPECTED:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(meshflow, name, None), f"{name} not on meshflow")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Cloud managed identity providers
# ─────────────────────────────────────────────────────────────────────────────

class TestAzureIdentityProvider(unittest.TestCase):

    def test_instantiation_stores_endpoint(self) -> None:
        from meshflow.agents.providers import AzureIdentityProvider
        p = AzureIdentityProvider(endpoint="https://test.openai.azure.com/")
        self.assertEqual(p._endpoint, "https://test.openai.azure.com/")

    def test_no_static_api_key(self) -> None:
        from meshflow.agents.providers import AzureIdentityProvider
        p = AzureIdentityProvider()
        self.assertEqual(p._api_key, "")

    def test_inherits_azure_openai_provider(self) -> None:
        from meshflow.agents.providers import AzureIdentityProvider, AzureOpenAIProvider
        self.assertTrue(issubclass(AzureIdentityProvider, AzureOpenAIProvider))

    def test_get_token_raises_import_error_without_sdk(self) -> None:
        import sys
        from meshflow.agents.providers import AzureIdentityProvider
        p = AzureIdentityProvider(endpoint="https://x.openai.azure.com/")
        saved = sys.modules.get("azure.identity")
        sys.modules["azure.identity"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises((ImportError, AttributeError)):
                p._get_token()
        finally:
            if saved is None:
                sys.modules.pop("azure.identity", None)
            else:
                sys.modules["azure.identity"] = saved


class TestBedrockIAMProvider(unittest.TestCase):

    def test_instantiation_defaults(self) -> None:
        from meshflow.agents.providers import BedrockIAMProvider
        p = BedrockIAMProvider()
        self.assertEqual(p._role_arn, "")
        self.assertEqual(p._profile_name, "")
        self.assertEqual(p._session_name, "meshflow-session")

    def test_role_arn_stored(self) -> None:
        from meshflow.agents.providers import BedrockIAMProvider
        arn = "arn:aws:iam::123456789012:role/MeshFlowRole"
        p = BedrockIAMProvider(role_arn=arn)
        self.assertEqual(p._role_arn, arn)

    def test_profile_name_stored(self) -> None:
        from meshflow.agents.providers import BedrockIAMProvider
        p = BedrockIAMProvider(profile_name="prod-readonly")
        self.assertEqual(p._profile_name, "prod-readonly")

    def test_inherits_bedrock_provider(self) -> None:
        from meshflow.agents.providers import BedrockIAMProvider, BedrockProvider
        self.assertTrue(issubclass(BedrockIAMProvider, BedrockProvider))

    def test_missing_boto3_raises(self) -> None:
        import sys
        from meshflow.agents.providers import BedrockIAMProvider
        p = BedrockIAMProvider()
        saved = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises((ImportError, AttributeError)):
                p._client()
        finally:
            if saved is None:
                sys.modules.pop("boto3", None)
            else:
                sys.modules["boto3"] = saved


class TestVertexAIProvider(unittest.TestCase):

    def test_instantiation_project(self) -> None:
        from meshflow.agents.providers import VertexAIProvider
        p = VertexAIProvider(project="my-project", location="us-central1")
        self.assertEqual(p._project, "my-project")
        self.assertEqual(p._location, "us-central1")

    def test_env_var_project_fallback(self) -> None:
        import os
        from meshflow.agents.providers import VertexAIProvider
        os.environ["GOOGLE_CLOUD_PROJECT"] = "env-project"
        try:
            p = VertexAIProvider()
            self.assertEqual(p._project, "env-project")
        finally:
            os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

    def test_complete_with_tools_falls_back_to_complete(self) -> None:
        import sys
        from meshflow.agents.providers import VertexAIProvider
        p = VertexAIProvider(project="test")
        saved = sys.modules.get("vertexai")
        sys.modules["vertexai"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises((ImportError, AttributeError, TypeError)):
                _run(p.complete_with_tools("gemini-flash", [], "sys", 100, [], {}))
        finally:
            if saved is None:
                sys.modules.pop("vertexai", None)
            else:
                sys.modules["vertexai"] = saved

    def test_default_model_set(self) -> None:
        from meshflow.agents.providers import VertexAIProvider
        p = VertexAIProvider()
        self.assertIn("gemini", p._model)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Marketplace HTTP registry round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketplaceServerClient(unittest.TestCase):

    def setUp(self) -> None:
        from meshflow.registry.templates import AgentTemplate, MarketplaceServer, TemplateRegistry
        from meshflow.registry.templates import MarketplaceClient
        self._port = _free_port()
        self._tmpdir = tempfile.mkdtemp()
        self._server = MarketplaceServer(
            registry_dir=self._tmpdir,
            port=self._port,
            host="127.0.0.1",
        )
        self._server.start(daemon=True)
        time.sleep(0.08)
        self._client = MarketplaceClient(f"http://127.0.0.1:{self._port}")
        self._tmpl = AgentTemplate(
            name="test-researcher",
            role="researcher",
            model="claude-haiku-4-5-20251001",
            description="A test researcher agent for marketplace round-trip.",
            tags=["test", "research"],
        )

    def tearDown(self) -> None:
        self._server.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_push_and_list(self) -> None:
        self._client.push(self._tmpl)
        items = self._client.list_all()
        names = [i["name"] for i in items]
        self.assertIn("test-researcher", names)

    def test_push_and_pull_round_trip(self) -> None:
        self._client.push(self._tmpl)
        pulled = self._client.pull("test-researcher")
        self.assertEqual(pulled.name, "test-researcher")
        self.assertEqual(pulled.role, "researcher")

    def test_pull_missing_raises_runtime_error(self) -> None:
        with self.assertRaises(RuntimeError):
            self._client.pull("nonexistent-template")

    def test_search_returns_matching(self) -> None:
        self._client.push(self._tmpl)
        results = self._client.search("research")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].name, "test-researcher")

    def test_server_url(self) -> None:
        self.assertEqual(self._server.url(), f"http://127.0.0.1:{self._port}")

    def test_push_multiple_and_list_all(self) -> None:
        from meshflow.registry.templates import AgentTemplate
        for i in range(3):
            self._client.push(AgentTemplate(
                name=f"agent-{i}", role="executor",
                description=f"Agent number {i}", tags=[],
            ))
        items = self._client.list_all()
        self.assertEqual(len(items), 3)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Docker code interpreter
# ─────────────────────────────────────────────────────────────────────────────

class TestDockerCodeInterpreter(unittest.TestCase):

    def test_docker_flag_stored(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter(docker=True, docker_image="python:3.11-slim")
        self.assertTrue(ci.docker)
        self.assertEqual(ci.docker_image, "python:3.11-slim")

    def test_docker_false_by_default(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        self.assertFalse(CodeInterpreter().docker)

    def test_run_docker_method_exists_and_callable(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        self.assertTrue(callable(getattr(CodeInterpreter, "_run_docker", None)))

    def test_subprocess_execution_works(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        result = CodeInterpreter(docker=False).run("x = 6 * 7\nprint(x)")
        self.assertIn("42", str(result))

    def test_docker_graceful_fail_without_daemon(self) -> None:
        if shutil.which("docker"):
            self.skipTest("Docker installed — skip graceful-fail test")
        from meshflow.tools.code_interpreter import CodeInterpreter
        result = CodeInterpreter(docker=True).run("print('hello')")
        self.assertIsNotNone(result)
        self.assertFalse(result.success)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Agent debate loops (DebatePanel)
# ─────────────────────────────────────────────────────────────────────────────

def _echo_agent(name: str, reply: str = "answer") -> Any:
    from meshflow.agents.base import EchoProvider
    from meshflow import Agent
    return Agent(name=name, role="executor", provider=EchoProvider(reply))


class TestDebatePanel(unittest.TestCase):

    def test_requires_at_least_two_debaters(self) -> None:
        from meshflow.agents.debate import DebatePanel
        with self.assertRaises(ValueError):
            DebatePanel(debaters=[_echo_agent("solo")])

    def test_debate_returns_debate_result(self) -> None:
        from meshflow.agents.debate import DebatePanel, DebateResult
        panel = DebatePanel(debaters=[_echo_agent("a"), _echo_agent("b")], max_rounds=1)
        result = _run(panel.debate("Should MeshFlow support Go?"))
        self.assertIsInstance(result, DebateResult)

    def test_verdict_is_valid_string(self) -> None:
        from meshflow.agents.debate import DebatePanel
        panel = DebatePanel(debaters=[_echo_agent("x"), _echo_agent("y")], max_rounds=1)
        result = _run(panel.debate("Test"))
        valid = {"unanimous", "majority", "arbiter", "tie", "no_consensus"}
        self.assertIn(result.verdict, valid)

    def test_debate_tree_populated(self) -> None:
        from meshflow.agents.debate import DebatePanel
        panel = DebatePanel(
            debaters=[_echo_agent("p"), _echo_agent("q"), _echo_agent("r")],
            max_rounds=1,
        )
        result = _run(panel.debate("Multi-debater test"))
        self.assertGreater(len(result.tree), 0)

    def test_debate_with_arbiter(self) -> None:
        from meshflow.agents.debate import DebatePanel, DebateResult
        panel = DebatePanel(
            debaters=[_echo_agent("d1"), _echo_agent("d2")],
            arbiter=_echo_agent("arb", "verdict"),
            max_rounds=1,
            confidence_threshold=0.0,
        )
        result = _run(panel.debate("Arbiter test"))
        self.assertIsInstance(result, DebateResult)

    def test_result_to_dict_has_required_keys(self) -> None:
        from meshflow.agents.debate import DebatePanel
        panel = DebatePanel(debaters=[_echo_agent("a"), _echo_agent("b")], max_rounds=1)
        result = _run(panel.debate("Dict test"))
        d = result.to_dict()
        for key in ("verdict", "confidence", "rounds"):
            self.assertIn(key, d)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Dynamic model switching (AdaptiveAgent)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdaptiveAgent(unittest.TestCase):

    def test_simple_task_complexity(self) -> None:
        from meshflow.agents.adaptive import _task_complexity
        self.assertEqual(_task_complexity("What is 2+2?"), "simple")

    def test_complex_task_complexity(self) -> None:
        from meshflow.agents.adaptive import _task_complexity
        self.assertEqual(_task_complexity("Audit this HIPAA compliance document in detail"), "complex")

    def test_medium_task_complexity(self) -> None:
        from meshflow.agents.adaptive import _task_complexity
        result = _task_complexity("Explain the difference between supervised and unsupervised learning")
        self.assertIn(result, ("simple", "medium", "complex"))

    def test_run_simple_task_returns_answer(self) -> None:
        from meshflow.agents.adaptive import AdaptiveAgent
        agent = AdaptiveAgent(
            _echo_agent("base", "42"),
            cheap_model="claude-haiku-4-5-20251001",
            expensive_model="claude-sonnet-4-6",
        )
        result = _run(agent.run("What is 6*7?"))
        self.assertIn("42", str(result))

    def test_run_returns_non_empty(self) -> None:
        from meshflow.agents.adaptive import AdaptiveAgent
        agent = AdaptiveAgent(
            _echo_agent("base", "done"),
            cheap_model="claude-haiku-4-5-20251001",
            expensive_model="claude-sonnet-4-6",
            downgrade_on_simple=True,
        )
        result = _run(agent.run("List top 3 fruits"))
        self.assertIsNotNone(result)
        self.assertTrue(bool(str(result)))


# ─────────────────────────────────────────────────────────────────────────────
# 7. Early exit guardrail (EarlyExitAgent)
# ─────────────────────────────────────────────────────────────────────────────

class TestEarlyExitAgent(unittest.TestCase):

    def test_exits_returns_turns_key(self) -> None:
        from meshflow.agents.early_exit import EarlyExitAgent
        agent = EarlyExitAgent(_echo_agent("ea", "sure"), confidence_threshold=0.0, max_turns=3)
        result = _run(agent.run("Tell me something."))
        self.assertIn("_turns", result)

    def test_turns_never_exceeds_max(self) -> None:
        from meshflow.agents.early_exit import EarlyExitAgent
        agent = EarlyExitAgent(_echo_agent("eb", "low"), confidence_threshold=1.1, max_turns=2)
        result = _run(agent.run("Uncertain task"))
        self.assertLessEqual(result["_turns"], 2)

    def test_result_has_confidence_key(self) -> None:
        from meshflow.agents.early_exit import EarlyExitAgent
        agent = EarlyExitAgent(_echo_agent("ec", "done"), confidence_threshold=0.5, max_turns=1)
        result = _run(agent.run("Task"))
        self.assertIn("_confidence", result)

    def test_result_key(self) -> None:
        from meshflow.agents.early_exit import EarlyExitAgent
        agent = EarlyExitAgent(_echo_agent("ed", "final answer"), confidence_threshold=0.0, max_turns=1)
        result = _run(agent.run("Q"))
        self.assertIn("result", result)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Parallel context deduplication (ContextDeduplicator)
# ─────────────────────────────────────────────────────────────────────────────

class TestContextDeduplicator(unittest.TestCase):

    def test_identical_values_stubbed_on_second_call(self) -> None:
        from meshflow.agents.context_dedup import ContextDeduplicator
        dedup = ContextDeduplicator(hash_threshold=5)
        ctx1 = {"task": "This is a large shared context block for dedup testing."}
        ctx2 = {"task": "This is a large shared context block for dedup testing."}
        dedup.deduplicate(ctx1, agent_name="agent-a")
        result2 = dedup.deduplicate(ctx2, agent_name="agent-b")
        # Second call: value replaced with a dedup stub
        self.assertIn("deduplicated", result2["task"])

    def test_unique_values_passed_through(self) -> None:
        from meshflow.agents.context_dedup import ContextDeduplicator
        dedup = ContextDeduplicator(hash_threshold=5)
        r = dedup.deduplicate({"a": "unique alpha", "b": "unique beta"})
        self.assertEqual(r["a"], "unique alpha")
        self.assertEqual(r["b"], "unique beta")

    def test_short_values_below_threshold_never_deduplicated(self) -> None:
        from meshflow.agents.context_dedup import ContextDeduplicator
        dedup = ContextDeduplicator(hash_threshold=100)
        ctx = {"k": "short"}
        r1 = dedup.deduplicate(ctx)
        r2 = dedup.deduplicate(ctx)
        self.assertEqual(r1["k"], "short")
        self.assertEqual(r2["k"], "short")

    def test_seen_count_increments(self) -> None:
        from meshflow.agents.context_dedup import ContextDeduplicator
        dedup = ContextDeduplicator(hash_threshold=1)
        dedup.deduplicate({"x": "hello world"})
        self.assertGreaterEqual(dedup.seen_count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 9. TokenBudgetPlanner + ModelSizingAdvisor
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenBudgetPlanner(unittest.TestCase):

    def test_estimate_tokens_nonempty(self) -> None:
        from meshflow.optimization.planner import TokenBudgetPlanner
        self.assertGreater(TokenBudgetPlanner.estimate_tokens("Hello world, this is a test."), 0)

    def test_estimate_tokens_empty(self) -> None:
        from meshflow.optimization.planner import TokenBudgetPlanner
        self.assertEqual(TokenBudgetPlanner.estimate_tokens(""), 0)

    def test_plan_budget_has_system_and_message_tokens(self) -> None:
        from meshflow.optimization.planner import TokenBudgetPlanner
        result = TokenBudgetPlanner().plan_budget(
            system_prompt="You are helpful.",
            messages=[{"role": "user", "content": "What is HIPAA?"}],
        )
        self.assertIn("system_tokens", result)
        self.assertIn("message_tokens", result)

    def test_plan_budget_total_key_present(self) -> None:
        from meshflow.optimization.planner import TokenBudgetPlanner
        result = TokenBudgetPlanner().plan_budget(
            system_prompt="sys", messages=[{"role": "user", "content": "hi"}]
        )
        # Key is total_estimated_in (not total_input_tokens)
        self.assertIn("total_estimated_in", result)
        self.assertGreater(result["total_estimated_in"], 0)

    def test_plan_budget_with_tools_increases_total(self) -> None:
        from meshflow.optimization.planner import TokenBudgetPlanner
        base = TokenBudgetPlanner().plan_budget("sys", [{"role": "user", "content": "q"}])
        with_tools = TokenBudgetPlanner().plan_budget(
            "sys", [{"role": "user", "content": "q"}],
            tools=[{"name": "search", "description": "a web search tool for queries"}],
        )
        self.assertGreaterEqual(with_tools["total_estimated_in"], base["total_estimated_in"])


class TestModelSizingAdvisor(unittest.TestCase):

    def test_recommends_haiku_tier_for_simple_task(self) -> None:
        from meshflow.optimization.planner import ModelSizingAdvisor
        model = ModelSizingAdvisor().recommend_model("What is 2+2?")
        self.assertIn("haiku", model.lower())

    def test_recommends_higher_tier_for_complex_task(self) -> None:
        from meshflow.optimization.planner import ModelSizingAdvisor
        model = ModelSizingAdvisor().recommend_model(
            "Audit this HIPAA compliance document for all violations"
        )
        self.assertNotIn("haiku", model.lower())

    def test_many_tools_triggers_high_tier(self) -> None:
        from meshflow.optimization.planner import ModelSizingAdvisor
        tools = [{"name": f"t{i}"} for i in range(3)]
        model = ModelSizingAdvisor().recommend_model("simple task", tools=tools)
        self.assertEqual(model, ModelSizingAdvisor.HIGH_TIER)

    def test_returns_string(self) -> None:
        from meshflow.optimization.planner import ModelSizingAdvisor
        result = ModelSizingAdvisor().recommend_model("List 5 colors")
        self.assertIsInstance(result, str)
        self.assertTrue(result)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Time-travel RewindEngine
# ─────────────────────────────────────────────────────────────────────────────

class TestRewindEngine(unittest.TestCase):

    def test_db_path_stored(self) -> None:
        from meshflow.core.time_travel import RewindEngine
        self.assertEqual(RewindEngine("my.db")._db, "my.db")

    def test_list_steps_returns_empty_for_missing_run(self) -> None:
        from meshflow.core.time_travel import RewindEngine
        engine = RewindEngine(":memory:")
        steps = _run(engine.list_steps("nonexistent-run-id"))
        self.assertEqual(steps, [])

    def test_step_snapshot_dataclass(self) -> None:
        from meshflow.core.time_travel import StepSnapshot
        s = StepSnapshot(
            idx=0, step_id="s1", node_id="node_a", node_kind="native",
            ok=True, blocked=False, cost_usd=0.001, tokens_used=100,
            duration_ms=50.0, uncertainty=0.1,
            output_preview="hello world", timestamp="2025-01-01T00:00:00",
        )
        self.assertEqual(s.node_id, "node_a")
        self.assertTrue(s.ok)

    def test_rewind_result_dataclass(self) -> None:
        from meshflow.core.time_travel import RewindResult
        r = RewindResult(
            original_run_id="orig", rewind_run_id="fork",
            rewound_to_step=2, model_override="haiku",
            prompt_override="be concise", output="done",
            completed=True, steps_replayed=3,
            total_cost_usd=0.002, total_tokens=200,
        )
        self.assertEqual(r.rewound_to_step, 2)
        self.assertTrue(r.completed)
        self.assertEqual(r.model_override, "haiku")


# ─────────────────────────────────────────────────────────────────────────────
# 11. Cost/Quality Pareto view (ParetoAnalyzer)
# ─────────────────────────────────────────────────────────────────────────────

class TestParetoAnalyzer(unittest.TestCase):

    def _bench(self) -> Any:
        from meshflow.eval.pareto import ModelBenchmark
        b = ModelBenchmark()
        b.add_run("claude-opus-4-8",          tokens=8200, cost_usd=0.123, pass_rate=0.96)
        b.add_run("claude-sonnet-4-6",         tokens=6100, cost_usd=0.024, pass_rate=0.92)
        b.add_run("claude-haiku-4-5-20251001", tokens=4800, cost_usd=0.004, pass_rate=0.81)
        return b

    def test_frontier_non_empty(self) -> None:
        from meshflow.eval.pareto import ParetoAnalyzer
        frontier = ParetoAnalyzer(self._bench()).pareto_frontier()
        self.assertGreater(len(frontier), 0)

    def test_haiku_on_frontier(self) -> None:
        from meshflow.eval.pareto import ParetoAnalyzer
        frontier = ParetoAnalyzer(self._bench()).pareto_frontier()
        self.assertIn("claude-haiku-4-5-20251001", [r.model for r in frontier])

    def test_comparison_table_contains_all_models(self) -> None:
        from meshflow.eval.pareto import ParetoAnalyzer
        table = ParetoAnalyzer(self._bench()).comparison_table()
        for m in ("claude-opus-4-8", "claude-haiku-4-5-20251001"):
            self.assertIn(m, table)

    def test_dominated_model_excluded(self) -> None:
        from meshflow.eval.pareto import ModelBenchmark, ParetoAnalyzer
        b = ModelBenchmark()
        b.add_run("cheap-good",    tokens=100, cost_usd=0.001, pass_rate=0.90)
        b.add_run("expensive-bad", tokens=200, cost_usd=0.100, pass_rate=0.50)
        frontier = ParetoAnalyzer(b).pareto_frontier()
        self.assertNotIn("expensive-bad", [r.model for r in frontier])

    def test_benchmark_run_fields(self) -> None:
        from meshflow.eval.pareto import BenchmarkRun
        r = BenchmarkRun(model="test-model", tokens=100, cost_usd=0.01, pass_rate=0.9)
        self.assertEqual(r.model, "test-model")
        self.assertEqual(r.tokens, 100)


if __name__ == "__main__":
    unittest.main()
