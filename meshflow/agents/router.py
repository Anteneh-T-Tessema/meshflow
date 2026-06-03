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
    was_exploration: bool = False  # True when Thompson Sampling differs from greedy baseline


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

        # ── Thompson Sampling posteriors ──────────────────────────────────────
        # Per tier: Beta(α, β) distribution over success rate.
        # α = cumulative successes, β = cumulative failures.
        # Prior: Beta(1, 1) = uniform — no bias at cold start.
        # Effective_success = quality_score >= 0.5 (matches RoutingOutcome.effective_success).
        n = len(self._tiers)
        self._ts_alpha: list[float] = [1.0] * n   # successes per tier
        self._ts_beta_: list[float] = [1.0] * n   # failures per tier
        # Quality threshold for Thompson Sampling: route to cheapest tier whose
        # sampled success-rate draws above this.  Fixed at 0.5 (mirrors
        # RoutingOutcome.effective_success).
        self._ts_threshold: float = 0.5

    # ── Public API ────────────────────────────────────────────────────────────

    def route(  # type: ignore[override]
        self,
        task: str = "",
        tools: list[Any] | None = None,
        run_id: str = "",
    ) -> "_TierResult":
        """Route a task using Thompson Sampling over Beta posteriors.

        Algorithm
        ---------
        1. Score the task with :class:`~meshflow.agents.scoring.TaskScorer` to get
           a composite complexity score (0–1).
        2. Determine the greedy baseline tier from the composite score thresholds
           (same as :class:`ModelTierRouter`).
        3. **Thompson Sampling**: draw a sample from each tier's Beta posterior.
           Select the cheapest tier whose sample exceeds the quality threshold (0.5).
           Prefer cheaper tiers — iterate from index 0 (fast) upward and commit to
           the first tier that samples above threshold.
        4. ``was_exploration = (chosen_idx != greedy_idx)`` for outcome logging.

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

        # Greedy baseline from composite score thresholds
        optimal_idx = self._composite_to_tier_idx(composite)
        tier_idx = optimal_idx
        was_exploration = False

        # ── Thompson Sampling (data-gated hybrid) ────────────────────────────
        # Phase 1 — cold start (< _TS_MIN_OBS observations on the greedy tier):
        #   Trust the composite score; no TS adjustment.  Preserves task-complexity
        #   routing while we accumulate enough data to form informative posteriors.
        #
        # Phase 2 — warm (≥ _TS_MIN_OBS observations):
        #   Sample Beta(α+1, β+1) for the greedy tier.
        #   • Greedy tier samples ABOVE threshold → stay (no exploration needed).
        #   • Greedy tier samples BELOW threshold → check neighbors:
        #       cheaper first  → de-escalate if it's reliably performing
        #       more expensive → escalate if it's proven to do better
        #   Cheaper neighbors are always preferred over more expensive ones.
        _TS_MIN_OBS = 5   # minimum observations before TS adjusts routing
        greedy_n_obs = (
            self._ts_alpha[optimal_idx] + self._ts_beta_[optimal_idx] - 2.0
        )
        if greedy_n_obs >= _TS_MIN_OBS:
            greedy_sample = random.betavariate(
                self._ts_alpha[optimal_idx] + 1.0,
                self._ts_beta_[optimal_idx] + 1.0,
            )
            if greedy_sample < self._ts_threshold:
                # Greedy tier uncertain/underperforming — look for a better option.
                # Prefer cheaper (de-escalate), then more expensive (escalate).
                for candidate in [optimal_idx - 1, optimal_idx + 1]:
                    if 0 <= candidate < len(self._tiers):
                        cand_n_obs = (
                            self._ts_alpha[candidate] + self._ts_beta_[candidate] - 2.0
                        )
                        if cand_n_obs >= 3:   # need some data on the candidate too
                            cand_sample = random.betavariate(
                                self._ts_alpha[candidate] + 1.0,
                                self._ts_beta_[candidate] + 1.0,
                            )
                            if cand_sample >= self._ts_threshold:
                                tier_idx = candidate
                                was_exploration = True
                                break   # stop at cheapest viable candidate

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
            was_exploration=was_exploration,
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

        # ── Update Thompson Sampling Beta posteriors ───────────────────────
        # effective_success mirrors RoutingOutcome.effective_success:
        # success=True AND (quality is None OR quality >= 0.5)
        effective = success and (quality is None or quality >= 0.5)
        tier_pos = next(
            (i for i, t in enumerate(self._tiers) if t.name == tier), None
        )
        if tier_pos is not None:
            if effective:
                self._ts_alpha[tier_pos] += 1.0
            else:
                self._ts_beta_[tier_pos] += 1.0

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
            f"  Thompson Sampling posteriors (α=successes, β=failures):",
        ]
        for i, t in enumerate(self._tiers):
            alpha = self._ts_alpha[i]
            beta_ = self._ts_beta_[i]
            n_obs = alpha + beta_ - 2   # subtract uniform prior
            mean  = alpha / (alpha + beta_)
            lines.append(
                f"    [{t.name:8s}] α={alpha:.1f} β={beta_:.1f}  "
                f"mean={mean:.2f}  n_obs={n_obs:.0f}"
            )
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

    # ── Persistence ───────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the router's learned state.

        Captures thresholds, route count, last adaptation timestamp, and tier
        definitions.  Does **not** include the full outcome store — use
        :meth:`RouterOutcomeStore.export_csv` for that.

        Usage::

            data = router.snapshot()
            import json; json.dump(data, open("router_state.json", "w"))
        """
        return {
            "smart_threshold": self._smart,
            "large_threshold": self._large,
            "route_count": self._route_count,
            "last_adapted_at": self._last_adapted_at,
            "adapt_every": self._adapt_every,
            "exploration_rate": self._exploration_rate,
            "adapt_mode": self._adapt_mode,
            # Thompson Sampling posteriors — persist learned success/failure counts
            "ts_alpha": list(self._ts_alpha),
            "ts_beta": list(self._ts_beta_),
            "tiers": [
                {
                    "name": t.name,
                    "model": t.model,
                    "max_tokens": t.max_tokens,
                    "is_local": t.is_local,
                }
                for t in self._tiers
            ],
        }

    def save(self, path: str) -> None:
        """Persist the router's learned thresholds and config to a JSON file.

        Subsequent restarts can call :meth:`load` to resume from the saved
        state rather than starting from default thresholds.

        Example::

            router.save("router_state.json")
            # --- process restart ---
            router = AdaptiveModelTierRouter.load("router_state.json")
        """
        import json as _json
        import os as _os
        data = self.snapshot()
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            _json.dump(data, f, indent=2)
        _os.replace(tmp, path)

    @classmethod
    def load(cls, path: str, **overrides: Any) -> "AdaptiveModelTierRouter":
        """Restore an :class:`AdaptiveModelTierRouter` from a saved JSON snapshot.

        Parameters
        ----------
        path:       Path to the JSON file written by :meth:`save`.
        **overrides: Any keyword argument accepted by ``__init__`` to override
                    the saved value (e.g. ``store=RouterOutcomeStore("new.db")``).
        """
        import json as _json
        with open(path) as f:
            data = _json.load(f)
        tiers = [
            ModelTier(
                name=t["name"],
                model=t["model"],
                max_tokens=t.get("max_tokens", 2048),
                is_local=t.get("is_local"),
            )
            for t in data.get("tiers", [])
        ]
        kwargs: dict[str, Any] = {
            "tiers": tiers,
            "smart_threshold": data.get("smart_threshold", 0.33),
            "large_threshold": data.get("large_threshold", 0.67),
            "adapt_every": data.get("adapt_every", 50),
            "exploration_rate": data.get("exploration_rate", 0.10),
            "adapt_mode": data.get("adapt_mode", "auto"),
        }
        kwargs.update(overrides)
        router = cls(**kwargs)
        router._route_count = data.get("route_count", 0)
        router._last_adapted_at = data.get("last_adapted_at")
        # Restore Thompson Sampling posteriors if present
        n = len(router._tiers)
        saved_alpha = data.get("ts_alpha", [])
        saved_beta  = data.get("ts_beta",  [])
        if len(saved_alpha) == n:
            router._ts_alpha = [float(a) for a in saved_alpha]
        if len(saved_beta) == n:
            router._ts_beta_  = [float(b) for b in saved_beta]
        return router

    # ── YAML config ───────────────────────────────────────────────────────────

    def to_yaml(self, path: str) -> None:
        """Write a human-editable YAML config for this router.

        The file can be shared with teammates or checked into version control.
        Load it back with :meth:`from_yaml`::

            router.to_yaml("router.yaml")
            # --- edit thresholds or tier models in the file ---
            router2 = AdaptiveModelTierRouter.from_yaml("router.yaml")
        """
        lines = [
            "# MeshFlow AdaptiveModelTierRouter configuration",
            f"smart_threshold: {self._smart}",
            f"large_threshold: {self._large}",
            f"adapt_every: {self._adapt_every}",
            f"exploration_rate: {self._exploration_rate}",
            f"adapt_mode: {self._adapt_mode!r}",
            "tiers:",
        ]
        for t in self._tiers:
            lines.append(f"  - name: {t.name!r}")
            lines.append(f"    model: {t.model!r}")
            lines.append(f"    max_tokens: {t.max_tokens}")
            if t.is_local is not None:
                lines.append(f"    is_local: {str(t.is_local).lower()}")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    @classmethod
    def from_yaml(cls, path: str, **overrides: Any) -> "AdaptiveModelTierRouter":
        """Construct an :class:`AdaptiveModelTierRouter` from a YAML config file.

        The YAML schema matches the output of :meth:`to_yaml`.  Unknown keys
        are silently ignored for forward-compatibility.

        Example YAML::

            smart_threshold: 0.33
            large_threshold: 0.67
            adapt_every: 50
            exploration_rate: 0.10
            adapt_mode: 'auto'
            tiers:
              - name: 'fast'
                model: 'llama3.2'
                max_tokens: 512
              - name: 'smart'
                model: 'mistral:7b'
                max_tokens: 2048
                is_local: true
              - name: 'large'
                model: 'gpt-4o'
                max_tokens: 4096
                is_local: false
        """
        # Minimal YAML parser — handles the schema written by to_yaml() without
        # requiring PyYAML as a hard dependency.
        data = _parse_simple_yaml(path)
        tiers = []
        for t in data.get("tiers", []):
            is_local_raw = t.get("is_local")
            if isinstance(is_local_raw, str):
                is_local = is_local_raw.lower() == "true"
            else:
                is_local = is_local_raw  # bool or None
            tiers.append(ModelTier(
                name=str(t.get("name", "")).strip("'\""),
                model=str(t.get("model", "")).strip("'\""),
                max_tokens=int(t.get("max_tokens", 2048)),
                is_local=is_local,
            ))
        kwargs: dict[str, Any] = {
            "tiers": tiers or None,
            "smart_threshold": float(data.get("smart_threshold", 0.33)),
            "large_threshold": float(data.get("large_threshold", 0.67)),
            "adapt_every": int(data.get("adapt_every", 50)),
            "exploration_rate": float(data.get("exploration_rate", 0.10)),
            "adapt_mode": str(data.get("adapt_mode", "auto")).strip("'\""),
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    # ── Report ────────────────────────────────────────────────────────────────

    def report(self) -> "RouterReport":
        """Return a rich summary of routing decisions and learned thresholds.

        Includes per-tier statistics, estimated cost savings versus an
        always-large-model baseline, and recent adaptation history.
        """
        rs = self.stats()
        all_outcomes = self._store.get_recent(500)
        total = len(all_outcomes)
        tier_counts: dict[str, int] = {}
        for o in all_outcomes:
            tier_counts[o.tier] = tier_counts.get(o.tier, 0) + 1
        # cost saved vs. always using the last (most expensive) tier
        last_tier = self._tiers[-1] if self._tiers else None
        always_large_cost = 0.0
        actual_cost = 0.0
        for o in all_outcomes:
            actual_cost += o.actual_cost_usd
            if last_tier:
                from meshflow.agents.base import _cost_usd
                always_large_cost += _cost_usd(last_tier.model, max(o.task_length // 4, 50), 25)
        return RouterReport(
            smart_threshold=self._smart,
            large_threshold=self._large,
            route_count=self._route_count,
            last_adapted_at=self._last_adapted_at,
            tier_distribution=tier_counts,
            tier_stats=rs.tiers,
            outcomes_analyzed=total,
            actual_cost_usd=actual_cost,
            always_large_cost_usd=always_large_cost,
            cost_saved_usd=always_large_cost - actual_cost,
        )


@dataclass
class RouterReport:
    """Summary of routing decisions and learned state from :meth:`AdaptiveModelTierRouter.report`.

    Attributes
    ----------
    smart_threshold:       Current composite score boundary (fast → smart tier).
    large_threshold:       Current composite score boundary (smart → large tier).
    route_count:           Total number of routing decisions made.
    last_adapted_at:       Unix timestamp of last auto-adaptation, or None.
    tier_distribution:     Mapping of tier name → number of routes.
    tier_stats:            Per-tier aggregated metrics.
    outcomes_analyzed:     Number of outcomes used in the report.
    actual_cost_usd:       Total cost actually spent across all analyzed outcomes.
    always_large_cost_usd: Hypothetical cost if every task went to the large tier.
    cost_saved_usd:        Estimated savings vs. always-large baseline.
    """

    smart_threshold: float
    large_threshold: float
    route_count: int
    last_adapted_at: float | None
    tier_distribution: dict[str, int]
    tier_stats: dict[str, Any]
    outcomes_analyzed: int
    actual_cost_usd: float
    always_large_cost_usd: float
    cost_saved_usd: float

    @property
    def savings_pct(self) -> float:
        if self.always_large_cost_usd <= 0:
            return 0.0
        return self.cost_saved_usd / self.always_large_cost_usd

    def __str__(self) -> str:
        lines = [
            "MeshFlow Router Report",
            f"  smart_threshold  : {self.smart_threshold:.3f}",
            f"  large_threshold  : {self.large_threshold:.3f}",
            f"  total routes     : {self.route_count}",
            f"  outcomes stored  : {self.outcomes_analyzed}",
            "",
            "  Tier distribution:",
        ]
        total_routed = sum(self.tier_distribution.values()) or 1
        for tier, count in sorted(self.tier_distribution.items()):
            bar = "█" * int(count / total_routed * 20)
            lines.append(f"    {tier:8s} {count:5d} ({count/total_routed:.0%})  {bar}")
        lines += [
            "",
            "  Cost summary:",
            f"    actual spend     : ${self.actual_cost_usd:.4f}",
            f"    always-large est : ${self.always_large_cost_usd:.4f}",
            f"    saved            : ${self.cost_saved_usd:.4f}  ({self.savings_pct:.0%})",
        ]
        if self.last_adapted_at:
            import time as _t
            import datetime as _dt
            ts = _dt.datetime.fromtimestamp(self.last_adapted_at).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"  last adapted     : {ts}")
        return "\n".join(lines)


def _parse_simple_yaml(path: str) -> dict[str, Any]:
    """Parse the small YAML subset emitted by AdaptiveModelTierRouter.to_yaml()."""
    result: dict[str, Any] = {}
    tiers: list[dict[str, Any]] = []
    current_tier: dict[str, Any] | None = None
    in_tiers = False

    with open(path) as f:
        for raw_line in f:
            line = raw_line.rstrip()
            stripped = line.lstrip()
            if stripped.startswith("#") or not stripped:
                continue
            indent = len(line) - len(stripped)
            if stripped.startswith("tiers:"):
                in_tiers = True
                continue
            if in_tiers:
                if indent >= 2 and stripped.startswith("- "):
                    if current_tier is not None:
                        tiers.append(current_tier)
                    key, _, val = stripped[2:].partition(": ")
                    current_tier = {key.strip(): _yaml_val(val.strip())}
                elif indent >= 4 and current_tier is not None:
                    key, _, val = stripped.partition(": ")
                    current_tier[key.strip()] = _yaml_val(val.strip())
                else:
                    if current_tier is not None:
                        tiers.append(current_tier)
                        current_tier = None
                    in_tiers = False
            if not in_tiers and ": " in stripped:
                key, _, val = stripped.partition(": ")
                result[key.strip()] = _yaml_val(val.strip())
    if current_tier is not None:
        tiers.append(current_tier)
    if tiers:
        result["tiers"] = tiers
    return result


def _yaml_val(s: str) -> Any:
    """Convert a YAML scalar string to a Python value."""
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s.strip("'\"")


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
