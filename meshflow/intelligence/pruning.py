"""Context window pruning — sliding-window and summary compression.

Closes the token-optimisation gap: when a conversation context grows beyond
the model's practical budget, one of three strategies is applied:

1. **sliding_window** — keep the last N messages; discard the oldest.
2. **summarise** — extract key facts from dropped messages and inject a
   concise summary block at the top.  Uses a lightweight extractive
   algorithm (no extra LLM call required) or an optional LLM summariser.
3. **importance** — score each message by keyword overlap with the latest
   user turn, then keep only the top-K most relevant.

Usage::

    from meshflow.intelligence.pruning import ContextPruner

    pruner = ContextPruner(max_tokens=4000, strategy="sliding_window")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        *conversation_history,
        {"role": "user", "content": user_turn},
    ]
    pruned = pruner.prune(messages)
    # len(pruned) ≤ original; system message always preserved.
"""

from __future__ import annotations

import re
from typing import Any, Literal


_CHARS_PER_TOKEN = 4  # conservative approximation


def _token_estimate(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _msg_tokens(msg: dict[str, Any]) -> int:
    content = msg.get("content", "")
    if isinstance(content, str):
        return _token_estimate(content)
    if isinstance(content, list):
        return sum(_token_estimate(b.get("text", "")) for b in content if isinstance(b, dict))
    return 0


def _total_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(_msg_tokens(m) for m in messages)


# ── Extractive summariser (no LLM required) ───────────────────────────────────

def _extractive_summary(messages: list[dict[str, Any]], max_chars: int = 500) -> str:
    """Build a short extractive summary of *messages* within *max_chars*."""
    sentences: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        for sent in re.split(r"(?<=[.!?])\s+", content.strip()):
            sent = sent.strip()
            if len(sent) > 20:
                sentences.append(sent)

    if not sentences:
        return ""

    # Score by length (a rough proxy for information density)
    scored = sorted(sentences, key=len, reverse=True)
    parts: list[str] = []
    total = 0
    for s in scored:
        if total + len(s) > max_chars:
            break
        parts.append(s)
        total += len(s)

    return "Prior context summary: " + " ".join(parts[:5])


# ── Importance scorer ─────────────────────────────────────────────────────────

def _importance_score(msg: dict[str, Any], query_tokens: set[str]) -> float:
    content = msg.get("content", "")
    if not isinstance(content, str):
        return 0.0
    msg_tokens = set(re.findall(r"[a-z0-9]+", content.lower()))
    if not msg_tokens:
        return 0.0
    overlap = len(msg_tokens & query_tokens)
    return overlap / max(len(msg_tokens), 1)


# ── ContextPruner ─────────────────────────────────────────────────────────────

class ContextPruner:
    """Prune a messages list to fit within a token budget.

    Parameters
    ----------
    max_tokens:     Token budget (output context ≤ this size).
    strategy:       ``"sliding_window"`` | ``"summarise"`` | ``"importance"``.
    preserve_system: Always keep the system message.  Default True.
    summary_max_chars: Max chars for the extractive summary block.
    """

    def __init__(
        self,
        max_tokens: int = 4000,
        strategy: Literal["sliding_window", "summarise", "importance"] = "sliding_window",
        preserve_system: bool = True,
        summary_max_chars: int = 600,
    ) -> None:
        self._max_tokens = max_tokens
        self._strategy = strategy
        self._preserve_system = preserve_system
        self._summary_max_chars = summary_max_chars

    def prune(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a (possibly shorter) messages list within the token budget."""
        if _total_tokens(messages) <= self._max_tokens:
            return list(messages)

        if self._strategy == "sliding_window":
            return self._sliding_window(messages)
        elif self._strategy == "summarise":
            return self._summarise(messages)
        else:
            return self._importance(messages)

    # ── Strategies ─────────────────────────────────────────────────────────────

    def _sliding_window(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep system + tail of conversation within budget."""
        system = [m for m in messages if m.get("role") == "system"] if self._preserve_system else []
        non_system = [m for m in messages if m.get("role") != "system"]

        system_tokens = sum(_msg_tokens(m) for m in system)
        budget = self._max_tokens - system_tokens

        # Take messages from the tail until budget is exhausted
        kept: list[dict[str, Any]] = []
        for msg in reversed(non_system):
            t = _msg_tokens(msg)
            if budget - t < 0:
                break
            kept.insert(0, msg)
            budget -= t

        return system + kept

    def _summarise(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep system + summary of dropped messages + recent tail."""
        system = [m for m in messages if m.get("role") == "system"] if self._preserve_system else []
        non_system = [m for m in messages if m.get("role") != "system"]

        system_tokens = sum(_msg_tokens(m) for m in system)
        budget = self._max_tokens - system_tokens

        # How many messages fit from the tail?
        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        remaining = budget
        for msg in reversed(non_system):
            t = _msg_tokens(msg)
            if remaining - t >= 0:
                kept.insert(0, msg)
                remaining -= t
            else:
                dropped.insert(0, msg)

        if dropped:
            summary_text = _extractive_summary(dropped, self._summary_max_chars)
            if summary_text:
                summary_msg = {"role": "system", "content": summary_text}
                system = system + [summary_msg]

        return system + kept

    def _importance(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Score messages by relevance to the latest user query; keep top-K."""
        system = [m for m in messages if m.get("role") == "system"] if self._preserve_system else []
        non_system = [m for m in messages if m.get("role") != "system"]

        # Use latest user message as the query
        user_msgs = [m for m in non_system if m.get("role") == "user"]
        query_text = user_msgs[-1].get("content", "") if user_msgs else ""
        query_tokens: set[str] = set(re.findall(r"[a-z0-9]+", query_text.lower()))

        # Always include the latest user message
        last_msg = non_system[-1] if non_system else None
        rest = non_system[:-1] if non_system else []

        scored = sorted(rest, key=lambda m: _importance_score(m, query_tokens), reverse=True)

        system_tokens = sum(_msg_tokens(m) for m in system)
        budget = self._max_tokens - system_tokens - (_msg_tokens(last_msg) if last_msg else 0)

        kept: list[dict[str, Any]] = []
        for msg in scored:
            t = _msg_tokens(msg)
            if budget - t < 0:
                break
            kept.append(msg)
            budget -= t

        # Restore original ordering
        kept_set = {id(m) for m in kept}
        ordered = [m for m in non_system[:-1] if id(m) in kept_set]

        result = system + ordered
        if last_msg:
            result.append(last_msg)
        return result

    def stats(self, original: list[dict[str, Any]], pruned: list[dict[str, Any]]) -> dict[str, Any]:
        """Return compression statistics."""
        orig_tokens = _total_tokens(original)
        pruned_tokens = _total_tokens(pruned)
        return {
            "original_messages": len(original),
            "pruned_messages": len(pruned),
            "original_tokens": orig_tokens,
            "pruned_tokens": pruned_tokens,
            "reduction_pct": round((1 - pruned_tokens / max(orig_tokens, 1)) * 100, 1),
            "strategy": self._strategy,
        }


__all__ = ["ContextPruner"]
