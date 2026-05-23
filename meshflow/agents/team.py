"""Team — group agents into a governed collaboration without writing a workflow graph.

Usage:
    from meshflow import Agent, Team

    planner    = Agent(name="planner",    role="planner")
    researcher = Agent(name="researcher", role="researcher")
    executor   = Agent(name="executor",   role="executor")
    critic     = Agent(name="critic",     role="critic")

    team = Team(
        name="dev_team",
        agents=[planner, researcher, executor, critic],
        pattern="sequential",   # or "parallel", "hierarchical", "supervised"
        policy="standard",
    )
    result = await team.run("Build a rate-limiter in Python")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from meshflow.core.schemas import Policy, policy_for_mode
from meshflow.core.workflow import WorkflowDefinition, WorkflowResult


TeamPattern = Literal["sequential", "parallel", "hierarchical", "supervised", "reflective"]


@dataclass
class Team:
    """A group of Agent objects that collaborate under one policy.

    Patterns
    --------
    sequential   Each agent runs in the order given; output feeds the next.
    parallel     All agents run concurrently; results are merged.
    hierarchical First agent is the planner/orchestrator; it drives the rest sequentially.
    supervised   Like sequential but the last agent is always a Critic that can veto.
    """

    name: str
    agents: list[Any]  # list[Agent]
    pattern: TeamPattern = "sequential"
    policy: Policy | str | None = None
    budget_usd: float = 5.0

    def __post_init__(self) -> None:
        if not self.agents:
            raise ValueError("Team must have at least one agent.")
        if isinstance(self.policy, str):
            self.policy = policy_for_mode(self.policy, budget_usd=self.budget_usd)
        if self.policy is None:
            self.policy = policy_for_mode("standard", budget_usd=self.budget_usd)

    @property
    def _policy(self) -> Policy:
        assert isinstance(self.policy, Policy)
        return self.policy

    async def run(self, task: str, context: dict[str, Any] | None = None) -> WorkflowResult:
        """Run the team on a task and return a WorkflowResult."""
        from meshflow.core.mesh import Mesh

        workflow = self._build_workflow()
        return await Mesh(policy=self._policy).run_workflow(workflow, task=task, **(context or {}))

    def _build_workflow(self) -> WorkflowDefinition:
        nodes = [agent.to_mesh_node() for agent in self.agents]

        if self.pattern == "parallel":
            return self._parallel(nodes)
        if self.pattern == "hierarchical":
            return self._hierarchical(nodes)
        if self.pattern == "supervised":
            return self._supervised(nodes)
        if self.pattern == "reflective":
            return self._reflective(nodes)
        return self._sequential(nodes)

    def _sequential(self, nodes: list[Any]) -> WorkflowDefinition:
        wf = WorkflowDefinition(self.name, policy=self._policy)
        for node in nodes:
            wf.add_node(node)
        for i in range(len(nodes) - 1):
            wf.add_edge(nodes[i].id, nodes[i + 1].id)
        wf.set_terminal(nodes[-1].id)
        return wf

    def _parallel(self, nodes: list[Any]) -> WorkflowDefinition:
        """Fan-out / fan-in: all agents run concurrently, last agent synthesises.

        With ≥3 agents: the first node fans out to every intermediate node, all
        intermediate nodes feed into the synthesizer (last node).  WorkflowDefinition
        already uses asyncio.gather for nodes that are ready simultaneously, so
        true concurrent execution is automatic.

        With <3 agents: falls back to sequential (no meaningful fan-out possible).
        """
        if len(nodes) < 3:
            return self._sequential(nodes)

        branches = nodes[:-1]  # first through second-to-last
        synthesizer = nodes[-1]  # final synthesizer

        wf = WorkflowDefinition(self.name, policy=self._policy)
        for node in nodes:
            wf.add_node(node)
        # Fan-out: entry node drives all remaining branches except itself
        for branch in branches[1:]:
            wf.add_edge(branches[0].id, branch.id)
        # Fan-in: every branch converges into the synthesizer
        for branch in branches:
            wf.add_edge(branch.id, synthesizer.id)
        wf.set_terminal(synthesizer.id)
        return wf

    def _hierarchical(self, nodes: list[Any]) -> WorkflowDefinition:
        """First agent orchestrates; remaining agents run sequentially after it."""
        if len(nodes) == 1:
            return self._sequential(nodes)
        orchestrator = nodes[0]
        rest = nodes[1:]
        wf = WorkflowDefinition(self.name, policy=self._policy)
        all_nodes = [orchestrator] + rest
        for node in all_nodes:
            wf.add_node(node)
        wf.add_edge(orchestrator.id, rest[0].id)
        for i in range(len(rest) - 1):
            wf.add_edge(rest[i].id, rest[i + 1].id)
        wf.set_terminal(rest[-1].id)
        return wf

    def _supervised(self, nodes: list[Any]) -> WorkflowDefinition:
        """Sequential, but last agent is a supervisor/critic that always runs last."""
        if len(nodes) == 1:
            return self._sequential(nodes)
        workers = nodes[:-1]
        supervisor = nodes[-1]
        wf = WorkflowDefinition(self.name, policy=self._policy)
        for node in nodes:
            wf.add_node(node)
        for i in range(len(workers) - 1):
            wf.add_edge(workers[i].id, workers[i + 1].id)
        wf.add_edge(workers[-1].id, supervisor.id)
        wf.set_terminal(supervisor.id)
        return wf

    def _reflective(self, nodes: list[Any]) -> WorkflowDefinition:
        """Generate → critique loop.

        Requires exactly 2 agents: generator (first) and critic (last).
        The critic loops back to the generator until confidence >= 0.9
        or a maximum of 5 iterations.
        """
        if len(nodes) < 2:
            return self._sequential(nodes)
        generator = nodes[0]
        critic = nodes[-1]
        wf = WorkflowDefinition(self.name, policy=self._policy)
        for node in nodes:
            wf.add_node(node)
        # Forward: generator → intermediate nodes → critic
        for i in range(len(nodes) - 1):
            wf.add_edge(nodes[i].id, nodes[i + 1].id)
        # Back-edge: critic → generator when confidence is still low
        wf.add_loop_edge(critic.id, generator.id, condition="confidence < 0.9", max_iterations=5)
        wf.set_terminal(critic.id)
        return wf
