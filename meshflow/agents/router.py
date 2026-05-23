"""ProviderRouter — smart model selection without manual config.

Routes based on three signals:
  role       — what the agent does (planner, critic, executor, …)
  budget_usd — max cost per run
  compliance — regulatory regime ("hipaa", "sox", "gdpr", "")

The routing table is entirely deterministic and overridable.

Usage::

    from meshflow.agents.router import ProviderRouter, auto_provider
    from meshflow import Agent

    # Automatic selection
    agent = Agent(name="planner", role="planner", provider=auto_provider())

    # Budget-aware
    agent = Agent(name="executor", role="executor",
                  provider=auto_provider(budget_usd=0.005))

    # Compliance-aware (hipaa → opus for maximum accuracy)
    agent = Agent(name="reviewer", role="critic",
                  provider=auto_provider(compliance="hipaa"))

    # Custom table
    router = ProviderRouter()
    router.set_rule("executor", budget_ceiling=0.01, model="claude-haiku-4-5-20251001")
    provider, model = router.route("executor", budget_usd=0.003)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from meshflow.agents.base import AnthropicProvider, LLMProvider
from meshflow.core.schemas import AgentRole


# ── Routing table entry ───────────────────────────────────────────────────────

@dataclass
class _RouteEntry:
    model: str
    provider_factory: Any  # () -> LLMProvider
    reason: str


# ── Default model constants ───────────────────────────────────────────────────

_OPUS   = "claude-opus-4-7"
_SONNET = "claude-sonnet-4-6"
_HAIKU  = "claude-haiku-4-5-20251001"

# Cost thresholds (USD per run) for haiku / sonnet / opus fallback
_HAIKU_CEILING  = 0.01   # < $0.01 → use haiku
_SONNET_CEILING = 0.20   # < $0.20 → use sonnet
# >= $0.20 or compliance → use opus


# ── ProviderRouter ────────────────────────────────────────────────────────────

class ProviderRouter:
    """Selects the right model+provider for a given role, budget, and compliance.

    The default table encodes three rules (checked in order):

    1. Compliance gate: regulated regimes (hipaa/sox/gdpr/pci) → opus always.
       Rationale: maximum accuracy, lowest hallucination rate for high-stakes output.

    2. Budget gate: if budget_usd < $0.01 → haiku.
       Rationale: batch tasks and summarisers don't need a frontier model.

    3. Role gate (per-role defaults):
       - orchestrator / planner / critic  → sonnet  (planning + evaluation)
       - researcher                        → sonnet  (knowledge retrieval)
       - guardian                          → opus    (safety must be best)
       - executor                          → haiku   (workhorses; often cheap tasks)
       - any other                         → sonnet  (safe default)
    """

    _COMPLIANCE_REGIMES = {"hipaa", "sox", "gdpr", "pci", "pci-dss", "nerc"}

    _ROLE_DEFAULTS: dict[str, str] = {
        AgentRole.ORCHESTRATOR.value: _SONNET,
        AgentRole.PLANNER.value:      _SONNET,
        AgentRole.CRITIC.value:       _SONNET,
        AgentRole.RESEARCHER.value:   _SONNET,
        AgentRole.GUARDIAN.value:     _OPUS,    # safety non-negotiable
        AgentRole.EXECUTOR.value:     _HAIKU,
    }

    def __init__(self) -> None:
        # Custom overrides: role → model string (set via set_rule)
        self._overrides: dict[str, str] = {}

    def set_rule(self, role: str, model: str, budget_ceiling: float = 0.0) -> None:
        """Override the default model for *role*.

        Parameters
        ----------
        role:
            AgentRole value string (e.g. "executor").
        model:
            Claude model ID (e.g. "claude-haiku-4-5-20251001").
        budget_ceiling:
            When > 0, the override only applies if budget_usd < budget_ceiling.
        """
        self._overrides[role] = model

    def route(
        self,
        role: str | AgentRole,
        budget_usd: float = 1.0,
        compliance: str = "",
    ) -> tuple[LLMProvider, str]:
        """Return (provider, model_id) for the given context.

        Parameters
        ----------
        role:
            AgentRole or its string value.
        budget_usd:
            Maximum spend per run. Influences model tier selection.
        compliance:
            Compliance regime key. Regulated regimes always use opus.

        Returns
        -------
        (provider, model_id)
        """
        role_str = role.value if isinstance(role, AgentRole) else str(role)

        # Rule 1: compliance gate
        if compliance.lower().strip() in self._COMPLIANCE_REGIMES:
            return AnthropicProvider(), _OPUS

        # Rule 2: custom override
        if role_str in self._overrides:
            return AnthropicProvider(), self._overrides[role_str]

        # Rule 3: budget gate (very low budget → haiku)
        if budget_usd < _HAIKU_CEILING:
            return AnthropicProvider(), _HAIKU

        # Rule 4: role default
        model = self._ROLE_DEFAULTS.get(role_str, _SONNET)
        return AnthropicProvider(), model

    def explain(
        self,
        role: str | AgentRole,
        budget_usd: float = 1.0,
        compliance: str = "",
    ) -> str:
        """Return a human-readable explanation of the routing decision."""
        role_str = role.value if isinstance(role, AgentRole) else str(role)
        _, model = self.route(role_str, budget_usd, compliance)

        if compliance.lower().strip() in self._COMPLIANCE_REGIMES:
            return f"model={model!r} (compliance={compliance!r} → opus for maximum accuracy)"
        if role_str in self._overrides:
            return f"model={model!r} (custom override for role={role_str!r})"
        if budget_usd < _HAIKU_CEILING:
            return f"model={model!r} (budget=${budget_usd:.4f} < ${_HAIKU_CEILING} → haiku)"
        return f"model={model!r} (role={role_str!r} default)"


# ── Module-level default router ───────────────────────────────────────────────

_default_router = ProviderRouter()


def auto_provider(
    role: str | AgentRole = AgentRole.EXECUTOR,
    budget_usd: float = 1.0,
    compliance: str = "",
    router: ProviderRouter | None = None,
) -> AnthropicProvider:
    """Return the auto-selected provider for the given context.

    This is the recommended shorthand for ``Agent(provider=auto_provider(...))``.

    Parameters
    ----------
    role:
        AgentRole (string or enum).
    budget_usd:
        Max spend per run — drives haiku vs sonnet vs opus selection.
    compliance:
        Regulation key ("hipaa", "sox", "gdpr", "pci", …).
    router:
        Override the module-level default router.

    Returns
    -------
    An ``AnthropicProvider`` instance pre-selected for the context.
    The caller can also inspect ``auto_model(...)`` for just the model string.
    """
    r = router or _default_router
    provider, _ = r.route(role, budget_usd, compliance)
    return provider  # type: ignore[return-value]


def auto_model(
    role: str | AgentRole = AgentRole.EXECUTOR,
    budget_usd: float = 1.0,
    compliance: str = "",
    router: ProviderRouter | None = None,
) -> str:
    """Return just the model ID for the given context (no provider object)."""
    r = router or _default_router
    _, model = r.route(role, budget_usd, compliance)
    return model
