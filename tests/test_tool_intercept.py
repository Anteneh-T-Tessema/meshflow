"""Tests for ToolCallInterceptor — unit + MCP path + StepRuntime wiring."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from meshflow.core.tool_intercept import (
    AllowListInterceptor,
    ChainedInterceptor,
    PolicyToolCallInterceptor,
    ToolCallDecision,
    ToolCallEvent,
    ToolCallInterceptor,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _event(tool_name: str = "read_file", args: dict | None = None, source: str = "llm") -> ToolCallEvent:
    return ToolCallEvent(
        tool_name=tool_name,
        args=args or {"path": "/tmp/test.txt"},
        agent_id="agent-1",
        source=source,
        run_id="run-abc",
        node_id="node-1",
    )


def _allow_engine() -> MagicMock:
    """PolicyEngine stub that always allows."""
    from meshflow.policy.engine import PolicyDecision, PolicyAction
    engine = MagicMock()
    engine.evaluate.return_value = PolicyDecision(
        action=PolicyAction.ALLOW, rule_name="", reason="default allow", matched=False
    )
    return engine


def _deny_engine(rule_name: str = "block-rule") -> MagicMock:
    """PolicyEngine stub that always denies."""
    from meshflow.policy.engine import PolicyDecision, PolicyAction
    engine = MagicMock()
    engine.evaluate.return_value = PolicyDecision(
        action=PolicyAction.DENY, rule_name=rule_name, reason="matched deny rule", matched=True
    )
    return engine


# ── ToolCallEvent ─────────────────────────────────────────────────────────────

def test_event_has_unique_call_id():
    e1 = _event()
    e2 = _event()
    assert e1.call_id != e2.call_id


def test_event_defaults():
    e = ToolCallEvent(tool_name="foo", args={}, agent_id="a")
    assert e.source == "llm"
    assert e.run_id == ""
    assert e.node_id == ""


# ── ToolCallDecision ──────────────────────────────────────────────────────────

def test_decision_allowed():
    d = ToolCallDecision(allowed=True)
    assert d.allowed
    assert d.block_reason == ""
    assert d.modified_args is None


def test_decision_blocked():
    d = ToolCallDecision(allowed=False, block_reason="policy:deny")
    assert not d.allowed
    assert "policy" in d.block_reason


# ── AllowListInterceptor ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_allow_list_permits_listed_tool():
    i = AllowListInterceptor(["read_file", "search"])
    d = await i.before_call(_event("read_file"))
    assert d.allowed


@pytest.mark.asyncio
async def test_allow_list_blocks_unlisted_tool():
    i = AllowListInterceptor(["read_file"])
    d = await i.before_call(_event("write_file"))
    assert not d.allowed
    assert "write_file" in d.block_reason


@pytest.mark.asyncio
async def test_allow_list_empty_blocks_all():
    i = AllowListInterceptor([])
    d = await i.before_call(_event("anything"))
    assert not d.allowed


# ── PolicyToolCallInterceptor — basic allow/deny ──────────────────────────────

@pytest.mark.asyncio
async def test_policy_interceptor_allows_when_engine_allows():
    i = PolicyToolCallInterceptor(_allow_engine())
    d = await i.before_call(_event())
    assert d.allowed


@pytest.mark.asyncio
async def test_policy_interceptor_blocks_when_engine_denies():
    i = PolicyToolCallInterceptor(_deny_engine("block-write"))
    d = await i.before_call(_event("write_file"))
    assert not d.allowed
    assert "block-write" in d.block_reason


@pytest.mark.asyncio
async def test_policy_interceptor_records_audit_log():
    i = PolicyToolCallInterceptor(_allow_engine())
    await i.before_call(_event("read_file"))
    await i.before_call(_event("search"))
    log = i.audit_log()
    assert len(log) == 2
    assert log[0]["tool_name"] == "read_file"
    assert log[1]["allowed"] is True


@pytest.mark.asyncio
async def test_policy_interceptor_audit_log_records_block():
    i = PolicyToolCallInterceptor(_deny_engine("no-writes"))
    await i.before_call(_event("write_file"))
    log = i.audit_log()
    assert log[0]["allowed"] is False
    assert "no-writes" in log[0]["block_reason"]


# ── PolicyToolCallInterceptor — allow-list integration ────────────────────────

@pytest.mark.asyncio
async def test_policy_interceptor_with_allow_list_blocks_before_engine():
    engine = _allow_engine()
    i = PolicyToolCallInterceptor(engine, allow_list=["read_file"])
    d = await i.before_call(_event("exec_shell"))
    assert not d.allowed
    # Engine should not have been consulted — blocked at allow-list stage
    engine.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_policy_interceptor_with_allow_list_passes_to_engine():
    engine = _allow_engine()
    i = PolicyToolCallInterceptor(engine, allow_list=["read_file"])
    d = await i.before_call(_event("read_file"))
    assert d.allowed
    engine.evaluate.assert_called_once()


# ── PolicyToolCallInterceptor — source field passed to engine ─────────────────

@pytest.mark.asyncio
async def test_policy_interceptor_passes_source_to_engine():
    engine = _allow_engine()
    i = PolicyToolCallInterceptor(engine)
    await i.before_call(_event("read_file", source="mcp"))
    ctx = engine.evaluate.call_args[0][0]
    assert ctx["source"] == "mcp"
    assert ctx["tool_name"] == "read_file"


# ── ChainedInterceptor ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chained_first_deny_wins():
    allow = AllowListInterceptor(["read_file", "exec_shell"])
    deny_engine = PolicyToolCallInterceptor(_deny_engine("global-deny"))
    chain = ChainedInterceptor([allow, deny_engine])
    d = await chain.before_call(_event("read_file"))
    assert not d.allowed


@pytest.mark.asyncio
async def test_chained_all_allow():
    i1 = AllowListInterceptor(["read_file"])
    i2 = PolicyToolCallInterceptor(_allow_engine())
    chain = ChainedInterceptor([i1, i2])
    d = await chain.before_call(_event("read_file"))
    assert d.allowed


@pytest.mark.asyncio
async def test_chained_propagates_modified_args():
    """If interceptor A modifies args, interceptor B receives the modified version."""
    class ArgModifier:
        async def before_call(self, _event: ToolCallEvent) -> ToolCallDecision:
            return ToolCallDecision(allowed=True, modified_args={"path": "/safe/path"})

    received: list[dict] = []

    class ArgCapture:
        async def before_call(self, event: ToolCallEvent) -> ToolCallDecision:
            received.append(event.args)
            return ToolCallDecision(allowed=True)

    chain = ChainedInterceptor([ArgModifier(), ArgCapture()])
    await chain.before_call(_event("read_file", args={"path": "/dangerous"}))
    assert received[0] == {"path": "/safe/path"}


# ── Protocol conformance ──────────────────────────────────────────────────────

def test_policy_interceptor_satisfies_protocol():
    i = PolicyToolCallInterceptor(_allow_engine())
    assert isinstance(i, ToolCallInterceptor)


def test_allow_list_interceptor_satisfies_protocol():
    i = AllowListInterceptor(["x"])
    assert isinstance(i, ToolCallInterceptor)


def test_chained_interceptor_satisfies_protocol():
    i = ChainedInterceptor([AllowListInterceptor(["x"])])
    assert isinstance(i, ToolCallInterceptor)


# ── MCP Gateway path ──────────────────────────────────────────────────────────

def _mcp_manifest(tool_name: str, server_uri: str = "http://localhost:9000"):  # -> ToolManifest
    """ToolManifest with cost within the default turn budget (0.05)."""
    from meshflow.mcp.gateway import ToolManifest
    return ToolManifest(
        tool_name=tool_name, server_uri=server_uri,
        description=tool_name, trusted=True, max_cost_usd=0.01,
    )


@pytest.mark.asyncio
async def test_mcp_gateway_passes_through_when_no_interceptor():
    from meshflow.mcp.gateway import MCPGateway
    gw = MCPGateway()
    gw.register_tool(_mcp_manifest("search"))
    handler = AsyncMock(return_value={"results": []})
    call = await gw.call("search", {}, "agent-1", "viewer", "trace-1", handler)
    assert call.validated
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_gateway_interceptor_blocks_call():
    from meshflow.mcp.gateway import MCPGateway
    interceptor = PolicyToolCallInterceptor(_deny_engine("no-mcp-search"))
    gw = MCPGateway(tool_call_interceptor=interceptor)
    gw.register_tool(_mcp_manifest("search"))
    handler = AsyncMock(return_value={"results": []})
    call = await gw.call("search", {}, "agent-1", "viewer", "trace-1", handler)
    assert call.blocked
    assert "no-mcp-search" in call.block_reason
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_gateway_interceptor_allows_call():
    from meshflow.mcp.gateway import MCPGateway
    interceptor = PolicyToolCallInterceptor(_allow_engine())
    gw = MCPGateway(tool_call_interceptor=interceptor)
    gw.register_tool(_mcp_manifest("search"))
    handler = AsyncMock(return_value={"ok": True})
    call = await gw.call("search", {"q": "test"}, "agent-1", "viewer", "trace-1", handler)
    assert not call.blocked
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_gateway_interceptor_source_is_mcp():
    """Interceptor receives source='mcp' so policy rules can distinguish origin."""
    from meshflow.mcp.gateway import MCPGateway
    received_events: list[ToolCallEvent] = []

    class CapturingInterceptor:
        async def before_call(self, event: ToolCallEvent) -> ToolCallDecision:
            received_events.append(event)
            return ToolCallDecision(allowed=True)

    gw = MCPGateway(tool_call_interceptor=CapturingInterceptor())
    gw.register_tool(_mcp_manifest("fetch", "http://localhost:9001"))
    await gw.call("fetch", {"url": "http://example.com"}, "agent-2", "viewer", "t-1", AsyncMock())
    assert received_events[0].source == "mcp"
    assert received_events[0].tool_name == "fetch"


# ── StepRuntime wiring ────────────────────────────────────────────────────────

def test_step_runtime_accepts_interceptor():
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.schemas import Policy

    interceptor = PolicyToolCallInterceptor(_allow_engine())
    rt = StepRuntime(
        policy=Policy(),
        run_id="run-1",
        tool_call_interceptor=interceptor,
    )
    assert rt.tool_call_interceptor is interceptor


def test_step_runtime_default_interceptor_is_none():
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.schemas import Policy

    rt = StepRuntime(policy=Policy(), run_id="run-1")
    assert rt.tool_call_interceptor is None


@pytest.mark.asyncio
async def test_step_runtime_injects_interceptor_into_node():
    """Node with set_tool_call_interceptor receives the runtime's interceptor."""
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.schemas import Policy
    from meshflow.core.node import MeshNode, NodeInput, NodeOutput, NodeKind

    interceptor = PolicyToolCallInterceptor(_allow_engine())

    class FakeNode(MeshNode):
        received_interceptor: object = None

        def set_tool_call_interceptor(self, i: object) -> None:
            FakeNode.received_interceptor = i

        async def run(self, _node_input: NodeInput) -> NodeOutput:
            return NodeOutput(content="ok", confidence=1.0)

    from meshflow.core.schemas import RiskTier
    node = FakeNode(id="n1", kind=NodeKind.NATIVE, risk_profile=RiskTier.READ_ONLY)
    rt = StepRuntime(policy=Policy(), run_id="run-2", tool_call_interceptor=interceptor)
    await rt.run(node, NodeInput(task="test"), {})
    assert FakeNode.received_interceptor is interceptor
