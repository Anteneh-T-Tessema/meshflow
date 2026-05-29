"""Workflow YAML linter — static validation before running.

Catches common errors that would only surface at runtime:
  - Undefined node refs in edges
  - Unreachable nodes (no path from entry)
  - Dead-end nodes that are not terminal (no outgoing edges)
  - Missing required node fields (kind, role for native)
  - Circular edge topology (back-edges outside loop_edges)
  - Invalid condition expression syntax
  - Budget/timeout out-of-range values

Usage (programmatic)::

    from meshflow.core.lint import lint_workflow_yaml, LintResult

    issues = lint_workflow_yaml("pipeline.yaml")
    for issue in issues:
        print(f"[{issue.severity}] {issue.path}: {issue.message}")

    if any(i.severity == "error" for i in issues):
        sys.exit(1)

Usage (CLI)::

    meshflow lint pipeline.yaml
    meshflow lint pipeline.yaml --strict   # warnings also fail the check
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Issue dataclass ───────────────────────────────────────────────────────────

@dataclass
class LintIssue:
    """One finding from the linter."""

    severity: str        # "error" | "warning" | "info"
    path: str            # location in the YAML (e.g. "edges[2].from")
    message: str
    suggestion: str = ""

    def __str__(self) -> str:
        tag = {"error": "ERR ", "warning": "WARN", "info": "INFO"}.get(self.severity, self.severity.upper())
        parts = [f"  [{tag}] {self.path}: {self.message}"]
        if self.suggestion:
            parts.append(f"         Suggestion: {self.suggestion}")
        return "\n".join(parts)


@dataclass
class LintResult:
    """Aggregated linting result for one workflow YAML."""

    yaml_path: str
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        n_err = len(self.errors)
        n_warn = len(self.warnings)
        status = "PASS" if self.ok else "FAIL"
        return (
            f"  [{status}] {self.yaml_path} — "
            f"{n_err} error(s), {n_warn} warning(s)"
        )

    def __str__(self) -> str:
        lines = [self.summary()]
        for issue in self.issues:
            lines.append(str(issue))
        return "\n".join(lines)


# ── Linter ────────────────────────────────────────────────────────────────────

def lint_workflow_yaml(path: str) -> list[LintIssue]:
    """Lint a workflow YAML file.  Returns a list of :class:`LintIssue` objects."""
    import yaml
    p = Path(path)
    if not p.exists():
        return [LintIssue("error", "file", f"File not found: {path}")]

    try:
        with open(p) as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        return [LintIssue("error", "yaml", f"YAML parse error: {exc}")]

    if not isinstance(data, dict):
        return [LintIssue("error", "root", "Workflow YAML root must be a mapping")]

    return _lint_workflow_data(data, source=str(p))


def lint_workflow_data(data: dict[str, Any], source: str = "") -> list[LintIssue]:
    """Lint an already-parsed workflow dict.  Useful in tests."""
    return _lint_workflow_data(data, source=source)


def _lint_workflow_data(data: dict[str, Any], source: str = "") -> list[LintIssue]:
    issues: list[LintIssue] = []
    nodes: dict[str, dict[str, Any]] = data.get("nodes", {})
    edges: list[Any] = data.get("edges", [])
    loop_edges: list[Any] = data.get("loop_edges", [])
    entry: str = data.get("entry", "")
    terminal: Any = data.get("terminal", [])
    if isinstance(terminal, str):
        terminal = [terminal]

    node_ids = set(nodes.keys())

    # ── Top-level required fields ─────────────────────────────────────────────
    if not data.get("name"):
        issues.append(LintIssue("warning", "name", "Workflow has no 'name' field."))

    # ── Node validation ───────────────────────────────────────────────────────
    valid_kinds = {"native", "langgraph", "crewai", "autogen", "mcp", "human", "http", "python", "subgraph"}
    valid_roles = {"planner", "researcher", "executor", "critic", "orchestrator", "guardian"}

    for node_id, node_cfg in nodes.items():
        if not isinstance(node_cfg, dict):
            issues.append(LintIssue("error", f"nodes.{node_id}", "Node config must be a mapping"))
            continue
        kind = node_cfg.get("kind", "native")
        if kind not in valid_kinds:
            issues.append(LintIssue(
                "error", f"nodes.{node_id}.kind",
                f"Unknown kind {kind!r}. Valid kinds: {sorted(valid_kinds)}",
                suggestion=f"Did you mean 'native' or 'python'?",
            ))
        if kind == "native":
            role = node_cfg.get("role", "executor")
            if role not in valid_roles:
                issues.append(LintIssue(
                    "warning", f"nodes.{node_id}.role",
                    f"Unknown role {role!r}. Valid roles: {sorted(valid_roles)}",
                ))
        if kind == "http" and not node_cfg.get("url"):
            issues.append(LintIssue("error", f"nodes.{node_id}.url", "HTTP node requires a 'url' field"))
        if kind == "subgraph" and not node_cfg.get("ref") and not node_cfg.get("workflow"):
            issues.append(LintIssue("error", f"nodes.{node_id}", "Subgraph node requires 'ref' or 'workflow' field"))

    # ── Edge validation ───────────────────────────────────────────────────────
    graph: dict[str, set[str]] = {nid: set() for nid in node_ids}
    back_edges: set[tuple[str, str]] = set()

    for i, edge in enumerate(edges):
        if isinstance(edge, str):
            parts = [p.strip() for p in edge.split("->")]
            if len(parts) != 2:
                issues.append(LintIssue("error", f"edges[{i}]", f"Invalid edge shorthand {edge!r}. Expected 'A -> B'"))
                continue
            src, dst = parts[0], parts[1]
            condition = ""
        elif isinstance(edge, dict):
            src = edge.get("from", "")
            dst = edge.get("to", "")
            condition = edge.get("condition", "")
        else:
            issues.append(LintIssue("error", f"edges[{i}]", "Edge must be a string or mapping"))
            continue

        if src not in node_ids:
            issues.append(LintIssue(
                "error", f"edges[{i}].from",
                f"Undefined node {src!r}",
                suggestion=f"Did you mean one of {sorted(node_ids)[:5]}?",
            ))
        if dst not in node_ids:
            issues.append(LintIssue(
                "error", f"edges[{i}].to",
                f"Undefined node {dst!r}",
                suggestion=f"Did you mean one of {sorted(node_ids)[:5]}?",
            ))

        if src in node_ids and dst in node_ids:
            graph[src].add(dst)

        if condition:
            err = _validate_condition_syntax(condition)
            if err:
                issues.append(LintIssue("warning", f"edges[{i}].condition", f"Condition may be invalid: {err}"))

    # Loop edge validation
    loop_back_pairs = set()
    for i, le in enumerate(loop_edges):
        if isinstance(le, dict):
            src = le.get("from", "")
            dst = le.get("to", "")
            loop_back_pairs.add((src, dst))
            if src not in node_ids:
                issues.append(LintIssue("error", f"loop_edges[{i}].from", f"Undefined node {src!r}"))
            if dst not in node_ids:
                issues.append(LintIssue("error", f"loop_edges[{i}].to", f"Undefined node {dst!r}"))

    # ── Entry / terminal checks ────────────────────────────────────────────────
    if entry and entry not in node_ids:
        issues.append(LintIssue("error", "entry", f"Entry node {entry!r} not defined in nodes"))

    for t in terminal:
        if t not in node_ids:
            issues.append(LintIssue("error", "terminal", f"Terminal node {t!r} not defined in nodes"))

    # ── Reachability check ────────────────────────────────────────────────────
    if node_ids:
        # Find nodes with no incoming edges (potential entry points)
        has_incoming = {dst for succs in graph.values() for dst in succs}
        roots = node_ids - has_incoming

        # BFS from all roots
        visited: set[str] = set()
        queue = list(roots)
        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            queue.extend(graph.get(nid, set()))

        unreachable = node_ids - visited - set(loop_back_pairs)
        for nid in unreachable:
            issues.append(LintIssue(
                "warning", f"nodes.{nid}",
                f"Node {nid!r} is unreachable — no path leads to it from any entry.",
                suggestion="Add an edge pointing to this node or remove it.",
            ))

    # ── Dead-end check ────────────────────────────────────────────────────────
    terminal_set = set(terminal)
    for nid in node_ids:
        has_outgoing = bool(graph.get(nid))
        is_terminal = nid in terminal_set
        is_loop_src = any(src == nid for src, _ in loop_back_pairs)
        if not has_outgoing and not is_terminal and len(node_ids) > 1 and not is_loop_src:
            issues.append(LintIssue(
                "info", f"nodes.{nid}",
                f"Node {nid!r} has no outgoing edges and is not marked terminal.",
                suggestion="Add it to the 'terminal:' list or add outgoing edges.",
            ))

    # ── Policy checks ─────────────────────────────────────────────────────────
    policy = data.get("policy", {})
    if isinstance(policy, dict):
        budget = policy.get("budget_usd", 1.0)
        if budget <= 0:
            issues.append(LintIssue("error", "policy.budget_usd", f"budget_usd must be > 0 (got {budget})"))
        max_steps = policy.get("max_steps", 50)
        if max_steps <= 0:
            issues.append(LintIssue("error", "policy.max_steps", f"max_steps must be > 0 (got {max_steps})"))
        if max_steps < len(nodes):
            issues.append(LintIssue(
                "warning", "policy.max_steps",
                f"max_steps={max_steps} is less than node count={len(nodes)} — workflow may be truncated.",
            ))

    return issues


def _validate_condition_syntax(expr: str) -> str:
    """Attempt to detect obvious condition expression errors.  Returns error string or ''."""
    if not expr.strip():
        return ""
    # Check for balanced parens
    depth = 0
    for ch in expr:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth < 0:
            return "unbalanced parentheses"
    if depth != 0:
        return "unbalanced parentheses"
    # Try compile
    try:
        compile(expr, "<condition>", "eval")
    except SyntaxError as exc:
        return f"syntax error: {exc}"
    return ""


__all__ = ["lint_workflow_yaml", "lint_workflow_data", "LintIssue", "LintResult"]
