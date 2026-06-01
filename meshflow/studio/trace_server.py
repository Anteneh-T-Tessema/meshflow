"""MeshFlow Visual Trace Server — LangSmith-style browser UI for run traces.

Serves a single-page trace UI that reads from the ReplayLedger and exposes:

  GET  /                        → trace.html
  GET  /api/runs                → recent run list
  GET  /api/trace/<run_id>      → full step-by-step trace JSON
  GET  /api/live/<run_id>       → SSE stream polling for new steps
  POST /api/rewind              → trigger rewind to a checkpoint

Usage::

    from meshflow.studio.trace_server import TraceServer

    server = TraceServer(db="meshflow_runs.db", port=7788)
    server.start()              # starts in background thread
    server.open_browser("run-abc123")
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import threading
import time
import webbrowser
from typing import Any, cast
from urllib.parse import urlparse, parse_qs


_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "studio", "templates")

# In-memory fork count overrides (seeded from curated templates, incremented by POST /api/templates/fork/<name>)
_fork_counts: dict[str, int] = {}


class _TraceHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for the trace server."""

    server_instance: "TraceServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # silence default access log

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, path: str) -> None:
        try:
            with open(path, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/", "/trace"):
            self._html(os.path.join(_TEMPLATE_DIR, "trace.html"))
            return

        if path in ("/graph", "/graph/"):
            self._html(os.path.join(_TEMPLATE_DIR, "graph.html"))
            return

        if path in ("/rag", "/rag/"):
            self._html(os.path.join(_TEMPLATE_DIR, "rag_builder.html"))
            return

        if path in ("/templates", "/templates/"):
            self._html(os.path.join(_TEMPLATE_DIR, "templates.html"))
            return

        if path == "/api/curated-templates":
            from meshflow.registry.curated_templates import CURATED_TEMPLATES
            curated_data = []
            for t in CURATED_TEMPLATES:
                d = t.to_dict()
                d["fork_count"] = _fork_counts.get(t.name, t.fork_count)
                curated_data.append(d)
            self._json(curated_data)
            return

        if path.startswith("/api/graph/"):
            run_id = path[len("/api/graph/"):]
            data = asyncio.run(self.server_instance.get_mermaid(run_id))
            self._json(data)
            return

        if path == "/api/runs":
            data = asyncio.run(self.server_instance.get_runs())  # type: ignore[arg-type]
            self._json(data)
            return

        if path.startswith("/api/trace/"):
            run_id = path[len("/api/trace/"):]
            data = asyncio.run(self.server_instance.get_trace(run_id))  # type: ignore[arg-type]
            if data is None:
                self._json({"error": f"run_id '{run_id}' not found"}, 404)
            else:
                self._json(data)
            return

        if path.startswith("/api/live/"):
            run_id = path[len("/api/live/"):]
            qs = parse_qs(parsed.query)
            since = int(qs.get("since", ["0"])[0])
            self._sse_stream(run_id, since)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        path = self.path.rstrip("/")

        if path == "/api/rewind":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            result = asyncio.run(self.server_instance.do_rewind(body))
            self._json(result)
            return

        if path.startswith("/api/templates/fork/"):
            name = path[len("/api/templates/fork/"):]
            from meshflow.registry.curated_templates import CURATED_TEMPLATES
            base = next((t.fork_count for t in CURATED_TEMPLATES if t.name == name), 0)
            _fork_counts[name] = _fork_counts.get(name, base) + 1
            self._json({"name": name, "fork_count": _fork_counts[name]})
            return

        self.send_error(404)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _sse_stream(self, run_id: str, since: int) -> None:
        """Poll the ledger every 800 ms and stream new steps as SSE events."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        sent = since
        deadline = time.time() + 60  # max 60s stream then client reconnects

        while time.time() < deadline:
            try:
                steps = asyncio.run(self.server_instance.get_steps_since(run_id, sent))
                if not steps:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                else:
                    for step in steps:
                        payload = json.dumps(step, default=str)
                        msg = f"data: {payload}\n\n".encode()
                        self.wfile.write(msg)
                        self.wfile.flush()
                        sent += 1
                time.sleep(0.8)
            except (BrokenPipeError, ConnectionResetError):
                break

        try:
            self.wfile.write(b"data: {\"done\": true}\n\n")
            self.wfile.flush()
        except Exception:
            pass


class TraceServer:
    """Lightweight HTTP server for the visual trace UI.

    Parameters
    ----------
    db:     Path to the MeshFlow SQLite ledger.
    port:   Local port to listen on (default 7788).
    """

    def __init__(self, db: str = "meshflow_runs.db", port: int = 7788) -> None:
        self._db = db
        self._port = port
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── Server lifecycle ───────────────────────────────────────────────────────

    def start(self, daemon: bool = True) -> None:
        """Start the server in a background daemon thread."""

        class BoundHandler(_TraceHandler):
            server_instance = self

        self._server = http.server.HTTPServer(("127.0.0.1", self._port), BoundHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=daemon)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    def open_browser(self, run_id: str = "") -> None:
        url = f"http://127.0.0.1:{self._port}"
        if run_id:
            url += f"?run_id={run_id}"
        webbrowser.open(url)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    # ── Data methods (called from request handlers) ────────────────────────────

    async def get_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent runs from the ledger as enriched dicts."""
        try:
            from meshflow.core.ledger import ReplayLedger
            ledger = ReplayLedger(self._db)
            run_ids: list[str] = await ledger.list_runs()
            result: list[dict[str, Any]] = []
            for rid in run_ids[:limit]:
                try:
                    summary = await ledger.run_summary(rid)
                    result.append({"run_id": rid, **summary})
                except Exception:
                    result.append({"run_id": rid})
            return result
        except Exception as exc:
            return [{"error": str(exc)}]

    async def get_trace(self, run_id: str) -> dict[str, Any] | None:
        """Return full structured trace for a run."""
        try:
            from meshflow.core.ledger import ReplayLedger
            ledger = ReplayLedger(self._db)
            steps = await ledger.get_run(run_id)
            if not steps:
                return None
            summary = await ledger.run_summary(run_id)
            chain = await ledger.verify_chain(run_id)

            # Build structured step list with state diffs
            enriched = []
            for i, step in enumerate(steps):
                entry: dict[str, Any] = {
                    "idx": i + 1,
                    "step_id": step.get("step_id", ""),
                    "node_id": step.get("node_id", ""),
                    "node_kind": step.get("node_kind", ""),
                    "input_preview": (step.get("input_task", "") or "")[:300],
                    "output_preview": (step.get("output_content", "") or "")[:500],
                    "verdict": step.get("verdict", "commit"),
                    "blocked": bool(step.get("blocked", False)),
                    "block_reason": step.get("block_reason", ""),
                    "uncertainty": round(float(step.get("uncertainty", 0)), 4),
                    "cost_usd": round(float(step.get("cost_usd", 0)), 6),
                    "tokens_used": int(step.get("tokens_used", 0)),
                    "carbon_gco2": round(float(step.get("carbon_gco2", 0)), 6),
                    "duration_ms": round(float(step.get("duration_ms", 0)), 1),
                    "timestamp": step.get("timestamp", ""),
                    "entry_hash": step.get("entry_hash", ""),
                }
                enriched.append(entry)

            # Waterfall: compute relative start times from duration_ms
            t = 0.0
            for s in enriched:
                s["start_ms"] = round(t, 1)
                t += s["duration_ms"]

            return {
                "run_id": run_id,
                "summary": summary,
                "chain_valid": chain.get("valid", True),
                "chain_errors": chain.get("errors", []),
                "total_duration_ms": round(t, 1),
                "steps": enriched,
            }
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id, "steps": []}

    async def get_steps_since(self, run_id: str, since_idx: int) -> list[dict[str, Any]]:
        """Return steps after *since_idx* for live streaming."""
        trace = await self.get_trace(run_id)
        if not trace or "steps" not in trace:
            return []
        steps = trace["steps"][since_idx:]
        return cast(list[dict[str, Any]], steps)

    async def get_mermaid(self, run_id: str) -> dict[str, Any]:
        """Return a Mermaid graph definition for a run's execution topology."""
        try:
            from meshflow.core.ledger import ReplayLedger
            ledger = ReplayLedger(self._db)
            steps = await ledger.get_run(run_id)
            if not steps:
                return {"mermaid": "graph LR\n  empty[No steps found]", "nodes": [], "edges": []}

            nodes: list[dict[str, Any]] = []
            edges: list[dict[str, Any]] = []
            seen: set[str] = set()
            prev_node: str = ""

            for step in steps:
                node_id = step.get("node_id", "unknown")
                node_kind = step.get("node_kind", "native")
                blocked = step.get("blocked", False)
                cost = step.get("cost_usd", 0.0)
                tokens = step.get("tokens_used", 0)
                latency = step.get("duration_ms", 0.0)

                if node_id not in seen:
                    shape_open, shape_close = ("[", "]")
                    if node_kind == "human":
                        shape_open, shape_close = (">", "]")
                    elif blocked:
                        shape_open, shape_close = ("{", "}")
                    nodes.append({
                        "id": node_id,
                        "kind": node_kind,
                        "blocked": blocked,
                        "cost_usd": cost,
                        "tokens": tokens,
                        "latency_ms": latency,
                        "shape_open": shape_open,
                        "shape_close": shape_close,
                    })
                    seen.add(node_id)

                if prev_node and prev_node != node_id:
                    edges.append({"from": prev_node, "to": node_id})
                prev_node = node_id

            # Build Mermaid syntax
            lines = ["graph TD"]
            for n in nodes:
                label = (
                    f"{n['id']}\\n"
                    f"{n['kind']} | "
                    f"${n['cost_usd']:.4f} | "
                    f"{int(n['latency_ms'])}ms"
                )
                safe_id = n["id"].replace("-", "_").replace(".", "_")
                lines.append(f"  {safe_id}{n['shape_open']}\"{label}\"{n['shape_close']}")
                if n["blocked"]:
                    lines.append(f"  style {safe_id} fill:#f97316,color:#fff")
                else:
                    lines.append(f"  style {safe_id} fill:#6366f1,color:#fff")

            for e in edges:
                src = e["from"].replace("-", "_").replace(".", "_")
                dst = e["to"].replace("-", "_").replace(".", "_")
                lines.append(f"  {src} --> {dst}")

            return {
                "mermaid": "\n".join(lines),
                "nodes": nodes,
                "edges": edges,
                "run_id": run_id,
            }
        except Exception as exc:
            return {"error": str(exc), "mermaid": f"graph LR\n  err[Error: {exc}]"}

    async def do_rewind(self, body: dict[str, Any]) -> dict[str, Any]:
        """Trigger a rewind-and-re-run from a checkpoint."""
        run_id = body.get("run_id", "")
        step_idx = int(body.get("step_idx", 0))
        model_override = body.get("model_override", "")
        prompt_override = body.get("prompt_override", "")

        try:
            from meshflow.core.time_travel import RewindEngine
            engine = RewindEngine(ledger_db=self._db)
            result = await engine.rewind(
                run_id,
                step_idx,
                model_override=model_override,
                prompt_override=prompt_override,
            )
            return {
                "ok": True,
                "new_run_id": result.rewind_run_id,
                "steps_replayed": result.steps_replayed,
                "output": result.output[:500],
                "total_cost_usd": result.total_cost_usd,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
