"""Tests for the 4 competitive gap closures verified for v1.0.

Gap 1: python_repl routes through CodeInterpreter (resource-isolated subprocess)
Gap 2: ReplayLedger.diff() / .fork() / .load_state() + CLI flags
Gap 3: ModelRouter wired into Agent.step() — model_override applied per task
Gap 4: ContextCompactor auto-invoked in Agent.step() and AgentSession.chat()
"""

from __future__ import annotations

import asyncio
import datetime
import unittest
from unittest.mock import MagicMock


# ── Gap 1: python_repl sandbox ────────────────────────────────────────────────

class TestPythonReplSandbox(unittest.TestCase):

    def _get_source(self) -> str:
        import inspect
        import meshflow.tools.builtins as b
        # python_repl is a Tool object — unwrap to the underlying function
        fn = getattr(b.python_repl, "fn", b.python_repl)
        return inspect.getsource(fn)

    def test_python_repl_uses_code_interpreter(self) -> None:
        self.assertIn("CodeInterpreter", self._get_source())

    def test_python_repl_no_raw_subprocess_exec(self) -> None:
        self.assertNotIn("create_subprocess_exec", self._get_source())

    def test_python_repl_sets_memory_limit(self) -> None:
        self.assertIn("max_memory_mb", self._get_source())

    def test_python_repl_blocks_network(self) -> None:
        self.assertIn("block_network=True", self._get_source())

    def test_python_repl_returns_output(self) -> None:
        from meshflow.tools.builtins import python_repl
        fn = getattr(python_repl, "fn", python_repl)
        result = asyncio.run(fn("print('hello gap1')"))
        self.assertIn("hello gap1", result)

    def test_python_repl_handles_timeout(self) -> None:
        from meshflow.tools.builtins import python_repl
        fn = getattr(python_repl, "fn", python_repl)
        result = asyncio.run(fn("import time; time.sleep(10)"))
        self.assertIn("timed out", result.lower())

    def test_python_repl_handles_error_gracefully(self) -> None:
        from meshflow.tools.builtins import python_repl
        fn = getattr(python_repl, "fn", python_repl)
        result = asyncio.run(fn("raise ValueError('oops')"))
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


# ── Gap 2: ReplayLedger interactive API ──────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TestReplayLedgerInteractiveAPI(unittest.IsolatedAsyncioTestCase):

    async def _make_ledger_with_runs(self):
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.runtime import StepRecord

        ledger = ReplayLedger(":memory:")

        def _rec(run_id, node_id, output, cost=0.01, tokens=100):
            return StepRecord(
                run_id=run_id, step_id=f"{run_id}-{node_id}-s", node_id=node_id,
                node_kind="llm", input_task="t", output_content=output,
                verdict="approved", blocked=False, block_reason="",
                uncertainty=0.1, cost_usd=cost, tokens_used=tokens,
                carbon_gco2=0.0, duration_ms=50, timestamp=_now(),
            )

        for n in ["fetch", "analyze", "summarize"]:
            await ledger.write(_rec("run-A", n, f"A-{n}", cost=0.01, tokens=100))

        await ledger.write(_rec("run-B", "fetch",      "B-fetch",   cost=0.02, tokens=150))
        await ledger.write(_rec("run-B", "analyze",    "CHANGED",   cost=0.02, tokens=150))
        await ledger.write(_rec("run-B", "summarize",  "B-summary", cost=0.02, tokens=150))
        await ledger.write(_rec("run-B", "extra_node", "B-extra",   cost=0.02, tokens=150))
        return ledger

    async def test_rundiff_dataclass_exists(self) -> None:
        from meshflow.core.ledger import RunDiff
        d = RunDiff(run_id_a="a", run_id_b="b")
        self.assertEqual(d.run_id_a, "a")
        self.assertEqual(d.only_in_a, [])

    async def test_diff_only_in_b(self) -> None:
        ledger = await self._make_ledger_with_runs()
        diff = await ledger.diff("run-A", "run-B")
        self.assertIn("extra_node", diff.only_in_b)
        self.assertEqual(diff.only_in_a, [])

    async def test_diff_changed_nodes(self) -> None:
        ledger = await self._make_ledger_with_runs()
        diff = await ledger.diff("run-A", "run-B")
        self.assertIn("analyze", [c["node_id"] for c in diff.changed])

    async def test_diff_common_nodes(self) -> None:
        ledger = await self._make_ledger_with_runs()
        diff = await ledger.diff("run-A", "run-B")
        for n in ("fetch", "analyze", "summarize"):
            self.assertIn(n, diff.common)

    async def test_diff_positive_cost_delta(self) -> None:
        ledger = await self._make_ledger_with_runs()
        diff = await ledger.diff("run-A", "run-B")
        self.assertGreater(diff.cost_delta_usd, 0)

    async def test_diff_positive_token_delta(self) -> None:
        ledger = await self._make_ledger_with_runs()
        diff = await ledger.diff("run-A", "run-B")
        self.assertGreater(diff.token_delta, 0)

    async def test_fork_returns_new_run_id(self) -> None:
        ledger = await self._make_ledger_with_runs()
        new_id = await ledger.fork("run-A", from_step=2)
        self.assertIsInstance(new_id, str)
        self.assertNotEqual(new_id, "run-A")

    async def test_fork_copies_correct_step_count(self) -> None:
        ledger = await self._make_ledger_with_runs()
        new_id = await ledger.fork("run-A", from_step=2)
        self.assertEqual(len(await ledger.get_run(new_id)), 2)

    async def test_fork_zero_copies_empty(self) -> None:
        ledger = await self._make_ledger_with_runs()
        new_id = await ledger.fork("run-A", from_step=0)
        self.assertEqual(await ledger.get_run(new_id), [])

    async def test_fork_negative_copies_all(self) -> None:
        ledger = await self._make_ledger_with_runs()
        new_id = await ledger.fork("run-A", from_step=-1)
        self.assertEqual(len(await ledger.get_run(new_id)), 3)

    async def test_fork_custom_run_id(self) -> None:
        ledger = await self._make_ledger_with_runs()
        new_id = await ledger.fork("run-A", from_step=1, new_run_id="my-fork")
        self.assertEqual(new_id, "my-fork")

    async def test_load_state_returns_correct_step(self) -> None:
        ledger = await self._make_ledger_with_runs()
        step = await ledger.load_state("run-A", 0)
        self.assertIsNotNone(step)
        self.assertEqual(step["node_id"], "fetch")  # type: ignore[index]

    async def test_load_state_out_of_range_is_none(self) -> None:
        ledger = await self._make_ledger_with_runs()
        self.assertIsNone(await ledger.load_state("run-A", 99))

    async def test_rundiff_in_meshflow_all(self) -> None:
        import meshflow
        self.assertIn("RunDiff", meshflow.__all__)

    def test_replay_cli_diff_flag(self) -> None:
        from meshflow.cli.main import build_parser
        args = build_parser().parse_args(["replay", "run-a", "--diff", "run-b", "--db", ":memory:"])
        self.assertEqual(args.diff, "run-b")

    def test_replay_cli_fork_at_flag(self) -> None:
        from meshflow.cli.main import build_parser
        args = build_parser().parse_args(["replay", "run-a", "--fork-at", "3", "--db", ":memory:"])
        self.assertEqual(args.fork_at, 3)


# ── Gap 3: ModelRouter wired into Agent ──────────────────────────────────────

class TestModelRouterWiring(unittest.IsolatedAsyncioTestCase):

    async def test_agent_has_model_router_field(self) -> None:
        import dataclasses
        from meshflow import Agent
        self.assertIn("model_router", {f.name for f in dataclasses.fields(Agent)})

    async def test_model_router_defaults_to_none(self) -> None:
        from meshflow import Agent
        self.assertIsNone(Agent(name="t", role="executor").model_router)

    async def test_built_agent_carries_router(self) -> None:
        from meshflow import Agent
        from meshflow.agents.model_router import ModelRouter
        router = ModelRouter()
        built = Agent(name="t", role="executor", model_router=router)._build()
        self.assertIs(built._model_router, router)

    async def test_step_calls_route_and_passes_model_override(self) -> None:
        from meshflow import Agent
        from meshflow.agents.model_router import ModelRouter, RoutingDecision

        mock_router = MagicMock(spec=ModelRouter)
        mock_router.route.return_value = RoutingDecision(
            tier="nano", model="claude-haiku-4-5-20251001",
            rationale="simple", token_estimate=50,
        )

        used_models: list[str] = []

        async def fake_think(messages, system=None, model_override=None):
            used_models.append(model_override or "")
            return "ok", 10, 0.001

        built = Agent(name="r", role="executor", model_router=mock_router)._build()
        built.think = fake_think  # type: ignore[method-assign]
        await built.step("classify this", {})

        mock_router.route.assert_called_once()
        self.assertEqual(used_models[0], "claude-haiku-4-5-20251001")

    async def test_step_survives_router_exception(self) -> None:
        from meshflow import Agent
        from meshflow.agents.model_router import ModelRouter

        bad = MagicMock(spec=ModelRouter)
        bad.route.side_effect = RuntimeError("boom")

        async def fake_think(messages, system=None, model_override=None):
            return "safe", 10, 0.001

        built = Agent(name="r2", role="executor", model_router=bad)._build()
        built.think = fake_think  # type: ignore[method-assign]
        result = await built.step("task", {})
        self.assertFalse(result.get("blocked"))
        self.assertEqual(result["result"], "safe")


# ── Gap 4: ContextCompactor auto-invocation ───────────────────────────────────

class TestContextCompactorWiring(unittest.IsolatedAsyncioTestCase):

    async def test_agent_has_context_pruner_field(self) -> None:
        import dataclasses
        from meshflow import Agent
        self.assertIn("context_pruner", {f.name for f in dataclasses.fields(Agent)})

    async def test_context_pruner_defaults_to_none(self) -> None:
        from meshflow import Agent
        self.assertIsNone(Agent(name="t", role="executor").context_pruner)

    async def test_built_agent_carries_pruner(self) -> None:
        from meshflow import Agent, SlidingWindowPruner
        pruner = SlidingWindowPruner(max_messages=5)
        built = Agent(name="t", role="executor", context_pruner=pruner)._build()
        self.assertIs(built._context_pruner, pruner)

    async def test_step_invokes_sliding_window_pruner(self) -> None:
        from meshflow import Agent, SlidingWindowPruner

        calls: list[int] = []

        class SpyPruner(SlidingWindowPruner):
            def prune(self, messages):
                calls.append(len(messages))
                return super().prune(messages)

        built = Agent(name="c", role="executor", context_pruner=SpyPruner(10))._build()

        async def fake_think(messages, system=None, model_override=None):
            return "done", 10, 0.001

        built.think = fake_think  # type: ignore[method-assign]
        await built.step("task", {})
        self.assertGreater(len(calls), 0)

    async def test_step_invokes_summary_pruner(self) -> None:
        from meshflow import Agent, SummaryPruner

        calls: list[int] = []

        class SpySummaryPruner(SummaryPruner):
            async def prune(self, messages):
                calls.append(len(messages))
                return await super().prune(messages)

        built = Agent(name="c2", role="executor",
                      context_pruner=SpySummaryPruner(max_tokens=999999))._build()

        async def fake_think(messages, system=None, model_override=None):
            return "done", 10, 0.001

        built.think = fake_think  # type: ignore[method-assign]
        await built.step("task", {})
        self.assertGreater(len(calls), 0)

    async def test_step_survives_pruner_exception(self) -> None:
        from meshflow import Agent

        class BrokenPruner:
            def prune(self, messages):
                raise RuntimeError("pruner exploded")

        built = Agent(name="safe", role="executor", context_pruner=BrokenPruner())._build()

        async def fake_think(messages, system=None, model_override=None):
            return "safe", 10, 0.001

        built.think = fake_think  # type: ignore[method-assign]
        result = await built.step("task", {})
        self.assertEqual(result["result"], "safe")

    async def test_agent_session_accepts_context_pruner(self) -> None:
        from meshflow import Agent, SlidingWindowPruner
        from meshflow.agents.session import AgentSession
        pruner = SlidingWindowPruner(max_messages=5)
        session = AgentSession(Agent(name="s", role="executor"), context_pruner=pruner)
        self.assertIs(session._context_pruner, pruner)

    async def test_agent_session_invokes_pruner_on_history(self) -> None:
        from meshflow import Agent, SlidingWindowPruner
        from meshflow.agents.session import AgentSession

        calls: list[int] = []

        class SpyPruner(SlidingWindowPruner):
            def prune(self, messages):
                calls.append(len(messages))
                return super().prune(messages)

        agent = Agent(name="sess", role="executor")

        async def fake_run(task, ctx=None):
            return {"result": "reply", "tokens": 5, "cost_usd": 0.001}

        agent.run = fake_run  # type: ignore[method-assign]
        session = AgentSession(agent, context_pruner=SpyPruner(max_messages=10))
        await session.chat("first message")
        await session.chat("second message")   # history present — pruner fires
        self.assertGreater(len(calls), 0)


# ── Public API integration ─────────────────────────────────────────────────────

class TestGapPublicAPI(unittest.TestCase):

    def test_rundiff_exported(self) -> None:
        import meshflow
        self.assertTrue(hasattr(meshflow, "RunDiff"))
        self.assertIn("RunDiff", meshflow.__all__)

    def test_sliding_window_pruner_exported(self) -> None:
        from meshflow import SlidingWindowPruner
        self.assertTrue(callable(SlidingWindowPruner))

    def test_summary_pruner_exported(self) -> None:
        from meshflow import SummaryPruner
        self.assertTrue(callable(SummaryPruner))

    def test_model_router_exported(self) -> None:
        from meshflow import ModelRouter
        self.assertTrue(callable(ModelRouter))

    def test_build_parser_is_callable(self) -> None:
        from meshflow.cli.main import build_parser
        import argparse
        self.assertIsInstance(build_parser(), argparse.ArgumentParser)


if __name__ == "__main__":
    unittest.main()
