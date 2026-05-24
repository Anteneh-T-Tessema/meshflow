"""Sprint 46 — Production deployment kit: agent-serve, budget CLI, registry CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import io

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.budget.store import BudgetAccount, BudgetStore
from meshflow.registry.core import AgentManifest, AgentRegistry


# ── Helpers: build argparse Namespace objects directly ───────────────────────

def _budget_ns(**kwargs):
    defaults = dict(db=":memory:", budget_cmd="list", agent_name="", account_id="")
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _registry_ns(**kwargs):
    defaults = dict(db=":memory:", registry_cmd="list", role="", owner="", tag="",
                    name="", query="", description="", tags="", capabilities="",
                    version="1.0.0", url="")
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# Pull the handler functions directly (avoid spawning a subprocess)
from meshflow.cli.main import _cmd_budget, _cmd_registry


# ── budget list ───────────────────────────────────────────────────────────────

class TestBudgetCLIList:
    def test_empty_list(self, capsys):
        store = BudgetStore(":memory:")
        args = _budget_ns(budget_cmd="list")
        # Patch the store — monkey-patch BudgetStore to return our in-memory one
        import meshflow.cli.main as _cli
        orig = _cli.__dict__.get("BudgetStore")
        try:
            import meshflow.budget.store as _bstore
            old_cls = _bstore.BudgetStore
            _bstore.BudgetStore = lambda db: store
            _cmd_budget(args)
        finally:
            _bstore.BudgetStore = old_cls
        out = capsys.readouterr().out
        assert "No budget accounts found" in out

    def test_list_shows_accounts(self, capsys):
        store = BudgetStore(":memory:")
        store.create(BudgetAccount(account_id="billing-daily", agent_name="billing-agent",
                                   period="daily", limit_usd=5.0))
        args = _budget_ns(budget_cmd="list")
        import meshflow.budget.store as _bstore
        old_cls = _bstore.BudgetStore
        try:
            _bstore.BudgetStore = lambda db: store
            _cmd_budget(args)
        finally:
            _bstore.BudgetStore = old_cls
        out = capsys.readouterr().out
        assert "billing-daily" in out
        assert "billing-agent" in out


# ── budget set + status ───────────────────────────────────────────────────────

class TestBudgetCLISet:
    def _patched_store(self):
        return BudgetStore(":memory:")

    def test_set_creates_account(self, capsys):
        store = self._patched_store()
        args = _budget_ns(
            budget_cmd="set", account_id="my-cap",
            agent_name="bot", period="daily",
            limit_usd=10.0, limit_tokens=0, name="",
        )
        import meshflow.budget.store as _bstore
        old_cls = _bstore.BudgetStore
        try:
            _bstore.BudgetStore = lambda db: store
            _cmd_budget(args)
        finally:
            _bstore.BudgetStore = old_cls
        assert store.get("my-cap") is not None
        assert store.get("my-cap").limit_usd == 10.0
        out = capsys.readouterr().out
        assert "saved" in out

    def test_status_shows_spend(self, capsys):
        store = self._patched_store()
        store.create(BudgetAccount(account_id="billing-daily", agent_name="bot",
                                   period="daily", limit_usd=5.0))
        store.record_spend("billing-daily", cost_usd=2.5)
        args = _budget_ns(budget_cmd="status", account_id="billing-daily")
        import meshflow.budget.store as _bstore
        old_cls = _bstore.BudgetStore
        try:
            _bstore.BudgetStore = lambda db: store
            _cmd_budget(args)
        finally:
            _bstore.BudgetStore = old_cls
        out = capsys.readouterr().out
        assert "2.5" in out
        assert "ALLOWED" in out

    def test_status_blocked_shows_blocked(self, capsys):
        store = self._patched_store()
        store.create(BudgetAccount(account_id="cap", agent_name="bot",
                                   period="daily", limit_usd=1.0))
        store.record_spend("cap", cost_usd=1.0)
        args = _budget_ns(budget_cmd="status", account_id="cap")
        import meshflow.budget.store as _bstore
        old_cls = _bstore.BudgetStore
        try:
            _bstore.BudgetStore = lambda db: store
            _cmd_budget(args)
        finally:
            _bstore.BudgetStore = old_cls
        out = capsys.readouterr().out
        assert "BLOCKED" in out

    def test_reset_zeroes_spend(self, capsys):
        store = self._patched_store()
        store.create(BudgetAccount(account_id="x", agent_name="bot",
                                   period="daily", limit_usd=5.0))
        store.record_spend("x", cost_usd=3.0)
        args = _budget_ns(budget_cmd="reset", account_id="x")
        import meshflow.budget.store as _bstore
        old_cls = _bstore.BudgetStore
        try:
            _bstore.BudgetStore = lambda db: store
            _cmd_budget(args)
        finally:
            _bstore.BudgetStore = old_cls
        assert store.current_spend("x").cost_usd == 0.0
        assert "reset" in capsys.readouterr().out

    def test_delete_removes_account(self, capsys):
        store = self._patched_store()
        store.create(BudgetAccount(account_id="gone", agent_name="bot",
                                   period="daily", limit_usd=1.0))
        args = _budget_ns(budget_cmd="delete", account_id="gone")
        import meshflow.budget.store as _bstore
        old_cls = _bstore.BudgetStore
        try:
            _bstore.BudgetStore = lambda db: store
            _cmd_budget(args)
        finally:
            _bstore.BudgetStore = old_cls
        assert store.get("gone") is None


# ── registry list ──────────────────────────────────────────────────────────────

class TestRegistryCLIList:
    def _make_reg(self):
        return AgentRegistry(":memory:")

    def test_empty_list(self, capsys):
        reg = self._make_reg()
        args = _registry_ns(registry_cmd="list")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls
        assert "No agents registered" in capsys.readouterr().out

    def test_list_shows_agents(self, capsys):
        reg = self._make_reg()
        reg.publish(AgentManifest(name="billing-agent", role="executor",
                                  description="Billing", owner="team-a"))
        args = _registry_ns(registry_cmd="list")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls
        out = capsys.readouterr().out
        assert "billing-agent" in out
        assert "executor" in out


# ── registry search ────────────────────────────────────────────────────────────

class TestRegistryCLISearch:
    def test_search_hit(self, capsys):
        reg = AgentRegistry(":memory:")
        reg.publish(AgentManifest(name="billing-agent", description="handles invoices"))
        args = _registry_ns(registry_cmd="search", query="invoice")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls
        out = capsys.readouterr().out
        assert "billing-agent" in out

    def test_search_miss(self, capsys):
        reg = AgentRegistry(":memory:")
        reg.publish(AgentManifest(name="support-agent", description="customer support"))
        args = _registry_ns(registry_cmd="search", query="zzznomatch")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls
        assert "No agents matched" in capsys.readouterr().out


# ── registry get ───────────────────────────────────────────────────────────────

class TestRegistryCLIGet:
    def test_get_prints_json(self, capsys):
        reg = AgentRegistry(":memory:")
        reg.publish(AgentManifest(name="billing-agent", version="2.0.0"))
        args = _registry_ns(registry_cmd="get", name="billing-agent")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["name"] == "billing-agent"
        assert data["version"] == "2.0.0"

    def test_get_missing_exits(self):
        reg = AgentRegistry(":memory:")
        args = _registry_ns(registry_cmd="get", name="ghost")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            with pytest.raises(SystemExit):
                _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls


# ── registry publish + unpublish ───────────────────────────────────────────────

class TestRegistryCLIPublish:
    def test_publish_creates_manifest(self, capsys):
        reg = AgentRegistry(":memory:")
        args = _registry_ns(
            registry_cmd="publish", name="new-agent",
            role="planner", description="A planner agent",
            tags="billing,hipaa", capabilities="plan,research",
            version="1.2.0", owner="team-x", url="",
        )
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls
        m = reg.get("new-agent")
        assert m is not None
        assert m.version == "1.2.0"
        assert "billing" in m.tags
        assert "plan" in m.capabilities
        assert "Published" in capsys.readouterr().out

    def test_unpublish_removes(self, capsys):
        reg = AgentRegistry(":memory:")
        reg.publish(AgentManifest(name="old-agent"))
        args = _registry_ns(registry_cmd="unpublish", name="old-agent")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls
        assert reg.get("old-agent") is None
        assert "Unpublished" in capsys.readouterr().out

    def test_unpublish_missing_exits(self):
        reg = AgentRegistry(":memory:")
        args = _registry_ns(registry_cmd="unpublish", name="ghost")
        import meshflow.registry.core as _rc
        old_cls = _rc.AgentRegistry
        try:
            _rc.AgentRegistry = lambda db: reg
            with pytest.raises(SystemExit):
                _cmd_registry(args)
        finally:
            _rc.AgentRegistry = old_cls


# ── agent-serve (argument parsing smoke test) ─────────────────────────────────

class TestAgentServeCLIParsing:
    def test_help_text(self):
        import subprocess
        result = subprocess.run(
            ["meshflow", "agent-serve", "--help"],
            capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        assert "--agent" in combined
        assert "--port" in combined
        assert "--host" in combined

    def test_missing_agent_arg_fails(self):
        import subprocess
        result = subprocess.run(
            ["meshflow", "agent-serve", "--port", "9999"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0


# ── Dockerfile ────────────────────────────────────────────────────────────────

class TestDockerfile:
    def test_uses_python_312(self):
        with open("Dockerfile") as f:
            content = f.read()
        assert "python:3.12" in content

    def test_has_registry_env(self):
        with open("Dockerfile") as f:
            content = f.read()
        assert "MESHFLOW_REGISTRY_PATH" in content

    def test_has_budget_env(self):
        with open("Dockerfile") as f:
            content = f.read()
        assert "MESHFLOW_BUDGET_PATH" in content

    def test_has_otel_env(self):
        with open("Dockerfile") as f:
            content = f.read()
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in content

    def test_healthcheck_present(self):
        with open("Dockerfile") as f:
            content = f.read()
        assert "HEALTHCHECK" in content

    def test_non_root_user(self):
        with open("Dockerfile") as f:
            content = f.read()
        assert "USER meshflow" in content


# ── .env.example ─────────────────────────────────────────────────────────────

class TestEnvExample:
    def test_env_example_exists(self):
        assert os.path.exists(".env.example")

    def test_contains_anthropic_key(self):
        with open(".env.example") as f:
            content = f.read()
        assert "ANTHROPIC_API_KEY" in content

    def test_contains_registry_path(self):
        with open(".env.example") as f:
            content = f.read()
        assert "MESHFLOW_REGISTRY_PATH" in content

    def test_contains_budget_path(self):
        with open(".env.example") as f:
            content = f.read()
        assert "MESHFLOW_BUDGET_PATH" in content

    def test_contains_otel_endpoint(self):
        with open(".env.example") as f:
            content = f.read()
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in content

    def test_contains_mock_mode(self):
        with open(".env.example") as f:
            content = f.read()
        assert "MESHFLOW_MOCK" in content


# ── CLI subcommand registration ───────────────────────────────────────────────

class TestCLIRegistration:
    def test_budget_subcommand_registered(self):
        import subprocess
        r = subprocess.run(["meshflow", "budget", "--help"],
                           capture_output=True, text=True)
        assert "list" in r.stdout + r.stderr
        assert "status" in r.stdout + r.stderr
        assert "set" in r.stdout + r.stderr

    def test_registry_subcommand_registered(self):
        import subprocess
        r = subprocess.run(["meshflow", "registry", "--help"],
                           capture_output=True, text=True)
        assert "publish" in r.stdout + r.stderr
        assert "search" in r.stdout + r.stderr

    def test_agent_serve_registered(self):
        import subprocess
        r = subprocess.run(["meshflow", "agent-serve", "--help"],
                           capture_output=True, text=True)
        assert "--agent" in r.stdout + r.stderr
