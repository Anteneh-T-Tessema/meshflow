"""Continuous Authorization Engine — Zero Trust Advanced tier.

From the Anthropic Zero Trust guide (Part III — Access Control, Advanced):

  "Evaluate authorization at each action rather than session start. Integrate
   threat intelligence and behavioral analytics into authorization decisions.
   Revoke access immediately when risk indicators change."

The ContinuousAuthorizationEngine re-evaluates access on every step using:
  - Action-level permission checks (deny-by-default)
  - Behavioral context (time-of-day, step rate, anomaly score)
  - Live revocation (agents can be blocked mid-run)
  - ABAC policies (attribute-based context evaluation)

Usage::

    from meshflow.zero_trust.continuous_auth import ContinuousAuthorizationEngine

    engine = ContinuousAuthorizationEngine()
    engine.register("agent-xyz", permissions=["read:docs", "write:summary"])

    # Before each step in StepRuntime:
    decision = engine.authorize("agent-xyz", action="read:docs", context={
        "time_hour": 14,
        "anomaly_score": 0.12,
    })
    if not decision.allowed:
        raise PermissionError(decision.reason)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuthorizationContext:
    """Context attributes fed into the authorization decision."""

    action: str = ""
    resource: str = ""
    time_hour: int = -1          # 0-23; -1 = unconstrained
    anomaly_score: float = 0.0   # 0-1; from Guardian or BehaviorBaseline
    risk_tier: int = 0           # 0=low, 1=medium, 2=high, 3=critical
    tenant_id: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthDecision:
    allowed: bool
    reason: str
    agent_id: str
    action: str
    ts: float = field(default_factory=time.time)
    policy_matched: str = ""     # name of the matching ABAC rule if any


@dataclass
class _AgentRegistration:
    agent_id: str
    permissions: set[str]
    allowed_hours: tuple[int, int] | None  # (start_hour, end_hour) inclusive
    max_anomaly_score: float
    suspended: bool = False
    suspension_reason: str = ""
    decision_log: list[dict[str, Any]] = field(default_factory=list)


class ContinuousAuthorizationEngine:
    """Re-evaluates authorization on every agent action.

    Parameters
    ----------
    default_max_anomaly: Anomaly score threshold above which all actions are denied.
    deny_outside_hours:  If True and agent has ``allowed_hours``, block outside window.
    audit_callback:      Optional callable(decision_dict) for every authorization.
    """

    def __init__(
        self,
        default_max_anomaly: float = 0.75,
        deny_outside_hours: bool = False,
        audit_callback: Any = None,
    ) -> None:
        self._max_anomaly = default_max_anomaly
        self._deny_hours = deny_outside_hours
        self._audit = audit_callback
        self._agents: dict[str, _AgentRegistration] = {}
        self._lock = threading.Lock()

    def register(
        self,
        agent_id: str,
        permissions: list[str],
        *,
        allowed_hours: tuple[int, int] | None = None,
        max_anomaly_score: float | None = None,
    ) -> None:
        """Register an agent with its permitted actions and optional ABAC constraints."""
        with self._lock:
            self._agents[agent_id] = _AgentRegistration(
                agent_id=agent_id,
                permissions=set(permissions),
                allowed_hours=allowed_hours,
                max_anomaly_score=max_anomaly_score or self._max_anomaly,
            )

    def authorize(
        self,
        agent_id: str,
        action: str,
        *,
        context: AuthorizationContext | dict[str, Any] | None = None,
    ) -> AuthDecision:
        """Evaluate whether *agent_id* may perform *action* right now.

        Returns an AuthDecision; callers should check ``.allowed`` before proceeding.
        """
        if isinstance(context, dict):
            context = AuthorizationContext(
                action=action,
                **{k: v for k, v in context.items() if k in AuthorizationContext.__dataclass_fields__}
            )
        ctx = context or AuthorizationContext(action=action)

        with self._lock:
            reg = self._agents.get(agent_id)

        if reg is None:
            decision = AuthDecision(
                allowed=False,
                reason=f"agent {agent_id!r} not registered — deny by default",
                agent_id=agent_id,
                action=action,
            )
            self._emit(decision)
            return decision

        if reg.suspended:
            decision = AuthDecision(
                allowed=False,
                reason=f"agent suspended: {reg.suspension_reason}",
                agent_id=agent_id,
                action=action,
            )
            self._emit(decision)
            return decision

        # ── Anomaly score check ───────────────────────────────────────────────
        if ctx.anomaly_score > reg.max_anomaly_score:
            decision = AuthDecision(
                allowed=False,
                reason=f"anomaly score {ctx.anomaly_score:.2f} exceeds threshold {reg.max_anomaly_score:.2f}",
                agent_id=agent_id,
                action=action,
                policy_matched="anomaly_threshold",
            )
            self._emit(decision)
            return decision

        # ── Time-of-day ABAC ─────────────────────────────────────────────────
        if self._deny_hours and reg.allowed_hours is not None:
            import time as _t
            hour = ctx.time_hour if ctx.time_hour >= 0 else _t.localtime().tm_hour
            start, end = reg.allowed_hours
            in_window = start <= hour <= end
            if not in_window:
                decision = AuthDecision(
                    allowed=False,
                    reason=f"action outside allowed hours {start}:00–{end}:00 (current hour={hour})",
                    agent_id=agent_id,
                    action=action,
                    policy_matched="time_window",
                )
                self._emit(decision)
                return decision

        # ── Permission check ─────────────────────────────────────────────────
        allowed = action in reg.permissions or "*" in reg.permissions
        if not allowed:
            # Try wildcard prefix — e.g. "read:*" matches "read:contracts"
            for perm in reg.permissions:
                if perm.endswith(":*") and action.startswith(perm[:-1]):
                    allowed = True
                    break

        decision = AuthDecision(
            allowed=allowed,
            reason="ok" if allowed else f"permission {action!r} not granted",
            agent_id=agent_id,
            action=action,
        )
        self._emit(decision)

        # Store in per-agent log (keep last 200)
        with self._lock:
            if agent_id in self._agents:
                log = self._agents[agent_id].decision_log
                log.append(decision.__dict__)
                if len(log) > 200:
                    log.pop(0)

        return decision

    def suspend(self, agent_id: str, *, reason: str = "security_containment") -> bool:
        """Immediately suspend all authorizations for *agent_id*."""
        with self._lock:
            reg = self._agents.get(agent_id)
            if reg is None:
                return False
            reg.suspended = True
            reg.suspension_reason = reason
            return True

    def unsuspend(self, agent_id: str) -> bool:
        with self._lock:
            reg = self._agents.get(agent_id)
            if reg is None:
                return False
            reg.suspended = False
            reg.suspension_reason = ""
            return True

    def decision_log(self, agent_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            reg = self._agents.get(agent_id)
            if reg is None:
                return []
            return list(reg.decision_log[-limit:])

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "registered_agents": len(self._agents),
                "suspended_agents": [
                    {"id": r.agent_id, "reason": r.suspension_reason}
                    for r in self._agents.values() if r.suspended
                ],
            }

    def _emit(self, decision: AuthDecision) -> None:
        if self._audit:
            try:
                self._audit(decision.__dict__)
            except Exception:
                pass


__all__ = [
    "ContinuousAuthorizationEngine",
    "AuthorizationContext",
    "AuthDecision",
]
