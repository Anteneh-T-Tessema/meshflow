"""WorkflowDefinition — portable, YAML-declarative, graph-topological workflow.

A workflow is a directed acyclic graph of MeshNodes with a single policy
applied to all edges. Any DAG topology is supported, including fan-out
(parallel branches) and fan-in (joins).

Fan-out / fan-in example::

    name: research_pipeline
    version: "1"

    policy:
      budget_usd: 2.00
      max_steps: 30
      enable_guardian: true

    nodes:
      planner:   {kind: native, role: planner}
      branch_a:  {kind: python, ref: agents.research_a}
      branch_b:  {kind: python, ref: agents.research_b}
      branch_c:  {kind: python, ref: agents.research_c}
      synthesizer: {kind: native, role: executor}

    edges:
      - planner -> branch_a
      - planner -> branch_b
      - planner -> branch_c
      - branch_a -> synthesizer
      - branch_b -> synthesizer
      - branch_c -> synthesizer

    terminal:
      - synthesizer

Execution order: planner runs first (level 0). branch_a, branch_b, branch_c
have no dependency between them so they run concurrently via asyncio.gather()
(level 1). synthesizer runs after all three complete (level 2).

Every node, including parallel branches, passes through the full StepRuntime
governance kernel: guardian scan, budget gate, HITL, OTEL span, uncertainty
scoring, collusion detection, and ledger write. Parallelism is transparent to
the control plane — each branch gets its own audit record.

Linear (sequential) example::

    nodes:
      planner: {kind: native, role: planner}
      researcher: {kind: crewai, ref: crews.market_research}
      validator: {kind: langgraph, ref: graphs.fact_check}
      approval: {kind: human}
      writer: {kind: native, role: executor}

    edges:
      - planner -> researcher
      - researcher -> validator
      - validator -> approval
      - approval -> writer

The YAML is the artifact you commit to git. It is reproducible and inspectable
without running any code.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import yaml

from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.runtime import RuntimeOutcome, StepRuntime
from meshflow.core.schemas import HumanInLoopConfig, Policy, RiskTier


@dataclass
class WorkflowEdge:
    from_node: str
    to_node: str
    condition: str = ""     # future: expression evaluated against step output


@dataclass
class WorkflowResult:
    """Final outcome of running a WorkflowDefinition."""

    run_id: str
    workflow_name: str
    completed: bool
    output: str
    steps: list[RuntimeOutcome]
    total_cost_usd: float
    total_tokens: int
    total_carbon_gco2: float
    duration_s: float
    blocked_nodes: list[str]
    paused_nodes: list[str]
    ledger_db: str


class WorkflowDefinition:
    """A governed, graph-topological workflow.

    Build from YAML with ``WorkflowDefinition.from_yaml(path)`` or
    programmatically with ``WorkflowDefinition(name=...).add_node(...).add_edge(...)``.
    """

    def __init__(
        self,
        name: str,
        version: str = "1",
        policy: Policy | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.policy = policy or Policy()
        self._nodes: dict[str, MeshNode] = {}
        self._edges: list[WorkflowEdge] = []
        self._entry: str = ""
        self._terminal: list[str] = []

    # ── Builder API ───────────────────────────────────────────────────────────

    def add_node(self, node: MeshNode) -> "WorkflowDefinition":
        self._nodes[node.id] = node
        if not self._entry:
            self._entry = node.id
        return self

    def add_edge(
        self, from_node: str, to_node: str, condition: str = ""
    ) -> "WorkflowDefinition":
        self._edges.append(WorkflowEdge(from_node, to_node, condition))
        return self

    def set_entry(self, node_id: str) -> "WorkflowDefinition":
        self._entry = node_id
        return self

    def set_terminal(self, *node_ids: str) -> "WorkflowDefinition":
        self._terminal = list(node_ids)
        return self

    # ── Graph helpers ─────────────────────────────────────────────────────────

    def _successors(self, node_id: str) -> list[str]:
        return [e.to_node for e in self._edges if e.from_node == node_id]

    def _is_terminal(self, node_id: str) -> bool:
        return node_id in self._terminal or not self._successors(node_id)

    def _topological_levels(self) -> list[list[str]]:
        """Kahn's algorithm — groups nodes into parallel-safe execution levels.

        All nodes within a level have no dependency between them and can run
        concurrently. Nodes in level N+1 depend only on nodes in level ≤ N.
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for edge in self._edges:
            if edge.to_node in in_degree:
                in_degree[edge.to_node] += 1

        current: list[str] = sorted(n for n, d in in_degree.items() if d == 0)
        levels: list[list[str]] = []

        while current:
            levels.append(current)
            next_level: list[str] = []
            for node_id in current:
                for succ in self._successors(node_id):
                    in_degree[succ] -= 1
                    if in_degree[succ] == 0:
                        next_level.append(succ)
            current = sorted(next_level)

        return levels

    # kept for backward compatibility with any external callers
    def _topological_order(self) -> list[str]:
        return [n for level in self._topological_levels() for n in level]

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(
        self,
        task: str,
        runtime: StepRuntime,
        context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute the workflow with full StepRuntime governance on every node.

        Nodes with no dependency between them run concurrently via
        asyncio.gather(). All governance (guardian, budget, HITL, ledger)
        fires per node — parallelism is transparent to the control plane.
        """
        run_id = runtime._run_id
        start = time.monotonic()
        ctx = dict(context or {})
        ctx["task"] = task

        steps: list[RuntimeOutcome] = []
        blocked_nodes: list[str] = []
        paused_nodes: list[str] = []

        levels = self._topological_levels()
        if not levels and self._entry:
            levels = [[self._entry]]

        for level in levels:
            level_nodes = [self._nodes[nid] for nid in level if nid in self._nodes]
            if not level_nodes:
                continue

            # Snapshot context so all parallel nodes see the same input state.
            # Each node gets its own copy so runtime can write back internal
            # keys (_upstream_confidence etc.) without cross-node interference.
            ctx_snapshot = ctx.copy()

            async def _run_node(nd: MeshNode) -> RuntimeOutcome:
                return await runtime.run(
                    nd,
                    NodeInput(task=task, context=ctx_snapshot.copy()),
                    ctx_snapshot.copy(),
                )

            outcomes: list[RuntimeOutcome] = list(
                await asyncio.gather(*[_run_node(nd) for nd in level_nodes])
            )

            for outcome in outcomes:
                steps.append(outcome)
                if outcome.paused_for_human:
                    paused_nodes.append(outcome.record.node_id)
                elif not outcome.ok:
                    blocked_nodes.append(outcome.record.node_id)

            # Merge all parallel outputs into shared context for the next level.
            # Public keys only — skip internal runtime state (_prefixed keys).
            for outcome in outcomes:
                if outcome.ok and outcome.output.content:
                    ctx[f"{outcome.record.node_id}_output"] = outcome.output.content
                if outcome.ok and outcome.output.structured:
                    ctx.update(
                        {k: v for k, v in outcome.output.structured.items()
                         if not k.startswith("_")}
                    )

            if blocked_nodes or paused_nodes:
                break

        # Build final output from last successful step
        final_output = ""
        for outcome in reversed(steps):
            if outcome.ok and outcome.output.content:
                final_output = outcome.output.content
                break

        total_cost = sum(s.record.cost_usd for s in steps)
        total_tokens = sum(s.record.tokens_used for s in steps)
        total_carbon = sum(s.record.carbon_gco2 for s in steps)

        ledger_db = getattr(runtime._ledger, "_db_path", ":memory:")

        return WorkflowResult(
            run_id=run_id,
            workflow_name=self.name,
            completed=not blocked_nodes and not paused_nodes,
            output=final_output,
            steps=steps,
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_gco2=round(total_carbon, 4),
            duration_s=round(time.monotonic() - start, 2),
            blocked_nodes=blocked_nodes,
            paused_nodes=paused_nodes,
            ledger_db=ledger_db,
        )

    # ── YAML loader ───────────────────────────────────────────────────────────

    @classmethod
    def from_yaml(
        cls,
        path: str,
        node_registry: dict[str, Any] | None = None,
    ) -> "WorkflowDefinition":
        """Load a WorkflowDefinition from a YAML file.

        ``node_registry`` maps ref strings in YAML to live Python objects::

            registry = {
                "crews.market_research": my_crewai_crew,
                "graphs.fact_check":     my_langgraph_graph,
            }
            wf = WorkflowDefinition.from_yaml("mesh.yaml", registry)
        """
        with open(path) as fh:
            data = yaml.safe_load(fh)

        # Policy
        pol_cfg = data.get("policy", {})
        hitl_tier_str = pol_cfg.get("human_approval_tier", "irreversible").upper()
        hitl_tier = RiskTier[hitl_tier_str] if hitl_tier_str in RiskTier.__members__ else RiskTier.IRREVERSIBLE
        hitl_enabled = hitl_tier_str != "NONE"

        pol = Policy(
            budget_usd=pol_cfg.get("budget_usd", 1.0),
            budget_tokens=pol_cfg.get("budget_tokens", 500_000),
            timeout_s=pol_cfg.get("timeout_s", 300.0),
            max_steps=pol_cfg.get("max_steps", 50),
            enable_guardian=pol_cfg.get("enable_guardian", True),
            enable_collusion_audit=pol_cfg.get("enable_collusion_audit", True),
            enable_uncertainty=pol_cfg.get("enable_uncertainty", True),
            enable_environmental=pol_cfg.get("enable_environmental", False),
            enable_cross_run_learning=pol_cfg.get("enable_cross_run_learning", False),
            human_in_loop=HumanInLoopConfig(
                enabled=hitl_enabled,
                tier_threshold=hitl_tier,
            ),
        )

        wf = cls(
            name=data.get("name", "unnamed"),
            version=str(data.get("version", "1")),
            policy=pol,
        )

        # Nodes
        for node_id, node_cfg in data.get("nodes", {}).items():
            kind_str = node_cfg.get("kind", "native").lower()
            kind = NodeKind(kind_str)
            risk_str = node_cfg.get("risk", "READ_ONLY").upper()
            risk = RiskTier[risk_str] if risk_str in RiskTier.__members__ else RiskTier.READ_ONLY
            ref = node_cfg.get("ref", "")

            if kind == NodeKind.NATIVE:
                node = _build_native_node(node_id, node_cfg, pol)
            elif kind == NodeKind.HUMAN:
                node = MeshNode.human_approval(node_id)
            elif kind == NodeKind.PYTHON:
                fn = (node_registry or {}).get(ref)
                if fn:
                    node = MeshNode.from_callable(node_id, fn, risk)
                else:
                    node = MeshNode(id=node_id, kind=kind, risk_profile=risk)
            elif kind == NodeKind.CREWAI:
                crew = (node_registry or {}).get(ref)
                node = MeshNode.from_crewai(node_id, crew) if crew else MeshNode(id=node_id, kind=kind, risk_profile=risk)
            elif kind == NodeKind.LANGGRAPH:
                graph = (node_registry or {}).get(ref)
                node = MeshNode.from_langgraph(node_id, graph) if graph else MeshNode(id=node_id, kind=kind, risk_profile=risk)
            elif kind == NodeKind.AUTOGEN:
                agent = (node_registry or {}).get(ref)
                node = MeshNode.from_autogen(node_id, agent) if agent else MeshNode(id=node_id, kind=kind, risk_profile=risk)
            elif kind == NodeKind.HTTP:
                url = node_cfg.get("url", "")
                node = MeshNode.from_http(node_id, url, risk=risk)
            else:
                node = MeshNode(id=node_id, kind=kind, risk_profile=risk)

            wf.add_node(node)

        # Edges
        for edge_data in data.get("edges", []):
            if isinstance(edge_data, str):
                # "nodeA -> nodeB" shorthand
                parts = [p.strip() for p in edge_data.split("->")]
                if len(parts) == 2:
                    wf.add_edge(parts[0], parts[1])
            elif isinstance(edge_data, dict):
                wf.add_edge(
                    edge_data.get("from", ""),
                    edge_data.get("to", ""),
                    edge_data.get("condition", ""),
                )

        # Entry + terminal
        entry = data.get("entry", "")
        if entry:
            wf.set_entry(entry)

        terminal = data.get("terminal", [])
        if isinstance(terminal, str):
            terminal = [terminal]
        if terminal:
            wf.set_terminal(*terminal)

        return wf

    def describe(self) -> dict[str, Any]:
        """Return a human-readable description of the workflow topology."""
        return {
            "name": self.name,
            "version": self.version,
            "nodes": [
                {"id": n.id, "kind": n.kind.value, "risk": int(n.risk_profile)}
                for n in self._nodes.values()
            ],
            "edges": [
                {"from": e.from_node, "to": e.to_node}
                for e in self._edges
            ],
            "entry": self._entry,
            "terminal": self._terminal,
            "policy": {
                "budget_usd": self.policy.budget_usd,
                "max_steps": self.policy.max_steps,
                "enable_guardian": self.policy.enable_guardian,
            },
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_native_node(
    node_id: str, node_cfg: dict[str, Any], pol: Policy
) -> MeshNode:
    """Construct a MeshFlow native agent node from YAML config."""
    from meshflow.agents.base import (
        AgentConfig, CriticAgent, ExecutorAgent, PlannerAgent, ResearcherAgent,
    )
    from meshflow.core.schemas import AgentRole

    role_str = node_cfg.get("role", "executor").lower()
    model = node_cfg.get("model", pol.model_tier_map.get(AgentRole(role_str), "claude-sonnet-4-6"))

    role_map = {
        "planner":    (PlannerAgent,    AgentRole.PLANNER),
        "researcher": (ResearcherAgent, AgentRole.RESEARCHER),
        "executor":   (ExecutorAgent,   AgentRole.EXECUTOR),
        "critic":     (CriticAgent,     AgentRole.CRITIC),
    }
    AgentCls, role = role_map.get(role_str, (ExecutorAgent, AgentRole.EXECUTOR))
    cfg = AgentConfig(role=role, model=model)
    agent = AgentCls(cfg, pol)

    return MeshNode.from_native(node_id, agent)
