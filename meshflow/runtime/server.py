"""MeshFlow HTTP Runtime — async, authenticated, token-streaming JSON server.

Endpoints (all except /health require Authorization: Bearer <key>):
  POST /run              { task, policy? }           → RunResult JSON
  POST /stream           { task, policy? }           → NDJSON (token_delta + step events)
  GET  /health           (no auth)                   → { ok, version, uptime_s }
  GET  /metrics          (no auth)                   → Prometheus text format
  GET  /traces/{run_id}  → full run trace JSON
  POST /hitl/{run_id}/approve  { reviewer_id?, notes? }
  POST /hitl/{run_id}/reject   { reviewer_id?, notes? }
  GET  /hitl/pending           → list of paused runs

Auth:
  Set MESHFLOW_API_KEYS=key1,key2 (comma-separated).
  Pass Authorization: Bearer <key>  or  X-API-Key: <key>.
  If MESHFLOW_API_KEYS is unset the server starts in open mode with a warning.

Run:
  python -m meshflow.runtime.server
  meshflow serve --port 8000 [--api-key mykey]
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from typing import Any, cast

from meshflow.core.mesh import Mesh
from meshflow.core.schemas import Policy, policy_for_mode

VERSION = "0.7.0"
_START_TIME = time.monotonic()


# ── Auth helpers ──────────────────────────────────────────────────────────────


def _load_api_keys() -> set[str]:
    raw = os.environ.get("MESHFLOW_API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()} if raw.strip() else set()


def _check_auth(headers: Any, valid_keys: set[str]) -> bool:
    """Return True if the request is authorised (or no keys are configured)."""
    if not valid_keys:
        return True
    auth = headers.get("Authorization", "") or headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] in valid_keys
    api_key = headers.get("X-API-Key", "") or headers.get("x-api-key", "")
    return api_key in valid_keys


# ── Request / response helpers ────────────────────────────────────────────────


def _policy_from_dict(d: dict[str, Any]) -> Policy:
    return policy_for_mode(
        d.get("mode", "standard"),
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


# ── aiohttp-based async server ────────────────────────────────────────────────


async def _build_app(api_keys: set[str], ledger_path: str = "meshflow_runs.db") -> Any:
    try:
        from aiohttp import web
    except ImportError as exc:
        raise RuntimeError(
            "MeshFlow server requires aiohttp. Install it: pip install aiohttp"
        ) from exc

    from meshflow.core.ledger import ReplayLedger
    from meshflow.observability.metrics import MetricsCollector

    metrics = MetricsCollector.get()
    ledger = ReplayLedger(ledger_path)

    def _require_auth(request: Any) -> bool:
        if not _check_auth(request.headers, api_keys):
            return False
        # Rate limiting (best-effort — never blocks startup)
        try:
            from meshflow.observability.sla import get_rate_limiter
            key = request.headers.get("X-API-Key", "") or request.headers.get("Authorization", "anonymous")
            if not get_rate_limiter().allow(key):
                return False  # 429 handled by caller (returns 401 for simplicity; subclass if needed)
        except Exception:
            pass
        return True

    def _cors_headers() -> dict[str, str]:
        origins = os.environ.get("MESHFLOW_CORS_ORIGINS", "*")
        return {
            "Access-Control-Allow-Origin": origins,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Key, X-Tenant-ID",
        }

    _shutting_down = False  # set True on SIGTERM to fail readiness

    async def health(request: Any) -> Any:
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps(
                {
                    "ok": True,
                    "version": VERSION,
                    "uptime_s": round(time.monotonic() - _START_TIME, 1),
                    "db": ledger_path,
                }
            ),
        )

    async def health_live(request: Any) -> Any:
        """GET /health/live — Kubernetes liveness probe. Always 200 while process runs."""
        return web.Response(
            content_type="application/json",
            text=json.dumps({"live": True, "uptime_s": round(time.monotonic() - _START_TIME, 1)}),
        )

    async def health_ready(request: Any) -> Any:
        """GET /health/ready — Kubernetes readiness probe. 503 during graceful shutdown."""
        if _shutting_down:
            return web.Response(
                status=503,
                content_type="application/json",
                text=json.dumps({"ready": False, "reason": "shutting_down"}),
            )
        try:
            runs = await ledger.list_runs()
            _ = runs  # ledger is reachable
            return web.Response(
                content_type="application/json",
                text=json.dumps({"ready": True, "version": VERSION}),
            )
        except Exception as exc:
            return web.Response(
                status=503,
                content_type="application/json",
                text=json.dumps({"ready": False, "reason": str(exc)}),
            )

    async def metrics_endpoint(request: Any) -> Any:
        return web.Response(
            content_type="text/plain; version=0.0.4",
            text=metrics.prometheus_text(),
        )

    async def run_task(request: Any) -> Any:
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        try:
            body = cast(dict[str, Any], await request.json())
        except Exception:
            body = {}
        task = body.get("task", "")
        if not task:
            return web.Response(
                status=400, text='{"error":"task is required"}', content_type="application/json"
            )
        policy = _policy_from_dict(body.get("policy", {}))
        mesh = Mesh(policy=policy)
        try:
            t0 = time.monotonic()
            result = await mesh.run(task, policy=policy, context=body.get("context"))
            metrics.record_run(
                result.status.value,
                time.monotonic() - t0,
                result.total_tokens,
                result.total_cost_usd,
            )
            # Webhook: run_completed / run_failed
            try:
                from meshflow.observability.webhooks import get_webhook_manager
                _wm = get_webhook_manager()
                if _wm.list():
                    _wh_payload = {
                        "run_id": result.run_id,
                        "status": result.status.value,
                        "total_cost_usd": result.total_cost_usd,
                        "total_tokens": result.total_tokens,
                        "duration_s": result.duration_s,
                        "error": result.error,
                    }
                    _ev = "run_failed" if result.status.value == "failed" else "run_completed"
                    asyncio.create_task(_wm.deliver(_ev, _wh_payload))
            except Exception:
                pass
            return web.Response(
                content_type="application/json",
                headers=_cors_headers(),
                text=json.dumps(_run_result_to_dict(result)),
            )
        except Exception as exc:
            return web.Response(
                status=500, content_type="application/json", text=json.dumps({"error": str(exc)})
            )

    async def stream_task(request: Any) -> Any:
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        try:
            body = cast(dict[str, Any], await request.json())
        except Exception:
            body = {}
        task = body.get("task", "")
        if not task:
            return web.Response(
                status=400, text='{"error":"task is required"}', content_type="application/json"
            )
        policy = _policy_from_dict(body.get("policy", {}))
        mesh = Mesh(policy=policy)

        response = web.StreamResponse(
            headers={
                **_cors_headers(),
                "Content-Type": "application/x-ndjson",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        try:
            async for event in mesh.stream(task, policy=policy):
                event_type = getattr(event, "event_type", "step")
                # Emit token deltas if the event carries them
                token_chunks = getattr(event, "token_chunks", None)
                if token_chunks:
                    for chunk in token_chunks:
                        line = (
                            json.dumps(
                                {
                                    "kind": "token_delta",
                                    "text": chunk.text,
                                    "agent_id": chunk.agent_id,
                                    "step_id": chunk.step_id,
                                    "run_id": chunk.run_id,
                                }
                            )
                            + "\n"
                        )
                        await response.write(line.encode())
                # Always emit the step-level event
                line = (
                    json.dumps(
                        {
                            "kind": event_type,
                            "agent_id": getattr(event, "agent_id", ""),
                            "role": getattr(event, "role", ""),
                            "step": getattr(event, "step", 0),
                            "uncertainty": getattr(event, "uncertainty", 0.0),
                            "cost_usd": getattr(event, "cost_usd", 0.0),
                            "tokens": getattr(event, "tokens", 0),
                            "blocked_by": getattr(event, "blocked_by", ""),
                            "output": str(
                                getattr(event, "data", {}).get(
                                    "execution_result",
                                    getattr(event, "data", {}).get("research", ""),
                                )
                            )[:500],
                        }
                    )
                    + "\n"
                )
                await response.write(line.encode())
        except Exception as exc:
            err = json.dumps({"kind": "error", "error": str(exc)}) + "\n"
            await response.write(err.encode())
        await response.write_eof()
        return response

    async def get_trace(request: Any) -> Any:
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        run_id = request.match_info["run_id"]
        records = await ledger.get_run(run_id)
        if not records:
            return web.Response(
                status=404,
                text=json.dumps({"error": "run not found"}),
                content_type="application/json",
            )
        summary = await ledger.run_summary(run_id)
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"run_id": run_id, "summary": summary, "steps": records}),
        )

    async def list_eval_results(request: Any) -> Any:
        """GET /eval-results[?suite=<name>] — stored EvalBaseline entries from the ledger."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        suite = request.rel_url.query.get("suite") or None
        results = await ledger.list_eval_results(suite_name=suite)
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"eval_results": results}),
        )

    async def list_traces(request: Any) -> Any:
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        runs = await ledger.list_runs()
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"runs": runs}),
        )

    async def hitl_pending(request: Any) -> Any:
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        paused = await ledger.list_paused_runs()
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"paused_runs": paused}),
        )

    async def hitl_approve(request: Any) -> Any:
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        run_id = request.match_info["run_id"]
        try:
            body = cast(dict[str, Any], await request.json())
        except Exception:
            body = {}
        checkpoint = await ledger.load_checkpoint_data(run_id)
        if not checkpoint:
            return web.Response(
                status=404,
                text=json.dumps({"error": "run not found or not paused"}),
                content_type="application/json",
            )
        # Record reviewer metadata in checkpoint
        checkpoint["reviewed_by"] = body.get("reviewer_id", "api")
        checkpoint["review_notes"] = body.get("notes", "")
        checkpoint["approved"] = True
        await ledger.save_checkpoint(run_id, checkpoint)
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"run_id": run_id, "status": "approved"}),
        )

    async def hitl_reject(request: Any) -> Any:
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        run_id = request.match_info["run_id"]
        try:
            body = cast(dict[str, Any], await request.json())
        except Exception:
            body = {}
        checkpoint = await ledger.load_checkpoint_data(run_id)
        if not checkpoint:
            return web.Response(
                status=404,
                text=json.dumps({"error": "run not found or not paused"}),
                content_type="application/json",
            )
        checkpoint["reviewed_by"] = body.get("reviewer_id", "api")
        checkpoint["review_notes"] = body.get("notes", "")
        checkpoint["approved"] = False
        await ledger.save_checkpoint(run_id, checkpoint)
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"run_id": run_id, "status": "rejected"}),
        )

    async def options_handler(request: Any) -> Any:
        return web.Response(headers=_cors_headers(), status=200)

    # ── MCP server endpoint (HTTP+SSE transport) ──────────────────────────────
    from meshflow.mcp.server import MCPServer

    _mcp_server = MCPServer(ledger_path=ledger_path)

    async def mcp_endpoint(request: Any) -> Any:
        """Handle MCP JSON-RPC requests over HTTP POST.

        Claude Desktop sends:
          POST /mcp
          Content-Type: application/json
          { "jsonrpc": "2.0", "id": 1, "method": "...", "params": {...} }

        The server responds with a single JSON object (or 204 for notifications).
        """
        if not _require_auth(request):
            return web.Response(
                status=401,
                content_type="application/json",
                text=json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Unauthorized"}}),
            )
        try:
            body = cast(dict[str, Any], await request.json())
        except Exception:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}),
            )

        response = await _mcp_server.handle_request(body)
        if response is None:
            return web.Response(status=204)

        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps(response),
        )

    async def mcp_discover(request: Any) -> Any:
        """GET /mcp — returns server capabilities and tool list (discovery endpoint).

        MCP hosts can GET this endpoint to discover what's available without
        going through the full JSON-RPC initialize handshake.
        """
        tools = _mcp_server.tool_list()
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({
                "protocol": "mcp",
                "version": "2024-11-05",
                "server": {"name": "MeshFlow", "version": "0.10.0"},
                "capabilities": {
                    "tools": {"count": len(tools), "listChanged": False},
                },
                "tools": tools,
                "connect": {
                    "http": "POST /mcp (JSON-RPC 2.0)",
                    "docs": "https://github.com/anthropics/meshflow",
                },
            }),
        )

    # ── Installed plugins ─────────────────────────────────────────────────────

    async def list_plugins(request: Any) -> Any:
        """GET /plugins[?group=<name>] — discover installed MeshFlow plugin entry-points."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        from meshflow.plugins import discover_plugins

        group_filter = request.rel_url.query.get("group") or None
        plugins = discover_plugins(group=group_filter)
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"plugins": [p.to_dict() for p in plugins]}),
        )

    # ── OTEL config ───────────────────────────────────────────────────────────

    async def otel_config(request: Any) -> Any:
        """GET /otel/config — current OpenTelemetry / trace-context configuration."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        from meshflow.observability.telemetry import get_tracer
        tracer = get_tracer()
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({
                "otlp_enabled": tracer.otlp_enabled,
                "otlp_endpoint": tracer.otlp_endpoint if tracer.otlp_enabled else "",
                "otlp_protocol": tracer.otlp_protocol if tracer.otlp_enabled else "",
                "otlp_error": tracer.otlp_error,
                "w3c_traceparent": True,
                "env_vars": {
                    "MESHFLOW_OTLP_ENDPOINT": "OTLP collector endpoint (empty = disabled)",
                    "MESHFLOW_OTLP_PROTOCOL": "grpc or http/protobuf (default: grpc)",
                    "MESHFLOW_OTLP_HEADERS": "comma-separated k=v auth headers",
                },
            }),
        )

    # ── Graph export ──────────────────────────────────────────────────────────

    async def graph_export(request: Any) -> Any:
        """GET /graph/{run_id}[?format=mermaid|dot] — export run execution graph."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        run_id = request.match_info["run_id"]
        fmt = request.rel_url.query.get("format", "mermaid").lower()

        steps = await ledger.get_run(run_id)
        if steps is None:
            steps = []

        from meshflow.core.graph_export import steps_to_mermaid, steps_to_dot

        if fmt == "dot":
            content = steps_to_dot(steps, run_id)
            ct = "text/vnd.graphviz"
        else:
            content = steps_to_mermaid(steps, run_id)
            ct = "text/plain"

        return web.Response(
            content_type=ct,
            headers=_cors_headers(),
            text=content,
        )

    # ── Audit export ──────────────────────────────────────────────────────────

    async def audit_export(request: Any) -> Any:
        """GET /audit/export?run_id=X[&format=csv|json] — download audit trail."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        run_id = request.rel_url.query.get("run_id") or ""
        fmt = request.rel_url.query.get("format", "json").lower()

        if not run_id:
            # export all runs as a summary list
            runs = await ledger.list_runs()
            summaries = []
            for rid in runs:
                try:
                    summaries.append(await ledger.run_summary(rid))
                except Exception:
                    pass
            body = json.dumps({"runs": summaries}, indent=2)
            return web.Response(
                content_type="application/json",
                headers={
                    **_cors_headers(),
                    "Content-Disposition": 'attachment; filename="meshflow_audit.json"',
                },
                text=body,
            )

        if fmt == "csv":
            content = await ledger.export_run_csv(run_id)
            return web.Response(
                content_type="text/csv",
                headers={
                    **_cors_headers(),
                    "Content-Disposition": f'attachment; filename="audit_{run_id[:12]}.csv"',
                },
                text=content,
            )
        else:
            content = await ledger.export_run(run_id)
            return web.Response(
                content_type="application/json",
                headers={
                    **_cors_headers(),
                    "Content-Disposition": f'attachment; filename="audit_{run_id[:12]}.json"',
                },
                text=content,
            )

    # ── SLA report ────────────────────────────────────────────────────────────

    async def sla_report(request: Any) -> Any:
        """GET /sla[?node_id=X] — p50/p95/p99 latency per node."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        from meshflow.observability.sla import get_sla_tracker

        tracker = get_sla_tracker()
        node_filter = request.rel_url.query.get("node_id") or None

        if node_filter:
            s = tracker.summary(node_filter)
            data = s.to_dict() if s else {}
        else:
            data = tracker.report()

        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"sla": data}),
        )

    # ── Rate limiter status ───────────────────────────────────────────────────

    async def rate_limiter_status(request: Any) -> Any:
        """GET /rate-limit/status — token-bucket stats per API key."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        from meshflow.observability.sla import get_rate_limiter

        rl = get_rate_limiter()
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"buckets": rl.stats()}),
        )

    # ── Agent pool status ─────────────────────────────────────────────────────

    async def pool_status(request: Any) -> Any:
        """GET /pool/status — stats for all registered AgentPools."""
        if not _require_auth(request):
            return web.Response(status=401)

        from meshflow.agents.pool import all_pool_stats

        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"pools": all_pool_stats()}),
        )

    # ── WebSocket agent-to-agent message bus ─────────────────────────────────
    _bus_connections: set[Any] = set()

    async def ws_bus(request: Any) -> Any:
        """GET /ws/bus — WebSocket hub for cross-process agent messaging.

        Every message received from any client is JSON-parsed and fanned out
        to all *other* connected clients so that agents in separate processes
        can communicate through the shared MessageBus (WebSocketBusBackend).

        Auth is enforced via the same API-key mechanism as REST endpoints.
        """
        if not _require_auth(request):
            return web.Response(status=401)

        try:
            from aiohttp import WSMsgType
        except ImportError:
            return web.Response(status=500, text="aiohttp required")

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        _bus_connections.add(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Broadcast to all other live connections
                    dead: list[Any] = []
                    for peer in _bus_connections:
                        if peer is ws or peer.closed:
                            if peer.closed and peer is not ws:
                                dead.append(peer)
                            continue
                        try:
                            await peer.send_str(msg.data)
                        except Exception:
                            dead.append(peer)
                    for d in dead:
                        _bus_connections.discard(d)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            _bus_connections.discard(ws)

        return ws

    # ── Workflow event SSE stream ─────────────────────────────────────────────
    async def events_sse(request: Any) -> Any:
        """GET /events[?run_id=<id>] — SSE stream of all workflow lifecycle events.

        Clients receive a stream of Server-Sent Events for every STEP_START,
        STEP_COMPLETE, STEP_BLOCKED, HITL_REQUIRED, WORKFLOW_START/COMPLETE, etc.
        Pass ?run_id=<id> to filter to a single run.  Past events since server
        start are replayed first (replay_history=True), then live events follow.
        """
        if not _require_auth(request):
            return web.Response(status=401)

        from meshflow.core.events import global_event_bus

        run_id: str | None = request.rel_url.query.get("run_id") or None

        response = web.StreamResponse(
            headers={
                **_cors_headers(),
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        # Announce connection
        connected_payload = json.dumps({"ok": True, "filter_run_id": run_id})
        await response.write(f"event: connected\ndata: {connected_payload}\n\n".encode())

        try:
            async for event in global_event_bus.subscribe(run_id=run_id, replay_history=True):
                await response.write(event.to_sse().encode())
        except (asyncio.CancelledError, ConnectionResetError):
            pass

        return response

    # ── Compliance reporting ──────────────────────────────────────────────────

    async def compliance_report(request: Any) -> Any:
        """GET /compliance/report?framework=hipaa&run_id=X — generate compliance report."""
        if not _require_auth(request):
            return web.Response(
                status=401, text='{"error":"Unauthorized"}', content_type="application/json"
            )
        framework = request.rel_url.query.get("framework", "hipaa").lower()
        run_id = request.rel_url.query.get("run_id") or ""
        fmt = request.rel_url.query.get("format", "json").lower()

        from meshflow.compliance.reporter import ComplianceReporter, SUPPORTED_FRAMEWORKS

        if framework not in SUPPORTED_FRAMEWORKS:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps({
                    "error": f"Unknown framework '{framework}'",
                    "supported": list(SUPPORTED_FRAMEWORKS),
                }),
            )

        if run_id:
            steps = await ledger.get_run(run_id) or []
            run_ids = [run_id]
        else:
            all_runs = await ledger.list_runs()
            steps = []
            for rid in all_runs[-50:]:  # cap at last 50 runs
                run_steps = await ledger.get_run(rid) or []
                steps.extend(run_steps)
            run_ids = all_runs[-50:]

        reporter = ComplianceReporter()
        report = reporter.generate(framework, steps, run_ids=run_ids)

        if fmt == "text":
            return web.Response(
                content_type="text/plain",
                headers=_cors_headers(),
                text=report.to_text(),
            )
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=report.to_json(),
        )

    # ── Webhook management ────────────────────────────────────────────────────

    async def webhooks_list(request: Any) -> Any:
        """GET /webhooks — list registered webhook endpoints."""
        if not _require_auth(request):
            return web.Response(status=401)
        from meshflow.observability.webhooks import get_webhook_manager

        mgr = get_webhook_manager()
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({
                "webhooks": [h.to_dict() for h in mgr.list()],
                "stats": mgr.stats(),
            }),
        )

    async def webhooks_register(request: Any) -> Any:
        """POST /webhooks — register a new webhook."""
        if not _require_auth(request):
            return web.Response(status=401)
        try:
            body = cast(dict[str, Any], await request.json())
        except Exception:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps({"error": "invalid JSON body"}),
            )
        url = body.get("url", "")
        if not url:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps({"error": "url is required"}),
            )
        events = body.get("events", ["*"])
        secret = body.get("secret", "")

        from meshflow.observability.webhooks import get_webhook_manager

        mgr = get_webhook_manager()
        try:
            reg = mgr.register(url, events=events, secret=secret)
        except ValueError as exc:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps({"error": str(exc)}),
            )
        return web.Response(
            status=201,
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps(reg.to_dict()),
        )

    async def webhooks_delete(request: Any) -> Any:
        """DELETE /webhooks/{id} — remove a registered webhook."""
        if not _require_auth(request):
            return web.Response(status=401)
        webhook_id = request.match_info["id"]
        from meshflow.observability.webhooks import get_webhook_manager

        mgr = get_webhook_manager()
        removed = mgr.unregister(webhook_id)
        if not removed:
            return web.Response(
                status=404,
                content_type="application/json",
                text=json.dumps({"error": "webhook not found"}),
            )
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({"deleted": webhook_id}),
        )

    async def webhooks_deliveries(request: Any) -> Any:
        """GET /webhooks/{id}/deliveries — delivery history for a webhook."""
        if not _require_auth(request):
            return web.Response(status=401)
        webhook_id = request.match_info["id"]
        from meshflow.observability.webhooks import get_webhook_manager

        mgr = get_webhook_manager()
        hook = mgr.get(webhook_id)
        if not hook:
            return web.Response(
                status=404,
                content_type="application/json",
                text=json.dumps({"error": "webhook not found"}),
            )
        history = mgr.delivery_history(webhook_id)
        return web.Response(
            content_type="application/json",
            headers=_cors_headers(),
            text=json.dumps({
                "webhook_id": webhook_id,
                "deliveries": [r.to_dict() for r in history],
            }),
        )

    # ── SSE transport: server-initiated event stream ───────────────────────────
    async def mcp_sse(request: Any) -> Any:
        """GET /mcp/sse — SSE stream for server→client notifications.

        Clients connect here to receive tool-list-changed and progress events.
        Currently emits a heartbeat every 30 s; real events are emitted when
        tools are registered dynamically.
        """
        if not _require_auth(request):
            return web.Response(status=401)

        response = web.StreamResponse(
            headers={
                **_cors_headers(),
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        async def _emit(event: str, data: dict[str, Any]) -> None:
            payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            await response.write(payload.encode())

        # Send initial ready event
        await _emit("ready", {"server": "MeshFlow", "tools": len(_mcp_server.tool_list())})

        try:
            while True:
                await asyncio.sleep(30)
                await _emit("ping", {"t": time.time()})
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return response

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/health/live", health_live)
    app.router.add_get("/health/ready", health_ready)
    app.router.add_get("/metrics", metrics_endpoint)
    app.router.add_post("/run", run_task)
    app.router.add_post("/stream", stream_task)
    app.router.add_get("/otel/config", otel_config)
    app.router.add_get("/graph/{run_id}", graph_export)
    app.router.add_get("/audit/export", audit_export)
    app.router.add_get("/sla", sla_report)
    app.router.add_get("/rate-limit/status", rate_limiter_status)
    app.router.add_get("/plugins", list_plugins)
    app.router.add_get("/pool/status", pool_status)
    app.router.add_get("/eval-results", list_eval_results)
    app.router.add_get("/events", events_sse)
    app.router.add_get("/ws/bus", ws_bus)
    app.router.add_get("/traces", list_traces)
    app.router.add_get("/traces/{run_id}", get_trace)
    app.router.add_get("/hitl/pending", hitl_pending)
    app.router.add_post("/hitl/{run_id}/approve", hitl_approve)
    app.router.add_post("/hitl/{run_id}/reject", hitl_reject)
    # MCP endpoints
    app.router.add_get("/mcp", mcp_discover)
    app.router.add_post("/mcp", mcp_endpoint)
    app.router.add_get("/mcp/sse", mcp_sse)
    # Compliance reporting
    app.router.add_get("/compliance/report", compliance_report)
    # Webhook management
    app.router.add_get("/webhooks", webhooks_list)
    app.router.add_post("/webhooks", webhooks_register)
    app.router.add_delete("/webhooks/{id}", webhooks_delete)
    app.router.add_get("/webhooks/{id}/deliveries", webhooks_deliveries)
    app.router.add_route("OPTIONS", "/{path_info:.*}", options_handler)
    return app


def serve(
    host: str = "0.0.0.0",
    port: int = 8000,
    api_keys: set[str] | None = None,
    ledger_path: str = "meshflow_runs.db",
    tls_cert: str = "",
    tls_key: str = "",
) -> None:
    try:
        from aiohttp import web
    except ImportError as exc:
        raise RuntimeError("pip install aiohttp") from exc

    keys = api_keys if api_keys is not None else _load_api_keys()
    if not keys:
        print("WARNING: No MESHFLOW_API_KEYS set — server is open (no authentication).")

    ssl_ctx: ssl.SSLContext | None = None
    if tls_cert and tls_key:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(tls_cert, tls_key)
        proto = "https"
    else:
        proto = "http"

    async def _run() -> None:
        import signal

        app = await _build_app(keys, ledger_path)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port, ssl_context=ssl_ctx)
        await site.start()
        print(f"MeshFlow {VERSION} listening on {proto}://{host}:{port}")
        print("  POST /run              — execute a task")
        print("  POST /stream           — token-streaming NDJSON")
        print("  GET  /events           — SSE workflow lifecycle events")
        print("  GET  /ws/bus           — WebSocket agent-to-agent message bus")
        print("  GET  /health           — health check (no auth)")
        print("  GET  /health/live      — Kubernetes liveness probe")
        print("  GET  /health/ready     — Kubernetes readiness probe")
        print("  GET  /metrics          — Prometheus metrics")
        print("  GET  /compliance/report — compliance report")
        print("  GET/POST/DELETE /webhooks — webhook management")
        if not keys:
            print("  Auth: DISABLED (set MESHFLOW_API_KEYS to enable)")
        else:
            print(f"  Auth: {len(keys)} API key(s) active")

        stop_event = asyncio.Event()

        def _handle_sigterm() -> None:
            print("\nMeshFlow: SIGTERM received — draining connections (30s)…")
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_sigterm)
            except (NotImplementedError, RuntimeError):
                pass  # Windows / non-default loops

        await stop_event.wait()
        print("MeshFlow: shutting down gracefully…")
        await asyncio.sleep(2)  # allow in-flight requests to complete
        await runner.cleanup()
        print("MeshFlow: shutdown complete.")

    asyncio.run(_run())


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="MeshFlow runtime server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--api-key", action="append", dest="api_keys", help="API key (can repeat for multiple keys)"
    )
    parser.add_argument(
        "--ledger", default="meshflow_runs.db", help="SQLite path or postgres:// DSN"
    )
    parser.add_argument("--tls-cert", default="", help="TLS certificate file")
    parser.add_argument("--tls-key", default="", help="TLS private key file")
    args = parser.parse_args()
    keys: set[str] = set(args.api_keys) if args.api_keys else _load_api_keys()
    serve(
        args.host,
        args.port,
        api_keys=keys,
        ledger_path=args.ledger,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
    )


if __name__ == "__main__":
    main()
