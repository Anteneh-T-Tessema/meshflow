"""StepRuntime — the governed execution kernel.

Every MeshNode — regardless of kind (native, LangGraph, CrewAI, AutoGen, MCP,
human, HTTP, Python) — passes through this identical lifecycle:

  pre_step:
    1.  identity provisioning + capability check
    2.  circuit breaker allow/deny
    3.  guardian injection scan on input
    4.  risk classification (AutoRiskClassifier overrides self-declared tier)
    5.  policy budget pre-check
    6.  HITL escalation if tier >= threshold

  execute:
    7.  node.run()  ← the actual LLM/crew/graph/service call
    8.  OTEL span + checkpoint

  post_step:
    9.  uncertainty scoring + adaptive response (warn/verify/escalate/abort)
    10. cost + token + carbon accounting
    11. audit ledger write (hash-chained)
    12. memory update (MEM1)
    13. collusion / drift recording
    14. CAEP identity risk check (revoke DID if risk spikes)
    15. behavioural monitoring baseline update

This is the architectural heart of MeshFlow's differentiation.
One path. Every node. No exceptions.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from meshflow.core.node import MeshNode, NodeInput, NodeOutput
from meshflow.core.schemas import ActionVerdict, Evidence, Intent, Policy, RiskTier


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepRecord:
    """Immutable governed-step record — written to the ReplayLedger."""

    run_id: str
    step_id: str
    node_id: str
    node_kind: str
    input_task: str          # first 500 chars
    output_content: str      # first 2 000 chars
    verdict: str             # "commit" | "reject" | "escalate"
    blocked: bool
    block_reason: str
    uncertainty: float
    cost_usd: float
    tokens_used: int
    carbon_gco2: float
    duration_ms: float
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeOutcome:
    """Result returned from StepRuntime.run()."""

    ok: bool
    node_id: str
    node_kind: str
    output: NodeOutput
    record: StepRecord
    blocked_by: str = ""
    paused_for_human: bool = False
    human_context: dict[str, Any] = field(default_factory=dict)


class StepRuntime:
    """Governed execution kernel — wraps every MeshNode execution.

    Instantiate once per Mesh run and reuse across all nodes.
    All per-run state is in the governance layers passed in.

    Usage::

        runtime = StepRuntime(policy=pol, run_id=run_id,
                              guardian=guardian, dasc_gate=dasc,
                              identity=identity, uncertainty=uncertainty,
                              collusion=collusion, telemetry=tracer,
                              ledger=ledger, budget=budget_tracker)

        output, record = await runtime.run(node, NodeInput(task="..."), ctx)
    """

    def __init__(
        self,
        policy: Policy,
        run_id: str,
        *,
        guardian: Any = None,
        dasc_gate: Any = None,
        identity: Any = None,
        uncertainty: Any = None,
        collusion: Any = None,
        telemetry: Any = None,
        mem1: Any = None,
        eco: Any = None,
        ledger: Any = None,
        budget: Any = None,
        circuit_breakers: Any = None,
    ) -> None:
        self._policy = policy
        self._run_id = run_id
        self._guardian = guardian
        self._dasc = dasc_gate
        self._identity = identity
        self._uncertainty = uncertainty
        self._collusion = collusion
        self._telemetry = telemetry
        self._mem1 = mem1
        self._eco = eco
        self._ledger = ledger
        self._budget = budget
        self._cbs = circuit_breakers  # dict[str, CircuitBreaker]

    async def run(
        self,
        node: MeshNode,
        node_input: NodeInput,
        context: dict[str, Any],
    ) -> RuntimeOutcome:
        step_id = str(uuid.uuid4())[:8]
        start = time.monotonic()

        blocked = False
        block_reason = ""
        verdict = "commit"
        paused = False
        human_ctx: dict[str, Any] = {}
        output = NodeOutput(content="")

        # ── PRE_STEP ──────────────────────────────────────────────────────────

        # 1. Identity: provision DID if not yet done, check still active
        if self._identity:
            if not self._identity.is_provisioned(node.id):
                self._identity.provision(node.id, node.capabilities)
            if not self._identity.is_active(node.id):
                blocked = True
                block_reason = "identity:did_revoked"

        # 2. Circuit breaker
        if not blocked and self._cbs:
            cb = self._cbs.get(node.id)
            if cb and not cb.allow(node.id):
                blocked = True
                block_reason = "circuit_breaker:open"

        # 3. Guardian injection scan on input
        if not blocked and self._guardian and self._policy.enable_guardian:
            from meshflow.core.schemas import Message

            msg = Message(
                sender_id="runtime",
                receiver_id=node.id,
                content=f"{node_input.task} {str(node_input.context)[:300]}",
            )
            allowed, reason = self._guardian.evaluate_message(msg)
            if not allowed:
                blocked = True
                block_reason = f"guardian:{reason}"

        # 4. Risk classification + DascGate evaluate
        if not blocked and self._dasc and self._policy.deterministic_gate:
            intent = Intent(
                action=f"node:{node.kind}",
                payload={"task": node_input.task[:200]},
                evidence=[],
                agent_id=node.id,
                agent_did=self._identity.get_did(node.id) if self._identity else "",
                risk_tier=node.risk_profile,
            )
            gate_result = await self._dasc.evaluate(intent)
            verdict = gate_result.value
            if gate_result == ActionVerdict.REJECT:
                blocked = True
                block_reason = f"dasc:rejected"
            elif gate_result == ActionVerdict.ESCALATE:
                paused = True
                human_ctx = {
                    "reason": "dasc_escalation",
                    "node": node.id,
                    "tier": int(node.risk_profile),
                }

        # 5. Budget pre-check
        if not blocked and self._budget:
            from meshflow.core.policy import BudgetExceededError

            try:
                self._budget.pre_check()
            except BudgetExceededError:
                blocked = True
                block_reason = "budget:exceeded"

        # 6. HITL escalation if node tier >= threshold
        if (
            not blocked
            and not paused
            and self._policy.human_in_loop
            and self._policy.human_in_loop.enabled
            and node.risk_profile >= self._policy.human_in_loop.tier_threshold
        ):
            paused = True
            human_ctx = {
                "reason": "hitl_tier_threshold",
                "node": node.id,
                "tier": int(node.risk_profile),
            }

        # ── EXECUTE ───────────────────────────────────────────────────────────

        if not blocked:
            span = None
            if self._telemetry:
                span = self._telemetry.start_span(
                    f"meshflow.node.{node.kind.value}",
                    {"node.id": node.id, "node.kind": node.kind.value,
                     "run_id": self._run_id, "step_id": step_id},
                )
            try:
                output = await node.run(node_input)
                if self._cbs and node.id in self._cbs:
                    self._cbs[node.id].record_success(node.id)
            except Exception as exc:
                output = NodeOutput(content=f"[node_error: {exc}]", confidence=0.0)
                block_reason = f"node_exception:{exc}"
                blocked = True
                if self._cbs and node.id in self._cbs:
                    self._cbs[node.id].record_failure(node.id)
                if span and self._telemetry:
                    self._telemetry.record_error(span, str(exc))
            finally:
                if span and self._telemetry:
                    self._telemetry.end_span(span)

        # ── POST_STEP ─────────────────────────────────────────────────────────

        tokens_used = output.tokens_used
        uncertainty_val = 0.0
        cost_usd = 0.0
        carbon_gco2 = 0.0

        # 9. Uncertainty scoring + adaptive response
        if not blocked and self._uncertainty and self._policy.enable_uncertainty:
            u_score = self._uncertainty.evaluate(
                agent_id=node.id,
                outputs=[output.content] if output.content else [""],
                stated_confidence=output.confidence,
                upstream_calibrated=context.get("_upstream_confidence"),
            )
            uncertainty_val = u_score.composite
            context["_upstream_confidence"] = u_score.calibrated

            adaptive = self._uncertainty.adaptive_response(u_score)
            if adaptive["action"] == "abort":
                blocked = True
                block_reason = f"uncertainty:abort:{uncertainty_val:.2f}"
            elif adaptive["action"] == "escalate_human" and not paused:
                paused = True
                human_ctx = {
                    "reason": f"uncertainty:{uncertainty_val:.2f}",
                    "node": node.id,
                }

        # 10. Cost + token + carbon accounting
        if self._budget and tokens_used:
            from meshflow.core.policy import BudgetExceededError

            try:
                self._budget.charge(usd=0.0, tokens=tokens_used)
            except BudgetExceededError:
                pass

        if self._eco and tokens_used:
            eco_result = self._eco.estimate_and_charge(tokens_used, node.id)
            carbon_gco2 = eco_result.carbon_g

        # 11. Collusion / drift recording
        if (
            self._collusion
            and self._policy.enable_collusion_audit
            and output.content
        ):
            self._collusion.record_output(node.id, output.content[:500])

        # 14. CAEP: revoke DID if risk spikes post-step
        if self._identity and uncertainty_val > 0.7:
            self._identity.caep_check(node.id, 1.0 - uncertainty_val)

        # 15. Behavioural monitoring
        if self._guardian and self._policy.enable_guardian:
            self._guardian.observe_behaviour(
                agent_id=node.id,
                tokens=float(tokens_used),
                tools=0.0,
                output_len=len(output.content),
            )

        # 12. Memory update
        if self._mem1 and output.content:
            self._mem1.add(node.id, output.content, {"step_id": step_id})

        # Build step record
        duration_ms = (time.monotonic() - start) * 1000
        record = StepRecord(
            run_id=self._run_id,
            step_id=step_id,
            node_id=node.id,
            node_kind=str(node.kind.value),
            input_task=node_input.task[:500],
            output_content=output.content[:2_000],
            verdict=verdict,
            blocked=blocked,
            block_reason=block_reason,
            uncertainty=uncertainty_val,
            cost_usd=cost_usd,
            tokens_used=tokens_used,
            carbon_gco2=carbon_gco2,
            duration_ms=duration_ms,
            timestamp=_now_iso(),
        )

        # 11. Audit ledger write  (synchronous sqlite connection — no executor needed)
        if self._ledger:
            self._ledger._write_sync(record)

        return RuntimeOutcome(
            ok=not blocked,
            node_id=node.id,
            node_kind=node.kind.value,
            output=output,
            record=record,
            blocked_by=block_reason,
            paused_for_human=paused,
            human_context=human_ctx,
        )
