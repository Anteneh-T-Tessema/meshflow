"""Backpressure-aware async queue for streaming consumers.

Prevents slow consumers from stalling producers or consuming unbounded memory.
Three strategies:

- ``DROP_OLDEST``  — discard the oldest item when the queue is full (default).
  Best for live UIs where stale frames are worthless.
- ``DROP_NEWEST``  — discard the incoming item when the queue is full.
  Best when historical context matters more than recency.
- ``BLOCK``        — await until space is available.
  Best when every item must be delivered (audit / compliance streams).

Usage::

    from meshflow.streaming.backpressure import BackpressureQueue, BackpressureStrategy

    q = BackpressureQueue(max_size=100, strategy=BackpressureStrategy.DROP_OLDEST)

    # Producer:
    await q.put(chunk)

    # Consumer:
    async for chunk in q:
        process(chunk)
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, AsyncIterator


class BackpressureStrategy(str, Enum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    BLOCK = "block"


_SENTINEL = object()


class BackpressureQueue:
    """Async queue with configurable backpressure handling.

    Parameters
    ----------
    max_size:
        Maximum number of items to hold.  When full, the chosen *strategy*
        is applied.  0 = unlimited (no backpressure).
    strategy:
        What to do when the queue is full.
    """

    def __init__(
        self,
        max_size: int = 256,
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
    ) -> None:
        self._max = max_size
        self._strategy = strategy
        self._q: asyncio.Queue[Any] = asyncio.Queue(maxsize=0)  # unbounded internally
        self._dropped = 0
        self._total_put = 0
        self._closed = False

    # ── Producer side ──────────────────────────────────────────────────────────

    async def put(self, item: Any) -> bool:
        """Enqueue *item*, applying the backpressure strategy if full.

        Returns True if the item was accepted, False if it was dropped.
        """
        if self._closed:
            return False

        self._total_put += 1

        if self._max > 0 and self._q.qsize() >= self._max:
            if self._strategy == BackpressureStrategy.DROP_NEWEST:
                self._dropped += 1
                return False
            elif self._strategy == BackpressureStrategy.DROP_OLDEST:
                try:
                    self._q.get_nowait()  # discard oldest
                except asyncio.QueueEmpty:
                    pass
                self._dropped += 1
            else:  # BLOCK
                await self._q.put(item)
                return True

        await self._q.put(item)
        return True

    def put_nowait(self, item: Any) -> bool:
        """Non-blocking put. Returns True if accepted."""
        if self._closed:
            return False
        if self._max > 0 and self._q.qsize() >= self._max:
            if self._strategy == BackpressureStrategy.DROP_NEWEST:
                self._dropped += 1
                return False
            elif self._strategy == BackpressureStrategy.DROP_OLDEST:
                try:
                    self._q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._dropped += 1
        self._q.put_nowait(item)
        return True

    async def close(self) -> None:
        """Signal end-of-stream to any consumers."""
        self._closed = True
        await self._q.put(_SENTINEL)

    # ── Consumer side ──────────────────────────────────────────────────────────

    async def get(self) -> Any:
        """Wait for the next item. Returns the sentinel when closed."""
        return await self._q.get()

    def get_nowait(self) -> Any:
        return self._q.get_nowait()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Any]:
        while True:
            item = await self._q.get()
            if item is _SENTINEL:
                break
            yield item

    # ── Stats ──────────────────────────────────────────────────────────────────

    @property
    def qsize(self) -> int:
        return self._q.qsize()

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def total_put(self) -> int:
        return self._total_put

    @property
    def drop_rate(self) -> float:
        if self._total_put == 0:
            return 0.0
        return round(self._dropped / self._total_put, 4)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def stats(self) -> dict[str, Any]:
        return {
            "qsize": self.qsize,
            "max_size": self._max,
            "strategy": self._strategy.value,
            "dropped": self._dropped,
            "total_put": self._total_put,
            "drop_rate": self.drop_rate,
            "closed": self._closed,
        }
