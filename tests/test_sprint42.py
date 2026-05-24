"""Sprint 42 — Agent registry: publish, discover, and govern agents."""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.registry.core import AgentManifest, AgentRegistry


def _manifest(**kwargs) -> AgentManifest:
    defaults = dict(
        name="billing-agent",
        role="executor",
        description="Handles invoice generation and payment queries.",
        tags=["billing", "finance", "hipaa"],
        capabilities=["generate_invoice", "refund"],
        version="1.0.0",
        owner="billing-team",
    )
    defaults.update(kwargs)
    return AgentManifest(**defaults)


@pytest.fixture
def reg():
    return AgentRegistry(":memory:")


# ── AgentManifest ─────────────────────────────────────────────────────────────

class TestAgentManifest:
    def test_to_dict_round_trip(self):
        m = _manifest()
        m2 = AgentManifest.from_dict(m.to_dict())
        assert m2.name == m.name
        assert m2.tags == m.tags
        assert m2.capabilities == m.capabilities

    def test_defaults(self):
        m = AgentManifest(name="x")
        assert m.role == "executor"
        assert m.version == "1.0.0"
        assert m.tags == []

    def test_created_at_auto(self):
        m = AgentManifest(name="y")
        assert m.created_at > 0


# ── AgentRegistry — CRUD ──────────────────────────────────────────────────────

class TestAgentRegistryCRUD:
    def test_publish_and_get(self, reg):
        reg.publish(_manifest())
        m = reg.get("billing-agent")
        assert m is not None
        assert m.name == "billing-agent"

    def test_get_missing(self, reg):
        assert reg.get("nonexistent") is None

    def test_publish_updates_existing(self, reg):
        reg.publish(_manifest(version="1.0.0"))
        reg.publish(_manifest(version="2.0.0"))
        m = reg.get("billing-agent")
        assert m.version == "2.0.0"

    def test_unpublish_returns_true(self, reg):
        reg.publish(_manifest())
        assert reg.unpublish("billing-agent") is True

    def test_unpublish_removes_manifest(self, reg):
        reg.publish(_manifest())
        reg.unpublish("billing-agent")
        assert reg.get("billing-agent") is None

    def test_unpublish_missing_returns_false(self, reg):
        assert reg.unpublish("ghost") is False

    def test_count(self, reg):
        reg.publish(_manifest(name="a"))
        reg.publish(_manifest(name="b"))
        assert reg.count() == 2


# ── AgentRegistry — list / filter ────────────────────────────────────────────

class TestAgentRegistryList:
    def test_list_all(self, reg):
        reg.publish(_manifest(name="a"))
        reg.publish(_manifest(name="b"))
        results = reg.list()
        assert len(results) == 2

    def test_list_by_role(self, reg):
        reg.publish(_manifest(name="exec", role="executor"))
        reg.publish(_manifest(name="plan", role="planner"))
        assert len(reg.list(role="executor")) == 1
        assert len(reg.list(role="planner")) == 1

    def test_list_by_owner(self, reg):
        reg.publish(_manifest(name="a", owner="team-a"))
        reg.publish(_manifest(name="b", owner="team-b"))
        assert len(reg.list(owner="team-a")) == 1

    def test_list_by_tag(self, reg):
        reg.publish(_manifest(name="a", tags=["hipaa", "billing"]))
        reg.publish(_manifest(name="b", tags=["finance"]))
        assert len(reg.list(tag="hipaa")) == 1

    def test_list_limit(self, reg):
        for i in range(10):
            reg.publish(_manifest(name=f"agent-{i}"))
        assert len(reg.list(limit=3)) == 3

    def test_list_newest_first(self, reg):
        reg.publish(_manifest(name="older"))
        time.sleep(0.01)
        reg.publish(_manifest(name="newer"))
        results = reg.list()
        assert results[0].name == "newer"


# ── AgentRegistry — search ────────────────────────────────────────────────────

class TestAgentRegistrySearch:
    def test_search_by_name(self, reg):
        reg.publish(_manifest(name="billing-agent"))
        reg.publish(_manifest(name="support-agent", description="Customer support"))
        results = reg.search("billing")
        assert any(m.name == "billing-agent" for m in results)

    def test_search_by_description(self, reg):
        reg.publish(_manifest(name="a", description="handles invoices and payments"))
        reg.publish(_manifest(name="b", description="manages user profiles"))
        results = reg.search("invoice")
        assert len(results) == 1
        assert results[0].name == "a"

    def test_search_by_tag(self, reg):
        reg.publish(_manifest(name="a", tags=["hipaa", "compliance"]))
        reg.publish(_manifest(name="b", tags=["finance"]))
        results = reg.search("hipaa")
        assert any(m.name == "a" for m in results)

    def test_search_by_capability(self, reg):
        reg.publish(_manifest(name="a", capabilities=["generate_invoice", "refund"]))
        reg.publish(_manifest(name="b", capabilities=["user_lookup"]))
        results = reg.search("refund")
        assert results[0].name == "a"

    def test_search_ranks_by_relevance(self, reg):
        reg.publish(_manifest(name="billing", description="billing invoices billing"))
        reg.publish(_manifest(name="other",   description="billing only once"))
        results = reg.search("billing")
        assert results[0].name == "billing"

    def test_search_empty_query_returns_all(self, reg):
        reg.publish(_manifest(name="a"))
        reg.publish(_manifest(name="b"))
        results = reg.search("")
        assert len(results) == 2

    def test_search_with_role_filter(self, reg):
        reg.publish(_manifest(name="a", role="executor", description="billing"))
        reg.publish(_manifest(name="b", role="planner",  description="billing"))
        results = reg.search("billing", role="executor")
        assert len(results) == 1 and results[0].role == "executor"

    def test_search_no_results(self, reg):
        reg.publish(_manifest(name="a", description="completely unrelated"))
        assert reg.search("zzznomatch") == []


# ── AgentRegistry — RBAC ─────────────────────────────────────────────────────

class TestAgentRegistryRBAC:
    def test_open_access_when_no_rules(self, reg):
        reg.publish(_manifest())
        assert reg.can_call("billing-agent", caller="anyone") is True

    def test_allow_grants_access(self, reg):
        reg.publish(_manifest())
        reg.allow("billing-agent", caller="payments-team")
        assert reg.can_call("billing-agent", caller="payments-team") is True

    def test_allow_blocks_others(self, reg):
        reg.publish(_manifest())
        reg.allow("billing-agent", caller="payments-team")
        assert reg.can_call("billing-agent", caller="random-team") is False

    def test_revoke_removes_access(self, reg):
        reg.publish(_manifest())
        reg.allow("billing-agent", caller="team-a")
        reg.revoke("billing-agent", caller="team-a")
        # After revoke, no rules remain → open access
        assert reg.can_call("billing-agent", caller="team-a") is True

    def test_allowed_callers_list(self, reg):
        reg.publish(_manifest())
        reg.allow("billing-agent", caller="team-a")
        reg.allow("billing-agent", caller="team-b")
        callers = reg.allowed_callers("billing-agent")
        assert "team-a" in callers
        assert "team-b" in callers

    def test_unpublish_removes_rbac(self, reg):
        reg.publish(_manifest())
        reg.allow("billing-agent", caller="team-a")
        reg.unpublish("billing-agent")
        # Rules should be gone too
        assert reg.allowed_callers("billing-agent") == []


# ── AgentRegistry — stats ─────────────────────────────────────────────────────

class TestAgentRegistryStats:
    def test_stats_empty(self, reg):
        s = reg.stats()
        assert s["total_agents"] == 0

    def test_stats_populated(self, reg):
        reg.publish(_manifest(name="a", role="executor"))
        reg.publish(_manifest(name="b", role="executor"))
        reg.publish(_manifest(name="c", role="planner"))
        s = reg.stats()
        assert s["total_agents"] == 3
        assert s["by_role"]["executor"] == 2
        assert s["by_role"]["planner"] == 1


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.registry.core import AgentManifest, AgentRegistry, get_registry
        assert all(x is not None for x in [AgentManifest, AgentRegistry, get_registry])

    def test_registry_package_import(self):
        from meshflow.registry import AgentManifest, AgentRegistry
        assert AgentManifest is not None and AgentRegistry is not None
