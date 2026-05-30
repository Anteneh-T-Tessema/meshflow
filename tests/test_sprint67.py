"""Sprint 67 — RAG token budget, context window pruning, cross-session memory.

All tests are deterministic and require no API key.
"""

from __future__ import annotations

import pytest

import meshflow
from meshflow.agents.rag_budget import RAGTokenBudget, KnowledgeBudgetResult
from meshflow.core.context_pruner import SlidingWindowPruner, SummaryPruner
from meshflow.intelligence.cross_session import CrossSessionMemoryStore, MemoryEntry


# ══════════════════════════════════════════════════════════════════════════════
#  RAGTokenBudget
# ══════════════════════════════════════════════════════════════════════════════

class TestRAGTokenBudget:

    def test_requires_at_least_one_limit(self):
        with pytest.raises(ValueError, match="max_chars or max_tokens"):
            RAGTokenBudget()

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            RAGTokenBudget(max_chars=100, strategy="invalid")

    def test_all_fit_returns_all_included(self):
        budget = RAGTokenBudget(max_chars=1000)
        result = budget.apply(["hello", "world"])
        assert result.included == ["hello", "world"]
        assert result.truncated == []
        assert result.dropped == []
        assert not result.over_budget

    def test_truncate_strategy_clips_last_block(self):
        budget = RAGTokenBudget(max_chars=10, strategy="truncate")
        result = budget.apply(["12345", "abcdefghij"])
        assert result.included[0] == "12345"
        assert len(result.included[1]) == 5   # 10 - 5 = 5 chars remaining
        assert len(result.truncated) == 1

    def test_drop_strategy_excludes_overflowing_blocks(self):
        budget = RAGTokenBudget(max_chars=10, strategy="drop")
        result = budget.apply(["12345", "abcdefghij"])
        assert result.included == ["12345"]
        assert result.dropped == ["abcdefghij"]

    def test_tail_strategy_keeps_most_recent(self):
        budget = RAGTokenBudget(max_chars=10, strategy="tail")
        result = budget.apply(["first_block_too_long", "recent"])
        assert "recent" in result.included
        assert "first_block_too_long" not in result.included

    def test_max_tokens_applied(self):
        # 1 token ≈ 4 chars → max_tokens=2 → ≈8 chars
        budget = RAGTokenBudget(max_tokens=2, strategy="drop")
        result = budget.apply(["ab", "cd", "efghijklmnop"])
        assert result.total_chars <= 8

    def test_both_limits_uses_stricter(self):
        # max_chars=100 but max_tokens=1 (≈4 chars) → 4 wins
        budget = RAGTokenBudget(max_chars=100, max_tokens=1, strategy="drop")
        result = budget.apply(["abcde", "xyz"])
        # Only ≈4 chars allowed, "abcde" (5 chars) dropped, "xyz" (3 chars) fits
        assert result.total_chars <= 4

    def test_to_prompt_text_joins_blocks(self):
        budget = RAGTokenBudget(max_chars=1000)
        result = budget.apply(["block one", "block two"])
        text = result.to_prompt_text()
        assert "block one" in text
        assert "block two" in text

    def test_apply_to_text_convenience(self):
        budget = RAGTokenBudget(max_chars=1000)
        text = budget.apply_to_text(["a", "b"])
        assert "a" in text and "b" in text

    def test_empty_blocks_returns_empty_result(self):
        budget = RAGTokenBudget(max_chars=100)
        result = budget.apply([])
        assert result.included == []
        assert result.total_chars == 0

    def test_objects_with_text_attribute(self):
        class Block:
            def __init__(self, text): self.text = text
        budget = RAGTokenBudget(max_chars=1000)
        result = budget.apply([Block("hello"), Block("world")])
        assert result.included == ["hello", "world"]

    def test_objects_with_content_attribute(self):
        class Doc:
            def __init__(self, content): self.content = content
        budget = RAGTokenBudget(max_chars=1000)
        result = budget.apply([Doc("foo"), Doc("bar")])
        assert result.included == ["foo", "bar"]

    def test_budget_chars_reported(self):
        budget = RAGTokenBudget(max_chars=500)
        result = budget.apply(["hello"])
        assert result.budget_chars == 500

    def test_result_fields(self):
        budget = RAGTokenBudget(max_chars=8, strategy="drop")
        result = budget.apply(["abc", "defghijk"])
        assert isinstance(result, KnowledgeBudgetResult)
        assert result.total_chars == 3
        assert result.dropped == ["defghijk"]


# ══════════════════════════════════════════════════════════════════════════════
#  SlidingWindowPruner
# ══════════════════════════════════════════════════════════════════════════════

def _msgs(*contents, include_system=False):
    out = []
    if include_system:
        out.append({"role": "system", "content": "You are helpful."})
    for i, c in enumerate(contents):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": c})
    return out


class TestSlidingWindowPruner:

    def test_no_pruning_when_under_limit(self):
        pruner = SlidingWindowPruner(max_messages=10)
        msgs = _msgs("a", "b", "c")
        result = pruner.prune(msgs)
        assert result.messages == msgs
        assert result.removed_count == 0

    def test_prunes_oldest_messages(self):
        pruner = SlidingWindowPruner(max_messages=3)
        msgs = _msgs("a", "b", "c", "d", "e")
        result = pruner.prune(msgs)
        assert len(result.messages) == 3
        # Should keep the 3 most recent
        contents = [m["content"] for m in result.messages]
        assert "c" in contents or "d" in contents or "e" in contents

    def test_system_message_preserved(self):
        pruner = SlidingWindowPruner(max_messages=2)
        msgs = _msgs("a", "b", "c", "d", include_system=True)
        result = pruner.prune(msgs)
        assert result.messages[0]["role"] == "system"

    def test_system_message_not_counted(self):
        pruner = SlidingWindowPruner(max_messages=2)
        msgs = _msgs("a", "b", "c", include_system=True)
        result = pruner.prune(msgs)
        # 1 system + 2 most recent non-system
        assert len(result.messages) == 3
        assert result.messages[0]["role"] == "system"

    def test_preserve_system_false(self):
        pruner = SlidingWindowPruner(max_messages=2, preserve_system=False)
        msgs = [{"role": "system", "content": "sys"}] + _msgs("a", "b", "c", "d")
        result = pruner.prune(msgs)
        assert len(result.messages) == 2

    def test_max_messages_one(self):
        pruner = SlidingWindowPruner(max_messages=1)
        msgs = _msgs("a", "b", "c")
        result = pruner.prune(msgs)
        assert len(result.messages) == 1

    def test_invalid_max_messages(self):
        with pytest.raises(ValueError):
            SlidingWindowPruner(max_messages=0)

    def test_strategy_reported(self):
        pruner = SlidingWindowPruner(max_messages=2)
        result = pruner.prune(_msgs("a", "b", "c", "d", "e"))
        assert result.strategy == "sliding_window"

    def test_original_count_correct(self):
        pruner = SlidingWindowPruner(max_messages=2)
        msgs = _msgs("a", "b", "c", "d")
        result = pruner.prune(msgs)
        assert result.original_count == 4
        assert result.pruned_count == 2

    def test_estimated_tokens_positive(self):
        pruner = SlidingWindowPruner(max_messages=10)
        msgs = _msgs("hello world", "foo bar")
        result = pruner.prune(msgs)
        assert result.estimated_tokens > 0


# ══════════════════════════════════════════════════════════════════════════════
#  SummaryPruner
# ══════════════════════════════════════════════════════════════════════════════

class TestSummaryPruner:

    @pytest.mark.asyncio
    async def test_noop_when_under_token_limit(self):
        pruner = SummaryPruner(max_tokens=10000, keep_recent=4)
        msgs = _msgs("hi", "hello")
        result = await pruner.prune(msgs)
        assert result.messages == msgs
        assert result.strategy == "summary_noop"

    @pytest.mark.asyncio
    async def test_compresses_old_messages(self):
        # Force compression with tiny max_tokens
        pruner = SummaryPruner(max_tokens=1, keep_recent=2)
        msgs = _msgs("a", "b", "c", "d", "e", "f")
        result = await pruner.prune(msgs)
        assert result.strategy == "summary"
        # Should have a summary msg + the 2 most recent
        roles = [m["role"] for m in result.messages]
        assert "assistant" in roles  # summary is an assistant message

    @pytest.mark.asyncio
    async def test_summary_message_content(self):
        pruner = SummaryPruner(max_tokens=1, keep_recent=2)
        msgs = _msgs("question one", "answer one", "question two", "answer two")
        result = await pruner.prune(msgs)
        summary_msg = next(m for m in result.messages if "[Conversation summary]" in m.get("content", ""))
        assert summary_msg is not None

    @pytest.mark.asyncio
    async def test_custom_summarize_fn_sync(self):
        def my_summary(msgs):
            return "CUSTOM SUMMARY"

        pruner = SummaryPruner(max_tokens=1, keep_recent=2, summarize_fn=my_summary)
        msgs = _msgs("a", "b", "c", "d")
        result = await pruner.prune(msgs)
        combined = " ".join(m.get("content", "") for m in result.messages)
        assert "CUSTOM SUMMARY" in combined

    @pytest.mark.asyncio
    async def test_custom_summarize_fn_async(self):
        async def my_summary(msgs):
            return "ASYNC SUMMARY"

        pruner = SummaryPruner(max_tokens=1, keep_recent=2, summarize_fn=my_summary)
        msgs = _msgs("a", "b", "c", "d")
        result = await pruner.prune(msgs)
        combined = " ".join(m.get("content", "") for m in result.messages)
        assert "ASYNC SUMMARY" in combined

    @pytest.mark.asyncio
    async def test_system_message_preserved(self):
        pruner = SummaryPruner(max_tokens=1, keep_recent=2)
        msgs = [{"role": "system", "content": "Be helpful."}] + _msgs("a", "b", "c", "d")
        result = await pruner.prune(msgs)
        assert result.messages[0]["role"] == "system"

    def test_invalid_keep_recent(self):
        with pytest.raises(ValueError):
            SummaryPruner(keep_recent=1)

    @pytest.mark.asyncio
    async def test_pruned_count_less_than_original(self):
        pruner = SummaryPruner(max_tokens=1, keep_recent=2)
        msgs = _msgs("a", "b", "c", "d", "e", "f")
        result = await pruner.prune(msgs)
        assert result.pruned_count < result.original_count


# ══════════════════════════════════════════════════════════════════════════════
#  CrossSessionMemoryStore
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossSessionMemoryStore:

    def _store(self):
        return CrossSessionMemoryStore(db_path=":memory:", similarity_threshold=0)

    def test_add_and_get(self):
        store = self._store()
        entry = store.add("agent1", "User prefers bullet points")
        assert entry.memory_id is not None
        fetched = store.get(entry.memory_id)
        assert fetched is not None
        assert fetched.content == "User prefers bullet points"

    def test_access_count_increments(self):
        store = self._store()
        entry = store.add("agent1", "some fact")
        store.get(entry.memory_id)
        store.get(entry.memory_id)
        fetched = store.get(entry.memory_id)
        assert fetched.access_count >= 2

    def test_tags_stored_and_retrieved(self):
        store = self._store()
        entry = store.add("agent1", "content", tags=["style", "ux"])
        fetched = store.get(entry.memory_id)
        assert "style" in fetched.tags
        assert "ux" in fetched.tags

    def test_metadata_stored(self):
        store = self._store()
        entry = store.add("agent1", "content", metadata={"source": "user"})
        fetched = store.get(entry.memory_id)
        assert fetched.metadata["source"] == "user"

    def test_session_id_stored(self):
        store = self._store()
        entry = store.add("agent1", "content", session_id="sess_abc")
        fetched = store.get(entry.memory_id)
        assert fetched.session_id == "sess_abc"

    def test_list_returns_agent_memories(self):
        store = self._store()
        store.add("agent1", "fact one")
        store.add("agent1", "fact two")
        store.add("agent2", "other agent")
        entries = store.list_memories("agent1")
        assert len(entries) == 2
        assert all(e.agent_id == "agent1" for e in entries)

    def test_list_filter_by_session(self):
        store = self._store()
        store.add("agent1", "in session", session_id="s1")
        store.add("agent1", "other session", session_id="s2")
        entries = store.list_memories("agent1", session_id="s1")
        assert len(entries) == 1
        assert entries[0].content == "in session"

    def test_list_filter_by_tags(self):
        store = self._store()
        store.add("agent1", "tagged", tags=["important"])
        store.add("agent1", "untagged")
        entries = store.list_memories("agent1", tags=["important"])
        assert len(entries) == 1
        assert entries[0].content == "tagged"

    def test_search_returns_relevant_entries(self):
        store = self._store()
        store.add("agent1", "Python programming tips")
        store.add("agent1", "Cooking recipes for pasta")
        store.add("agent1", "Python async patterns")
        results = store.search("agent1", "Python", top_k=2)
        assert len(results) == 2
        assert all("Python" in r.content for r in results)

    def test_search_respects_top_k(self):
        store = self._store()
        for i in range(10):
            store.add("agent1", f"memory {i}")
        results = store.search("agent1", "memory", top_k=3)
        assert len(results) == 3

    def test_delete_removes_entry(self):
        store = self._store()
        entry = store.add("agent1", "to be deleted")
        deleted = store.delete(entry.memory_id)
        assert deleted is True
        assert store.get(entry.memory_id) is None

    def test_delete_nonexistent_returns_false(self):
        store = self._store()
        assert store.delete("nonexistent_id") is False

    def test_update_content(self):
        store = self._store()
        entry = store.add("agent1", "original content")
        updated = store.update(entry.memory_id, content="updated content")
        assert updated is True
        fetched = store.get(entry.memory_id)
        assert fetched.content == "updated content"

    def test_update_tags(self):
        store = self._store()
        entry = store.add("agent1", "content", tags=["old"])
        store.update(entry.memory_id, tags=["new"])
        fetched = store.get(entry.memory_id)
        assert fetched.tags == ["new"]

    def test_update_nonexistent_returns_false(self):
        store = self._store()
        assert store.update("bad_id", content="x") is False

    def test_clear_removes_all_agent_memories(self):
        store = self._store()
        store.add("agent1", "a")
        store.add("agent1", "b")
        store.add("agent2", "c")
        deleted = store.clear("agent1")
        assert deleted == 2
        assert store.count("agent1") == 0
        assert store.count("agent2") == 1

    def test_count(self):
        store = self._store()
        assert store.count("agent1") == 0
        store.add("agent1", "one")
        store.add("agent1", "two")
        assert store.count("agent1") == 2

    def test_dedup_prevents_duplicate(self):
        store = CrossSessionMemoryStore(db_path=":memory:", similarity_threshold=0.9)
        store.add("agent1", "User prefers dark mode theme")
        store.add("agent1", "User prefers dark mode theme")  # near-identical
        assert store.count("agent1") == 1

    def test_dedup_disabled_allows_duplicates(self):
        store = CrossSessionMemoryStore(db_path=":memory:", similarity_threshold=0)
        store.add("agent1", "same content", deduplicate=False)
        store.add("agent1", "same content", deduplicate=False)
        assert store.count("agent1") == 2

    def test_eviction_at_max_entries(self):
        store = CrossSessionMemoryStore(db_path=":memory:", max_entries_per_agent=3, similarity_threshold=0)
        for i in range(5):
            store.add("agent1", f"entry {i}")
        assert store.count("agent1") <= 3

    def test_memory_entry_fields(self):
        store = self._store()
        entry = store.add("agent1", "test", tags=["t"], metadata={"k": "v"}, session_id="s1")
        assert isinstance(entry, MemoryEntry)
        assert entry.agent_id == "agent1"
        assert entry.content == "test"
        assert entry.created_at > 0
        assert entry.age_seconds() >= 0
        assert entry.to_dict()["content"] == "test"

    def test_get_nonexistent_returns_none(self):
        store = self._store()
        assert store.get("no_such_id") is None

    def test_multi_agent_isolation(self):
        store = self._store()
        store.add("agent_a", "agent a memory")
        store.add("agent_b", "agent b memory")
        a_results = store.search("agent_a", "memory")
        b_results = store.search("agent_b", "memory")
        assert all(r.agent_id == "agent_a" for r in a_results)
        assert all(r.agent_id == "agent_b" for r in b_results)


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_rag_budget_exported(self):
        assert hasattr(meshflow, "RAGTokenBudget")
        assert hasattr(meshflow, "KnowledgeBudgetResult")

    def test_context_pruner_exported(self):
        assert hasattr(meshflow, "SlidingWindowPruner")
        assert hasattr(meshflow, "SummaryPruner")
        assert hasattr(meshflow, "PruneResult")

    def test_cross_session_exported(self):
        assert hasattr(meshflow, "CrossSessionMemoryStore")
        assert hasattr(meshflow, "CrossSessionEntry")

    def test_in_all(self):
        for sym in (
            "RAGTokenBudget", "KnowledgeBudgetResult",
            "SlidingWindowPruner", "SummaryPruner", "PruneResult",
            "CrossSessionMemoryStore", "CrossSessionEntry",
        ):
            assert sym in meshflow.__all__, f"{sym} missing from __all__"

    def test_version_bumped(self):
        assert meshflow.__version__ >= "0.77.0"
