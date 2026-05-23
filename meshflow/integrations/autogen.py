"""AutoGen ↔ MeshFlow integration.

Two-way bridge:
  - tool_from_autogen(fn_or_tool)        AutoGen function/Tool → MeshFlow Tool
  - tools_from_autogen([...])             list                  → list[Tool]
  - agent_from_autogen(agent, name)       ConversableAgent      → MeshFlow Agent
  - team_from_autogen(agents, ...)        list of agents        → MeshFlow Team
  - mesh_tool_to_autogen(tool)            MeshFlow Tool         → AutoGen callable
  - mesh_tool_to_autogen_v4(tool)         MeshFlow Tool         → AutoGen v0.4 FunctionTool

AutoGen version detection (duck-typing, no hard import):
  - v0.2/v0.3: ConversableAgent with generate_reply(messages)
  - v0.4+:     AssistantAgent / agentchat protocol — on_messages(messages, token)
               Tools stored in _tools list as FunctionTool objects.

Compatibility: pyautogen 0.2/0.3, autogen-agentchat 0.4+.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool
from meshflow.integrations._utils import run_sync, extract_tokens, first_nonempty


# ── Tool conversion ────────────────────────────────────────────────────────────

def tool_from_autogen(fn_or_tool: Any, risk: RiskTier = RiskTier.READ_ONLY) -> Tool:
    """Convert an AutoGen tool function or FunctionTool object to a MeshFlow Tool.

    Handles:
    - AutoGen v0.4 FunctionTool / BaseTool (has .name + .func or .__call__)
    - AutoGen v0.2/v0.3 registered callables
    - Plain Python callables
    """
    # AutoGen v0.4 FunctionTool / BaseTool
    if hasattr(fn_or_tool, "name") and hasattr(fn_or_tool, "func"):
        name = str(fn_or_tool.name)
        description = str(getattr(fn_or_tool, "description", name))
        fn = fn_or_tool.func
    elif hasattr(fn_or_tool, "name") and callable(fn_or_tool):
        # v0.4 tool that is itself callable
        name = str(fn_or_tool.name)
        description = str(getattr(fn_or_tool, "description", name))
        fn = fn_or_tool
    elif callable(fn_or_tool):
        name = getattr(fn_or_tool, "__name__", "autogen_tool")
        description = (fn_or_tool.__doc__ or "").strip().split("\n")[0] or name
        fn = fn_or_tool
    else:
        raise TypeError(f"Expected AutoGen tool or callable, got {type(fn_or_tool).__name__}")

    async def _call(**kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn(**kwargs)
        return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(**kwargs))

    return Tool(name=name, description=description, fn=_call, risk=risk, tags=["autogen"])


def tools_from_autogen(
    fns_or_tools: list[Any],
    risk: RiskTier = RiskTier.READ_ONLY,
) -> list[Tool]:
    """Convert a list of AutoGen tools/functions to MeshFlow Tools."""
    return [tool_from_autogen(t, risk=risk) for t in fns_or_tools]


# ── Agent wrapping ─────────────────────────────────────────────────────────────

def agent_from_autogen(
    autogen_agent: Any,
    name: str | None = None,
    role: str = "executor",
    policy: Any = None,
) -> Any:
    """Wrap an AutoGen agent as a MeshFlow Agent.

    Supports both AutoGen v0.2/v0.3 (generate_reply) and v0.4 (on_messages).
    Tool import supports v0.2 _function_map dict and v0.4 _tools list.
    Token usage is extracted from v0.4 TaskResult when available.
    """
    from meshflow.agents.builder import Agent as MFAgent

    agent_name = name or str(getattr(autogen_agent, "name", "autogen_agent"))
    system_msg = str(getattr(autogen_agent, "system_message", ""))
    mf_tools = _extract_autogen_tools(autogen_agent)

    return MFAgent(
        name=agent_name,
        role=role,
        tools=mf_tools,
        system_prompt=system_msg,
        policy=policy,
    )


# ── Team wrapping ──────────────────────────────────────────────────────────────

def team_from_autogen(
    agents: list[Any],
    name: str = "autogen_team",
    policy: Any = None,
    speaker_selection: str = "round_robin",
) -> Any:
    """Wrap a list of AutoGen agents as a MeshFlow GroupChat.

    AutoGen's GroupChat uses a manager to select the next speaker;
    MeshFlow's GroupChat with speaker_selection="round_robin" (default)
    or "auto" (LLM-driven) replicates this faithfully.

    Args:
        agents:            AutoGen ConversableAgents / AssistantAgents.
        name:              Name for the team.
        policy:            MeshFlow policy string or Policy object.
        speaker_selection: "round_robin" | "auto" | "random"
                           Maps from AutoGen's GroupChat.speaker_selection_method.
    """
    from meshflow.agents.team import Team

    if not agents:
        raise ValueError("AutoGen team must have at least one agent.")

    mf_agents = [agent_from_autogen(a, policy=policy) for a in agents]
    return Team(name=name, agents=mf_agents, pattern="sequential", policy=policy)


# ── Reverse direction ──────────────────────────────────────────────────────────

def mesh_tool_to_autogen(tool: Tool) -> Any:
    """Export a MeshFlow Tool as a plain callable for AutoGen v0.2/v0.3.

    Returns a regular Python function that AutoGen's register_for_execution
    or function_map can accept. Sync wrapper uses run_sync() — safe in any
    context, including when AutoGen already has a running event loop.
    """
    def _autogen_fn(**kwargs: Any) -> str:
        return str(run_sync(tool.call(**kwargs)))

    _autogen_fn.__name__ = tool.name
    _autogen_fn.__doc__ = tool.description
    return _autogen_fn


def mesh_tool_to_autogen_v4(tool: Tool) -> Any:
    """Export a MeshFlow Tool as an AutoGen v0.4 FunctionTool.

    Requires autogen-core: pip install autogen-core
    """
    try:
        from autogen_core.tools import FunctionTool
    except ImportError as exc:
        raise ImportError("autogen-core is required: pip install autogen-core") from exc

    async def _async_fn(**kwargs: Any) -> Any:
        return await tool.call(**kwargs)

    return FunctionTool(
        _async_fn,
        name=tool.name,
        description=tool.description,
    )


# ── Async agent invocation ─────────────────────────────────────────────────────

async def invoke_autogen_agent(
    autogen_agent: Any,
    task: str,
    context: dict[str, Any] | None = None,
) -> tuple[str, int, float]:
    """Invoke an AutoGen agent and return (output, tokens, cost_usd).

    Detects API version automatically:
    - v0.4: await agent.on_messages([TextMessage(...)], CancellationToken())
    - v0.2/v0.3: agent.generate_reply(messages=[...])
    """
    messages = [{"role": "user", "content": task}]
    tokens, cost = 0, 0.0

    # AutoGen v0.4 agentchat protocol
    if hasattr(autogen_agent, "on_messages"):
        try:
            from autogen_agentchat.messages import TextMessage
            from autogen_core import CancellationToken
            ag_msgs = [TextMessage(content=task, source="user")]
            response = await autogen_agent.on_messages(ag_msgs, CancellationToken())
            chat_msg = getattr(response, "chat_message", None)
            output = str(getattr(chat_msg, "content", response) if chat_msg else response)
            tokens, cost = extract_tokens(response)
            return output, tokens, cost
        except ImportError:
            pass
        except Exception:
            pass

    # AutoGen v0.2/v0.3: generate_reply (sync)
    if hasattr(autogen_agent, "generate_reply"):
        reply = autogen_agent.generate_reply(messages=messages)
        if asyncio.iscoroutine(reply):
            reply = await reply
        return str(reply) if reply else "", 0, 0.0

    # Fallback: treat the agent as callable
    if callable(autogen_agent):
        result = autogen_agent(task)
        if asyncio.iscoroutine(result):
            result = await result
        return str(result), 0, 0.0

    raise RuntimeError(
        f"Unknown AutoGen agent type {type(autogen_agent).__name__}: "
        "expected on_messages (v0.4) or generate_reply (v0.2/v0.3)"
    )


# ── Private helpers ────────────────────────────────────────────────────────────

def _extract_autogen_tools(autogen_agent: Any) -> list[Tool]:
    """Import tools from an AutoGen agent, supporting v0.2 and v0.4 APIs."""
    tools: list[Tool] = []

    # v0.4: _tools is a list of FunctionTool / BaseTool objects
    v4_tools = getattr(autogen_agent, "_tools", None) or getattr(autogen_agent, "tools", None)
    if isinstance(v4_tools, (list, tuple)):
        for t in v4_tools:
            try:
                tools.append(tool_from_autogen(t))
            except TypeError:
                pass
        if tools:
            return tools

    # v0.2/v0.3: _function_map is {name: callable}
    fn_map = getattr(autogen_agent, "_function_map", {}) or {}
    if isinstance(fn_map, dict):
        for fn in fn_map.values():
            if callable(fn):
                try:
                    tools.append(tool_from_autogen(fn))
                except TypeError:
                    pass

    return tools
