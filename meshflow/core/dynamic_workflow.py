"""DynamicWorkflow — agent/node spawning at runtime based on intermediate results.

Implements Claude Code's "dynamic workflow" pattern: a coordinator agent analyses
intermediate results and decides whether to spawn additional specialist agents to
handle sub-problems that weren't foreseeable at workflow design time.

Classes
-------
SpawnDecision        — coordinator's decision about whether/what to spawn
DynamicNode          — a workflow node whose output can trigger new spawns
DynamicCoordinator   — agent that orchestrates dynamic spawning decisions
DynamicWorkflow      — workflow that supports mid-run node spawning
DynamicWorkflowResult— extends WorkflowResult with spawn history

Usage::

    from meshflow import Agent
    from meshflow.core.dynamic_workflow import (
        DynamicWorkflow, DynamicCoordinator, DynamicNode,
    )
    from meshflow.agents.base import EchoProvider

    wf = DynamicWorkflow(max_dynamic_nodes=10, mode="sandbox")
    wf.add(Agent("researcher", provider=EchoProvider("found 3 sub-topics")))
    wf.set_coordinator(
        DynamicCoordinator(
            spawn_keywords={"sub-topic": "analyst", "error": "debugger"},
            mode="sandbox",
        )
    )
    result = wf.run("Research quantum computing.")
    print(result.spawn_history)    # list of dynamically added nodes
    print(result.total_spawns)     # integer count
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any


# ── SpawnDecision ─────────────────────────────────────────────────────────────

@dataclass
class SpawnDecision:
    """A coordinator's decision about whether to spawn new agents.

    Attributes
    ----------
    spawn:
        True when new agents should be spawned.
    agents:
        List of ``(name, role, task)`` tuples for agents to spawn.
    reason:
        Human-readable explanation.
    confidence:
        Coordinator's confidence in this decision (0–1).
    """
    spawn: bool
    agents: list[tuple[str, str, str]] = field(default_factory=list)
    reason: str = ""
    confidence: float = 1.0


# ── DynamicCoordinator ────────────────────────────────────────────────────────

class DynamicCoordinator:
    """Examines intermediate workflow outputs and decides what to spawn next.

    Parameters
    ----------
    spawn_keywords:
        Dict mapping keyword → role.  When a keyword appears in a node's
        output, a new agent of the mapped role is spawned for that sub-task.
        Example: ``{"error": "debugger", "sub-topic": "researcher"}``.
    spawn_patterns:
        Dict mapping regex pattern → role.  More flexible than keywords.
    max_spawns_per_node:
        Upper bound on spawns triggered by a single node output.
    provider:
        LLMProvider (for LLM-based spawn decisions in production).
    mode:
        ``"sandbox"`` uses keyword/pattern matching only (no LLM call).
    """

    def __init__(
        self,
        spawn_keywords: dict[str, str] | None = None,
        spawn_patterns: dict[str, str] | None = None,
        max_spawns_per_node: int = 3,
        provider: Any = None,
        mode: str = "production",
    ) -> None:
        self.spawn_keywords = spawn_keywords or {}
        self.spawn_patterns = {
            re.compile(pat, re.IGNORECASE): role
            for pat, role in (spawn_patterns or {}).items()
        }
        self.max_spawns_per_node = max_spawns_per_node
        self.provider = provider
        self.mode = mode

    def decide(self, node_name: str, output: str) -> SpawnDecision:
        """Decide whether to spawn agents based on *output* from *node_name*."""
        agents: list[tuple[str, str, str]] = []
        output_lower = output.lower()

        # Keyword matching
        for keyword, role in self.spawn_keywords.items():
            if keyword.lower() in output_lower and len(agents) < self.max_spawns_per_node:
                agent_name = f"dynamic_{role}_{len(agents) + 1}"
                sub_task = self._extract_subtask(output, keyword)
                agents.append((agent_name, role, sub_task))

        # Pattern matching (only if keywords didn't fill the quota)
        for pattern, role in self.spawn_patterns.items():
            if len(agents) >= self.max_spawns_per_node:
                break
            match = pattern.search(output)
            if match:
                agent_name = f"dynamic_{role}_{len(agents) + 1}"
                sub_task = match.group(0)
                agents.append((agent_name, role, sub_task))

        if not agents:
            return SpawnDecision(spawn=False, reason="no spawn triggers detected")

        return SpawnDecision(
            spawn=True,
            agents=agents,
            reason=f"detected {len(agents)} spawn trigger(s) in output of {node_name}",
        )

    def _extract_subtask(self, output: str, keyword: str) -> str:
        """Extract the sentence containing *keyword* as the sub-task."""
        for sentence in re.split(r"[.!?]\s+", output):
            if keyword.lower() in sentence.lower():
                return sentence.strip() or keyword
        return keyword


# ── SpawnRecord ───────────────────────────────────────────────────────────────

@dataclass
class SpawnRecord:
    """Records one dynamic spawn event."""
    parent_node: str
    agent_name: str
    role: str
    task: str
    output: str = ""
    triggered_by: str = ""


# ── DynamicWorkflowResult ─────────────────────────────────────────────────────

@dataclass
class DynamicWorkflowResult:
    """Result of a :class:`DynamicWorkflow` run.

    Attributes
    ----------
    output:
        Final aggregated output (all node outputs joined).
    completed:
        True when the workflow completed without errors.
    total_spawns:
        Number of agents dynamically spawned during the run.
    spawn_history:
        List of :class:`SpawnRecord` objects — one per dynamic spawn.
    node_outputs:
        Dict mapping node name → output string.
    total_tokens:
        Approximate total tokens across all nodes.
    total_cost_usd:
        Approximate total cost across all nodes.
    """
    output: str
    completed: bool = True
    total_spawns: int = 0
    spawn_history: list[SpawnRecord] = field(default_factory=list)
    node_outputs: dict[str, str] = field(default_factory=dict)
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    def __str__(self) -> str:
        return self.output


# ── DynamicWorkflow ───────────────────────────────────────────────────────────

class DynamicWorkflow:
    """Workflow that can spawn new agent nodes at runtime.

    The coordinator examines each node's output and decides whether to spawn
    additional specialist agents.  Spawned agents run in parallel (when
    ``parallel_spawns=True``) and their outputs are appended to the result.

    Parameters
    ----------
    max_dynamic_nodes:
        Upper bound on total dynamic spawns (prevents runaway recursion).
    parallel_spawns:
        Run spawned agents concurrently (default True).
    mode:
        ``"sandbox"`` for offline testing.
    """

    def __init__(
        self,
        max_dynamic_nodes: int = 20,
        parallel_spawns: bool = True,
        mode: str = "production",
    ) -> None:
        self.max_dynamic_nodes = max_dynamic_nodes
        self.parallel_spawns = parallel_spawns
        self.mode = mode
        self._agents: list[Any] = []
        self._coordinator: DynamicCoordinator | None = None

    def add(self, agent: Any) -> "DynamicWorkflow":
        """Add a base agent to the workflow."""
        self._agents.append(agent)
        return self

    def set_coordinator(self, coordinator: DynamicCoordinator) -> "DynamicWorkflow":
        """Set the coordinator that decides on dynamic spawns."""
        self._coordinator = coordinator
        return self

    def run(self, task: str) -> DynamicWorkflowResult:
        """Run the dynamic workflow synchronously."""
        from meshflow.integrations._utils import run_sync
        return run_sync(self.arun(task))

    async def arun(self, task: str) -> DynamicWorkflowResult:
        """Run the dynamic workflow asynchronously."""
        from meshflow.agents.builder import Agent
        from meshflow.core.workflow import Workflow

        node_outputs: dict[str, str] = {}
        spawn_history: list[SpawnRecord] = []
        total_tokens = 0
        total_cost = 0.0
        spawn_count = 0

        # Run the base workflow
        wf = Workflow(mode=self.mode)
        for agent in self._agents:
            wf.add(agent)

        loop = asyncio.get_event_loop()
        base_result = await loop.run_in_executor(None, wf.run, task)
        base_output = base_result.output or ""
        total_tokens += base_result.total_tokens
        total_cost += base_result.total_cost_usd

        # Record base node outputs
        for step in base_result.steps:
            node_name = getattr(step.record, "node_name", "base")
            out = getattr(step.record, "output", "") or ""
            node_outputs[node_name] = out

            # Coordinator: decide spawns based on this node's output
            if (
                self._coordinator is not None
                and spawn_count < self.max_dynamic_nodes
                and out
            ):
                decision = self._coordinator.decide(node_name, out)
                if decision.spawn:
                    spawn_results = await self._run_spawns(
                        decision.agents, task, spawn_count
                    )
                    for agent_name, role, subtask, spawn_out, stok, scost in spawn_results:
                        if spawn_count >= self.max_dynamic_nodes:
                            break
                        node_outputs[agent_name] = spawn_out
                        spawn_history.append(SpawnRecord(
                            parent_node=node_name,
                            agent_name=agent_name,
                            role=role,
                            task=subtask,
                            output=spawn_out,
                            triggered_by=decision.reason,
                        ))
                        total_tokens += stok
                        total_cost += scost
                        spawn_count += 1

        # Aggregate all outputs
        all_outputs = [base_output] + [r.output for r in spawn_history if r.output]
        final_output = "\n\n".join(o for o in all_outputs if o)

        return DynamicWorkflowResult(
            output=final_output,
            completed=True,
            total_spawns=spawn_count,
            spawn_history=spawn_history,
            node_outputs=node_outputs,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
        )

    async def _run_spawns(
        self,
        agents: list[tuple[str, str, str]],
        base_task: str,
        current_count: int,
    ) -> list[tuple[str, str, str, str, int, float]]:
        """Run spawned agents and return (name, role, task, output, tokens, cost)."""
        from meshflow.agents.builder import Agent
        from meshflow.core.workflow import Workflow

        async def _run_one(
            agent_name: str, role: str, subtask: str
        ) -> tuple[str, str, str, str, int, float]:
            a = Agent(name=agent_name, role=role, mode=self.mode)
            wf = Workflow(mode=self.mode)
            wf.add(a)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, wf.run, subtask or base_task)
            return (
                agent_name, role, subtask,
                result.output or "",
                result.total_tokens,
                result.total_cost_usd,
            )

        remaining = agents[: self.max_dynamic_nodes - current_count]
        if self.parallel_spawns:
            return list(await asyncio.gather(*[_run_one(*a) for a in remaining]))
        else:
            results = []
            for a in remaining:
                results.append(await _run_one(*a))
            return results
