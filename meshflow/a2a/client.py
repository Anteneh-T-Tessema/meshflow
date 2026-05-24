"""A2A HTTP client — calls remote MeshFlow agents over A2A."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from .protocol import A2AMessage, A2AResponse, AgentCard


class A2AClient:
    """HTTP client for calling remote A2A agents.

    Zero external dependencies — uses stdlib urllib.

    Parameters
    ----------
    url:       Base URL of the remote A2A server (e.g. ``http://localhost:8080``).
    timeout_s: Per-request timeout in seconds.
    """

    def __init__(self, url: str, timeout_s: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self._card: AgentCard | None = None

    # ── discovery ──────────────────────────────────────────────────────────────

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

    # ── task execution ─────────────────────────────────────────────────────────

    def run(
        self,
        content: str,
        *,
        sender: str = "user",
        context: dict[str, Any] | None = None,
    ) -> A2AResponse:
        """Send *content* as a task to the remote agent and return its response."""
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
        """Async wrapper — offloads the blocking call to a thread-pool executor."""
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.run(content, sender=sender, context=context),
        )
