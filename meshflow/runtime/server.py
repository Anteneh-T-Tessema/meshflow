"""MeshFlow HTTP Runtime — multi-language access over a simple JSON protocol.

Any language can call MeshFlow by talking to this server.
The server is the runtime; language SDKs are thin HTTP wrappers.

Endpoints:
  POST /run       { task, policy? }              → RunResult JSON
  POST /stream    { task, policy? }              → NDJSON stream of MeshEvents
  GET  /health                                   → { ok: true, version: "0.6.0" }
  GET  /policy/check  { task, agent_count? }     → complexity recommendation

Run:
  python -m meshflow.runtime.server
  meshflow serve --port 8000
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from meshflow.core.mesh import Mesh
from meshflow.core.schemas import (
    CircuitBreakerConfig, HumanInLoopConfig, Policy,
)

VERSION = "0.6.0"


def _policy_from_dict(d: dict[str, Any]) -> Policy:
    return Policy(
        budget_usd=d.get("budget_usd", 1.0),
        budget_tokens=d.get("budget_tokens", 500_000),
        timeout_s=d.get("timeout_s", 300.0),
        max_steps=d.get("max_steps", 50),
        deterministic_gate=d.get("deterministic_gate", True),
        enable_guardian=d.get("enable_guardian", True),
        enable_collusion_audit=d.get("enable_collusion_audit", True),
        enable_uncertainty=d.get("enable_uncertainty", True),
        enable_environmental=d.get("enable_environmental", False),
        enable_cross_run_learning=d.get("enable_cross_run_learning", False),
        carbon_budget_g=d.get("carbon_budget_g", 500.0),
    )


def _run_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "status": result.status.value,
        "output": result.output,
        "total_cost_usd": result.total_cost_usd,
        "total_tokens": result.total_tokens,
        "total_carbon_g": result.total_carbon_g,
        "duration_s": result.duration_s,
        "ledger_entries": result.ledger_entries,
        "trace_id": result.trace_id,
        "checkpoints": result.checkpoints,
        "error": result.error,
        "collusion_alerts": result.collusion_alerts,
    }


class MeshFlowHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # suppress default access log

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_ndjson_line(self, data: dict[str, Any]) -> None:
        line = (json.dumps(data) + "\n").encode()
        self.wfile.write(line)
        self.wfile.flush()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "version": VERSION})
        else:
            self._send_json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        body = self._read_body()

        if self.path == "/run":
            self._handle_run(body)
        elif self.path == "/stream":
            self._handle_stream(body)
        elif self.path == "/policy/check":
            self._handle_complexity(body)
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_run(self, body: dict[str, Any]) -> None:
        task = body.get("task", "")
        if not task:
            self._send_json(400, {"error": "task is required"})
            return
        policy = _policy_from_dict(body.get("policy", {}))
        mesh = Mesh(policy=policy)
        try:
            result = asyncio.run(mesh.run(task, policy=policy, context=body.get("context")))
            self._send_json(200, _run_result_to_dict(result))
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_stream(self, body: dict[str, Any]) -> None:
        task = body.get("task", "")
        if not task:
            self._send_json(400, {"error": "task is required"})
            return
        policy = _policy_from_dict(body.get("policy", {}))
        mesh = Mesh(policy=policy)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        async def _stream() -> None:
            async for event in mesh.stream(task, policy=policy):
                self._send_ndjson_line({
                    "event_type": event.event_type,
                    "agent_id": event.agent_id,
                    "role": event.role,
                    "step": event.step,
                    "uncertainty": event.uncertainty,
                    "cost_usd": event.cost_usd,
                    "tokens": event.tokens,
                    "blocked_by": event.blocked_by,
                    "output": str(event.data.get("execution_result", event.data.get("research", "")))[:500],
                })

        try:
            asyncio.run(_stream())
        except Exception as e:
            self._send_ndjson_line({"event_type": "error", "error": str(e)})

    def _handle_complexity(self, body: dict[str, Any]) -> None:
        from meshflow.core.policy import PolicyEngine
        task = body.get("task", "")
        agent_count = body.get("agent_count", 4)
        engine = PolicyEngine(Policy(), "check")
        rec = engine.check_complexity(task, agent_count)
        self._send_json(200, rec)


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    server = HTTPServer((host, port), MeshFlowHandler)
    print(f"MeshFlow runtime listening on http://{host}:{port}")
    print(f"  POST /run          — execute a task")
    print(f"  POST /stream       — stream events (NDJSON)")
    print(f"  GET  /health       — health check")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MeshFlow runtime server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
