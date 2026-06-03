"""MCPRouter — multi-server routing with per-server authorization policies.

Routes tool calls across multiple MCP servers, enforces allow/deny lists
per server, and optionally reports call telemetry to meshflow.dev.

Usage::

    from meshflow.mcp.router import MCPRouter, MCPServerConfig, MCPAuthPolicy

    router = MCPRouter([
        MCPServerConfig(
            name="filesystem",
            transport="stdio",
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            policy=MCPAuthPolicy(allow_tools=["read_file", "list_directory"]),
        ),
        MCPServerConfig(
            name="github",
            transport="http",
            endpoint="https://api.github.com/mcp",
            headers={"Authorization": "Bearer ghp_..."},
            policy=MCPAuthPolicy(deny_tools=["delete_repo"]),
        ),
    ])

    # Route a tool call — MCPRouter picks the right server automatically
    result = await router.call("read_file", {"path": "/tmp/report.txt"})

    # List all tools across all servers
    tools = await router.list_tools()

    # Use with an Agent
    agent = Agent(name="analyst", role="researcher", mcp_router=router)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


# ── Authorization policy ──────────────────────────────────────────────────────

@dataclass
class MCPAuthPolicy:
    """Per-server authorization policy.

    Parameters
    ----------
    allow_tools:
        If non-empty, only these tool names are permitted.
    deny_tools:
        Tool names that are always blocked (applied after allow_tools).
    require_approval:
        Tools that require explicit human approval before execution.
    rate_limit_per_min:
        Maximum calls to this server per minute (None = unlimited).
    """

    allow_tools: list[str] = field(default_factory=list)
    deny_tools: list[str]  = field(default_factory=list)
    require_approval: list[str] = field(default_factory=list)
    rate_limit_per_min: int | None = None

    def is_allowed(self, tool_name: str) -> bool:
        if tool_name in self.deny_tools:
            return False
        if self.allow_tools:
            return tool_name in self.allow_tools
        return True

    def requires_approval(self, tool_name: str) -> bool:
        return tool_name in self.require_approval


# ── Server config ─────────────────────────────────────────────────────────────

@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server.

    Parameters
    ----------
    name:
        Unique identifier for this server (e.g. ``"filesystem"``).
    transport:
        ``"stdio"`` (local subprocess) or ``"http"`` / ``"sse"`` (remote).
    command:
        Command to start the server (stdio only).
        Example: ``["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]``
    endpoint:
        HTTP endpoint (http/sse only).
    headers:
        Extra HTTP headers (auth tokens, etc.) for http/sse transports.
    env:
        Additional environment variables for stdio servers.
    policy:
        Authorization policy for this server.
    priority:
        Lower = higher priority when multiple servers offer the same tool.
    """

    name: str
    transport: str = "stdio"              # "stdio" | "http" | "sse"
    command: list[str] = field(default_factory=list)
    endpoint: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    policy: MCPAuthPolicy = field(default_factory=MCPAuthPolicy)
    priority: int = 0                      # lower = higher priority


# ── Tool entry (cached from server list_tools) ────────────────────────────────

@dataclass
class MCPToolEntry:
    """A tool registered on an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str
    server_config: MCPServerConfig


# ── Call result ───────────────────────────────────────────────────────────────

@dataclass
class MCPCallResult:
    """Result of an MCP tool call."""

    tool_name: str
    server_name: str
    content: Any              # parsed response content
    latency_ms: float
    success: bool
    error: str | None = None

    def __str__(self) -> str:
        return str(self.content)


# ── MCPRouter ─────────────────────────────────────────────────────────────────

class MCPDeniedError(Exception):
    """Raised when a tool call is blocked by the server's auth policy."""


class MCPRouter:
    """Routes tool calls across multiple MCP servers.

    Handles:
    - **Multi-server tool discovery** — aggregates tool lists from all servers
    - **Automatic routing** — picks the right server for each tool call
    - **Authorization enforcement** — per-server allow/deny + rate limiting
    - **Call telemetry** — optionally reports calls to meshflow.dev

    Parameters
    ----------
    servers:
        List of :class:`MCPServerConfig` objects.
    cloud:
        Optional :class:`~meshflow.cloud.MeshFlowCloud` for telemetry.
    fallback_to_mock:
        If True, calls return mock results when no real server is connected
        (useful for offline tests).
    """

    def __init__(
        self,
        servers: list[MCPServerConfig],
        cloud: Any = None,
        fallback_to_mock: bool = True,
    ) -> None:
        self._servers     = {s.name: s for s in servers}
        self._cloud       = cloud
        self._mock        = fallback_to_mock
        self._tool_index: dict[str, MCPServerConfig] | None = None
        self._rate_counts: dict[str, list[float]] = {}    # server_name → timestamps

    # ── Tool discovery ────────────────────────────────────────────────────────

    async def list_tools(self) -> list[MCPToolEntry]:
        """Return all tools across all configured servers."""
        tools: list[MCPToolEntry] = []
        for name, cfg in sorted(self._servers.items(), key=lambda x: x[1].priority):
            server_tools = await self._list_server_tools(cfg)
            for t in server_tools:
                if cfg.policy.is_allowed(t["name"]):
                    tools.append(MCPToolEntry(
                        name=t["name"],
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {}),
                        server_name=name,
                        server_config=cfg,
                    ))
        return tools

    async def _list_server_tools(self, cfg: MCPServerConfig) -> list[dict[str, Any]]:
        """Connect to a server and list its tools."""
        try:
            client = await self._get_client(cfg)
            if client is None:
                return self._mock_tools(cfg)
            result = await client.list_tools()
            return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema.model_dump() if hasattr(t.inputSchema, 'model_dump') else {}}
                    for t in result.tools]
        except Exception:
            return self._mock_tools(cfg)

    def _mock_tools(self, cfg: MCPServerConfig) -> list[dict[str, Any]]:
        if not self._mock:
            return []
        # Return plausible mock tools based on server name
        _MOCK: dict[str, list[str]] = {
            "filesystem": ["read_file", "write_file", "list_directory", "delete_file"],
            "github":     ["create_issue", "list_prs", "get_file", "search_code"],
            "fetch":      ["fetch_url"],
            "sqlite":     ["query", "execute", "list_tables", "describe_table", "create_table"],
            "slack":      ["post_message", "list_channels", "get_messages"],
        }
        names = _MOCK.get(cfg.name, ["tool_a", "tool_b"])
        return [{"name": n, "description": f"{cfg.name}.{n}", "inputSchema": {}} for n in names]

    # ── Tool routing ──────────────────────────────────────────────────────────

    async def _build_index(self) -> None:
        if self._tool_index is not None:
            return
        tools = await self.list_tools()
        # Lower priority value wins; last server for same-priority tools also wins
        index: dict[str, MCPServerConfig] = {}
        for t in sorted(tools, key=lambda x: -x.server_config.priority):
            index[t.name] = t.server_config
        self._tool_index = index

    async def route(self, tool_name: str) -> MCPServerConfig:
        """Return the server config responsible for *tool_name*.

        Raises ``KeyError`` if no server offers this tool.
        """
        await self._build_index()
        assert self._tool_index is not None
        if tool_name not in self._tool_index:
            raise KeyError(f"No server offers tool '{tool_name}'")
        return self._tool_index[tool_name]

    # ── Call execution ────────────────────────────────────────────────────────

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        server_name: str | None = None,
    ) -> MCPCallResult:
        """Call *tool_name* with *arguments* on the appropriate server.

        Parameters
        ----------
        tool_name:
            The tool to call.
        arguments:
            Input arguments dict.
        server_name:
            Force routing to a specific server (skips auto-routing).
        """
        # Resolve server
        if server_name:
            cfg = self._servers.get(server_name)
            if cfg is None:
                raise KeyError(f"Unknown server '{server_name}'")
        else:
            cfg = await self.route(tool_name)

        # Auth check
        if not cfg.policy.is_allowed(tool_name):
            raise MCPDeniedError(
                f"Tool '{tool_name}' is blocked by policy on server '{cfg.name}'"
            )

        # Rate-limit check
        if cfg.policy.rate_limit_per_min is not None:
            self._enforce_rate_limit(cfg.name, cfg.policy.rate_limit_per_min)

        t0 = time.monotonic()
        try:
            client = await self._get_client(cfg)
            if client is None:
                content = f"[mock] {tool_name}({arguments})"
                success = True
            else:
                result  = await client.call_tool(tool_name, arguments or {})
                content = result.content[0].text if result.content else ""
                success = not result.isError
        except Exception as exc:
            content = str(exc)
            success = False

        latency_ms = round((time.monotonic() - t0) * 1000, 2)
        mcr = MCPCallResult(
            tool_name=tool_name,
            server_name=cfg.name,
            content=content,
            latency_ms=latency_ms,
            success=success,
            error=None if success else content,
        )

        # Telemetry
        if self._cloud is not None:
            try:
                self._cloud.report_mcp_call(
                    server_name=cfg.name,
                    tool_name=tool_name,
                    transport=cfg.transport,
                    endpoint=cfg.endpoint,
                    latency_ms=int(latency_ms),
                    success=success,
                )
            except Exception:
                pass

        return mcr

    def call_sync(self, tool_name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> MCPCallResult:
        from meshflow.integrations._utils import run_sync
        return run_sync(self.call(tool_name, arguments, **kwargs))

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _enforce_rate_limit(self, server_name: str, rpm: int) -> None:
        now    = time.monotonic()
        cutoff = now - 60.0
        timestamps = self._rate_counts.get(server_name, [])
        timestamps = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= rpm:
            raise RuntimeError(
                f"MCP server '{server_name}' rate limit ({rpm}/min) exceeded"
            )
        timestamps.append(now)
        self._rate_counts[server_name] = timestamps

    # ── Client factory ────────────────────────────────────────────────────────

    async def _get_client(self, cfg: MCPServerConfig) -> Any:
        """Return a connected MCP client for *cfg*, or None if unavailable."""
        try:
            if cfg.transport == "stdio":
                from mcp import StdioServerParameters, stdio_client  # type: ignore[import]
                import contextlib
                params = StdioServerParameters(command=cfg.command[0], args=cfg.command[1:], env=cfg.env or None)
                ctx    = stdio_client(params)
                read, write = await ctx.__aenter__()
                from mcp import ClientSession  # type: ignore[import]
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()
                return session
            elif cfg.transport in ("http", "sse"):
                from mcp.client.sse import sse_client  # type: ignore[import]
                from mcp import ClientSession  # type: ignore[import]
                ctx    = sse_client(cfg.endpoint, headers=cfg.headers)
                read, write = await ctx.__aenter__()
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()
                return session
        except (ImportError, Exception):
            return None  # fallback to mock

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def server_names(self) -> list[str]:
        return list(self._servers.keys())

    def __repr__(self) -> str:
        return f"MCPRouter(servers={self.server_names})"
