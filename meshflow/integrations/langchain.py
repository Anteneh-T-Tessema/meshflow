"""LangChain tool bridge — use any LangChain tool inside MeshFlow.

Usage:
    from meshflow.integrations.langchain import lc_tool, lc_tools, mesh_tool_to_lc

    # Wrap a single LangChain tool
    from langchain_community.tools import WikipediaQueryRun
    wiki = WikipediaQueryRun(...)
    mf_tool = lc_tool(wiki)

    # Wrap a list of LangChain tools
    from langchain_community.agent_toolkits import FileManagementToolkit
    tools = lc_tools(FileManagementToolkit().get_tools())

    # Use MeshFlow tools in a LangChain agent
    from langchain.agents import initialize_agent
    lc_tool_obj = mesh_tool_to_lc(my_meshflow_tool)
    agent = initialize_agent([lc_tool_obj], llm, ...)
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool


# ── LangChain → MeshFlow ──────────────────────────────────────────────────────

def lc_tool(
    langchain_tool: Any,
    risk: RiskTier = RiskTier.EXTERNAL_IO,
    override_name: str = "",
) -> Tool:
    """Wrap a single LangChain BaseTool as a MeshFlow Tool.

    Parameters
    ----------
    langchain_tool:  Any object with ``name``, ``description``, and either
                     ``_run(input)`` or ``_arun(input)`` methods.
    risk:            MeshFlow risk tier (defaults to EXTERNAL_IO because most
                     LangChain tools perform I/O).
    override_name:   Use this name instead of the tool's own name.
    """
    name = override_name or getattr(langchain_tool, "name", "lc_tool")
    description = getattr(langchain_tool, "description", "LangChain tool")

    has_arun = hasattr(langchain_tool, "_arun") and inspect.iscoroutinefunction(
        getattr(langchain_tool, "_arun")
    )

    if has_arun:
        async def _async_fn(**kwargs: Any) -> Any:
            tool_input = kwargs.get("input", "") or " ".join(str(v) for v in kwargs.values())
            return await langchain_tool._arun(tool_input)

        fn = _async_fn
    else:
        async def _sync_fn(**kwargs: Any) -> Any:
            tool_input = kwargs.get("input", "") or " ".join(str(v) for v in kwargs.values())
            return langchain_tool._run(tool_input)

        fn = _sync_fn

    return Tool(
        name=name,
        description=description,
        fn=fn,
        risk=risk,
    )


def lc_tools(
    langchain_tools: list[Any],
    risk: RiskTier = RiskTier.EXTERNAL_IO,
) -> list[Tool]:
    """Wrap a list of LangChain tools as MeshFlow Tools."""
    return [lc_tool(t, risk=risk) for t in langchain_tools]


# ── MeshFlow → LangChain ──────────────────────────────────────────────────────

def mesh_tool_to_lc(mesh_tool: Tool) -> Any:
    """Convert a MeshFlow Tool to a LangChain StructuredTool.

    Requires ``langchain_core`` to be installed.
    """
    try:
        from langchain_core.tools import StructuredTool
    except ImportError:
        try:
            from langchain.tools import StructuredTool  # type: ignore[no-redef]
        except ImportError as exc:
            raise ImportError(
                "langchain_core (or langchain) is required: pip install langchain-core"
            ) from exc

    fn = mesh_tool.fn
    if inspect.iscoroutinefunction(fn):
        # LangChain StructuredTool can accept a coroutine_func
        return StructuredTool.from_function(
            func=None,
            coroutine=fn,
            name=mesh_tool.name,
            description=mesh_tool.description or mesh_tool.name,
        )
    return StructuredTool.from_function(
        func=fn,
        name=mesh_tool.name,
        description=mesh_tool.description or mesh_tool.name,
    )


# ── LangChain agent → MeshFlow Agent ─────────────────────────────────────────

def agent_from_lc(
    lc_agent: Any,
    name: str = "lc_agent",
    role: str = "executor",
    policy: Any = None,
) -> Any:
    """Wrap a LangChain agent (AgentExecutor or LCEL chain) as a MeshFlow Agent.

    The resulting Agent can be placed in a Team or WorkflowDefinition like any
    other MeshFlow agent.
    """
    from meshflow.agents.builder import Agent
    from meshflow.core.node import MeshNode, NodeInput, NodeOutput
    from meshflow.core.schemas import RiskTier

    is_async = hasattr(lc_agent, "ainvoke")

    class _LCNode(MeshNode):
        def __init__(self) -> None:
            super().__init__(id=name, kind="python", capabilities=[role])

        async def run(self, node_input: NodeInput) -> NodeOutput:
            payload = {"input": node_input.task}
            if is_async:
                result = await lc_agent.ainvoke(payload)
            else:
                result = lc_agent.invoke(payload)

            output = result.get("output", str(result)) if isinstance(result, dict) else str(result)
            return NodeOutput(content=output, confidence=0.8, risk_tier=RiskTier.EXTERNAL_IO)

    agent = Agent(name=name, role=role, policy=policy)
    agent._prebuilt_node = _LCNode()
    return agent


__all__ = ["lc_tool", "lc_tools", "mesh_tool_to_lc", "agent_from_lc"]
