"""Low-friction governance wrapper for existing agent workflows."""

from __future__ import annotations

from typing import Any

from meshflow.core.mesh import Mesh
from meshflow.core.node import MeshNode, NodeKind
from meshflow.core.schemas import Policy, policy_for_mode
from meshflow.core.workflow import WorkflowDefinition


class GovernedApp:
    """Wrap an existing workflow or callable with MeshFlow governance."""

    def __init__(self, app: Any, policy: Policy | None = None) -> None:
        self._app = app
        self._policy = policy or policy_for_mode("standard")

    async def run(self, task: str, **kwargs: Any) -> Any:
        if isinstance(self._app, WorkflowDefinition):
            return await Mesh(policy=self._policy).run_workflow(self._app, task=task, **kwargs)

        if isinstance(self._app, MeshNode):
            workflow = (
                WorkflowDefinition("governed_node", policy=self._policy)
                .add_node(self._app)
                .set_terminal(self._app.id)
            )
            return await Mesh(policy=self._policy).run_workflow(workflow, task=task, **kwargs)

        node = _node_from_app(self._app)
        workflow = (
            WorkflowDefinition("governed_app", policy=self._policy)
            .add_node(node)
            .set_terminal(node.id)
        )
        return await Mesh(policy=self._policy).run_workflow(workflow, task=task, **kwargs)


def govern(app: Any, policy: Policy | str | None = None) -> GovernedApp:
    """Wrap an existing LangGraph/CrewAI/AutoGen/callable-style object.

    The wrapper chooses a best-effort adapter based on common framework methods.
    For precise production integrations, prefer explicit ``MeshNode.from_*``
    factories, but this is the near-zero-friction L0 on-ramp.
    """
    resolved_policy: Policy | None
    if isinstance(policy, str):
        resolved_policy = policy_for_mode(policy)
    else:
        resolved_policy = policy
    return GovernedApp(app, policy=resolved_policy)


def _node_from_app(app: Any) -> MeshNode:
    if callable(app):
        return MeshNode.from_callable("governed_callable", app)
    if hasattr(app, "ainvoke") or hasattr(app, "invoke"):
        return MeshNode.from_langgraph("governed_langgraph", app)
    if hasattr(app, "kickoff"):
        return MeshNode.from_crewai("governed_crewai", app)
    if hasattr(app, "generate_reply") or hasattr(app, "run"):
        return MeshNode.from_autogen("governed_autogen", app)
    return MeshNode(
        id="governed_object",
        kind=NodeKind.PYTHON,
        _runner=lambda inp: MeshNode.from_callable("stringify", lambda task, ctx: str(app)).run(
            inp
        ),
    )
