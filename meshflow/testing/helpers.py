"""Test helper factories for WorkflowDefinition, StepRuntime, and agents.

Usage::

    from meshflow.testing import fake_agent, make_workflow, make_runtime

    agent = fake_agent("researcher", response="Research complete.")
    wf = make_workflow(nodes={"step": agent}, edges=[])
    runtime = make_runtime(run_id="test-run")

    result = await wf.run(task="Do research", runtime=runtime)
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any


def fake_agent(
    name: str = "agent",
    response: str = "mock result",
    tokens: int = 10,
    cost: float = 0.001,
    confidence: float = 0.9,
) -> Any:
    """Create a minimal Agent-like object whose ``run()`` returns a fixed dict.

    The returned object also has a ``.to_mesh_node()`` method so it can be
    registered in a WorkflowDefinition directly.

    Usage::

        agent = fake_agent("researcher", response="Research done. CONFIDENCE:0.85")
        result = await agent.run("some task")
        assert result["result"] == "Research done. CONFIDENCE:0.85"
    """

    class _FakeAgent:
        def __init__(self) -> None:
            self.name = name
            self.role = "executor"
            self.model = "fake-model"
            self.system_prompt = ""
            self._call_count = 0
            self._call_history: list[tuple[str, dict]] = []

        async def run(self, task: str, context: dict | None = None) -> dict[str, Any]:
            self._call_count += 1
            self._call_history.append((task, context or {}))
            return {
                "result":            response,
                "agent_name":        name,
                "role":              "executor",
                "tokens":            tokens,
                "cost_usd":          cost,
                "stated_confidence": confidence,
                "blocked":           False,
            }

        async def step(self, task: str, context: dict | None = None) -> dict[str, Any]:
            return await self.run(task, context)

        def to_mesh_node(self) -> Any:
            from meshflow.core.node import MeshNode
            return MeshNode.from_native(self.name, self)

        @property
        def call_count(self) -> int:
            return self._call_count

        @property
        def call_history(self) -> list[tuple[str, dict]]:
            return self._call_history

    return _FakeAgent()


def make_workflow(
    nodes: dict[str, Any],
    edges: list[tuple[str, str] | tuple[str, str, str]] | None = None,
    *,
    name: str = "test-wf",
    policy: Any = None,
) -> Any:
    """Build a WorkflowDefinition from a dict of {node_id: agent_or_node}.

    Parameters
    ----------
    nodes:  ``{node_id: agent_instance | MeshNode}``
    edges:  List of ``(from, to)`` or ``(from, to, condition)`` tuples.
    name:   Workflow name.
    policy: Optional Policy.  Defaults to dev policy.
    """
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.node import MeshNode
    from meshflow.core.schemas import policy_for_mode

    wf = WorkflowDefinition(name=name, policy=policy or policy_for_mode("dev"))

    for node_id, agent_or_node in nodes.items():
        if isinstance(agent_or_node, MeshNode):
            node = agent_or_node
            node.id = node_id
        else:
            # Assume it's an agent-like object
            try:
                node = agent_or_node.to_mesh_node()
                node.id = node_id
            except AttributeError:
                node = MeshNode.from_native(node_id, agent_or_node)
        wf.add_node(node)

    for edge in (edges or []):
        if len(edge) == 2:
            wf.add_edge(edge[0], edge[1])
        else:
            wf.add_edge(edge[0], edge[1], edge[2])

    return wf


def make_runtime(
    run_id: str = "",
    policy: Any = None,
    ledger: Any = None,
) -> Any:
    """Build a minimal StepRuntime for testing.

    By default uses an in-memory ledger and dev policy.

    Usage::

        runtime = make_runtime(run_id="test-abc")
        result  = await wf.run(task="...", runtime=runtime)
    """
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.schemas import policy_for_mode
    from meshflow.core.ledger import ReplayLedger

    pol = policy or policy_for_mode("dev")
    rid = run_id or f"test-{uuid.uuid4().hex[:8]}"
    led = ledger or ReplayLedger(":memory:")
    return StepRuntime(policy=pol, run_id=rid, ledger=led)


def make_step_record(
    run_id: str = "test-run",
    node_id: str = "step",
    output: str = "output",
    blocked: bool = False,
    cost_usd: float = 0.001,
    tokens_used: int = 10,
    uncertainty: float = 0.1,
) -> Any:
    """Build a minimal StepRecord for testing ledger-related code."""
    from meshflow.core.runtime import StepRecord

    return StepRecord(
        run_id=run_id,
        step_id=uuid.uuid4().hex[:8],
        node_id=node_id,
        node_kind="native",
        input_task="test task",
        output_content=output,
        verdict="block" if blocked else "commit",
        blocked=blocked,
        block_reason="test" if blocked else "",
        uncertainty=uncertainty,
        cost_usd=cost_usd,
        tokens_used=tokens_used,
        carbon_gco2=0.0,
        duration_ms=10.0,
        timestamp=datetime.datetime.now().isoformat(),
    )


__all__ = ["fake_agent", "make_workflow", "make_runtime", "make_step_record"]
