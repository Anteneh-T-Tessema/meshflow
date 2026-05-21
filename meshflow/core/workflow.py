"""WorkflowDefinition — portable, YAML-declarative, graph-topological workflow.

A workflow is a directed graph of MeshNodes with a single policy applied to
all edges. Any cycle-free topology is supported: linear chains, fan-out
(parallel branches), fan-in (joins), conditional routing, and DAGs.

YAML format::

    name: research_pipeline
    version: "1"

    policy:
      budget_usd: 1.00
      max_steps: 20
      enable_guardian: true
      human_approval_tier: irreversible   # READ_ONLY|INTERNAL|EXTERNAL_IO|IRREVERSIBLE

    nodes:
      planner:
        kind: native
        role: planner

      research_crew:
        kind: crewai
        ref: crews.market_research        # looked up in node_registry

      validator:
        kind: langgraph
        ref: graphs.fact_check

      approval:
        kind: human

      final_writer:
        kind: native
        role: executor

    edges:
      - planner -> research_crew
      - research_crew -> validator
      - validator -> approval
      - approval -> final_writer

    terminal:
      - final_writer

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

    def _topological_order(self) -> list[str]:
        """Kahn's algorithm — returns nodes in execution order for a DAG."""
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for edge in self._edges:
            if edge.to_node in in_degree:
                in_degree[edge.to_node] += 1

        queue: deque[str] = deque(
            n for n, d in in_degree.items() if d == 0
        )
        order: list[str] = []
        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for succ in self._successors(node_id):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)
        return order

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(
        self,
        task: str,
        runtime: StepRuntime,
        context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute the workflow with full StepRuntime governance on every node."""
        run_id = runtime._run_id
        start = time.monotonic()
        ctx = dict(context or {})
        ctx["task"] = task

        steps: list[RuntimeOutcome] = []
        blocked_nodes: list[str] = []
        paused_nodes: list[str] = []

        # Walk the graph in topological order
        exec_order = self._topological_order()
        if not exec_order and self._entry:
            exec_order = [self._entry]

        for node_id in exec_order:
            node = self._nodes.get(node_id)
            if node is None:
                continue

            node_input = NodeInput(task=task, context=ctx.copy())
            outcome = await runtime.run(node, node_input, ctx)
            steps.append(outcome)

            if outcome.paused_for_human:
                paused_nodes.append(node_id)
                # Paused workflows stop here — caller must resume
                break

            if not outcome.ok:
                blocked_nodes.append(node_id)
                break

            # Merge output into shared context for downstream nodes
            if outcome.output.content:
                ctx[f"{node_id}_output"] = outcome.output.content
            if outcome.output.structured:
                ctx.update(
                    {k: v for k, v in outcome.output.structured.items()
                     if not k.startswith("_")}
                )

        # Build final output from last successful step
        final_output = ""
        for outcome in reversed(steps):
            if outcome.ok and outcome.output.content:
                final_output = outcome.output.content
                break

        total_cost = sum(s.record.cost_usd for s in steps)
        total_tokens = sum(s.record.tokens_used for s in steps)
        total_carbon = sum(s.record.carbon_gco2 for s in steps)

        # Determine ledger db path from ledger if available
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
