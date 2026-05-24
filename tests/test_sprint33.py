"""Sprint 33 — Persistent memory: SQLite/Postgres-backed cross-session AgentMemory."""

from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.intelligence.memory_backends import (
    InMemoryBackend,
    SQLiteMemoryBackend,
    MemoryBackend,
    snapshot_from_memory,
    restore_memory,
)
from meshflow.intelligence.memory import AgentMemory, MemoryItem


# ── MemoryItem serialisation ──────────────────────────────────────────────────

class TestMemoryItemSerialisation:
    def test_item_to_dict_round_trip(self):
        from meshflow.intelligence.memory_backends import _item_to_dict, _dict_to_item
        item = MemoryItem(content="fact A", tier="working", metadata={"step": 1})
        d = _item_to_dict(item)
        item2 = _dict_to_item(d)
        assert item2.content == "fact A"
        assert item2.tier == "working"
        assert item2.metadata == {"step": 1}

    def test_access_count_preserved(self):
        from meshflow.intelligence.memory_backends import _item_to_dict, _dict_to_item
        item = MemoryItem(content="x", tier="episodic", access_count=5)
        item2 = _dict_to_item(_item_to_dict(item))
        assert item2.access_count == 5


# ── snapshot helpers ──────────────────────────────────────────────────────────

class TestSnapshotHelpers:
    def _make_memory(self) -> AgentMemory:
        mem = AgentMemory(agent_id="test", max_working=5, max_episodic=10)
        mem.add("fact one")
        mem.add("fact two")
        return mem

    def test_snapshot_contains_agent_id(self):
        mem = self._make_memory()
        snap = snapshot_from_memory(mem)
        assert snap["agent_id"] == "test"

    def test_snapshot_contains_working(self):
        mem = self._make_memory()
        snap = snapshot_from_memory(mem)
        assert len(snap["working"]) == 2

    def test_restore_repopulates_working(self):
        mem = self._make_memory()
        snap = snapshot_from_memory(mem)

        mem2 = AgentMemory(agent_id="test", max_working=5, max_episodic=10)
        restore_memory(mem2, snap)
        assert len(list(mem2._working)) == 2

    def test_restore_rebuilds_bm25_index(self):
        mem = self._make_memory()
        snap = snapshot_from_memory(mem)

        mem2 = AgentMemory(agent_id="test", max_working=5)
        restore_memory(mem2, snap)
        results = mem2.recall("fact")
        assert len(results) > 0

    def test_restore_preserves_step_count(self):
        mem = self._make_memory()
        snap = snapshot_from_memory(mem)

        mem2 = AgentMemory(agent_id="test")
        restore_memory(mem2, snap)
        assert mem2._step_count == mem._step_count

    def test_empty_snapshot_restore(self):
        mem = AgentMemory(agent_id="empty")
        snap = snapshot_from_memory(mem)

        mem2 = AgentMemory(agent_id="empty")
        restore_memory(mem2, snap)
        assert len(list(mem2._working)) == 0


# ── InMemoryBackend ───────────────────────────────────────────────────────────

class TestInMemoryBackend:
    def test_save_and_load(self):
        backend = InMemoryBackend()
        snap = {"agent_id": "a", "working": [], "episodic": [], "procedural": [], "step_count": 0}
        backend.save("session1", snap)
        loaded = backend.load("session1")
        assert loaded["agent_id"] == "a"

    def test_load_missing_returns_none(self):
        backend = InMemoryBackend()
        assert backend.load("nonexistent") is None

    def test_delete(self):
        backend = InMemoryBackend()
        backend.save("s", {"agent_id": "x", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        backend.delete("s")
        assert backend.load("s") is None

    def test_list_sessions(self):
        backend = InMemoryBackend()
        backend.save("s1", {"agent_id": "x", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        backend.save("s2", {"agent_id": "y", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        assert set(backend.list_sessions()) == {"s1", "s2"}

    def test_save_is_deep_copy(self):
        backend = InMemoryBackend()
        snap = {"agent_id": "a", "working": [{"content": "x"}], "episodic": [], "procedural": [], "step_count": 1}
        backend.save("s", snap)
        snap["working"].append({"content": "y"})
        loaded = backend.load("s")
        assert len(loaded["working"]) == 1  # original, not mutated

    def test_overwrite(self):
        backend = InMemoryBackend()
        backend.save("s", {"agent_id": "v1", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        backend.save("s", {"agent_id": "v2", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        assert backend.load("s")["agent_id"] == "v2"


# ── SQLiteMemoryBackend ───────────────────────────────────────────────────────

class TestSQLiteMemoryBackend:
    def _backend(self) -> SQLiteMemoryBackend:
        return SQLiteMemoryBackend(":memory:")

    def test_save_and_load(self):
        backend = self._backend()
        snap = {"agent_id": "a", "working": [], "episodic": [], "procedural": [], "step_count": 0}
        backend.save("s1", snap)
        assert backend.load("s1")["agent_id"] == "a"

    def test_load_missing_returns_none(self):
        assert self._backend().load("missing") is None

    def test_delete(self):
        backend = self._backend()
        backend.save("s", {"agent_id": "x", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        backend.delete("s")
        assert backend.load("s") is None

    def test_overwrite(self):
        backend = self._backend()
        snap1 = {"agent_id": "v1", "working": [], "episodic": [], "procedural": [], "step_count": 0}
        snap2 = {"agent_id": "v2", "working": [], "episodic": [], "procedural": [], "step_count": 0}
        backend.save("s", snap1)
        backend.save("s", snap2)
        assert backend.load("s")["agent_id"] == "v2"

    def test_list_sessions(self):
        backend = self._backend()
        backend.save("s1", {"agent_id": "x", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        backend.save("s2", {"agent_id": "y", "working": [], "episodic": [], "procedural": [], "step_count": 0})
        sessions = backend.list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions

    def test_persists_to_file(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            backend1 = SQLiteMemoryBackend(path)
            snap = {"agent_id": "test", "working": [{"content": "remembered", "tier": "working",
                    "timestamp": 0.0, "metadata": {}, "access_count": 0}],
                    "episodic": [], "procedural": [], "step_count": 1}
            backend1.save("session", snap)

            backend2 = SQLiteMemoryBackend(path)
            loaded = backend2.load("session")
            assert loaded["working"][0]["content"] == "remembered"
        finally:
            os.unlink(path)


# ── Agent persistent memory integration ──────────────────────────────────────

class TestAgentPersistentMemory:
    @pytest.mark.asyncio
    async def test_memory_restored_across_builds(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        backend = InMemoryBackend()
        agent = Agent(
            name="persist-agent",
            role="executor",
            memory=True,
            memory_backend=backend,
            memory_session_id="session-A",
        )

        # First run — adds a memory entry
        await agent.run("Remember that HIPAA covers PHI disclosures.")

        # Second build reads the backend
        built2 = agent._build()
        # The backend should have something saved
        snap = backend.load("session-A")
        assert snap is not None
        assert snap["step_count"] >= 1

    @pytest.mark.asyncio
    async def test_sqlite_string_shorthand(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(
            name="sqlite-agent",
            role="executor",
            memory=True,
            memory_backend="sqlite://:memory:",
        )
        built = agent._build()
        assert built._memory_backend is not None

    @pytest.mark.asyncio
    async def test_no_backend_no_persist(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="no-backend", role="executor", memory=True)
        built = agent._build()
        assert built._memory_backend is None

    def test_resolve_memory_backend_sqlite_shorthand(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="a", memory_backend="sqlite://test.db")
        backend = agent._resolve_memory_backend()
        assert isinstance(backend, SQLiteMemoryBackend)
        assert "test.db" in backend.path

    def test_resolve_memory_backend_instance(self):
        from meshflow.agents.builder import Agent

        b = InMemoryBackend()
        agent = Agent(name="a", memory_backend=b)
        assert agent._resolve_memory_backend() is b

    def test_resolve_memory_backend_none(self):
        from meshflow.agents.builder import Agent
        agent = Agent(name="a")
        assert agent._resolve_memory_backend() is None


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_backends_importable(self):
        from meshflow.intelligence.memory_backends import (
            MemoryBackend, InMemoryBackend, SQLiteMemoryBackend,
            PostgresMemoryBackend, snapshot_from_memory, restore_memory,
        )
        assert all(x is not None for x in [
            MemoryBackend, InMemoryBackend, SQLiteMemoryBackend,
            PostgresMemoryBackend, snapshot_from_memory, restore_memory,
        ])

    def test_agent_has_memory_backend_field(self):
        from meshflow.agents.builder import Agent
        import dataclasses
        fields = {f.name for f in dataclasses.fields(Agent)}
        assert "memory_backend" in fields
        assert "memory_session_id" in fields
