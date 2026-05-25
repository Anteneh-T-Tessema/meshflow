"""Sprint 48 — RateLimitGuardrail: per-agent/per-team rate limiting via GuardrailStack."""

from __future__ import annotations

from typing import Optional

from meshflow.security.guardrails import Guardrail, GuardrailResult
from .window import RateLimitPolicy, RateLimitStore, RateLimitResult, get_rate_limit_store


class RateLimitGuardrail(Guardrail):
    """Sliding-window rate limiter that plugs into :class:`GuardrailStack`.

    Checks both request velocity and token throughput before each LLM call.
    Call :meth:`record` after the call completes to debit the window.

    Parameters
    ----------
    agent_name:   Key used for policy lookup (typically the agent name or team slug).
    policy:       :class:`RateLimitPolicy` to enforce.  If *None*, uses the
                  policy already registered in *store* for *agent_name* or ``"*"``.
    store:        Shared :class:`RateLimitStore` (defaults to the global singleton).
    name:         Guardrail name (shows in results and logs).

    Usage::

        policy  = RateLimitPolicy(max_requests=60, max_tokens=100_000, window_s=60)
        guardrail = RateLimitGuardrail("billing-agent", policy=policy)

        result = guardrail.check("Run billing report")
        if result.passed:
            # ... call LLM ...
            guardrail.record(tokens=512)
    """

    def __init__(
        self,
        agent_name: str,
        policy: Optional[RateLimitPolicy] = None,
        store: Optional[RateLimitStore] = None,
        name: str = "rate_limit_guardrail",
    ) -> None:
        super().__init__(action="block", name=name)
        self.agent_name = agent_name
        self._store = store or get_rate_limit_store()
        if policy is not None:
            self._store.set_policy(agent_name, policy)

    def check(self, text: str = "", tokens: int = 0) -> GuardrailResult:  # type: ignore[override]
        """Pre-flight rate limit check.

        Parameters
        ----------
        text:    Ignored (required by Guardrail ABC; policy applies to all calls).
        tokens:  Estimated tokens for this call (used against token-rate limit).
        """
        result: RateLimitResult = self._store.check(self.agent_name, tokens=tokens)
        if result.allowed:
            metadata: dict = result.to_dict()
            if result.near_limit:
                metadata["warning"] = "approaching rate limit"
            return GuardrailResult(
                passed=True,
                guardrail_name=self.name,
                severity="warn" if result.near_limit else "block",
                metadata=metadata,
            )
        return GuardrailResult(
            passed=False,
            guardrail_name=self.name,
            reason=result.reason,
            severity="block",
            metadata=result.to_dict(),
        )

    def record(self, tokens: int = 0) -> None:
        """Debit the sliding window after a successful LLM call."""
        self._store.record(self.agent_name, tokens=tokens)

    def check_and_record(self, tokens: int = 0) -> GuardrailResult:
        """Atomic check + record — use when tokens are known upfront."""
        result: RateLimitResult = self._store.check_and_record(self.agent_name, tokens=tokens)
        if result.allowed:
            return GuardrailResult(
                passed=True,
                guardrail_name=self.name,
                severity="warn" if result.near_limit else "block",
                metadata=result.to_dict(),
            )
        return GuardrailResult(
            passed=False,
            guardrail_name=self.name,
            reason=result.reason,
            severity="block",
            metadata=result.to_dict(),
        )

    def status(self) -> Optional[dict]:
        """Current window usage for this agent."""
        return self._store.status(self.agent_name)

    def reset(self) -> None:
        """Reset the sliding window (e.g. for testing)."""
        self._store.reset(self.agent_name)


class TeamRateLimitGuardrail(RateLimitGuardrail):
    """Rate limiter scoped to a team slug rather than an individual agent.

    All agents sharing the same *team_name* draw from the same window,
    enabling shared-quota enforcement across a team.

    Usage::

        team_policy = RateLimitPolicy(max_requests=300, window_s=60)
        guardrail   = TeamRateLimitGuardrail("billing-team", policy=team_policy)
    """

    def __init__(
        self,
        team_name: str,
        policy: Optional[RateLimitPolicy] = None,
        store: Optional[RateLimitStore] = None,
        name: str = "team_rate_limit_guardrail",
    ) -> None:
        super().__init__(
            agent_name=team_name,
            policy=policy,
            store=store,
            name=name,
        )
        self.team_name = team_name
