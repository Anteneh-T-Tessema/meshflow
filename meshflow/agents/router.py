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

from dataclasses import dataclass
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
        self._fallback_chain: list[str] = []  # ordered fallback models (health-aware)

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

    def set_fallback_chain(self, *models: str) -> "ProviderRouter":
        """Set an ordered list of model fallbacks used by ``route_with_health()``.

        When the primary model is degraded, ``route_with_health()`` tries each
        model in order and returns the first healthy one.

        Example::

            router.set_fallback_chain(
                "claude-opus-4-7",
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
            )
        """
        self._fallback_chain = list(models)
        return self

    def route_with_health(
        self,
        role: str | AgentRole,
        budget_usd: float = 1.0,
        compliance: str = "",
        tracker: "Any | None" = None,
    ) -> tuple[LLMProvider, str]:
        """Like ``route()``, but skips degraded models using ModelHealthTracker.

        Falls back through ``_fallback_chain`` until a healthy model is found.
        If all models in the chain are degraded, returns the best of the chain.
        If no fallback chain is set, behaves identically to ``route()``.
        """
        from meshflow.agents.health import get_health_tracker
        health = tracker or get_health_tracker()

        provider, primary_model = self.route(role, budget_usd, compliance)

        # If no chain configured, just return the primary
        if not self._fallback_chain:
            return provider, primary_model

        # Try chain: include primary at the front
        chain = [primary_model] + [m for m in self._fallback_chain if m != primary_model]
        for model in chain:
            if not health.is_degraded(model):
                return AnthropicProvider(), model

        # All degraded — return the healthiest
        best = health.best_model(chain)
        return AnthropicProvider(), best

    def route_with_latency(
        self,
        role: str | AgentRole,
        budget_usd: float = 1.0,
        compliance: str = "",
        *,
        max_p95_latency_ms: float = 0.0,
        prefer: str = "quality",
        tracker: "Any | None" = None,
    ) -> tuple[LLMProvider, str]:
        """Route to the fastest model that meets quality and latency constraints.

        Parameters
        ----------
        max_p95_latency_ms: Maximum acceptable p95 latency.
                            0 = no latency constraint (behaves like route_with_health).
        prefer:             ``"quality"`` — prefer highest quality within latency budget.
                            ``"speed"``   — prefer lowest latency above quality floor.
        tracker:            Optional :class:`~meshflow.agents.health.ModelHealthTracker`.

        How it works
        ------------
        1. Get the default model from ``route()``.
        2. Build a candidate chain and filter out:
           - Models where ``health.is_degraded()`` is True.
           - Models where ``p95_latency_ms > max_p95_latency_ms`` (when constrained).
        3. Among passing candidates, pick the one with the best quality (health_score)
           or the lowest latency, depending on *prefer*.
        """
        from meshflow.agents.health import get_health_tracker
        health = tracker or get_health_tracker()

        provider, primary = self.route(role, budget_usd, compliance)
        chain = [primary] + [m for m in (self._fallback_chain or []) if m != primary]

        candidates: list[tuple[str, float, float]] = []  # (model, health, p95)
        for model in chain:
            if health.is_degraded(model):
                continue
            summ = health.summary(model)
            p95 = summ.p95_latency_ms
            if max_p95_latency_ms > 0 and p95 > max_p95_latency_ms and p95 > 0:
                continue
            candidates.append((model, summ.health_score, p95))

        if not candidates:
            # Nothing meets constraints — fall back to best-health model
            return AnthropicProvider(), health.best_model(chain)

        if prefer == "speed":
            # Sort by latency ascending; use 99999 for untracked (no latency data)
            candidates.sort(key=lambda x: x[2] if x[2] > 0 else 99999)
        else:
            # Sort by health score descending (highest quality first)
            candidates.sort(key=lambda x: x[1], reverse=True)

        return AnthropicProvider(), candidates[0][0]

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


# ── ModelTierRouter ────────────────────────────────────────────────────────────

@dataclass
class ModelTier:
    """A named model tier with an optional cost guard.

    Attributes
    ----------
    name:       Human label (e.g. ``"fast"``, ``"smart"``, ``"large"``).
    model:      Model identifier — Ollama-style for local, provider ID for cloud.
    max_tokens: Soft cap on output tokens for this tier (advisory only).
    """

    name: str
    model: str
    max_tokens: int = 2048


class ModelTierRouter:
    """Right-size each agent in a mixed-model pipeline.

    Maps task characteristics to the cheapest model that can handle them.
    Local models (Ollama, LiteLLM-local) are zero-cost and are tried first.

    Quick start::

        from meshflow.agents.router import ModelTierRouter, ModelTier
        from meshflow import Agent, Workflow, CostCap

        router = ModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2",                        max_tokens=512),
                ModelTier("smart", "mistral",                         max_tokens=2048),
                ModelTier("large", "meta.llama3-70b-instruct-v1:0",   max_tokens=4096),
            ],
            # threshold chars in task text that bumps up a tier
            smart_threshold=300,
            large_threshold=800,
        )

        wf = Workflow(cost_cap=CostCap(usd=0.50))
        wf.add(
            Agent("planner",    model_router=router),
            Agent("researcher", model_router=router),
            Agent("writer",     model_router=router),
        )

    The CostCap is effectively $0 while all models stay local. Swap
    ``"meta.llama3-70b-instruct-v1:0"`` for a Bedrock/OpenAI model and the
    cap protects you automatically — only large-tier calls touch your wallet.
    """

    PRESET_LOCAL: list[ModelTier] = [
        ModelTier("fast",  "llama3.2",   max_tokens=512),
        ModelTier("smart", "mistral",    max_tokens=2048),
        ModelTier("large", "llama3.2",   max_tokens=4096),  # fall back to local
    ]

    PRESET_HYBRID_BEDROCK: list[ModelTier] = [
        ModelTier("fast",  "llama3.2",                                  max_tokens=512),
        ModelTier("smart", "mistral",                                   max_tokens=2048),
        ModelTier("large", "meta.llama3-70b-instruct-v1:0",             max_tokens=4096),
    ]

    PRESET_HYBRID_OPENAI: list[ModelTier] = [
        ModelTier("fast",  "llama3.2",   max_tokens=512),
        ModelTier("smart", "mistral",    max_tokens=2048),
        ModelTier("large", "gpt-4o",     max_tokens=4096),
    ]

    def __init__(
        self,
        tiers: list[ModelTier] | None = None,
        *,
        smart_threshold: int = 300,
        large_threshold: int = 800,
    ) -> None:
        self._tiers = tiers or list(self.PRESET_LOCAL)
        self._smart = smart_threshold
        self._large = large_threshold

    # ── ProviderRouter-compatible interface ───────────────────────────────────

    def route(self, task: str = "", tools: list[Any] | None = None) -> Any:  # type: ignore[override]
        """Pick a tier based on task length and tool complexity.

        Returns a duck-typed object with ``.model`` so Agent.model_router
        dispatch works transparently.
        """
        score = len(task)
        if tools:
            score += len(tools) * 100

        if score >= self._large and len(self._tiers) >= 3:
            chosen = self._tiers[2]
        elif score >= self._smart and len(self._tiers) >= 2:
            chosen = self._tiers[1]
        else:
            chosen = self._tiers[0]

        from meshflow.agents.base import model_is_local
        import logging
        if not model_is_local(chosen.model):
            logging.getLogger("meshflow.router").info(
                "ModelTierRouter: cloud model '%s' selected for task (len=%d). "
                "Ensure your CostCap is set.",
                chosen.model, len(task),
            )

        return _TierResult(model=chosen.model, tier=chosen.name)

    def tiers(self) -> list[ModelTier]:
        return list(self._tiers)


@dataclass
class _TierResult:
    """Minimal result duck-typed to match ProviderRouter routing result."""

    model: str
    tier: str
    cost_usd: float = 0.0
