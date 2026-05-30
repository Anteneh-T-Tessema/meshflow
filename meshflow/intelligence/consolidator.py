"""Memory consolidation — merge redundant facts and compress stale memories.

Consolidation runs offline (not on every read) and is idempotent: running it
twice produces the same result.  Use it as a nightly/weekly batch job or
trigger it when a store exceeds a size threshold.

Three consolidation targets:
- :class:`EntityMemory`          → merge duplicate facts for the same entity
- :class:`WorkspaceMemoryStore`  → merge overlapping workspace entries
- :class:`CrossSessionMemoryStore` → merge near-duplicate cross-session memories

Usage::

    from meshflow.intelligence.consolidator import MemoryConsolidator
    from meshflow.intelligence.entity_memory import EntityMemory

    em = EntityMemory("agent_facts.db")
    cons = MemoryConsolidator()
    report = await cons.consolidate_entity(em, "Alice")
    print(report.facts_before, "→", report.facts_after, "facts")
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConsolidationReport:
    """Summary of what a consolidation pass did."""

    target: str
    facts_before: int
    facts_after: int
    entries_before: int
    entries_after: int
    duration_ms: float
    method: str

    @property
    def facts_removed(self) -> int:
        return max(0, self.facts_before - self.facts_after)

    @property
    def entries_removed(self) -> int:
        return max(0, self.entries_before - self.entries_after)

    def summary(self) -> str:
        parts = []
        if self.facts_before:
            parts.append(
                f"facts {self.facts_before}→{self.facts_after} "
                f"(-{self.facts_removed})"
            )
        if self.entries_before:
            parts.append(
                f"entries {self.entries_before}→{self.entries_after} "
                f"(-{self.entries_removed})"
            )
        return f"[{self.target}] {', '.join(parts) or 'nothing to consolidate'} ({self.method}, {self.duration_ms:.0f}ms)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "facts_before": self.facts_before,
            "facts_after": self.facts_after,
            "entries_before": self.entries_before,
            "entries_after": self.entries_after,
            "facts_removed": self.facts_removed,
            "entries_removed": self.entries_removed,
            "duration_ms": round(self.duration_ms, 1),
            "method": self.method,
        }


class MemoryConsolidator:
    """Merge redundant memories and compress stale facts across all memory stores.

    Parameters
    ----------
    provider:
        Optional LLM provider for smart (LLM-based) consolidation.
        Falls back to extractive summarisation when None or unavailable.
    model:
        Model to use for LLM consolidation (default: haiku for cost efficiency).
    similarity_threshold:
        Minimum bigram-overlap similarity (0–1) to consider two facts/entries
        as duplicates.  Default 0.75.
    """

    def __init__(
        self,
        provider: Any = None,
        model: str = "claude-haiku-4-5",
        similarity_threshold: float = 0.75,
    ) -> None:
        self._provider = provider
        self._model = model
        self._sim_threshold = similarity_threshold

    # ── EntityMemory ───────────────────────────────────────────────────────────

    async def consolidate_entity(
        self,
        store: Any,
        entity: str | None = None,
    ) -> ConsolidationReport:
        """Consolidate facts in *store* for *entity* (or all entities if None).

        Steps:
        1. Purge expired facts.
        2. For each entity, merge fact values that are near-duplicates.
        3. Compress fact values longer than 500 chars using LLM or extractive.
        """
        t0 = time.monotonic()
        entities = [entity] if entity else store.list_entities()
        facts_before = sum(len(store.recall_entity(e)) for e in entities)

        expired = store.purge_expired()
        method = "extractive"

        for ent in entities:
            facts = store.recall_entity(ent)
            merged = await self._merge_entity_facts(facts)
            # Write back only changed/compressed facts
            for key, val in merged.items():
                orig = facts.get(key, "")
                if val != orig:
                    store.remember(ent, key, val)
            # Remove keys that were merged into others
            for key in set(facts) - set(merged):
                store.forget_fact(ent, key)

        facts_after = sum(len(store.recall_entity(e)) for e in store.list_entities())
        return ConsolidationReport(
            target=f"EntityMemory[{entity or 'all'}]",
            facts_before=facts_before,
            facts_after=facts_after,
            entries_before=0,
            entries_after=0,
            duration_ms=(time.monotonic() - t0) * 1000,
            method=method,
        )

    async def _merge_entity_facts(self, facts: dict[str, str]) -> dict[str, str]:
        """Merge near-duplicate fact values and compress long ones."""
        if not facts:
            return facts

        merged: dict[str, str] = {}
        seen_values: list[str] = []

        for key, val in facts.items():
            # Check if this value is near-duplicate of an existing one
            is_dup = any(
                self._bigram_similarity(val, sv) >= self._sim_threshold
                for sv in seen_values
            )
            if is_dup:
                continue  # drop the duplicate

            # Compress long values
            compressed = await self._compress(val, max_chars=400)
            merged[key] = compressed
            seen_values.append(val)

        return merged

    # ── WorkspaceMemoryStore ───────────────────────────────────────────────────

    async def consolidate_workspace(
        self,
        store: Any,
        workspace_id: str,
        *,
        agent_name: str = "*",
    ) -> ConsolidationReport:
        """Consolidate entries in a workspace.

        Steps:
        1. Purge expired entries.
        2. Merge near-duplicate content entries.
        3. Compress entries longer than 600 chars.
        """
        t0 = time.monotonic()
        snapshot = store.snapshot(workspace_id)
        entries_before = len(snapshot)
        store.purge_expired(workspace_id)

        # Group by agent_name (or "*" = all)
        by_agent: dict[str, list[dict[str, Any]]] = {}
        for e in snapshot:
            a = e.get("agent_name", "")
            by_agent.setdefault(a, []).append(e)

        removed = 0
        for agent, entries in by_agent.items():
            if agent_name != "*" and agent != agent_name:
                continue
            kept, dropped = await self._merge_workspace_entries(entries)
            for d in dropped:
                try:
                    store._connect().execute(
                        "DELETE FROM workspace_memories WHERE entry_id=?",
                        (d.get("entry_id", ""),)
                    )
                    store._connect().commit()
                except Exception:
                    pass
            removed += len(dropped)

        entries_after = entries_before - removed
        return ConsolidationReport(
            target=f"WorkspaceMemory[{workspace_id}]",
            facts_before=0,
            facts_after=0,
            entries_before=entries_before,
            entries_after=entries_after,
            duration_ms=(time.monotonic() - t0) * 1000,
            method="extractive",
        )

    async def _merge_workspace_entries(
        self, entries: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (kept, dropped) after deduplication."""
        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        seen: list[str] = []

        for entry in entries:
            content = entry.get("content", "")
            if any(self._bigram_similarity(content, s) >= self._sim_threshold for s in seen):
                dropped.append(entry)
            else:
                kept.append(entry)
                seen.append(content)

        return kept, dropped

    # ── CrossSessionMemoryStore ────────────────────────────────────────────────

    async def consolidate_cross_session(
        self,
        store: Any,
        agent_id: str,
        *,
        max_keep: int = 200,
    ) -> ConsolidationReport:
        """Consolidate cross-session memories for *agent_id*.

        Steps:
        1. Remove duplicate memories (bigram similarity ≥ threshold).
        2. If count still exceeds *max_keep*, evict oldest/least-accessed.
        """
        t0 = time.monotonic()
        entries_before = store.count(agent_id)
        all_memories = store.list_memories(agent_id, limit=1000)

        seen: list[str] = []
        to_delete: list[str] = []

        for mem in all_memories:
            if any(self._bigram_similarity(mem.content, s) >= self._sim_threshold for s in seen):
                to_delete.append(mem.memory_id)
            else:
                seen.append(mem.content)

        for mid in to_delete:
            store.delete(mid)

        # Trim to max_keep by LRU (oldest accessed_at first)
        remaining = store.list_memories(agent_id, limit=1000)
        if len(remaining) > max_keep:
            oldest = sorted(remaining, key=lambda m: m.accessed_at)[: len(remaining) - max_keep]
            for mem in oldest:
                store.delete(mem.memory_id)
                to_delete.append(mem.memory_id)

        entries_after = store.count(agent_id)
        return ConsolidationReport(
            target=f"CrossSessionMemory[{agent_id}]",
            facts_before=0,
            facts_after=0,
            entries_before=entries_before,
            entries_after=entries_after,
            duration_ms=(time.monotonic() - t0) * 1000,
            method="bigram-dedup",
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _compress(self, text: str, max_chars: int = 400) -> str:
        """Compress *text* to ≤ max_chars using LLM or first-sentence extraction."""
        if len(text) <= max_chars:
            return text

        if self._provider is not None:
            try:
                prompt = (
                    f"Compress the following text to ≤{max_chars} characters "
                    f"while preserving all key facts. Output ONLY the compressed text:\n\n{text}"
                )
                content, _, _ = await self._provider.complete(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    system="You are a concise technical summarizer.",
                    max_tokens=256,
                )
                if content and len(content) <= max_chars + 50:
                    return content[:max_chars]
            except Exception:
                pass

        # Extractive fallback: keep first sentence(s) up to max_chars
        sentences = re.split(r"(?<=[.!?])\s+", text)
        result = ""
        for s in sentences:
            if len(result) + len(s) + 1 > max_chars:
                break
            result = (result + " " + s).strip()
        return result or text[:max_chars]

    @staticmethod
    def _bigram_similarity(a: str, b: str) -> float:
        """Character bigram Jaccard similarity in [0, 1]."""
        def bigrams(s: str) -> set[str]:
            s = s.lower()
            return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()
        ba, bb = bigrams(a), bigrams(b)
        if not ba and not bb:
            return 1.0
        if not ba or not bb:
            return 0.0
        return len(ba & bb) / len(ba | bb)
