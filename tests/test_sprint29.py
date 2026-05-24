"""Sprint 29 — A2A Protocol: Agent-to-Agent over HTTP."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.a2a.protocol import AgentCard, A2AMessage, A2AResponse
from meshflow.a2a.client import A2AClient
from meshflow.a2a.server import A2AServer


# ── AgentCard ─────────────────────────────────────────────────────────────────

class TestAgentCard:
    def test_defaults(self):
        card = AgentCard(name="bot")
        assert card.version == "1.0"
        assert card.capabilities == []
        assert card.url == ""

    def test_round_trip_dict(self):
        card = AgentCard(name="bot", description="A helper", url="http://x", capabilities=["run"])
        card2 = AgentCard.from_dict(card.to_dict())
        assert card2.name == "bot"
        assert card2.description == "A helper"
        assert card2.capabilities == ["run"]

    def test_from_dict_partial(self):
        card = AgentCard.from_dict({"name": "minimal"})
        assert card.name == "minimal"
        assert card.version == "1.0"


# ── A2AMessage ────────────────────────────────────────────────────────────────

class TestA2AMessage:
    def test_defaults(self):
        msg = A2AMessage(content="hello")
        assert msg.sender == "user"
        assert msg.context == {}

    def test_to_dict(self):
        msg = A2AMessage(content="hi", sender="agent1")
        d = msg.to_dict()
        assert d["content"] == "hi"
        assert d["sender"] == "agent1"

    def test_from_dict(self):
        msg = A2AMessage.from_dict({"content": "task", "sender": "orchestrator"})
        assert msg.content == "task"
        assert msg.sender == "orchestrator"

    def test_from_dict_defaults(self):
        msg = A2AMessage.from_dict({})
        assert msg.content == ""
        assert msg.sender == "user"


# ── A2AResponse ───────────────────────────────────────────────────────────────

class TestA2AResponse:
    def test_success_true(self):
        r = A2AResponse(content="done")
        assert r.success is True

    def test_success_false_on_error(self):
        r = A2AResponse(content="", error="timeout")
        assert r.success is False

    def test_success_false_when_blocked(self):
        r = A2AResponse(content="", blocked=True)
        assert r.success is False

    def test_round_trip_dict(self):
        r = A2AResponse(content="ok", agent_name="bot", tokens=42, cost_usd=0.01)
        r2 = A2AResponse.from_dict(r.to_dict())
        assert r2.content == "ok"
        assert r2.tokens == 42
        assert r2.agent_name == "bot"

    def test_from_dict_defaults(self):
        r = A2AResponse.from_dict({"content": "hi"})
        assert r.tokens == 0
        assert not r.blocked
        assert r.error == ""


# ── Minimal stub HTTP server ───────────────────────────────────────────────────

def _find_free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _stub_server(port: int) -> HTTPServer:
    """A minimal A2A-compatible stub server for client tests."""
    card_data = json.dumps({
        "name": "stub-agent",
        "description": "stub",
        "url": f"http://127.0.0.1:{port}",
        "capabilities": ["run"],
        "version": "1.0",
        "input_schema": {},
        "output_schema": {},
    }).encode()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            if self.path in ("/.well-known/agent-card", "/agent-card"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(card_data)))
                self.end_headers()
                self.wfile.write(card_data)
            elif self.path == "/health":
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/run":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                resp = {
                    "content": f"Echo: {body.get('content', '')}",
                    "agent_name": "stub-agent",
                    "tokens": 5,
                    "cost_usd": 0.001,
                    "blocked": False,
                    "error": "",
                    "metadata": {},
                }
                body_bytes = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
            else:
                self.send_response(404)
                self.end_headers()

    return HTTPServer(("127.0.0.1", port), _Handler)


@pytest.fixture(scope="module")
def stub_port():
    port = _find_free_port()
    srv = _stub_server(port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    yield port
    srv.shutdown()


# ── A2AClient ─────────────────────────────────────────────────────────────────

class TestA2AClient:
    def test_card_fetch(self, stub_port):
        client = A2AClient(f"http://127.0.0.1:{stub_port}")
        card = client.card()
        assert card.name == "stub-agent"
        assert "run" in card.capabilities

    def test_card_cached(self, stub_port):
        client = A2AClient(f"http://127.0.0.1:{stub_port}")
        c1 = client.card()
        c2 = client.card()
        assert c1 is c2  # same object — cached

    def test_run_returns_response(self, stub_port):
        client = A2AClient(f"http://127.0.0.1:{stub_port}")
        resp = client.run("What is 2+2?")
        assert resp.success
        assert "Echo:" in resp.content
        assert "What is 2+2?" in resp.content

    def test_run_tokens_populated(self, stub_port):
        client = A2AClient(f"http://127.0.0.1:{stub_port}")
        resp = client.run("hello")
        assert resp.tokens > 0

    def test_run_async(self, stub_port):
        client = A2AClient(f"http://127.0.0.1:{stub_port}")
        resp = asyncio.run(client.run_async("async task"))
        assert resp.success
        assert "async task" in resp.content

    def test_trailing_slash_stripped(self, stub_port):
        client = A2AClient(f"http://127.0.0.1:{stub_port}/")
        assert not client.url.endswith("/")


# ── A2AServer — integration with Agent ────────────────────────────────────────

class TestA2AServer:
    def test_server_card(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="test-agent", role="executor")
        server = A2AServer(agent, port=_find_free_port(), description="Test agent")
        card = server.card()
        assert card.name == "test-agent"
        assert card.url.startswith("http://")
        assert "run" in card.capabilities

    def test_server_url_format(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="a", role="executor")
        srv = A2AServer(agent, host="0.0.0.0", port=9876)
        assert srv.url == "http://0.0.0.0:9876"

    def test_server_context_manager_starts_stops(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        port = _find_free_port()
        agent = Agent(name="cm-agent", role="executor")
        with A2AServer(agent, port=port) as srv:
            assert srv._server is not None
        assert srv._server is None

    def test_full_roundtrip_with_mock_agent(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        port = _find_free_port()
        agent = Agent(name="echo-agent", role="executor")
        with A2AServer(agent, port=port) as _srv:
            time.sleep(0.15)
            client = A2AClient(f"http://127.0.0.1:{port}")
            card = client.card()
            assert card.name == "echo-agent"
            resp = client.run("hello world")
            # EchoProvider response has content
            assert isinstance(resp.content, str)


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_top_level_imports(self):
        from meshflow.a2a import AgentCard, A2AMessage, A2AResponse, A2AClient, A2AServer
        assert all(x is not None for x in [AgentCard, A2AMessage, A2AResponse, A2AClient, A2AServer])
