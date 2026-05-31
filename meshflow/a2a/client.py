"""A2A HTTP client — synchronous and async, supports full task lifecycle."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Iterator

from .protocol import A2AMessage, A2AResponse, AgentCard
from .tasks import A2ATask


class A2AClient:
    """HTTP client for A2A agents.

    Zero external dependencies — stdlib urllib only.

    Parameters
    ----------
    url:       Base URL of the remote A2A server (e.g. ``http://localhost:8080``).
    timeout_s: Per-request timeout in seconds.

    Legacy (Sprint 29) usage still works::

        resp = client.run("What is 2+2?")

    Full task lifecycle::

        task_id = client.submit("What is 2+2?")
        task    = client.wait(task_id)      # blocks until completed/failed
        print(task.result)

    SSE streaming::

        for task in client.stream(task_id):
            print(task.state, task.result)
    """

    def __init__(self, url: str, timeout_s: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self._card: AgentCard | None = None

    # ── Discovery ──────────────────────────────────────────────────────────────

    def card(self) -> AgentCard:
        """Fetch (and cache) the remote agent's :class:`AgentCard`."""
        if self._card is None:
            with urllib.request.urlopen(
                f"{self.url}/.well-known/agent-card",
                timeout=self.timeout_s,
            ) as resp:
                data = json.loads(resp.read())
            self._card = AgentCard.from_dict(data)
        return self._card

    # ── Legacy /run (Sprint 29 compat) ────────────────────────────────────────

    def run(
        self,
        content: str,
        *,
        sender: str = "user",
        context: dict[str, Any] | None = None,
    ) -> A2AResponse:
        """Synchronous fire-and-forget run (legacy endpoint)."""
        msg = A2AMessage(content=content, sender=sender, context=context or {})
        payload = json.dumps(msg.to_dict()).encode()
        req = urllib.request.Request(
            f"{self.url}/run",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.loads(resp.read())
        return A2AResponse.from_dict(data)

    async def run_async(
        self,
        content: str,
        *,
        sender: str = "user",
        context: dict[str, Any] | None = None,
    ) -> A2AResponse:
        """Async wrapper around :meth:`run`."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.run(content, sender=sender, context=context),
        )

    # ── Async task lifecycle ───────────────────────────────────────────────────

    def submit(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Submit a task; returns ``task_id`` immediately (state=submitted)."""
        payload = json.dumps({"content": content, "context": context or {}}).encode()
        req = urllib.request.Request(
            f"{self.url}/tasks",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.loads(resp.read())
        return data["task_id"]

    def poll(self, task_id: str) -> A2ATask:
        """Fetch current state of a task."""
        with urllib.request.urlopen(
            f"{self.url}/tasks/{task_id}",
            timeout=self.timeout_s,
        ) as resp:
            data = json.loads(resp.read())
        return A2ATask.from_dict(data)

    def wait(
        self,
        task_id: str,
        *,
        poll_interval: float = 0.1,
        timeout: float = 60.0,
    ) -> A2ATask:
        """Poll until the task reaches a terminal state.

        Raises :class:`TimeoutError` if *timeout* seconds elapse.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.poll(task_id)
            if task.is_terminal():
                return task
            time.sleep(poll_interval)
        raise TimeoutError(f"Task {task_id!r} did not complete within {timeout}s")

    def stream(
        self,
        task_id: str,
        *,
        timeout: float = 60.0,
    ) -> Iterator[A2ATask]:
        """SSE stream — yields :class:`A2ATask` on each state change."""
        import urllib.error

        deadline = time.monotonic() + timeout
        try:
            with urllib.request.urlopen(
                f"{self.url}/tasks/{task_id}/stream",
                timeout=self.timeout_s,
            ) as resp:
                for raw_line in resp:
                    if time.monotonic() > deadline:
                        break
                    line: str = raw_line.decode().strip()
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:].strip())
                    task = A2ATask.from_dict(data)
                    yield task
                    if task.is_terminal():
                        break
        except urllib.error.URLError:
            pass  # server disconnected — stop iteration

    def list_tasks(self, limit: int = 20) -> list[A2ATask]:
        """Return the most recent tasks from the server."""
        with urllib.request.urlopen(
            f"{self.url}/tasks",
            timeout=self.timeout_s,
        ) as resp:
            data = json.loads(resp.read())
        return [A2ATask.from_dict(t) for t in data.get("tasks", [])[:limit]]

    # ── Async variants ────────────────────────────────────────────────────────

    async def submit_async(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.submit(content, context))

    async def wait_async(
        self,
        task_id: str,
        poll_interval: float = 0.1,
        timeout: float = 60.0,
    ) -> A2ATask:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.wait(task_id, poll_interval=poll_interval, timeout=timeout),
        )
