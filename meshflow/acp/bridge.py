"""ACP (Agent Communication Protocol) bridge implementation.

Implements the minimal ACP surface needed for MeshFlow ↔ BeeAI interop:
  - Agent card discovery (/.well-known/acp)
  - Run creation and polling (POST/GET /runs)
  - SSE streaming (/runs/{id}/events)
  - acp_tool() factory — wraps any ACP endpoint as a MeshFlow @tool

Zero external dependencies — uses stdlib http.server and urllib.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import threading
import time
import uuid
import urllib.request
from dataclasses import dataclass, field
from typing import Any


# ── ACP wire types ────────────────────────────────────────────────────────────

@dataclass
class ACPAgentCard:
    """Describes a MeshFlow agent in ACP's agent card format.

    Serialises to the JSON structure returned at ``/.well-known/acp``.
    """

    name: str
    description: str = ""
    version: str = "1.0.0"
    capabilities: list[str] = field(default_factory=lambda: ["text", "stream"])
    input_schema: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task to run"},
            "context": {"type": "object", "description": "Optional context"},
        },
        "required": ["task"],
    })
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "capabilities": self.capabilities,
            "input_schema": self.input_schema,
            "protocol": "acp/1.0",
            "metadata": {
                "framework": "meshflow",
                "framework_version": "1.0.0",
                **self.metadata,
            },
        }


# ── ACPServer ─────────────────────────────────────────────────────────────────

class ACPServer:
    """Expose a MeshFlow Agent as an ACP-compatible HTTP server.

    Implements::

        GET  /.well-known/acp         → agent card JSON
        POST /runs                    → create a run (returns run_id)
        GET  /runs/{id}               → poll run status / result
        GET  /runs/{id}/events        → SSE stream of output tokens
        DELETE /runs/{id}             → cancel a run

    Parameters
    ----------
    agent:
        Any MeshFlow Agent instance.
    port:
        HTTP port to listen on (default: 8001).
    host:
        Bind address (default: 127.0.0.1).
    description:
        Human-readable description in the agent card.
    """

    def __init__(
        self,
        agent: Any,
        port: int = 8001,
        host: str = "127.0.0.1",
        description: str = "",
    ) -> None:
        self._agent = agent
        self._port = port
        self._host = host
        self._card = ACPAgentCard(
            name=getattr(agent, "name", "meshflow-agent"),
            description=description or f"MeshFlow agent: {getattr(agent, 'role', 'executor')}",
        )
        self._runs: dict[str, dict[str, Any]] = {}
        self._server: Any = None
        self._thread: Any = None

    def start(self, daemon: bool = True) -> None:
        """Start the ACP server in a background thread."""
        agent = self._agent
        card = self._card
        runs = self._runs

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                pass

            def _json(self, data: Any, code: int = 200) -> None:
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = self.path.split("?")[0].rstrip("/")

                if path == "/.well-known/acp":
                    self._json(card.to_dict())
                    return

                if path == "/runs":
                    self._json({"runs": list(runs.values())})
                    return

                if path.startswith("/runs/") and not path.endswith("/events"):
                    run_id = path[len("/runs/"):]
                    run = runs.get(run_id)
                    self._json(run or {"error": "not_found"}, 200 if run else 404)
                    return

                if path.startswith("/runs/") and path.endswith("/events"):
                    run_id = path[len("/runs/"):].rstrip("/events")
                    self._stream_events(run_id)
                    return

                self.send_error(404)

            def _stream_events(self, run_id: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                deadline = time.time() + 120  # 2 min max
                while time.time() < deadline:
                    run = runs.get(run_id, {})
                    status = run.get("status", "pending")

                    if status == "completed":
                        data = json.dumps({"type": "result", "output": run.get("output", "")})
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                        break
                    elif status == "failed":
                        data = json.dumps({"type": "error", "error": run.get("error", "")})
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                        break
                    else:
                        # heartbeat
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        time.sleep(0.5)

            def do_POST(self) -> None:
                path = self.path.rstrip("/")
                if path == "/runs":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length)) if length else {}
                    task = body.get("task", "") or body.get("input", {}).get("task", "")
                    run_id = str(uuid.uuid4())
                    runs[run_id] = {
                        "id": run_id, "status": "running",
                        "input": body, "output": None, "error": None,
                        "created_at": time.time(),
                    }

                    # Execute in background thread
                    def _exec() -> None:
                        try:
                            result = asyncio.run(agent.run(task, body.get("context", {})))
                            output = result.get("output", "") if isinstance(result, dict) else str(result)
                            runs[run_id]["status"] = "completed"
                            runs[run_id]["output"] = output
                        except Exception as exc:
                            runs[run_id]["status"] = "failed"
                            runs[run_id]["error"] = str(exc)

                    threading.Thread(target=_exec, daemon=True).start()
                    self._json({"id": run_id, "status": "running"}, 201)
                    return

                self.send_error(404)

            def do_DELETE(self) -> None:
                path = self.path.rstrip("/")
                if path.startswith("/runs/"):
                    run_id = path[len("/runs/"):]
                    if run_id in runs:
                        runs[run_id]["status"] = "cancelled"
                        self._json({"id": run_id, "status": "cancelled"})
                    else:
                        self.send_error(404)
                    return
                self.send_error(404)

            def do_OPTIONS(self) -> None:
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

        self._server = http.server.HTTPServer((self._host, self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=daemon)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"


# ── ACPClient ─────────────────────────────────────────────────────────────────

class ACPClient:
    """Call any ACP-compatible agent (MeshFlow or BeeAI) from MeshFlow.

    Parameters
    ----------
    base_url:
        Base URL of the remote ACP server.
    timeout:
        HTTP timeout in seconds.
    """

    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: bytes | None = None) -> Any:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def discover(self) -> dict[str, Any]:
        """Fetch the remote agent card."""
        return self._request("GET", "/.well-known/acp")

    async def run(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        poll_interval: float = 0.5,
        max_wait: float = 120.0,
    ) -> str:
        """Create a run on the remote ACP agent and wait for completion.

        Returns the output string.
        """
        payload = json.dumps({"task": task, "context": context or {}}).encode()
        create_resp = self._request("POST", "/runs", payload)
        run_id = create_resp.get("id")
        if not run_id:
            raise RuntimeError(f"ACP server did not return run id: {create_resp}")

        deadline = asyncio.get_event_loop().time() + max_wait
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(poll_interval)
            status_resp = self._request("GET", f"/runs/{run_id}")
            status = status_resp.get("status")
            if status == "completed":
                return str(status_resp.get("output", ""))
            if status == "failed":
                raise RuntimeError(f"ACP run {run_id} failed: {status_resp.get('error')}")
            if status == "cancelled":
                raise RuntimeError(f"ACP run {run_id} was cancelled")

        raise TimeoutError(f"ACP run {run_id} did not complete within {max_wait}s")

    def cancel(self, run_id: str) -> None:
        self._request("DELETE", f"/runs/{run_id}")


# ── acp_tool factory ──────────────────────────────────────────────────────────

def acp_tool(base_url: str, name: str = "", description: str = "") -> Any:
    """Wrap a remote ACP endpoint as a MeshFlow @tool.

    Usage::

        from meshflow.acp import acp_tool
        from meshflow import Agent

        beeai = acp_tool("http://beeai-host:8001", name="beeai_researcher")
        agent = Agent(name="planner", role="planner", tools=[beeai])
        result = await agent.run("Research LLM governance papers")

    The tool discovers the remote agent card on first call and uses
    its name/description if not overridden.
    """
    from meshflow.tools.registry import tool as _tool, RiskTier  # type: ignore[attr-defined]

    client = ACPClient(base_url)

    # Discover remote agent to get name/description
    _name = name
    _desc = description
    try:
        card = client.discover()
        _name = _name or card.get("name", "acp_agent")
        _desc = _desc or card.get("description", f"Remote ACP agent at {base_url}")
    except Exception:
        _name = _name or "acp_agent"
        _desc = _desc or f"Remote ACP agent at {base_url}"

    @_tool(name=_name, description=_desc, risk=RiskTier.EXTERNAL_IO)
    async def _acp_call(task: str) -> str:
        """Call the remote ACP agent."""
        try:
            return await client.run(task)
        except Exception as exc:
            return f"[acp_error: {exc}]"

    return _acp_call


__all__ = ["ACPServer", "ACPClient", "ACPAgentCard", "acp_tool"]
