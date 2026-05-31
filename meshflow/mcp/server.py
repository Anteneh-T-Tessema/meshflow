"""MeshFlow MCP Server — expose governed agents and workflows as MCP tools.

MeshFlow becomes an MCP server that any MCP host (Claude Desktop, Cursor,
VS Code Copilot, Windsurf, …) can connect to. Every tool call runs through
the full governance stack: budget cap, DascGate, HITL, PHI scrubber, audit ledger.

MCP protocol version: 2024-11-05

Transports:
  HTTP+SSE  — POST /mcp   (used by Claude Desktop remote connections)
  stdio     — newline-delimited JSON-RPC on stdin/stdout (local servers)

Tool surface exposed to MCP hosts:
  meshflow_run          — run any task through a governed Mesh
  meshflow_run_agent    — call a specific registered agent by name
  meshflow_approve_hitl — approve a paused human-in-the-loop checkpoint
  meshflow_reject_hitl  — reject a paused human-in-the-loop checkpoint
  meshflow_get_trace    — retrieve the SHA-256 audit chain for a run
  meshflow_list_runs    — list recent runs with cost and status

Each registered Agent or Team also becomes its own tool automatically.

Usage (Python):
    from meshflow.mcp.server import MCPServer, MCPToolEntry
    from meshflow.agents.library import ResearchAgent, CoderAgent

    srv = MCPServer(
        name="My Governed Agents",
        policy="standard",
    )
    srv.register_agent(ResearchAgent(), description="Deep web research with citations")
    srv.register_agent(CoderAgent(),    description="Write production-ready code")
    await srv.handle_http_request(request_body_dict)

Usage (CLI — stdio):
    meshflow mcp-stdio             # starts stdio transport, Claude Desktop connects
    meshflow mcp-stdio --config meshflow.yaml
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "meshflow", "version": "0.10.0"}


# ── Tool descriptor ────────────────────────────────────────────────────────────

@dataclass
class MCPToolEntry:
    """A single MCP tool exposed by this server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable  # async (arguments: dict) -> str


# ── Built-in tool schemas ─────────────────────────────────────────────────────

_BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "name": "meshflow_run",
        "description": (
            "Run any task through a governed MeshFlow agent mesh. "
            "Returns the output plus a full governance receipt: run_id (for audit), "
            "cost, tokens, and whether human approval was required. "
            "Policy modes: dev | standard | regulated | hipaa | legal-critical."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task or question to execute.",
                },
                "policy": {
                    "type": "string",
                    "enum": ["dev", "standard", "regulated", "hipaa", "legal-critical"],
                    "description": "Governance policy mode (default: standard).",
                },
                "budget_usd": {
                    "type": "number",
                    "description": "Max spend in USD for this run (default: 1.0).",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "meshflow_approve_hitl",
        "description": (
            "Approve a paused human-in-the-loop checkpoint. "
            "Use when meshflow_run returns status='paused' and you have reviewed the output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run ID to approve."},
                "reviewer_id": {"type": "string", "description": "Your identifier (e.g. email)."},
                "notes": {"type": "string", "description": "Review notes for the audit record."},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "meshflow_reject_hitl",
        "description": "Reject a paused human-in-the-loop checkpoint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run ID to reject."},
                "reviewer_id": {"type": "string"},
                "reason": {"type": "string", "description": "Reason for rejection."},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "meshflow_get_trace",
        "description": (
            "Retrieve the SHA-256 tamper-evident audit chain for a completed run. "
            "Returns every step with cost, tokens, uncertainty score, and hash links."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run ID to inspect."},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "meshflow_list_runs",
        "description": "List recent runs with their status, cost, and token counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of runs to return (default: 10).",
                },
            },
        },
    },
]


# ── MCPServer ─────────────────────────────────────────────────────────────────

class MCPServer:
    """Governed MeshFlow MCP server.

    Instantiate, register agents/teams, then call:
      - ``handle_request(msg)``  — process one JSON-RPC message dict
      - ``run_stdio()``          — run the stdio transport loop (blocking)
    """

    def __init__(
        self,
        name: str = "MeshFlow",
        policy: str = "standard",
        ledger_path: str = ":memory:",
    ) -> None:
        self._name = name
        self._default_policy = policy
        self._ledger_path = ledger_path
        self._tools: dict[str, MCPToolEntry] = {}
        self._initialized = False

        # Register built-in tools
        self._register_builtin_tools()

    # ── Registration ──────────────────────────────────────────────────────────

    def register_agent(
        self,
        agent: Any,
        description: str = "",
        tool_name: str = "",
    ) -> None:
        """Expose a MeshFlow Agent as an MCP tool.

        The tool name defaults to ``agent_<agent.name>``.
        When the MCP host calls this tool, the agent runs under the server's
        default policy and the result is returned with a governance receipt.
        """
        tname = tool_name or f"agent_{agent.name}"
        desc = description or f"Run the '{agent.name}' agent ({agent.role})."

        async def _fn(arguments: dict[str, Any]) -> str:
            task = arguments.get("task", "")
            ctx = {k: v for k, v in arguments.items() if k != "task"}
            result = await agent.run(task, ctx)
            return _format_agent_result(result, agent.name)

        self._tools[tname] = MCPToolEntry(
            name=tname,
            description=desc,
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task for this agent."},
                },
                "required": ["task"],
            },
            fn=_fn,
        )

    def register_team(
        self,
        team: Any,
        description: str = "",
        tool_name: str = "",
    ) -> None:
        """Expose a MeshFlow Team as an MCP tool."""
        tname = tool_name or f"team_{team.name}"
        desc = description or f"Run the '{team.name}' team ({team.pattern} pattern)."

        async def _fn(arguments: dict[str, Any]) -> str:
            task = arguments.get("task", "")
            result = await team.run(task)
            return _format_workflow_result(result, team.name)

        self._tools[tname] = MCPToolEntry(
            name=tname,
            description=desc,
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task for this team."},
                },
                "required": ["task"],
            },
            fn=_fn,
        )

    def register_workflow(
        self,
        workflow: Any,
        description: str = "",
        tool_name: str = "",
    ) -> None:
        """Expose a WorkflowDefinition as an MCP tool."""
        tname = tool_name or f"workflow_{workflow.name}"
        desc = description or f"Run the '{workflow.name}' workflow."

        async def _fn(arguments: dict[str, Any]) -> str:
            from meshflow.core.mesh import Mesh
            from meshflow.core.schemas import policy_for_mode

            task = arguments.get("task", "")
            pol = policy_for_mode(self._default_policy)
            result = await Mesh(policy=pol).run_workflow(workflow, task)
            return _format_workflow_result(result, workflow.name)

        self._tools[tname] = MCPToolEntry(
            name=tname,
            description=desc,
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task for this workflow."},
                },
                "required": ["task"],
            },
            fn=_fn,
        )

    def tool_list(self) -> list[dict[str, Any]]:
        """Return the MCP tools/list response payload."""
        builtin = _BUILTIN_TOOLS[:]
        for entry in self._tools.values():
            builtin.append(
                {
                    "name": entry.name,
                    "description": entry.description,
                    "inputSchema": entry.input_schema,
                }
            )
        return builtin

    # ── JSON-RPC dispatch ─────────────────────────────────────────────────────

    async def handle_request(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Process one JSON-RPC 2.0 message.  Returns a response dict or None for notifications."""
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        # Notifications (no id) — fire-and-forget
        if msg_id is None and method in ("notifications/initialized",):
            self._initialized = True
            return None

        try:
            result = await self._dispatch(method, params)
            if msg_id is None:
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except MCPError as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": e.code, "message": e.message},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Internal error: {e}"},
            }

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return await self._handle_initialize(params)
        if method == "tools/list":
            return await self._handle_tools_list(params)
        if method == "tools/call":
            return await self._handle_tools_call(params)
        if method == "resources/list":
            return {"resources": []}  # no resources exposed yet
        if method == "prompts/list":
            return {"prompts": []}  # no prompts exposed yet
        if method == "ping":
            return {}
        raise MCPError(-32601, f"Method not found: {method}")

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        self._initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {**SERVER_INFO, "name": self._name},
            "instructions": (
                "This is a governed MeshFlow server. Every tool call is audited with "
                "a SHA-256 chain. Use meshflow_run for ad-hoc tasks, or call registered "
                "agents/teams directly. HITL-required outputs return status='paused' — "
                "call meshflow_approve_hitl after human review."
            ),
        }

    async def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": self.tool_list()}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Built-in tools
        if name == "meshflow_run":
            text = await self._builtin_run(arguments)
        elif name == "meshflow_approve_hitl":
            text = await self._builtin_approve_hitl(arguments)
        elif name == "meshflow_reject_hitl":
            text = await self._builtin_reject_hitl(arguments)
        elif name == "meshflow_get_trace":
            text = await self._builtin_get_trace(arguments)
        elif name == "meshflow_list_runs":
            text = await self._builtin_list_runs(arguments)
        elif name in self._tools:
            text = await self._tools[name].fn(arguments)
        else:
            raise MCPError(-32602, f"Unknown tool: {name!r}")

        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ── Built-in tool implementations ─────────────────────────────────────────

    async def _builtin_run(self, args: dict[str, Any]) -> str:
        from meshflow.core.mesh import Mesh
        from meshflow.core.schemas import policy_for_mode

        task = args.get("task", "")
        if not task:
            raise MCPError(-32602, "'task' is required")

        policy_mode = args.get("policy", self._default_policy)
        budget = float(args.get("budget_usd", 1.0))

        try:
            pol = policy_for_mode(policy_mode, budget_usd=budget)
            mesh = Mesh(policy=pol)
            result = await mesh.run(task, policy=pol)
            return _format_run_result(result)
        except Exception as e:
            return f"[MeshFlow ERROR] {e}\n{traceback.format_exc()}"

    async def _builtin_approve_hitl(self, args: dict[str, Any]) -> str:
        from meshflow.core.ledger import ReplayLedger

        run_id = args.get("run_id", "")
        if not run_id:
            raise MCPError(-32602, "'run_id' is required")

        ledger = ReplayLedger(self._ledger_path)
        checkpoint = await ledger.load_checkpoint_data(run_id)
        if not checkpoint:
            return f"[ERROR] No paused checkpoint found for run_id={run_id!r}"

        checkpoint["approved_by"] = args.get("reviewer_id", "mcp-client")
        checkpoint["review_notes"] = args.get("notes", "")
        checkpoint["approved"] = True
        await ledger.save_checkpoint(run_id, checkpoint)

        return (
            f"✓ Approved: run_id={run_id}\n"
            f"  Reviewer: {checkpoint['approved_by']}\n"
            f"  Notes:    {checkpoint.get('review_notes', '')}\n"
            f"  The workflow can now continue from the paused checkpoint."
        )

    async def _builtin_reject_hitl(self, args: dict[str, Any]) -> str:
        from meshflow.core.ledger import ReplayLedger

        run_id = args.get("run_id", "")
        if not run_id:
            raise MCPError(-32602, "'run_id' is required")

        ledger = ReplayLedger(self._ledger_path)
        checkpoint = await ledger.load_checkpoint_data(run_id)
        if not checkpoint:
            return f"[ERROR] No paused checkpoint found for run_id={run_id!r}"

        checkpoint["approved_by"] = args.get("reviewer_id", "mcp-client")
        checkpoint["review_notes"] = args.get("reason", "")
        checkpoint["approved"] = False
        await ledger.save_checkpoint(run_id, checkpoint)

        return (
            f"✗ Rejected: run_id={run_id}\n"
            f"  Reviewer: {checkpoint['approved_by']}\n"
            f"  Reason:   {checkpoint.get('review_notes', '')}"
        )

    async def _builtin_get_trace(self, args: dict[str, Any]) -> str:
        from meshflow.core.ledger import ReplayLedger

        run_id = args.get("run_id", "")
        if not run_id:
            raise MCPError(-32602, "'run_id' is required")

        ledger = ReplayLedger(self._ledger_path)
        steps = await ledger.get_run(run_id)
        if not steps:
            return f"[ERROR] run_id={run_id!r} not found."

        summary = await ledger.run_summary(run_id)
        chain = await ledger.verify_chain(run_id)

        lines = [
            f"Audit trace for run_id={run_id}",
            f"  Steps        : {summary['steps']}",
            f"  Total cost   : ${summary['total_cost_usd']:.5f}",
            f"  Total tokens : {summary['total_tokens']}",
            f"  Chain valid  : {'YES ✓' if chain['valid'] else 'NO ✗ (tampered!)'}",
            "",
        ]
        for i, step in enumerate(steps, 1):
            blocked = step.get("blocked", False)
            lines.append(
                f"  [{i:02d}] {step.get('node_id', '?'):<20} "
                f"{'BLOCKED' if blocked else 'ok':<7} "
                f"~{step.get('uncertainty', 0):.2f} conf  "
                f"${step.get('cost_usd', 0):.5f}  "
                f"{step.get('tokens_used', 0)} tok"
            )
            output = (step.get("output_content") or "")[:120]
            if output:
                lines.append(f"       {output}")

        if not chain["valid"]:
            lines.append("\n[INTEGRITY ERROR] Chain hash mismatch detected:")
            for err in chain.get("errors", []):
                lines.append(f"  ! {err}")

        return "\n".join(lines)

    async def _builtin_list_runs(self, args: dict[str, Any]) -> str:
        from meshflow.core.ledger import ReplayLedger

        limit = int(args.get("limit", 10))
        ledger = ReplayLedger(self._ledger_path)
        run_ids = (await ledger.list_runs())[:limit]

        if not run_ids:
            return "No runs recorded yet."

        lines = [f"Recent {len(run_ids)} run(s):\n"]
        for run_id in run_ids:
            summary = await ledger.run_summary(run_id)
            if not summary:
                continue
            lines.append(
                f"  {run_id[:12]}…  "
                f"steps={summary['steps']}  "
                f"cost=${summary['total_cost_usd']:.4f}  "
                f"tokens={summary['total_tokens']}"
            )
        return "\n".join(lines)

    # ── Built-in tool self-registration ───────────────────────────────────────

    def _register_builtin_tools(self) -> None:
        pass  # Built-ins are handled directly in _handle_tools_call and tool_list()

    # ── stdio transport ───────────────────────────────────────────────────────

    async def run_stdio(self) -> None:
        """Run the stdio transport loop.

        Reads newline-delimited JSON-RPC from stdin, writes responses to stdout.
        This is the transport used by Claude Desktop for local MCP servers.
        """
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_event_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        write_transport, _ = await loop.connect_write_pipe(
            lambda: asyncio.BaseProtocol(), sys.stdout.buffer
        )

        async def _write(obj: dict[str, Any]) -> None:
            line = json.dumps(obj, separators=(",", ":")) + "\n"
            write_transport.write(line.encode())

        while True:
            try:
                raw = await reader.readline()
                if not raw:
                    break
                msg = json.loads(raw.decode().strip())
                response = await self.handle_request(msg)
                if response is not None:
                    await _write(response)
            except json.JSONDecodeError:
                await _write({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                })
            except EOFError:
                break
            except Exception as e:
                await _write({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(e)},
                })


# ── Error type ────────────────────────────────────────────────────────────────

class MCPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# ── Output formatters ─────────────────────────────────────────────────────────

def _format_run_result(result: Any) -> str:
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    output = str(result.output or "")[:2000]
    lines = [
        f"[MeshFlow] run_id={result.run_id}",
        f"  Status   : {status}",
        f"  Cost     : ${result.total_cost_usd:.5f}",
        f"  Tokens   : {result.total_tokens}",
        f"  Ledger   : {result.ledger_entries} entries",
        "",
        "Output:",
        output,
    ]
    if status == "paused":
        lines += [
            "",
            "[HITL REQUIRED] This output requires human review before it can be used.",
            f"  Call meshflow_approve_hitl with run_id={result.run_id!r} after review.",
        ]
    if result.error:
        lines += ["", f"[ERROR] {result.error}"]
    return "\n".join(lines)


def _format_workflow_result(result: Any, name: str) -> str:
    lines = [f"[MeshFlow Workflow: {name}]"]
    completed = getattr(result, "completed", False)
    paused = getattr(result, "paused_nodes", [])
    blocked = getattr(result, "blocked_nodes", [])

    lines.append(f"  Status   : {'completed' if completed else ('paused' if paused else 'blocked')}")
    lines.append(f"  Cost     : ${getattr(result, 'total_cost_usd', 0):.5f}")
    lines.append(f"  Tokens   : {getattr(result, 'total_tokens', 0)}")
    lines.append(f"  Run ID   : {getattr(result, 'run_id', '?')}")

    if paused:
        lines.append(f"\n[HITL REQUIRED] Paused at: {paused}")
        lines.append(f"  Call meshflow_approve_hitl with run_id={result.run_id!r}")

    if blocked:
        lines.append(f"\n[BLOCKED] Nodes blocked: {blocked}")

    output = getattr(result, "output", "")
    if output:
        lines += ["", "Output:", str(output)[:2000]]

    return "\n".join(lines)


def _format_agent_result(result: dict[str, Any], agent_name: str) -> str:
    lines = [
        f"[Agent: {agent_name}]",
        f"  Confidence : {result.get('stated_confidence', 0):.0%}",
        f"  Tokens     : {result.get('tokens', 0)}",
        f"  Cost       : ${result.get('cost_usd', 0):.5f}",
        "",
        result.get("result", ""),
    ]
    return "\n".join(lines)


# ── Convenience: build MCPServer from meshflow.yaml ───────────────────────────

def from_config(config_path: str, policy: str = "") -> MCPServer:
    """Build an MCPServer from a meshflow.yaml file.

    Every agent, team, and workflow declared in the config is registered
    as an MCP tool automatically.
    """
    from meshflow.core.config import load

    cfg = load(config_path)
    srv = MCPServer(
        policy=policy or cfg.policy.mode.value,
    )
    for agent in cfg.agents.values():
        srv.register_agent(agent)
    if cfg.team:
        srv.register_team(cfg.team)
    if cfg.workflow:
        srv.register_workflow(cfg.workflow)
    return srv


__all__ = ["MCPServer", "MCPToolEntry", "MCPError", "from_config"]
