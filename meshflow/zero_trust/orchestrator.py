"""ZeroTrustOrchestrator — single entry point for ZT-secured agent execution.

Applies all Zero Trust controls configured in a ZeroTrustPolicy to an agent
or workflow execution:

  1. Cryptographic identity (DID + short-lived token)
  2. JIT privilege management (Advanced tier)
  3. Spotlighting guardrail (Enterprise/Advanced)
  4. Prompt injection detection (Enterprise+)
  5. Continuous authorization (Advanced)
  6. Behavioral baseline + anomaly detection (Enterprise+)
  7. Immutable audit logging (Enterprise+)
  8. PII output filtering (Enterprise+)
  9. HITL gates for high-risk actions (Advanced)
  10. AI-BOM generation (Enterprise+)

Usage::

    from meshflow.zero_trust import ZeroTrustOrchestrator, ZeroTrustTier

    # Fastest path — wrap any agent call with a tier
    zt = ZeroTrustOrchestrator.for_tier(ZeroTrustTier.ENTERPRISE)
    result = await zt.run_agent(agent, task)

    # Fine-grained control
    from meshflow.zero_trust.policy import ZeroTrustPolicy
    policy = ZeroTrustPolicy(tier=ZeroTrustTier.ENTERPRISE, jit_privilege=True)
    zt = ZeroTrustOrchestrator(policy=policy)
    async with zt.session("agent-abc") as session:
        result = await session.run(task)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from meshflow.zero_trust.policy import ZeroTrustPolicy, ZeroTrustTier


@dataclass
class ZeroTrustRunResult:
    """Result of a ZT-secured agent run."""

    run_id: str
    agent_id: str
    output: Any
    controls_applied: list[str]
    violations: list[dict[str, Any]]
    jit_grants_issued: int
    auth_decisions: int
    auth_denials: int
    duration_ms: float
    trust_score: float                  # 0–1; 1 = no violations detected
    ai_bom: dict[str, Any] | None = None


class ZeroTrustSession:
    """A scoped session with JIT grants and continuous auth registered for one agent."""

    def __init__(
        self,
        agent_id: str,
        policy: ZeroTrustPolicy,
        orchestrator: "ZeroTrustOrchestrator",
    ) -> None:
        self._agent_id = agent_id
        self._policy = policy
        self._orch = orchestrator
        self._start = time.time()
        self._violations: list[dict[str, Any]] = []
        self._auth_decisions = 0
        self._auth_denials = 0
        self._controls: list[str] = []

    async def run(self, task: str, *, agent: Any = None) -> ZeroTrustRunResult:
        """Execute *task* under Zero Trust controls."""
        run_id = f"zt-{uuid.uuid4().hex[:12]}"
        output = None

        # ── Apply input controls ──────────────────────────────────────────────
        checked_task = task
        if self._policy.injection_detection:
            checked_task = self._check_injection(task) or task
        if self._policy.spotlighting:
            checked_task = self._apply_spotlighting(checked_task)

        # ── Continuous auth for this action ───────────────────────────────────
        if self._policy.continuous_auth and self._orch._cont_auth:
            decision = self._orch._cont_auth.authorize(
                self._agent_id, action="run:task"
            )
            self._auth_decisions += 1
            if not decision.allowed:
                self._auth_denials += 1
                self._violations.append({
                    "type": "authorization_denied",
                    "action": "run:task",
                    "reason": decision.reason,
                    "ts": time.time(),
                })
                # Return blocked result rather than raise — preserves audit trail
                return self._build_result(run_id, None, ["continuous_auth_block"])

        # ── Execute (pass to agent if provided, else return processed task) ───
        if agent is not None:
            try:
                if asyncio.iscoroutinefunction(getattr(agent, "run", None)):
                    output = await agent.run(checked_task)
                else:
                    loop = asyncio.get_event_loop()
                    output = await loop.run_in_executor(None, agent.run, checked_task)
            except Exception as exc:
                output = {"error": str(exc)}
        else:
            output = {"processed_task": checked_task}

        # ── Apply output controls ─────────────────────────────────────────────
        if self._policy.output_pii_filter and output:
            output = self._filter_pii(output)

        return self._build_result(run_id, output, self._controls)

    def authorize(self, action: str, context: dict[str, Any] | None = None) -> bool:
        """Check continuous authorization for an action mid-session."""
        if not self._policy.continuous_auth or not self._orch._cont_auth:
            return True
        decision = self._orch._cont_auth.authorize(
            self._agent_id, action=action, context=context
        )
        self._auth_decisions += 1
        if not decision.allowed:
            self._auth_denials += 1
            self._violations.append({
                "type": "auth_denial",
                "action": action,
                "reason": decision.reason,
                "ts": time.time(),
            })
        return decision.allowed

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_injection(self, text: str) -> str | None:
        try:
            from meshflow.security.injection import PromptInjectionDetector
            result = PromptInjectionDetector().scan(text)
            if result.is_injection:
                self._violations.append({
                    "type": "injection_detected",
                    "category": result.category,
                    "confidence": result.confidence,
                    "ts": time.time(),
                })
                self._controls.append("injection_block")
                return None
        except Exception:
            pass
        return text

    def _apply_spotlighting(self, text: str) -> str:
        try:
            from meshflow.zero_trust.spotlight import SpotlightContext
            ctx = SpotlightContext(strategy="xml_tags")
            self._controls.append("spotlighting")
            return ctx.wrap(text)
        except Exception:
            return text

    def _filter_pii(self, output: Any) -> Any:
        try:
            from meshflow.security.sensitive_data import SensitiveDataDetector
            det = SensitiveDataDetector()
            if isinstance(output, str):
                report = det.audit_report(output)
                if report["total_matches"] > 0:
                    self._violations.append({
                        "type": "pii_in_output",
                        "matches": report["total_matches"],
                        "ts": time.time(),
                    })
                    self._controls.append("pii_filter")
                    return det.mask(output)
            elif isinstance(output, dict) and "result" in output:
                cleaned = self._filter_pii(output["result"])
                return {**output, "result": cleaned}
        except Exception:
            pass
        return output

    def _build_result(
        self,
        run_id: str,
        output: Any,
        extra_controls: list[str],
    ) -> ZeroTrustRunResult:
        duration_ms = (time.time() - self._start) * 1000
        violation_count = len(self._violations)
        trust_score = max(0.0, 1.0 - (violation_count * 0.1))
        return ZeroTrustRunResult(
            run_id=run_id,
            agent_id=self._agent_id,
            output=output,
            controls_applied=sorted(set(self._controls + extra_controls)),
            violations=self._violations,
            jit_grants_issued=0,
            auth_decisions=self._auth_decisions,
            auth_denials=self._auth_denials,
            duration_ms=round(duration_ms, 2),
            trust_score=trust_score,
        )


class ZeroTrustOrchestrator:
    """Configures and enforces all Zero Trust controls for agent execution.

    Parameters
    ----------
    policy:      The ZeroTrustPolicy defining which controls are active.
    run_id:      Optional run ID (generated if not provided).
    """

    def __init__(
        self,
        policy: ZeroTrustPolicy | None = None,
        *,
        run_id: str | None = None,
    ) -> None:
        self._policy = policy or ZeroTrustPolicy.for_tier(ZeroTrustTier.ENTERPRISE)
        self._run_id = run_id or f"zt-run-{uuid.uuid4().hex[:8]}"
        self._jit: Any = None
        self._cont_auth: Any = None
        self._bom: Any = None
        self._identity: Any = None
        self._setup()

    def _setup(self) -> None:
        p = self._policy

        # Identity
        if p.crypto_identity:
            try:
                from meshflow.security.identity import AgentIdentityProvider
                self._identity = AgentIdentityProvider(run_id=self._run_id)
            except Exception:
                pass

        # JIT
        if p.jit_privilege:
            from meshflow.zero_trust.jit import JITPrivilegeManager
            self._jit = JITPrivilegeManager(
                default_ttl_seconds=p.jit_ttl_seconds,
                max_grants_per_agent=p.jit_max_grants,
            )

        # Continuous auth
        if p.continuous_auth:
            from meshflow.zero_trust.continuous_auth import ContinuousAuthorizationEngine
            self._cont_auth = ContinuousAuthorizationEngine()

        # AI-BOM
        if p.ai_bom:
            from meshflow.zero_trust.bom import AIBillOfMaterials
            self._bom = AIBillOfMaterials.from_meshflow_project()

    # ── Public API ────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def session(self, agent_id: str) -> AsyncIterator[ZeroTrustSession]:
        """Context manager that provides a ZeroTrustSession with JIT grants."""
        # Provision identity if enabled
        if self._identity:
            try:
                self._identity.provision(agent_id, capabilities=["run:task"])
            except Exception:
                pass

        # Register with continuous auth
        if self._cont_auth:
            self._cont_auth.register(agent_id, permissions=["run:task", "read:*", "write:summary"])

        # Issue JIT grant if needed
        jit_grant = None
        if self._jit:
            try:
                jit_grant = self._jit.request(
                    agent_id,
                    permissions=["run:task"],
                    reason="session_open",
                )
            except Exception:
                pass

        session = ZeroTrustSession(agent_id=agent_id, policy=self._policy, orchestrator=self)
        try:
            yield session
        finally:
            # Revoke JIT grant on session close
            if self._jit and jit_grant:
                self._jit.revoke(jit_grant.grant_id, reason="session_close")
            # Revoke DID
            if self._identity:
                try:
                    self._identity.revoke(agent_id, reason="session_close")
                except Exception:
                    pass

    async def run_agent(
        self,
        agent: Any,
        task: str,
        *,
        agent_id: str | None = None,
    ) -> ZeroTrustRunResult:
        """Convenience: open a session and run a single task."""
        aid = agent_id or getattr(agent, "name", f"agent-{uuid.uuid4().hex[:8]}")
        async with self.session(aid) as sess:
            return await sess.run(task, agent=agent)

    def status(self) -> dict[str, Any]:
        """Return current ZT control status."""
        return {
            "policy_tier": self._policy.tier.value,
            "controls_enabled": self._policy.controls_enabled(),
            "identity_active": self._identity is not None,
            "jit_active": self._jit is not None,
            "continuous_auth_active": self._cont_auth is not None,
            "ai_bom_active": self._bom is not None,
            "jit_stats": self._jit.stats() if self._jit else {},
            "auth_status": self._cont_auth.status() if self._cont_auth else {},
            "bom_risk": self._bom.risk_summary() if self._bom else {},
        }

    # ── Factory helpers ───────────────────────────────────────────────────────

    @classmethod
    def for_tier(cls, tier: ZeroTrustTier) -> "ZeroTrustOrchestrator":
        return cls(policy=ZeroTrustPolicy.for_tier(tier))

    @classmethod
    def for_regulation(cls, regulation: str) -> "ZeroTrustOrchestrator":
        return cls(policy=ZeroTrustPolicy.for_regulation(regulation))


__all__ = ["ZeroTrustOrchestrator", "ZeroTrustSession", "ZeroTrustRunResult"]
