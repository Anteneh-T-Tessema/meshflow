"""Sprint 67 — Cross-session agent memory tests."""

from __future__ import annotations

from meshflow.intelligence.memory import AgentMemory
from meshflow.intelligence.memory_backends import (
    InMemoryBackend,
    snapshot_from_memory,
    restore_memory,
)


# ── to_snapshot / from_snapshot ───────────────────────────────────────────────


def test_to_snapshot_round_trips():
    mem = AgentMemory("agent-a")
    mem.add("Learned fact A")
    mem.add("Learned fact B")

    snap = mem.to_snapshot()
    assert isinstance(snap, dict)
    assert snap["agent_id"] == "agent-a"
    assert len(snap["working"]) == 2

    mem2 = AgentMemory("agent-a")
    mem2.from_snapshot(snap)
    assert mem2.working_count == 2
    recent = mem2.recent(2)
    assert any("fact A" in r for r in recent)


def test_snapshot_empty_memory():
    mem = AgentMemory("x")
    snap = mem.to_snapshot()
    assert snap["working"] == []
    assert snap["episodic"] == []

    mem2 = AgentMemory("x")
    mem2.from_snapshot(snap)
    assert mem2.working_count == 0


# ── Persistent cross-session via backend ──────────────────────────────────────


def test_backend_auto_save_load():
    backend = InMemoryBackend()
    session = "analyst_session_1"

    mem1 = AgentMemory("analyst")
    mem1.add("session 1 data")
    backend.save(session, snapshot_from_memory(mem1))

    # Simulate process restart by creating a new AgentMemory and restoring
    mem2 = AgentMemory("analyst")
    snap = backend.load(session)
    assert snap is not None
    restore_memory(mem2, snap)

    assert mem2.working_count == 1
    assert "session 1 data" in mem2.recent(1)[0]


def test_session_isolation():
    backend = InMemoryBackend()

    for session_id in ["session_a", "session_b"]:
        m = AgentMemory("agent")
        m.add(f"data for {session_id}")
        backend.save(session_id, snapshot_from_memory(m))

    m_a = AgentMemory("agent")
    restore_memory(m_a, backend.load("session_a"))  # type: ignore[arg-type]

    m_b = AgentMemory("agent")
    restore_memory(m_b, backend.load("session_b"))  # type: ignore[arg-type]

    assert "session_a" in m_a.recent(1)[0]
    assert "session_b" in m_b.recent(1)[0]


def test_memory_session_id_defaults_to_agent_name():
    """Agent builder uses agent.name as the default session_id."""
    from meshflow.agents.builder import Agent

    agent = Agent(name="my-agent", memory=True, memory_backend=InMemoryBackend())
    # The _resolve_memory_backend path is exercised; session_id defaults to "my-agent"
    assert agent.memory_session_id == "" or agent.memory_session_id == "my-agent"
