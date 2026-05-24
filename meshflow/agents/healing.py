"""Self-healing orchestration — automatic retry, model-switch, and escalation.

When an agent produces a blocked, low-confidence, or error result, the
healing layer applies a configurable sequence of recovery strategies before
giving up.

Usage::

    from meshflow import Agent
    from meshflow.agents.healing import HealingPolicy, HealingStrategy

    agent = Agent(
        name="analyst",
        role="researcher",
        healing=HealingPolicy(
            confidence_threshold=0.7,
            strategies=[
                HealingStrategy.retry_same,
                HealingStrategy.retry_different_model,
                HealingStrategy.escalate_to_supervisor,
            ],
            fallback_models=["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
            max_retries=3,
        ),
    )
    result = await agent.run("Analyse HIPAA compliance gaps")
    # result["healed"] == True  if a strategy recovered
    # result["healing_attempts"] == 2  how many extra attempts were needed
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass


class HealingStrategy(str, Enum):
    """Ordered recovery strategy applied when an agent step fails or under-performs."""

    retry_same             = "retry_same"
    retry_different_model  = "retry_different_model"
    escalate_to_supervisor = "escalate_to_supervisor"
    fallback_to_cache      = "fallback_to_cache"


@dataclass
class HealingPolicy:
    """Configuration for self-healing behaviour.

    Parameters
    ----------
    confidence_threshold:
        Minimum ``stated_confidence`` accepted as a passing result (0–1).
        Results below this threshold trigger healing. Default 0.5.
    strategies:
        Ordered list of strategies to try. The first strategy that produces
        an acceptable result wins; remaining strategies are skipped.
    fallback_models:
        Model names tried (in order) when :attr:`HealingStrategy.retry_different_model`
        fires.  Defaults to ``["claude-haiku-4-5-20251001"]``.
    max_retries:
        Maximum total healing attempts (across all strategies). Default 3.
    retry_delay_s:
        Seconds to wait between retry attempts. Default 0 (no delay).
    supervisor_prompt:
        System prompt for the escalation supervisor agent.
    """

    confidence_threshold: float = 0.5
    strategies: list[HealingStrategy] = field(default_factory=lambda: [
        HealingStrategy.retry_same,
        HealingStrategy.retry_different_model,
        HealingStrategy.escalate_to_supervisor,
    ])
    fallback_models: list[str] = field(
        default_factory=lambda: ["claude-haiku-4-5-20251001"]
    )
    max_retries: int = 3
    retry_delay_s: float = 0.0
    supervisor_prompt: str = (
        "You are a senior reviewer. The following agent response was "
        "unsatisfactory. Improve it with higher accuracy and confidence."
    )

    def is_passing(self, result: dict[str, Any]) -> bool:
        """Return True if *result* meets the quality bar."""
        if result.get("blocked", False):
            return False
        if result.get("error"):
            return False
        confidence = result.get("stated_confidence", 1.0)
        return confidence >= self.confidence_threshold


@dataclass
class HealingResult:
    """Wraps an agent result dict with healing metadata."""

    result: dict[str, Any]
    attempts: int = 1
    strategies_tried: list[str] = field(default_factory=list)
    healed: bool = False

    def __getitem__(self, key: str) -> Any:
        return self.result[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.result.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.result

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.result,
            "healed": self.healed,
            "healing_attempts": self.attempts,
            "healing_strategies_tried": self.strategies_tried,
        }


# ── Core healing logic ─────────────────────────────────────────────────────────

async def run_with_healing(
    agent: Any,  # meshflow.agents.builder.Agent
    task: str,
    context: dict[str, Any] | None = None,
    policy: HealingPolicy | None = None,
) -> HealingResult:
    """Run *agent* on *task* applying *policy* healing strategies on failure.

    Parameters
    ----------
    agent:   An :class:`~meshflow.agents.builder.Agent` instance.
    task:    The task string.
    context: Optional extra context.
    policy:  Overrides ``agent.healing`` when provided.
    """
    p = policy or getattr(agent, "healing", None) or HealingPolicy()
    ctx = context or {}

    # ── Initial attempt ──────────────────────────────────────────────────────
    result = await agent.run(task, ctx)
    if p.is_passing(result):
        return HealingResult(result=result, attempts=1)

    strategies_tried: list[str] = []
    total_attempts = 1
    best_result = result  # keep best attempt in case all strategies fail

    for strategy in p.strategies:
        if total_attempts >= p.max_retries + 1:
            break

        strategies_tried.append(strategy.value)

        if p.retry_delay_s > 0:
            await asyncio.sleep(p.retry_delay_s)

        if strategy == HealingStrategy.retry_same:
            result = await agent.run(task, ctx)
            total_attempts += 1

        elif strategy == HealingStrategy.retry_different_model:
            from meshflow.agents.builder import Agent as _Agent
            import dataclasses as _dc
            for fallback_model in p.fallback_models:
                if total_attempts >= p.max_retries + 1:
                    break
                fb_agent = _dc.replace(agent, model=fallback_model)
                result = await fb_agent.run(task, ctx)
                total_attempts += 1
                if p.is_passing(result):
                    break

        elif strategy == HealingStrategy.escalate_to_supervisor:
            from meshflow.agents.builder import Agent as _Agent
            import dataclasses as _dc
            sup_task = (
                f"{p.supervisor_prompt}\n\n"
                f"Original task: {task}\n\n"
                f"Unsatisfactory response: {best_result.get('result', '')}\n\n"
                f"Please provide a better response."
            )
            sup_model = p.fallback_models[0] if p.fallback_models else agent.model
            supervisor = _dc.replace(
                agent,
                name=f"{agent.name}_supervisor",
                model=sup_model,
                system_prompt=p.supervisor_prompt,
            )
            result = await supervisor.run(sup_task, ctx)
            total_attempts += 1

        elif strategy == HealingStrategy.fallback_to_cache:
            # Try the agent's cache (if any) with a slightly looser threshold
            cache_obj = getattr(agent, "cache", None)
            if cache_obj and cache_obj is not True and cache_obj is not False:
                from meshflow.cache.core import _make_key, _prompt_text
                msgs = [{"role": "user", "content": task}]
                cached = cache_obj.get_semantic(
                    agent.model or "unknown",
                    agent.system_prompt or "",
                    msgs,
                )
                if cached is not None:
                    result = {
                        "result": cached.response,
                        "agent_name": agent.name,
                        "tokens": cached.tokens,
                        "cost_usd": 0.0,
                        "stated_confidence": p.confidence_threshold,
                        "blocked": False,
                        "guardrail_results": [],
                        "from_cache": True,
                    }
                    total_attempts += 1

        # Keep the best non-blocked result
        if not result.get("blocked", False) and not result.get("error"):
            curr_conf = result.get("stated_confidence", 0.0)
            best_conf = best_result.get("stated_confidence", 0.0)
            if curr_conf >= best_conf:
                best_result = result

        if p.is_passing(result):
            return HealingResult(
                result=result,
                attempts=total_attempts,
                strategies_tried=strategies_tried,
                healed=True,
            )

    # All strategies exhausted — return best result we found
    return HealingResult(
        result=best_result,
        attempts=total_attempts,
        strategies_tried=strategies_tried,
        healed=False,
    )
