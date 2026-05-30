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
from typing import Any, AsyncIterator

from meshflow.agents.task import Task, TaskOutput
from meshflow.core.streaming import StreamChunk


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
    role_router: Any = None          # optional RoleRouter for dynamic agent creation

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ValueError("Crew must have at least one task.")
        if self.role_router is None and not self.agents:
            raise ValueError("Crew must have at least one agent (or a role_router).")
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

    async def kickoff_stream(
        self,
        inputs: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream token-by-token output from each task in the crew.

        Yields ``StreamChunk`` events:
          ``task_start`` — task is beginning (node_name = description preview)
          ``token``      — one text token from the agent's LLM stream
          ``task_end``   — task finished (content = full output, metadata has tokens)
          ``done``       — all tasks complete

        The agent's LLM is called **once** per task (streaming); the full
        collected output is stored as the task's ``output`` for context injection.

        Works for sequential and hierarchical process modes.
        Parallel mode yields all tasks concurrently (interleaved by task_index).
        """
        return self._kickoff_stream_impl(inputs)

    async def _kickoff_stream_impl(
        self,
        inputs: dict[str, Any] | None,
    ) -> AsyncIterator[StreamChunk]:
        if self.process == Process.parallel:
            async for chunk in self._stream_parallel(inputs):
                yield chunk
            return

        # Sequential / hierarchical — stream tasks in execution order
        ordered_tasks = list(self.tasks)
        if self.process == Process.hierarchical and len(self.tasks) >= 2:
            # manager first, rest sequential
            pass  # same order as self.tasks; context injection happens below

        executed: list[Task] = []

        for i, task in enumerate(ordered_tasks):
            # Auto-wire context for sequential/hierarchical
            if task.context is None and executed:
                task.context = list(executed)

            desc_preview = task.description[:60]
            yield StreamChunk(kind="task_start", task_index=i, node_name=desc_preview)

            prompt = task._build_prompt(inputs)
            agent = task.agent
            if agent is None:
                yield StreamChunk(kind="error", task_index=i, content="Task has no agent")
                continue

            collected: list[str] = []
            async for token in agent.stream(prompt):
                yield StreamChunk(kind="token", content=token, task_index=i, node_name=agent.name)
                collected.append(token)

            full_output = "".join(collected)

            # Store output so downstream tasks can use it as context
            from meshflow.agents.task import TaskOutput as _TO
            task.output = _TO(
                raw=full_output,
                task_description=task.description[:120],
                agent_name=getattr(agent, "name", ""),
            )
            executed.append(task)

            yield StreamChunk(
                kind="task_end",
                task_index=i,
                content=full_output,
                node_name=getattr(agent, "name", ""),
                metadata={"tokens": len(full_output.split())},
            )

            if self.verbose:
                print(f"[Crew stream] Task {i+1}/{len(ordered_tasks)} ✓")

        yield StreamChunk(kind="done")

    async def _stream_parallel(
        self,
        inputs: dict[str, Any] | None,
    ) -> AsyncIterator[StreamChunk]:
        q: asyncio.Queue[StreamChunk | None] = asyncio.Queue()
        active = len(self.tasks)

        async def _run_task(i: int, task: Task) -> None:
            desc = task.description[:60]
            await q.put(StreamChunk(kind="task_start", task_index=i, node_name=desc))
            prompt = task._build_prompt(inputs)
            agent = task.agent
            if agent is None:
                await q.put(StreamChunk(kind="error", task_index=i, content="No agent"))
                await q.put(None)
                return
            collected: list[str] = []
            async for token in agent.stream(prompt):
                await q.put(StreamChunk(kind="token", content=token, task_index=i, node_name=agent.name))
                collected.append(token)
            full = "".join(collected)
            from meshflow.agents.task import TaskOutput as _TO
            task.output = _TO(raw=full, task_description=task.description[:120], agent_name=agent.name)
            await q.put(StreamChunk(kind="task_end", task_index=i, content=full, node_name=agent.name))
            await q.put(None)

        tasks = [asyncio.create_task(_run_task(i, t)) for i, t in enumerate(self.tasks)]
        finished = 0
        while finished < active:
            chunk = await q.get()
            if chunk is None:
                finished += 1
            else:
                yield chunk
        await asyncio.gather(*tasks, return_exceptions=True)
        yield StreamChunk(kind="done")

    # ── Execution modes ───────────────────────────────────────────────────────

    async def _resolve_agent_for_task(self, task: Task) -> None:
        """If a RoleRouter is configured, assign a dynamically created agent to
        any task that has no agent assigned yet."""
        if task.agent is not None or self.role_router is None:
            return
        try:
            spec = await self.role_router.route(task.description)
            task.agent = spec.to_agent(name=f"dynamic-{spec.role}")
            if self.verbose:
                print(f"[Crew] RoleRouter → {spec.role} ({spec.model_tier}) "
                      f"for task: {task.description[:50]}")
        except Exception as exc:
            # Fallback to first available agent
            if self.agents:
                task.agent = self.agents[0]
            if self.verbose:
                print(f"[Crew] RoleRouter failed ({exc}), using fallback agent")

    async def _run_sequential(self, inputs: dict[str, Any] | None) -> CrewOutput:
        """Tasks execute in order; each receives all prior task outputs as context."""
        outputs: list[TaskOutput] = []
        for i, task in enumerate(self.tasks):
            await self._resolve_agent_for_task(task)
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

    # ── YAML factory ──────────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str) -> "Crew":
        """Instantiate a :class:`Crew` from a YAML file.

        Expected format::

            version: "1.0"
            kind: crew                  # optional; default is "crew"
            name: my_pipeline           # optional label
            process: sequential         # sequential | parallel | hierarchical
            verbose: false
            agents:
              - name: researcher
                role: researcher
                model: claude-haiku-4-5-20251001   # optional
              - name: writer
                role: executor
            tasks:
              - description: "Research {topic}"
                expected_output: "A research summary"
                agent: researcher       # matches agent name above
              - description: "Write article"
                agent: writer
        """
        import yaml  # type: ignore[import]
        from meshflow.agents.builder import Agent  # avoid circular at module level

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Build agents index
        agent_defs = data.get("agents", [])
        agent_map: dict[str, Any] = {}
        for a in agent_defs:
            name = a["name"]
            kwargs: dict[str, Any] = {"name": name}
            if "role" in a:
                kwargs["role"] = a["role"]
            if "model" in a:
                kwargs["model"] = a["model"]
            if "system_prompt" in a:
                kwargs["system_prompt"] = a["system_prompt"]
            if "skills" in a:
                kwargs["skills"] = a["skills"]
            agent_map[name] = Agent(**kwargs)

        # Build tasks
        task_objs: list[Task] = []
        for t in data.get("tasks", []):
            agent_ref = agent_map.get(t.get("agent", ""))
            task_objs.append(
                Task(
                    description=t["description"],
                    expected_output=t.get("expected_output", ""),
                    agent=agent_ref,
                )
            )

        process_str = data.get("process", "sequential")
        verbose = bool(data.get("verbose", False))

        return cls(
            agents=list(agent_map.values()),
            tasks=task_objs,
            process=Process(process_str),
            verbose=verbose,
        )

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
