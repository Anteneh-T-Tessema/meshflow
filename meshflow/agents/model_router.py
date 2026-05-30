"""ModelRouter — task-complexity-aware model tier routing.

Classifies a task description and routes it to the appropriate model tier
(nano / small / medium / large) before any LLM call is made. Configurable
via YAML or Python dict, and emits routing decisions to cost analytics.

This closes the token-optimization whitespace: none of the six competing
frameworks (LangGraph, CrewAI, AutoGen, Dify, Flowise, Haystack) have a
systematic pre-dispatch routing layer.

Usage::

    from meshflow.agents.model_router import ModelRouter, RouterConfig

    router = ModelRouter()                       # defaults
    decision = router.route("What is 2+2?")
    print(decision.model)                        # claude-haiku-4-5-20251001
    print(decision.tier)                         # nano
    print(decision.rationale)

    # YAML-configurable tiers
    config = RouterConfig.from_yaml("router.yaml")
    router = ModelRouter(config=config)

    # Use with an Agent
    agent = Agent(name="worker", role="executor", model=router.route(task).model)

YAML schema::

    model_router:
      tiers:
        nano:   claude-haiku-4-5-20251001
        small:  claude-haiku-4-5-20251001
        medium: claude-sonnet-4-6
        large:  claude-opus-4-8
      thresholds:
        token_nano:   200     # estimated tokens below which → nano
        token_small:  800     # below which → small
        token_medium: 3000    # below which → medium
        # above medium → large
      complexity_keywords:
        large:  [audit, compliance, diagnose, architect, synthesize, evaluate]
        medium: [analyze, explain, compare, debug, optimize, summarize]
      fallback: medium
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_TIERS: dict[str, str] = {
    "nano":   "claude-haiku-4-5-20251001",
    "small":  "claude-haiku-4-5-20251001",
    "medium": "claude-sonnet-4-6",
    "large":  "claude-opus-4-8",
}

_DEFAULT_THRESHOLDS = {
    "token_nano":   200,
    "token_small":  800,
    "token_medium": 3000,
}

_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "large": [
        "audit", "compliance", "hipaa", "gdpr", "sox", "pci", "diagnose",
        "architect", "synthesize", "evaluate", "critical", "security", "forensic",
        "negotiate", "arbitrate", "refactor", "multi-step", "comprehensive",
    ],
    "medium": [
        "analyze", "explain", "compare", "debug", "optimize", "summarize",
        "outline", "draft", "review", "plan", "design", "write", "generate",
    ],
}

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    words = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
    return max(1, int(len(words) * 1.35))


# ── RouterConfig ──────────────────────────────────────────────────────────────


@dataclass
class RouterConfig:
    """Configures model tier thresholds and model names.

    Parameters
    ----------
    tiers:
        Mapping of tier name → model string.
    thresholds:
        Token count breakpoints for tier assignment.
    complexity_keywords:
        Per-tier keyword lists. Checked before token-count thresholds.
    fallback:
        Default tier if nothing matches (default: ``"medium"``).
    """

    tiers: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_TIERS))
    thresholds: dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_THRESHOLDS))
    complexity_keywords: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in _DEFAULT_KEYWORDS.items()}
    )
    fallback: str = "medium"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouterConfig":
        """Load from a dict (e.g. parsed from YAML ``model_router:`` section)."""
        section = data.get("model_router", data)
        return cls(
            tiers={**_DEFAULT_TIERS, **section.get("tiers", {})},
            thresholds={**_DEFAULT_THRESHOLDS, **section.get("thresholds", {})},
            complexity_keywords={
                **{k: list(v) for k, v in _DEFAULT_KEYWORDS.items()},
                **section.get("complexity_keywords", {}),
            },
            fallback=section.get("fallback", "medium"),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "RouterConfig":
        """Load from a YAML file."""
        import yaml
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f) or {})


# ── RoutingDecision ───────────────────────────────────────────────────────────


@dataclass
class RoutingDecision:
    """Result of a model routing classification.

    Attributes
    ----------
    tier:       Tier name (``"nano"`` | ``"small"`` | ``"medium"`` | ``"large"``).
    model:      Model string resolved from the config.
    rationale:  Human-readable explanation of why this tier was chosen.
    token_estimate: Approximate token count used for the decision.
    cost_multiplier: Relative cost vs. medium tier (informational).
    """

    tier: str
    model: str
    rationale: str
    token_estimate: int
    cost_multiplier: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "model": self.model,
            "rationale": self.rationale,
            "token_estimate": self.token_estimate,
            "cost_multiplier": self.cost_multiplier,
        }


# ── ModelRouter ───────────────────────────────────────────────────────────────


class ModelRouter:
    """Classifies a task and returns the optimal model tier + model string.

    Classification pipeline (first match wins):
    1. Keyword matching — task text scanned for per-tier keyword lists
       (``large`` keywords checked first, then ``medium``)
    2. Token-count thresholds — estimated token footprint of the task
    3. Fallback tier from config

    Parameters
    ----------
    config:
        A ``RouterConfig`` instance. Defaults to built-in sensible values.
    record_decisions:
        If True, routing decisions are appended to ``self.history`` for
        analytics / cost reporting (default: False).

    Example
    -------
    ::

        router = ModelRouter()
        dec = router.route("Audit this HIPAA policy for violations")
        # → RoutingDecision(tier='large', model='claude-opus-4-8', ...)
    """

    _COST_MULTIPLIERS: dict[str, float] = {
        "nano":   0.05,
        "small":  0.05,
        "medium": 1.00,
        "large":  5.00,
    }

    def __init__(
        self,
        config: RouterConfig | None = None,
        *,
        record_decisions: bool = False,
        analytics_ledger: Any = None,
    ) -> None:
        self._config = config or RouterConfig()
        self._record = record_decisions
        self.history: list[RoutingDecision] = []
        self._ledger = analytics_ledger  # optional ReplayLedger for cost analytics emission

    def route(self, task: str, *, tools: list[Any] | None = None) -> RoutingDecision:
        """Classify *task* and return the appropriate model tier.

        Parameters
        ----------
        task:
            The task description / user message to classify.
        tools:
            Optional list of tool schemas. Many tools → bump to medium+.
        """
        task_lower = task.lower()
        token_est = _estimate_tokens(task)

        # 1. Keyword matching (ordered: large → medium; nano/small have no keywords)
        for tier in ("large", "medium"):
            keywords = self._config.complexity_keywords.get(tier, [])
            for kw in keywords:
                pattern = r"\b" + re.escape(kw.lower()) + r"\b"
                if re.search(pattern, task_lower):
                    model = self._config.tiers.get(tier, _DEFAULT_TIERS.get(tier, "claude-sonnet-4-6"))
                    decision = RoutingDecision(
                        tier=tier,
                        model=model,
                        rationale=f"Keyword match: '{kw}' → {tier} tier",
                        token_estimate=token_est,
                        cost_multiplier=self._COST_MULTIPLIERS.get(tier, 1.0),
                    )
                    return self._record_and_return(decision)

        # 2. Tool count bump
        if tools and len(tools) >= 3:
            tier = "medium"
            model = self._config.tiers.get(tier, _DEFAULT_TIERS["medium"])
            decision = RoutingDecision(
                tier=tier,
                model=model,
                rationale=f"Tool count ({len(tools)}) ≥ 3 → medium tier",
                token_estimate=token_est,
                cost_multiplier=self._COST_MULTIPLIERS["medium"],
            )
            return self._record_and_return(decision)

        # 3. Token count thresholds
        nano_thresh = self._config.thresholds.get("token_nano", 200)
        small_thresh = self._config.thresholds.get("token_small", 800)
        medium_thresh = self._config.thresholds.get("token_medium", 3000)

        if token_est <= nano_thresh:
            tier = "nano"
        elif token_est <= small_thresh:
            tier = "small"
        elif token_est <= medium_thresh:
            tier = "medium"
        else:
            tier = "large"

        model = self._config.tiers.get(tier, _DEFAULT_TIERS.get(tier, "claude-sonnet-4-6"))
        decision = RoutingDecision(
            tier=tier,
            model=model,
            rationale=f"Token estimate {token_est} → {tier} tier (threshold: {nano_thresh}/{small_thresh}/{medium_thresh})",
            token_estimate=token_est,
            cost_multiplier=self._COST_MULTIPLIERS.get(tier, 1.0),
        )
        return self._record_and_return(decision)

    def _record_and_return(self, decision: RoutingDecision) -> RoutingDecision:
        if self._record:
            self.history.append(decision)
        # Emit routing decision to cost analytics ledger if wired
        if self._ledger is not None:
            try:
                import asyncio
                import json as _json
                event = {
                    "event_type": "model_router_decision",
                    "tier": decision.tier,
                    "model": decision.model,
                    "token_estimate": decision.token_estimate,
                    "cost_multiplier": decision.cost_multiplier,
                    "rationale": decision.rationale,
                }
                # Non-blocking: fire-and-forget write to ledger metadata
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(
                            self._emit_to_ledger(event)
                        )
                except RuntimeError:
                    pass  # no event loop — skip silently
            except Exception:
                pass
        return decision

    async def _emit_to_ledger(self, event: dict[str, Any]) -> None:
        """Write a routing-decision event to the analytics ledger."""
        try:
            await self._ledger.write(
                run_id="model_router",
                step_id=f"route_{id(event)}",
                node_id="model_router",
                node_kind="router",
                output="",
                cost_usd=0.0,
                tokens_used=event.get("token_estimate", 0),
                blocked=False,
                metadata=event,
            )
        except Exception:
            pass

    def savings_vs_default(self, decisions: list[RoutingDecision] | None = None) -> dict[str, Any]:
        """Estimate cost savings vs. always using the large/medium model.

        Returns a dict with ``saved_pct`` and ``total_decisions``.
        """
        items = decisions if decisions is not None else self.history
        if not items:
            return {"saved_pct": 0.0, "total_decisions": 0}
        total_actual = sum(d.cost_multiplier for d in items)
        total_if_large = len(items) * self._COST_MULTIPLIERS["large"]
        saved_pct = round(100.0 * (1.0 - total_actual / total_if_large), 1)
        return {
            "saved_pct": max(0.0, saved_pct),
            "total_decisions": len(items),
            "actual_cost_units": round(total_actual, 2),
            "baseline_cost_units": round(total_if_large, 2),
        }


__all__ = ["ModelRouter", "RouterConfig", "RoutingDecision"]
