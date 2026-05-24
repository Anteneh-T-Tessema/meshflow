"""A2A HTTP server with full task lifecycle and SSE streaming.

Endpoints
---------
GET  /.well-known/agent-card   — AgentCard (capability manifest)
GET  /health                   — liveness probe
POST /run                      — legacy synchronous execution (Sprint 29 compat)
POST /tasks                    — submit async task → {"task_id", "state"}
GET  /tasks                    — list recent tasks
GET  /tasks/{id}               — poll task state
GET  /tasks/{id}/stream        — SSE stream of state transitions

Task lifecycle::

    submitted → working → completed
                        → failed

Uses only stdlib (http.server, threading, asyncio).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from .protocol import A2AResponse, AgentCard
from .tasks import A2ATask, A2ATaskStore, TaskState

if TYPE_CHECKING:
    from meshflow.agents.builder import Agent


class A2AServer:
    """Serve a MeshFlow :class:`~meshflow.agents.builder.Agent` over A2A HTTP.

    Parameters
    ----------
    agent:       The MeshFlow agent to expose.
    host:        Bind address (default ``127.0.0.1``).
    port:        TCP port (default ``8080``).
    description: Human-readable description surfaced in the AgentCard.

    Usage::

        with A2AServer(agent, port=8080) as srv:
            client = A2AClient("http://127.0.0.1:8080")
            # legacy sync
            resp = client.run("What is 2+2?")
            # async task lifecycle
            task_id = client.submit("What is 2+2?")
            task    = client.wait(task_id)
            print(task.result)
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
        self._store = A2ATaskStore()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def card(self) -> AgentCard:
        return AgentCard(
            name=self.agent.name,
            description=self.description,
            url=self.url,
            capabilities=["run", "tasks", "stream", "metrics"],
            version="2.0",
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        handler_cls = self._make_handler()
        self._server = HTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name=f"a2a-{self.agent.name}",
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

    # ── Request handler factory ────────────────────────────────────────────────

    def _make_handler(self) -> type:
        agent = self.agent
        store = self._store
        card_bytes = json.dumps(self.card().to_dict()).encode()

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass  # silence access logs

            # ── helpers ───────────────────────────────────────────────────────

            def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                return json.loads(raw) if raw else {}

            def _run_agent(self, content: str, context: dict[str, Any]) -> dict[str, Any]:
                loop = asyncio.new_event_loop()
                try:
                    built = agent._build()
                    return loop.run_until_complete(built.step(content, context))
                finally:
                    loop.close()

            # ── GET routes ───────────────────────────────────────────────────

            def do_GET(self) -> None:
                path = self.path.split("?")[0].rstrip("/")

                if path in ("/.well-known/agent-card", "/agent-card"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(card_bytes)))
                    self.end_headers()
                    self.wfile.write(card_bytes)

                elif path == "/health":
                    self._send_json({"status": "ok", "agent": agent.name})

                elif path == "/ready":
                    self._send_json({"status": "ready", "agent": agent.name, "tasks": len(store.list(1000))})

                elif path == "/metrics":
                    try:
                        from meshflow.observability.metrics import MetricsCollector
                        body = MetricsCollector.get().prometheus_text().encode()
                    except Exception as exc:
                        body = f"# error generating metrics: {exc}\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                elif path == "/tasks":
                    tasks = [t.to_dict() for t in store.list(50)]
                    self._send_json({"tasks": tasks, "count": len(tasks)})

                elif path.startswith("/tasks/"):
                    parts = path.split("/")
                    # /tasks/{id}  or  /tasks/{id}/stream
                    if len(parts) == 3:
                        # poll
                        task_id = parts[2]
                        task = store.get(task_id)
                        if task is None:
                            self._send_json({"error": "task not found"}, 404)
                        else:
                            self._send_json(task.to_dict())
                    elif len(parts) == 4 and parts[3] == "stream":
                        task_id = parts[2]
                        self._sse_stream(task_id)
                    else:
                        self._send_json({"error": "not found"}, 404)

                else:
                    self._send_json({"error": "not found"}, 404)

            # ── POST routes ──────────────────────────────────────────────────

            def do_POST(self) -> None:
                path = self.path.split("?")[0].rstrip("/")

                if path == "/run":
                    self._handle_legacy_run()
                elif path == "/tasks":
                    self._handle_submit_task()
                else:
                    self._send_json({"error": "not found"}, 404)

            # ── Legacy /run (Sprint 29 compat) ───────────────────────────────

            def _handle_legacy_run(self) -> None:
                try:
                    msg = self._read_body()
                    content = msg.get("content", "")
                    context = msg.get("context", {})
                    result = self._run_agent(content, context)
                    resp = A2AResponse(
                        content=result.get("result", result.get("content", "")),
                        agent_name=agent.name,
                        tokens=result.get("tokens", 0),
                        cost_usd=result.get("cost_usd", 0.0),
                        blocked=result.get("blocked", False),
                    )
                    self._send_json(resp.to_dict())
                except Exception as exc:
                    self._send_json(A2AResponse(content="", error=str(exc)).to_dict(), 500)

            # ── Async task submission ────────────────────────────────────────

            def _handle_submit_task(self) -> None:
                try:
                    msg = self._read_body()
                    content = msg.get("content", "")
                    context = msg.get("context", {})
                    task = A2ATask(
                        content=content,
                        agent_name=agent.name,
                        context=context,
                    )
                    task.transition(TaskState.submitted)
                    store.put(task)
                    # Execute in background thread
                    threading.Thread(
                        target=_execute_task,
                        args=(task, agent, store),
                        daemon=True,
                    ).start()
                    self._send_json(task.to_dict(), 202)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 500)

            # ── SSE streaming ────────────────────────────────────────────────

            def _sse_stream(self, task_id: str) -> None:
                task = store.get(task_id)
                if task is None:
                    self._send_json({"error": "task not found"}, 404)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                # Send current state immediately
                self._write_sse(task)
                if task.is_terminal():
                    return

                eq = store.subscribe(task_id)
                try:
                    for updated in eq.iter_until_done(poll_timeout=1.0):
                        self._write_sse(updated)
                        if updated.is_terminal():
                            break
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    eq.close()

            def _write_sse(self, task: A2ATask) -> None:
                data = json.dumps(task.to_dict())
                event = f"data: {data}\n\n".encode()
                try:
                    self.wfile.write(event)
                    self.wfile.flush()
                except (BrokenPipeError, OSError):
                    pass

        return _Handler


# ── Background task executor ───────────────────────────────────────────────────

def _execute_task(task: A2ATask, agent: "Agent", store: A2ATaskStore) -> None:
    """Run agent step in a background thread; update task state in store."""
    task.transition(TaskState.working)
    store.put(task)
    try:
        loop = asyncio.new_event_loop()
        try:
            built = agent._build()
            result = loop.run_until_complete(built.step(task.content, task.context))
        finally:
            loop.close()

        task.result = result.get("result", result.get("content", ""))
        task.tokens = result.get("tokens", 0)
        task.cost_usd = result.get("cost_usd", 0.0)
        task.transition(
            TaskState.failed if result.get("blocked") else TaskState.completed
        )
        if result.get("blocked"):
            task.error = result.get("guardrail_reason", "blocked")
    except Exception as exc:
        task.error = str(exc)
        task.transition(TaskState.failed)

    store.put(task)
