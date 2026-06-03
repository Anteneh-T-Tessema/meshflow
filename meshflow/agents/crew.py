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
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator

from meshflow.agents.task import Task, TaskOutput
from meshflow.core.streaming import StreamChunk


def _parse_confidence(text: str) -> float:
    """Extract a confidence score from task output text (0.0 if absent)."""
    m = re.search(r'"?confidence"?\s*:\s*([0-9]*\.?[0-9]+)', text, re.IGNORECASE)
    return float(m.group(1)) if m else 0.0


def _dedup_context(prompts: list[str]) -> list[str]:
    """Remove duplicate sentences/paragraphs shared across parallel task prompts.

    Returns a new list of prompts where text blocks repeated verbatim across
    two or more prompts are replaced with a ``[shared context omitted]`` marker
    in the duplicates, reducing total token count for parallel crew runs.
    """
    if len(prompts) < 2:
        return list(prompts)

    # Split each prompt into sentence-level chunks
    def _chunks(text: str) -> list[str]:
        return [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n{2,}', text) if len(s.strip()) > 40]

    # Count how many prompts each chunk appears in
    chunk_counts: dict[str, int] = {}
    for p in prompts:
        for chunk in set(_chunks(p)):
            chunk_counts[chunk] = chunk_counts.get(chunk, 0) + 1

    shared = {c for c, n in chunk_counts.items() if n >= 2}
    if not shared:
        return list(prompts)

    # Keep shared chunks only in the first prompt that contains them
    seen: set[str] = set()
    result: list[str] = []
    for p in prompts:
        deduped_parts: list[str] = []
        for chunk in _chunks(p):
            if chunk in shared:
                if chunk not in seen:
                    seen.add(chunk)
                    deduped_parts.append(chunk)
                # else: silently drop the duplicate
            else:
                deduped_parts.append(chunk)
        result.append(" ".join(deduped_parts) if deduped_parts else p)
    return result


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
    stop_on_confidence: float | None = None  # exit sequential run early when met
    context_dedup: bool = False              # deduplicate shared context in parallel runs
    memory_config: dict[str, Any] | None = None
    """Crew-level memory provider configuration (CrewAI-compatible).

    Supported keys:

    ``provider`` — ``"sqlite"`` (default), ``"redis"``, ``"file"``, ``"in_memory"``.
    ``config``   — provider-specific options (e.g. path, url, ttl, prefix).

    Example::

        crew = Crew(
            agents=[a1, a2],
            tasks=[t1, t2],
            memory_config={
                "provider": "sqlite",
                "config": {"path": "crew_memory.db"},
            },
        )

    When set, a shared :class:`~meshflow.intelligence.memory.AgentMemory`
    backed by the specified backend is attached to every agent in the crew
    before execution.
    """

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ValueError("Crew must have at least one task.")
        if self.role_router is None and not self.agents:
            raise ValueError("Crew must have at least one agent (or a role_router).")
        if isinstance(self.process, str):
            self.process = Process(self.process)
        if self.memory_config:
            self._apply_memory_config(self.memory_config)

    def _apply_memory_config(self, cfg: dict[str, Any]) -> None:
        """Attach a shared memory backend to all agents from memory_config."""
        provider = cfg.get("provider", "sqlite")
        options: dict[str, Any] = cfg.get("config", {})
        try:
            if provider == "in_memory":
                from meshflow.intelligence.memory_backends import InMemoryBackend
                backend = InMemoryBackend()
            elif provider == "file":
                from meshflow.intelligence.memory_backends import FileMemoryBackend
                backend = FileMemoryBackend(options.get("directory", "crew_memory"))
            elif provider == "redis":
                from meshflow.intelligence.memory_backends import RedisMemoryBackend
                backend = RedisMemoryBackend(
                    options.get("url", "redis://localhost:6379/0"),
                    ttl=options.get("ttl"),
                    prefix=options.get("prefix", "crew:memory:"),
                )
            else:
                from meshflow.intelligence.memory_backends import SQLiteMemoryBackend
                backend = SQLiteMemoryBackend(options.get("path", "crew_memory.db"))
            for agent in self.agents:
                if hasattr(agent, "memory_backend"):
                    agent.memory_backend = backend
                    agent.memory = True
        except Exception:
            pass  # memory_config errors are non-fatal

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
        prev_output: TaskOutput | None = None
        for i, task in enumerate(self.tasks):
            await self._resolve_agent_for_task(task)

            # Condition gate — skip if condition(previous_output) is falsy
            if task.condition is not None:
                try:
                    should_run = bool(task.condition(prev_output))
                except Exception:
                    should_run = True
                if not should_run:
                    if self.verbose:
                        print(f"[Crew] Task {i+1}/{len(self.tasks)} skipped (condition=False): "
                              f"{task.description[:60]}")
                    continue

            if self.verbose:
                agent_name = getattr(task.agent, "name", "?") if task.agent else "?"
                print(f"[Crew] Task {i+1}/{len(self.tasks)}: {task.description[:60]}  → {agent_name}")

            # Auto-wire context: inject all previous tasks if task.context is not set
            if task.context is None and outputs:
                task.context = list(self.tasks[:i])

            out = await task.run(inputs)
            prev_output = out
            outputs.append(out)

            if self.verbose:
                print(f"[Crew]   ✓ {len(out.raw)} chars, {out.tokens} tokens")

            # Early exit when confidence threshold is met
            if self.stop_on_confidence is not None:
                confidence = _parse_confidence(out.raw)
                if confidence >= self.stop_on_confidence:
                    if self.verbose:
                        print(f"[Crew] stop_on_confidence={self.stop_on_confidence} met "
                              f"(confidence={confidence:.2f}) — skipping "
                              f"{len(self.tasks) - i - 1} remaining task(s)")
                    break

        return self._aggregate(outputs)

    async def _run_parallel(self, inputs: dict[str, Any] | None) -> CrewOutput:
        """All tasks run concurrently — no inter-task context injection."""
        if self.verbose:
            print(f"[Crew] Running {len(self.tasks)} tasks in parallel")

        # Deduplicate shared context across task prompts before running
        if self.context_dedup and len(self.tasks) >= 2:
            raw_prompts = [t._build_prompt(inputs) for t in self.tasks]
            deduped = _dedup_context(raw_prompts)
            saved = sum(len(r) - len(d) for r, d in zip(raw_prompts, deduped))
            if self.verbose and saved > 0:
                print(f"[Crew] context_dedup removed ~{saved} chars of duplicate context")
            # Patch each task's description to use the deduped prompt
            for task, deduped_prompt in zip(self.tasks, deduped):
                task._deduped_prompt = deduped_prompt  # type: ignore[attr-defined]

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
        import yaml  # type: ignore[import-untyped]
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

    # ── Train & Replay ────────────────────────────────────────────────────────

    async def train(
        self,
        n_iterations: int,
        filename: str,
        inputs: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run the crew *n_iterations* times and save training data to *filename*.

        Each iteration produces a ``CrewOutput``.  The training record stores the
        inputs, the final output, per-task outputs, and an auto-computed quality
        score (confidence extracted from the final output).

        Training data is written as newline-delimited JSON (one record per line),
        suitable for fine-tuning pipelines.

        Parameters
        ----------
        n_iterations:
            Number of complete crew runs to collect.
        filename:
            Path to write training data (appended if exists).
        inputs:
            Fixed inputs passed to every run.
        """
        import json as _json
        records: list[dict[str, Any]] = []
        for i in range(n_iterations):
            if self.verbose:
                print(f"[Crew.train] Iteration {i+1}/{n_iterations}")
            result = await self.kickoff(inputs)
            record: dict[str, Any] = {
                "iteration": i + 1,
                "inputs": inputs or {},
                "output": result.raw,
                "task_outputs": [
                    {"description": t.task_description, "output": t.raw}
                    for t in result.tasks_output
                ],
                "quality_score": _parse_confidence(result.raw),
                "tokens": result.total_tokens,
                "cost_usd": result.total_cost_usd,
            }
            records.append(record)
        with open(filename, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(_json.dumps(rec) + "\n")
        if self.verbose:
            print(f"[Crew.train] Saved {len(records)} records to {filename!r}")
        return records

    async def replay(
        self,
        task_id: int,
        inputs: dict[str, Any] | None = None,
    ) -> "CrewOutput":
        """Replay crew execution starting from *task_id* (0-based index).

        Tasks before *task_id* are treated as already-complete: their outputs
        are set to a placeholder so downstream context injection still works.
        Tasks from *task_id* onward are re-executed.

        Parameters
        ----------
        task_id:
            0-based index of the task to replay from.
        inputs:
            Inputs for the re-run segment.
        """
        if task_id < 0 or task_id >= len(self.tasks):
            raise ValueError(
                f"task_id {task_id} is out of range — crew has {len(self.tasks)} tasks "
                f"(valid: 0–{len(self.tasks)-1})"
            )

        # Inject placeholder outputs for tasks that are being skipped
        for i, task in enumerate(self.tasks[:task_id]):
            if task.output is None:
                task.output = TaskOutput(
                    raw=f"[replayed — skipped task {i}]",
                    task_description=task.description,
                    agent_name=getattr(getattr(task, "agent", None), "name", ""),
                )

        # Run only tasks from task_id onward
        replay_crew = type(self)(
            agents=self.agents,
            tasks=self.tasks[task_id:],
            process=self.process,
            manager_llm=self.manager_llm,
            verbose=self.verbose,
            policy=self.policy,
        )
        return await replay_crew.kickoff(inputs)

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
