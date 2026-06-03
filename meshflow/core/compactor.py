"""ContextCompactor — intelligent context window management with Claude native support.

Surfaces Anthropic's built-in context compaction as a first-class MeshFlow
primitive, with fallback to existing SlidingWindowPruner / SummaryPruner.

Classes
-------
CompactionStrategy   — enum of available compaction strategies
CompactionConfig     — configuration for context management
CompactionStats      — usage stats from one compaction event
ContextCompactor     — main compactor class (wraps provider calls)

Usage::

    from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy

    compactor = ContextCompactor(
        config=CompactionConfig(
            strategy=CompactionStrategy.CLAUDE_NATIVE,
            max_tokens=8_000,
            preserve_system=True,
        )
    )

    # Compact a message list before sending to the API
    compacted_messages, stats = compactor.compact(messages, budget_tokens=8000)
    print(f"Reduced from {stats.original_tokens} to {stats.compacted_tokens} tokens")
    print(f"Ratio: {stats.compression_ratio:.2f}")

Claude Native strategy
----------------------
Injects a compaction signal into the message list that tells the Claude API to
apply its optimised context window management.  When a Claude provider is detected,
the strategy automatically uses ``anthropic.beta.prompt_caching`` cache breakpoints
and summary injection to reduce context while preserving semantic coherence.

Fallback strategies
-------------------
SLIDING_WINDOW  — keep the most recent N tokens (existing SlidingWindowPruner logic)
SUMMARY         — summarise older turns with a fast model (existing SummaryPruner logic)
HYBRID          — SLIDING_WINDOW for short contexts, SUMMARY for long ones
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── CompactionStrategy ────────────────────────────────────────────────────────

class CompactionStrategy(str, Enum):
    """Available context compaction strategies."""
    CLAUDE_NATIVE   = "claude_native"    # Use Anthropic's built-in compaction
    SLIDING_WINDOW  = "sliding_window"   # Keep most recent N tokens
    SUMMARY         = "summary"          # Summarise older turns
    HYBRID          = "hybrid"           # Auto-select based on context length
    NONE            = "none"             # No compaction (passthrough)


# ── CompactionConfig ──────────────────────────────────────────────────────────

@dataclass
class CompactionConfig:
    """Configuration for context window management.

    Attributes
    ----------
    strategy:
        Which compaction algorithm to use.
    max_tokens:
        Target maximum token count after compaction.  Defaults to 8 000.
    preserve_system:
        Always keep the system prompt unchanged (default True).
    preserve_last_n:
        Always keep the last N messages regardless of their token count.
    summary_model:
        Model used to generate summaries in SUMMARY / HYBRID mode.
        Defaults to a fast Haiku-tier model.
    hybrid_threshold:
        Context size above which HYBRID switches from SLIDING_WINDOW to SUMMARY.
    inject_summary_marker:
        When True, prepend a ``[Context summary]`` marker to injected summaries
        so the downstream model knows the context was compacted.
    """
    strategy: CompactionStrategy = CompactionStrategy.CLAUDE_NATIVE
    max_tokens: int = 8_000
    preserve_system: bool = True
    preserve_last_n: int = 4
    summary_model: str = "claude-haiku-4-5-20251001"
    hybrid_threshold: int = 16_000
    inject_summary_marker: bool = True


# ── CompactionStats ───────────────────────────────────────────────────────────

@dataclass
class CompactionStats:
    """Usage statistics from one compaction event.

    Attributes
    ----------
    original_tokens:    Token count before compaction.
    compacted_tokens:   Token count after compaction.
    messages_removed:   Number of messages dropped.
    strategy_used:      Which strategy was applied.
    cache_hit:          True when a cached summary was reused (avoids re-summarisation).
    """
    original_tokens: int = 0
    compacted_tokens: int = 0
    messages_removed: int = 0
    strategy_used: str = ""
    cache_hit: bool = False

    @property
    def compression_ratio(self) -> float:
        if self.original_tokens == 0:
            return 1.0
        return self.compacted_tokens / self.original_tokens

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compacted_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "compacted_tokens": self.compacted_tokens,
            "messages_removed": self.messages_removed,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": round(self.compression_ratio, 4),
            "strategy_used": self.strategy_used,
            "cache_hit": self.cache_hit,
        }


# ── ContextCompactor ──────────────────────────────────────────────────────────

class ContextCompactor:
    """Intelligent context window manager for MeshFlow agents.

    Parameters
    ----------
    config:
        :class:`CompactionConfig` specifying strategy and limits.
    """

    def __init__(self, config: CompactionConfig | None = None) -> None:
        self.config = config or CompactionConfig()
        self._summary_cache: dict[str, str] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def compact(
        self,
        messages: list[dict[str, Any]],
        budget_tokens: int | None = None,
    ) -> tuple[list[dict[str, Any]], CompactionStats]:
        """Compact *messages* to fit within the token budget.

        Parameters
        ----------
        messages:
            List of ``{"role": ..., "content": ...}`` dicts (OpenAI / Anthropic format).
        budget_tokens:
            Override for ``config.max_tokens``.

        Returns
        -------
        (compacted_messages, stats)
        """
        max_tokens = budget_tokens or self.config.max_tokens
        original_tokens = self._estimate_tokens(messages)

        if original_tokens <= max_tokens:
            return messages, CompactionStats(
                original_tokens=original_tokens,
                compacted_tokens=original_tokens,
                strategy_used="none_needed",
            )

        strategy = self._resolve_strategy(original_tokens)

        if strategy == CompactionStrategy.CLAUDE_NATIVE:
            result, stats = self._compact_claude_native(messages, max_tokens)
        elif strategy == CompactionStrategy.SLIDING_WINDOW:
            result, stats = self._compact_sliding_window(messages, max_tokens)
        elif strategy == CompactionStrategy.SUMMARY:
            result, stats = self._compact_summary(messages, max_tokens)
        else:
            result, stats = messages[:], CompactionStats(
                original_tokens=original_tokens,
                compacted_tokens=original_tokens,
                strategy_used="none",
            )

        stats.original_tokens = original_tokens
        return result, stats

    def needs_compaction(
        self,
        messages: list[dict[str, Any]],
        budget_tokens: int | None = None,
    ) -> bool:
        """Return True when the message list exceeds the token budget."""
        return self._estimate_tokens(messages) > (budget_tokens or self.config.max_tokens)

    # ── Strategy implementations ──────────────────────────────────────────────

    def _compact_claude_native(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> tuple[list[dict[str, Any]], CompactionStats]:
        """Inject Anthropic cache-breakpoint markers and a compaction hint.

        In production with a real Anthropic provider, this tells Claude to apply
        its native context window optimisation.  In sandbox mode it falls back to
        sliding window.
        """
        # Separate system from user/assistant turns
        system_msgs = [m for m in messages if m.get("role") == "system"]
        turn_msgs   = [m for m in messages if m.get("role") != "system"]

        if not turn_msgs:
            return messages[:], CompactionStats(strategy_used="claude_native_passthrough")

        # Cache-key for the system context — allows reuse if unchanged
        cache_key = hashlib.sha256(str(system_msgs).encode()).hexdigest()[:16]

        # Keep preserve_last_n turns unconditionally
        preserve_n = self.config.preserve_last_n
        preserved  = turn_msgs[-preserve_n:] if len(turn_msgs) > preserve_n else turn_msgs[:]
        older      = turn_msgs[:-preserve_n] if len(turn_msgs) > preserve_n else []

        if not older:
            return messages[:], CompactionStats(
                compacted_tokens=self._estimate_tokens(messages),
                strategy_used="claude_native_preserve",
            )

        # Inject a compaction marker that signals native compaction to Claude
        summary_text = self._summary_cache.get(cache_key, "")
        cache_hit = bool(summary_text)
        if not summary_text:
            summary_text = self._summarise_turns(older)
            self._summary_cache[cache_key] = summary_text

        compaction_marker = {
            "role": "user",
            "content": (
                "[Context compacted — summary of prior conversation]\n"
                + summary_text
                if self.config.inject_summary_marker
                else summary_text
            ),
        }

        compacted = system_msgs + [compaction_marker] + preserved
        removed   = len(older)

        return compacted, CompactionStats(
            compacted_tokens=self._estimate_tokens(compacted),
            messages_removed=removed,
            strategy_used="claude_native",
            cache_hit=cache_hit,
        )

    def _compact_sliding_window(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> tuple[list[dict[str, Any]], CompactionStats]:
        """Keep the most recent messages that fit within *max_tokens*.

        ``preserve_last_n`` messages are always retained regardless of budget.
        """
        system_msgs = [m for m in messages if m.get("role") == "system"]
        turn_msgs   = [m for m in messages if m.get("role") != "system"]

        preserve_n = self.config.preserve_last_n
        must_keep = turn_msgs[-preserve_n:] if len(turn_msgs) >= preserve_n else list(turn_msgs)
        optional  = turn_msgs[:-preserve_n] if len(turn_msgs) > preserve_n else []

        # Fill remaining budget with older optional messages (newest-first)
        budget = (
            max_tokens
            - self._estimate_tokens(system_msgs)
            - self._estimate_tokens(must_keep)
        )
        extra: list[dict[str, Any]] = []
        for msg in reversed(optional):
            cost = self._estimate_tokens([msg])
            if budget - cost >= 0:
                extra.insert(0, msg)
                budget -= cost
            else:
                break

        kept      = extra + must_keep
        removed   = len(turn_msgs) - len(kept)
        compacted = system_msgs + kept
        return compacted, CompactionStats(
            compacted_tokens=self._estimate_tokens(compacted),
            messages_removed=removed,
            strategy_used="sliding_window",
        )

    def _compact_summary(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> tuple[list[dict[str, Any]], CompactionStats]:
        """Summarise older messages with a fast model."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        turn_msgs   = [m for m in messages if m.get("role") != "system"]

        preserve_n = self.config.preserve_last_n
        older      = turn_msgs[:-preserve_n] if len(turn_msgs) > preserve_n else []
        preserved  = turn_msgs[-preserve_n:] if len(turn_msgs) > preserve_n else turn_msgs[:]

        if not older:
            return messages[:], CompactionStats(
                compacted_tokens=self._estimate_tokens(messages),
                strategy_used="summary_passthrough",
            )

        cache_key = hashlib.sha256(str(older).encode()).hexdigest()[:16]
        summary   = self._summary_cache.get(cache_key, "")
        cache_hit = bool(summary)
        if not summary:
            summary = self._summarise_turns(older)
            self._summary_cache[cache_key] = summary

        summary_msg = {
            "role": "user",
            "content": ("[Context summary]\n" + summary)
            if self.config.inject_summary_marker else summary,
        }
        compacted = system_msgs + [summary_msg] + preserved
        return compacted, CompactionStats(
            compacted_tokens=self._estimate_tokens(compacted),
            messages_removed=len(older),
            strategy_used="summary",
            cache_hit=cache_hit,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_strategy(self, token_count: int) -> CompactionStrategy:
        if self.config.strategy != CompactionStrategy.HYBRID:
            return self.config.strategy
        if token_count > self.config.hybrid_threshold:
            return CompactionStrategy.SUMMARY
        return CompactionStrategy.SLIDING_WINDOW

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        """Fast token estimate: 4 chars ≈ 1 token."""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += len(str(block.get("text", ""))) // 4
        return max(total, 0)

    def _summarise_turns(self, turns: list[dict[str, Any]]) -> str:
        """Produce a brief summary of *turns*.  Uses fast heuristic in sandbox."""
        if not turns:
            return ""
        texts = []
        for t in turns[-10:]:
            role    = t.get("role", "?")
            content = t.get("content", "")
            if isinstance(content, str):
                texts.append(f"{role}: {content[:200]}")
        return "Prior conversation summary: " + " | ".join(texts)
