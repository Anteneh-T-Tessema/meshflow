"""Tests for MeshFlowHTTPProxy — stdlib HTTP reverse proxy with enforcement."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from meshflow.proxy.http_server import MeshFlowHTTPProxy, _assemble_from_sse
from meshflow.core.tool_intercept import AllowListInterceptor, PolicyToolCallInterceptor


# ── Fake upstream server ──────────────────────────────────────────────────────

class _FakeUpstreamHandler(BaseHTTPRequestHandler):
    """Minimal HTTP server that returns canned responses."""

    response_body: bytes = b"{}"
    response_status: int = 200
    response_headers: dict = {}

    def log_message(self, *a: Any) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = self.__class__.response_body
        self.send_response(self.__class__.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in self.__class__.response_headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"object":"list","data":[]}')


def _fake_upstream(body: dict | str, status: int = 200) -> tuple[HTTPServer, int, str]:
    """Start a fake upstream; return (server, port, base_url)."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    raw = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
    _FakeUpstreamHandler.response_body = raw
    _FakeUpstreamHandler.response_status = status

    server = HTTPServer(("127.0.0.1", port), _FakeUpstreamHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port, f"http://127.0.0.1:{port}"


def _proxy(upstream_url: str, interceptor: Any = None, port: int = 0) -> tuple[MeshFlowHTTPProxy, int]:
    """Start a proxy in front of *upstream_url*; return (proxy, port)."""
    import socket
    if port == 0:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    p = MeshFlowHTTPProxy(port=port, upstream=upstream_url, interceptor=interceptor)
    p.start(daemon=True)
    time.sleep(0.05)
    return p, port


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _completion_body(tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": "hi"}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
    }


def _tc(name: str, args: dict, call_id: str = "call-1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# ── _assemble_from_sse helper ─────────────────────────────────────────────────

def test_assemble_from_sse_empty():
    from meshflow.proxy.http_server import _assemble_from_sse
    assert _assemble_from_sse([]) == {}


def test_assemble_from_sse_single_call():
    from meshflow.proxy.http_server import _assemble_from_sse
    lines = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c-1",
            "function": {"name": "search", "arguments": '{"q":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0,
            "function": {"name": "", "arguments": '"hello"}'}}]}}]},
    ]
    result = _assemble_from_sse(lines)
    assert result[0]["name"] == "search"
    assert result[0]["arguments"] == '{"q":"hello"}'


# ── Proxy construction ────────────────────────────────────────────────────────

def test_proxy_base_url():
    p = MeshFlowHTTPProxy(port=9876, host="127.0.0.1")
    assert p.base_url == "http://127.0.0.1:9876/v1"


def test_proxy_stats_initial():
    p = MeshFlowHTTPProxy(port=9877)
    assert p.stats() == {}


def test_proxy_importable_from_meshflow():
    from meshflow import MeshFlowHTTPProxy as _P
    assert _P is MeshFlowHTTPProxy


# ── Pass-through (no tool calls) ──────────────────────────────────────────────

def test_proxy_passthrough_no_tool_calls():
    upstream_body = _completion_body()
    upstream, u_port, u_url = _fake_upstream(upstream_body)
    proxy, p_port = _proxy(u_url)

    resp = _post(f"http://127.0.0.1:{p_port}/v1/chat/completions",
                 {"model": "gpt-4o", "messages": []})

    assert resp["choices"][0]["message"]["role"] == "assistant"
    upstream.shutdown()


# ── Allow path ────────────────────────────────────────────────────────────────

def test_proxy_allows_tool_call():
    upstream_body = _completion_body([_tc("search", {"q": "ok"})])
    upstream, u_port, u_url = _fake_upstream(upstream_body)
    interceptor = AllowListInterceptor(["search"])
    proxy, p_port = _proxy(u_url, interceptor)

    resp = _post(f"http://127.0.0.1:{p_port}/v1/chat/completions",
                 {"model": "gpt-4o", "messages": []})

    tcs = resp["choices"][0]["message"].get("tool_calls") or []
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "search"
    assert proxy.stats().get("allowed", 0) == 1
    upstream.shutdown()


# ── Block path ────────────────────────────────────────────────────────────────

def test_proxy_blocks_tool_call():
    upstream_body = _completion_body([_tc("exec_shell", {"cmd": "rm -rf /"})])
    upstream, u_port, u_url = _fake_upstream(upstream_body)
    interceptor = AllowListInterceptor(["search"])  # exec_shell not in list
    proxy, p_port = _proxy(u_url, interceptor)

    resp = _post(f"http://127.0.0.1:{p_port}/v1/chat/completions",
                 {"model": "gpt-4o", "messages": []})

    tcs = resp["choices"][0]["message"].get("tool_calls")
    assert not tcs
    assert proxy.stats().get("blocked", 0) == 1
    upstream.shutdown()


# ── Mixed allow/block ─────────────────────────────────────────────────────────

def test_proxy_mixed_tool_calls():
    upstream_body = _completion_body([
        _tc("search", {"q": "safe"}, "c-1"),
        _tc("exec_shell", {"cmd": "danger"}, "c-2"),
    ])
    upstream, u_port, u_url = _fake_upstream(upstream_body)
    interceptor = AllowListInterceptor(["search"])
    proxy, p_port = _proxy(u_url, interceptor)

    resp = _post(f"http://127.0.0.1:{p_port}/v1/chat/completions",
                 {"model": "gpt-4o", "messages": []})

    tcs = resp["choices"][0]["message"].get("tool_calls") or []
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "search"
    assert proxy.stats().get("allowed", 0) == 1
    assert proxy.stats().get("blocked", 0) == 1
    upstream.shutdown()


# ── No interceptor — pure passthrough ────────────────────────────────────────

def test_proxy_no_interceptor_passes_all():
    upstream_body = _completion_body([_tc("write_file", {"path": "/etc/passwd"})])
    upstream, u_port, u_url = _fake_upstream(upstream_body)
    proxy, p_port = _proxy(u_url, interceptor=None)

    resp = _post(f"http://127.0.0.1:{p_port}/v1/chat/completions",
                 {"model": "gpt-4o", "messages": []})

    tcs = resp["choices"][0]["message"].get("tool_calls") or []
    assert len(tcs) == 1
    upstream.shutdown()


# ── on_block callback ─────────────────────────────────────────────────────────

def test_proxy_on_block_callback():
    fired: list[dict] = []
    upstream_body = _completion_body([_tc("drop_table", {})])
    upstream, u_port, u_url = _fake_upstream(upstream_body)

    interceptor = AllowListInterceptor([])  # block everything
    proxy, p_port = _proxy(u_url, interceptor)
    proxy._handler_cls.on_block = fired.append

    _post(f"http://127.0.0.1:{p_port}/v1/chat/completions",
          {"model": "gpt-4o", "messages": []})

    assert len(fired) == 1
    assert fired[0]["tool_name"] == "drop_table"
    upstream.shutdown()


# ── Non-completions path forwarded transparently ──────────────────────────────

def test_proxy_forwards_non_completions_path():
    upstream, u_port, u_url = _fake_upstream({"object": "list", "data": []})
    proxy, p_port = _proxy(u_url)

    req = urllib.request.Request(f"http://127.0.0.1:{p_port}/v1/models")
    with urllib.request.urlopen(req, timeout=5) as r:
        resp = json.loads(r.read())

    assert "data" in resp
    upstream.shutdown()
