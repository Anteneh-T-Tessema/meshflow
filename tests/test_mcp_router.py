import pytest
import asyncio
from unittest.mock import patch, MagicMock

from meshflow.mcp.router import MCPRouter, MCPServerConfig, MCPAuthPolicy, MCPDeniedError
from meshflow.mcp.client import MCPRemoteTool

class MockClient:
    def __init__(self, tools, call_result):
        self.tools = tools
        self.call_result = call_result
        self.list_called = 0
        self.call_called = 0

    async def list_tools(self):
        self.list_called += 1
        class Res:
            tools = self.tools
        return Res()

    async def call_tool(self, name, args):
        self.call_called += 1
        class TextObj:
            def __init__(self, t):
                self.text = t
        class Res:
            content = [TextObj(self.call_result)]
            isError = False
        return Res()

@pytest.fixture
def base_servers():
    return [
        MCPServerConfig(
            name="filesystem",
            policy=MCPAuthPolicy(allow_tools=["read_file", "write_file"], deny_tools=["delete_file"]),
            priority=10
        ),
        MCPServerConfig(
            name="db",
            policy=MCPAuthPolicy(rate_limit_per_min=2),
            priority=5
        )
    ]

@pytest.fixture
def router(base_servers):
    return MCPRouter(servers=base_servers, fallback_to_mock=False)

# ── MCPAuthPolicy Tests ────────────────────────────────────────────────────

def test_auth_policy():
    p = MCPAuthPolicy(allow_tools=["a", "b"], deny_tools=["b", "c"])
    assert p.is_allowed("a") is True
    assert p.is_allowed("b") is False  # deny takes precedence
    assert p.is_allowed("c") is False
    assert p.is_allowed("d") is False  # not in allow list

    p2 = MCPAuthPolicy(deny_tools=["c"])
    assert p2.is_allowed("a") is True  # empty allow list -> all allowed

    p3 = MCPAuthPolicy(require_approval=["a"])
    assert p3.requires_approval("a") is True
    assert p3.requires_approval("b") is False

# ── MCPRouter Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_list_tools_fallback_mock(base_servers):
    r = MCPRouter(servers=base_servers, fallback_to_mock=True)
    with patch.object(r, "_get_client", return_value=None):
        tools = await r.list_tools()
        # Mock tools for 'filesystem' and 'db'
        names = {t.name for t in tools}
        assert "read_file" in names
        assert "write_file" in names
        assert "delete_file" not in names # blocked by filesystem policy
        assert "tool_a" in names # default mock for db
        assert "tool_b" in names

@pytest.mark.asyncio
async def test_router_routing_priority(base_servers):
    # Both servers offer 'shared_tool', but 'db' has priority 5 and 'fs' has 10
    r = MCPRouter(servers=base_servers, fallback_to_mock=False)
    
    class Tool:
        def __init__(self, name, desc="", inputSchema={}):
            self.name = name
            self.description = desc
            self.inputSchema = inputSchema
            
    mock_db = MockClient([Tool("shared_tool")], "db_res")
    mock_fs = MockClient([Tool("shared_tool")], "fs_res")

    async def get_client_mock(cfg):
        return mock_db if cfg.name == "db" else mock_fs
        
    with patch.object(r, "_get_client", side_effect=get_client_mock):
        cfg = await r.route("shared_tool")
        assert cfg.name == "db"  # lower priority wins

@pytest.mark.asyncio
async def test_router_call_denied_by_policy(router):
    with pytest.raises(MCPDeniedError):
        # 'delete_file' is in filesystem deny_tools
        await router.call("delete_file", {}, server_name="filesystem")

@pytest.mark.asyncio
async def test_router_call_rate_limit(router):
    class Tool:
        def __init__(self, name):
            self.name = name
            self.description = ""
            self.inputSchema = {}
            
    mock_client = MockClient([Tool("query")], "ok")
    
    with patch.object(router, "_get_client", return_value=mock_client):
        # Rate limit is 2 per minute for 'db'
        await router.call("query", {}, server_name="db")
        await router.call("query", {}, server_name="db")
        
        with pytest.raises(RuntimeError) as exc:
            await router.call("query", {}, server_name="db")
        assert "rate limit" in str(exc.value)

@pytest.mark.asyncio
async def test_router_call_success_and_telemetry(base_servers):
    mock_cloud = MagicMock()
    r = MCPRouter(servers=base_servers, cloud=mock_cloud, fallback_to_mock=False)
    
    class Tool:
        def __init__(self, name):
            self.name = name
            self.description = ""
            self.inputSchema = {}
            
    mock_client = MockClient([Tool("read_file")], "file_content")
    
    with patch.object(r, "_get_client", return_value=mock_client):
        res = await r.call("read_file", server_name="filesystem")
        assert res.success is True
        assert res.content == "file_content"
        assert res.server_name == "filesystem"
        assert res.latency_ms > 0
        
        mock_cloud.report_mcp_call.assert_called_once()
        args = mock_cloud.report_mcp_call.call_args[1]
        assert args["server_name"] == "filesystem"
        assert args["tool_name"] == "read_file"
        assert args["success"] is True

def test_router_call_sync(router):
    with patch.object(router, "call", return_value="sync_res") as mock_call:
        res = router.call_sync("read_file")
        # Ensure it works synchronously by using the meshflow run_sync
        assert mock_call.called
