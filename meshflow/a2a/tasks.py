"""A2A task state machine — full async task lifecycle.

States::

    submitted ──► working ──► completed
                          └──► failed
              └──► input_required ──► working ──► completed
                                              └──► failed

Wire format (``to_dict()``)::

    {
        "task_id": "abc123",
        "state":   "working",
        "content": "What is HIPAA?",
        "result":  "",
        "error":   "",
        "agent_name": "compliance-agent",
        "tokens":  0,
        "cost_usd": 0.0,
        "created_at": 1234567890.1,
        "updated_at": 1234567890.5,
    }
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


# ── State machine ──────────────────────────────────────────────────────────────

class TaskState(str, Enum):
    submitted      = "submitted"
    working        = "working"
    input_required = "input_required"
    completed      = "completed"
    failed         = "failed"


# ── Task ───────────────────────────────────────────────────────────────────────

@dataclass
class A2ATask:
    """A single task managed by the A2A server."""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    content: str = ""
    state: TaskState = TaskState.submitted
    result: str = ""
    error: str = ""
    agent_name: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)

    def is_terminal(self) -> bool:
        return self.state in (TaskState.completed, TaskState.failed)

    def transition(self, new_state: TaskState) -> None:
        self.state = new_state
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id":    self.task_id,
            "state":      self.state.value,
            "content":    self.content,
            "result":     self.result,
            "error":      self.error,
            "agent_name": self.agent_name,
            "tokens":     self.tokens,
            "cost_usd":   self.cost_usd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "A2ATask":
        return cls(
            task_id=d.get("task_id", ""),
            content=d.get("content", ""),
            state=TaskState(d.get("state", "submitted")),
            result=d.get("result", ""),
            error=d.get("error", ""),
            agent_name=d.get("agent_name", ""),
            tokens=d.get("tokens", 0),
            cost_usd=d.get("cost_usd", 0.0),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


# ── Store ──────────────────────────────────────────────────────────────────────

class A2ATaskStore:
    """Thread-safe in-memory store for A2A tasks with SSE listener support."""

    def __init__(self) -> None:
        self._tasks: dict[str, A2ATask] = {}
        self._lock = threading.Lock()
        # task_id → list of Queue[A2ATask | None]
        self._listeners: dict[str, list[queue.Queue]] = {}

    def put(self, task: A2ATask) -> None:
        """Upsert task and notify all SSE listeners."""
        with self._lock:
            self._tasks[task.task_id] = task
        self._notify(task)

    def get(self, task_id: str) -> A2ATask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, limit: int = 50) -> list[A2ATask]:
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True,
            )
        return tasks[:limit]

    # ── SSE subscription ──────────────────────────────────────────────────────

    def subscribe(self, task_id: str) -> "TaskEventQueue":
        """Return an event queue that receives every state update for *task_id*."""
        q: queue.Queue[A2ATask | None] = queue.Queue(maxsize=64)
        with self._lock:
            self._listeners.setdefault(task_id, []).append(q)
        return TaskEventQueue(q, task_id, self)

    def unsubscribe(self, task_id: str, q: queue.Queue) -> None:
        with self._lock:
            listeners = self._listeners.get(task_id, [])
            try:
                listeners.remove(q)
            except ValueError:
                pass

    def _notify(self, task: A2ATask) -> None:
        with self._lock:
            listeners = list(self._listeners.get(task.task_id, []))
        for q in listeners:
            try:
                q.put_nowait(task)
            except queue.Full:
                pass


class TaskEventQueue:
    """Blocking iterator over task state-change events (for SSE streaming)."""

    def __init__(
        self,
        q: "queue.Queue[A2ATask | None]",
        task_id: str,
        store: A2ATaskStore,
    ) -> None:
        self._q = q
        self._task_id = task_id
        self._store = store

    def next_event(self, timeout: float = 30.0) -> A2ATask | None:
        """Block until next event or timeout.  Returns ``None`` on timeout."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def iter_until_done(self, poll_timeout: float = 1.0) -> Iterator[A2ATask]:
        """Yield tasks until a terminal state is reached."""
        while True:
            task = self.next_event(timeout=poll_timeout)
            if task is None:
                # timeout — check if already terminal via store
                current = self._store.get(self._task_id)
                if current and current.is_terminal():
                    break
                continue
            yield task
            if task.is_terminal():
                break

    def close(self) -> None:
        self._store.unsubscribe(self._task_id, self._q)


__all__ = ["TaskState", "A2ATask", "A2ATaskStore", "TaskEventQueue"]
