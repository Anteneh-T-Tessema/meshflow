"""StreamMultiplexer — fan one async generator to N independent consumers.

Each consumer gets its own :class:`~meshflow.streaming.backpressure.BackpressureQueue`
so one slow consumer never blocks another.

Usage::

    from meshflow.streaming.multiplexer import StreamMultiplexer

    mux = StreamMultiplexer(source_gen, max_per_consumer=128)
    await mux.start()

    sub1 = mux.subscribe()
    sub2 = mux.subscribe()

    async for chunk in sub1:
        ...

    await mux.stop()
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator

from meshflow.streaming.backpressure import BackpressureQueue, BackpressureStrategy


class StreamMultiplexer:
    """Fan out one async generator to N independently-consumed queues.

    Parameters
    ----------
    source:
        An async generator (or any async iterable) producing items.
    max_per_consumer:
        Max queue depth per subscriber.  Older items are dropped for slow
        consumers (``DROP_OLDEST`` strategy by default).
    strategy:
        Backpressure strategy applied to each consumer queue.
    """

    def __init__(
        self,
        source: AsyncIterator[Any],
        max_per_consumer: int = 256,
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
    ) -> None:
        self._source = source
        self._max = max_per_consumer
        self._strategy = strategy
        self._subscribers: dict[str, BackpressureQueue] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._running = False
        self._items_produced = 0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> "StreamMultiplexer":
        """Start pumping items from *source* into subscriber queues."""
        self._running = True
        self._task = asyncio.create_task(self._pump())
        return self

    async def stop(self) -> None:
        """Stop the pump and close all subscriber queues."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for q in self._subscribers.values():
                await q.close()

    async def __aenter__(self) -> "StreamMultiplexer":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(
        self,
        subscriber_id: str = "",
        max_size: int | None = None,
    ) -> "Subscription":
        """Register a new consumer and return a :class:`Subscription`.

        Parameters
        ----------
        subscriber_id:  Optional label for debugging.
        max_size:       Override per-consumer queue depth.
        """
        sid = subscriber_id or str(uuid.uuid4())[:8]
        q = BackpressureQueue(
            max_size=max_size if max_size is not None else self._max,
            strategy=self._strategy,
        )
        self._subscribers[sid] = q
        return Subscription(sid, q, self)

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a consumer by ID."""
        self._subscribers.pop(subscriber_id, None)

    # ── Internals ──────────────────────────────────────────────────────────────

    async def _pump(self) -> None:
        try:
            async for item in self._source:
                if not self._running:
                    break
                self._items_produced += 1
                async with self._lock:
                    subs = list(self._subscribers.values())
                for q in subs:
                    await q.put(item)
        finally:
            async with self._lock:
                for q in self._subscribers.values():
                    await q.close()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def items_produced(self) -> int:
        return self._items_produced

    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "subscribers": self.subscriber_count,
            "items_produced": self._items_produced,
            "queues": {
                sid: q.stats() for sid, q in self._subscribers.items()
            },
        }


class Subscription:
    """A single consumer's view of a multiplexed stream."""

    def __init__(
        self,
        subscriber_id: str,
        queue: BackpressureQueue,
        mux: StreamMultiplexer,
    ) -> None:
        self._id = subscriber_id
        self._q = queue
        self._mux = mux

    @property
    def subscriber_id(self) -> str:
        return self._id

    @property
    def dropped(self) -> int:
        return self._q.dropped

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._q._iter()

    async def cancel(self) -> None:
        """Unregister this subscription and close its queue."""
        self._mux.unsubscribe(self._id)
        await self._q.close()
