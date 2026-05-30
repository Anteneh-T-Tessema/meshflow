"""RunStreamHub — global per-run event hub for WebSocket subscriptions.

The hub lets the runtime server publish :class:`~meshflow.core.streaming.StreamChunk`
events for a run, and lets WebSocket clients subscribe to receive them in
real-time.

Architecture::

    StepRuntime / Team.stream()
           │  publish(run_id, chunk)
           ▼
      RunStreamHub (singleton)
           │  fan-out to subscriber queues
           ▼
    BackpressureQueue  ×  BackpressureQueue  ×  ...
           │                    │
    WS client A          WS client B

Usage (server side)::

    hub = get_run_hub()
    hub.publish("run-123", StreamChunk(kind="token", content="Hello"))

Usage (WebSocket handler)::

    hub = get_run_hub()
    async for chunk in hub.subscribe("run-123"):
        await ws.send_str(chunk.to_json())
    hub.unsubscribe("run-123", sub_id)

Usage (from agent / Team)::

    from meshflow.streaming.run_hub import get_run_hub

    hub = get_run_hub()
    async for chunk in team.stream("Build a rate limiter"):
        hub.publish(run_id, chunk)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator

from meshflow.streaming.backpressure import BackpressureQueue, BackpressureStrategy


_SENTINEL = object()


class RunStreamHub:
    """Thread-safe, asyncio-native event hub for per-run streaming.

    Parameters
    ----------
    max_per_subscriber:
        Max queue depth per WebSocket connection.
    strategy:
        Backpressure strategy (default: ``DROP_OLDEST`` — live UIs prefer
        fresh tokens over stale ones).
    max_run_age_s:
        Seconds after which an idle run's subscriptions are cleaned up.
    """

    def __init__(
        self,
        max_per_subscriber: int = 512,
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
        max_run_age_s: float = 3600,
    ) -> None:
        self._max = max_per_subscriber
        self._strategy = strategy
        self._max_age = max_run_age_s
        # run_id → {sub_id → BackpressureQueue}
        self._runs: dict[str, dict[str, BackpressureQueue]] = {}
        self._lock = asyncio.Lock()

    # ── Publisher API (called by runtime / agents) ────────────────────────────

    def publish(self, run_id: str, chunk: Any) -> int:
        """Publish *chunk* to all subscribers for *run_id*.

        Non-blocking — uses ``put_nowait`` with backpressure.
        Returns the number of active subscribers.
        """
        subs = self._runs.get(run_id, {})
        delivered = 0
        for q in list(subs.values()):
            q.put_nowait(chunk)
            delivered += 1
        return delivered

    async def publish_async(self, run_id: str, chunk: Any) -> int:
        """Async version of :meth:`publish` — awaits on BLOCK strategy."""
        async with self._lock:
            subs = dict(self._runs.get(run_id, {}))
        delivered = 0
        for q in subs.values():
            await q.put(chunk)
            delivered += 1
        return delivered

    async def finish(self, run_id: str) -> None:
        """Signal end-of-run to all subscribers for *run_id*."""
        async with self._lock:
            subs = dict(self._runs.get(run_id, {}))
        for q in subs.values():
            await q.close()

    # ── Subscriber API (called by WebSocket handlers) ─────────────────────────

    async def subscribe(
        self,
        run_id: str,
        subscriber_id: str = "",
        max_size: int | None = None,
    ) -> tuple[str, AsyncIterator[Any]]:
        """Register a new subscriber for *run_id*.

        Returns ``(subscriber_id, async_iterator)`` where the iterator yields
        :class:`~meshflow.core.streaming.StreamChunk` objects.
        """
        sid = subscriber_id or str(uuid.uuid4())[:12]
        q = BackpressureQueue(
            max_size=max_size if max_size is not None else self._max,
            strategy=self._strategy,
        )
        async with self._lock:
            if run_id not in self._runs:
                self._runs[run_id] = {}
            self._runs[run_id][sid] = q
        return sid, q._iter()

    async def unsubscribe(self, run_id: str, subscriber_id: str) -> None:
        """Remove a subscriber and close its queue."""
        async with self._lock:
            run_subs = self._runs.get(run_id, {})
            q = run_subs.pop(subscriber_id, None)
            if not run_subs:
                self._runs.pop(run_id, None)
        if q is not None:
            await q.close()

    def subscriber_count(self, run_id: str | None = None) -> int:
        """Return subscriber count for *run_id* (or total across all runs)."""
        if run_id is not None:
            return len(self._runs.get(run_id, {}))
        return sum(len(subs) for subs in self._runs.values())

    def active_runs(self) -> list[str]:
        return list(self._runs.keys())

    async def cleanup_run(self, run_id: str) -> None:
        """Close all subscribers for *run_id* and remove it from the hub."""
        await self.finish(run_id)
        async with self._lock:
            self._runs.pop(run_id, None)

    def stats(self) -> dict[str, Any]:
        return {
            "active_runs": len(self._runs),
            "total_subscribers": self.subscriber_count(),
            "runs": {rid: len(subs) for rid, subs in self._runs.items()},
        }


# ── Singleton access ──────────────────────────────────────────────────────────

_hub: RunStreamHub | None = None


def get_run_hub() -> RunStreamHub:
    """Return the process-level RunStreamHub singleton."""
    global _hub
    if _hub is None:
        _hub = RunStreamHub()
    return _hub


def reset_run_hub() -> None:
    """Reset the singleton (for tests)."""
    global _hub
    _hub = None
