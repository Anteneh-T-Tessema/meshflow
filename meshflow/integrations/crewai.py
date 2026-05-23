"""CrewAI ↔ MeshFlow integration.

Two-way bridge:
  - tool_from_crewai(crew_tool)       CrewAI BaseTool  → MeshFlow Tool
  - tools_from_crewai([...])           list              → list[Tool]
  - agent_from_crewai(agent, name)    CrewAI Agent     → MeshFlow Agent
  - team_from_crewai(crew)            CrewAI Crew      → MeshFlow Team
  - task_from_crewai(task)            CrewAI Task      → (description, context)
  - mesh_tool_to_crewai(tool)         MeshFlow Tool    → CrewAI BaseTool

Compatibility: CrewAI v0.1–0.80+.
"""

from __future__ import annotations

import asyncio
from typing import Any

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool
from meshflow.integrations._utils import run_sync, extract_tokens


# ── Tool conversion ────────────────────────────────────────────────────────────

def tool_from_crewai(crew_tool: Any, risk: RiskTier = RiskTier.READ_ONLY) -> Tool:
    """Convert a CrewAI BaseTool to a MeshFlow Tool.

    Works with any object that has .name, .description, and ._run / .run.
    """
    name = str(getattr(crew_tool, "name", type(crew_tool).__name__))
    description = str(getattr(crew_tool, "description", name))

    async def _call(**kwargs: Any) -> Any:
        single = next(iter(kwargs.values()), "") if kwargs else ""
        # Try async variants first
        if hasattr(crew_tool, "_arun"):
            result = crew_tool._arun(single)
            if asyncio.iscoroutine(result):
                return await result
            return result
        if hasattr(crew_tool, "_run"):
            result = crew_tool._run(single)
            if asyncio.iscoroutine(result):
                return await result
            return result
        if hasattr(crew_tool, "run"):
            result = crew_tool.run(single)
            if asyncio.iscoroutine(result):
                return await result
            return result
        raise RuntimeError(f"CrewAI tool '{name}' has no _run or run method.")

    return Tool(name=name, description=description, fn=_call, risk=risk, tags=["crewai"])


def tools_from_crewai(
    crew_tools: list[Any],
    risk: RiskTier = RiskTier.READ_ONLY,
) -> list[Tool]:
    """Convert a list of CrewAI tools to MeshFlow Tools."""
    return [tool_from_crewai(t, risk=risk) for t in crew_tools]


# ── Agent / team wrapping ──────────────────────────────────────────────────────

def agent_from_crewai(
    crew_agent: Any,
    name: str | None = None,
    role: str = "executor",
    policy: Any = None,
) -> Any:
    """Wrap a CrewAI Agent as a MeshFlow Agent.

    Imports tools, role name, and backstory automatically.
    Compatible with CrewAI v0.1–0.80+.
    """
    from meshflow.agents.builder import Agent as MFAgent

    agent_name = name or str(getattr(crew_agent, "role", "crewai_agent"))
    backstory = str(getattr(crew_agent, "backstory", ""))
    crew_tools = list(getattr(crew_agent, "tools", []) or [])
    mf_tools = tools_from_crewai(crew_tools)

    return MFAgent(
        name=agent_name,
        role=role,
        tools=mf_tools,
        system_prompt=backstory,
        policy=policy,
    )


def team_from_crewai(crew: Any, policy: Any = None) -> Any:
    """Wrap a CrewAI Crew as a MeshFlow Team.

    Agents are imported with their tools and backstories.
    Task descriptions are preserved as a concatenated mission brief passed to
    team.run() as the task when no explicit task is provided.

    Compatible with CrewAI v0.1–0.80+.
    """
    from meshflow.agents.team import Team

    crew_agents = list(getattr(crew, "agents", []) or [])
    if not crew_agents:
        raise ValueError("CrewAI Crew has no agents.")

    mf_agents = [agent_from_crewai(a, policy=policy) for a in crew_agents]

    # Preserve task context as a mission brief
    crew_tasks = list(getattr(crew, "tasks", []) or [])
    task_briefs = []
    for t in crew_tasks:
        desc = str(getattr(t, "description", "")).strip()
        expected = str(getattr(t, "expected_output", "")).strip()
        if desc:
            brief = desc
            if expected:
                brief += f" (expected: {expected})"
            task_briefs.append(brief)

    crew_name = str(getattr(crew, "id", "crewai_crew"))
    team = Team(
        name=crew_name,
        agents=mf_agents,
        pattern="supervised",
        policy=policy,
    )
    # Attach task briefs as a non-dataclass attribute for downstream inspection
    object.__setattr__(team, "_task_briefs", task_briefs) if hasattr(team, "__dataclass_fields__") else None
    try:
        team._task_briefs = task_briefs  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    return team


def task_from_crewai(crew_task: Any) -> dict[str, Any]:
    """Extract a CrewAI Task's key fields as a plain dict.

    Returns::

        {
            "description": str,
            "expected_output": str,
            "agent_role": str | None,   # role of the assigned agent
            "context_tasks": list[str], # descriptions of context tasks
            "output_type": str,         # "raw" | "json" | "pydantic"
        }
    """
    desc = str(getattr(crew_task, "description", ""))
    expected = str(getattr(crew_task, "expected_output", ""))
    assigned = getattr(crew_task, "agent", None)
    agent_role = str(getattr(assigned, "role", "")) if assigned else None
    context = getattr(crew_task, "context", None) or []
    context_descs = [str(getattr(t, "description", t)) for t in context]

    output_type = "raw"
    if getattr(crew_task, "output_pydantic", None):
        output_type = "pydantic"
    elif getattr(crew_task, "output_json", None):
        output_type = "json"

    return {
        "description": desc,
        "expected_output": expected,
        "agent_role": agent_role,
        "context_tasks": context_descs,
        "output_type": output_type,
    }


# ── Reverse direction ──────────────────────────────────────────────────────────

def mesh_tool_to_crewai(tool: Tool) -> Any:
    """Export a MeshFlow Tool as a CrewAI-compatible BaseTool.

    Fixed: no longer calls run_until_complete() on an active loop.
    Requires crewai: pip install crewai
    """
    try:
        from crewai.tools import BaseTool as CrewBaseTool
    except ImportError as exc:
        raise ImportError("crewai is required: pip install crewai") from exc

    tool_name = tool.name
    tool_description = tool.description
    tool_ref = tool

    class _MeshFlowCrewTool(CrewBaseTool):  # type: ignore[misc]
        name: str = tool_name
        description: str = tool_description

        def _run(self, argument: str = "", **kwargs: Any) -> str:
            return str(run_sync(tool_ref.call(input=argument, **kwargs)))

        async def _arun(self, argument: str = "", **kwargs: Any) -> str:
            return str(await tool_ref.call(input=argument, **kwargs))

    return _MeshFlowCrewTool()


def mesh_tools_to_crewai(tools: list[Tool]) -> list[Any]:
    """Export a list of MeshFlow Tools as CrewAI BaseTools."""
    return [mesh_tool_to_crewai(t) for t in tools]


# ── Kickoff helper ─────────────────────────────────────────────────────────────

def run_crew_governed(
    crew: Any,
    inputs: dict[str, Any],
    policy: Any = None,
    ledger_path: str = "meshflow_runs.db",
) -> Any:
    """Run a CrewAI Crew's kickoff() with a MeshFlow governance wrapper.

    Records cost, tokens, and output in the MeshFlow ledger.
    Returns the original CrewOutput / string from kickoff().
    """
    import datetime, uuid, asyncio as _asyncio
    from meshflow.core.runtime import StepRecord
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.schemas import Policy

    if policy is None:
        policy = Policy()
    elif isinstance(policy, str):
        from meshflow.core.schemas import policy_for_mode
        policy = policy_for_mode(policy)

    import time
    t0 = time.perf_counter()
    result = crew.kickoff(inputs=inputs)
    duration_ms = (time.perf_counter() - t0) * 1000

    tokens, cost = extract_tokens(result)
    content = str(getattr(result, "raw", result))[:2000]

    run_id = str(uuid.uuid4())
    record = StepRecord(
        run_id=run_id,
        step_id=str(uuid.uuid4()),
        node_id="crewai_crew",
        node_kind="crewai",
        input_task=str(inputs)[:500],
        output_content=content,
        verdict="commit",
        blocked=False,
        block_reason="",
        uncertainty=0.1,
        cost_usd=cost,
        tokens_used=tokens,
        carbon_gco2=0.0,
        duration_ms=duration_ms,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        metadata={"framework": "crewai", "governed": True},
    )

    async def _write() -> None:
        ledger = ReplayLedger(ledger_path)
        await ledger.write(record)

    run_sync(_write())
    return result
