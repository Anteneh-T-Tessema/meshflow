"""Framework adapters — import agents from CrewAI, LangGraph, AutoGen without rewriting.

The adapter pattern: each adapter speaks the source framework's internal protocol
and exposes a standard MeshFlow BaseAgent interface. Users never touch glue code.

Usage:
    from meshflow.agents.adapters import from_crewai, from_autogen, from_langgraph

    agent = from_crewai(crew_agent)
    agent = from_autogen(autogen_agent)
    agent = from_langgraph(lg_runnable, role=AgentRole.RESEARCHER)
"""
from __future__ import annotations

import uuid
from typing import Any

from meshflow.agents.base import AgentConfig, BaseAgent
from meshflow.core.schemas import AgentRole, Evidence, Policy


class _WrappedExternalAgent(BaseAgent):
    """Wraps an external agent with a MeshFlow-compatible step() method."""

    def __init__(
        self,
        config: AgentConfig,
        policy: Policy,
        run_fn: Any,
        framework: str,
    ) -> None:
        super().__init__(config, policy)
        self._run_fn = run_fn
        self._framework = framework

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._invoke(task, context)
            return {
                "execution_result": str(result),
                "framework": self._framework,
                "agent_id": self.agent_id,
                "stated_confidence": 0.75,
                "tokens": 0,   # external agents don't report MeshFlow token counts
                "cost_usd": 0.0,
            }
        except Exception as e:
            return {
                "execution_result": f"Error from {self._framework} agent: {e}",
                "framework": self._framework,
                "agent_id": self.agent_id,
                "error": str(e),
                "stated_confidence": 0.0,
                "tokens": 0,
                "cost_usd": 0.0,
            }

    async def _invoke(self, task: str, context: dict[str, Any]) -> Any:
        import asyncio, inspect
        if inspect.iscoroutinefunction(self._run_fn):
            return await self._run_fn(task, context)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run_fn, task, context)


# ── Public adapter functions ──────────────────────────────────────────────────

def from_crewai(
    crew_agent: Any,
    role: AgentRole = AgentRole.EXECUTOR,
    policy: Policy | None = None,
) -> BaseAgent:
    """Wrap a CrewAI Agent as a MeshFlow agent.

    CrewAI agents expose an .execute_task(task) method.
    We wrap it in an async callable that MeshFlow's step() can drive.
    """
    if policy is None:
        policy = Policy()

    agent_id = getattr(crew_agent, "role", str(uuid.uuid4())[:8])
    model = getattr(crew_agent, "llm", {})
    model_name = str(model) if model else "claude-sonnet-4-6"

    def run_fn(task: str, context: dict[str, Any]) -> str:
        # CrewAI task execution — simplified bridge
        from dataclasses import dataclass
        try:
            # CrewAI Task object
            task_obj = type("Task", (), {"description": task, "context": context})()
            return crew_agent.execute_task(task_obj)
        except TypeError:
            # Fallback for different CrewAI versions
            return crew_agent.execute_task(task)

    config = AgentConfig(
        agent_id=str(agent_id)[:12],
        role=role,
        model=model_name,
        system_prompt=getattr(crew_agent, "backstory", ""),
    )
    return _WrappedExternalAgent(config, policy, run_fn, "crewai")


def from_autogen(
    autogen_agent: Any,
    role: AgentRole = AgentRole.EXECUTOR,
    policy: Policy | None = None,
) -> BaseAgent:
    """Wrap an AutoGen ConversableAgent as a MeshFlow agent.

    AutoGen agents respond via .generate_reply(messages).
    """
    if policy is None:
        policy = Policy()

    agent_id = getattr(autogen_agent, "name", str(uuid.uuid4())[:8])
    model_name = "claude-sonnet-4-6"

    async def run_fn(task: str, context: dict[str, Any]) -> str:
        messages = [{"role": "user", "content": task}]
        reply = autogen_agent.generate_reply(messages=messages)
        return str(reply) if reply else ""

    config = AgentConfig(
        agent_id=str(agent_id)[:12],
        role=role,
        model=model_name,
        system_prompt=getattr(autogen_agent, "system_message", ""),
    )
    return _WrappedExternalAgent(config, policy, run_fn, "autogen")


def from_langgraph(
    runnable: Any,
    role: AgentRole = AgentRole.EXECUTOR,
    policy: Policy | None = None,
    agent_id: str | None = None,
) -> BaseAgent:
    """Wrap a LangGraph Runnable (chain, agent, graph) as a MeshFlow agent.

    LangGraph runnables expose .invoke(input) and .ainvoke(input).
    """
    if policy is None:
        policy = Policy()

    aid = agent_id or str(uuid.uuid4())[:8]

    async def run_fn(task: str, context: dict[str, Any]) -> str:
        combined = {"input": task, **context}
        import inspect
        if hasattr(runnable, "ainvoke"):
            result = await runnable.ainvoke(combined)
        elif hasattr(runnable, "invoke"):
            import asyncio
            result = await asyncio.get_event_loop().run_in_executor(
                None, runnable.invoke, combined
            )
        else:
            result = str(runnable)
        # LangGraph returns dicts, AIMessages, or strings
        if isinstance(result, dict):
            return result.get("output", result.get("answer", str(result)))
        return str(result)

    config = AgentConfig(
        agent_id=aid,
        role=role,
        model="claude-sonnet-4-6",
    )
    return _WrappedExternalAgent(config, policy, run_fn, "langgraph")


def from_callable(
    fn: Any,
    role: AgentRole = AgentRole.EXECUTOR,
    policy: Policy | None = None,
    agent_id: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> BaseAgent:
    """Wrap any async or sync callable as a MeshFlow agent.

    The callable signature must accept (task: str, context: dict) -> str.
    """
    if policy is None:
        policy = Policy()
    aid = agent_id or str(uuid.uuid4())[:8]
    config = AgentConfig(agent_id=aid, role=role, model=model)
    return _WrappedExternalAgent(config, policy, fn, "callable")
