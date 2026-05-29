"""Parallel context deduplication — remove redundant content across agents.

When multiple agents in a parallel workflow branch are all given the same
large context block, they duplicate input tokens and cost.  This module
provides fingerprint-based dedup: any content block that was already seen
in this run is stripped from subsequent agents' context.

Usage (programmatic)::

    from meshflow.agents.context_dedup import ContextDeduplicator

    dedup = ContextDeduplicator()

    ctx_a = {"shared_docs": "...", "unique_a": "agent A only"}
    ctx_b = {"shared_docs": "...", "unique_b": "agent B only"}

    clean_a = dedup.deduplicate(ctx_a, agent_name="a")
    clean_b = dedup.deduplicate(ctx_b, agent_name="b")
    # clean_b["shared_docs"] is omitted — already seen in agent A

Usage (workflow integration)::

    In WorkflowDefinition.run(), context is snapshotted before each parallel
    level.  Pass the snapshot through ContextDeduplicator before building
    NodeInput for parallel agents.  Each agent gets a fresh copy minus the
    content that all parallel siblings already received.

    dedup = ContextDeduplicator()
    for nd in level_nodes:
        clean_ctx = dedup.deduplicate(ctx_snapshot, agent_name=nd.id)
        NodeInput(task=task, context=clean_ctx, ...)
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any


class ContextDeduplicator:
    """Thread-safe fingerprint-based context deduplicator.

    Tracks which content fingerprints have been sent to any agent in a run.
    On each call, returns a version of the context with duplicate values
    replaced by short ``[deduplicated: <key>]`` placeholders.

    Parameters
    ----------
    hash_threshold:  Minimum value length (chars) to fingerprint.
                     Short strings (labels, IDs) are never deduplicated.
    """

    def __init__(self, hash_threshold: int = 100) -> None:
        self._seen: dict[str, str] = {}   # fingerprint → first agent_name that sent it
        self._lock = threading.Lock()
        self._threshold = hash_threshold

    def _fingerprint(self, value: Any) -> str | None:
        text = str(value)
        if len(text) < self._threshold:
            return None
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def deduplicate(
        self,
        context: dict[str, Any],
        agent_name: str = "",
        skip_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """Return a deduplicated copy of *context*.

        Keys in *skip_keys* are always forwarded as-is.
        Keys starting with ``_`` (internal) are never deduplicated.
        """
        skip = skip_keys or set()
        result: dict[str, Any] = {}
        with self._lock:
            for key, value in context.items():
                if key.startswith("_") or key in skip:
                    result[key] = value
                    continue
                fp = self._fingerprint(value)
                if fp is None:
                    result[key] = value
                    continue
                if fp in self._seen:
                    # Duplicate — replace with lightweight placeholder
                    result[key] = f"[deduplicated from {self._seen[fp]!r}]"
                else:
                    self._seen[fp] = agent_name or "unknown"
                    result[key] = value
        return result

    def seen_count(self) -> int:
        """Return the number of unique content fingerprints tracked."""
        with self._lock:
            return len(self._seen)

    def reset(self) -> None:
        """Clear all tracked fingerprints (e.g. between workflow runs)."""
        with self._lock:
            self._seen.clear()

    def savings_estimate(self, original_contexts: list[dict[str, Any]]) -> dict[str, Any]:
        """Estimate byte savings from deduplication.

        Runs dedup over *original_contexts* and returns stats without
        modifying internal state.
        """
        temp = ContextDeduplicator(self._threshold)
        total_orig = 0
        total_deduped = 0
        for i, ctx in enumerate(original_contexts):
            orig_len = sum(len(str(v)) for v in ctx.values())
            deduped = temp.deduplicate(ctx, agent_name=f"agent_{i}")
            ded_len = sum(len(str(v)) for v in deduped.values())
            total_orig += orig_len
            total_deduped += ded_len
        return {
            "original_bytes": total_orig,
            "deduped_bytes": total_deduped,
            "saved_bytes": total_orig - total_deduped,
            "reduction_pct": round((1 - total_deduped / max(total_orig, 1)) * 100, 1),
        }


__all__ = ["ContextDeduplicator"]
