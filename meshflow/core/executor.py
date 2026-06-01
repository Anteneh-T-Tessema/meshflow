"""GovernedStepExecutor — the heart of MeshFlow's differentiation.

Every single agent step passes through every governance layer in strict order.
This is what makes MeshFlow a governed runtime, not just an orchestration library.

Order of operations per step:
  1.  Identity check          — is this agent's DID still active?
  2.  Circuit breaker         — is this agent allowed to run?
  3.  Guardian message scan   — is the incoming context clean?
  4.  Budget pre-check        — does this step fit in remaining budget?
  5.  Agent.step()            — the actual LLM call
  6.  Uncertainty evaluation  — calibrated confidence + propagation
  7.  Adaptive response       — escalate/pause/abort if confidence too low
  8.  Intent evaluation       — Guardian + DascGate on any declared action
  9.  CAEP risk check         — revoke DID if risk spikes post-step
  10. Collusion recording     — log output for cross-agent pattern analysis
  11. Policy post-step        — charge budget, record reliability
  12. Telemetry span          — emit OTEL span
  13. Environmental cost      — charge carbon/water budget
  14. Behavioural monitoring  — update baseline for anomaly detection
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from meshflow.agents.base import BaseAgent
from meshflow.core.policy import BudgetExceededError, CircuitOpenError, PolicyEngine
from meshflow.core.schemas import (
    ActionVerdict,
    Evidence,
    Intent,
    Message,
    UncertaintyScore,
)
from meshflow.efficiency.environmental import EnvironmentalOptimizer
from meshflow.intelligence.collusion import CollusionAuditor
from meshflow.intelligence.uncertainty import UncertaintyEngine
from meshflow.observability.telemetry import MeshFlowTracer
from meshflow.security.dasc_gate import DascGate
from meshflow.security.guardian import Guardian
from meshflow.security.identity import AgentIdentityProvider


@dataclass
class StepOutcome:
    """Result of a governed agent step."""

    ok: bool
    data: dict[str, Any]
    agent_id: str
    role: str
    tokens: int = 0
    cost_usd: float = 0.0
    carbon_g: float = 0.0
    duration_ms: float = 0.0
    uncertainty: UncertaintyScore | None = None
    blocked_by: str = ""  # which layer blocked this step
    paused_for_human: bool = False
    human_context: dict[str, Any] | None = None
    dasc_verdict: str = "commit"
    collusion_alerts: int = 0


class GovernedStepExecutor:
    """Executes one agent step through all governance layers.

    This is instantiated once per run and shared across all agents.
    All state per-run is held in the layers passed in, not here.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        identity: AgentIdentityProvider,
        guardian: Guardian,
        dasc_gate: DascGate,
        uncertainty: UncertaintyEngine,
        collusion: CollusionAuditor,
        telemetry: MeshFlowTracer,
        eco: EnvironmentalOptimizer | None,
        run_id: str,
        trace_id: str,
        zero_trust: Any = None,
    ) -> None:
        self._policy = policy_engine
        self._identity = identity
        self._guardian = guardian
        self._dasc = dasc_gate
        self._uncertainty = uncertainty
        self._collusion = collusion
        self._telemetry = telemetry
        self._eco = eco
        self._run_id = run_id
        self._trace_id = trace_id
        self._zero_trust = zero_trust  # ZeroTrustOrchestrator | None

    async def execute(
        self,
        agent: BaseAgent,
        task: str,
        context: dict[str, Any],
    ) -> StepOutcome:
        start = time.monotonic()

        # ── 1. Identity check ─────────────────────────────────────────────────
        if not self._identity.is_active(agent.agent_id):
            return StepOutcome(
                ok=False,
                data={},
                agent_id=agent.agent_id,
                role=agent.role.value,
                blocked_by="identity",
                human_context={"reason": "DID revoked"},
            )

        # ── 1b. Zero Trust — continuous authorization + input spotlighting ────
        if self._zero_trust is not None:
            try:
                zt_policy = getattr(self._zero_trust, "_policy", None)
                # Continuous authorization
                if zt_policy and getattr(zt_policy, "continuous_auth", False):
                    cont_auth = getattr(self._zero_trust, "_cont_auth", None)
                    if cont_auth:
                        decision = cont_auth.authorize(agent.agent_id, action="run:step")
                        if not decision.allowed:
                            return StepOutcome(
                                ok=False,
                                data={},
                                agent_id=agent.agent_id,
                                role=agent.role.value,
                                blocked_by=f"zero_trust:auth:{decision.reason}",
                            )
                # Spotlighting on task input
                if zt_policy and getattr(zt_policy, "spotlighting", False):
                    from meshflow.zero_trust.spotlight import SpotlightContext
                    task = SpotlightContext(strategy="xml_tags").wrap(task)
            except Exception:
                pass  # ZT must never break execution

        # ── 2. Circuit breaker ────────────────────────────────────────────────
        try:
            self._policy.pre_step(agent.agent_id)
        except (CircuitOpenError, BudgetExceededError):
            return StepOutcome(
                ok=False,
                data={},
                agent_id=agent.agent_id,
                role=agent.role.value,
                blocked_by="circuit_breaker",
            )

        # ── 3. Guardian — scan incoming context for injection ─────────────────
        context_text = f"{task} {str(context)[:500]}"
        probe_msg = Message(
            sender_id="orchestrator",
            receiver_id=agent.agent_id,
            content=context_text,
        )
        allowed, block_reason = self._guardian.evaluate_message(probe_msg)
        if not allowed:
            return StepOutcome(
                ok=False,
                data={},
                agent_id=agent.agent_id,
                role=agent.role.value,
                blocked_by=f"guardian:{block_reason}",
            )

        # ── 5. Agent step ─────────────────────────────────────────────────────
        try:
            result = await agent.step(task, context)
            success = True
        except Exception as e:
            result = {"error": str(e)}
            success = False

        duration_ms = (time.monotonic() - start) * 1000
        tokens = result.get("tokens", 0)
        cost_usd = result.get("cost_usd", 0.0)

        # ── 6. Uncertainty evaluation ─────────────────────────────────────────
        output_text = str(
            result.get("execution_result", result.get("research", result.get("plan", "")))
        )
        upstream_conf: float | None = context.get("_upstream_confidence")

        uncertainty_score = self._uncertainty.evaluate(
            agent_id=agent.agent_id,
            outputs=[output_text] if output_text else [""],
            stated_confidence=float(result.get("stated_confidence", 0.80)),
            upstream_calibrated=upstream_conf,
        )
        result["_uncertainty_score"] = uncertainty_score.composite
        result["_upstream_confidence"] = uncertainty_score.calibrated

        # ── 7. Adaptive response ──────────────────────────────────────────────
        adaptive = self._uncertainty.adaptive_response(uncertainty_score)
        paused = False
        human_ctx: dict[str, Any] | None = None

        if adaptive["action"] == "abort":
            self._record_telemetry(agent, tokens, cost_usd, duration_ms, False)
            return StepOutcome(
                ok=False,
                data=result,
                agent_id=agent.agent_id,
                role=agent.role.value,
                blocked_by=f"uncertainty_abort:{adaptive['reason']}",
                uncertainty=uncertainty_score,
            )
        if adaptive["action"] == "escalate_human":
            paused = True
            human_ctx = {
                "reason": adaptive["reason"],
                "agent": agent.agent_id,
                "score": uncertainty_score.composite,
            }
            result["__pause_for_human__"] = True
            result["__human_context__"] = human_ctx

        # ── 8. Intent evaluation (DascGate) ───────────────────────────────────
        # Evaluate for every step using the agent's role as the intent action.
        # Explicit _intent_action from the result overrides the default.
        dasc_verdict = "commit"
        intent_action = result.get("_intent_action") or f"agent_step:{agent.role.value}"
        if self._policy.policy.deterministic_gate:
            evidence = result.get("evidence", [])
            intent = Intent(
                action=intent_action,
                payload=result.get("_intent_payload", {}),
                evidence=evidence if isinstance(evidence, list) else [],
                agent_id=agent.agent_id,
                agent_did=agent.state.did,
                tainted=any(
                    isinstance(e, Evidence) and e.trust_level == "untrusted"
                    for e in (evidence if isinstance(evidence, list) else [])
                ),
            )
            guardian_ok, _ = self._guardian.evaluate_intent(intent, result.get("_tools_used", []))
            if not guardian_ok:
                self._record_telemetry(agent, tokens, cost_usd, duration_ms, False)
                return StepOutcome(
                    ok=False,
                    data=result,
                    agent_id=agent.agent_id,
                    role=agent.role.value,
                    blocked_by=f"guardian_intent:{intent_action}",
                    uncertainty=uncertainty_score,
                )
            verdict = await self._dasc.evaluate(intent)
            dasc_verdict = verdict.value
            if verdict == ActionVerdict.REJECT:
                self._record_telemetry(agent, tokens, cost_usd, duration_ms, False)
                return StepOutcome(
                    ok=False,
                    data=result,
                    agent_id=agent.agent_id,
                    role=agent.role.value,
                    blocked_by=f"dasc_reject:{intent_action}",
                    uncertainty=uncertainty_score,
                    dasc_verdict=dasc_verdict,
                )
            if verdict == ActionVerdict.ESCALATE and not paused:
                paused = True
                human_ctx = {"intent": intent_action, "tier": int(intent.effective_tier)}
                result["__pause_for_human__"] = True
                result["__human_context__"] = human_ctx

        # ── 9. CAEP risk check ────────────────────────────────────────────────
        risk_score = 1.0 - uncertainty_score.composite
        agent.state.risk_score = risk_score
        self._identity.caep_check(agent.agent_id, risk_score)

        # ── 10. Collusion recording ───────────────────────────────────────────
        if self._policy.policy.enable_collusion_audit:
            self._collusion.record_output(agent.agent_id, output_text[:500])

        # ── 11. Policy post-step ──────────────────────────────────────────────
        carbon_g = 0.0
        if self._eco:
            eco_cost = self._eco.estimate_and_charge(tokens, agent.config.model)
            carbon_g = eco_cost.carbon_g

        self._policy.post_step(
            agent_id=agent.agent_id,
            success=success,
            usd=cost_usd,
            tokens=tokens,
            carbon_g=carbon_g,
            step_reliability=uncertainty_score.composite,
        )
        self._dasc.record_outcome(agent.agent_id, success)

        # ── 12. Telemetry ─────────────────────────────────────────────────────
        self._record_telemetry(agent, tokens, cost_usd, duration_ms, success)

        # ── 14. Behavioural monitoring ────────────────────────────────────────
        if self._policy.policy.enable_guardian:
            self._guardian.observe_behaviour(
                agent_id=agent.agent_id,
                tokens=float(tokens),
                tools=float(len(result.get("_tools_used", []))),
                output_len=len(output_text),
            )

        return StepOutcome(
            ok=success,
            data=result,
            agent_id=agent.agent_id,
            role=agent.role.value,
            tokens=tokens,
            cost_usd=cost_usd,
            carbon_g=carbon_g,
            duration_ms=duration_ms,
            uncertainty=uncertainty_score,
            paused_for_human=paused,
            human_context=human_ctx,
            dasc_verdict=dasc_verdict,
        )

    def _record_telemetry(
        self,
        agent: BaseAgent,
        tokens: int,
        cost_usd: float,
        duration_ms: float,
        success: bool,
    ) -> None:
        self._telemetry.record_agent_step(
            run_id=self._run_id,
            agent_id=agent.agent_id,
            role=agent.role.value,
            tokens=tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            success=success,
        )
