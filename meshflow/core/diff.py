"""Workflow diff — compare two WorkflowDefinition topologies.

Shows which nodes and edges were added, removed, or modified between two
workflow YAML files or WorkflowDefinition objects.

Usage::

    from meshflow.core.diff import workflow_diff

    result = workflow_diff("pipeline_v1.yaml", "pipeline_v2.yaml")
    print(result.summary())

    # Programmatic
    result = workflow_diff_objects(wf_old, wf_new)
    if result.has_changes:
        print(result.to_dict())

CLI::

    meshflow diff pipeline_v1.yaml pipeline_v2.yaml
    meshflow diff pipeline_v1.yaml pipeline_v2.yaml --json
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiffChange:
    """A single change between two workflow versions."""

    kind: str           # "node_added" | "node_removed" | "node_changed"
                        # "edge_added" | "edge_removed" | "edge_changed"
                        # "policy_changed" | "metadata_changed"
    target: str         # node_id / "edge:A→B" / "policy" / "metadata"
    detail: str         # human-readable description
    old_value: Any = None
    new_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind":      self.kind,
            "target":    self.target,
            "detail":    self.detail,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }

    def __str__(self) -> str:
        icon = {
            "node_added":     "+",
            "node_removed":   "-",
            "node_changed":   "~",
            "edge_added":     "+",
            "edge_removed":   "-",
            "edge_changed":   "~",
            "policy_changed": "~",
            "metadata_changed": "~",
        }.get(self.kind, "?")
        return f"  {icon}  [{self.kind}] {self.target}: {self.detail}"


@dataclass
class DiffResult:
    """Aggregated diff between two workflows."""

    yaml_a: str
    yaml_b: str
    changes: list[DiffChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    @property
    def nodes_added(self) -> list[str]:
        return [c.target for c in self.changes if c.kind == "node_added"]

    @property
    def nodes_removed(self) -> list[str]:
        return [c.target for c in self.changes if c.kind == "node_removed"]

    def summary(self) -> str:
        if not self.has_changes:
            return f"  [=] No differences between {self.yaml_a!r} and {self.yaml_b!r}"
        lines = [f"\n  Diff: {self.yaml_a!r}  →  {self.yaml_b!r}"]
        lines.append(f"  Changes: {len(self.changes)}")
        lines.append("")
        for ch in self.changes:
            lines.append(str(ch))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "yaml_a":      self.yaml_a,
            "yaml_b":      self.yaml_b,
            "has_changes": self.has_changes,
            "changes":     [c.to_dict() for c in self.changes],
        }


# ── Public API ────────────────────────────────────────────────────────────────

def workflow_diff(path_a: str, path_b: str) -> DiffResult:
    """Diff two workflow YAML files.  Returns a :class:`DiffResult`."""
    import yaml
    from pathlib import Path

    def _load(p: str) -> dict[str, Any]:
        if not Path(p).exists():
            raise FileNotFoundError(f"Workflow file not found: {p}")
        with open(p) as fh:
            return yaml.safe_load(fh) or {}

    data_a = _load(path_a)
    data_b = _load(path_b)
    result = _diff_dicts(data_a, data_b)
    result.yaml_a = path_a
    result.yaml_b = path_b
    return result


def workflow_diff_objects(wf_a: Any, wf_b: Any) -> DiffResult:
    """Diff two :class:`~meshflow.core.workflow.WorkflowDefinition` objects."""
    data_a = _wf_to_dict(wf_a)
    data_b = _wf_to_dict(wf_b)
    result = _diff_dicts(data_a, data_b)
    result.yaml_a = getattr(wf_a, "name", "wf_a")
    result.yaml_b = getattr(wf_b, "name", "wf_b")
    return result


# ── Internal ──────────────────────────────────────────────────────────────────

def _wf_to_dict(wf: Any) -> dict[str, Any]:
    """Serialize a WorkflowDefinition to a comparable dict."""
    nodes: dict[str, Any] = {}
    for nid, node in wf._nodes.items():
        nodes[nid] = {"kind": node.kind.value, "risk": int(node.risk_profile)}

    edges: list[dict[str, Any]] = [
        {"from": e.from_node, "to": e.to_node, "condition": e.condition}
        for e in wf._edges
    ]
    return {
        "name":     wf.name,
        "version":  wf.version,
        "nodes":    nodes,
        "edges":    edges,
        "entry":    wf._entry,
        "terminal": wf._terminal,
        "policy":   {
            "budget_usd": wf.policy.budget_usd,
            "max_steps":  wf.policy.max_steps,
        },
    }


def _diff_dicts(data_a: dict[str, Any], data_b: dict[str, Any]) -> DiffResult:
    changes: list[DiffChange] = []

    nodes_a: dict[str, Any] = data_a.get("nodes", {})
    nodes_b: dict[str, Any] = data_b.get("nodes", {})

    # Nodes added
    for nid in set(nodes_b) - set(nodes_a):
        cfg = nodes_b[nid]
        kind = cfg.get("kind", "?") if isinstance(cfg, dict) else "?"
        changes.append(DiffChange(
            kind="node_added",
            target=nid,
            detail=f"New node added (kind={kind!r})",
            new_value=cfg,
        ))

    # Nodes removed
    for nid in set(nodes_a) - set(nodes_b):
        cfg = nodes_a[nid]
        kind = cfg.get("kind", "?") if isinstance(cfg, dict) else "?"
        changes.append(DiffChange(
            kind="node_removed",
            target=nid,
            detail=f"Node removed (was kind={kind!r})",
            old_value=cfg,
        ))

    # Nodes changed
    for nid in set(nodes_a) & set(nodes_b):
        cfg_a = nodes_a[nid] if isinstance(nodes_a[nid], dict) else {}
        cfg_b = nodes_b[nid] if isinstance(nodes_b[nid], dict) else {}
        diffs = _dict_diff(cfg_a, cfg_b)
        if diffs:
            changes.append(DiffChange(
                kind="node_changed",
                target=nid,
                detail=", ".join(diffs),
                old_value=cfg_a,
                new_value=cfg_b,
            ))

    # Edges comparison
    def _edge_key(e: Any) -> str:
        if isinstance(e, str):
            parts = [p.strip() for p in e.split("->")]
            return f"{parts[0]}→{parts[1] if len(parts) > 1 else '?'}"
        if isinstance(e, dict):
            cond = f"[{e['condition']}]" if e.get("condition") else ""
            return f"{e.get('from', '?')}→{e.get('to', '?')}{cond}"
        return str(e)

    edges_a_keys = {_edge_key(e): e for e in data_a.get("edges", [])}
    edges_b_keys = {_edge_key(e): e for e in data_b.get("edges", [])}

    for key in set(edges_b_keys) - set(edges_a_keys):
        changes.append(DiffChange(
            kind="edge_added", target=f"edge:{key}",
            detail=f"New edge added", new_value=edges_b_keys[key],
        ))
    for key in set(edges_a_keys) - set(edges_b_keys):
        changes.append(DiffChange(
            kind="edge_removed", target=f"edge:{key}",
            detail=f"Edge removed", old_value=edges_a_keys[key],
        ))

    # Policy changes
    pol_a = data_a.get("policy") or {}
    pol_b = data_b.get("policy") or {}
    if isinstance(pol_a, dict) and isinstance(pol_b, dict):
        pol_diffs = _dict_diff(pol_a, pol_b)
        if pol_diffs:
            changes.append(DiffChange(
                kind="policy_changed", target="policy",
                detail=", ".join(pol_diffs), old_value=pol_a, new_value=pol_b,
            ))

    # Entry / terminal
    if data_a.get("entry") != data_b.get("entry"):
        changes.append(DiffChange(
            kind="metadata_changed", target="entry",
            detail=f"{data_a.get('entry')!r} → {data_b.get('entry')!r}",
        ))

    return DiffResult(yaml_a="", yaml_b="", changes=changes)


def _dict_diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Return a list of human-readable changes between dicts a and b."""
    diffs: list[str] = []
    all_keys = set(a) | set(b)
    for k in sorted(all_keys):
        if k not in a:
            diffs.append(f"{k}=+{b[k]!r}")
        elif k not in b:
            diffs.append(f"{k}=-{a[k]!r}")
        elif a[k] != b[k]:
            diffs.append(f"{k}: {a[k]!r}→{b[k]!r}")
    return diffs


__all__ = ["workflow_diff", "workflow_diff_objects", "DiffResult", "DiffChange"]
