"""Main Mesh class — the single entry point for all MeshFlow operations.

All nine layers are coordinated here. The user calls mesh.run() and
all complexity is hidden inside. This is the "easy to use" promise.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from meshflow.agents.base import (
    AgentConfig, BaseAgent, CriticAgent, ExecutorAgent,
    PlannerAgent, ResearcherAgent,
)
from meshflow.core.graph import GraphEdge, GraphNode, StateGraph
from meshflow.core.policy import PolicyEngine
from meshflow.core.schemas import (
    AgentRole, AgentState, CheckpointRecord, Evidence,
    Intent, Message, Policy, RunResult, RunStatus, RiskTier,
)
from meshflow.efficiency.cross_run import CrossRunLearner, CrossRunStore, LearningQuery
from meshflow.efficiency.environmental import EnvironmentalOptimizer
from meshflow.intelligence.collusion import CollusionAuditor
from meshflow.intelligence.mem1 import MEM1Store
from meshflow.intelligence.uncertainty import UncertaintyEngine
from meshflow.mcp.gateway import MCPGateway, ToolManifest
from meshflow.security.dasc_gate import DascGate
from meshflow.security.guardian import Guardian
from meshflow.security.identity import AgentIdentityProvider


class Mesh:
    """MeshFlow orchestrator — all nine layers, one simple API.

    Usage:
        mesh = Mesh()
        result = await mesh.run(
            task="Research and summarise the top 5 LLM frameworks",
            policy=Policy(budget_usd=0.50),
        )
        print(result.output)

    Import external agents:
        from meshflow.agents.adapters import from_crewai
        mesh = Mesh(agents=[from_crewai(my_crew_agent)])
    """

    def __init__(
        self,
        agents: list[BaseAgent] | None = None,
        policy: Policy | None = None,
        mcp_tools: list[ToolManifest] | None = None,
        cross_run_db: str = ":memory:",
    ) -> None:
        self._custom_agents = agents or []
        self._policy = policy or Policy()
        self._mcp_tools = mcp_tools or []
        self._cross_run_db = cross_run_db
        self._cross_run_store = CrossRunStore(cross_run_db)

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run(
        self,
        task: str,
        policy: Policy | None = None,
        context: dict[str, Any] | None = None,
    ) -> RunResult:
        """Execute a task through the full MeshFlow pipeline."""
        pol = policy or self._policy
        run_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        start = time.monotonic()

        # ── Initialise all layers ─────────────────────────────────────────────
        policy_engine = PolicyEngine(pol, run_id)
        identity      = AgentIdentityProvider(run_id)
        guardian      = Guardian(budget_usd=pol.budget_usd)
        dasc_gate     = DascGate(pol, run_id)
        uncertainty   = UncertaintyEngine()
        collusion     = CollusionAuditor()
        eco           = EnvironmentalOptimizer(pol.carbon_budget_g) if pol.enable_environmental else None
        learner       = CrossRunLearner(self._cross_run_store) if pol.enable_cross_run_learning else None
        mcp           = MCPGateway(budget_usd_per_turn=pol.budget_usd / 10)
        for tool in self._mcp_tools:
            mcp.register_tool(tool)

        # ── Complexity check ──────────────────────────────────────────────────
        complexity = policy_engine.check_complexity(task, len(self._custom_agents) or 4)
        if complexity["recommendation"] == "single_agent" and not self._custom_agents:
            return await self._single_agent_run(task, pol, run_id, trace_id, start)

        # ── Cross-run learning recommendation ─────────────────────────────────
        if learner:
            rec = learner.recommend(LearningQuery(
                task_description=task,
                estimated_tokens=len(task.split()) * 10,
                available_roles=[r.value for r in AgentRole],
            ))
            # Use recommendation to inform agent selection (simplified)

        # ── Build agent pool ──────────────────────────────────────────────────
        agents = self._build_agents(pol)

        # Provision DIDs for all agents
        for agent in agents:
            caps = self._capabilities_for_role(agent.role)
            doc = identity.provision(agent.agent_id, caps)
            agent.state.did = doc.did

        # ── Build state graph ─────────────────────────────────────────────────
        graph = self._build_graph(run_id, agents, task, context or {})
        ctx = context or {}

        # ── Execute graph ─────────────────────────────────────────────────────
        error = ""
        output: Any = None
        agent_states: dict[str, AgentState] = {}
        checkpoint_ids: list[str] = []

        try:
            graph_state = await graph.run(
                initial_data={"task": task, **ctx},
                on_checkpoint=self._make_checkpoint_handler(checkpoint_ids),
            )

            output = (
                graph_state.data.get("execution_result")
                or graph_state.data.get("research")
                or graph_state.data.get("plan")
                or graph_state.data
            )
            checkpoint_ids = graph.checkpoint_ids()

            # ── Post-run collusion audit ───────────────────────────────────────
            agent_ids = [a.agent_id for a in agents]
            collusion_alerts = collusion.audit(agent_ids)

        except Exception as e:
            error = str(e)
            graph_state = type("GS", (), {"status": RunStatus.FAILED, "data": {}})()

        # ── Revoke all DIDs ───────────────────────────────────────────────────
        identity.revoke_all(reason="run_complete")

        # ── Aggregate costs ───────────────────────────────────────────────────
        total_tokens = sum(a.state.token_count for a in agents)
        total_cost = sum(a.state.cost_usd for a in agents)
        total_carbon = eco.summary()["carbon_spent_g"] if eco else 0.0

        for a in agents:
            agent_states[a.agent_id] = a.state

        # ── Cross-run learning: record outcome ────────────────────────────────
        if learner:
            learner.record_run_outcome(
                task_description=task,
                agent_config={"roles": [a.role.value for a in agents]},
                strategy="planner→researcher→executor→critic",
                success=not error,
                cost_usd=total_cost,
                tokens=total_tokens,
                carbon_g=total_carbon,
            )

        status = RunStatus.COMPLETED if not error else RunStatus.FAILED

        return RunResult(
            run_id=run_id,
            status=status,
            output=output,
            agent_states=agent_states,
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_g=round(total_carbon, 4),
            duration_s=round(time.monotonic() - start, 2),
            checkpoints=checkpoint_ids,
            ledger_entries=dasc_gate.ledger_count(),
            trace_id=trace_id,
            error=error,
            collusion_alerts=collusion.total_alerts(),
            drift_alerts=0,
        )

    # ── Single-agent fast path ────────────────────────────────────────────────

    async def _single_agent_run(
        self,
        task: str,
        pol: Policy,
        run_id: str,
        trace_id: str,
        start: float,
    ) -> RunResult:
        """Bypass multi-agent overhead for simple tasks — the complexity router in action."""
        agent = ExecutorAgent(
            AgentConfig(role=AgentRole.EXECUTOR, model=pol.model_tier_map[AgentRole.EXECUTOR]),
            pol,
        )
        result = await agent.step(task, {})
        return RunResult(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            output=result.get("execution_result", ""),
            agent_states={agent.agent_id: agent.state},
            total_cost_usd=result.get("cost_usd", 0.0),
            total_tokens=result.get("tokens", 0),
            total_carbon_g=0.0,
            duration_s=round(time.monotonic() - start, 2),
            checkpoints=[],
            ledger_entries=0,
            trace_id=trace_id,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_agents(self, pol: Policy) -> list[BaseAgent]:
        if self._custom_agents:
            return list(self._custom_agents)
        return [
            PlannerAgent(
                AgentConfig(role=AgentRole.PLANNER, model=pol.model_tier_map[AgentRole.PLANNER]),
                pol,
            ),
            ResearcherAgent(
                AgentConfig(role=AgentRole.RESEARCHER, model=pol.model_tier_map[AgentRole.RESEARCHER]),
                pol,
            ),
            ExecutorAgent(
                AgentConfig(role=AgentRole.EXECUTOR, model=pol.model_tier_map[AgentRole.EXECUTOR]),
                pol,
            ),
            CriticAgent(
                AgentConfig(role=AgentRole.CRITIC, model=pol.model_tier_map[AgentRole.CRITIC]),
                pol,
            ),
        ]

    def _build_graph(
        self,
        run_id: str,
        agents: list[BaseAgent],
        task: str,
        context: dict[str, Any],
    ) -> StateGraph:
        graph = StateGraph(run_id)
        agent_map = {a.role: a for a in agents}

        # Build nodes
        for agent in agents:
            node = GraphNode(
                node_id=agent.role.value,
                agent_id=agent.agent_id,
                fn=lambda data, a=agent: a.step(data.get("task", ""), data),
            )
            graph.add_node(node)

        # Wire edges: planner → researcher → executor → critic
        graph.add_edge(GraphEdge("planner", "researcher"))
        graph.add_edge(GraphEdge("researcher", "executor"))
        graph.add_edge(GraphEdge(
            "executor", "critic",
            condition=lambda d: AgentRole.CRITIC.value in {a.role.value for a in agents},
        ))
        graph.add_edge(GraphEdge("critic", "executor",
            condition=lambda d: not d.get("critic_passed", True),
        ))

        # Set entry and terminals
        if AgentRole.PLANNER in agent_map:
            graph.set_entry("planner")
        elif agents:
            graph.set_entry(agents[0].role.value)

        graph.set_terminals("critic", "executor")
        return graph

    def _capabilities_for_role(self, role: AgentRole) -> list[str]:
        role_caps = {
            AgentRole.ORCHESTRATOR: ["plan", "delegate", "evaluate", "terminate"],
            AgentRole.PLANNER:      ["plan", "decompose"],
            AgentRole.RESEARCHER:   ["search", "read", "synthesize"],
            AgentRole.EXECUTOR:     ["write", "compute", "call_tool"],
            AgentRole.CRITIC:       ["evaluate", "score"],
            AgentRole.GUARDIAN:     ["monitor", "block", "alert"],
        }
        return role_caps.get(role, ["read"])

    @staticmethod
    def _make_checkpoint_handler(
        checkpoint_ids: list[str],
    ):
        async def handler(cp: CheckpointRecord) -> None:
            checkpoint_ids.append(cp.checkpoint_id)
        return handler
