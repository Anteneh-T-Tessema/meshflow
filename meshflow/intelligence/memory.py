"""4-tier AgentMemory — replaces the truncated dict in _BuiltAgent.

Tier 1 — Working   : last N entries, always in the LLM context window.
Tier 2 — Episodic  : compressed summaries of older turns this session.
Tier 3 — Semantic  : BM25-style recall across all accumulated content.
Tier 4 — Procedural: verifier/outcome records from the ledger (append-only).

The design is intentionally zero-dependency for Tiers 1-3.  numpy improves
Tier-3 recall quality but is not required.  The ledger integration for Tier 4
is wired in StepRuntime and does not require any extra deps.

Usage::

    from meshflow.intelligence.memory import AgentMemory

    mem = AgentMemory(agent_id="researcher", max_working=10)

    mem.add("Learned that HIPAA §164.502 covers minimum-necessary disclosures.")
    mem.add("Treatment purpose is a TPO exception — authorization not required.")

    relevant = mem.recall("What are the HIPAA exceptions for treatment?", top_k=2)
    # ["Treatment purpose is a TPO exception...", "Learned that HIPAA §164.502..."]

    # Inject into LLM prompt
    ctx = mem.context_string()
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


# ── Entry types ───────────────────────────────────────────────────────────────

@dataclass
class MemoryItem:
    content: str
    tier: str  # "working" | "episodic" | "semantic" | "procedural"
    timestamp: float = field(default_factory=time.monotonic)
    metadata: dict[str, Any] = field(default_factory=dict)
    access_count: int = 0

    @property
    def key(self) -> str:
        return hashlib.md5(self.content.encode()).hexdigest()[:12]


# ── Tier-3 BM25-style index (zero numpy dependency) ──────────────────────────

class _BM25Index:
    """Lightweight BM25 index for semantic recall without external deps.

    Falls back to simple TF-IDF when the corpus is small.
    O(n) search — sufficient for < 10k entries per agent session.
    """

    K1 = 1.5
    B  = 0.75

    def __init__(self) -> None:
        self._docs: list[str]         = []
        self._tokenized: list[list[str]] = []
        self._df: dict[str, int]      = {}
        self._avg_len: float          = 0.0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def add(self, content: str) -> None:
        tokens = self._tokenize(content)
        self._docs.append(content)
        self._tokenized.append(tokens)
        for tok in set(tokens):
            self._df[tok] = self._df.get(tok, 0) + 1
        total = sum(len(t) for t in self._tokenized)
        self._avg_len = total / max(len(self._tokenized), 1)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        if not self._docs:
            return []
        q_tokens = self._tokenize(query)
        n = len(self._docs)
        scores: list[tuple[str, float]] = []

        for idx, (doc, tokens) in enumerate(zip(self._docs, self._tokenized)):
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            doc_len = len(tokens)
            score = 0.0
            for qt in q_tokens:
                df = self._df.get(qt, 0)
                if df == 0:
                    continue
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
                f = tf.get(qt, 0)
                denom = f + self.K1 * (1 - self.B + self.B * doc_len / max(self._avg_len, 1))
                score += idf * (f * (self.K1 + 1)) / denom
            if score > 0:
                scores.append((doc, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# ── AgentMemory ───────────────────────────────────────────────────────────────

class AgentMemory:
    """4-tier memory for a single agent across its lifetime.

    Parameters
    ----------
    agent_id:
        Identifier for the owning agent (used in ledger records).
    max_working:
        Maximum entries in working memory before promotion to episodic.
    max_episodic:
        Maximum episodic summaries before oldest are dropped.
    enabled:
        False → all operations are no-ops (disabled state).
    """

    def __init__(
        self,
        agent_id: str = "agent",
        max_working: int = 10,
        max_episodic: int = 50,
        enabled: bool = True,
        auto_consolidate: bool = True,
        consolidate_at_chars: int = 20_000,
    ) -> None:
        """
        Parameters
        ----------
        auto_consolidate:
            When True (default), automatically prune episodic memory when the
            total character footprint of all tiers exceeds *consolidate_at_chars*.
            The consolidation keeps the most-important half of episodic entries
            (scored by recency + content length) and drops the rest.
        consolidate_at_chars:
            Character budget that triggers auto-consolidation (default 20 000).
        """
        self._agent_id = agent_id
        self._enabled = enabled
        self._max_working = max_working
        self._max_episodic = max_episodic
        self._auto_consolidate = auto_consolidate
        self._consolidate_at_chars = consolidate_at_chars

        # Tier 1 — working (deque for O(1) append/evict)
        self._working: deque[MemoryItem] = deque(maxlen=max_working)
        # Tier 2 — episodic (summaries of evicted working entries)
        self._episodic: list[MemoryItem] = []
        # Tier 3 — semantic index (BM25 over all content ever added)
        self._index = _BM25Index()
        # Tier 4 — procedural (verifier/outcome records from ledger)
        self._procedural: list[MemoryItem] = []

        self._step_count = 0

    # ── Write ─────────────────────────────────────────────────────────────────

    def add(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        tier_hint: str = "working",
    ) -> None:
        """Add content to the appropriate memory tier."""
        if not self._enabled or not content:
            return

        self._step_count += 1
        item = MemoryItem(
            content=content[:2000],  # hard cap; LLMs don't benefit from longer raw text
            tier=tier_hint,
            metadata={"step": self._step_count, **(metadata or {})},
        )

        if tier_hint == "procedural":
            self._procedural.append(item)
        else:
            # Check if working is full — if so, demote oldest to episodic
            if len(self._working) == self._max_working:
                oldest = self._working[0]
                self._promote_to_episodic(oldest)

            self._working.append(item)

        # All non-procedural content goes into the semantic index
        if tier_hint != "procedural":
            self._index.add(content)

        # Auto-consolidate when total char footprint exceeds budget
        if self._auto_consolidate and self._enabled:
            self._maybe_consolidate()

    def record_outcome(
        self, node_id: str, success: bool, confidence: float, verifier_score: float = 0.0
    ) -> None:
        """Record a step outcome in procedural (Tier 4) memory."""
        if not self._enabled:
            return
        summary = (
            f"node={node_id} success={'yes' if success else 'no'} "
            f"confidence={confidence:.2f} verifier={verifier_score:.2f}"
        )
        self.add(summary, {"node_id": node_id, "success": success}, tier_hint="procedural")

    def _promote_to_episodic(self, item: MemoryItem) -> None:
        item.tier = "episodic"
        self._episodic.append(item)
        if len(self._episodic) > self._max_episodic:
            self._episodic.pop(0)

    def _total_chars(self) -> int:
        return (
            sum(len(i.content) for i in self._working)
            + sum(len(i.content) for i in self._episodic)
            + sum(len(i.content) for i in self._procedural)
        )

    def _maybe_consolidate(self) -> None:
        """Trim episodic memory when char footprint exceeds the budget."""
        if not self._episodic:
            return
        if self._total_chars() <= self._consolidate_at_chars:
            return
        self.consolidate()

    def consolidate(self) -> int:
        """Prune the lower-importance half of episodic memory now.

        Importance score = recency (newer = higher) + content length (longer = more signal).
        Returns the number of entries dropped.
        """
        if not self._episodic:
            return 0
        n = len(self._episodic)
        keep_n = max(1, n // 2)

        # Score: normalise step position (recency) + normalised char length
        max_len = max(len(i.content) for i in self._episodic) or 1
        scored = [
            (
                (idx / max(n - 1, 1)) * 0.6        # recency weight
                + (len(item.content) / max_len) * 0.4,  # length weight
                item,
            )
            for idx, item in enumerate(self._episodic)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        kept = [item for _, item in scored[:keep_n]]
        # Preserve original chronological order
        kept_set = {id(i) for i in kept}
        self._episodic = [i for i in self._episodic if id(i) in kept_set]
        return n - len(self._episodic)

    # ── Read ──────────────────────────────────────────────────────────────────

    def recall(self, query: str, top_k: int = 3) -> list[str]:
        """Return the most relevant memories by BM25 score with a recency bonus.

        Recency bonus: working-memory items get +0.5 added to their BM25 score so
        that recent context beats slightly less-relevant older entries, but a highly
        relevant older entry still beats an irrelevant recent one.
        """
        if not self._enabled:
            return []

        # Build a recency-bonus set from working memory keys
        working_keys: set[str] = {item.key for item in self._working}

        # BM25 search over all indexed content
        bm25_hits: list[tuple[str, float]] = self._index.search(query, top_k=top_k * 3)

        # Boost working-memory hits and de-duplicate
        seen: set[str] = set()
        scored: list[tuple[float, str]] = []
        for content, score in bm25_hits:
            h = hashlib.md5(content.encode()).hexdigest()[:12]
            if h in seen:
                continue
            seen.add(h)
            bonus = 0.5 if h in working_keys else 0.0
            scored.append((score + bonus, content))

        # Include any working items the BM25 didn't surface (edge case: very small corpus)
        for item in reversed(self._working):
            if item.key not in seen:
                seen.add(item.key)
                scored.append((0.1, item.content))  # low score — recency only

        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:top_k]]

    def recent(self, n: int = 5) -> list[str]:
        """Return the most recent *n* working-memory entries."""
        if not self._enabled:
            return []
        items = list(self._working)[-n:]
        return [item.content for item in reversed(items)]

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def working_count(self) -> int:
        return len(self._working)

    @property
    def episodic_count(self) -> int:
        return len(self._episodic)

    @property
    def total_items(self) -> int:
        return len(self._working) + len(self._episodic) + len(self._procedural)

    def stats(self) -> dict[str, Any]:
        return {
            "agent_id": self._agent_id,
            "working": len(self._working),
            "episodic": len(self._episodic),
            "procedural": len(self._procedural),
            "semantic_index_size": len(self._index._docs),
            "steps": self._step_count,
        }

    def reset(self) -> None:
        """Clear all tiers. Useful for testing or session reset."""
        self._working.clear()
        self._episodic.clear()
        self._procedural.clear()
        self._index = _BM25Index()
        self._step_count = 0
        if hasattr(self, "_entity"):
            from meshflow.intelligence.entity_memory import EntityMemory
            self._entity = EntityMemory()

    # ── Snapshot helpers (thin wrappers for ergonomic API) ────────────────────

    def to_snapshot(self) -> dict[str, Any]:
        """Serialise all tiers to a dict (inverse of :meth:`from_snapshot`)."""
        from meshflow.intelligence.memory_backends import snapshot_from_memory
        return snapshot_from_memory(self)

    def from_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Restore all tiers from a *snapshot* dict (inverse of :meth:`to_snapshot`)."""
        from meshflow.intelligence.memory_backends import restore_memory
        restore_memory(self, snapshot)

    # ── Tier 5 — Entity memory ────────────────────────────────────────────────

    @property
    def entity(self) -> Any:
        """Tier 5 entity memory (lazy-initialised in-process store)."""
        if not hasattr(self, "_entity"):
            from meshflow.intelligence.entity_memory import EntityMemory
            self._entity = EntityMemory()
        return self._entity

    def remember_entity(self, entity: str, fact_key: str, fact_value: str) -> None:
        """Store a structured fact about a named entity."""
        self.entity.remember(entity, fact_key, fact_value)

    def recall_entity(self, entity: str) -> dict[str, str]:
        """Return all known facts for *entity*."""
        return self.entity.recall_entity(entity)

    def context_string(self, max_chars: int = 800, query: str = "") -> str:
        """Format working + episodic memories as an LLM context block.

        When *query* is supplied, relevant entity facts are appended under
        ``[Entities]``.  Output is capped to *max_chars*.
        """
        if not self._enabled:
            return ""

        # Build working-memory string (newest-first iteration → join oldest-first)
        working_parts: list[str] = []
        total = 0
        for item in reversed(self._working):
            entry = f"[step {item.metadata.get('step', '?')}] {item.content}"
            if total + len(entry) > max_chars:
                break
            working_parts.append(entry)
            total += len(entry)

        mem_str = "\n".join(reversed(working_parts)) if working_parts else ""

        # Append entity context when a query is provided
        if query and hasattr(self, "_entity"):
            relevant = self._entity.entities_in_text(query)
            if relevant:
                entity_str = self._entity.to_context_string(
                    relevant, max_chars=max(100, max_chars - total)
                )
                if entity_str:
                    suffix = f"[Entities]\n{entity_str}"
                    mem_str = f"{mem_str}\n{suffix}" if mem_str else suffix

        return mem_str
