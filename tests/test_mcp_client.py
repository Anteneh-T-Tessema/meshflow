import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock

from meshflow.mcp.client import MCPClientSession, MCPClient, MCPClientError, MCPRemoteTool

class MockResponse:
    def __init__(self, data: dict):
        self.data = data

    def read(self):
        return json.dumps(self.data).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

def mock_urlopen_success(data):
    return MagicMock(return_value=MockResponse(data))

# ── MCPClientSession Tests ──────────────────────────────────────────────────

def test_client_session_initialize():
    session = MCPClientSession("http://localhost:3000")
    
    mock_resp = {"jsonrpc": "2.0", "id": "123", "result": {"serverInfo": {"name": "TestServer"}}}
    with patch("urllib.request.urlopen", return_value=MockResponse(mock_resp)):
        res = session.initialize()
        assert res["serverInfo"]["name"] == "TestServer"
        assert session._initialized is True
        assert session.server_info["name"] == "TestServer"

def test_client_session_list_tools():
    session = MCPClientSession("http://localhost:3000")
    session._initialized = True  # skip initialize call
    
    mock_resp = {
        "jsonrpc": "2.0",
        "result": {
            "tools": [
                {"name": "test_tool", "description": "A test tool", "inputSchema": {}}
            ]
        }
    }
    with patch("urllib.request.urlopen", return_value=MockResponse(mock_resp)):
        tools = session.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "test_tool"
        assert tools[0].server_url == "http://localhost:3000"
        
        # Check property
        assert len(session.cached_tools) == 1
        assert session.cached_tools[0].name == "test_tool"
        
        # Check to_tool_schema
        schema = tools[0].to_tool_schema()
        assert schema["name"] == "test_tool"

def test_client_session_call_tool():
    session = MCPClientSession("http://localhost:3000")
    session._initialized = True
    
    mock_resp = {
        "jsonrpc": "2.0",
        "result": {
            "content": [{"type": "text", "text": "Success!"}]
        }
    }
    with patch("urllib.request.urlopen", return_value=MockResponse(mock_resp)):
        result = session.call_tool("test_tool", {})
        assert result == "Success!"

def test_client_session_rpc_error():
    session = MCPClientSession("http://localhost:3000")
    
    mock_resp = {
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": "Method not found"}
    }
    with patch("urllib.request.urlopen", return_value=MockResponse(mock_resp)):
        with pytest.raises(MCPClientError) as exc:
            session._rpc("unknown_method")
        assert exc.value.code == -32601
        assert "Method not found" in exc.value.message

@pytest.mark.asyncio
async def test_client_session_async_methods():
    session = MCPClientSession("http://localhost:3000")
    
    mock_init = {"jsonrpc": "2.0", "result": {"serverInfo": {"name": "AsyncServer"}}}
    with patch("urllib.request.urlopen", return_value=MockResponse(mock_init)):
        res = await session.initialize_async()
        assert res["serverInfo"]["name"] == "AsyncServer"

    mock_tools = {"jsonrpc": "2.0", "result": {"tools": [{"name": "async_tool"}]}}
    with patch("urllib.request.urlopen", return_value=MockResponse(mock_tools)):
        tools = await session.list_tools_async()
        assert len(tools) == 1
        assert tools[0].name == "async_tool"

    mock_call = {"jsonrpc": "2.0", "result": {"content": [{"text": "async_result"}]}}
    with patch("urllib.request.urlopen", return_value=MockResponse(mock_call)):
        res = await session.call_tool_async("async_tool", {})
        assert res == "async_result"

# ── MCPClient Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_client_connect_and_routing():
    mock_init = {"jsonrpc": "2.0", "result": {"serverInfo": {"name": "S1"}}}
    mock_tools = {"jsonrpc": "2.0", "result": {"tools": [{"name": "tool_1"}]}}
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            MockResponse(mock_init),
            MockResponse(mock_tools)
        ]
        client = await MCPClient.connect(["http://server1:3000"])
        
        assert client.session_count() == 1
        assert len(client.all_tools()) == 1
        assert client.tool_names() == ["tool_1"]
        
        t = client.find_tool("tool_1")
        assert t is not None
        assert t.name == "tool_1"
        assert client.find_tool("nonexistent") is None

        stats = client.stats()
        assert stats["servers"] == 1
        assert stats["tools"] == 1

        # Test call_tool through client
        mock_call = {"jsonrpc": "2.0", "result": {"content": [{"text": "called"}]}}
        mock_urlopen.side_effect = [MockResponse(mock_call)]
        
        res = await client.call_tool("tool_1", {})
        assert res == "called"
        
        # Test unknown tool
        with pytest.raises(MCPClientError):
            await client.call_tool("unknown_tool", {})

@pytest.mark.asyncio
async def test_mcp_client_connect_failure_skip_unreachable():
    with patch("urllib.request.urlopen", side_effect=Exception("Connection failed")):
        # Should suppress error
        client = await MCPClient.connect(["http://badurl"], skip_unreachable=True)
        assert client.session_count() == 0
        
        # Should raise error
        with pytest.raises(Exception):
            await MCPClient.connect(["http://badurl"], skip_unreachable=False)

def test_mcp_client_add_session():
    client = MCPClient()
    session = MCPClientSession("http://localhost")
    session._tools = [MCPRemoteTool("t1", "", {}, "http://localhost")]
    client.add_session(session)
    assert client.session_count() == 1
    assert client.tool_names() == ["t1"]
