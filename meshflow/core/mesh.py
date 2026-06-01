"""MeshFlow Mesh — control plane for multi-agent systems.

Positioning: MeshFlow is NOT a replacement for LangGraph, CrewAI, or AutoGen.
It is the governance and orchestration standard ABOVE them — a unified control
plane that runs agents from any framework under a single policy, identity,
audit, and security layer.

  Use LangGraph to build graphs.
  Use CrewAI to build crews.
  Use AutoGen to build agent conversations.
  Use MeshFlow to govern, orchestrate, audit, and standardize them all.

Three entry points:

  1. Native workflow (fastest start):
        result = await Mesh().run("your task")

  2. Universal MeshNode workflow (cross-framework):
        from meshflow.core.node import MeshNode
        from meshflow.core.workflow import WorkflowDefinition

        wf = (WorkflowDefinition("pipeline")
              .add_node(MeshNode.from_crewai("research", crew))
              .add_node(MeshNode.from_langgraph("validate", graph))
              .add_node(MeshNode.human_approval("approve"))
              .add_edge("research", "validate")
              .add_edge("validate", "approve"))

        result = await Mesh().run_workflow(wf, task="analyse Q2 revenue")

  3. YAML config (reproducible, git-committable):
        mesh = Mesh.from_yaml("meshflow.yaml")

Every node — regardless of origin — passes through the StepRuntime kernel:
  pre_step  → identity | circuit-breaker | guardian | risk-gate | budget | HITL
  execute   → node.run() | trace | checkpoint
  post_step → uncertainty | cost | ledger | memory | collusion | CAEP
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

from meshflow.core.events import WorkflowEventBus
from meshflow.core.ledger import ReplayLedger
from meshflow.core.runtime import StepRuntime
from meshflow.core.workflow import HumanDecision, WorkflowDefinition, WorkflowResult
from meshflow.agents.base import (
    AgentConfig,
    BaseAgent,
    CriticAgent,
    ExecutorAgent,
    PlannerAgent,
    ResearcherAgent,
)
from meshflow.core.executor import GovernedStepExecutor, StepOutcome
from meshflow.core.policy import PolicyEngine, BudgetTracker
from meshflow.core.schemas import (
    AgentRole,
    Policy,
    RunResult,
    RunStatus,
    policy_for_mode,
)
from meshflow.efficiency.cross_run import CrossRunLearner, CrossRunStore, LearningQuery
from meshflow.efficiency.environmental import EnvironmentalOptimizer
from meshflow.intelligence.collusion import CollusionAuditor
from meshflow.intelligence.uncertainty import UncertaintyEngine
from meshflow.mcp.gateway import MCPGateway, ToolManifest
from meshflow.observability.telemetry import MeshFlowTracer
from meshflow.security.dasc_gate import DascGate
from meshflow.security.guardian import Guardian
from meshflow.security.identity import AgentIdentityProvider


@dataclass
class MeshEvent:
    """Streaming event emitted as each governed step completes."""

    event_type: str  # "step_complete" | "step_blocked" | "paused" | "run_complete" | "error"
    agent_id: str
    role: str
    data: dict[str, Any]
    run_id: str
    step: int
    timestamp: float = field(default_factory=time.monotonic)
    uncertainty: float = 0.0
    cost_usd: float = 0.0
    tokens: int = 0
    blocked_by: str = ""


class Mesh:
    """MeshFlow governed orchestration runtime.

    Three usage patterns:

    1. Native agents (fastest start):
        result = await Mesh().run("your task")

    2. Import from another framework:
        from meshflow.agents.adapters import from_crewai, from_autogen, from_langgraph
        mesh = Mesh(agents=[from_crewai(agent), from_autogen(agent2)])

    3. YAML config:
        mesh = Mesh.from_yaml("meshflow.yaml")

    All three patterns route every step through the same governance layers.
    """

    def __init__(
        self,
        agents: list[BaseAgent] | None = None,
        policy: Policy | None = None,
        mcp_tools: list[ToolManifest] | None = None,
        cross_run_db: str = ":memory:",
        telemetry_console: bool = False,
        telemetry_otlp_endpoint: str = "",
        telemetry_otlp_protocol: str = "",
        compliance: str = "",
        name: str = "",
    ) -> None:
        self.name = name
        self._custom_agents = agents or []
        self._compliance_profile = None
        if compliance:
            from meshflow.core.compliance import compliance_profile
            self._compliance_profile = compliance_profile(compliance)
            if policy is None:
                policy = self._compliance_profile.to_policy()
        self._policy = policy or Policy()
        self._mcp_tools = mcp_tools or []
        self._cross_run_db = cross_run_db
        self._telemetry_console = telemetry_console
        self._telemetry_otlp_endpoint = telemetry_otlp_endpoint
        self._telemetry_otlp_protocol = telemetry_otlp_protocol

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run(
        self,
        task: str,
        policy: Policy | None = None,
        context: dict[str, Any] | None = None,
    ) -> RunResult:
        """Execute a task through the full governed pipeline."""
        events = []
        async for event in self.stream(task, policy=policy, context=context):
            events.append(event)

        # Reconstruct RunResult from the terminal run_complete event
        terminal = next((e for e in reversed(events) if e.event_type == "run_complete"), None)
        result = cast(
            RunResult,
            terminal.data.get("_run_result", self._empty_result(task, events))
            if terminal else self._empty_result(task, events),
        )

        # Fire-and-forget cloud telemetry (no-op when MESHFLOW_CLOUD_KEY is unset)
        try:
            from meshflow.cloud.reporter import report_run
            _pol = policy or self._policy
            report_run(
                result,
                workflow_name=self.name or "mesh",
                agent_count=len(result.agent_states),
                policy_mode=_pol.mode.value if hasattr(_pol.mode, "value") else str(_pol.mode),
                compliance=self._compliance_profile.name if self._compliance_profile else None,
            )
        except Exception:
            pass  # telemetry must never raise

        return result

    async def stream(
        self,
        task: str,
        policy: Policy | None = None,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[MeshEvent]:
        """Stream governed events as each agent step completes.

        Usage:
            async for event in mesh.stream("task"):
                print(f"{event.role}: {event.data.get('execution_result', '')[:100]}")
                print(f"  confidence={event.uncertainty:.2f}  cost=${event.cost_usd:.4f}")
        """
        pol = policy or self._policy
        run_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        start = time.monotonic()

        # ── Initialise all governance layers ──────────────────────────────────
        policy_engine = PolicyEngine(pol, run_id)
        identity = AgentIdentityProvider(run_id)
        guardian = Guardian(budget_usd=pol.budget_usd)
        dasc_gate = DascGate(pol, run_id)
        uncertainty = UncertaintyEngine()
        collusion = CollusionAuditor()
        telemetry = self._new_tracer()
        eco = EnvironmentalOptimizer(pol.carbon_budget_g) if pol.enable_environmental else None
        mcp = MCPGateway(budget_usd_per_turn=pol.budget_usd / 20)
        for tool in self._mcp_tools:
            mcp.register_tool(tool)

        # Zero Trust — Foundation tier active by default on every run
        _zero_trust = None
        try:
            from meshflow.zero_trust.orchestrator import ZeroTrustOrchestrator
            from meshflow.zero_trust.policy import ZeroTrustTier
            _zero_trust = ZeroTrustOrchestrator.for_tier(ZeroTrustTier.FOUNDATION)
            # Register each agent for continuous auth (Foundation: deny-by-default RBAC)
            if _zero_trust._cont_auth:
                for _a in self._custom_agents or []:
                    _aid = getattr(_a, "agent_id", getattr(_a, "name", str(id(_a))))
                    _zero_trust._cont_auth.register(_aid, permissions=["run:step", "read:*"])
        except Exception:
            pass  # ZT bootstrap must never block execution

        executor = GovernedStepExecutor(
            policy_engine=policy_engine,
            identity=identity,
            guardian=guardian,
            dasc_gate=dasc_gate,
            uncertainty=uncertainty,
            collusion=collusion,
            telemetry=telemetry,
            eco=eco,
            run_id=run_id,
            trace_id=trace_id,
            zero_trust=_zero_trust,
        )

        # ── Complexity check — single vs multi agent ──────────────────────────
        complexity = policy_engine.check_complexity(task, len(self._custom_agents) or 4)
        if complexity["recommendation"] == "single_agent" and not self._custom_agents:
            async for event in self._single_agent_stream(
                task, pol, run_id, trace_id, start, executor, identity
            ):
                yield event
            return

        # ── Cross-run learning ────────────────────────────────────────────────
        learner: CrossRunLearner | None = None
        if pol.enable_cross_run_learning:
            store = CrossRunStore(self._cross_run_db)
            learner = CrossRunLearner(store)
            learner.recommend(
                LearningQuery(
                    task_description=task,
                    estimated_tokens=len(task.split()) * 10,
                    available_roles=[r.value for r in AgentRole],
                )
            )

        # ── Build agents and provision identities ─────────────────────────────
        agents = self._build_agents(pol)
        for agent in agents:
            caps = self._capabilities_for_role(agent.role)
            doc = identity.provision(agent.agent_id, caps)
            agent.state.did = doc.did

        # ── Execute pipeline with full governance at every step ───────────────
        ctx = dict(context or {})
        ctx["task"] = task
        step = 0
        error = ""
        checkpoint_ids: list[str] = []
        total_tokens = 0
        total_cost = 0.0
        all_outcomes: list[StepOutcome] = []

        # Define the governed pipeline order
        pipeline = self._build_pipeline(agents, pol)

        for agent in pipeline:
            step += 1

            outcome = await executor.execute(agent, task, ctx)
            all_outcomes.append(outcome)

            event_type = "step_complete" if outcome.ok else "step_blocked"
            total_tokens += outcome.tokens
            total_cost += outcome.cost_usd

            yield MeshEvent(
                event_type=event_type,
                agent_id=outcome.agent_id,
                role=outcome.role,
                data=outcome.data,
                run_id=run_id,
                step=step,
                uncertainty=outcome.uncertainty.composite if outcome.uncertainty else 0.0,
                cost_usd=outcome.cost_usd,
                tokens=outcome.tokens,
                blocked_by=outcome.blocked_by,
            )

            if not outcome.ok:
                error = outcome.blocked_by
                break

            # Merge outcome into shared context for next agent
            ctx.update({k: v for k, v in outcome.data.items() if not k.startswith("__")})

            # Checkpoint after each successful governed step
            checkpoint_ids.append(f"{run_id}:step:{step}")

            # Human-in-loop pause
            if outcome.paused_for_human:
                yield MeshEvent(
                    event_type="paused",
                    agent_id=outcome.agent_id,
                    role=outcome.role,
                    data={"human_context": outcome.human_context},
                    run_id=run_id,
                    step=step,
                )
                break

            # Critic logic: if critic fails, re-run executor (max 1 retry)
            if outcome.role == AgentRole.CRITIC.value and not outcome.data.get(
                "critic_passed", True
            ):
                executor_agent = next((a for a in agents if a.role == AgentRole.EXECUTOR), None)
                if executor_agent:
                    step += 1
                    retry = await executor.execute(executor_agent, task, ctx)
                    all_outcomes.append(retry)
                    ctx.update({k: v for k, v in retry.data.items() if not k.startswith("__")})
                    yield MeshEvent(
                        event_type="step_complete" if retry.ok else "step_blocked",
                        agent_id=retry.agent_id,
                        role=retry.role,
                        data=retry.data,
                        run_id=run_id,
                        step=step,
                        uncertainty=retry.uncertainty.composite if retry.uncertainty else 0.0,
                        cost_usd=retry.cost_usd,
                        tokens=retry.tokens,
                    )
                    total_tokens += retry.tokens
                    total_cost += retry.cost_usd

        # ── Post-run audits ───────────────────────────────────────────────────
        agent_ids = [a.agent_id for a in agents]
        collusion_alerts = collusion.audit(agent_ids)
        identity.revoke_all(reason="run_complete")

        total_carbon = eco.summary()["carbon_spent_g"] if eco else 0.0
        agent_states = {a.agent_id: a.state for a in agents}

        final_output = self._extract_output(ctx)
        status = RunStatus.FAILED if error else RunStatus.COMPLETED

        # Record for cross-run learning
        if learner:
            learner.record_run_outcome(
                task_description=task,
                agent_config={"roles": [a.role.value for a in agents]},
                strategy="governed-pipeline",
                success=not error,
                cost_usd=total_cost,
                tokens=total_tokens,
                carbon_g=total_carbon,
            )

        run_result = RunResult(
            run_id=run_id,
            status=status,
            output=final_output,
            agent_states=agent_states,
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_g=round(total_carbon, 4),
            duration_s=round(time.monotonic() - start, 2),
            checkpoints=checkpoint_ids,
            ledger_entries=dasc_gate.ledger_count(),
            trace_id=trace_id,
            error=error,
            collusion_alerts=len(collusion_alerts),
            drift_alerts=0,
        )

        yield MeshEvent(
            event_type="run_complete",
            agent_id="orchestrator",
            role="orchestrator",
            data={"_run_result": run_result, "output": final_output},
            run_id=run_id,
            step=step,
            cost_usd=total_cost,
            tokens=total_tokens,
        )

    # ── Single-agent fast path ────────────────────────────────────────────────

    async def _single_agent_stream(
        self,
        task: str,
        pol: Policy,
        run_id: str,
        trace_id: str,
        start: float,
        executor: GovernedStepExecutor,
        identity: AgentIdentityProvider,
    ) -> AsyncIterator[MeshEvent]:
        agent = ExecutorAgent(
            AgentConfig(role=AgentRole.EXECUTOR, model=pol.model_tier_map[AgentRole.EXECUTOR]),
            pol,
        )
        doc = identity.provision(agent.agent_id, ["compute", "read"])
        agent.state.did = doc.did

        outcome = await executor.execute(agent, task, {"task": task})
        identity.revoke_all(reason="run_complete")

        yield MeshEvent(
            event_type="step_complete" if outcome.ok else "step_blocked",
            agent_id=outcome.agent_id,
            role=outcome.role,
            data=outcome.data,
            run_id=run_id,
            step=1,
            uncertainty=outcome.uncertainty.composite if outcome.uncertainty else 0.0,
            cost_usd=outcome.cost_usd,
            tokens=outcome.tokens,
        )

        run_result = RunResult(
            run_id=run_id,
            status=RunStatus.COMPLETED if outcome.ok else RunStatus.FAILED,
            output=outcome.data.get("execution_result", ""),
            agent_states={agent.agent_id: agent.state},
            total_cost_usd=round(outcome.cost_usd, 6),
            total_tokens=outcome.tokens,
            total_carbon_g=0.0,
            duration_s=round(time.monotonic() - start, 2),
            checkpoints=[],
            ledger_entries=0,
            trace_id=trace_id,
            error=outcome.blocked_by,
        )
        yield MeshEvent(
            event_type="run_complete",
            agent_id="orchestrator",
            role="orchestrator",
            data={"_run_result": run_result},
            run_id=run_id,
            step=1,
        )

    # ── YAML config loader ────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str) -> "Mesh":
        """Load a Mesh from a YAML config file.

        Example meshflow.yaml:
            policy:
              budget_usd: 1.00
              timeout_s: 120
              enable_guardian: true
              enable_collusion_audit: true
            agents:
              - role: planner
                model: claude-sonnet-4-6
              - role: researcher
                model: claude-sonnet-4-6
              - role: executor
                model: claude-haiku-4-5-20251001
              - role: critic
                model: claude-sonnet-4-6
        """
        import yaml

        with open(path) as f:
            cfg = yaml.safe_load(f)

        pol_cfg = cfg.get("policy", {})
        pol = policy_for_mode(
            pol_cfg.get("mode", "standard"),
            budget_usd=pol_cfg.get("budget_usd", 1.0),
            budget_tokens=pol_cfg.get("budget_tokens", 500_000),
            timeout_s=pol_cfg.get("timeout_s", 300.0),
            max_steps=pol_cfg.get("max_steps", 50),
            deterministic_gate=pol_cfg.get("enable_guardian", True),
            enable_guardian=pol_cfg.get("enable_guardian", True),
            enable_collusion_audit=pol_cfg.get("enable_collusion_audit", True),
            enable_environmental=pol_cfg.get("enable_environmental", False),
            enable_cross_run_learning=pol_cfg.get("enable_cross_run_learning", False),
        )

        agents: list[BaseAgent] = []
        role_map = {
            "planner": PlannerAgent,
            "researcher": ResearcherAgent,
            "executor": ExecutorAgent,
            "critic": CriticAgent,
        }
        for agent_cfg in cfg.get("agents", []):
            role_str = agent_cfg.get("role", "executor")
            model = agent_cfg.get("model", "claude-sonnet-4-6")
            AgentClass = role_map.get(role_str, ExecutorAgent)
            role = AgentRole(role_str)
            config = AgentConfig(role=role, model=model)
            agents.append(AgentClass(config, pol))

        return cls(agents=agents, policy=pol)

    # ── Universal MeshNode workflow entry point ───────────────────────────────

    async def run_workflow(
        self,
        workflow: "WorkflowDefinition",
        task: str = "",
        ledger_db: str = ":memory:",
        event_bus: WorkflowEventBus | None = None,
    ) -> "WorkflowResult":
        """Run a WorkflowDefinition through the StepRuntime governance kernel.

        Every node — regardless of kind — passes through the full governed path.
        Pass an ``event_bus`` to receive structured WorkflowEvents as the run
        progresses (useful for dashboards, CLI watch, and SSE endpoints).

        Usage::

            wf = (WorkflowDefinition("my_pipeline")
                  .add_node(MeshNode.from_crewai("research", crew))
                  .add_node(MeshNode.from_langgraph("validate", graph))
                  .add_edge("research", "validate"))

            bus = WorkflowEventBus()
            result = await Mesh(policy=Policy(budget_usd=2.0)).run_workflow(
                wf, "analyse Q2", event_bus=bus
            )
        """
        pol = (
            workflow.policy
            if workflow.policy.budget_usd < self._policy.budget_usd
            else self._policy
        )
        run_id = str(uuid.uuid4())

        ledger = ReplayLedger(ledger_db)

        # Zero Trust — Foundation tier active by default on every workflow run
        _wf_zero_trust = None
        try:
            from meshflow.zero_trust.orchestrator import ZeroTrustOrchestrator
            from meshflow.zero_trust.policy import ZeroTrustTier
            _wf_zero_trust = ZeroTrustOrchestrator.for_tier(ZeroTrustTier.FOUNDATION)
        except Exception:
            pass

        runtime = StepRuntime(
            policy=pol,
            run_id=run_id,
            guardian=Guardian(budget_usd=pol.budget_usd) if pol.enable_guardian else None,
            dasc_gate=DascGate(pol, run_id) if pol.deterministic_gate else None,
            identity=AgentIdentityProvider(run_id),
            uncertainty=UncertaintyEngine() if pol.enable_uncertainty else None,
            collusion=CollusionAuditor() if pol.enable_collusion_audit else None,
            telemetry=self._new_tracer(),
            eco=EnvironmentalOptimizer(pol.carbon_budget_g) if pol.enable_environmental else None,
            ledger=ledger,
            budget=BudgetTracker(pol),
            zero_trust=_wf_zero_trust,
        )

        return await workflow.run(task or f"Execute {workflow.name}", runtime, event_bus=event_bus)

    async def resume_workflow(
        self,
        workflow: "WorkflowDefinition",
        run_id: str,
        decision: "HumanDecision",
        ledger_db: str = ":memory:",
    ) -> "WorkflowResult":
        """Resume a workflow that paused at a human approval gate.

        Loads the checkpoint from ``ledger_db``, injects the human's decision,
        and continues execution through the governance kernel.

        Usage::

            # Original run paused
            result = await Mesh().run_workflow(wf, task="...", ledger_db="runs.db")
            # result.paused_nodes == ["approval"]

            # Human decides
            from meshflow import HumanDecision
            result = await Mesh().resume_workflow(
                wf,
                run_id=result.run_id,
                decision=HumanDecision(approved=True, comment="Reviewed and approved"),
                ledger_db="runs.db",
            )
            assert result.completed is True
        """
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.workflow import HumanDecision as _HD  # noqa: F401 type ref

        ledger = ReplayLedger(ledger_db)

        # Load checkpoint to get the original run's policy and run_id
        checkpoint = await ledger.load_checkpoint_data(run_id)
        if checkpoint is None:
            raise ValueError(f"No paused workflow found for run_id={run_id!r}")

        pol = (
            workflow.policy
            if workflow.policy.budget_usd < self._policy.budget_usd
            else self._policy
        )

        runtime = StepRuntime(
            policy=pol,
            run_id=run_id,  # preserve original run_id for ledger continuity
            guardian=Guardian(budget_usd=pol.budget_usd) if pol.enable_guardian else None,
            dasc_gate=DascGate(pol, run_id) if pol.deterministic_gate else None,
            identity=AgentIdentityProvider(run_id),
            uncertainty=UncertaintyEngine() if pol.enable_uncertainty else None,
            collusion=CollusionAuditor() if pol.enable_collusion_audit else None,
            telemetry=self._new_tracer(),
            eco=EnvironmentalOptimizer(pol.carbon_budget_g) if pol.enable_environmental else None,
            ledger=ledger,
            budget=BudgetTracker(pol),
        )

        return await workflow.resume(run_id, decision, ledger, runtime)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _new_tracer(self) -> MeshFlowTracer:
        return MeshFlowTracer(
            export_to_console=self._telemetry_console,
            otlp_endpoint=self._telemetry_otlp_endpoint,
            otlp_protocol=self._telemetry_otlp_protocol,
        )

    def _build_agents(self, pol: Policy) -> list[BaseAgent]:
        if self._custom_agents:
            return list(self._custom_agents)
        return [
            PlannerAgent(
                AgentConfig(role=AgentRole.PLANNER, model=pol.model_tier_map[AgentRole.PLANNER]),
                pol,
            ),
            ResearcherAgent(
                AgentConfig(
                    role=AgentRole.RESEARCHER, model=pol.model_tier_map[AgentRole.RESEARCHER]
                ),
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

    def _build_pipeline(self, agents: list[BaseAgent], pol: Policy) -> list[BaseAgent]:
        """Ordered pipeline: Planner → Researcher → Executor → Critic."""
        role_order = [
            AgentRole.PLANNER,
            AgentRole.RESEARCHER,
            AgentRole.EXECUTOR,
            AgentRole.CRITIC,
        ]
        ordered = []
        agent_by_role = {a.role: a for a in agents}
        for role in role_order:
            if role in agent_by_role:
                ordered.append(agent_by_role[role])
        # Any custom agents not in the role order go at the end
        known = set(agent_by_role.values())
        for agent in agents:
            if agent not in known:
                ordered.append(agent)
        return ordered

    def _capabilities_for_role(self, role: AgentRole) -> list[str]:
        caps = {
            AgentRole.ORCHESTRATOR: ["plan", "delegate", "evaluate", "terminate"],
            AgentRole.PLANNER: ["plan", "decompose"],
            AgentRole.RESEARCHER: ["search", "read", "synthesize"],
            AgentRole.EXECUTOR: ["write", "compute", "call_tool"],
            AgentRole.CRITIC: ["evaluate", "score"],
            AgentRole.GUARDIAN: ["monitor", "block", "alert"],
        }
        return caps.get(role, ["read"])

    def _extract_output(self, ctx: dict[str, Any]) -> Any:
        for key in ("execution_result", "research", "plan"):
            if key in ctx:
                return ctx[key]
        return {k: v for k, v in ctx.items() if not k.startswith("_")}

    def _empty_result(self, task: str, events: list[MeshEvent]) -> RunResult:
        run_id = events[0].run_id if events else str(uuid.uuid4())
        return RunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            output=None,
            agent_states={},
            total_cost_usd=sum(e.cost_usd for e in events),
            total_tokens=sum(e.tokens for e in events),
            total_carbon_g=0.0,
            duration_s=0.0,
            checkpoints=[],
            ledger_entries=0,
            trace_id=run_id,
        )
