"""OpenAI Assistants / Agents SDK ↔ MeshFlow integration.

Bridges OpenAI Assistants API and the OpenAI Agents SDK into MeshFlow.

Adapter surface:
  - agent_from_openai_assistant(assistant_id, api_key, ...)   → MeshFlow Agent
  - agent_from_openai_agents_sdk(oai_agent, ...)              → MeshFlow Agent
  - team_from_openai_agents(agents, name, policy, pattern)    → MeshFlow Team
  - tool_from_openai_function(fn, ...)                        → MeshFlow Tool
  - mesh_tool_to_openai_function(tool)                        → OpenAI function schema dict

Usage:
    from meshflow.integrations.openai import (
        agent_from_openai_assistant,
        agent_from_openai_agents_sdk,
        team_from_openai_agents,
        tool_from_openai_function,
        mesh_tool_to_openai_function,
    )

    # Wrap an OpenAI Assistant as a governed MeshFlow Agent
    agent = agent_from_openai_assistant(
        assistant_id="asst_abc123",
        api_key="sk-...",
        name="gpt_researcher",
    )

    # Wrap an OpenAI Agents SDK agent (openai-agents package)
    from agents import Agent as OAIAgent
    oai_agent = OAIAgent(name="helper", instructions="You help with tasks.")
    mf_agent = agent_from_openai_agents_sdk(oai_agent, name="oai_helper")
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool


def agent_from_openai_assistant(
    assistant_id: str,
    api_key: str,
    name: str = "openai_assistant",
    role: str = "executor",
    policy: Any = None,
    poll_interval: float = 1.0,
    timeout: float = 120.0,
) -> Any:
    """Wrap an OpenAI Assistant as a governed MeshFlow Agent.

    Requires openai: pip install openai

    Calls the Assistants API (threads → runs → messages) and returns
    the final assistant message as the node output.
    """
    from meshflow.agents.builder import Agent

    async def _step(task: str, context: dict[str, Any]) -> Any:
        from meshflow.core.node import NodeOutput

        try:
            import openai

            client = openai.AsyncOpenAI(api_key=api_key)

            thread = await client.beta.threads.create()
            await client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=task,
            )
            run = await client.beta.threads.runs.create(
                thread_id=thread.id,
                assistant_id=assistant_id,
            )

            elapsed = 0.0
            while run.status in ("queued", "in_progress", "cancelling"):
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                if elapsed >= timeout:
                    await client.beta.threads.runs.cancel(thread_id=thread.id, run_id=run.id)
                    return NodeOutput(content="[OpenAI] Run timed out.", confidence=0.0)
                run = await client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

            if run.status != "completed":
                return NodeOutput(
                    content=f"[OpenAI] Run ended with status: {run.status}",
                    confidence=0.0,
                )

            messages = await client.beta.threads.messages.list(thread_id=thread.id, order="desc")
            for msg in messages.data:
                if msg.role == "assistant":
                    content = msg.content[0]
                    text = content.text.value if hasattr(content, "text") else str(content)
                    return NodeOutput(
                        content=text,
                        structured={"assistant_id": assistant_id, "thread_id": thread.id},
                        confidence=0.85,
                    )
            return NodeOutput(content="[OpenAI] No assistant message found.", confidence=0.0)

        except ImportError:
            return NodeOutput(
                content=f"[openai stub] Task: {task[:100]}",
                structured={"assistant_id": assistant_id, "stub": True},
                confidence=0.5,
            )

    from meshflow.core.node import MeshNode

    node = MeshNode.from_callable(
        name,
        _step,
        risk=RiskTier.EXTERNAL_IO,
        capabilities=["openai", "assistant", role],
    )
    a = Agent(name=name, role=role, policy=policy)
    a._prebuilt_node = node
    return a


def agent_from_openai_agents_sdk(
    oai_agent: Any,
    name: str | None = None,
    role: str = "executor",
    policy: Any = None,
) -> Any:
    """Wrap an OpenAI Agents SDK Agent as a MeshFlow Agent.

    Requires openai-agents: pip install openai-agents
    Works with the Agent class from the openai-agents package.
    """
    from meshflow.agents.builder import Agent

    agent_name = name or str(getattr(oai_agent, "name", "oai_agent"))

    async def _step(task: str, context: dict[str, Any]) -> Any:
        from meshflow.core.node import NodeOutput

        try:
            from agents import Runner

            result = await Runner.run(oai_agent, task)
            output = result.final_output if hasattr(result, "final_output") else str(result)
            return NodeOutput(content=str(output), confidence=0.85)
        except ImportError:
            return NodeOutput(
                content=f"[openai-agents stub] Task: {task[:100]}",
                structured={"stub": True},
                confidence=0.5,
            )

    from meshflow.core.node import MeshNode

    node = MeshNode.from_callable(
        agent_name,
        _step,
        risk=RiskTier.EXTERNAL_IO,
        capabilities=["openai-agents", role],
    )
    a = Agent(name=agent_name, role=role, policy=policy)
    a._prebuilt_node = node
    return a


def team_from_openai_agents(
    agents: list[Any],
    name: str = "openai_team",
    policy: Any = None,
    pattern: str = "sequential",
) -> Any:
    """Wrap a list of OpenAI Agents SDK agents as a governed MeshFlow Team.

    Each agent is converted via ``agent_from_openai_agents_sdk`` and placed in
    a ``Team`` under the given pattern (sequential / parallel / hierarchical /
    supervised).

    Requires openai-agents: pip install openai-agents
    """
    from meshflow.agents.team import Team

    if not agents:
        raise ValueError("OpenAI agents team must have at least one agent.")

    mf_agents = [agent_from_openai_agents_sdk(a, policy=policy) for a in agents]
    return Team(name=name, agents=mf_agents, pattern=pattern, policy=policy)


def mesh_tool_to_openai_function(tool: Tool) -> dict[str, Any]:
    """Convert a MeshFlow Tool to an OpenAI function-calling schema dict.

    The returned dict is compatible with the ``tools`` parameter of:
    - OpenAI Chat Completions API  (``client.chat.completions.create(tools=[...])`` )
    - OpenAI Assistants API        (``client.beta.assistants.create(tools=[...])`` )

    Example return value::

        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for information.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    """
    import inspect

    import typing

    _PY_TO_JSON: dict[Any, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    properties: dict[str, Any] = {}
    required: list[str] = []

    try:
        sig = inspect.signature(tool.fn)
    except (ValueError, TypeError):
        sig = None

    hints: dict[str, Any] = {}
    try:
        hints = typing.get_type_hints(tool.fn)
    except Exception:
        pass

    if sig is not None:
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            annotation = hints.get(pname, param.annotation)
            json_type = "string"
            if annotation is not inspect.Parameter.empty:
                json_type = _PY_TO_JSON.get(annotation, "string")
            properties[pname] = {"type": json_type}
            if param.default is inspect.Parameter.empty:
                required.append(pname)

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def tool_from_openai_function(
    fn: Any,
    name: str | None = None,
    description: str = "",
    risk: RiskTier = RiskTier.READ_ONLY,
) -> Tool:
    """Wrap an OpenAI function-calling tool as a MeshFlow Tool.

    Works with plain Python callables registered for OpenAI function calling.
    """
    tool_name = str(name or getattr(fn, "__name__", "openai_tool"))
    tool_desc = str(description or (fn.__doc__ or "").strip().split("\n")[0] or tool_name)

    async def _call(**kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn(**kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(**kwargs))

    return Tool(
        name=tool_name,
        description=tool_desc,
        fn=_call,
        risk=risk,
        tags=["openai"],
    )
