"""Crew — CrewAI-compatible crew with MeshFlow governance.

Usage (CrewAI style):
    from meshflow import Agent, Task, Crew, Process

    analyst  = Agent(name="analyst",  role="researcher")
    writer   = Agent(name="writer",   role="executor")
    guardian = Agent(name="guardian", role="guardian")

    research = Task(description="Research {topic}.", expected_output="5 findings.", agent=analyst)
    draft    = Task(description="Write report.", expected_output="Draft.", agent=writer, context=[research])

    crew = Crew(
        agents=[analyst, writer],
        tasks=[research, draft],
        process=Process.sequential,
        verbose=True,
    )
    result = await crew.kickoff(inputs={"topic": "LLM governance"})
    print(result.raw)               # final task output
    print(result.tasks_output)      # per-task outputs

Process modes:
    sequential    Tasks run in order; each receives prior outputs as context.
    parallel      All tasks run concurrently (no inter-task context); last result is "final".
    hierarchical  First task is the manager; it plans, then remaining tasks run sequentially.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from meshflow.agents.task import Task, TaskOutput


class Process(str, Enum):
    """Execution strategy for a Crew."""

    sequential   = "sequential"
    parallel     = "parallel"
    hierarchical = "hierarchical"


@dataclass
class CrewOutput:
    """Aggregated result of a Crew.kickoff() call."""

    raw: str                         # final task's raw text
    tasks_output: list[TaskOutput]   # one entry per task, in execution order
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return (
            f"CrewOutput(tasks={len(self.tasks_output)}, "
            f"tokens={self.total_tokens}, cost=${self.total_cost_usd:.4f})"
        )


@dataclass
class Crew:
    """A governed, policy-aware crew of agents that work through a list of tasks.

    Parameters
    ----------
    agents:      All Agent instances in this crew.
    tasks:       Ordered list of Task objects to execute.
    process:     Execution strategy — sequential (default), parallel, hierarchical.
    manager_llm: Override LLM for the manager agent in hierarchical mode.
    verbose:     Print progress lines during execution.
    policy:      MeshFlow Policy (defaults to "standard").
    """

    agents: list[Any]                # list[Agent]
    tasks: list[Task]
    process: Process = Process.sequential
    manager_llm: Any = None
    verbose: bool = False
    policy: Any = None               # meshflow.core.schemas.Policy

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ValueError("Crew must have at least one task.")
        if not self.agents:
            raise ValueError("Crew must have at least one agent.")
        if isinstance(self.process, str):
            self.process = Process(self.process)

    # ── Public API ────────────────────────────────────────────────────────────

    async def kickoff(self, inputs: dict[str, Any] | None = None) -> CrewOutput:
        """Run the crew through all tasks and return an aggregated CrewOutput."""
        if self.process == Process.parallel:
            return await self._run_parallel(inputs)
        if self.process == Process.hierarchical:
            return await self._run_hierarchical(inputs)
        return await self._run_sequential(inputs)

    # ── Execution modes ───────────────────────────────────────────────────────

    async def _run_sequential(self, inputs: dict[str, Any] | None) -> CrewOutput:
        """Tasks execute in order; each receives all prior task outputs as context."""
        outputs: list[TaskOutput] = []
        for i, task in enumerate(self.tasks):
            if self.verbose:
                agent_name = getattr(task.agent, "name", "?") if task.agent else "?"
                print(f"[Crew] Task {i+1}/{len(self.tasks)}: {task.description[:60]}  → {agent_name}")

            # Auto-wire context: inject all previous tasks if task.context is not set
            if task.context is None and outputs:
                task.context = list(self.tasks[:i])

            out = await task.run(inputs)
            outputs.append(out)

            if self.verbose:
                print(f"[Crew]   ✓ {len(out.raw)} chars, {out.tokens} tokens")

        return self._aggregate(outputs)

    async def _run_parallel(self, inputs: dict[str, Any] | None) -> CrewOutput:
        """All tasks run concurrently — no inter-task context injection."""
        if self.verbose:
            print(f"[Crew] Running {len(self.tasks)} tasks in parallel")

        results = await asyncio.gather(
            *[task.run(inputs) for task in self.tasks],
            return_exceptions=True,
        )

        outputs: list[TaskOutput] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                raise r
            outputs.append(r)  # type: ignore[arg-type]

        return self._aggregate(outputs)

    async def _run_hierarchical(self, inputs: dict[str, Any] | None) -> CrewOutput:
        """First task acts as manager/planner; remaining tasks run sequentially with manager context."""
        if len(self.tasks) < 2:
            return await self._run_sequential(inputs)

        manager_task = self.tasks[0]
        worker_tasks = self.tasks[1:]

        if self.verbose:
            agent_name = getattr(manager_task.agent, "name", "manager")
            print(f"[Crew] Manager ({agent_name}): {manager_task.description[:60]}")

        # Apply manager LLM override if provided
        original_llm = None
        if self.manager_llm is not None and manager_task.agent is not None:
            original_llm = getattr(manager_task.agent, "llm", None)
            manager_task.agent.llm = self.manager_llm

        try:
            manager_out = await manager_task.run(inputs)
        finally:
            if original_llm is not None and manager_task.agent is not None:
                manager_task.agent.llm = original_llm

        outputs = [manager_out]

        for i, task in enumerate(worker_tasks):
            if self.verbose:
                agent_name = getattr(task.agent, "name", "?") if task.agent else "?"
                print(f"[Crew] Worker {i+1}/{len(worker_tasks)} ({agent_name}): {task.description[:60]}")

            # Auto-inject manager output as context
            if task.context is None:
                task.context = [manager_task]

            out = await task.run(inputs)
            outputs.append(out)

            if self.verbose:
                print(f"[Crew]   ✓ {len(out.raw)} chars")

        return self._aggregate(outputs)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _aggregate(outputs: list[TaskOutput]) -> CrewOutput:
        total_tokens = sum(o.tokens for o in outputs)
        total_cost   = sum(o.cost_usd for o in outputs)
        final_raw    = outputs[-1].raw if outputs else ""
        return CrewOutput(
            raw=final_raw,
            tasks_output=outputs,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
        )
