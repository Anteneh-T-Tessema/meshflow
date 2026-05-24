"""A2A HTTP server — serves a MeshFlow Agent over A2A.

Uses stdlib http.server (no FastAPI dependency).
Runs in a daemon thread so the calling event loop stays free.
"""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from .protocol import A2AResponse, AgentCard

if TYPE_CHECKING:
    from meshflow.agents.builder import Agent


class A2AServer:
    """Serve a :class:`~meshflow.agents.builder.Agent` over A2A HTTP.

    Parameters
    ----------
    agent:       The MeshFlow agent to expose.
    host:        Bind address (default ``127.0.0.1``).
    port:        TCP port (default ``8080``).
    description: Human-readable description surfaced in the AgentCard.

    Usage::

        server = A2AServer(agent, port=8080)
        server.start()
        # ... later ...
        server.stop()

    Or as a context manager::

        with A2AServer(agent, port=8080):
            client = A2AClient("http://127.0.0.1:8080")
            resp = client.run("What is 2+2?")
    """

    def __init__(
        self,
        agent: "Agent",
        host: str = "127.0.0.1",
        port: int = 8080,
        description: str = "",
    ) -> None:
        self.agent = agent
        self.host = host
        self.port = port
        self.description = description or f"MeshFlow agent: {agent.name}"
        self._server: HTTPServer | None = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def card(self) -> AgentCard:
        return AgentCard(
            name=self.agent.name,
            description=self.description,
            url=self.url,
            capabilities=["run"],
            version="1.0",
        )

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the HTTP server in a daemon thread."""
        import threading

        handler_cls = self._make_handler()
        self._server = HTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name=f"a2a-{self.agent.name}"
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    def __enter__(self) -> "A2AServer":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ── request handler factory ────────────────────────────────────────────────

    def _make_handler(self) -> type:
        agent = self.agent
        card_bytes = json.dumps(self.card().to_dict()).encode()

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence noisy access logs
                pass

            def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path in ("/.well-known/agent-card", "/agent-card"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(card_bytes)))
                    self.end_headers()
                    self.wfile.write(card_bytes)
                elif self.path == "/health":
                    self._send_json({"status": "ok", "agent": agent.name})
                else:
                    self._send_json({"error": "not found"}, 404)

            def do_POST(self) -> None:
                if self.path != "/run":
                    self._send_json({"error": "not found"}, 404)
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    msg = json.loads(body)
                    content = msg.get("content", "")
                    loop = asyncio.new_event_loop()
                    try:
                        built = agent._build()
                        ctx = msg.get("context", {})
                        result = loop.run_until_complete(built.step(content, ctx))
                    finally:
                        loop.close()
                    resp = A2AResponse(
                        content=result.get("result", result.get("content", "")),
                        agent_name=agent.name,
                        tokens=result.get("tokens", 0),
                        cost_usd=result.get("cost_usd", 0.0),
                        blocked=result.get("blocked", False),
                    )
                    self._send_json(resp.to_dict())
                except Exception as exc:
                    self._send_json(
                        A2AResponse(content="", error=str(exc)).to_dict(), 500
                    )

        return _Handler
