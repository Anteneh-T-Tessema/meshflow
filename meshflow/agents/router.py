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
    is_local:   Explicit override for locality. ``True`` → zero cost, ``False``
                → cloud pricing. Leave as ``None`` (default) to auto-detect via
                :func:`meshflow.agents.base.model_is_local`. Set this when your
                model name is not in the recognised local-family list (e.g. a
                custom Ollama model, LiteLLM proxy, or private fine-tune).

    Example::

        # Custom Ollama fine-tune — auto-detect won't recognise "corp-llm"
        ModelTier("fast", "corp-llm", max_tokens=512, is_local=True)

        # LiteLLM proxy forwarding to a local endpoint
        ModelTier("smart", "http://localhost:4000/v1", max_tokens=2048, is_local=True)
    """

    name: str
    model: str
    max_tokens: int = 2048
    is_local: bool | None = None


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
        smart_threshold: int | float = 300,
        large_threshold: int | float = 800,
    ) -> None:
        self._tiers = tiers or list(self.PRESET_LOCAL)
        self._smart: float = float(smart_threshold)
        self._large: float = float(large_threshold)

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
        # Honour explicit is_local on the tier; fall back to pattern matching.
        if chosen.is_local is not None:
            is_local = chosen.is_local
        else:
            is_local = model_is_local(chosen.model)
        if not is_local:
            logging.getLogger("meshflow.router").info(
                "ModelTierRouter: cloud model '%s' selected for task (len=%d). "
                "Ensure your CostCap is set.",
                chosen.model, len(task),
            )

        return _TierResult(model=chosen.model, tier=chosen.name, is_local=is_local)

    def tiers(self) -> list[ModelTier]:
        return list(self._tiers)


@dataclass
class _TierResult:
    """Minimal result duck-typed to match ProviderRouter routing result."""

    model: str
    tier: str
    cost_usd: float = 0.0
    is_local: bool = False
    routing_id: str = ""      # set by AdaptiveModelTierRouter for outcome matching


# ── AdaptiveModelTierRouter ───────────────────────────────────────────────────


class AdaptiveModelTierRouter(ModelTierRouter):
    """Self-improving router that learns from observed routing outcomes.

    Extends :class:`ModelTierRouter` with three additions:

    1. **Multi-dimensional task scoring** via :class:`~meshflow.agents.scoring.TaskScorer`.
       Composite 0–1 score replaces raw character count.

    2. **Epsilon-greedy exploration** with annealing.  With probability *ε*
       (decaying toward 0 as data accumulates) the router picks a tier ±1 from
       the greedy-optimal choice.  Exploration outcomes are flagged so the
       optimizer can distinguish them.

    3. **Automatic threshold adaptation** every ``adapt_every`` routes.
       :class:`~meshflow.agents.adaptation.ThresholdOptimizer` analyses recent
       outcomes and shifts ``smart_threshold`` / ``large_threshold`` when a tier
       is failing on tasks in its assigned score range.

    Quality signal
    --------------
    Outcomes are recorded by the ``_BuiltAgent.step()`` hook in
    ``meshflow/agents/builder.py``.  The CONFIDENCE:0.XX marker emitted by
    agents is extracted automatically — no user code changes required.
    Quality < 0.5 counts as a failure for threshold optimisation purposes.

    Usage::

        from meshflow import AdaptiveModelTierRouter, ModelTier, Agent, Workflow

        router = AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "llama3.2",    max_tokens=512),
                ModelTier("smart", "mistral:7b",  max_tokens=2048),
                ModelTier("large", "gpt-4o",      max_tokens=4096),
            ],
            adapt_every=50,          # auto-adapt after every 50 routes
            exploration_rate=0.10,   # 10% exploration, decays with experience
        )

        wf = Workflow()
        wf.add(Agent("analyst", model_router=router))
        result = wf.run("analyse the dataset")

        # Inspect what the router learned
        print(router.stats())
        print(router.explain("short task"))
    """

    def __init__(
        self,
        tiers: list["ModelTier"] | None = None,
        *,
        smart_threshold: float = 0.33,
        large_threshold: float = 0.67,
        adapt_every: int = 50,
        exploration_rate: float = 0.10,
        store: Any | None = None,
        registry: Any | None = None,
        adapt_mode: str = "auto",
    ) -> None:
        # Initialise base with dummy char-count thresholds (not used by this class)
        super().__init__(tiers, smart_threshold=9999, large_threshold=99999)
        self._smart = smart_threshold    # composite 0-1
        self._large = large_threshold
        self._adapt_every = adapt_every
        self._exploration_rate = exploration_rate
        self._adapt_mode = adapt_mode

        # Lazy imports to avoid circular imports at module load time
        from meshflow.agents.adaptation import RouterOutcomeStore, ThresholdOptimizer
        from meshflow.agents.scoring import TaskScorer

        self._store: Any = store if store is not None else RouterOutcomeStore()
        self._scorer = TaskScorer()
        self._optimizer = ThresholdOptimizer()
        self._registry = registry  # optional ModelRegistry

        self._route_count: int = 0
        self._last_adapted_at: float | None = None
        # pending: routing_id → (task, composite_score, model, tier, was_exploration)
        self._pending: dict[str, tuple[str, float, str, str, bool]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def route(  # type: ignore[override]
        self,
        task: str = "",
        tools: list[Any] | None = None,
        run_id: str = "",
    ) -> "_TierResult":
        """Route a task, optionally with epsilon-greedy exploration.

        Parameters
        ----------
        task:    Task text (any length).
        tools:   Tool names available (bumps composite score).
        run_id:  Step or run ID; used to match the routing to its outcome when
                 :meth:`record_outcome` is called.  If empty, a UUID is generated.
        """
        import random
        import uuid as _uuid

        ts = self._scorer.score(task, tools)
        composite = ts.composite

        # Greedy tier index
        optimal_idx = self._composite_to_tier_idx(composite)

        # Epsilon-greedy with annealing
        n = self._route_count
        eps_effective = self._exploration_rate * (200.0 / (n + 200.0))
        was_exploration = False
        tier_idx = optimal_idx
        if len(self._tiers) > 1 and random.random() < eps_effective:
            direction = random.choice([-1, 1])
            tier_idx = max(0, min(len(self._tiers) - 1, optimal_idx + direction))
            was_exploration = True

        chosen = self._tiers[tier_idx]

        # Resolve is_local (registry → tier override → pattern detection)
        is_local = self._resolve_is_local(chosen)

        # Log cloud selection
        if not is_local:
            import logging
            logging.getLogger("meshflow.adaptive_router").info(
                "AdaptiveModelTierRouter: cloud model '%s' (tier=%s, composite=%.3f). "
                "Ensure CostCap is set.",
                chosen.model, chosen.name, composite,
            )

        routing_id = run_id or str(_uuid.uuid4())
        self._pending[routing_id] = (task, composite, chosen.model, chosen.name, was_exploration)
        self._route_count += 1

        if self._adapt_mode == "auto" and self._route_count % self._adapt_every == 0:
            self._maybe_adapt()

        return _TierResult(
            model=chosen.model,
            tier=chosen.name,
            is_local=is_local,
            routing_id=routing_id,
        )

    def record_outcome(
        self,
        run_id: str,
        *,
        success: bool,
        quality: float | None = None,
        latency_ms: float = 0.0,
        actual_cost_usd: float = 0.0,
    ) -> None:
        """Record the outcome of a previously routed task.

        Normally called automatically by the ``_BuiltAgent.step()`` hook in
        ``builder.py``.  Can also be called manually for custom pipelines.

        Parameters
        ----------
        run_id:           The run/step ID passed to :meth:`route` (or the
                          ``routing_id`` in the returned :class:`_TierResult`).
        success:          False if the agent step raised an exception.
        quality:          CONFIDENCE score from agent output (0–1 or None).
        latency_ms:       Wall-clock duration of the step.
        actual_cost_usd:  Actual cost from the ledger.
        """
        pending = self._pending.pop(run_id, None)
        if pending is None:
            return

        task_text, composite, model, tier, was_exploration = pending

        from meshflow.agents.adaptation import RoutingOutcome
        outcome = RoutingOutcome.build(
            run_id=run_id,
            task=task_text,
            composite_score=composite,
            model=model,
            tier=tier,
            was_exploration=was_exploration,
            success=success,
            quality_score=quality,
            latency_ms=latency_ms,
            actual_cost_usd=actual_cost_usd,
        )
        self._store.record(outcome)

    def adapt(self) -> "Any":
        """Force a threshold optimisation pass and apply the recommendation.

        Returns the :class:`~meshflow.agents.adaptation.ThresholdRecommendation`.
        """
        from meshflow.agents.adaptation import ThresholdOptimizer
        rec = self._optimizer.optimize(self._store, self._smart, self._large)
        if rec.changed and rec.confidence >= 0.30:
            self._smart = rec.smart_threshold
            self._large = rec.large_threshold
            import logging, time
            self._last_adapted_at = time.time()
            logging.getLogger("meshflow.adaptive_router").info(
                "Thresholds adapted: %s", rec.summary
            )
        return rec

    def explain(self, task: str, tools: list[Any] | None = None) -> str:
        """Return a human-readable explanation of the routing decision for *task*."""
        ts = self._scorer.score(task, tools)
        idx = self._composite_to_tier_idx(ts.composite)
        chosen = self._tiers[idx]
        is_local = self._resolve_is_local(chosen)
        lines = [
            f"Task routing explanation",
            f"  task_type   : {ts.task_type}",
            f"  length      : {ts.length} chars",
            f"  complexity  : {ts.complexity:.3f}",
            f"  tool_count  : {ts.tool_count}",
            f"  composite   : {ts.composite:.3f}",
            f"  thresholds  : smart={self._smart:.3f}, large={self._large:.3f}",
            f"  → tier      : {chosen.name!r} ({chosen.model})",
            f"  → is_local  : {is_local}",
            f"  routes_seen : {self._route_count}",
            f"  last_adapted: {self._last_adapted_at or 'never'}",
        ]
        return "\n".join(lines)

    def stats(self) -> "Any":
        """Return aggregated per-tier stats as a :class:`~meshflow.agents.adaptation.RouterStats`."""
        from meshflow.agents.adaptation import RouterStats
        total = self._store.count()
        explorations = self._store.count_explorations()
        tier_stats = {}
        for t in self._tiers:
            tier_stats[t.name] = self._store.get_tier_stats(t.name)
        return RouterStats(
            tiers=tier_stats,
            total_runs=total,
            exploration_rate_actual=explorations / total if total else 0.0,
            last_adapted_at=self._last_adapted_at,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _composite_to_tier_idx(self, composite: float) -> int:
        if composite >= self._large and len(self._tiers) >= 3:
            return 2
        if composite >= self._smart and len(self._tiers) >= 2:
            return 1
        return 0

    def _resolve_is_local(self, tier: "ModelTier") -> bool:
        if tier.is_local is not None:
            return tier.is_local
        if self._registry is not None:
            return self._registry.is_local(tier.model)
        from meshflow.agents.base import model_is_local
        return model_is_local(tier.model)

    def _maybe_adapt(self) -> None:
        try:
            self.adapt()
        except Exception:
            pass  # never crash a route() call due to adaptation failure


# ── CascadeRouter ─────────────────────────────────────────────────────────────


@dataclass
class _CascadeState:
    """Tracks a single in-flight cascade attempt."""
    task: str
    composite: float
    current_tier_idx: int
    escalations_used: int
    initial_routing_id: str


class CascadeRouter:
    """FrugalGPT-style cascade: try the cheap model first, escalate on low confidence.

    Route flow::

        fast model  →  CONFIDENCE < threshold?  →  smart model  →  CONFIDENCE < threshold?  →  large model
                              (first escalation)                        (second escalation)

    Only pays for expensive models when the cheap model's CONFIDENCE marker
    falls below ``escalation_threshold``.  Most tasks (simple Q&A, summaries)
    are handled at $0 by the local fast-tier model.

    Usage::

        from meshflow import CascadeRouter, AdaptiveModelTierRouter, ModelTier, Agent, Workflow

        base = AdaptiveModelTierRouter(tiers=[
            ModelTier("fast",  "llama3.2", max_tokens=512),
            ModelTier("smart", "mistral",  max_tokens=2048),
            ModelTier("large", "gpt-4o",   max_tokens=4096),
        ])

        cascade = CascadeRouter(base, escalation_threshold=0.65, max_escalations=2)

        wf = Workflow()
        wf.add(Agent("analyst", model_router=cascade, cascade_threshold=0.65))
        result = wf.run("Explain quantum entanglement simply.")

    Parameters
    ----------
    router:                 Any router with a ``route()`` interface (typically
                            :class:`AdaptiveModelTierRouter`).
    escalation_threshold:   CONFIDENCE below this triggers escalation (default 0.65).
    max_escalations:        Maximum number of tier upgrades per task (default 2).
    """

    def __init__(
        self,
        router: Any,
        escalation_threshold: float = 0.65,
        max_escalations: int = 2,
    ) -> None:
        self._router = router
        self.escalation_threshold = escalation_threshold
        self.max_escalations = max_escalations
        self._states: dict[str, _CascadeState] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def route(
        self,
        task: str = "",
        tools: list[Any] | None = None,
        run_id: str = "",
    ) -> "_TierResult":
        """Route a task — delegates to the wrapped router for the initial tier."""
        import inspect as _inspect
        import uuid as _uuid
        routing_id = run_id or str(_uuid.uuid4())
        # Pass run_id only if the wrapped router's route() supports it
        _route_sig = _inspect.signature(self._router.route)
        if "run_id" in _route_sig.parameters:
            result = self._router.route(task, tools, run_id=routing_id)
        else:
            result = self._router.route(task, tools)
        # Record cascade state for potential escalation
        tiers = getattr(self._router, "_tiers", [])
        tier_idx = next(
            (i for i, t in enumerate(tiers) if t.name == result.tier), 0
        )
        self._states[routing_id] = _CascadeState(
            task=task,
            composite=result.composite if hasattr(result, "composite") else 0.0,
            current_tier_idx=tier_idx,
            escalations_used=0,
            initial_routing_id=routing_id,
        )
        result.routing_id = routing_id  # type: ignore[attr-defined]
        return result

    def escalate(self, routing_id: str) -> "_TierResult | None":
        """Return the next tier's result for a given routing ID.

        Called by ``_BuiltAgent.step()`` when CONFIDENCE < escalation_threshold.
        Returns ``None`` when max_escalations is reached or no higher tier exists.
        """
        state = self._states.get(routing_id)
        if state is None:
            return None
        if state.escalations_used >= self.max_escalations:
            return None

        tiers = getattr(self._router, "_tiers", [])
        next_idx = state.current_tier_idx + 1
        if next_idx >= len(tiers):
            return None

        next_tier = tiers[next_idx]
        state.current_tier_idx = next_idx
        state.escalations_used += 1

        is_local = False
        if hasattr(self._router, "_resolve_is_local"):
            is_local = self._router._resolve_is_local(next_tier)
        elif next_tier.is_local is not None:
            is_local = next_tier.is_local
        else:
            from meshflow.agents.base import model_is_local
            is_local = model_is_local(next_tier.model)

        if not is_local:
            import logging
            logging.getLogger("meshflow.cascade_router").info(
                "CascadeRouter: escalating to '%s' (tier=%s, escalation %d/%d). "
                "CONFIDENCE was below %.2f.",
                next_tier.model, next_tier.name,
                state.escalations_used, self.max_escalations,
                self.escalation_threshold,
            )

        return _TierResult(
            model=next_tier.model,
            tier=next_tier.name,
            is_local=is_local,
            routing_id=routing_id,
        )

    def record_outcome(self, run_id: str, **kw: Any) -> None:
        """Forward outcome recording to the wrapped router if it supports it."""
        if hasattr(self._router, "record_outcome"):
            self._router.record_outcome(run_id, **kw)
        self._states.pop(run_id, None)

    def escalation_count(self, routing_id: str) -> int:
        """Return how many escalations have been used for a given routing ID."""
        state = self._states.get(routing_id)
        return state.escalations_used if state else 0

    def tiers(self) -> list[Any]:
        return self._router.tiers() if hasattr(self._router, "tiers") else []
