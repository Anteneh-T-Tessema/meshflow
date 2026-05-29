"""Sprint 68 — EntityMemory tests."""

from __future__ import annotations

import pytest
from meshflow.intelligence.entity_memory import EntityMemory


@pytest.fixture
def em():
    return EntityMemory(":memory:")


# ── remember / recall_entity ──────────────────────────────────────────────────


def test_remember_and_recall(em):
    em.remember("Alice", "role", "CTO")
    em.remember("Alice", "company", "Acme Corp")
    facts = em.recall_entity("Alice")
    assert facts["role"] == "CTO"
    assert facts["company"] == "Acme Corp"


def test_recall_nonexistent_entity(em):
    assert em.recall_entity("Nobody") == {}


def test_remember_updates_fact(em):
    em.remember("Bob", "status", "active")
    em.remember("Bob", "status", "inactive")
    assert em.recall_entity("Bob")["status"] == "inactive"


# ── forget ────────────────────────────────────────────────────────────────────


def test_forget_removes_entity(em):
    em.remember("Carol", "role", "engineer")
    em.forget("Carol")
    assert em.recall_entity("Carol") == {}


def test_forget_fact(em):
    em.remember("Dave", "role", "engineer")
    em.remember("Dave", "company", "Corp")
    em.forget_fact("Dave", "role")
    facts = em.recall_entity("Dave")
    assert "role" not in facts
    assert facts["company"] == "Corp"


# ── search_entities ───────────────────────────────────────────────────────────


def test_search_entities_by_partial_name(em):
    em.remember("Alice Smith", "role", "PM")
    em.remember("Bob Jones", "role", "Dev")
    results = em.search_entities("alice")
    assert any("Alice" in r for r in results)


def test_search_entities_no_match(em):
    em.remember("Alice", "role", "PM")
    results = em.search_entities("zzz_no_match")
    assert results == []


def test_list_entities(em):
    em.remember("E1", "k", "v")
    em.remember("E2", "k", "v")
    assert set(em.list_entities()) == {"E1", "E2"}


# ── context helpers ───────────────────────────────────────────────────────────


def test_entities_in_text(em):
    em.remember("OpenAI", "type", "company")
    em.remember("Anthropic", "type", "company")
    found = em.entities_in_text("I use both OpenAI and Anthropic APIs.")
    assert "OpenAI" in found
    assert "Anthropic" in found


def test_to_context_string(em):
    em.remember("Alice", "role", "CTO")
    ctx = em.to_context_string(["Alice"])
    assert "Alice" in ctx
    assert "CTO" in ctx


def test_to_context_string_max_chars(em):
    for i in range(20):
        em.remember(f"Entity{i}", "fact", "x" * 50)
    ctx = em.to_context_string(max_chars=100)
    assert len(ctx) <= 150  # some tolerance for the block header


# ── AgentMemory integration ───────────────────────────────────────────────────


def test_agent_memory_entity_tier():
    from meshflow.intelligence.memory import AgentMemory
    mem = AgentMemory("agent")
    mem.remember_entity("Alice", "role", "CTO")
    assert mem.recall_entity("Alice") == {"role": "CTO"}


def test_agent_memory_context_string_includes_entities():
    from meshflow.intelligence.memory import AgentMemory
    mem = AgentMemory("agent")
    mem.add("some working memory")
    mem.remember_entity("Alice", "role", "CTO")
    ctx = mem.context_string(query="What does Alice do?")
    assert "Alice" in ctx


def test_agent_memory_stats_not_broken():
    from meshflow.intelligence.memory import AgentMemory
    mem = AgentMemory("agent")
    mem.add("fact")
    s = mem.stats()
    assert s["working"] == 1
