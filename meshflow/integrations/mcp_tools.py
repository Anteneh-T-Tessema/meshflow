"""MCP (Model Context Protocol) tool loader.

Load tools from any MCP server into MeshFlow's tool registry.
MCP tools become first-class MeshFlow Tools — governed, auditable, rate-limited.

Usage:
    from meshflow.integrations.mcp_tools import tools_from_mcp_server, MCPToolLoader

    # Load all tools from an MCP server
    tools = await tools_from_mcp_server("http://localhost:3000/mcp")

    # Use them in an agent
    agent = Agent(name="researcher", role="researcher", tools=tools)

    # Or load from a stdio MCP server command
    tools = await tools_from_mcp_command(["npx", "-y", "@modelcontextprotocol/server-filesystem"])
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, cast

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool


@dataclass
class MCPServerInfo:
    name: str
    version: str
    url: str
    tool_count: int


class MCPToolLoader:
    """Load and govern tools from an MCP server.

    Connects to any MCP-compliant server over HTTP (SSE or JSON-RPC)
    and imports every tool as a governed MeshFlow Tool.
    """

    def __init__(
        self,
        server_url: str,
        default_risk: RiskTier = RiskTier.EXTERNAL_IO,
        timeout: float = 30.0,
    ) -> None:
        self._url = server_url.rstrip("/")
        self._default_risk = default_risk
        self._timeout = timeout

    async def load(self) -> list[Tool]:
        """Discover all tools from the MCP server and return MeshFlow Tools."""
        tools_data = await self._list_tools()
        return [self._mcp_tool_to_mesh(t) for t in tools_data]

    async def server_info(self) -> MCPServerInfo:
        """Fetch server name, version, and tool count."""
        info = await self._initialize()
        tools = await self._list_tools()
        return MCPServerInfo(
            name=info.get("serverInfo", {}).get("name", "mcp_server"),
            version=info.get("serverInfo", {}).get("version", "unknown"),
            url=self._url,
            tool_count=len(tools),
        )

    async def _initialize(self) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "meshflow", "version": "0.8.0"},
            },
        }
        return await self._rpc(payload)

    async def _list_tools(self) -> list[dict[str, Any]]:
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        result = await self._rpc(payload)
        return cast(list[dict[str, Any]], result.get("tools", []))

    async def _call_tool(self, name: str, args: dict[str, Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
        result = await self._rpc(payload)
        content = result.get("content", [])
        if content and isinstance(content, list):
            parts = [c.get("text", str(c)) for c in content if isinstance(c, dict)]
            return "\n".join(parts)
        return str(result)

    async def _rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        import urllib.request

        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=self._timeout).read(),
            )
            response = cast(dict[str, Any], json.loads(raw))
            if "error" in response:
                raise RuntimeError(f"MCP error: {response['error']}")
            return cast(dict[str, Any], response.get("result", response))
        except Exception as e:
            raise RuntimeError(f"MCP call to {self._url} failed: {e}") from e

    def _mcp_tool_to_mesh(self, mcp_tool: dict[str, Any]) -> Tool:
        name = str(mcp_tool.get("name", "mcp_tool"))
        description = str(mcp_tool.get("description", name))
        mcp_tool.get("inputSchema", {})

        async def _call(**kwargs: Any) -> Any:
            return await self._call_tool(name, kwargs)

        return Tool(
            name=name,
            description=description,
            fn=_call,
            risk=self._default_risk,
            tags=["mcp", "external"],
        )


# ── Convenience functions ─────────────────────────────────────────────────────


async def tools_from_mcp_server(
    url: str,
    risk: RiskTier = RiskTier.EXTERNAL_IO,
    timeout: float = 30.0,
) -> list[Tool]:
    """Load all tools from an MCP HTTP server as MeshFlow Tools.

    Args:
        url:     MCP server URL (e.g. "http://localhost:3000/mcp")
        risk:    Risk tier to assign to all loaded tools
        timeout: HTTP timeout in seconds
    """
    loader = MCPToolLoader(url, default_risk=risk, timeout=timeout)
    return await loader.load()


async def tools_from_mcp_command(
    command: list[str],
    risk: RiskTier = RiskTier.EXTERNAL_IO,
    timeout: float = 30.0,
) -> list[Tool]:
    """Start an MCP stdio server via command and load its tools.

    Args:
        command: Command to start the MCP server, e.g.
                 ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        risk:    Risk tier for all loaded tools
        timeout: Startup + first-response timeout in seconds

    The server is started as a subprocess. Tools communicate via stdin/stdout
    using the MCP stdio transport (newline-delimited JSON-RPC).
    """
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Failed to start MCP subprocess")

    async def _rpc_stdio(payload: dict[str, Any]) -> dict[str, Any]:
        line = (json.dumps(payload) + "\n").encode()
        proc.stdin.write(line)  # type: ignore[union-attr]
        await proc.stdin.drain()  # type: ignore[union-attr]
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)  # type: ignore[union-attr]
        response = cast(dict[str, Any], json.loads(raw))
        return cast(dict[str, Any], response.get("result", response))

    # Initialize
    await _rpc_stdio(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "meshflow", "version": "0.8.0"},
            },
        }
    )

    # List tools
    result = await _rpc_stdio({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools_data = result.get("tools", [])

    def _make_tool(mcp_tool: dict[str, Any]) -> Tool:
        name = str(mcp_tool.get("name", "mcp_tool"))
        description = str(mcp_tool.get("description", name))

        async def _call(**kwargs: Any) -> Any:
            res = await _rpc_stdio(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": kwargs},
                }
            )
            content = res.get("content", [])
            if content:
                return "\n".join(c.get("text", str(c)) for c in content if isinstance(c, dict))
            return str(res)

        return Tool(name=name, description=description, fn=_call, risk=risk, tags=["mcp", "stdio"])

    return [_make_tool(t) for t in tools_data]
