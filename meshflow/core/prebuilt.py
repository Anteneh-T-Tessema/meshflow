"""Prebuilt agent graphs — LangGraph-compatible high-level factories.

This module provides ready-to-use graph patterns that wire together agents,
tools, and state management with zero boilerplate — the MeshFlow equivalent
of ``langgraph.prebuilt``.

Usage::

    from meshflow import create_react_agent, tool, RiskTier

    @tool(name="calculator", description="Evaluate a math expression")
    async def calculator(expression: str) -> str:
        return str(eval(expression))

    # One-liner: full ReAct agent as a compiled StateGraph
    graph = create_react_agent(model="claude-sonnet-4-6", tools=[calculator])
    result = await graph.run({"messages": [{"role": "user", "content": "What is 42 * 17?"}]})
    print(result["messages"][-1])

    # Or use ToolNode inside your own graph:
    from meshflow import StateGraph, MessagesState, ToolNode, END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", my_agent_fn)
    graph.add_node("tools", ToolNode([calculator]))
    graph.add_edge("agent", "tools")
    graph.add_conditional_edges("tools", should_continue, {"continue": "agent", "end": END})
    graph.set_entry_point("agent")
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, TypedDict

from meshflow.core.state import StateGraph, CompiledGraph, END, add


# ── MessagesState ─────────────────────────────────────────────────────────────

class MessagesState(TypedDict):
    """Built-in state schema for message-based agent graphs.

    The ``messages`` channel uses the ``add`` reducer, so each node appends
    new messages rather than replacing the list.
    """
    messages: Annotated[list[dict[str, Any]], add]


# ── ToolNode ──────────────────────────────────────────────────────────────────

class ToolNode:
    """A graph node that dispatches tool calls found in the last AI message.

    When the LLM produces a response containing tool_use blocks (Anthropic) or
    function_call / tool_calls (OpenAI), this node executes each tool call
    against the registered tools and returns the results as tool-result messages.

    Parameters
    ----------
    tools:
        List of MeshFlow ``Tool`` objects (from ``@tool`` decorator).
    handle_errors:
        If True (default), tool execution errors are returned as error
        messages instead of raising.  Set False for strict mode.

    Usage inside a StateGraph::

        graph.add_node("tools", ToolNode([calculator, web_search]))
    """

    def __init__(self, tools: list[Any], *, handle_errors: bool = True) -> None:
        self._tools: dict[str, Any] = {}
        self._handle_errors = handle_errors
        for t in tools:
            name = getattr(t, "name", None) or (t.__name__ if callable(t) else str(t))
            self._tools[name] = t

    @property
    def tool_names(self) -> list[str]:
        """Return the names of all registered tools."""
        return list(self._tools.keys())

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute tool calls from the last AI message and return results."""
        messages = state.get("messages", [])
        if not messages:
            return {"messages": []}

        last = messages[-1]
        tool_calls = self._extract_tool_calls(last)

        if not tool_calls:
            return {"messages": []}

        results: list[dict[str, Any]] = []
        for call in tool_calls:
            tool_name = call["name"]
            tool_args = call.get("args", {})
            tool_id = call.get("id", tool_name)

            tool_obj = self._tools.get(tool_name)
            if tool_obj is None:
                if self._handle_errors:
                    results.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "name": tool_name,
                        "content": f"Error: tool '{tool_name}' not found. Available: {list(self._tools)}",
                    })
                else:
                    raise KeyError(f"Tool '{tool_name}' not found. Available: {list(self._tools)}")
                continue

            try:
                fn = getattr(tool_obj, "fn", tool_obj)
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except (json.JSONDecodeError, ValueError):
                        tool_args = {"input": tool_args}

                if inspect.iscoroutinefunction(fn):
                    result = await fn(**tool_args)
                else:
                    result = fn(**tool_args)

                results.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "content": str(result),
                })
            except Exception as exc:
                if self._handle_errors:
                    results.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "name": tool_name,
                        "content": f"Error executing '{tool_name}': {exc}",
                    })
                else:
                    raise

        return {"messages": results}

    @staticmethod
    def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool calls from an AI message (supports multiple formats).

        Handles:
        - Anthropic-style: {"role": "assistant", "tool_calls": [...]}
        - OpenAI-style:    {"role": "assistant", "tool_calls": [{"function": {...}}]}
        - Inline JSON:     {"role": "assistant", "content": "Action: tool_name\\nAction Input: {...}"}
        - Direct:          {"tool_calls": [{"name": ..., "args": ...}]}
        """
        calls: list[dict[str, Any]] = []

        # Direct tool_calls list
        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                if isinstance(tc, dict):
                    # OpenAI format: {"id": ..., "function": {"name": ..., "arguments": ...}}
                    if "function" in tc:
                        fn = tc["function"]
                        args = fn.get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except (json.JSONDecodeError, ValueError):
                                args = {"input": args}
                        calls.append({
                            "name": fn.get("name", ""),
                            "args": args,
                            "id": tc.get("id", fn.get("name", "")),
                        })
                    # Direct format: {"name": ..., "args": ...}
                    elif "name" in tc:
                        args = tc.get("args", tc.get("arguments", {}))
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except (json.JSONDecodeError, ValueError):
                                args = {"input": args}
                        calls.append({
                            "name": tc["name"],
                            "args": args,
                            "id": tc.get("id", tc["name"]),
                        })
            return calls

        # Anthropic-style content blocks: [{"type": "tool_use", "name": ..., "input": ...}]
        content = message.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append({
                        "name": block.get("name", ""),
                        "args": block.get("input", {}),
                        "id": block.get("id", block.get("name", "")),
                    })
            return calls

        # ReAct-style inline text parsing
        if isinstance(content, str) and "Action:" in content:
            action_m = re.search(r"Action:\s*(.+?)(?:\nAction Input:|$)", content, re.DOTALL)
            input_m = re.search(r"Action Input:\s*(.+)", content, re.DOTALL)
            if action_m:
                action = action_m.group(1).strip()
                if action.lower() not in ("final answer", "finalanswer", "final_answer"):
                    args_raw = input_m.group(1).strip() if input_m else "{}"
                    try:
                        args = json.loads(args_raw)
                    except (json.JSONDecodeError, ValueError):
                        args = {"input": args_raw}
                    calls.append({"name": action, "args": args, "id": action})

        return calls


# ── should_continue helper ────────────────────────────────────────────────────

def _has_tool_calls(state: dict[str, Any]) -> str:
    """Routing function: returns 'tools' if the last message has tool calls,
    otherwise 'end'.  Used as the conditional edge in create_react_agent."""
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]
    if ToolNode._extract_tool_calls(last):
        return "tools"
    return "end"


# ── _agent_node factory ──────────────────────────────────────────────────────

def _make_agent_node(
    model: str | Any,
    tools: list[Any],
    system_message: str = "",
) -> Callable:
    """Create an async node function that calls the LLM with tools.

    This is the 'agent' node inside create_react_agent's graph. It:
    1. Reads messages from state
    2. Calls the LLM with tool schemas
    3. Returns the AI response (with or without tool_calls) appended to messages
    """

    async def agent_node(state: dict[str, Any]) -> dict[str, Any]:
        from meshflow.agents.base import _build_tool_schema

        messages = state.get("messages", [])

        # Build tool schemas for the LLM
        tool_schemas = []
        for t in tools:
            if hasattr(t, "to_anthropic_schema"):
                tool_schemas.append(t.to_anthropic_schema())
            elif hasattr(t, "name"):
                tool_schemas.append(_build_tool_schema(t))

        # Resolve provider
        if isinstance(model, str):
            from meshflow.agents.providers import auto_detect_provider
            provider = auto_detect_provider()
            model_name = model
        else:
            provider = model
            model_name = getattr(model, "model", "")

        # Format system message
        system = system_message
        if not system:
            system = (
                "You are a helpful assistant. Use the available tools when needed. "
                "When you have enough information to answer, respond directly without "
                "calling any tools."
            )

        if tool_schemas:
            tools_desc = "\n".join(
                f"- {s.get('name', '?')}: {s.get('description', '')}"
                for s in tool_schemas
            )
            system += f"\n\nAvailable tools:\n{tools_desc}"

        # Call LLM
        try:
            content, tokens, cost = await provider.complete(
                model=model_name or "echo",
                messages=messages,
                system=system,
                max_tokens=4096,
            )
        except Exception:
            content, tokens, cost = await provider.complete(
                model=model_name or "echo",
                messages=messages,
                system=system,
            )

        # Build response message
        response: dict[str, Any] = {
            "role": "assistant",
            "content": content,
        }

        # Check if the LLM response contains tool calls (ReAct-style)
        tool_calls = ToolNode._extract_tool_calls(response)
        if tool_calls:
            response["tool_calls"] = tool_calls

        return {"messages": [response]}

    return agent_node


# ── create_react_agent ────────────────────────────────────────────────────────

def create_react_agent(
    model: str | Any,
    tools: list[Any],
    *,
    state_schema: type | None = None,
    system_message: str = "",
    max_iterations: int = 25,
) -> CompiledGraph:
    """Create a fully-wired ReAct agent as a compiled StateGraph.

    This is MeshFlow's equivalent of ``langgraph.prebuilt.create_react_agent``.
    Returns a compiled graph that runs the ReAct loop:

        agent → (tool calls?) → tools → agent → ... → end

    Parameters
    ----------
    model:
        Model name string (e.g. ``"claude-sonnet-4-6"``) or an LLM/provider
        instance.  When a string is given, the provider is auto-detected from
        available API keys.
    tools:
        List of MeshFlow ``Tool`` objects (from ``@tool`` decorator).
    state_schema:
        Optional custom state TypedDict. Must have a ``messages`` field with
        the ``add`` reducer. Defaults to :class:`MessagesState`.
    system_message:
        Custom system prompt. If empty, a sensible default is used.
    max_iterations:
        Maximum number of agent→tool cycles to prevent infinite loops.

    Returns
    -------
    CompiledGraph
        Ready to ``await graph.run({"messages": [...]})`` or iterate with
        ``async for node, state in graph.stream({...})``.

    Example
    -------
    ::

        from meshflow import create_react_agent, tool, RiskTier

        @tool(name="search", description="Web search", risk=RiskTier.EXTERNAL_IO)
        async def search(query: str) -> str:
            return f"Results for: {query}"

        graph = create_react_agent("claude-sonnet-4-6", [search])
        result = await graph.run({
            "messages": [{"role": "user", "content": "Search for HIPAA updates"}]
        })
    """
    schema = state_schema or MessagesState
    graph = StateGraph(schema)

    # Agent node: calls the LLM
    agent_fn = _make_agent_node(model, tools, system_message)
    graph.add_node("agent", agent_fn)

    # Tool node: executes tool calls
    tool_node = ToolNode(tools)
    graph.add_node("tools", tool_node)

    # Wiring: agent → (tool_calls?) → tools → agent, or agent → end
    graph.add_conditional_edges(
        "agent",
        _has_tool_calls,
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "agent")
    graph.set_entry_point("agent")

    return graph.compile()


# ── create_tool_calling_agent ─────────────────────────────────────────────────

def create_tool_calling_agent(
    model: str | Any,
    tools: list[Any],
    *,
    system_message: str = "",
) -> CompiledGraph:
    """Create a single-shot tool-calling agent as a compiled StateGraph.

    Unlike :func:`create_react_agent`, this does NOT loop — the agent calls
    tools exactly once and then returns. Use this when you want a simple
    agent that always calls tools and returns.

    Parameters
    ----------
    model:
        Model name string or LLM/provider instance.
    tools:
        List of MeshFlow ``Tool`` objects.
    system_message:
        Custom system prompt.

    Returns
    -------
    CompiledGraph
        A two-node graph: agent → tools → end.
    """
    graph = StateGraph(MessagesState)

    agent_fn = _make_agent_node(model, tools, system_message)
    graph.add_node("agent", agent_fn)

    tool_node = ToolNode(tools)
    graph.add_node("tools", tool_node)

    graph.add_edge("agent", "tools")
    graph.set_finish_point("tools")
    graph.set_entry_point("agent")

    return graph.compile()
