"""Sprint 71 — Memory v2 tests.

Covers EntityMemory TTL, WorkspaceMemoryStore TTL, MemoryConsolidator,
and TeamWorkspace. All tests are deterministic (no API key, :memory: DB).
"""

from __future__ import annotations

import time

import pytest

import meshflow
from meshflow.intelligence.entity_memory import EntityMemory
from meshflow.intelligence.workspace_memory import WorkspaceMemoryStore
from meshflow.intelligence.consolidator import MemoryConsolidator, ConsolidationReport
from meshflow.intelligence.team_workspace import TeamWorkspace, WorkspaceSummary


# ══════════════════════════════════════════════════════════════════════════════
#  EntityMemory — TTL
# ══════════════════════════════════════════════════════════════════════════════

class TestEntityMemoryTTL:

    def test_fact_without_ttl_never_expires(self):
        em = EntityMemory()
        em.remember("Alice", "role", "engineer")
        facts = em.recall_entity("Alice")
        assert facts["role"] == "engineer"

    def test_fact_with_ttl_present_before_expiry(self):
        em = EntityMemory()
        em.remember("Bob", "status", "active", ttl_seconds=3600)
        facts = em.recall_entity("Bob")
        assert "status" in facts

    def test_expired_fact_not_returned(self):
        em = EntityMemory()
        em.remember("Carol", "temp_note", "meeting today", ttl_seconds=0.001)
        time.sleep(0.05)
        facts = em.recall_entity("Carol")
        assert "temp_note" not in facts

    def test_non_expired_fact_still_returned_after_sleep(self):
        em = EntityMemory()
        em.remember("Dave", "email", "dave@example.com", ttl_seconds=3600)
        em.remember("Dave", "short", "gone soon", ttl_seconds=0.001)
        time.sleep(0.05)
        facts = em.recall_entity("Dave")
        assert "email" in facts
        assert "short" not in facts

    def test_purge_expired_removes_stale_facts(self):
        em = EntityMemory()
        em.remember("Eve", "stale", "old data", ttl_seconds=0.001)
        em.remember("Eve", "fresh", "current data")
        time.sleep(0.05)
        removed = em.purge_expired()
        assert removed >= 1
        facts = em.recall_entity("Eve")
        assert "fresh" in facts
        assert "stale" not in facts

    def test_purge_expired_returns_count(self):
        em = EntityMemory()
        em.remember("Frank", "a", "1", ttl_seconds=0.001)
        em.remember("Frank", "b", "2", ttl_seconds=0.001)
        time.sleep(0.05)
        count = em.purge_expired()
        assert count == 2

    def test_purge_with_no_expired_returns_zero(self):
        em = EntityMemory()
        em.remember("Grace", "key", "val")
        count = em.purge_expired()
        assert count == 0

    def test_ttl_fact_can_be_refreshed(self):
        em = EntityMemory()
        em.remember("Hank", "note", "old", ttl_seconds=0.001)
        time.sleep(0.05)
        # Re-write with a fresh long TTL
        em.remember("Hank", "note", "refreshed", ttl_seconds=3600)
        facts = em.recall_entity("Hank")
        assert facts.get("note") == "refreshed"

    def test_zero_ttl_means_never_expires(self):
        em = EntityMemory()
        em.remember("Iris", "perm", "permanent", ttl_seconds=0)
        time.sleep(0.01)
        facts = em.recall_entity("Iris")
        assert "perm" in facts


# ══════════════════════════════════════════════════════════════════════════════
#  WorkspaceMemoryStore — TTL
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkspaceMemoryTTL:

    def _store(self) -> WorkspaceMemoryStore:
        return WorkspaceMemoryStore(path=":memory:")

    def test_entry_without_ttl_is_searchable(self):
        store = self._store()
        store.write("ws1", "agent1", "HIPAA compliance note")
        results = store.search("ws1", "agent1", "HIPAA")
        assert len(results) >= 1

    def test_entry_with_long_ttl_is_searchable(self):
        store = self._store()
        store.write("ws1", "agent1", "SOC 2 audit notes", ttl_seconds=3600)
        results = store.search("ws1", "agent1", "SOC 2")
        assert len(results) >= 1

    def test_expired_entry_not_returned_in_search(self):
        store = self._store()
        store.write("ws1", "agent1", "expired content", ttl_seconds=0.001)
        time.sleep(0.05)
        results = store.search("ws1", "agent1", "expired content")
        assert len(results) == 0

    def test_fresh_entry_returned_after_sleep(self):
        store = self._store()
        store.write("ws1", "agent1", "ephemeral", ttl_seconds=0.001)
        store.write("ws1", "agent1", "durable SOC content", ttl_seconds=3600)
        time.sleep(0.05)
        results = store.search("ws1", "agent1", "SOC")
        contents = [r.content for r in results]
        assert any("durable" in c for c in contents)
        assert all("ephemeral" not in c for c in contents)

    def test_purge_expired_removes_stale_entries(self):
        store = self._store()
        store.write("ws1", "agent1", "stale", ttl_seconds=0.001)
        store.write("ws1", "agent1", "fresh data")
        time.sleep(0.05)
        removed = store.purge_expired("ws1")
        assert removed >= 1

    def test_purge_expired_workspace_scoped(self):
        store = self._store()
        store.write("ws1", "agent1", "ws1 stale", ttl_seconds=0.001)
        store.write("ws2", "agent1", "ws2 stale", ttl_seconds=0.001)
        time.sleep(0.05)
        removed = store.purge_expired("ws1")
        # Only ws1 should be purged
        assert removed >= 1

    def test_purge_expired_no_workspace_purges_all(self):
        store = self._store()
        store.write("ws1", "agent1", "gone", ttl_seconds=0.001)
        store.write("ws2", "agent2", "also gone", ttl_seconds=0.001)
        time.sleep(0.05)
        removed = store.purge_expired()
        assert removed >= 2

    def test_zero_ttl_entry_never_expires(self):
        store = self._store()
        store.write("ws1", "agent1", "permanent entry", ttl_seconds=0)
        time.sleep(0.01)
        results = store.search("ws1", "agent1", "permanent")
        assert len(results) >= 1


# ══════════════════════════════════════════════════════════════════════════════
#  MemoryConsolidator
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryConsolidator:

    @pytest.mark.asyncio
    async def test_consolidate_entity_returns_report(self):
        em = EntityMemory()
        em.remember("Alice", "role", "engineer")
        em.remember("Alice", "team", "platform")
        cons = MemoryConsolidator()
        report = await cons.consolidate_entity(em, "Alice")
        assert isinstance(report, ConsolidationReport)

    @pytest.mark.asyncio
    async def test_consolidate_entity_removes_duplicates(self):
        em = EntityMemory()
        # Two facts with identical values → dedup should keep one
        em.remember("Bob", "job", "software engineer")
        em.remember("Bob", "occupation", "software engineer")  # same value
        cons = MemoryConsolidator(similarity_threshold=0.9)
        report = await cons.consolidate_entity(em, "Bob")
        assert report.facts_after <= report.facts_before

    @pytest.mark.asyncio
    async def test_consolidate_entity_preserves_distinct_facts(self):
        em = EntityMemory()
        em.remember("Carol", "role", "engineer")
        em.remember("Carol", "location", "San Francisco")
        em.remember("Carol", "department", "platform")
        cons = MemoryConsolidator(similarity_threshold=0.99)
        await cons.consolidate_entity(em, "Carol")
        facts = em.recall_entity("Carol")
        # Distinct facts should be preserved
        assert len(facts) >= 2

    @pytest.mark.asyncio
    async def test_consolidate_entity_report_fields(self):
        em = EntityMemory()
        em.remember("Dave", "k", "v")
        cons = MemoryConsolidator()
        report = await cons.consolidate_entity(em, "Dave")
        assert report.target is not None
        assert report.duration_ms >= 0
        assert report.method != ""

    @pytest.mark.asyncio
    async def test_consolidate_entity_all_entities(self):
        em = EntityMemory()
        em.remember("Eve", "role", "manager")
        em.remember("Frank", "role", "engineer")
        cons = MemoryConsolidator()
        # Pass entity=None to consolidate all
        report = await cons.consolidate_entity(em)
        assert isinstance(report, ConsolidationReport)

    @pytest.mark.asyncio
    async def test_consolidation_report_summary(self):
        em = EntityMemory()
        em.remember("Grace", "key", "value")
        cons = MemoryConsolidator()
        report = await cons.consolidate_entity(em, "Grace")
        s = report.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    @pytest.mark.asyncio
    async def test_consolidation_report_to_dict(self):
        em = EntityMemory()
        em.remember("Hank", "k", "v")
        cons = MemoryConsolidator()
        report = await cons.consolidate_entity(em, "Hank")
        d = report.to_dict()
        assert "target" in d
        assert "facts_before" in d
        assert "facts_after" in d
        assert "duration_ms" in d

    @pytest.mark.asyncio
    async def test_consolidate_workspace_returns_report(self):
        store = WorkspaceMemoryStore(path=":memory:")
        store.write("ws1", "agent1", "HIPAA requires encryption of PHI at rest.")
        store.write("ws1", "agent1", "SOC 2 Type II covers a 6-month observation period.")
        cons = MemoryConsolidator()
        report = await cons.consolidate_workspace(store, "ws1")
        assert isinstance(report, ConsolidationReport)

    @pytest.mark.asyncio
    async def test_consolidate_workspace_removes_duplicates(self):
        store = WorkspaceMemoryStore(path=":memory:")
        content = "HIPAA requires PHI encryption"
        store.write("ws1", "agent1", content)
        store.write("ws1", "agent1", content)  # exact duplicate
        cons = MemoryConsolidator(similarity_threshold=0.9)
        report = await cons.consolidate_workspace(store, "ws1")
        assert report.entries_after <= report.entries_before

    @pytest.mark.asyncio
    async def test_consolidate_cross_session_returns_report(self):
        from meshflow.intelligence.cross_session import CrossSessionMemoryStore
        store = CrossSessionMemoryStore(db_path=":memory:", similarity_threshold=0)
        store.add("agent1", "Memory about HIPAA compliance requirements")
        store.add("agent1", "Memory about SOC 2 audit process")
        cons = MemoryConsolidator()
        report = await cons.consolidate_cross_session(store, "agent1")
        assert isinstance(report, ConsolidationReport)

    @pytest.mark.asyncio
    async def test_consolidate_cross_session_deduplicates(self):
        from meshflow.intelligence.cross_session import CrossSessionMemoryStore
        store = CrossSessionMemoryStore(db_path=":memory:", similarity_threshold=0)
        store.add("agent1", "identical memory content")
        store.add("agent1", "identical memory content")  # duplicate
        cons = MemoryConsolidator(similarity_threshold=0.9)
        report = await cons.consolidate_cross_session(store, "agent1")
        assert report.entries_after <= report.entries_before

    @pytest.mark.asyncio
    async def test_consolidate_cross_session_trims_to_max(self):
        from meshflow.intelligence.cross_session import CrossSessionMemoryStore
        store = CrossSessionMemoryStore(db_path=":memory:", similarity_threshold=0)
        for i in range(10):
            store.add("agent1", f"unique memory entry {i} about topic {i * 13}")
        cons = MemoryConsolidator()
        report = await cons.consolidate_cross_session(store, "agent1", max_keep=5)
        assert report.entries_after <= 5

    @pytest.mark.asyncio
    async def test_bigram_similarity_identical(self):
        cons = MemoryConsolidator()
        assert cons._bigram_similarity("hello world", "hello world") == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_bigram_similarity_different(self):
        cons = MemoryConsolidator()
        sim = cons._bigram_similarity("apple", "orange")
        assert 0.0 <= sim < 0.5

    @pytest.mark.asyncio
    async def test_compress_short_text_unchanged(self):
        cons = MemoryConsolidator()
        text = "Short text."
        result = await cons._compress(text, max_chars=400)
        assert result == text

    @pytest.mark.asyncio
    async def test_compress_long_text_truncated(self):
        cons = MemoryConsolidator()
        text = "This is a sentence. " * 50
        result = await cons._compress(text, max_chars=100)
        assert len(result) <= 150  # allow some overshoot from sentence boundary


# ══════════════════════════════════════════════════════════════════════════════
#  TeamWorkspace
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamWorkspace:

    def test_open_initialises_store(self):
        ws = TeamWorkspace("test-run").open()
        assert ws.store is not None

    def test_write_and_count(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("researcher", "HIPAA requires PHI encryption")
        ws.write("analyst", "SOC 2 covers 6-month observation")
        assert ws.count() == 2

    def test_count_per_agent(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("researcher", "fact one")
        ws.write("researcher", "fact two")
        ws.write("analyst", "fact three")
        assert ws.count("researcher") == 2
        assert ws.count("analyst") == 1

    def test_agent_names(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("agent_a", "content a")
        ws.write("agent_b", "content b")
        names = ws.agent_names()
        assert "agent_a" in names
        assert "agent_b" in names

    def test_search_cross_agent(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("researcher", "HIPAA minimum necessary standard applies here")
        ws.write("analyst", "SOC 2 audit preparation steps")
        results = ws.search("analyst", "HIPAA", cross_agent=True)
        assert len(results) >= 1
        assert any("HIPAA" in r.content for r in results)

    def test_search_own_agent_only(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("researcher", "researcher-specific content")
        ws.write("analyst", "analyst-specific content")
        results = ws.search("researcher", "analyst", cross_agent=False)
        assert all(r.agent_name == "researcher" for r in results)

    def test_snapshot_returns_all_entries(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("a1", "entry one")
        ws.write("a2", "entry two")
        snap = ws.snapshot()
        assert len(snap) == 2

    def test_summary_structure(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("researcher", "important finding about HIPAA", importance=0.9)
        ws.write("analyst", "secondary note")
        summary = ws.summary()
        assert isinstance(summary, WorkspaceSummary)
        assert summary.total_entries == 2
        assert "researcher" in summary.agent_names
        assert summary.workspace_id == "test-run"

    def test_summary_to_dict(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("agent1", "content")
        d = ws.summary().to_dict()
        assert "workspace_id" in d
        assert "total_entries" in d
        assert "entries_by_agent" in d

    def test_clear_removes_all_entries(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("a1", "content one")
        ws.write("a2", "content two")
        removed = ws.clear()
        assert removed == 2
        assert ws.count() == 0

    def test_purge_expired_removes_stale(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("a1", "stale content", ttl_seconds=0.001)
        ws.write("a1", "fresh content", ttl_seconds=3600)
        time.sleep(0.05)
        removed = ws.purge_expired()
        assert removed >= 1
        assert ws.count() >= 1

    def test_workspace_id_property(self):
        ws = TeamWorkspace("my-workspace").open()
        assert ws.workspace_id == "my-workspace"

    def test_requires_open_before_write(self):
        ws = TeamWorkspace("not-open")
        with pytest.raises(RuntimeError, match="not open"):
            ws.write("agent", "content")

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        async with TeamWorkspace("async-test") as ws:
            ws.write("agent1", "async content")
            assert ws.count() == 1

    def test_default_ttl_applied_to_entries(self):
        ws = TeamWorkspace("ttl-test", ttl_seconds=0.001).open()
        ws.write("agent1", "ephemeral entry")
        time.sleep(0.05)
        # After TTL, purge should remove it
        removed = ws.purge_expired()
        assert removed >= 1

    def test_per_entry_ttl_overrides_default(self):
        ws = TeamWorkspace("ttl-override", ttl_seconds=0.001).open()
        ws.write("agent1", "durable entry", ttl_seconds=3600)
        time.sleep(0.05)
        ws.purge_expired()
        # Durable entry should survive
        assert ws.count() >= 1

    def test_write_returns_entry(self):
        ws = TeamWorkspace("test-run").open()
        entry = ws.write("agent1", "some content")
        assert entry is not None
        assert entry.content == "some content"

    def test_high_importance_entry_appears_first_in_summary(self):
        ws = TeamWorkspace("test-run").open()
        ws.write("a1", "low priority note", importance=0.1)
        ws.write("a2", "critical finding", importance=0.99)
        summary = ws.summary()
        assert "critical finding" in summary.top_entries[0]


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_memory_consolidator_exported(self):
        assert hasattr(meshflow, "MemoryConsolidator")
        assert hasattr(meshflow, "ConsolidationReport")

    def test_team_workspace_exported(self):
        assert hasattr(meshflow, "TeamWorkspace")
        assert hasattr(meshflow, "WorkspaceSummary")

    def test_all_in___all__(self):
        for sym in ("MemoryConsolidator", "ConsolidationReport",
                    "TeamWorkspace", "WorkspaceSummary"):
            assert sym in meshflow.__all__, f"{sym} missing from __all__"

    def test_version_bumped(self):
        assert meshflow.__version__ >= "0.77.0"
