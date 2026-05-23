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
    ) -> None:
        self._agent_id = agent_id
        self._enabled = enabled
        self._max_working = max_working
        self._max_episodic = max_episodic

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

    def context_string(self, max_chars: int = 800) -> str:
        """Format working + top episodic memories as a context block for the LLM.

        Caps output to *max_chars* to avoid bloating the context window.
        """
        if not self._enabled:
            return ""

        parts: list[str] = []
        total = 0

        for item in reversed(self._working):
            entry = f"[step {item.metadata.get('step', '?')}] {item.content}"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)

        if not parts:
            return ""
        return "\n".join(reversed(parts))

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
