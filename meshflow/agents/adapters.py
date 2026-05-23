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
from meshflow.core.schemas import AgentRole, Policy


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
                "tokens": 0,  # external agents don't report MeshFlow token counts
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
        import asyncio
        import inspect

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

    Compatible with CrewAI v0.1–0.80+.

    Uses a minimal single-task Crew kickoff so the agent actually executes
    rather than calling execute_task() directly (which changed signature in
    CrewAI ≥ 0.70 and requires a proper Task object with expected_output).
    """
    if policy is None:
        policy = Policy()

    agent_id = getattr(crew_agent, "role", str(uuid.uuid4())[:8])
    model = getattr(crew_agent, "llm", {})
    model_name = str(model) if model else "claude-sonnet-4-6"

    def run_fn(task: str, context: dict[str, Any]) -> str:
        # Strategy 1: modern CrewAI (≥ 0.70) — create a proper Task + mini Crew
        try:
            from crewai import Task as CrewTask, Crew as CrewCrew  # type: ignore[import]
            t = CrewTask(
                description=task,
                expected_output="Provide a comprehensive response to the task.",
                agent=crew_agent,
            )
            mini_crew = CrewCrew(agents=[crew_agent], tasks=[t], verbose=False)
            result = mini_crew.kickoff()
            return str(getattr(result, "raw", result))
        except Exception:
            pass

        # Strategy 2: older CrewAI — execute_task with string
        try:
            return str(crew_agent.execute_task(task))
        except Exception:
            pass

        # Strategy 3: execute_task with fake Task object (oldest versions)
        try:
            task_obj = type("Task", (), {"description": task, "context": context})()
            return str(crew_agent.execute_task(task_obj))
        except Exception as e:
            return f"CrewAI execution error: {e}"

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
    """Wrap an AutoGen agent as a MeshFlow agent.

    Supports AutoGen v0.2/v0.3 (generate_reply) and v0.4 (on_messages).
    Version is detected at call time via duck-typing — no hard import required.
    """
    if policy is None:
        policy = Policy()

    agent_id = getattr(autogen_agent, "name", str(uuid.uuid4())[:8])
    model_name = "claude-sonnet-4-6"

    async def run_fn(task: str, context: dict[str, Any]) -> str:
        from meshflow.integrations.autogen import invoke_autogen_agent
        output, _tokens, _cost = await invoke_autogen_agent(autogen_agent, task, context)
        return output

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
        if hasattr(runnable, "ainvoke"):
            result = await runnable.ainvoke(combined)
        elif hasattr(runnable, "invoke"):
            import asyncio

            result = await asyncio.get_event_loop().run_in_executor(None, runnable.invoke, combined)
        else:
            result = str(runnable)
        from meshflow.integrations.langgraph import _extract_lg_output
        return _extract_lg_output(result)

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
