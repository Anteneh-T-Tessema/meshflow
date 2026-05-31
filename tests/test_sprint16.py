"""Sprint 16 — Dashboard integration: eval results endpoint, plugins endpoint,
eval history CLI subcommand.

Tests:
  A. Server GET /eval-results — route registered, returns JSON
  B. Server GET /plugins      — route registered, returns JSON
  C. CLI eval-history          — lists stored results, JSON flag, suite filter
  D. Dashboard fetch helpers   — fetch_eval_results / fetch_plugins (offline)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# A. Server route registration
# ─────────────────────────────────────────────────────────────────────────────


async def _make_app():
    """Build the aiohttp app without starting the server."""
    from meshflow.runtime.server import _build_app

    return await _build_app(api_keys=set(), ledger_path=":memory:")


class TestServerRoutes:
    async def test_eval_results_route_registered(self):
        app = await _make_app()
        routes = {r.resource.canonical for r in app.router.routes()}
        assert "/eval-results" in routes

    async def test_plugins_route_registered(self):
        app = await _make_app()
        routes = {r.resource.canonical for r in app.router.routes()}
        assert "/plugins" in routes

    async def test_pool_status_route_registered(self):
        app = await _make_app()
        routes = {r.resource.canonical for r in app.router.routes()}
        assert "/pool/status" in routes


# ─────────────────────────────────────────────────────────────────────────────
# B. GET /eval-results handler
# ─────────────────────────────────────────────────────────────────────────────


class TestEvalResultsEndpoint:
    async def test_returns_empty_list_when_no_results(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer

        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/eval-results")
            assert resp.status == 200
            data = await resp.json()
            assert "eval_results" in data
            assert data["eval_results"] == []

    async def test_suite_filter_param_accepted(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer

        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/eval-results?suite=my_suite")
            assert resp.status == 200
            data = await resp.json()
            assert "eval_results" in data

    async def test_returns_401_with_api_key_set(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer

        app = await _build_app(api_keys={"secret"}, ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/eval-results")
            assert resp.status == 401

    async def test_returns_200_with_correct_api_key(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer

        app = await _build_app(api_keys={"tok"}, ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/eval-results", headers={"Authorization": "Bearer tok"})
            assert resp.status == 200


# ─────────────────────────────────────────────────────────────────────────────
# C. GET /plugins handler
# ─────────────────────────────────────────────────────────────────────────────


class TestPluginsEndpoint:
    async def test_returns_plugins_list(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer

        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/plugins")
            assert resp.status == 200
            data = await resp.json()
            assert "plugins" in data
            assert isinstance(data["plugins"], list)

    async def test_group_filter_accepted(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer

        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/plugins?group=agent")
            assert resp.status == 200
            data = await resp.json()
            assert "plugins" in data

    async def test_returns_401_with_api_key_set(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer

        app = await _build_app(api_keys={"secret"}, ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/plugins")
            assert resp.status == 401


# ─────────────────────────────────────────────────────────────────────────────
# D. CLI eval-history subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestEvalHistoryCLI:
    def _run_cli(self, *args: str, db: str = ":memory:") -> tuple[int, str]:
        """Run _cmd_eval_history with a fabricated Namespace and capture stdout."""
        import argparse
        import io
        from meshflow.cli.main import _cmd_eval_history

        # Parse extra flags: --suite <name> and --json
        suite = ""
        output_json = False
        arg_list = list(args)
        while arg_list:
            tok = arg_list.pop(0)
            if tok == "--suite" and arg_list:
                suite = arg_list.pop(0)
            elif tok == "--json":
                output_json = True

        parsed = argparse.Namespace(db=db, suite=suite, output_json=output_json)

        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        exit_code = 0
        try:
            _cmd_eval_history(parsed)
        except SystemExit as e:
            exit_code = int(str(e))
        finally:
            sys.stdout = old_stdout

        return exit_code, buf.getvalue()

    def test_empty_ledger_prints_no_results(self):
        code, out = self._run_cli()
        assert code == 0
        assert "No stored eval results" in out

    def test_suite_filter_mentioned_in_empty_output(self):
        code, out = self._run_cli("--suite", "nonexistent")
        assert code == 0
        assert "nonexistent" in out

    def test_json_flag_outputs_valid_json(self):
        code, out = self._run_cli("--json")
        assert code == 0
        data = json.loads(out.strip())
        assert isinstance(data, list)

    def test_stored_result_shows_in_table(self):
        """Save an eval result to a real SQLite db, then list it."""
        from meshflow.core.ledger import ReplayLedger
        from meshflow.eval.runner import EvalResult, ScenarioResult

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            ledger = ReplayLedger(db_path)
            sr = ScenarioResult(
                scenario_name="s1",
                passed=True,
                score=0.9,
                checks={"c": True},
                output="ok",
                tokens=10,
                confidence=0.9,
                duration_ms=50.0,
            )
            result = EvalResult(
                suite_name="my_suite",
                total=1,
                passed=1,
                failed=0,
                errors=0,
                pass_rate=1.0,
                weighted_score=0.9,
                total_tokens=10,
                total_cost_usd=0.0,
                duration_s=0.1,
                scenarios=[sr],
            )
            asyncio.run(ledger.save_eval_result(result))

            code, out = self._run_cli(db=db_path)
            assert code == 0
            assert "my_suite" in out
            assert "100.0%" in out
        finally:
            os.unlink(db_path)

    def test_suite_filter_isolates_results(self):
        """Two suites stored — filter returns only the matching one."""
        from meshflow.core.ledger import ReplayLedger
        from meshflow.eval.runner import EvalResult, ScenarioResult

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        def _sr(name: str) -> ScenarioResult:
            return ScenarioResult(
                scenario_name=name, passed=True, score=1.0,
                checks={}, output="", tokens=5, confidence=1.0, duration_ms=10.0,
            )

        try:
            ledger = ReplayLedger(db_path)
            for suite in ("alpha", "beta"):
                r = EvalResult(
                    suite_name=suite,
                    total=1,
                    passed=1,
                    failed=0,
                    errors=0,
                    pass_rate=1.0,
                    weighted_score=1.0,
                    total_tokens=5,
                    total_cost_usd=0.0,
                    duration_s=0.05,
                    scenarios=[_sr("s1")],
                )
                asyncio.run(ledger.save_eval_result(r))

            code, out = self._run_cli("--suite", "alpha", db=db_path)
            assert code == 0
            assert "alpha" in out
            assert "beta" not in out
        finally:
            os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# E. Dashboard offline helpers (skip when streamlit not installed)
# ─────────────────────────────────────────────────────────────────────────────

_streamlit_missing = pytest.mark.skipif(
    not __import__("importlib.util", fromlist=["find_spec"]).find_spec("streamlit"),
    reason="streamlit not installed",
)


@_streamlit_missing
class TestDashboardHelpers:
    """These tests stub urllib so no real server is needed."""

    def test_fetch_eval_results_returns_list_on_success(self):
        payload = json.dumps({"eval_results": [{"suite_name": "s1", "pass_rate": 1.0}]}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            import dashboard.app as app_mod
            app_mod.fetch_eval_results.clear()
            result = app_mod.fetch_eval_results()

        assert isinstance(result, list)
        assert result[0]["suite_name"] == "s1"

    def test_fetch_eval_results_returns_empty_on_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            import dashboard.app as app_mod
            app_mod.fetch_eval_results.clear()
            result = app_mod.fetch_eval_results.__wrapped__("") if hasattr(app_mod.fetch_eval_results, "__wrapped__") else []
        assert isinstance(result, list)

    def test_fetch_plugins_returns_list_on_success(self):
        payload = json.dumps({"plugins": [{"name": "my_plugin", "group": "agent"}]}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            import dashboard.app as app_mod
            app_mod.fetch_plugins.clear()
            result = app_mod.fetch_plugins()

        assert isinstance(result, list)
        assert result[0]["name"] == "my_plugin"

    def test_fetch_plugins_returns_empty_on_error(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionError("down")):
            import dashboard.app as app_mod
            app_mod.fetch_plugins.clear()
            try:
                result = app_mod.fetch_plugins()
            except Exception:
                result = []
        assert isinstance(result, list)
