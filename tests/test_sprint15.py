"""Sprint 15 — Eval baseline/CI regression, AgentPool, Plugin system.

Tests:
  A. EvalBaseline: from_result, save/load, diff, regressions, improvements
  B. Ledger eval storage: save_eval_result / list_eval_results
  C. AgentPool: submit, map, stats, stop, context-manager, round-robin
  D. Plugin system: discover (empty), PluginInfo, verify_plugin error paths
  E. Server /pool/status route registered
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_scenario_result(name: str, passed: bool, score: float, tokens: int = 10):
    from meshflow.eval.runner import ScenarioResult

    return ScenarioResult(
        scenario_name=name,
        passed=passed,
        score=score,
        checks={"c1": passed},
        output="output",
        tokens=tokens,
        confidence=0.9,
        duration_ms=50.0,
    )


def _make_eval_result(
    suite_name: str = "my_suite",
    pass_rate: float = 1.0,
    scenarios: list | None = None,
):
    from meshflow.eval.runner import EvalResult

    if scenarios is None:
        scenarios = [_make_scenario_result("s1", True, 1.0)]
    passed = sum(1 for s in scenarios if s.passed)
    return EvalResult(
        suite_name=suite_name,
        total=len(scenarios),
        passed=passed,
        failed=len(scenarios) - passed,
        errors=0,
        pass_rate=passed / max(len(scenarios), 1),
        weighted_score=sum(s.score for s in scenarios) / max(len(scenarios), 1),
        total_tokens=sum(s.tokens for s in scenarios),
        total_cost_usd=0.0,
        duration_s=0.1,
        scenarios=scenarios,
    )


# ─────────────────────────────────────────────────────────────────────────────
# A. EvalBaseline
# ─────────────────────────────────────────────────────────────────────────────


class TestEvalBaseline:
    def test_from_result_captures_all_scenarios(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        result = _make_eval_result(scenarios=[
            _make_scenario_result("qa", True, 1.0),
            _make_scenario_result("code", False, 0.5),
        ])
        baseline = EvalBaseline.from_result(result)
        assert baseline.suite_name == "my_suite"
        assert "qa" in baseline.scenarios
        assert "code" in baseline.scenarios
        assert baseline.scenarios["qa"].passed is True
        assert baseline.scenarios["code"].passed is False

    def test_to_dict_round_trips(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        result = _make_eval_result()
        baseline = EvalBaseline.from_result(result)
        d = baseline.to_dict()
        assert d["suite_name"] == "my_suite"
        assert "scenarios" in d
        assert isinstance(d["scenarios"], dict)

    def test_save_and_load(self, tmp_path) -> None:
        from meshflow.eval.baseline import EvalBaseline

        result = _make_eval_result(scenarios=[
            _make_scenario_result("s1", True, 0.9, tokens=20),
        ])
        baseline = EvalBaseline.from_result(result)
        path = tmp_path / "baseline.json"
        baseline.save(path)
        assert path.exists()

        loaded = EvalBaseline.load(path)
        assert loaded.suite_name == baseline.suite_name
        assert loaded.pass_rate == baseline.pass_rate
        assert "s1" in loaded.scenarios
        assert loaded.scenarios["s1"].tokens == 20

    def test_save_creates_parent_dirs(self, tmp_path) -> None:
        from meshflow.eval.baseline import EvalBaseline

        result = _make_eval_result()
        baseline = EvalBaseline.from_result(result)
        nested = tmp_path / "deep" / "dir" / "baseline.json"
        baseline.save(nested)
        assert nested.exists()

    def test_diff_no_change(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        result = _make_eval_result(scenarios=[_make_scenario_result("s1", True, 1.0)])
        b1 = EvalBaseline.from_result(result)
        b2 = EvalBaseline.from_result(result)
        diff = b1.diff(b2)
        assert not diff.has_regressions
        assert diff.regressions == []
        assert diff.improvements == []

    def test_diff_detects_regression(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        old = _make_eval_result(scenarios=[
            _make_scenario_result("s1", True, 1.0),
            _make_scenario_result("s2", True, 1.0),
        ])
        new = _make_eval_result(scenarios=[
            _make_scenario_result("s1", True, 1.0),
            _make_scenario_result("s2", False, 0.3),  # regression
        ])
        b_old = EvalBaseline.from_result(old)
        b_new = EvalBaseline.from_result(new)
        diff = b_old.diff(b_new)
        assert diff.has_regressions
        assert "s2" in diff.regressions
        assert diff.pass_rate_delta < 0

    def test_diff_detects_improvement(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        old = _make_eval_result(scenarios=[_make_scenario_result("s1", False, 0.3)])
        new = _make_eval_result(scenarios=[_make_scenario_result("s1", True, 1.0)])
        diff = EvalBaseline.from_result(old).diff(EvalBaseline.from_result(new))
        assert not diff.has_regressions
        assert "s1" in diff.improvements

    def test_diff_new_scenario_tracked(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        old = _make_eval_result(scenarios=[_make_scenario_result("s1", True, 1.0)])
        new = _make_eval_result(scenarios=[
            _make_scenario_result("s1", True, 1.0),
            _make_scenario_result("s2_new", True, 1.0),
        ])
        diff = EvalBaseline.from_result(old).diff(EvalBaseline.from_result(new))
        assert "s2_new" in diff.new_scenarios

    def test_diff_removed_scenario_tracked(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        old = _make_eval_result(scenarios=[
            _make_scenario_result("s1", True, 1.0),
            _make_scenario_result("old_only", True, 1.0),
        ])
        new = _make_eval_result(scenarios=[_make_scenario_result("s1", True, 1.0)])
        diff = EvalBaseline.from_result(old).diff(EvalBaseline.from_result(new))
        assert "old_only" in diff.removed_scenarios

    def test_report_contains_verdict(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        old = _make_eval_result(scenarios=[_make_scenario_result("s1", True, 1.0)])
        new = _make_eval_result(scenarios=[_make_scenario_result("s1", False, 0.0)])
        diff = EvalBaseline.from_result(old).diff(EvalBaseline.from_result(new))
        report = diff.report()
        assert "REGRESSION" in report
        assert "s1" in report

    def test_report_ok_when_no_regressions(self) -> None:
        from meshflow.eval.baseline import EvalBaseline

        result = _make_eval_result()
        diff = EvalBaseline.from_result(result).diff(EvalBaseline.from_result(result))
        assert "OK" in diff.report()


# ─────────────────────────────────────────────────────────────────────────────
# B. Ledger eval storage
# ─────────────────────────────────────────────────────────────────────────────


class TestLedgerEvalStorage:
    @pytest.mark.asyncio
    async def test_save_and_list_eval_result(self) -> None:
        from meshflow.core.ledger import ReplayLedger

        ledger = ReplayLedger(":memory:")
        result = _make_eval_result(suite_name="smoke")
        key = await ledger.save_eval_result(result)
        assert key.startswith("eval:smoke:")

        stored = await ledger.list_eval_results()
        assert len(stored) == 1
        assert stored[0]["suite_name"] == "smoke"
        assert stored[0]["storage_key"] == key

    @pytest.mark.asyncio
    async def test_filter_by_suite_name(self) -> None:
        from meshflow.core.ledger import ReplayLedger

        ledger = ReplayLedger(":memory:")
        await ledger.save_eval_result(_make_eval_result(suite_name="suite_a"))
        await ledger.save_eval_result(_make_eval_result(suite_name="suite_b"))

        a_results = await ledger.list_eval_results(suite_name="suite_a")
        assert all(r["suite_name"] == "suite_a" for r in a_results)
        assert len(a_results) == 1

    @pytest.mark.asyncio
    async def test_stored_result_has_scenarios(self) -> None:
        from meshflow.core.ledger import ReplayLedger

        ledger = ReplayLedger(":memory:")
        result = _make_eval_result(scenarios=[
            _make_scenario_result("t1", True, 1.0),
            _make_scenario_result("t2", False, 0.5),
        ])
        await ledger.save_eval_result(result)
        stored = await ledger.list_eval_results()
        scenarios = stored[0]["scenarios"]
        assert "t1" in scenarios
        assert "t2" in scenarios


# ─────────────────────────────────────────────────────────────────────────────
# C. AgentPool
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_agent(name: str = "agent", cost: float = 0.0, tokens: int = 0) -> MagicMock:
    agent = MagicMock()
    agent.name = name

    async def run(task, **kwargs):
        return MagicMock(total_cost_usd=cost, total_tokens=tokens, output=f"[{name}] {task}")

    agent.run = run
    return agent


class TestAgentPool:
    def test_empty_agents_raises(self) -> None:
        from meshflow.agents.pool import AgentPool

        with pytest.raises(ValueError, match="at least one agent"):
            AgentPool(agents=[])

    def test_concurrency_zero_raises(self) -> None:
        from meshflow.agents.pool import AgentPool

        with pytest.raises(ValueError, match="concurrency"):
            AgentPool(agents=[_make_mock_agent()], concurrency=0)

    @pytest.mark.asyncio
    async def test_submit_returns_result(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent("a1")], concurrency=2)
        async with pool:
            result = await pool.submit("do something")
        assert result is not None

    @pytest.mark.asyncio
    async def test_map_returns_all_results(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=4)
        tasks = ["task 1", "task 2", "task 3"]
        async with pool:
            results = await pool.map(tasks)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_stats_submitted_increments(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=2)
        async with pool:
            await pool.submit("t1")
            await pool.submit("t2")
        assert pool.stats.total_submitted == 2
        assert pool.stats.total_completed == 2

    @pytest.mark.asyncio
    async def test_stats_failed_increments_on_exception(self) -> None:
        from meshflow.agents.pool import AgentPool

        bad_agent = MagicMock()
        bad_agent.name = "bad"

        async def run(task, **kwargs):
            raise RuntimeError("intentional failure")

        bad_agent.run = run

        pool = AgentPool(agents=[bad_agent], concurrency=1)
        async with pool:
            with pytest.raises(RuntimeError):
                await pool.submit("failing task")
        assert pool.stats.total_failed == 1

    @pytest.mark.asyncio
    async def test_pool_round_robins_agents(self) -> None:
        from meshflow.agents.pool import AgentPool

        called: list[str] = []

        def make_agent(name: str) -> MagicMock:
            a = MagicMock()
            a.name = name

            async def run(task, **kwargs):
                called.append(name)
                return MagicMock(total_cost_usd=0.0, total_tokens=0)

            a.run = run
            return a

        agents = [make_agent("a"), make_agent("b"), make_agent("c")]
        pool = AgentPool(agents=agents, concurrency=3)
        async with pool:
            await pool.map(["t1", "t2", "t3"])

        # All three agents should have been called
        assert len(set(called)) == 3

    @pytest.mark.asyncio
    async def test_pool_stats_pool_name(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=1, name="my_pool")
        assert pool.stats.pool_name == "my_pool"

    @pytest.mark.asyncio
    async def test_pool_cost_and_tokens_accumulated(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent("a", cost=0.01, tokens=100)], concurrency=2)
        async with pool:
            await pool.map(["t1", "t2"])
        assert pool.stats.total_cost_usd == pytest.approx(0.02, abs=1e-6)
        assert pool.stats.total_tokens == 200

    @pytest.mark.asyncio
    async def test_context_manager_stops_cleanly(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=2)
        async with pool:
            pass
        assert not pool._started

    @pytest.mark.asyncio
    async def test_pool_stats_uptime(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=1)
        await pool.start()
        await asyncio.sleep(0.05)
        assert pool.stats.uptime_s > 0
        await pool.stop()

    @pytest.mark.asyncio
    async def test_register_and_all_pool_stats(self) -> None:
        from meshflow.agents.pool import AgentPool, register_pool, deregister_pool, all_pool_stats

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=1, name="reg_pool")
        register_pool(pool)
        stats = all_pool_stats()
        assert any(s["pool_name"] == "reg_pool" for s in stats)
        deregister_pool("reg_pool")

    @pytest.mark.asyncio
    async def test_pool_status_dict(self) -> None:
        from meshflow.agents.pool import AgentPool

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=4, name="test")
        d = pool.stats.to_dict()
        assert d["concurrency"] == 4
        assert d["pool_name"] == "test"
        assert "total_submitted" in d


# ─────────────────────────────────────────────────────────────────────────────
# D. Plugin system
# ─────────────────────────────────────────────────────────────────────────────


class TestPluginSystem:
    def test_discover_returns_list(self) -> None:
        from meshflow.plugins import discover_plugins

        result = discover_plugins()
        assert isinstance(result, list)

    def test_discover_with_unknown_group_returns_empty(self) -> None:
        from meshflow.plugins import discover_plugins

        result = discover_plugins(group="nonexistent_group")
        # If no plugins are installed for this group, should return []
        assert isinstance(result, list)

    def test_plugin_groups_constant(self) -> None:
        from meshflow.plugins import PLUGIN_GROUPS

        assert "agent" in PLUGIN_GROUPS
        assert "tool" in PLUGIN_GROUPS
        assert "compliance" in PLUGIN_GROUPS
        assert "ledger" in PLUGIN_GROUPS

    def test_verify_plugin_not_found(self) -> None:
        from meshflow.plugins import verify_plugin

        ok, msg = verify_plugin("nonexistent_plugin_xyz", group="meshflow.agents")
        assert ok is False
        assert "nonexistent_plugin_xyz" in msg or "No plugin" in msg

    def test_load_plugin_key_error_on_missing(self) -> None:
        from meshflow.plugins import load_plugin

        with pytest.raises(KeyError):
            load_plugin("no_such_plugin", group="meshflow.agents")

    def test_plugin_info_to_dict(self) -> None:
        from meshflow.plugins import PluginInfo

        info = PluginInfo(
            name="my_agent",
            group="agent",
            ep_group="meshflow.agents",
            module="my_package.agents:MyAgent",
            dist_name="my-package",
            version="1.0.0",
            description="A great agent.",
        )
        d = info.to_dict()
        assert d["name"] == "my_agent"
        assert d["group"] == "agent"
        assert d["module"] == "my_package.agents:MyAgent"
        assert d["version"] == "1.0.0"
        assert d["loaded"] is False

    def test_list_plugins_table_returns_list(self) -> None:
        from meshflow.plugins import list_plugins_table

        table = list_plugins_table()
        assert isinstance(table, list)
        # Each item (if any) is a dict
        for item in table:
            assert isinstance(item, dict)
            assert "name" in item

    def test_discover_accepts_each_valid_group(self) -> None:
        from meshflow.plugins import discover_plugins, PLUGIN_GROUPS

        for group in PLUGIN_GROUPS:
            result = discover_plugins(group=group)
            assert isinstance(result, list)

    def test_verify_returns_tuple(self) -> None:
        from meshflow.plugins import verify_plugin

        result = verify_plugin("anything")
        assert isinstance(result, tuple)
        assert len(result) == 2
        ok, msg = result
        assert isinstance(ok, bool)
        assert isinstance(msg, str)

    def test_top_level_exports(self) -> None:
        import meshflow

        assert hasattr(meshflow, "discover_plugins")
        assert hasattr(meshflow, "load_plugin")
        assert hasattr(meshflow, "verify_plugin")
        assert hasattr(meshflow, "PluginInfo")


# ─────────────────────────────────────────────────────────────────────────────
# E. Server routes
# ─────────────────────────────────────────────────────────────────────────────


class TestServerPoolRoute:
    @pytest.mark.asyncio
    async def test_pool_status_route_registered(self) -> None:
        from meshflow.runtime.server import _build_app

        app = await _build_app(api_keys=set())
        paths = {r.resource.canonical for r in app.router.routes()}
        assert "/pool/status" in paths

    @pytest.mark.asyncio
    async def test_pool_status_returns_pools_key(self) -> None:
        from aiohttp.test_utils import TestClient, TestServer
        from meshflow.runtime.server import _build_app

        app = await _build_app(api_keys=set())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/pool/status")
            assert resp.status == 200
            data = await resp.json()
            assert "pools" in data
            assert isinstance(data["pools"], list)

    @pytest.mark.asyncio
    async def test_pool_status_shows_registered_pool(self) -> None:
        from aiohttp.test_utils import TestClient, TestServer
        from meshflow.runtime.server import _build_app
        from meshflow.agents.pool import AgentPool, register_pool, deregister_pool

        pool = AgentPool(agents=[_make_mock_agent()], concurrency=2, name="server_test_pool")
        register_pool(pool)
        try:
            app = await _build_app(api_keys=set())
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/pool/status")
                data = await resp.json()
                names = [p["pool_name"] for p in data["pools"]]
                assert "server_test_pool" in names
        finally:
            deregister_pool("server_test_pool")


# ─────────────────────────────────────────────────────────────────────────────
# F. Top-level exports (A/B/C)
# ─────────────────────────────────────────────────────────────────────────────


class TestTopLevelExports:
    def test_eval_baseline_exported(self) -> None:
        from meshflow import EvalBaseline, BaselineDiff

        assert EvalBaseline is not None
        assert BaselineDiff is not None

    def test_agent_pool_exported(self) -> None:
        from meshflow import AgentPool, PoolStats

        assert AgentPool is not None
        assert PoolStats is not None

    def test_plugins_exported(self) -> None:
        from meshflow import PluginInfo, discover_plugins, load_plugin, verify_plugin

        assert PluginInfo is not None
        assert callable(discover_plugins)
        assert callable(load_plugin)
        assert callable(verify_plugin)
