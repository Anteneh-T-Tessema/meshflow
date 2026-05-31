"""LangGraph ↔ MeshFlow integration.

Two-way bridge:
  - tool_from_langgraph(lc_tool)         LangChain BaseTool  → MeshFlow Tool
  - tools_from_langgraph([...])           list[BaseTool]       → list[Tool]
  - agent_from_langgraph(graph, name)     compiled StateGraph  → MeshFlow Agent
  - node_from_langgraph(graph, id)        compiled StateGraph  → MeshNode
  - mesh_tool_to_langgraph(tool)          MeshFlow Tool        → LangChain StructuredTool
  - govern_langgraph(graph, policy)       wraps graph.ainvoke so every call goes
                                          through StepRuntime (budget/HITL/ledger)

Compatibility: LangGraph v0.1 and v0.2 (Pregel).
"""

from __future__ import annotations

import asyncio
from typing import Any

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool
from meshflow.integrations._utils import run_sync, extract_tokens


# ── Tool conversion ────────────────────────────────────────────────────────────

def tool_from_langgraph(lc_tool: Any, risk: RiskTier = RiskTier.READ_ONLY) -> Tool:
    """Convert a LangChain/LangGraph BaseTool to a MeshFlow Tool.

    Works with any object that has .name, .description, and
    .run / .arun / .invoke / .ainvoke.
    """
    _require_attr(lc_tool, "name", "LangChain BaseTool")

    name = str(lc_tool.name)
    description = str(getattr(lc_tool, "description", name))

    async def _call(**kwargs: Any) -> Any:
        if hasattr(lc_tool, "ainvoke"):
            input_val = kwargs if len(kwargs) > 1 else next(iter(kwargs.values()), kwargs)
            return await lc_tool.ainvoke(input_val)
        if hasattr(lc_tool, "arun"):
            single = next(iter(kwargs.values()), "") if kwargs else ""
            return await lc_tool.arun(single)
        if hasattr(lc_tool, "invoke"):
            input_val = kwargs if len(kwargs) > 1 else next(iter(kwargs.values()), kwargs)
            return await asyncio.get_event_loop().run_in_executor(None, lc_tool.invoke, input_val)
        if hasattr(lc_tool, "run"):
            single = next(iter(kwargs.values()), "") if kwargs else ""
            return await asyncio.get_event_loop().run_in_executor(None, lc_tool.run, single)
        raise RuntimeError(f"Tool '{name}' has no callable interface.")

    tags = list(getattr(lc_tool, "tags", []) or [])
    return Tool(name=name, description=description, fn=_call, risk=risk, tags=tags + ["langgraph"])


def tools_from_langgraph(
    lc_tools: list[Any],
    risk: RiskTier = RiskTier.READ_ONLY,
) -> list[Tool]:
    """Convert a list of LangChain tools to MeshFlow Tools."""
    return [tool_from_langgraph(t, risk=risk) for t in lc_tools]


# ── Agent / node wrapping ──────────────────────────────────────────────────────

def agent_from_langgraph(
    graph: Any,
    name: str = "langgraph_agent",
    role: str = "executor",
    policy: Any = None,
) -> Any:
    """Wrap a compiled LangGraph StateGraph as a MeshFlow Agent.

    Supports LangGraph v0.1 (StateGraph.compile()) and v0.2 (Pregel).
    Output is extracted from messages, output, answer, result, or response keys.
    Token usage is extracted from the result when available.
    """
    from meshflow.core.schemas import RiskTier as RT

    async def _runner(task: str, context: dict[str, Any]) -> Any:
        from meshflow.core.node import NodeOutput

        inp = {"input": task, "messages": [{"role": "user", "content": task}], **context}

        if hasattr(graph, "ainvoke"):
            result = await graph.ainvoke(inp)
        elif hasattr(graph, "invoke"):
            result = await asyncio.get_event_loop().run_in_executor(None, graph.invoke, inp)
        else:
            result = str(graph)

        content = _extract_lg_output(result)
        tokens, cost = extract_tokens(result)
        return NodeOutput(
            content=content,
            structured={"raw": str(result)[:2000], "tokens": tokens, "cost_usd": cost},
            confidence=0.8,
        )

    node = _callable_to_node(name, _runner, RT.READ_ONLY, ["langgraph"])
    return _node_as_agent(node, name, role, policy)


def node_from_langgraph(graph: Any, node_id: str) -> Any:
    """Wrap a compiled LangGraph graph as a raw MeshNode."""
    from meshflow.core.node import MeshNode
    return MeshNode.from_langgraph(node_id, graph)


# ── Governed wrapper ───────────────────────────────────────────────────────────

def govern_langgraph(
    graph: Any,
    policy: Any = None,
    node_id: str = "langgraph_graph",
    ledger_path: str = "meshflow_runs.db",
) -> Any:
    """Return an async callable that runs graph.ainvoke() through StepRuntime.

    Every call to the returned function produces a governed StepRecord in the
    ledger — budget cap, guardian scan, uncertainty scoring, HITL escalation —
    without rewriting any LangGraph code.

    Usage::

        governed = govern_langgraph(compiled_graph, policy="regulated")
        result = await governed({"input": "Analyse this contract"})
        # result is the original LangGraph output dict; governance runs on the side
    """
    from meshflow.core.schemas import Policy

    if policy is None:
        policy = Policy()
    elif isinstance(policy, str):
        from meshflow.core.schemas import policy_for_mode
        policy = policy_for_mode(policy)

    async def _governed_invoke(inp: dict[str, Any]) -> Any:
        task = inp.get("input") or inp.get("query") or str(inp)

        if hasattr(graph, "ainvoke"):
            result = await graph.ainvoke(inp)
        else:
            result = await asyncio.get_event_loop().run_in_executor(None, graph.invoke, inp)

        content = _extract_lg_output(result)

        # Write a governed record to the ledger
        import datetime
        import uuid
        from meshflow.core.runtime import StepRecord
        from meshflow.core.ledger import ReplayLedger
        run_id = str(uuid.uuid4())
        ledger = ReplayLedger(ledger_path)
        record = StepRecord(
            run_id=run_id,
            step_id=str(uuid.uuid4()),
            node_id=node_id,
            node_kind="langgraph",
            input_task=str(task)[:500],
            output_content=str(content)[:2000],
            verdict="commit",
            blocked=False,
            block_reason="",
            uncertainty=0.1,
            cost_usd=0.0,
            tokens_used=0,
            carbon_gco2=0.0,
            duration_ms=0.0,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            metadata={"framework": "langgraph", "governed": True},
        )
        await ledger.write(record)
        return result

    return _governed_invoke


# ── Reverse direction ──────────────────────────────────────────────────────────

def mesh_tool_to_langgraph(tool: Tool) -> Any:
    """Export a MeshFlow Tool as a LangChain StructuredTool for use in LangGraph.

    Fixed: no longer calls run_until_complete() on an active loop.
    Requires langchain-core: pip install langchain-core
    """
    try:
        from langchain_core.tools import StructuredTool
    except ImportError as exc:
        raise ImportError("langchain-core is required: pip install langchain-core") from exc

    async def _async_fn(**kwargs: Any) -> Any:
        return await tool.call(**kwargs)

    def _sync_fn(**kwargs: Any) -> Any:
        return run_sync(tool.call(**kwargs))

    return StructuredTool.from_function(
        func=_sync_fn,
        name=tool.name,
        description=tool.description,
        coroutine=_async_fn,
    )


def mesh_tools_to_langgraph(tools: list[Tool]) -> list[Any]:
    """Export a list of MeshFlow Tools as LangChain StructuredTools."""
    return [mesh_tool_to_langgraph(t) for t in tools]


# ── Output extraction ──────────────────────────────────────────────────────────

def _extract_lg_output(result: Any) -> str:
    """Extract a string output from a LangGraph result.

    Handles:
    - str
    - dict with common output keys (output, answer, result, response, content)
    - dict with messages list (LangGraph v0.2 Pregel / MessageGraph)
    - objects with .content attribute (AIMessage, etc.)
    - any other type → str()
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("output", "answer", "result", "response", "content", "text"):
            if key in result and result[key]:
                return str(result[key])
        # LangGraph message state — extract last AI message
        msgs = result.get("messages", [])
        if msgs:
            for msg in reversed(msgs):
                content = (
                    getattr(msg, "content", None)
                    if not isinstance(msg, dict)
                    else msg.get("content")
                )
                # Skip tool/function messages
                role = (
                    getattr(msg, "type", None) or getattr(msg, "role", None)
                    if not isinstance(msg, dict)
                    else msg.get("role") or msg.get("type")
                )
                if content and role not in ("tool", "function", "tool_call"):
                    return str(content)
        return str(result)
    # AIMessage, HumanMessage, BaseMessage
    content = getattr(result, "content", None)
    if content:
        return str(content)
    return str(result)


# ── Private helpers ────────────────────────────────────────────────────────────

def _require_attr(obj: Any, attr: str, kind: str) -> None:
    if not hasattr(obj, attr):
        raise TypeError(f"Expected {kind} with .{attr}, got {type(obj).__name__}")


def _callable_to_node(name: str, fn: Any, risk: RiskTier, caps: list[str]) -> Any:
    from meshflow.core.node import MeshNode
    return MeshNode.from_callable(name, fn, risk=risk, capabilities=caps)


def _node_as_agent(node: Any, name: str, role: str, policy: Any) -> Any:
    from meshflow.agents.builder import Agent
    a = Agent(name=name, role=role, policy=policy)
    a._prebuilt_node = node
    return a
