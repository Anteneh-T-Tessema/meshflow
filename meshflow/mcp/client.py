"""MCP client — connect to external MCP servers and consume their tools.

Supports HTTP transport (JSON-RPC 2.0 POST to a single endpoint).
Zero external dependencies — uses stdlib urllib only.

Usage::

    # Single server
    session = MCPClientSession("http://localhost:3000")
    tools = session.list_tools()
    result = session.call_tool("search", {"query": "HIPAA"})

    # Multi-server via MCPClient
    import asyncio
    client = asyncio.run(MCPClient.connect(["http://localhost:3000"]))
    for tool in client.all_tools():
        print(tool.name)
    result = asyncio.run(client.call_tool("search", {"query": "HIPAA"}))
"""

from __future__ import annotations

import json
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


# ── Wire types ─────────────────────────────────────────────────────────────────

@dataclass
class MCPRemoteTool:
    """A tool discovered from a remote MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_url: str

    def to_tool_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# ── Session (one server) ───────────────────────────────────────────────────────

class MCPClientSession:
    """JSON-RPC 2.0 session with one MCP server (HTTP transport).

    Lazily initialises on first ``list_tools()`` or ``call_tool()`` call.
    """

    def __init__(self, url: str, timeout_s: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self._initialized = False
        self._tools: list[MCPRemoteTool] = []
        self._server_info: dict[str, Any] = {}

    # ── JSON-RPC transport ────────────────────────────────────────────────────

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send one JSON-RPC 2.0 request; return ``result`` or raise."""
        msg = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4())[:8],
            "method": method,
            "params": params or {},
        }
        payload = json.dumps(msg).encode()
        req = urllib.request.Request(
            self.url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.loads(resp.read())
        if "error" in data:
            err = data["error"]
            raise MCPClientError(err.get("code", -1), err.get("message", "rpc error"))
        return data.get("result")

    async def _rpc_async(self, method: str, params: dict[str, Any] | None = None) -> Any:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._rpc(method, params))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def initialize(self) -> dict[str, Any]:
        """MCP initialization handshake."""
        result = self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "meshflow-mcp-client", "version": "0.39.0"},
        })
        self._initialized = True
        self._server_info = (result or {}).get("serverInfo", {})
        return result or {}

    async def initialize_async(self) -> dict[str, Any]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.initialize)

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    # ── Tool discovery ────────────────────────────────────────────────────────

    def list_tools(self) -> list[MCPRemoteTool]:
        """Fetch (and cache) the list of tools from the server."""
        self._ensure_initialized()
        result = self._rpc("tools/list")
        tools_raw = (result or {}).get("tools", [])
        self._tools = [
            MCPRemoteTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", t.get("input_schema", {})),
                server_url=self.url,
            )
            for t in tools_raw
            if t.get("name")
        ]
        return self._tools

    async def list_tools_async(self) -> list[MCPRemoteTool]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.list_tools)

    @property
    def cached_tools(self) -> list[MCPRemoteTool]:
        return list(self._tools)

    # ── Tool invocation ───────────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Invoke a tool on the remote server; return text result."""
        self._ensure_initialized()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result is None:
            return ""
        content = result.get("content", [])
        if content and isinstance(content, list):
            first = content[0]
            if isinstance(first, dict):
                return first.get("text", json.dumps(first))
            return str(first)
        return json.dumps(result)

    async def call_tool_async(self, name: str, arguments: dict[str, Any]) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.call_tool(name, arguments))

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)


# ── Multi-server client ────────────────────────────────────────────────────────

class MCPClient:
    """Multi-server MCP client.

    Connects to one or more MCP servers and provides unified access to all
    discovered tools.

    Usage::

        import asyncio
        client = asyncio.run(MCPClient.connect(["http://localhost:3000"]))
        for t in client.all_tools():
            print(t.name, "—", t.description)
        result = asyncio.run(client.call_tool("my_tool", {"arg": "value"}))
    """

    def __init__(self) -> None:
        self._sessions: list[MCPClientSession] = []

    @classmethod
    async def connect(
        cls,
        urls: list[str],
        timeout_s: float = 10.0,
        skip_unreachable: bool = True,
    ) -> "MCPClient":
        """Connect to all MCP server URLs; initialize and discover tools."""
        import asyncio

        client = cls()
        loop = asyncio.get_event_loop()
        for url in urls:
            session = MCPClientSession(url, timeout_s=timeout_s)
            try:
                await loop.run_in_executor(None, session.initialize)
                await loop.run_in_executor(None, session.list_tools)
                client._sessions.append(session)
            except Exception:
                if not skip_unreachable:
                    raise
        return client

    def add_session(self, session: MCPClientSession) -> None:
        """Manually add an already-initialized session."""
        self._sessions.append(session)

    # ── Tool access ───────────────────────────────────────────────────────────

    def all_tools(self) -> list[MCPRemoteTool]:
        """Return all tools from all connected servers."""
        tools: list[MCPRemoteTool] = []
        for s in self._sessions:
            tools.extend(s.cached_tools)
        return tools

    def tool_names(self) -> list[str]:
        return [t.name for t in self.all_tools()]

    def find_tool(self, name: str) -> MCPRemoteTool | None:
        for t in self.all_tools():
            if t.name == name:
                return t
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a named tool across connected servers."""
        for session in self._sessions:
            for tool in session.cached_tools:
                if tool.name == name:
                    return await session.call_tool_async(name, arguments)
        raise MCPClientError(-32601, f"Tool not found: {name!r}")

    def session_count(self) -> int:
        return len(self._sessions)

    def stats(self) -> dict[str, Any]:
        return {
            "servers": self.session_count(),
            "tools": len(self.all_tools()),
            "tool_names": self.tool_names(),
        }


# ── Error ──────────────────────────────────────────────────────────────────────

class MCPClientError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[MCP {code}] {message}")


__all__ = ["MCPClientSession", "MCPRemoteTool", "MCPClient", "MCPClientError"]
