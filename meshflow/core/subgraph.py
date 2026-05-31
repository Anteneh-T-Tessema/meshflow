"""SubgraphNode — nest an entire WorkflowDefinition inside a single node.

This closes the LangGraph "subgraph / nested graph composition" gap.
A subgraph node wraps a ``WorkflowDefinition`` and, when executed,
runs the inner workflow to completion with its own child run-id and
full governance (guardian, budget, ledger) inherited from the parent
``StepRuntime``.

Usage — programmatic::

    from meshflow.core.subgraph import SubgraphNode
    from meshflow.core.workflow import WorkflowDefinition

    inner = WorkflowDefinition.from_yaml("inner_research.yaml", registry)

    outer = WorkflowDefinition(name="pipeline")
    outer.add_node(SubgraphNode.create("research_phase", inner))
    outer.add_node(MeshNode.from_native("writer", writer_agent))
    outer.add_edge("research_phase", "writer")

Usage — YAML::

    nodes:
      research_phase:
        kind: subgraph
        ref: inner_research.yaml
      writer:
        kind: native
        role: executor

    edges:
      - research_phase -> writer

The inner workflow's final output becomes this node's ``NodeOutput.content``.
Context from the parent is forwarded with a ``subgraph_`` prefix to avoid
key collisions. Inner workflow outputs are merged back into the parent
context under ``{node_id}_output`` as usual.

A ``max_depth`` guard (default 3) prevents infinite nesting.
"""

from __future__ import annotations

import uuid
from typing import Any, TYPE_CHECKING

from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.schemas import RiskTier

if TYPE_CHECKING:
    from meshflow.core.workflow import WorkflowDefinition


# Thread-local (actually asyncio-task-local) depth counter
_DEPTH_KEY = "_subgraph_depth"


class SubgraphNode:
    """Factory that creates a MeshNode wrapping an entire WorkflowDefinition.

    This is not itself a node — use ``SubgraphNode.create(...)`` to get a
    ``MeshNode`` with ``kind=SUBGRAPH``.
    """

    @staticmethod
    def create(
        node_id: str,
        inner_workflow: "WorkflowDefinition",
        *,
        max_depth: int = 3,
        risk: RiskTier = RiskTier.INTERNAL,
        capabilities: list[str] | None = None,
    ) -> MeshNode:
        """Create a MeshNode that runs *inner_workflow* as a nested subgraph.

        Parameters
        ----------
        node_id:
            Unique identifier for this node within the parent workflow.
        inner_workflow:
            The ``WorkflowDefinition`` to execute when this node runs.
        max_depth:
            Maximum nesting depth (prevents infinite recursion). Default 3.
        risk:
            Risk tier for the subgraph node. Default INTERNAL.
        capabilities:
            Capability tags. Default ``["subgraph", "workflow_composition"]``.
        """

        async def runner(inp: NodeInput) -> NodeOutput:
            depth = inp.context.get(_DEPTH_KEY, 0)
            if depth >= max_depth:
                return NodeOutput(
                    content=f"[subgraph:{node_id}] max depth {max_depth} exceeded",
                    confidence=0.0,
                    metadata={"error": "max_depth_exceeded", "depth": depth},
                )

            # Build a child runtime that inherits the parent's governance
            from meshflow.core.runtime import StepRuntime

            # Forward parent context with depth tracking
            child_ctx = {
                f"subgraph_{k}": v
                for k, v in inp.context.items()
                if not k.startswith("_")
            }
            child_ctx["task"] = inp.task
            child_ctx[_DEPTH_KEY] = depth + 1

            # Use parent's policy if available, else inner workflow's
            parent_policy = inp.context.get("_parent_policy")
            policy = parent_policy if parent_policy else inner_workflow.policy

            # Create child run_id as child of parent
            parent_run_id = inp.context.get("run_id", "")
            child_run_id = (
                f"{parent_run_id}/sub_{node_id}_{uuid.uuid4().hex[:6]}"
                if parent_run_id
                else f"sub_{node_id}_{uuid.uuid4().hex[:8]}"
            )

            # Build a lightweight StepRuntime for the inner workflow
            child_runtime = StepRuntime(
                policy=policy,
                run_id=child_run_id,
                guardian=inp.context.get("_guardian"),
                ledger=inp.context.get("_ledger"),
                budget=inp.context.get("_budget"),
            )

            result = await inner_workflow.run(
                task=inp.task,
                runtime=child_runtime,
                context=child_ctx,
            )

            return NodeOutput(
                content=result.output,
                structured={
                    "subgraph_run_id": child_run_id,
                    "subgraph_name": inner_workflow.name,
                    "subgraph_completed": result.completed,
                    "subgraph_steps": len(result.steps),
                    "subgraph_cost_usd": result.total_cost_usd,
                    "subgraph_tokens": result.total_tokens,
                },
                tokens_used=result.total_tokens,
                confidence=0.9 if result.completed else 0.3,
                metadata={
                    "inner_workflow": inner_workflow.name,
                    "depth": depth + 1,
                    "blocked_nodes": result.blocked_nodes,
                    "paused_nodes": result.paused_nodes,
                },
            )

        return MeshNode(
            id=node_id,
            kind=NodeKind.SUBGRAPH,
            risk_profile=risk,
            capabilities=capabilities or ["subgraph", "workflow_composition"],
            metadata={
                "inner_workflow": inner_workflow.name,
                "max_depth": max_depth,
            },
            _runner=runner,
        )


def subgraph_from_yaml(
    node_id: str,
    yaml_path: str,
    node_registry: dict[str, Any] | None = None,
    **kwargs: Any,
) -> MeshNode:
    """Convenience: load an inner workflow from YAML and wrap it as a subgraph node.

    Parameters
    ----------
    node_id:       Node identifier in the parent workflow.
    yaml_path:     Path to the inner workflow YAML.
    node_registry: Registry for resolving ``ref:`` fields in the inner YAML.
    **kwargs:      Forwarded to ``SubgraphNode.create()``.
    """
    from meshflow.core.workflow import WorkflowDefinition

    inner = WorkflowDefinition.from_yaml(yaml_path, node_registry)
    return SubgraphNode.create(node_id, inner, **kwargs)
