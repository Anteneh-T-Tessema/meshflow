"""Sprint 38 — MCP client: consume external MCP servers from any Agent."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.mcp.client import MCPClient, MCPClientError, MCPClientSession, MCPRemoteTool


# ── Minimal in-process MCP server for testing ─────────────────────────────────

_TOOLS = [
    {
        "name": "echo",
        "description": "Echoes the input text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Adds two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
    },
]


class _MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        method = body.get("method", "")
        msg_id = body.get("id")

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-mcp", "version": "0.1"},
            }
        elif method == "tools/list":
            result = {"tools": _TOOLS}
        elif method == "tools/call":
            name = body.get("params", {}).get("name", "")
            args = body.get("params", {}).get("arguments", {})
            if name == "echo":
                text = args.get("text", "")
                result = {"content": [{"type": "text", "text": f"echo:{text}"}]}
            elif name == "add":
                result = {"content": [{"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}]}
            else:
                self._send({"jsonrpc": "2.0", "id": msg_id,
                            "error": {"code": -32601, "message": f"Unknown tool: {name}"}})
                return
        else:
            self._send({"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32601, "message": "Method not found"}})
            return

        self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _send(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_mcp_server(port: int) -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", port), _MCPHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


@pytest.fixture(scope="module")
def mcp_server():
    port = 19300
    srv = _start_mcp_server(port)
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


# ── MCPRemoteTool ─────────────────────────────────────────────────────────────

class TestMCPRemoteTool:
    def test_to_tool_schema(self):
        t = MCPRemoteTool(
            name="search",
            description="search the web",
            input_schema={"type": "object"},
            server_url="http://localhost",
        )
        schema = t.to_tool_schema()
        assert schema["name"] == "search"
        assert "inputSchema" in schema


# ── MCPClientSession ──────────────────────────────────────────────────────────

class TestMCPClientSession:
    def test_initialize(self, mcp_server):
        session = MCPClientSession(mcp_server)
        info = session.initialize()
        assert session._initialized
        assert "protocolVersion" in info or isinstance(info, dict)

    def test_list_tools(self, mcp_server):
        session = MCPClientSession(mcp_server)
        tools = session.list_tools()
        assert len(tools) == 2
        names = [t.name for t in tools]
        assert "echo" in names
        assert "add" in names

    def test_list_tools_auto_initializes(self, mcp_server):
        session = MCPClientSession(mcp_server)
        assert not session._initialized
        session.list_tools()
        assert session._initialized

    def test_cached_tools(self, mcp_server):
        session = MCPClientSession(mcp_server)
        session.list_tools()
        assert len(session.cached_tools) == 2

    def test_call_tool_echo(self, mcp_server):
        session = MCPClientSession(mcp_server)
        result = session.call_tool("echo", {"text": "hello"})
        assert "hello" in result

    def test_call_tool_add(self, mcp_server):
        session = MCPClientSession(mcp_server)
        result = session.call_tool("add", {"a": 3, "b": 4})
        assert "7" in result

    def test_call_unknown_tool_raises(self, mcp_server):
        session = MCPClientSession(mcp_server)
        with pytest.raises(MCPClientError):
            session.call_tool("nonexistent", {})

    def test_server_info(self, mcp_server):
        session = MCPClientSession(mcp_server)
        session.initialize()
        info = session.server_info
        assert isinstance(info, dict)

    def test_tool_has_server_url(self, mcp_server):
        session = MCPClientSession(mcp_server)
        tools = session.list_tools()
        assert all(t.server_url == mcp_server for t in tools)

    @pytest.mark.asyncio
    async def test_list_tools_async(self, mcp_server):
        session = MCPClientSession(mcp_server)
        tools = await session.list_tools_async()
        assert len(tools) == 2

    @pytest.mark.asyncio
    async def test_call_tool_async(self, mcp_server):
        session = MCPClientSession(mcp_server)
        result = await session.call_tool_async("echo", {"text": "world"})
        assert "world" in result


# ── MCPClient ─────────────────────────────────────────────────────────────────

class TestMCPClient:
    @pytest.mark.asyncio
    async def test_connect(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        assert client.session_count() == 1

    @pytest.mark.asyncio
    async def test_all_tools(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        tools = client.all_tools()
        assert len(tools) == 2

    @pytest.mark.asyncio
    async def test_tool_names(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        names = client.tool_names()
        assert "echo" in names and "add" in names

    @pytest.mark.asyncio
    async def test_find_tool(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        tool = client.find_tool("echo")
        assert tool is not None
        assert tool.name == "echo"

    @pytest.mark.asyncio
    async def test_find_tool_missing(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        assert client.find_tool("nonexistent") is None

    @pytest.mark.asyncio
    async def test_call_tool(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        result = await client.call_tool("echo", {"text": "meshflow"})
        assert "meshflow" in result

    @pytest.mark.asyncio
    async def test_call_tool_not_found_raises(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        with pytest.raises(MCPClientError):
            await client.call_tool("unknown_tool", {})

    @pytest.mark.asyncio
    async def test_unreachable_server_skipped(self):
        client = await MCPClient.connect(["http://127.0.0.1:19399"], skip_unreachable=True)
        assert client.session_count() == 0
        assert client.all_tools() == []

    @pytest.mark.asyncio
    async def test_stats(self, mcp_server):
        client = await MCPClient.connect([mcp_server])
        stats = client.stats()
        assert stats["servers"] == 1
        assert stats["tools"] == 2
        assert "echo" in stats["tool_names"]

    def test_add_session_manually(self, mcp_server):
        session = MCPClientSession(mcp_server)
        session.list_tools()
        client = MCPClient()
        client.add_session(session)
        assert client.session_count() == 1
        assert len(client.all_tools()) == 2


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.mcp.client import MCPClient, MCPClientSession, MCPRemoteTool, MCPClientError
        assert all(x is not None for x in [MCPClient, MCPClientSession, MCPRemoteTool, MCPClientError])

    def test_mcp_init_exports(self):
        from meshflow.mcp import MCPClient, MCPClientSession, MCPRemoteTool
        assert all(x is not None for x in [MCPClient, MCPClientSession, MCPRemoteTool])
