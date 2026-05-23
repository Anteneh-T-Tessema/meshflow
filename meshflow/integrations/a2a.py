"""Google A2A (Agent-to-Agent) Protocol — client and server.

A2A is an open standard for agent interoperability: any A2A-compliant agent
can call any other, regardless of the framework that built it.

Spec: https://google.github.io/A2A

  A2AClient  — call any A2A-compliant agent as a MeshFlow Tool or Agent
  A2AServer  — expose a MeshFlow Team/Agent as an A2A endpoint
  agent_from_a2a(url) — import a remote A2A agent as a MeshFlow Agent

Usage — calling an external A2A agent:
    from meshflow.integrations.a2a import A2AClient, agent_from_a2a

    client = A2AClient("https://research-agent.example.com")
    card = await client.agent_card()
    result = await client.run("Summarise the latest AI safety papers")

Usage — exposing MeshFlow as A2A:
    from meshflow.integrations.a2a import A2AServer

    server = A2AServer(
        team=my_team,
        name="MeshFlow Research Agent",
        description="A governed research pipeline",
        port=8080,
    )
    await server.serve()
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ── A2A data models ───────────────────────────────────────────────────────────


@dataclass
class AgentCard:
    """A2A Agent Card — describes an agent's identity and capabilities."""

    name: str
    description: str
    url: str
    version: str = "1.0"
    capabilities: list[str] = field(default_factory=list)
    skills: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "protocolVersion": "0.2.1",
        }


@dataclass
class A2ATask:
    """A2A Task — a unit of work submitted to an agent."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    state: str = "submitted"  # submitted | working | completed | failed | canceled
    result: str = ""
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": {"state": self.state},
            "artifacts": [{"parts": [{"type": "text", "text": self.result}]}]
            if self.result
            else [],
            "error": self.error or None,
            "createdAt": self.created_at,
            "completedAt": self.completed_at or None,
        }


# ── A2A Client ────────────────────────────────────────────────────────────────


class A2AClient:
    """Call any A2A-compliant agent from MeshFlow.

    Implements the A2A JSON-RPC 2.0 protocol over HTTP.
    The remote agent is available as a MeshFlow Tool via .as_tool() or
    as a MeshFlow Agent via agent_from_a2a(url).
    """

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def agent_card(self) -> AgentCard:
        """Fetch the remote agent's Agent Card."""
        data = await self._get("/.well-known/agent.json")
        return AgentCard(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            url=self._base,
            version=data.get("version", "1.0"),
            capabilities=data.get("capabilities", []),
            skills=data.get("skills", []),
        )

    async def run(self, message: str, context: dict[str, Any] | None = None) -> str:
        """Submit a task and wait for completion."""
        task_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": task_id,
            "method": "tasks/send",
            "params": {
                "id": task_id,
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": message}],
                },
                "metadata": context or {},
            },
        }
        result = await self._post("/", payload)
        return self._extract_result(result)

    async def send_and_poll(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        poll_interval: float = 1.0,
        max_polls: int = 60,
    ) -> str:
        """Submit a task and poll until completed (for long-running agents)."""
        task_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": task_id,
            "method": "tasks/send",
            "params": {
                "id": task_id,
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": message}],
                },
            },
        }
        await self._post("/", payload)

        for _ in range(max_polls):
            await asyncio.sleep(poll_interval)
            result = await self._post(
                "/",
                {
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "tasks/get",
                    "params": {"id": task_id},
                },
            )
            state = result.get("result", {}).get("status", {}).get("state", "working")
            if state in ("completed", "failed", "canceled"):
                return self._extract_result(result)

        return "[A2A] Task timed out."

    def as_tool(self) -> Any:
        """Return this A2A agent as a MeshFlow Tool."""
        from meshflow.tools.registry import Tool
        from meshflow.core.schemas import RiskTier

        async def _call(message: str) -> str:
            return await self.run(message)

        return Tool(
            name=f"a2a_{self._base.split('/')[-1] or 'agent'}",
            description=f"A2A agent at {self._base}",
            fn=_call,
            risk=RiskTier.EXTERNAL_IO,
            tags=["a2a", "external"],
        )

    async def _get(self, path: str) -> dict[str, Any]:
        import urllib.request

        url = self._base + path
        try:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(url, timeout=self._timeout).read(),
            )
            return cast(dict[str, Any], json.loads(raw))
        except Exception as e:
            raise RuntimeError(f"A2A GET {url} failed: {e}") from e

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import urllib.request

        url = self._base + path
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=self._timeout).read(),
            )
            return cast(dict[str, Any], json.loads(raw))
        except Exception as e:
            raise RuntimeError(f"A2A POST {url} failed: {e}") from e

    def _extract_result(self, response: dict[str, Any]) -> str:
        result = response.get("result", {})
        artifacts = result.get("artifacts", [])
        if artifacts:
            parts = artifacts[0].get("parts", [])
            if parts:
                return str(parts[0].get("text", str(parts[0])))
        if "error" in response:
            return f"[A2A error] {response['error']}"
        return str(result)


# ── A2A Server ────────────────────────────────────────────────────────────────


class A2AServer:
    """Expose a MeshFlow Team or Agent as an A2A-compliant HTTP endpoint.

    Any A2A client (from any framework) can call this agent over HTTP.
    Implements JSON-RPC 2.0 + Agent Card discovery.
    """

    def __init__(
        self,
        team: Any,
        name: str = "MeshFlow Agent",
        description: str = "A governed MeshFlow multi-agent system.",
        host: str = "0.0.0.0",
        port: int = 8080,
        version: str = "1.0",
    ) -> None:
        self._team = team
        self._card = AgentCard(
            name=name,
            description=description,
            url=f"http://{host}:{port}",
            version=version,
            capabilities=["tasks/send", "tasks/get"],
            skills=[{"id": "run", "name": "Run a task", "description": description}],
        )
        self._host = host
        self._port = port
        self._tasks: dict[str, A2ATask] = {}

    async def serve(self) -> None:
        """Start the A2A HTTP server (asyncio-based, no extra dependencies)."""
        import asyncio.streams

        server = await asyncio.start_server(self._handle_connection, self._host, self._port)
        print(f"[A2A] MeshFlow agent listening on {self._host}:{self._port}")
        print(f"[A2A] Agent Card: http://{self._host}:{self._port}/.well-known/agent.json")
        async with server:
            await server.serve_forever()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.read(65536)
            request = raw.decode(errors="replace")
            lines = request.split("\r\n")
            method_path = lines[0].split(" ") if lines else []
            method = method_path[0] if method_path else "GET"
            path = method_path[1] if len(method_path) > 1 else "/"

            body_start = request.find("\r\n\r\n")
            body = request[body_start + 4 :] if body_start >= 0 else ""

            response_body, status = await self._route(method, path, body)
            response = (
                f"HTTP/1.1 {status}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(response_body)}\r\n"
                f"Access-Control-Allow-Origin: *\r\n"
                f"\r\n"
                f"{response_body}"
            )
            writer.write(response.encode())
            await writer.drain()
        except Exception as e:
            err = json.dumps({"error": str(e)})
            writer.write(f"HTTP/1.1 500\r\nContent-Type: application/json\r\n\r\n{err}".encode())
        finally:
            writer.close()

    async def _route(self, method: str, path: str, body: str) -> tuple[str, str]:
        if path == "/.well-known/agent.json":
            return json.dumps(self._card.to_dict()), "200 OK"

        if method == "POST" and path in ("/", "/rpc"):
            try:
                payload = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                return json.dumps({"error": "invalid JSON"}), "400 Bad Request"

            rpc_method = payload.get("method", "")
            params = payload.get("params", {})
            rpc_id = payload.get("id", str(uuid.uuid4()))

            if rpc_method == "tasks/send":
                result = await self._handle_send(params)
                return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result}), "200 OK"
            if rpc_method == "tasks/get":
                result = await self._handle_get(params)
                return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result}), "200 OK"

            return json.dumps({"error": f"Unknown method: {rpc_method}"}), "400 Bad Request"

        return json.dumps({"error": "Not found"}), "404 Not Found"

    async def _handle_send(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("id", str(uuid.uuid4()))
        message_obj = params.get("message", {})
        parts = message_obj.get("parts", []) if isinstance(message_obj, dict) else []
        text = ""
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                break
        if not text and isinstance(message_obj, str):
            text = message_obj

        task = A2ATask(id=task_id, message=text, state="working")
        self._tasks[task_id] = task

        try:
            result = await self._team.run(text)
            task.result = result.output if hasattr(result, "output") else str(result)
            task.state = "completed"
            task.completed_at = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            task.state = "failed"
            task.error = str(e)

        return task.to_dict()

    async def _handle_get(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("id", "")
        task = self._tasks.get(task_id)
        if not task:
            return {"error": f"Task '{task_id}' not found"}
        return task.to_dict()


# ── Convenience function ──────────────────────────────────────────────────────


def agent_from_a2a(url: str, name: str | None = None, policy: Any = None) -> Any:
    """Import a remote A2A agent as a MeshFlow Agent.

    The remote agent is called via HTTP on each step.
    """
    from meshflow.agents.builder import Agent
    from meshflow.core.schemas import RiskTier

    client = A2AClient(url)
    agent_name = name or f"a2a_{url.split('/')[-1] or 'agent'}"

    async def _runner(task: str, context: dict[str, Any]) -> Any:
        from meshflow.core.node import NodeOutput

        result = await client.run(task, context)
        return NodeOutput(content=result, confidence=0.8)

    from meshflow.core.node import MeshNode

    node = MeshNode.from_callable(
        agent_name,
        _runner,
        risk=RiskTier.EXTERNAL_IO,
        capabilities=["a2a", "external"],
    )

    a = Agent(name=agent_name, role="executor", policy=policy)
    a._prebuilt_node = node
    return a
