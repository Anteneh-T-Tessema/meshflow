"""Static analysis linter for WorkflowDefinition graphs.

WorkflowLinter inspects a compiled WorkflowDefinition for structural
problems before any execution occurs, catching issues that would otherwise
surface only at runtime.

Checks performed
----------------
- **cycle** — detects directed cycles in the forward-edge graph (excluding
  declared loop_edges which are intentional back-edges).
- **unreachable_node** — nodes with no incoming edge and not the entry node
  (dead branches that will never execute).
- **dead_end** — non-terminal nodes with no outgoing edges (execution would
  stall).
- **missing_terminal** — workflow has no terminal node declared.
- **conflicting_conditions** — two edges from the same source have identical
  non-empty condition expressions (one will always shadow the other).
- **missing_entry** — no entry node is set.
- **unknown_node_ref** — an edge references a node ID not in the node map.

Usage::

    from meshflow.core.workflow_linter import WorkflowLinter

    wf = WorkflowDefinition("pipeline") ...
    linter = WorkflowLinter(wf)
    report = linter.lint()
    if report.errors:
        raise ValueError(report.format())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meshflow.core.workflow import WorkflowDefinition


@dataclass
class LintIssue:
    """A single linting finding."""

    code: str       # e.g. "cycle", "dead_end"
    severity: str   # "error" | "warning"
    message: str
    nodes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        node_str = f" [nodes: {', '.join(self.nodes)}]" if self.nodes else ""
        return f"[{self.severity.upper()}] {self.code}: {self.message}{node_str}"


@dataclass
class LintReport:
    """Aggregated findings from WorkflowLinter.lint()."""

    issues: list[LintIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def format(self) -> str:
        if not self.issues:
            return "WorkflowLinter: no issues found."
        lines = [f"WorkflowLinter found {len(self.issues)} issue(s):"]
        for issue in self.issues:
            lines.append(f"  {issue}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format()


class WorkflowLinter:
    """Static analyser for :class:`~meshflow.core.workflow.WorkflowDefinition`.

    Parameters
    ----------
    workflow:
        The compiled ``WorkflowDefinition`` to analyse.
    """

    def __init__(self, workflow: "WorkflowDefinition") -> None:
        self._wf = workflow

    def lint(self) -> LintReport:
        """Run all checks and return a :class:`LintReport`."""
        report = LintReport()
        self._check_missing_entry(report)
        self._check_unknown_node_refs(report)
        self._check_missing_terminal(report)
        self._check_cycle(report)
        self._check_unreachable(report)
        self._check_dead_ends(report)
        self._check_conflicting_conditions(report)
        return report

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_missing_entry(self, report: LintReport) -> None:
        if not self._wf._entry and self._wf._nodes:
            report.issues.append(LintIssue(
                code="missing_entry",
                severity="error",
                message="No entry node set. Call .set_entry(node_id) on the workflow.",
            ))

    def _check_missing_terminal(self, report: LintReport) -> None:
        if not self._wf._terminal and self._wf._nodes:
            report.issues.append(LintIssue(
                code="missing_terminal",
                severity="error",
                message="No terminal node declared. Call .set_terminal(*node_ids).",
            ))

    def _check_unknown_node_refs(self, report: LintReport) -> None:
        known = set(self._wf._nodes.keys())
        for edge in self._wf._edges:
            for ref in (edge.from_node, edge.to_node):
                if ref not in known:
                    report.issues.append(LintIssue(
                        code="unknown_node_ref",
                        severity="error",
                        message=f"Edge references unknown node '{ref}'.",
                        nodes=[ref],
                    ))

    def _check_cycle(self, report: LintReport) -> None:
        """DFS-based cycle detection on forward edges only."""
        loop_pairs = {(e.src, e.dst) for e in self._wf._loop_edges}
        adjacency: dict[str, list[str]] = {n: [] for n in self._wf._nodes}
        for edge in self._wf._edges:
            if (edge.from_node, edge.to_node) not in loop_pairs:
                adjacency.setdefault(edge.from_node, []).append(edge.to_node)

        visited: set[str] = set()
        in_stack: set[str] = set()
        cycle_nodes: list[str] = []

        def dfs(node: str) -> bool:
            visited.add(node)
            in_stack.add(node)
            for neighbour in adjacency.get(node, []):
                if neighbour not in visited:
                    if dfs(neighbour):
                        return True
                elif neighbour in in_stack:
                    cycle_nodes.append(neighbour)
                    return True
            in_stack.discard(node)
            return False

        for node in self._wf._nodes:
            if node not in visited:
                if dfs(node):
                    report.issues.append(LintIssue(
                        code="cycle",
                        severity="error",
                        message=(
                            "Directed cycle detected in forward edges. "
                            "Use add_loop_edge() for intentional back-edges."
                        ),
                        nodes=cycle_nodes[:],
                    ))
                    cycle_nodes.clear()

    def _check_unreachable(self, report: LintReport) -> None:
        if not self._wf._entry:
            return
        # BFS from entry
        reachable: set[str] = set()
        queue = [self._wf._entry]
        while queue:
            curr = queue.pop()
            if curr in reachable:
                continue
            reachable.add(curr)
            for edge in self._wf._edges:
                if edge.from_node == curr and edge.to_node not in reachable:
                    queue.append(edge.to_node)
        # Also follow loop edges so their targets aren't flagged
        for le in self._wf._loop_edges:
            reachable.add(le.dst)

        unreachable = [n for n in self._wf._nodes if n not in reachable]
        if unreachable:
            report.issues.append(LintIssue(
                code="unreachable_node",
                severity="warning",
                message=(
                    f"{len(unreachable)} node(s) are unreachable from the entry node "
                    f"'{self._wf._entry}'."
                ),
                nodes=unreachable,
            ))

    def _check_dead_ends(self, report: LintReport) -> None:
        terminal_set = set(self._wf._terminal)
        nodes_with_outgoing = {e.from_node for e in self._wf._edges}
        nodes_with_loop_out = {e.src for e in self._wf._loop_edges}
        for node_id in self._wf._nodes:
            if node_id in terminal_set:
                continue
            if node_id not in nodes_with_outgoing and node_id not in nodes_with_loop_out:
                report.issues.append(LintIssue(
                    code="dead_end",
                    severity="warning",
                    message=(
                        f"Node '{node_id}' has no outgoing edges and is not declared "
                        "terminal — execution would stall here."
                    ),
                    nodes=[node_id],
                ))

    def _check_conflicting_conditions(self, report: LintReport) -> None:
        from collections import defaultdict
        # Group edges by source node
        by_source: dict[str, list[str]] = defaultdict(list)
        for edge in self._wf._edges:
            if edge.condition:
                by_source[edge.from_node].append(edge.condition.strip())

        for src, conditions in by_source.items():
            seen: set[str] = set()
            for cond in conditions:
                if cond in seen:
                    report.issues.append(LintIssue(
                        code="conflicting_conditions",
                        severity="warning",
                        message=(
                            f"Node '{src}' has two outgoing edges with identical "
                            f"condition '{cond}'. One will always shadow the other."
                        ),
                        nodes=[src],
                    ))
                seen.add(cond)
