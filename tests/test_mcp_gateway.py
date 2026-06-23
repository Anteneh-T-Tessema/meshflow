import pytest
import asyncio
from unittest.mock import MagicMock
import hashlib

from meshflow.mcp.gateway import MCPGateway, ToolManifest, RateLimiterState
from meshflow.core.schemas import MCPToolCall

# ── ToolManifest Tests ──────────────────────────────────────────────────────

def test_tool_manifest_signature():
    # Valid signature
    name, uri, desc = "test", "http://a", "desc"
    expected = hashlib.sha256(f"{name}:{uri}:{desc}".encode()).hexdigest()
    m1 = ToolManifest(tool_name=name, server_uri=uri, description=desc, signature=expected)
    assert m1.validate_signature() is True

    # Invalid signature
    m2 = ToolManifest(tool_name=name, server_uri=uri, description=desc, signature="bad")
    assert m2.validate_signature() is False

    # Trusted override
    m3 = ToolManifest(tool_name=name, server_uri=uri, description=desc, signature="bad", trusted=True)
    assert m3.validate_signature() is True

def test_rate_limiter_state():
    rl = RateLimiterState()
    assert rl.allow(2) is True
    assert rl.allow(2) is True
    assert rl.allow(2) is False  # Limit reached

# ── MCPGateway Tests ────────────────────────────────────────────────────────

@pytest.fixture
def gateway():
    return MCPGateway(budget_usd_per_turn=0.10)

@pytest.fixture
def valid_manifest():
    name, uri, desc = "safe_tool", "http://local", "safe"
    sig = hashlib.sha256(f"{name}:{uri}:{desc}".encode()).hexdigest()
    return ToolManifest(
        tool_name=name, server_uri=uri, description=desc, signature=sig,
        max_cost_usd=0.05, max_calls_per_minute=2, allowed_agent_roles=["admin"]
    )

@pytest.mark.asyncio
async def test_gateway_missing_manifest(gateway):
    call = await gateway.call("unknown", {}, "a1", "admin", "t1", None)
    assert call.blocked is True
    assert "not in registry" in call.block_reason
    assert gateway.blocked_count() == 1

@pytest.mark.asyncio
async def test_gateway_invalid_signature(gateway, valid_manifest):
    valid_manifest.signature = "bad"
    gateway.register_tool(valid_manifest)
    call = await gateway.call("safe_tool", {}, "a1", "admin", "t1", None)
    assert call.blocked is True
    assert "signature invalid" in call.block_reason

@pytest.mark.asyncio
async def test_gateway_role_not_allowed(gateway, valid_manifest):
    gateway.register_tool(valid_manifest)
    call = await gateway.call("safe_tool", {}, "a1", "guest", "t1", None)
    assert call.blocked is True
    assert "Role 'guest' not allowed" in call.block_reason

@pytest.mark.asyncio
async def test_gateway_rate_limit(gateway, valid_manifest):
    gateway.register_tool(valid_manifest)
    async def dummy_handler(name, params): return "ok"
    
    # 2 calls allowed
    await gateway.call("safe_tool", {}, "a1", "admin", "t1", dummy_handler)
    await gateway.call("safe_tool", {}, "a1", "admin", "t1", dummy_handler)
    
    # 3rd blocked
    call = await gateway.call("safe_tool", {}, "a1", "admin", "t1", dummy_handler)
    assert call.blocked is True
    assert "Rate limit exceeded" in call.block_reason

@pytest.mark.asyncio
async def test_gateway_budget_cap(gateway, valid_manifest):
    valid_manifest.max_cost_usd = 0.50 # > 0.10 gateway budget
    gateway.register_tool(valid_manifest)
    call = await gateway.call("safe_tool", {}, "a1", "admin", "t1", None)
    assert call.blocked is True
    assert "exceeds turn budget" in call.block_reason

@pytest.mark.asyncio
async def test_gateway_interceptor(gateway, valid_manifest):
    class MockInterceptor:
        async def before_call(self, event):
            from meshflow.core.tool_intercept import ToolCallDecision
            if event.args.get("bad"):
                return ToolCallDecision(allowed=False, block_reason="bad arg")
            return ToolCallDecision(allowed=True, modified_args={"modified": True})
            
    gateway._interceptor = MockInterceptor()
    gateway.register_tool(valid_manifest)
    
    # Blocked by interceptor
    call1 = await gateway.call("safe_tool", {"bad": True}, "a1", "admin", "t1", None)
    assert call1.blocked is True
    assert "interceptor:bad arg" in call1.block_reason
    
    # Modified by interceptor
    handled_args = {}
    async def dummy_handler(name, params):
        handled_args.update(params)
        return "ok"
        
    call2 = await gateway.call("safe_tool", {}, "a1", "admin", "t1", dummy_handler)
    assert call2.blocked is False
    assert handled_args.get("modified") is True

@pytest.mark.asyncio
async def test_gateway_success_and_stats(gateway, valid_manifest):
    gateway.register_tools([valid_manifest])
    
    async def dummy_handler(name, params):
        if params.get("fail"):
            raise ValueError("failed handler")
        return "ok"
        
    # Success
    call = await gateway.call("safe_tool", {}, "a1", "admin", "t1", dummy_handler)
    assert call.blocked is False
    assert call.result == "ok"
    assert call.cost_usd == 0.05
    assert call.latency_ms is not None
    
    # Handler failure (not blocked, but result is None)
    call2 = await gateway.call("safe_tool", {"fail": True}, "a1", "admin", "t1", dummy_handler)
    assert call2.blocked is False
    assert call2.result is None
    assert "failed handler" in call2.block_reason

    stats = gateway.stats()
    assert stats["total_calls"] == 2
    assert stats["total_cost_usd"] == 0.10
    assert stats["registered_tools"] == 1
