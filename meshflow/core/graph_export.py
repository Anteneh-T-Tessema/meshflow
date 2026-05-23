"""Workflow graph export — Mermaid and DOT/Graphviz serialisers.

Works from two sources:
  1. A ``StateGraph`` instance (static definition, pre-run).
  2. A list of step records from the ledger (post-run trace).

The post-run path is the primary one used by the REST endpoint; it shows
the *actual* execution path (including skipped branches) rather than the
full static definition.
"""
from __future__ import annotations

from typing import Any


# ── From step records (post-run) ──────────────────────────────────────────────

def steps_to_mermaid(steps: list[dict[str, Any]], run_id: str = "") -> str:
    """Build a Mermaid flowchart from a ledger step sequence."""
    if not steps:
        return "flowchart LR\n    empty([No steps recorded])"

    lines = ["flowchart LR"]
    node_ids: list[str] = []
    node_labels: dict[str, str] = {}

    for i, step in enumerate(steps):
        nid = step.get("node_id", f"step_{i}")
        safe = _safe_id(nid, i)
        blocked = step.get("blocked", False)
        verdict = step.get("verdict", "commit")
        tokens = step.get("tokens_used", 0)
        cost = step.get("cost_usd", 0.0)
        shape_open, shape_close = ("[", "]") if not blocked else ("([", "])")
        label = f"{nid}\\ntokens={tokens} ${cost:.4f}"
        if blocked:
            label += "\\n⛔ blocked"
        node_labels[safe] = label
        node_ids.append(safe)
        lines.append(f'    {safe}{shape_open}"{label}"{shape_close}')

    # Edges: sequential execution order
    for i in range(len(node_ids) - 1):
        lines.append(f"    {node_ids[i]} --> {node_ids[i+1]}")

    # Style blocked nodes red
    for i, step in enumerate(steps):
        if step.get("blocked"):
            safe = _safe_id(step.get("node_id", f"step_{i}"), i)
            lines.append(f"    style {safe} fill:#ff6b6b,color:#fff")

    if run_id:
        lines.insert(1, f'    subgraph run["{run_id[:24]}"]')
        lines.append("    end")

    return "\n".join(lines)


def steps_to_dot(steps: list[dict[str, Any]], run_id: str = "") -> str:
    """Build a Graphviz DOT graph from a ledger step sequence."""
    graph_name = f'"{run_id[:24]}"' if run_id else "MeshFlow"
    lines = [f"digraph {graph_name} {{", "    rankdir=LR;", '    node [shape=box fontname="Helvetica"];']

    node_ids: list[str] = []
    for i, step in enumerate(steps):
        nid = step.get("node_id", f"step_{i}")
        safe = _safe_id(nid, i)
        blocked = step.get("blocked", False)
        tokens = step.get("tokens_used", 0)
        cost = step.get("cost_usd", 0.0)
        label = f"{nid}\\ntokens={tokens} ${cost:.4f}"
        color = 'fillcolor="#ff6b6b" fontcolor=white style=filled' if blocked else 'style=filled fillcolor="#d4edda"'
        lines.append(f'    {safe} [label="{label}" {color}];')
        node_ids.append(safe)

    for i in range(len(node_ids) - 1):
        lines.append(f"    {node_ids[i]} -> {node_ids[i+1]};")

    lines.append("}")
    return "\n".join(lines)


# ── From a StateGraph definition (pre-run) ────────────────────────────────────

def graph_to_mermaid(graph: Any) -> str:
    """Render a ``StateGraph`` as a Mermaid flowchart (static definition)."""
    lines = ["flowchart LR"]
    nodes = getattr(graph, "_nodes", {})
    edges = getattr(graph, "_edges", {})
    entry = getattr(graph, "_entry", "")
    terminals = getattr(graph, "_terminals", set())

    for nid in nodes:
        safe = _safe_id(nid, 0)
        if nid == entry:
            lines.append(f'    {safe}(("{nid}"))')
        elif nid in terminals:
            lines.append(f'    {safe}(["{nid}"])')
        else:
            lines.append(f'    {safe}["{nid}"]')

    for src, edge_list in edges.items():
        for edge in edge_list:
            tgt = edge.target
            label = " -- cond --> " if edge.condition else " --> "
            lines.append(f"    {_safe_id(src, 0)}{label}{_safe_id(tgt, 0)}")

    return "\n".join(lines)


def graph_to_dot(graph: Any) -> str:
    """Render a ``StateGraph`` as a Graphviz DOT graph."""
    nodes = getattr(graph, "_nodes", {})
    edges = getattr(graph, "_edges", {})
    entry = getattr(graph, "_entry", "")
    terminals = getattr(graph, "_terminals", set())

    lines = ['digraph MeshFlow {', '    rankdir=LR;', '    node [shape=box fontname="Helvetica"];']
    for nid in nodes:
        safe = _safe_id(nid, 0)
        if nid == entry:
            color = 'fillcolor="#cce5ff" style=filled'
        elif nid in terminals:
            color = 'fillcolor="#d4edda" style=filled'
        else:
            color = ""
        lines.append(f'    {safe} [label="{nid}" {color}];')

    for src, edge_list in edges.items():
        for edge in edge_list:
            tgt = edge.target
            style = ' [style=dashed label="cond"]' if edge.condition else ""
            lines.append(f"    {_safe_id(src, 0)} -> {_safe_id(tgt, 0)}{style};")

    lines.append("}")
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_id(name: str, idx: int) -> str:
    """Return a Mermaid/DOT-safe identifier."""
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    if not safe or safe[0].isdigit():
        safe = f"n{idx}_{safe}"
    return safe
