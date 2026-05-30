"""Sprint 66 — Prebuilt Agent Graphs tests.

Tests for MessagesState, ToolNode, create_react_agent, and create_tool_calling_agent.
All tests are deterministic (no API key needed — uses EchoProvider).
"""

from __future__ import annotations

import asyncio
import json
import pytest
from typing import Annotated, Any, TypedDict

# ── Imports ───────────────────────────────────────────────────────────────────

from meshflow.core.prebuilt import (
    MessagesState,
    ToolNode,
    create_react_agent,
    create_tool_calling_agent,
    _has_tool_calls,
    _make_agent_node,
)
from meshflow.core.state import StateGraph, CompiledGraph, END, add
from meshflow.tools.registry import Tool
from meshflow.core.schemas import RiskTier
from meshflow.agents.base import EchoProvider


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sync_add(a: int, b: int) -> str:
    return str(a + b)


async def _async_multiply(x: int, y: int) -> str:
    return str(x * y)


def _fail_tool() -> str:
    raise ValueError("intentional failure")


_add_tool = Tool(name="add", description="Add two numbers", fn=_sync_add, risk=RiskTier.READ_ONLY)
_mul_tool = Tool(name="multiply", description="Multiply two numbers", fn=_async_multiply, risk=RiskTier.READ_ONLY)
_fail_tool_obj = Tool(name="fail", description="Always fails", fn=_fail_tool, risk=RiskTier.READ_ONLY)


# ══════════════════════════════════════════════════════════════════════════════
#  MessagesState
# ══════════════════════════════════════════════════════════════════════════════


class TestMessagesState:
    """Verify the MessagesState TypedDict and its reducer."""

    def test_is_typed_dict(self):
        """MessagesState should be a TypedDict subclass."""
        assert hasattr(MessagesState, "__annotations__")
        assert "messages" in MessagesState.__annotations__

    def test_messages_field_uses_add_reducer(self):
        """The messages field should use the 'add' reducer via Annotated."""
        from meshflow.core.state import _extract_channels
        channels = _extract_channels(MessagesState)
        assert "messages" in channels
        ch = channels["messages"]
        # The reducer should be the 'add' function
        assert ch.reducer is add

    def test_add_reducer_appends(self):
        """The add reducer should concatenate lists."""
        result = add([{"role": "user", "content": "hi"}], [{"role": "assistant", "content": "hello"}])
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_add_reducer_handles_non_list(self):
        """The add reducer should wrap non-lists."""
        result = add(None, [{"role": "user", "content": "hi"}])
        assert len(result) == 1


# ══════════════════════════════════════════════════════════════════════════════
#  ToolNode
# ══════════════════════════════════════════════════════════════════════════════


class TestToolNode:
    """Verify ToolNode dispatches tool calls correctly."""

    def test_init_registers_tools(self):
        node = ToolNode([_add_tool, _mul_tool])
        assert "add" in node.tool_names
        assert "multiply" in node.tool_names
        assert len(node.tool_names) == 2

    @pytest.mark.asyncio
    async def test_sync_tool_call(self):
        """ToolNode should execute sync tool functions."""
        node = ToolNode([_add_tool])
        state = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"name": "add", "args": {"a": 3, "b": 4}}]}
            ]
        }
        result = await node(state)
        msgs = result["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["content"] == "7"
        assert msgs[0]["name"] == "add"

    @pytest.mark.asyncio
    async def test_async_tool_call(self):
        """ToolNode should execute async tool functions."""
        node = ToolNode([_mul_tool])
        state = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"name": "multiply", "args": {"x": 5, "y": 6}}]}
            ]
        }
        result = await node(state)
        msgs = result["messages"]
        assert len(msgs) == 1
        assert msgs[0]["content"] == "30"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self):
        """ToolNode should handle multiple tool calls in one message."""
        node = ToolNode([_add_tool, _mul_tool])
        state = {
            "messages": [
                {"role": "assistant", "tool_calls": [
                    {"name": "add", "args": {"a": 1, "b": 2}},
                    {"name": "multiply", "args": {"x": 3, "y": 4}},
                ]}
            ]
        }
        result = await node(state)
        msgs = result["messages"]
        assert len(msgs) == 2
        assert msgs[0]["content"] == "3"
        assert msgs[1]["content"] == "12"

    @pytest.mark.asyncio
    async def test_missing_tool_handled(self):
        """ToolNode should return error message for missing tools (handle_errors=True)."""
        node = ToolNode([_add_tool])
        state = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"name": "nonexistent", "args": {}}]}
            ]
        }
        result = await node(state)
        msgs = result["messages"]
        assert len(msgs) == 1
        assert "Error" in msgs[0]["content"]
        assert "nonexistent" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_missing_tool_strict(self):
        """ToolNode should raise for missing tools when handle_errors=False."""
        node = ToolNode([_add_tool], handle_errors=False)
        state = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"name": "nonexistent", "args": {}}]}
            ]
        }
        with pytest.raises(KeyError, match="nonexistent"):
            await node(state)

    @pytest.mark.asyncio
    async def test_tool_error_handled(self):
        """ToolNode should catch tool exceptions when handle_errors=True."""
        node = ToolNode([_fail_tool_obj])
        state = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"name": "fail", "args": {}}]}
            ]
        }
        result = await node(state)
        msgs = result["messages"]
        assert len(msgs) == 1
        assert "Error" in msgs[0]["content"]
        assert "intentional failure" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_tool_error_strict(self):
        """ToolNode should re-raise tool exceptions when handle_errors=False."""
        node = ToolNode([_fail_tool_obj], handle_errors=False)
        state = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"name": "fail", "args": {}}]}
            ]
        }
        with pytest.raises(ValueError, match="intentional failure"):
            await node(state)

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_empty(self):
        """ToolNode should return empty messages when there are no tool calls."""
        node = ToolNode([_add_tool])
        state = {
            "messages": [
                {"role": "assistant", "content": "Hello, how can I help?"}
            ]
        }
        result = await node(state)
        assert result["messages"] == []

    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty(self):
        """ToolNode should handle empty message list."""
        node = ToolNode([_add_tool])
        result = await node({"messages": []})
        assert result["messages"] == []


# ══════════════════════════════════════════════════════════════════════════════
#  ToolNode._extract_tool_calls — format parsing
# ══════════════════════════════════════════════════════════════════════════════


class TestExtractToolCalls:
    """Verify tool call extraction from various message formats."""

    def test_direct_format(self):
        """Direct format: {"tool_calls": [{"name": ..., "args": ...}]}."""
        msg = {"role": "assistant", "tool_calls": [{"name": "add", "args": {"a": 1, "b": 2}}]}
        calls = ToolNode._extract_tool_calls(msg)
        assert len(calls) == 1
        assert calls[0]["name"] == "add"
        assert calls[0]["args"] == {"a": 1, "b": 2}

    def test_openai_format(self):
        """OpenAI format: {"tool_calls": [{"function": {"name": ..., "arguments": ...}}]}."""
        msg = {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_123",
                "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'},
            }]
        }
        calls = ToolNode._extract_tool_calls(msg)
        assert len(calls) == 1
        assert calls[0]["name"] == "add"
        assert calls[0]["args"] == {"a": 1, "b": 2}
        assert calls[0]["id"] == "call_123"

    def test_anthropic_content_blocks(self):
        """Anthropic format: content = [{"type": "tool_use", ...}]."""
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me calculate..."},
                {"type": "tool_use", "id": "tu_1", "name": "add", "input": {"a": 3, "b": 4}},
            ]
        }
        calls = ToolNode._extract_tool_calls(msg)
        assert len(calls) == 1
        assert calls[0]["name"] == "add"
        assert calls[0]["args"] == {"a": 3, "b": 4}

    def test_react_inline_format(self):
        """ReAct inline format: Action: tool_name / Action Input: {...}."""
        msg = {
            "role": "assistant",
            "content": 'Thought: I need to add numbers\nAction: add\nAction Input: {"a": 5, "b": 6}',
        }
        calls = ToolNode._extract_tool_calls(msg)
        assert len(calls) == 1
        assert calls[0]["name"] == "add"
        assert calls[0]["args"] == {"a": 5, "b": 6}

    def test_react_final_answer_not_extracted(self):
        """ReAct 'Final Answer' should not be treated as a tool call."""
        msg = {
            "role": "assistant",
            "content": "Thought: I know the answer\nAction: Final Answer\nAction Input: 42",
        }
        calls = ToolNode._extract_tool_calls(msg)
        assert len(calls) == 0

    def test_no_tool_calls(self):
        """Plain text response should yield no tool calls."""
        msg = {"role": "assistant", "content": "Here's the answer: 42"}
        calls = ToolNode._extract_tool_calls(msg)
        assert len(calls) == 0

    def test_string_args_parsed(self):
        """String arguments in direct format should be JSON-parsed."""
        msg = {"role": "assistant", "tool_calls": [{"name": "add", "args": '{"a": 1, "b": 2}'}]}
        calls = ToolNode._extract_tool_calls(msg)
        assert calls[0]["args"] == {"a": 1, "b": 2}

    def test_invalid_json_args_wrapped(self):
        """Non-JSON string args should be wrapped in {"input": ...}."""
        msg = {"role": "assistant", "tool_calls": [{"name": "search", "args": "hello world"}]}
        calls = ToolNode._extract_tool_calls(msg)
        assert calls[0]["args"] == {"input": "hello world"}


# ══════════════════════════════════════════════════════════════════════════════
#  _has_tool_calls routing function
# ══════════════════════════════════════════════════════════════════════════════


class TestHasToolCalls:
    """Verify the routing function for conditional edges."""

    def test_returns_tools_when_calls_present(self):
        state = {"messages": [{"role": "assistant", "tool_calls": [{"name": "add", "args": {}}]}]}
        assert _has_tool_calls(state) == "tools"

    def test_returns_end_when_no_calls(self):
        state = {"messages": [{"role": "assistant", "content": "Done"}]}
        assert _has_tool_calls(state) == "end"

    def test_returns_end_for_empty_messages(self):
        assert _has_tool_calls({"messages": []}) == "end"
        assert _has_tool_calls({}) == "end"


# ══════════════════════════════════════════════════════════════════════════════
#  create_react_agent
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateReactAgent:
    """Verify create_react_agent produces a valid compiled graph."""

    def test_returns_compiled_graph(self):
        """create_react_agent should return a CompiledGraph."""
        graph = create_react_agent(EchoProvider(), [_add_tool])
        assert isinstance(graph, CompiledGraph)

    def test_graph_has_agent_and_tools_nodes(self):
        """The graph should contain 'agent' and 'tools' nodes."""
        graph = create_react_agent(EchoProvider(), [_add_tool])
        assert "agent" in graph._g._nodes
        assert "tools" in graph._g._nodes

    def test_graph_entry_is_agent(self):
        """The graph entry point should be 'agent'."""
        graph = create_react_agent(EchoProvider(), [_add_tool])
        assert graph._g._entry == "agent"

    def test_graph_has_conditional_edges(self):
        """The agent node should have conditional edges."""
        graph = create_react_agent(EchoProvider(), [_add_tool])
        assert "agent" in graph._g._conditional

    @pytest.mark.asyncio
    async def test_run_with_echo_provider(self):
        """The graph should run end-to-end with EchoProvider (no tools called)."""
        graph = create_react_agent(EchoProvider(), [_add_tool])
        result = await graph.run({
            "messages": [{"role": "user", "content": "What is 2 + 3?"}]
        })
        assert "messages" in result
        # EchoProvider echoes the input, which won't contain tool calls
        # so the graph should terminate after one agent step
        assert len(result["messages"]) >= 2  # user msg + at least one assistant msg

    def test_custom_state_schema(self):
        """create_react_agent should accept a custom state schema."""
        class MyState(TypedDict):
            messages: Annotated[list[dict[str, Any]], add]
            metadata: str

        graph = create_react_agent(EchoProvider(), [_add_tool], state_schema=MyState)
        assert graph._g._schema is MyState

    def test_custom_system_message(self):
        """create_react_agent should accept a custom system message."""
        graph = create_react_agent(EchoProvider(), [_add_tool], system_message="You are a math tutor.")
        assert isinstance(graph, CompiledGraph)


# ══════════════════════════════════════════════════════════════════════════════
#  create_tool_calling_agent
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateToolCallingAgent:
    """Verify create_tool_calling_agent produces a valid compiled graph."""

    def test_returns_compiled_graph(self):
        graph = create_tool_calling_agent(EchoProvider(), [_add_tool])
        assert isinstance(graph, CompiledGraph)

    def test_graph_has_agent_and_tools_nodes(self):
        graph = create_tool_calling_agent(EchoProvider(), [_add_tool])
        assert "agent" in graph._g._nodes
        assert "tools" in graph._g._nodes

    def test_graph_is_linear(self):
        """The graph should be agent → tools (no loop)."""
        graph = create_tool_calling_agent(EchoProvider(), [_add_tool])
        # No conditional edges — it's a straight pipeline
        assert "agent" not in graph._g._conditional

    @pytest.mark.asyncio
    async def test_run_with_echo_provider(self):
        """The graph should run end-to-end with EchoProvider."""
        graph = create_tool_calling_agent(EchoProvider(), [_add_tool])
        result = await graph.run({
            "messages": [{"role": "user", "content": "Calculate 5 + 3"}]
        })
        assert "messages" in result
        assert len(result["messages"]) >= 2


# ══════════════════════════════════════════════════════════════════════════════
#  ToolNode inside a custom StateGraph
# ══════════════════════════════════════════════════════════════════════════════


class TestToolNodeInCustomGraph:
    """Verify ToolNode integrates cleanly with user-built StateGraphs."""

    @pytest.mark.asyncio
    async def test_custom_graph_with_tool_node(self):
        """ToolNode should work as a regular node in a custom graph."""
        tool_node = ToolNode([_add_tool])

        async def agent_fn(state: dict) -> dict:
            return {
                "messages": [{
                    "role": "assistant",
                    "tool_calls": [{"name": "add", "args": {"a": 10, "b": 20}}],
                }]
            }

        async def tools_fn(state: dict) -> dict:
            return await tool_node(state)

        graph = StateGraph(MessagesState)
        graph.add_node("agent", agent_fn)
        graph.add_node("tools", tools_fn)
        graph.add_edge("agent", "tools")
        graph.set_entry_point("agent")
        graph.set_finish_point("tools")

        result = await graph.run({"messages": [{"role": "user", "content": "add 10 and 20"}]})
        messages = result["messages"]

        # Should have: user, assistant (tool_calls), tool (result)
        assert len(messages) == 3
        tool_result = messages[-1]
        assert tool_result["role"] == "tool"
        assert tool_result["content"] == "30"
        assert tool_result["name"] == "add"


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════


class TestPublicAPIExports:
    """Verify all new symbols are exported from meshflow."""

    def test_messages_state_exported(self):
        import meshflow
        assert hasattr(meshflow, "MessagesState")

    def test_tool_node_exported(self):
        import meshflow
        assert hasattr(meshflow, "ToolNode")

    def test_create_react_agent_exported(self):
        import meshflow
        assert hasattr(meshflow, "create_react_agent")

    def test_create_tool_calling_agent_exported(self):
        import meshflow
        assert hasattr(meshflow, "create_tool_calling_agent")

    def test_version_bumped(self):
        import meshflow
        assert meshflow.__version__ >= "0.77.0"

    def test_in_all(self):
        import meshflow
        for sym in ("MessagesState", "ToolNode", "create_react_agent", "create_tool_calling_agent"):
            assert sym in meshflow.__all__, f"{sym} missing from __all__"
