"""Tests for the MeshFlow MCP server implementation."""

from __future__ import annotations

import asyncio
import json
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rpc(method: str, params: dict = None, id: int = 1) -> dict:
    msg = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        msg["params"] = params
    return msg


def _notification(method: str, params: dict = None) -> dict:
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


async def _call(srv, method, params=None, id=1):
    return await srv.handle_request(_rpc(method, params, id))


# ── initialize ────────────────────────────────────────────────────────────────

def test_mcp_initialize_returns_server_info():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer(name="TestServer")
    response = asyncio.run(_call(srv, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    }))

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "TestServer"
    assert "tools" in result["capabilities"]


def test_mcp_initialize_sets_initialized_flag():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    assert not srv._initialized
    asyncio.run(_call(srv, "initialize", {}))
    assert srv._initialized


def test_mcp_notification_returns_none():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(srv.handle_request(
        _notification("notifications/initialized")
    ))
    assert response is None


# ── tools/list ────────────────────────────────────────────────────────────────

def test_mcp_tools_list_returns_builtin_tools():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "tools/list", {}))

    tools = response["result"]["tools"]
    tool_names = {t["name"] for t in tools}

    assert "meshflow_run" in tool_names
    assert "meshflow_approve_hitl" in tool_names
    assert "meshflow_reject_hitl" in tool_names
    assert "meshflow_get_trace" in tool_names
    assert "meshflow_list_runs" in tool_names


def test_mcp_tools_list_has_valid_input_schemas():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "tools/list", {}))
    for tool in response["result"]["tools"]:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema


def test_mcp_tools_list_includes_registered_agents():
    from meshflow.mcp.server import MCPServer

    class _MockAgent:
        name = "tester"
        role = "researcher"

        async def run(self, task, ctx):
            return {"result": "done", "tokens": 5, "cost_usd": 0.001, "stated_confidence": 0.9}

    srv = MCPServer()
    srv.register_agent(_MockAgent(), description="A test agent")

    response = asyncio.run(_call(srv, "tools/list", {}))
    tool_names = {t["name"] for t in response["result"]["tools"]}
    assert "agent_tester" in tool_names


def test_mcp_tools_list_includes_registered_teams():
    from meshflow.mcp.server import MCPServer

    class _MockTeam:
        name = "dev_team"
        pattern = "sequential"

        async def run(self, task, context=None):
            from dataclasses import dataclass
            @dataclass
            class R:
                completed = True
                paused_nodes = []
                blocked_nodes = []
                total_cost_usd = 0.01
                total_tokens = 50
                run_id = "test-run"
                output = "team result"
            return R()

    srv = MCPServer()
    srv.register_team(_MockTeam())

    response = asyncio.run(_call(srv, "tools/list", {}))
    tool_names = {t["name"] for t in response["result"]["tools"]}
    assert "team_dev_team" in tool_names


# ── tools/call ────────────────────────────────────────────────────────────────

def test_mcp_tools_call_unknown_tool_returns_error():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "tools/call", {
        "name": "nonexistent_tool",
        "arguments": {},
    }))

    assert "error" in response
    assert response["error"]["code"] == -32602


def test_mcp_tools_call_missing_task_returns_error():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "tools/call", {
        "name": "meshflow_run",
        "arguments": {},  # missing 'task'
    }))

    assert "error" in response
    assert response["error"]["code"] == -32602


def test_mcp_tools_call_registered_agent():
    from meshflow.mcp.server import MCPServer

    class _MockAgent:
        name = "mock"
        role = "executor"

        async def run(self, task, ctx):
            return {
                "result": f"executed: {task}",
                "tokens": 10,
                "cost_usd": 0.001,
                "stated_confidence": 0.95,
            }

    srv = MCPServer()
    srv.register_agent(_MockAgent(), description="Mock agent")

    response = asyncio.run(_call(srv, "tools/call", {
        "name": "agent_mock",
        "arguments": {"task": "test task"},
    }))

    assert "result" in response
    content = response["result"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert "executed: test task" in content[0]["text"]
    assert response["result"]["isError"] is False


def test_mcp_tools_call_list_runs_empty():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer(ledger_path=":memory:")
    response = asyncio.run(_call(srv, "tools/call", {
        "name": "meshflow_list_runs",
        "arguments": {"limit": 5},
    }))

    assert "result" in response
    text = response["result"]["content"][0]["text"]
    assert "No runs" in text


def test_mcp_tools_call_get_trace_missing_run():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer(ledger_path=":memory:")
    response = asyncio.run(_call(srv, "tools/call", {
        "name": "meshflow_get_trace",
        "arguments": {"run_id": "nonexistent-run-id"},
    }))

    assert "result" in response
    text = response["result"]["content"][0]["text"]
    assert "not found" in text.lower() or "ERROR" in text


def test_mcp_tools_call_approve_hitl_missing_run():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer(ledger_path=":memory:")
    response = asyncio.run(_call(srv, "tools/call", {
        "name": "meshflow_approve_hitl",
        "arguments": {"run_id": "nonexistent", "reviewer_id": "test"},
    }))

    assert "result" in response
    text = response["result"]["content"][0]["text"]
    assert "not found" in text.lower() or "ERROR" in text


# ── error handling ────────────────────────────────────────────────────────────

def test_mcp_unknown_method_returns_method_not_found():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "unknown/method", {}))

    assert "error" in response
    assert response["error"]["code"] == -32601
    assert "not found" in response["error"]["message"].lower()


def test_mcp_ping():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "ping", {}))
    assert "result" in response
    assert response["result"] == {}


def test_mcp_resources_list_returns_empty():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "resources/list", {}))
    assert response["result"]["resources"] == []


def test_mcp_prompts_list_returns_empty():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    response = asyncio.run(_call(srv, "prompts/list", {}))
    assert response["result"]["prompts"] == []


# ── tool_list() ───────────────────────────────────────────────────────────────

def test_mcp_tool_list_format():
    from meshflow.mcp.server import MCPServer

    srv = MCPServer()
    tools = srv.tool_list()
    assert isinstance(tools, list)
    assert len(tools) >= 5  # at least the 5 built-ins

    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"


def test_mcp_register_agent_custom_tool_name():
    from meshflow.mcp.server import MCPServer

    class _A:
        name = "my_agent"
        role = "researcher"

        async def run(self, task, ctx):
            return {"result": "done", "tokens": 1, "cost_usd": 0.0, "stated_confidence": 0.9}

    srv = MCPServer()
    srv.register_agent(_A(), tool_name="custom_tool_name", description="Custom desc")

    tool_names = {t["name"] for t in srv.tool_list()}
    assert "custom_tool_name" in tool_names
    assert "agent_my_agent" not in tool_names


# ── from_config ───────────────────────────────────────────────────────────────

def test_mcp_from_config_loads_agents():
    from meshflow.mcp.server import from_config

    yaml_content = """\
version: "1.0"
policy:
  mode: dev
agents:
  - name: researcher
    role: researcher
  - name: writer
    role: executor
team:
  name: my_team
  pattern: sequential
  agents: [researcher, writer]
"""
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        path = f.name

    try:
        srv = from_config(path)
        tool_names = {t["name"] for t in srv.tool_list()}
        assert "agent_researcher" in tool_names
        assert "agent_writer" in tool_names
        assert "team_my_team" in tool_names
    finally:
        os.unlink(path)


# ── HTTP endpoint integration ─────────────────────────────────────────────────

def test_mcp_server_registered_in_aiohttp_app():
    """The /mcp route is registered in the aiohttp application."""
    from meshflow.runtime.server import _build_app

    app = asyncio.run(_build_app(set(), ":memory:"))
    routes = {r.resource.canonical for r in app.router.routes()}
    assert "/mcp" in routes


def test_mcp_discover_endpoint():
    """GET /mcp returns a discovery JSON with tools."""
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
    from meshflow.runtime.server import _build_app

    async def run():
        app = await _build_app(set(), ":memory:")
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/mcp")
            assert resp.status == 200
            data = await resp.json()
            assert data["protocol"] == "mcp"
            assert "tools" in data
            assert len(data["tools"]) >= 5

    asyncio.run(run())


def test_mcp_http_initialize():
    """POST /mcp with initialize returns valid MCP response."""
    from meshflow.runtime.server import _build_app

    async def run():
        app = await _build_app(set(), ":memory:")
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/mcp", json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["result"]["protocolVersion"] == "2024-11-05"
            assert data["result"]["serverInfo"]["name"] == "MeshFlow"

    asyncio.run(run())


def test_mcp_http_tools_list():
    """POST /mcp with tools/list returns all built-in tools."""
    from meshflow.runtime.server import _build_app

    async def run():
        app = await _build_app(set(), ":memory:")
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/mcp", json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            })
            assert resp.status == 200
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "meshflow_run" in tool_names
            assert "meshflow_get_trace" in tool_names

    asyncio.run(run())


def test_mcp_http_notification_returns_204():
    """POST /mcp with a notification (no id) returns 204."""
    from meshflow.runtime.server import _build_app

    async def run():
        app = await _build_app(set(), ":memory:")
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/mcp", json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })
            assert resp.status == 204

    asyncio.run(run())


def test_mcp_http_auth_required():
    """POST /mcp with api_keys set returns 401 when no auth provided."""
    from meshflow.runtime.server import _build_app

    async def run():
        app = await _build_app({"secret-key"}, ":memory:")
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/mcp", json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            })
            assert resp.status == 401

    asyncio.run(run())


def test_mcp_http_auth_bearer_accepted():
    """POST /mcp with valid Bearer token is accepted."""
    from meshflow.runtime.server import _build_app

    async def run():
        app = await _build_app({"my-key"}, ":memory:")
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                },
                headers={"Authorization": "Bearer my-key"},
            )
            assert resp.status == 200

    asyncio.run(run())
