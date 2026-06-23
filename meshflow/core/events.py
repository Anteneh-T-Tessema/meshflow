"""WorkflowEventBus — structured async event stream for workflow visibility.

Every node transition emits a typed WorkflowEvent so that:
  - meshflow watch  can tail a running workflow in real time
  - SSE / WebSocket endpoints can push events to frontends
  - Webhook sinks can forward events to external systems
  - Tests can assert on the exact sequence of events

Usage::

    bus = WorkflowEventBus()

    # Emit from workflow engine (internal):
    await bus.emit(WorkflowEvent(EventKind.STEP_START, run_id="abc", node_id="planner"))

    # Subscribe from CLI / dashboard:
    async for event in bus.subscribe(run_id="abc"):
        print(event.to_sse())
        if event.kind == EventKind.WORKFLOW_COMPLETE:
            break

    # Tap a one-shot list of events (useful for tests):
    events = await bus.collect(run_id="abc", until=EventKind.WORKFLOW_COMPLETE)
"""

from __future__ import annotations

import asyncio
import collections
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator



class EventKind(str, Enum):
    WORKFLOW_START    = "workflow_start"
    WORKFLOW_COMPLETE = "workflow_complete"
    WORKFLOW_FAILED   = "workflow_failed"
    STEP_START        = "step_start"
    STEP_COMPLETE     = "step_complete"
    STEP_BLOCKED      = "step_blocked"
    STEP_PAUSED       = "step_paused"
    STEP_SKIPPED      = "step_skipped"
    CHECKPOINT_SAVED  = "checkpoint_saved"
    HITL_REQUIRED     = "hitl_required"
    HITL_APPROVED     = "hitl_approved"
    BUDGET_WARNING    = "budget_warning"


@dataclass
class WorkflowEvent:
    """A single lifecycle event emitted by the workflow engine."""

    kind: EventKind
    run_id: str
    timestamp: float = field(default_factory=time.time)
    node_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """Format as a Server-Sent Event string."""
        payload = json.dumps({
            "kind": self.kind.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "node_id": self.node_id,
            "data": self.data,
        })
        return f"event: {self.kind.value}\ndata: {payload}\n\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "node_id": self.node_id,
            "data": self.data,
        }


class WorkflowEventBus:
    """Async publish-subscribe event bus for workflow lifecycle events.

    Producers call ``emit()``; consumers use ``subscribe()`` (async generator)
    or ``collect()`` (gather-until). Multiple concurrent subscribers are
    supported; each gets a copy of every event. Late subscribers can replay
    all events emitted so far via ``replay_history=True`` (default).

    The bus is safe to share across coroutines within the same event loop.
    Cross-process event delivery requires an external broker (Redis, NATS,
    etc.) — use the webhook / SSE adapter for that use case.
    """

    def __init__(self, maxsize: int = 2000) -> None:
        self._queues: list[asyncio.Queue[WorkflowEvent | None]] = []
        self._history: collections.deque[WorkflowEvent] = collections.deque(maxlen=maxsize)
        self._maxsize = maxsize
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        """Clear all event history."""
        self._history.clear()


    async def emit(self, event: WorkflowEvent) -> None:
        """Publish an event to all current subscribers."""
        async with self._lock:
            self._history.append(event)
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow subscriber — drop rather than block

    async def subscribe(
        self,
        run_id: str | None = None,
        replay_history: bool = True,
    ) -> AsyncIterator[WorkflowEvent]:
        """Async generator yielding events, optionally filtered by run_id.

        Replay past events then stream live events until ``close()`` is
        called or a caller breaks out of the loop.
        """
        q: asyncio.Queue[WorkflowEvent | None] = asyncio.Queue(maxsize=self._maxsize)
        self._queues.append(q)
        try:
            if replay_history:
                for event in list(self._history):
                    if run_id is None or event.run_id == run_id:
                        yield event
            while True:
                evt = await q.get()
                if evt is None:
                    break
                if run_id is None or evt.run_id == run_id:
                    yield evt

        finally:
            if q in self._queues:
                self._queues.remove(q)

    async def collect(
        self,
        run_id: str | None = None,
        until: EventKind | None = None,
        timeout: float = 30.0,
    ) -> list[WorkflowEvent]:
        """Collect events until the terminal ``until`` kind or ``timeout`` seconds.

        Useful in tests::

            events = await bus.collect(run_id=run_id, until=EventKind.WORKFLOW_COMPLETE)
            assert any(e.kind == EventKind.STEP_COMPLETE for e in events)
        """
        collected: list[WorkflowEvent] = []
        try:
            async with asyncio.timeout(timeout):
                async for event in self.subscribe(run_id=run_id, replay_history=True):
                    collected.append(event)
                    if until is not None and event.kind == until:
                        break
        except TimeoutError:
            pass
        return collected

    async def close(self) -> None:
        """Signal all active subscribers to stop."""
        for q in list(self._queues):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def history(self, run_id: str | None = None) -> list[WorkflowEvent]:
        """Return all events emitted so far, optionally filtered by run_id."""
        if run_id is None:
            return list(self._history)
        return [e for e in self._history if e.run_id == run_id]

    def __len__(self) -> int:
        return len(self._history)


# Process-wide default bus — used by meshflow watch and the runtime server.
# Workflows that don't pass an explicit bus use this one.
global_event_bus: WorkflowEventBus = WorkflowEventBus()
