"""MeshFlow HTTP Proxy — stdlib reverse proxy with wire-level enforcement.

Language-agnostic governance: any process (Python, JavaScript, Ruby, curl,
Go, Rust) that routes to ``http://localhost:<port>/v1`` gets tool call
enforcement, PII scanning, and audit logging — no SDK integration required.

Architecture
------------
- ``ThreadingHTTPServer`` — one thread per connection, concurrent-safe
- All requests forwarded to ``--upstream`` (default https://api.openai.com)
- POST /v1/chat/completions — response is intercepted, tool calls enforced
- Streaming (``stream: true``) — SSE lines parsed, tool call deltas assembled,
  blocked call lines dropped, allowed lines re-emitted
- All other paths — forwarded transparently (auth, embeddings, models, etc.)

Usage
-----

    # Basic — no policy, just audit logging
    meshflow proxy --port 8080

    # With policy file
    meshflow proxy --port 8080 --policy policy.yaml

    # Custom upstream (Azure OpenAI, proxy chain, etc.)
    meshflow proxy --port 8080 --upstream https://my-azure.openai.azure.com

    # Point any client to the proxy
    OPENAI_BASE_URL=http://localhost:8080/v1 python my_app.py
    OPENAI_API_BASE=http://localhost:8080/v1  # LangChain env var

Security note
-------------
The proxy forwards the ``Authorization: Bearer`` header to the upstream as-is.
Run the proxy on localhost or a trusted network only; never expose it publicly.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


_INTERCEPT_PATH = "/v1/chat/completions"


def _assemble_from_sse(chunks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Assemble complete tool calls from a list of parsed SSE chunk dicts.

    Returns ``{index: {id, name, arguments}}`` — same contract as
    ``_assemble_tool_calls_from_chunks`` in the Python-client proxy,
    but operates on already-parsed dicts rather than SDK response objects.
    """
    result: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                entry = result.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    entry["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    entry["name"] += fn["name"]
                if fn.get("arguments"):
                    entry["arguments"] += fn["arguments"]
    return result
_DEFAULT_UPSTREAM = "https://api.openai.com"
_PASSTHROUGH_METHODS = {"GET", "DELETE", "OPTIONS", "HEAD"}
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


# ── Request handler ───────────────────────────────────────────────────────────

class _ProxyHandler(BaseHTTPRequestHandler):
    """Single-connection HTTP proxy handler."""

    # Set by MeshFlowHTTPProxy before serving
    upstream: str = _DEFAULT_UPSTREAM
    interceptor: Any = None
    agent_id: str = "http-proxy"
    on_block: Any = None
    _stats: dict[str, int] = {}
    _stats_lock: threading.Lock = threading.Lock()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # silence default access log

    def do_GET(self) -> None: self._forward()
    def do_DELETE(self) -> None: self._forward()
    def do_OPTIONS(self) -> None: self._forward()
    def do_HEAD(self) -> None: self._forward()

    def do_POST(self) -> None:
        if self.path.rstrip("/") == _INTERCEPT_PATH.rstrip("/"):
            self._intercept_completions()
        else:
            self._forward()

    # ── Intercept path ────────────────────────────────────────────────────────

    def _intercept_completions(self) -> None:
        body = self._read_body()
        try:
            req_json = json.loads(body)
        except Exception:
            req_json = {}

        is_stream = req_json.get("stream", False)
        upstream_resp, upstream_body, upstream_headers = self._call_upstream(body)

        if upstream_resp is None:
            return  # error already written

        if is_stream:
            self._handle_streaming(upstream_resp, upstream_headers)
        else:
            self._handle_non_streaming(upstream_body, upstream_headers)

    def _handle_non_streaming(
        self, body: bytes, upstream_headers: dict[str, str]
    ) -> None:
        try:
            resp_json = json.loads(body)
        except Exception:
            self._send_raw(200, upstream_headers, body)
            return

        if self.interceptor is None:
            self._send_json(200, upstream_headers, resp_json)
            return

        # Enforce tool calls
        choices = resp_json.get("choices", [])
        for choice in choices:
            msg = choice.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                continue

            allowed = []
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    args = json.loads(args_str)
                except Exception:
                    args = {"_raw": args_str}

                decision = self._enforce(name, args, tc.get("id", str(uuid.uuid4())[:8]))
                if decision["allowed"]:
                    allowed.append(tc)
                else:
                    self._record_block(name, decision["block_reason"])

            msg["tool_calls"] = allowed or None
            choice["message"] = msg

        self._send_json(200, upstream_headers, resp_json)

    def _handle_streaming(self, upstream_resp: Any, upstream_headers: dict[str, str]) -> None:
        """Stream SSE lines, enforce tool calls, drop blocked call lines."""
        self.send_response(200)
        for k, v in upstream_headers.items():
            if k.lower() not in _HOP_BY_HOP:
                self.send_header(k, v)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        # Accumulate tool call deltas per index
        tc_acc: dict[int, dict[str, Any]] = {}
        blocked_indices: set[int] = set()
        buffered_tc_lines: list[tuple[int, bytes]] = []  # (index, raw_line)
        enforcement_done = False

        try:
            for raw_line in upstream_resp:
                if not raw_line.strip():
                    if not enforcement_done:
                        self.wfile.write(b"\n")
                    continue

                if not raw_line.startswith(b"data: "):
                    self.wfile.write(raw_line + b"\n")
                    continue

                data_str = raw_line[6:].strip().decode("utf-8", errors="replace")
                if data_str == "[DONE]":
                    # Enforce on accumulated tool calls before flushing
                    if not enforcement_done and tc_acc and self.interceptor is not None:
                        blocked_indices = self._enforce_stream_acc(tc_acc)
                        enforcement_done = True

                    # Replay buffered tc lines, skipping blocked indices
                    for idx, buffered in buffered_tc_lines:
                        if idx not in blocked_indices:
                            self.wfile.write(b"data: " + buffered + b"\n\n")
                    buffered_tc_lines.clear()

                    self.wfile.write(b"data: [DONE]\n\n")
                    break

                try:
                    chunk = json.loads(data_str)
                except Exception:
                    self.wfile.write(raw_line + b"\n")
                    continue

                # Check for tool call deltas
                choices = chunk.get("choices", [])
                has_tc = False
                for choice in choices:
                    delta = choice.get("delta", {})
                    tcs = delta.get("tool_calls")
                    if not tcs:
                        continue
                    has_tc = True
                    for tc_delta in tcs:
                        idx = tc_delta.get("index", 0)
                        entry = tc_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc_delta.get("id"):
                            entry["id"] = tc_delta["id"]
                        fn = tc_delta.get("function", {})
                        if fn.get("name"):
                            entry["name"] = fn["name"]
                        if fn.get("arguments"):
                            entry["arguments"] += fn["arguments"]

                    # Buffer tc lines — emit after enforcement
                    tc_indices = {tc.get("index", 0) for tc in tcs}
                    for idx in tc_indices:
                        buffered_tc_lines.append((idx, data_str.encode("utf-8")))

                if not has_tc:
                    # Flush any buffered tc lines not yet flushed
                    # (happens at finish_reason chunk before [DONE])
                    finish = choices[0].get("finish_reason") if choices else None
                    if finish == "tool_calls" and not enforcement_done and tc_acc and self.interceptor is not None:
                        blocked_indices = self._enforce_stream_acc(tc_acc)
                        enforcement_done = True
                        for idx, buffered in buffered_tc_lines:
                            if idx not in blocked_indices:
                                self.wfile.write(b"data: " + buffered + b"\n\n")
                        buffered_tc_lines.clear()
                    elif not enforcement_done:
                        # Pass through non-tool-call content chunks immediately
                        self.wfile.write(b"data: " + data_str.encode("utf-8") + b"\n\n")
                    else:
                        self.wfile.write(b"data: " + data_str.encode("utf-8") + b"\n\n")

        except (BrokenPipeError, ConnectionResetError):
            pass

    def _enforce_stream_acc(self, tc_acc: dict[int, dict[str, Any]]) -> set[int]:
        """Enforce assembled streaming tool calls; return blocked indices."""
        blocked: set[int] = set()
        for idx, tc in tc_acc.items():
            name = tc.get("name", "")
            args_str = tc.get("arguments", "{}")
            try:
                args = json.loads(args_str)
            except Exception:
                args = {"_raw": args_str}
            decision = self._enforce(name, args, tc.get("id", str(idx)))
            if not decision["allowed"]:
                blocked.add(idx)
                self._record_block(name, decision["block_reason"])
        return blocked

    def _parse_tc_acc(self, parsed_chunks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        """Delegate to module-level helper for testability."""
        return _assemble_from_sse(parsed_chunks)

    # ── Generic forward ───────────────────────────────────────────────────────

    def _forward(self) -> None:
        body = self._read_body()
        upstream_resp, upstream_body, upstream_headers = self._call_upstream(body)
        if upstream_resp is None:
            return
        self._send_raw(upstream_resp.status if hasattr(upstream_resp, "status") else 200,
                       upstream_headers, upstream_body)

    # ── Upstream call ─────────────────────────────────────────────────────────

    def _call_upstream(
        self, body: bytes
    ) -> tuple[Any | None, bytes, dict[str, str]]:
        target = self.upstream.rstrip("/") + self.path
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        headers["Host"] = target.split("/")[2] if "://" in target else target
        req = Request(target, data=body or None, headers=headers, method=self.command)
        try:
            with urlopen(req, timeout=120) as resp:
                resp_headers = {k: v for k, v in resp.getheaders()
                                if k.lower() not in _HOP_BY_HOP}
                resp_body = resp.read()
                return resp, resp_body, resp_headers
        except HTTPError as e:
            body_err = e.read()
            self._send_raw(e.code, dict(e.headers), body_err)
            return None, b"", {}
        except Exception as e:
            err = json.dumps({"error": {"message": str(e), "type": "proxy_error"}}).encode()
            self._send_raw(502, {"Content-Type": "application/json"}, err)
            return None, b"", {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_raw(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for k, v in headers.items():
            # Always recompute Content-Length from the actual body — never
            # forward the upstream value, which becomes stale when we modify
            # the response (e.g. removing blocked tool calls).
            if k.lower() in _HOP_BY_HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, headers: dict[str, str], data: Any) -> None:
        body = json.dumps(data).encode()
        h = dict(headers)
        h["Content-Type"] = "application/json"
        self._send_raw(status, h, body)

    def _enforce(self, tool_name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
        """Synchronously enforce a single tool call; return {allowed, block_reason}."""
        if self.interceptor is None:
            with self._stats_lock:
                self._stats["allowed"] = self._stats.get("allowed", 0) + 1
            return {"allowed": True, "block_reason": ""}

        from meshflow.core.tool_intercept import ToolCallEvent
        import asyncio

        event = ToolCallEvent(
            tool_name=tool_name,
            args=args,
            agent_id=self.agent_id,
            call_id=call_id,
            source="http_proxy",
        )
        try:
            loop = asyncio.new_event_loop()
            decision = loop.run_until_complete(self.interceptor.before_call(event))
            loop.close()
        except Exception:
            with self._stats_lock:
                self._stats["allowed"] = self._stats.get("allowed", 0) + 1
            return {"allowed": True, "block_reason": ""}

        with self._stats_lock:
            key = "allowed" if decision.allowed else "blocked"
            self._stats[key] = self._stats.get(key, 0) + 1

        return {"allowed": decision.allowed, "block_reason": decision.block_reason}

    def _record_block(self, tool_name: str, reason: str) -> None:
        if self.on_block:
            try:
                self.on_block({"tool_name": tool_name, "reason": reason,
                               "ts": time.time(), "agent_id": self.agent_id})
            except Exception:
                pass


# ── Server ────────────────────────────────────────────────────────────────────

class MeshFlowHTTPProxy:
    """Language-agnostic HTTP reverse proxy with tool call enforcement.

    Any process that routes to ``http://localhost:<port>/v1`` gets full
    MeshFlow governance — no Python SDK required.

    Usage::

        from meshflow import MeshFlowHTTPProxy, PolicyToolCallInterceptor
        from meshflow.policy.engine import PolicyStore, PolicyEngine, PolicyAction

        store = PolicyStore()
        store.add_rule("block-shell", PolicyAction.DENY,
                       [("tool_name", "eq", "exec_shell")], framework="tool_calls")
        interceptor = PolicyToolCallInterceptor(PolicyEngine(store))

        proxy = MeshFlowHTTPProxy(port=8080, interceptor=interceptor)
        proxy.start()   # background thread
        # ... or proxy.serve_forever() for blocking

        # Point any client to the proxy
        # OPENAI_BASE_URL=http://localhost:8080/v1 python my_app.py
    """

    def __init__(
        self,
        port: int = 8080,
        upstream: str = _DEFAULT_UPSTREAM,
        interceptor: Any = None,
        agent_id: str = "http-proxy",
        on_block: Any = None,
        host: str = "127.0.0.1",
    ) -> None:
        self.port = port
        self.host = host

        # Build a handler subclass with bound config
        stats: dict[str, int] = {}
        stats_lock = threading.Lock()

        class BoundHandler(_ProxyHandler):
            pass

        BoundHandler.upstream = upstream.rstrip("/")
        BoundHandler.interceptor = interceptor
        BoundHandler.agent_id = agent_id
        BoundHandler.on_block = on_block
        BoundHandler._stats = stats
        BoundHandler._stats_lock = stats_lock

        self._handler_cls = BoundHandler
        self._stats = stats
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, daemon: bool = True) -> None:
        """Start the proxy in a background thread."""
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=daemon)
        self._thread.start()

    def serve_forever(self) -> None:
        """Start the proxy and block until stopped."""
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler_cls)
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._server.shutdown()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"


__all__ = ["MeshFlowHTTPProxy", "_assemble_from_sse"]
