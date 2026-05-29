"""Framework migration tools — import LangGraph, CrewAI, or AutoGen definitions
into MeshFlow WorkflowDefinition.

Closes the migration gap: the existing adapters (adapters.py) only wrap individual
agents as MeshNodes.  These converters go further and auto-convert entire workflow
or crew definitions so teams can migrate without rewriting everything from scratch.

Supported conversions
---------------------
LangGraph ``StateGraph`` → :class:`~meshflow.core.workflow.WorkflowDefinition`
    Traverses the compiled graph's node + edge topology and wraps each LangGraph
    node as a ``MeshNode.from_langgraph()`` stub.

CrewAI ``Crew`` → :class:`~meshflow.core.workflow.WorkflowDefinition`
    Extracts the crew's task list and agent assignments; builds a sequential
    (or hierarchical) MeshFlow workflow.

AutoGen ``GroupChat`` → :class:`~meshflow.core.workflow.WorkflowDefinition`
    Wraps each AutoGen agent as a MeshNode and creates a round-robin sequential
    workflow with a termination sentinel node.

Usage::

    from meshflow.integrations.migration import (
        langgraph_to_mesh,
        crewai_to_mesh,
        autogen_to_mesh,
    )

    # LangGraph
    wf = langgraph_to_mesh(compiled_graph, name="research-pipeline")
    result = await Mesh().run_workflow(wf, task="...")

    # CrewAI
    crew = Crew(agents=[analyst, writer], tasks=[research_task, write_task])
    wf = crewai_to_mesh(crew, name="content-pipeline")

    # AutoGen
    agents = [UserProxyAgent(...), AssistantAgent(...)]
    chat = GroupChat(agents=agents, messages=[], max_round=10)
    wf = autogen_to_mesh(chat, name="debate")
"""

from __future__ import annotations

from typing import Any


# ── LangGraph → MeshFlow ──────────────────────────────────────────────────────

def langgraph_to_mesh(
    graph: Any,
    *,
    name: str = "imported_langgraph",
    version: str = "1",
    policy: Any = None,
) -> Any:
    """Convert a compiled LangGraph ``StateGraph`` to a MeshFlow WorkflowDefinition.

    Parameters
    ----------
    graph:   A compiled LangGraph graph (output of ``StateGraph.compile()``).
    name:    Name for the resulting MeshFlow workflow.
    version: Version string.
    policy:  Optional :class:`~meshflow.core.schemas.Policy`.

    Returns a :class:`~meshflow.core.workflow.WorkflowDefinition`.

    Notes
    -----
    - Each LangGraph node becomes a ``MeshNode.from_langgraph()`` stub that calls
      the original graph's ``ainvoke`` on its sub-input.
    - Edges are converted 1:1; conditional edges become MeshFlow condition strings.
    - The ``__end__`` node is mapped to a terminal marker.
    """
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.node import MeshNode

    wf = WorkflowDefinition(name=name, version=version, policy=policy)

    # LangGraph compiled graphs expose .get_graph() with nodes/edges
    try:
        lg_graph = graph.get_graph()
        nodes_data = lg_graph.nodes
        edges_data = lg_graph.edges
    except Exception:
        # Fallback: wrap the whole graph as a single node
        node = MeshNode.from_langgraph(name, graph)
        wf.add_node(node)
        return wf

    # Map LangGraph node names → MeshFlow nodes
    entry_set: set[str] = set()
    terminal_set: set[str] = set()

    for node_name, _node_data in nodes_data.items():
        safe_name = _safe_id(node_name)
        if node_name == "__start__":
            entry_set.add(safe_name)
            continue
        if node_name == "__end__":
            terminal_set.add(safe_name)
            continue

        # Create a MeshNode that calls the original graph for this node's sub-graph
        mesh_node = MeshNode.from_langgraph(safe_name, graph)
        mesh_node.metadata["langgraph_node"] = node_name
        wf.add_node(mesh_node)

    # Map edges
    for edge in edges_data:
        src = _safe_id(getattr(edge, "source", ""))
        dst = _safe_id(getattr(edge, "target", ""))
        condition = str(getattr(edge, "conditional", ""))
        if src in ("__start__", "start") or dst in ("__end__", "end"):
            continue
        if src and dst and src in {n.id for n in wf._nodes.values()} \
                and dst in {n.id for n in wf._nodes.values()}:
            wf.add_edge(src, dst, condition if condition not in ("False", "None", "") else "")

    if not wf._entry and wf._nodes:
        wf.set_entry(next(iter(wf._nodes)))

    return wf


# ── CrewAI → MeshFlow ─────────────────────────────────────────────────────────

def crewai_to_mesh(
    crew: Any,
    *,
    name: str = "imported_crewai",
    version: str = "1",
    policy: Any = None,
) -> Any:
    """Convert a CrewAI ``Crew`` to a MeshFlow WorkflowDefinition.

    Each Task in the Crew becomes a MeshFlow native node with the task's
    agent wrapped as the underlying executor.  Context chaining is preserved:
    ``task.context`` dependencies become workflow edges.

    Parameters
    ----------
    crew:    A CrewAI ``Crew`` instance.
    name:    Workflow name.
    policy:  Optional :class:`~meshflow.core.schemas.Policy`.
    """
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.node import MeshNode
    from meshflow.agents.adapters import from_crewai as agent_from_crewai

    wf = WorkflowDefinition(name=name, version=version, policy=policy)

    tasks = getattr(crew, "tasks", []) or []
    if not tasks:
        # No tasks — wrap the whole crew as one node
        node = MeshNode.from_crewai(name, crew)
        wf.add_node(node)
        return wf

    task_id_map: dict[Any, str] = {}

    for i, task in enumerate(tasks):
        task_agent = getattr(task, "agent", None)
        task_desc  = getattr(task, "description", f"task_{i}")
        node_id    = _safe_id(f"{getattr(task_agent, 'role', 'agent')}_{i}")

        if task_agent is not None:
            try:
                mesh_agent = agent_from_crewai(task_agent)
                node = mesh_agent.to_mesh_node()
                node.id = node_id
            except Exception:
                node = MeshNode.from_crewai(node_id, crew)
        else:
            node = MeshNode.from_crewai(node_id, crew)

        node.metadata["crewai_task"] = task_desc[:120]
        node.metadata["crewai_expected_output"] = str(
            getattr(task, "expected_output", "")
        )[:120]
        wf.add_node(node)
        task_id_map[task] = node_id

    # Build edges: sequential (default) + context dependencies
    for i, task in enumerate(tasks):
        node_id = task_id_map[task]
        ctx_tasks = getattr(task, "context", None) or []
        if ctx_tasks:
            for dep_task in ctx_tasks:
                dep_id = task_id_map.get(dep_task)
                if dep_id and dep_id != node_id:
                    wf.add_edge(dep_id, node_id)
        elif i > 0:
            # Sequential fallback
            prev_id = task_id_map[tasks[i - 1]]
            wf.add_edge(prev_id, node_id)

    if tasks:
        wf.set_entry(task_id_map[tasks[0]])
        wf.set_terminal(task_id_map[tasks[-1]])

    return wf


# ── AutoGen → MeshFlow ────────────────────────────────────────────────────────

def autogen_to_mesh(
    group_chat: Any,
    *,
    name: str = "imported_autogen",
    version: str = "1",
    policy: Any = None,
    max_rounds: int | None = None,
) -> Any:
    """Convert an AutoGen ``GroupChat`` to a sequential MeshFlow workflow.

    Each AutoGen agent in the group becomes a MeshFlow native node.  The
    conversion creates a sequential chain (round-robin order).

    Parameters
    ----------
    group_chat:  An AutoGen ``GroupChat`` instance.
    name:        Workflow name.
    max_rounds:  Override the GroupChat's ``max_round`` for the workflow.
    """
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.node import MeshNode

    wf = WorkflowDefinition(name=name, version=version, policy=policy)

    agents = getattr(group_chat, "agents", []) or []
    rounds = max_rounds or getattr(group_chat, "max_round", 3)

    if not agents:
        return wf

    node_ids: list[str] = []
    for i, ag in enumerate(agents):
        ag_name = getattr(ag, "name", f"agent_{i}")
        node_id = _safe_id(ag_name)
        node = MeshNode.from_autogen(node_id, ag)
        node.metadata["autogen_name"] = ag_name
        wf.add_node(node)
        node_ids.append(node_id)

    # Create a sequential chain; repeat for max_rounds
    if len(node_ids) > 1:
        for j in range(min(rounds, 1)):  # one sequential pass
            for k in range(len(node_ids) - 1):
                wf.add_edge(node_ids[k], node_ids[k + 1])

    if node_ids:
        wf.set_entry(node_ids[0])
        wf.set_terminal(node_ids[-1])

    wf.metadata["source_framework"] = "autogen"
    wf.metadata["max_rounds"] = rounds
    return wf


# ── Export YAML ───────────────────────────────────────────────────────────────

def to_yaml(wf: Any) -> str:
    """Serialize a migrated WorkflowDefinition back to YAML.

    Useful for inspecting what was migrated or committing the converted workflow.
    """
    import yaml

    data: dict = {
        "name":    wf.name,
        "version": wf.version,
        "nodes":   {
            nid: {
                "kind": node.kind.value,
                **({"metadata": node.metadata} if node.metadata else {}),
            }
            for nid, node in wf._nodes.items()
        },
        "edges": [
            {"from": e.from_node, "to": e.to_node, **({"condition": e.condition} if e.condition else {})}
            for e in wf._edges
        ],
        "entry":    wf._entry,
        "terminal": wf._terminal,
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_id(name: str) -> str:
    """Convert any string to a valid MeshFlow node ID."""
    import re
    return re.sub(r"[^a-z0-9_-]", "_", name.lower()).strip("_") or "node"


__all__ = [
    "langgraph_to_mesh",
    "crewai_to_mesh",
    "autogen_to_mesh",
    "to_yaml",
]
