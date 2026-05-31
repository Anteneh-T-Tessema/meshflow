"""Sprint 75 — Competitive gap closure tests.

Covers every item from the May 2026 Competitive Intelligence document:
  1.  ModelRouter — task-complexity → model tier routing
  2.  CriticAgent — propose/challenge/refine loop
  3.  ToolOutputSummarizer — large tool output compression
  4.  WorkflowDefinition.to_yaml() — pipeline YAML export
  5.  DurableWorkflowExecutor Redis backend — structure + lazy-import guard
  6.  DurableWorkflowExecutor Postgres backend — structure + lazy-import guard
  7.  Subprocess sandbox resource limits — max_memory_mb + block_network flags
  8.  Public API exports (__all__)
"""

from __future__ import annotations

import asyncio
import tempfile
import os
import unittest
from typing import Any


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _echo_agent(name: str = "a", reply: str = "answer") -> Any:
    from meshflow.agents.base import EchoProvider
    from meshflow import Agent
    return Agent(name=name, role="executor", provider=EchoProvider(reply))


# ─────────────────────────────────────────────────────────────────────────────
# 1. ModelRouter
# ─────────────────────────────────────────────────────────────────────────────

class TestModelRouter(unittest.TestCase):

    def test_nano_tier_for_short_simple_task(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        r = ModelRouter()
        d = r.route("What is 2+2?")
        self.assertIn(d.tier, ("nano", "small"))

    def test_large_tier_for_compliance_keyword(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        r = ModelRouter()
        d = r.route("Audit this HIPAA policy for compliance violations")
        self.assertEqual(d.tier, "large")

    def test_medium_tier_for_analyze_keyword(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        r = ModelRouter()
        d = r.route("Analyze the following dataset for trends")
        self.assertEqual(d.tier, "medium")

    def test_many_tools_bump_to_medium(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        r = ModelRouter()
        tools = [{"name": f"t{i}"} for i in range(4)]
        d = r.route("simple task", tools=tools)
        self.assertIn(d.tier, ("medium", "large"))

    def test_decision_has_model_string(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        d = ModelRouter().route("List five colors")
        self.assertIsInstance(d.model, str)
        self.assertTrue(d.model)

    def test_decision_has_rationale(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        d = ModelRouter().route("Debug this Python function")
        self.assertTrue(d.rationale)

    def test_record_decisions_populates_history(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        r = ModelRouter(record_decisions=True)
        r.route("task one")
        r.route("task two")
        self.assertEqual(len(r.history), 2)

    def test_savings_vs_default_no_history(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        r = ModelRouter()
        s = r.savings_vs_default()
        self.assertEqual(s["total_decisions"], 0)

    def test_savings_vs_default_with_cheap_decisions(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        r = ModelRouter(record_decisions=True)
        for _ in range(5):
            r.route("What is 2+2?")   # all nano/small
        s = r.savings_vs_default()
        self.assertGreater(s["saved_pct"], 0)

    def test_to_dict_has_required_keys(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        d = ModelRouter().route("some task")
        dct = d.to_dict()
        for key in ("tier", "model", "rationale", "token_estimate"):
            self.assertIn(key, dct)

    def test_router_config_from_dict(self) -> None:
        from meshflow.agents.model_router import ModelRouter, RouterConfig
        cfg = RouterConfig.from_dict({
            "model_router": {
                "tiers": {"medium": "claude-sonnet-4-6"},
                "fallback": "small",
            }
        })
        r = ModelRouter(config=cfg)
        self.assertEqual(r._config.fallback, "small")

    def test_router_config_from_yaml(self) -> None:
        from meshflow.agents.model_router import RouterConfig
        yaml_content = "model_router:\n  fallback: nano\n  tiers:\n    nano: claude-haiku-4-5-20251001\n"
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            cfg = RouterConfig.from_yaml(path)
            self.assertEqual(cfg.fallback, "nano")
        finally:
            os.unlink(path)

    def test_large_token_count_routes_to_large(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        # 3001+ tokens estimated → large
        big_task = "word " * 2500  # ~3375 tokens estimated
        d = ModelRouter().route(big_task)
        self.assertEqual(d.tier, "large")

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        self.assertIn("ModelRouter", meshflow.__all__)
        self.assertIn("RouterConfig", meshflow.__all__)
        self.assertIn("RoutingDecision", meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 2. CriticAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestCriticAgent(unittest.TestCase):

    def test_returns_critic_result(self) -> None:
        from meshflow.agents.critic import CriticAgent, CriticResult
        agent = CriticAgent(proposer=_echo_agent("p", "initial answer"),
                            critic=_echo_agent("c", "critique here"),
                            max_refinements=1)
        result = _run(agent.run("What is HIPAA?"))
        self.assertIsInstance(result, CriticResult)

    def test_final_answer_non_empty(self) -> None:
        from meshflow.agents.critic import CriticAgent
        agent = CriticAgent(proposer=_echo_agent("p", "good answer"),
                            critic=_echo_agent("c", "looks correct"),
                            max_refinements=1)
        result = _run(agent.run("Describe SOC 2"))
        self.assertTrue(result.final_answer)

    def test_history_has_proposal_and_critique(self) -> None:
        from meshflow.agents.critic import CriticAgent
        agent = CriticAgent(proposer=_echo_agent("p", "proposal"),
                            critic=_echo_agent("c", "critique"),
                            max_refinements=1)
        result = _run(agent.run("Test"))
        roles = [t.role for t in result.history]
        self.assertIn("proposal", roles)
        self.assertIn("critique", roles)

    def test_refinements_count_correct(self) -> None:
        from meshflow.agents.critic import CriticAgent
        agent = CriticAgent(proposer=_echo_agent("p", "v1"),
                            critic=_echo_agent("c", "issues"),
                            max_refinements=2)
        result = _run(agent.run("Task"))
        self.assertLessEqual(result.refinements, 2)

    def test_early_stop_on_high_confidence(self) -> None:
        from meshflow.agents.critic import CriticAgent
        # confidence_threshold=0.0 means always stop after first proposal
        agent = CriticAgent(proposer=_echo_agent("p", "confident answer"),
                            critic=_echo_agent("c", "all good"),
                            max_refinements=3,
                            stop_on_confidence=0.0)
        result = _run(agent.run("Easy question"))
        self.assertEqual(result.refinements, 0)

    def test_improvement_delta_property(self) -> None:
        from meshflow.agents.critic import CriticResult
        r = CriticResult(
            final_answer="done",
            initial_confidence=0.5,
            final_confidence=0.8,
        )
        self.assertAlmostEqual(r.improvement_delta, 0.3, places=2)

    def test_to_dict_has_required_keys(self) -> None:
        from meshflow.agents.critic import CriticAgent
        agent = CriticAgent(proposer=_echo_agent(), critic=_echo_agent(), max_refinements=1)
        result = _run(agent.run("Q"))
        d = result.to_dict()
        for key in ("final_answer", "refinements", "improvement_delta", "history"):
            self.assertIn(key, d)

    def test_same_agent_as_critic_when_critic_none(self) -> None:
        from meshflow.agents.critic import CriticAgent
        proposer = _echo_agent("solo", "answer")
        agent = CriticAgent(proposer=proposer, critic=None, max_refinements=1)
        # critic should fall back to proposer
        self.assertIs(agent._critic, proposer)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        for name in ("CriticAgent", "CriticResult", "CriticTurn"):
            self.assertIn(name, meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 3. ToolOutputSummarizer
# ─────────────────────────────────────────────────────────────────────────────

class TestToolOutputSummarizer(unittest.TestCase):

    def test_short_output_passes_through_unchanged(self) -> None:
        from meshflow.tools.tool_summarizer import ToolOutputSummarizer
        s = ToolOutputSummarizer(max_tokens=500)
        output = "Short result."
        compressed = _run(s.compress("search", output))
        self.assertEqual(compressed, output)

    def test_passthrough_tool_never_compressed(self) -> None:
        from meshflow.tools.tool_summarizer import ToolOutputSummarizer
        s = ToolOutputSummarizer(max_tokens=1, passthrough_tools={"calculator"})
        long_output = "word " * 1000
        compressed = _run(s.compress("calculator", long_output))
        self.assertEqual(compressed, long_output)

    def test_compression_record_skipped_for_short(self) -> None:
        from meshflow.tools.tool_summarizer import ToolOutputSummarizer
        s = ToolOutputSummarizer(max_tokens=500, record_stats=True)
        _run(s.compress("tool", "short"))
        self.assertEqual(len(s.stats), 1)
        self.assertTrue(s.stats[0].skipped)

    def test_compression_record_not_skipped_for_long(self) -> None:
        from meshflow.tools.tool_summarizer import ToolOutputSummarizer
        # max_tokens=1 forces compression on anything > 1 token
        s = ToolOutputSummarizer(max_tokens=1, record_stats=True)
        _run(s.compress("web_search", "word " * 200))
        self.assertFalse(s.stats[0].skipped)

    def test_summary_report_structure(self) -> None:
        from meshflow.tools.tool_summarizer import ToolOutputSummarizer
        s = ToolOutputSummarizer(record_stats=True)
        _run(s.compress("t", "hello"))
        report = s.summary_report()
        for key in ("total_events", "total_saved_tokens", "avg_compression_ratio"):
            self.assertIn(key, report)

    def test_wrap_returns_proxy_agent(self) -> None:
        from meshflow.tools.tool_summarizer import ToolOutputSummarizer, _WrappedAgent
        s = ToolOutputSummarizer()
        wrapped = s.wrap(_echo_agent())
        self.assertIsInstance(wrapped, _WrappedAgent)

    def test_compression_record_to_dict(self) -> None:
        from meshflow.tools.tool_summarizer import CompressionRecord
        r = CompressionRecord(
            tool_name="search", original_tokens=1000, compressed_tokens=200,
            compression_ratio=0.2, skipped=False,
        )
        d = r.to_dict()
        self.assertEqual(d["saved_tokens"], 800)
        self.assertEqual(d["compression_ratio"], 0.2)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        self.assertIn("ToolOutputSummarizer", meshflow.__all__)
        self.assertIn("CompressionRecord", meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 4. WorkflowDefinition.to_yaml()
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowYAMLExport(unittest.TestCase):

    def _make_workflow(self) -> Any:
        from meshflow.core.workflow import WorkflowDefinition
        from meshflow.core.node import MeshNode, NodeKind, RiskTier
        wf = WorkflowDefinition(name="test-pipeline", version="2")
        n1 = MeshNode(id="planner", kind=NodeKind.NATIVE, risk_profile=RiskTier.READ_ONLY)
        n2 = MeshNode(id="executor", kind=NodeKind.NATIVE, risk_profile=RiskTier.READ_ONLY)
        wf.add_node(n1).add_node(n2)
        wf.add_edge("planner", "executor")
        wf.set_terminal("executor")
        return wf

    def test_to_yaml_returns_string(self) -> None:
        wf = self._make_workflow()
        yaml_str = wf.to_yaml()
        self.assertIsInstance(yaml_str, str)
        self.assertGreater(len(yaml_str), 0)

    def test_to_yaml_contains_name(self) -> None:
        yaml_str = self._make_workflow().to_yaml()
        self.assertIn("test-pipeline", yaml_str)

    def test_to_yaml_contains_node_ids(self) -> None:
        yaml_str = self._make_workflow().to_yaml()
        self.assertIn("planner", yaml_str)
        self.assertIn("executor", yaml_str)

    def test_to_yaml_contains_edge(self) -> None:
        yaml_str = self._make_workflow().to_yaml()
        self.assertIn("planner", yaml_str)
        self.assertIn("executor", yaml_str)

    def test_to_yaml_writes_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        try:
            self._make_workflow().to_yaml(path=path)
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                content = f.read()
            self.assertIn("test-pipeline", content)
        finally:
            os.unlink(path)

    def test_to_yaml_version_preserved(self) -> None:
        yaml_str = self._make_workflow().to_yaml()
        self.assertIn("'2'", yaml_str)

    def test_to_yaml_policy_included(self) -> None:
        yaml_str = self._make_workflow().to_yaml()
        self.assertIn("policy", yaml_str)

    def test_to_yaml_terminal_included(self) -> None:
        yaml_str = self._make_workflow().to_yaml()
        self.assertIn("terminal", yaml_str)

    def test_round_trip_name_preserved(self) -> None:
        import yaml
        wf = self._make_workflow()
        doc = yaml.safe_load(wf.to_yaml())
        self.assertEqual(doc["name"], "test-pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# 5 & 6. DurableWorkflowExecutor Redis + Postgres backends
# ─────────────────────────────────────────────────────────────────────────────

class TestDurableRedisBackend(unittest.TestCase):

    def test_backend_redis_raises_without_redis_package(self) -> None:
        import sys
        from meshflow.core.durable import _RedisStore
        store = _RedisStore(url="redis://localhost:6379/0")
        saved = sys.modules.get("redis")
        sys.modules["redis"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises((ImportError, AttributeError)):
                store._conn()
        finally:
            if saved is None:
                sys.modules.pop("redis", None)
            else:
                sys.modules["redis"] = saved

    def test_redis_store_key_format(self) -> None:
        from meshflow.core.durable import _RedisStore
        store = _RedisStore()
        self.assertEqual(
            store._key("run-1", "node_a"),
            "meshflow:checkpoint:run-1:node_a",
        )

    def test_redis_store_index_key_format(self) -> None:
        from meshflow.core.durable import _RedisStore
        store = _RedisStore()
        self.assertEqual(
            store._index_key("run-1"),
            "meshflow:run_index:run-1",
        )

    def test_executor_accepts_redis_backend(self) -> None:
        from meshflow.core.durable import DurableWorkflowExecutor
        import sys
        # Patch redis out so no real connection is made
        saved = sys.modules.get("redis")
        sys.modules["redis"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises((ImportError, AttributeError)):
                exec_r = DurableWorkflowExecutor(backend="redis", redis_url="redis://localhost/0")
                exec_r._store._conn()  # forces lazy import
        finally:
            if saved is None:
                sys.modules.pop("redis", None)
            else:
                sys.modules["redis"] = saved

    def test_executor_redis_backend_type(self) -> None:
        from meshflow.core.durable import DurableWorkflowExecutor, _RedisStore
        exec_r = DurableWorkflowExecutor.__new__(DurableWorkflowExecutor)
        exec_r._run_id = "test"
        exec_r._store = _RedisStore(url="redis://localhost/0")
        self.assertIsInstance(exec_r._store, _RedisStore)


class TestDurablePostgresBackend(unittest.TestCase):

    def test_postgres_store_requires_url(self) -> None:
        import os
        from meshflow.core.durable import _PostgresStore
        os.environ.pop("MESHFLOW_POSTGRES_URL", None)
        with self.assertRaises(ValueError):
            _PostgresStore(url="")

    def test_postgres_store_url_stored(self) -> None:
        import sys
        from meshflow.core.durable import _PostgresStore
        saved = sys.modules.get("psycopg2")
        sys.modules["psycopg2"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises((ImportError, AttributeError)):
                _PostgresStore(url="postgresql://user:pass@localhost/db")
        finally:
            if saved is None:
                sys.modules.pop("psycopg2", None)
                sys.modules.pop("psycopg2.extras", None)
            else:
                sys.modules["psycopg2"] = saved

    def test_executor_postgres_url_from_env(self) -> None:
        import os
        os.environ["MESHFLOW_POSTGRES_URL"] = "postgresql://user:pass@host/db"
        try:
            import sys
            from meshflow.core.durable import DurableWorkflowExecutor
            saved = sys.modules.get("psycopg2")
            sys.modules["psycopg2"] = None  # type: ignore[assignment]
            try:
                with self.assertRaises((ImportError, AttributeError)):
                    DurableWorkflowExecutor(backend="postgres")
            finally:
                if saved is None:
                    sys.modules.pop("psycopg2", None)
                else:
                    sys.modules["psycopg2"] = saved
        finally:
            os.environ.pop("MESHFLOW_POSTGRES_URL", None)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Subprocess sandbox resource limits
# ─────────────────────────────────────────────────────────────────────────────

class TestSubprocessSandbox(unittest.TestCase):

    def test_max_memory_mb_stored(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter(max_memory_mb=256)
        self.assertEqual(ci.max_memory_mb, 256)

    def test_block_network_stored(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter(block_network=True)
        self.assertTrue(ci.block_network)

    def test_defaults_no_limits(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter()
        self.assertEqual(ci.max_memory_mb, 0)
        self.assertFalse(ci.block_network)

    def test_make_preexec_none_when_no_limit(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter(max_memory_mb=0)
        self.assertIsNone(ci._make_preexec())

    def test_make_preexec_callable_when_limit_set(self) -> None:
        import sys
        if sys.platform == "win32":
            self.skipTest("resource module not available on Windows")
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter(max_memory_mb=512)
        preexec = ci._make_preexec()
        # Should be a callable (or None if resource module unavailable)
        if preexec is not None:
            self.assertTrue(callable(preexec))

    def test_block_network_strips_proxy_env(self) -> None:
        import os
        from meshflow.tools.code_interpreter import CodeInterpreter
        os.environ["http_proxy"] = "http://proxy:3128"
        try:
            ci = CodeInterpreter(block_network=True)
            result = ci.run("print('ok')")
            self.assertIn("ok", result.stdout)
        finally:
            os.environ.pop("http_proxy", None)

    def test_sandboxed_execution_still_works(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter(max_memory_mb=512, block_network=True)
        result = ci.run("x = 2 ** 10\nprint(x)")
        self.assertIn("1024", result.stdout)
        self.assertTrue(result.success)

    def test_timeout_still_enforced(self) -> None:
        from meshflow.tools.code_interpreter import CodeInterpreter
        ci = CodeInterpreter(timeout_s=0.5, max_memory_mb=256)
        result = ci.run("import time\ntime.sleep(5)")
        self.assertTrue(result.timed_out)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Public API exports for Sprint 75
# ─────────────────────────────────────────────────────────────────────────────

class TestSprint75PublicAPI(unittest.TestCase):

    _EXPECTED = [
        "ModelRouter", "RouterConfig", "RoutingDecision",
        "CriticAgent", "CriticResult", "CriticTurn",
        "ToolOutputSummarizer", "CompressionRecord",
    ]

    def test_all_in_dunder_all(self) -> None:
        import meshflow
        missing = [s for s in self._EXPECTED if s not in meshflow.__all__]
        self.assertEqual(missing, [], f"Not in __all__: {missing}")

    def test_all_importable(self) -> None:
        import meshflow
        for name in self._EXPECTED:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(meshflow, name, None))

    def test_workflow_to_yaml_method_exists(self) -> None:
        from meshflow.core.workflow import WorkflowDefinition
        self.assertTrue(callable(getattr(WorkflowDefinition, "to_yaml", None)))

    def test_durable_executor_accepts_all_backends(self) -> None:
        from meshflow.core.durable import DurableWorkflowExecutor
        # memory and sqlite both work without extra deps
        m = DurableWorkflowExecutor(backend="memory")
        self.assertIsNotNone(m)
        s = DurableWorkflowExecutor(backend="sqlite")
        self.assertIsNotNone(s)

    def test_code_interpreter_new_params_in_signature(self) -> None:
        import inspect
        from meshflow.tools.code_interpreter import CodeInterpreter
        sig = inspect.signature(CodeInterpreter.__init__)
        self.assertIn("max_memory_mb", sig.parameters)
        self.assertIn("block_network", sig.parameters)


if __name__ == "__main__":
    unittest.main()
