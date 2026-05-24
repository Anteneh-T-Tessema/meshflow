"""Sprint 45 — BudgetGuardrail: cumulative spend gate for agent calls.

Slots into the existing GuardrailStack.  check() is a pre-flight gate that
blocks calls when any budget account for the agent is exhausted.
record_spend() debits actual post-call spend.

Usage::

    from meshflow.budget.store import BudgetAccount, BudgetStore
    from meshflow.budget.guardrail import BudgetGuardrail
    from meshflow.security.guardrails import GuardrailStack

    store = BudgetStore(":memory:")
    store.create(BudgetAccount(
        account_id="billing-daily",
        agent_name="billing-agent",
        period="daily",
        limit_usd=5.00,
    ))

    guardrail = BudgetGuardrail(agent_name="billing-agent", store=store)
    stack = GuardrailStack([guardrail])

    # Pre-flight
    passed, reason, _ = stack.run("task text")

    # Post-call debit
    guardrail.record_spend(cost_usd=0.12, tokens=1_200)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from meshflow.security.guardrails import Guardrail, GuardrailResult

if TYPE_CHECKING:
    from meshflow.budget.store import BudgetStore


class BudgetGuardrail(Guardrail):
    """Block agent calls when any cumulative budget account is exhausted.

    Parameters
    ----------
    agent_name: Agent whose budget accounts to check.
    store:      BudgetStore to query.  Uses the module-level default if None.
    warn_at:    Fraction (0–1) of the budget used above which a ``"warn"``
                metadata flag is included even when the call is allowed.
                Default 0.8 (80 %).
    name:       Override guardrail name.
    """

    def __init__(
        self,
        agent_name: str,
        store: "BudgetStore | None" = None,
        warn_at: float = 0.80,
        name: str = "budget_guardrail",
    ) -> None:
        super().__init__(action="block", name=name)
        self.agent_name = agent_name
        self._store = store
        self.warn_at = warn_at

    @property
    def store(self) -> "BudgetStore":
        if self._store is not None:
            return self._store
        from meshflow.budget.store import get_budget_store
        return get_budget_store()

    def check(self, text: str) -> GuardrailResult:
        """Pre-flight: block if any budget account for this agent is exhausted."""
        allowed, reason = self.store.is_agent_allowed(self.agent_name)
        if not allowed:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=reason,
                metadata={"agent_name": self.agent_name},
            )

        # Passed — check if any account is approaching its limit
        results = self.store.check_agent(self.agent_name)
        near_limit = [r for r in results if r.percent_used >= self.warn_at]
        meta: dict[str, Any] = {"agent_name": self.agent_name}
        if near_limit:
            meta["near_limit"] = [r.to_dict() for r in near_limit]

        return GuardrailResult(
            passed=True,
            guardrail_name=self.name,
            metadata=meta,
        )

    def record_spend(self, *, cost_usd: float = 0.0, tokens: int = 0) -> None:
        """Debit actual post-call spend across all accounts for this agent."""
        for account in self.store.list(agent_name=self.agent_name):
            try:
                self.store.record_spend(
                    account.account_id, cost_usd=cost_usd, tokens=tokens
                )
            except Exception:
                pass

    def status(self) -> list[dict[str, Any]]:
        """Return a summary dict for each budget account of this agent."""
        return [
            self.store.summary(a.account_id)
            for a in self.store.list(agent_name=self.agent_name)
        ]


__all__ = ["BudgetGuardrail"]
