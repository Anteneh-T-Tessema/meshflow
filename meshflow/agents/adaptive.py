"""AdaptiveAgent — dynamic model switching based on task complexity and output quality.

Closes the AutoGen/token-opt gap: during execution, the agent evaluates whether
the current task warrants an expensive model.  If the task is simple, it
downgrades to a cheaper model automatically.  If the output is poor quality
(low confidence / high uncertainty), it upgrades and retries.

Usage::

    from meshflow import Agent
    from meshflow.agents.adaptive import AdaptiveAgent

    base = Agent(name="executor", role="executor", model="claude-sonnet-4-6")
    agent = AdaptiveAgent(
        base,
        cheap_model="claude-haiku-4-5-20251001",
        expensive_model="claude-sonnet-4-6",
        quality_threshold=0.7,  # upgrade if output confidence < 0.7
        downgrade_on_simple=True,
    )
    result = await agent.run("What is 2 + 2?")
    # Runs on haiku (simple task detected), no upgrade needed

    result = await agent.run("Audit this HIPAA policy document for violations.")
    # Runs on sonnet (complex task), validates quality
"""

from __future__ import annotations

import re
from typing import Any


# ── Complexity heuristics ─────────────────────────────────────────────────────

_COMPLEX_PATTERNS = re.compile(
    r"\b(audit|analyze|critique|debug|optimize|refactor|architect|synthesize|"
    r"evaluate|diagnose|compliance|security|legal|hipaa|gdpr|sox|pci|"
    r"multi-step|step-by-step|explain in detail|comprehensive)\b",
    re.IGNORECASE,
)

_SIMPLE_PATTERNS = re.compile(
    r"^(what is|define|list|name|translate|summarize in one|give me|"
    r"how many|when did|who is|spell)\b",
    re.IGNORECASE,
)


def _task_complexity(task: str) -> str:
    """Return 'simple', 'medium', or 'complex' based on task heuristics."""
    if _COMPLEX_PATTERNS.search(task):
        return "complex"
    if _SIMPLE_PATTERNS.match(task.strip()):
        return "simple"
    if len(task.split()) < 15:
        return "simple"
    if len(task.split()) > 80:
        return "complex"
    return "medium"


def _extract_confidence(text: str) -> float:
    m = re.search(r"CONFIDENCE:\s*(0?\.\d+|1\.0+)", text, re.IGNORECASE)
    if m:
        try:
            return min(1.0, max(0.0, float(m.group(1))))
        except ValueError:
            pass
    return 0.8  # optimistic default


# ── AdaptiveAgent ─────────────────────────────────────────────────────────────

class AdaptiveAgent:
    """Wraps an Agent with cost-aware dynamic model selection.

    Parameters
    ----------
    base_agent:        The base ``Agent`` instance.
    cheap_model:       Model to use for simple/medium tasks.
    expensive_model:   Model to use for complex tasks.
    quality_threshold: If output confidence < this, retry on *expensive_model*.
    downgrade_on_simple: Automatically use *cheap_model* for simple tasks.
    upgrade_on_low_quality: Retry with *expensive_model* when quality is low.
    max_retries:       Max upgrade retries on quality failure.
    """

    def __init__(
        self,
        base_agent: Any,
        *,
        cheap_model: str = "claude-haiku-4-5-20251001",
        expensive_model: str = "claude-sonnet-4-6",
        quality_threshold: float = 0.7,
        downgrade_on_simple: bool = True,
        upgrade_on_low_quality: bool = True,
        max_retries: int = 1,
    ) -> None:
        self._base = base_agent
        self._cheap = cheap_model
        self._expensive = expensive_model
        self._quality_threshold = quality_threshold
        self._downgrade = downgrade_on_simple
        self._upgrade = upgrade_on_low_quality
        self._max_retries = max_retries

    async def run(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run with automatic model selection and quality-based upgrade."""
        ctx = context or {}
        complexity = _task_complexity(task)

        # ── Downgrade to cheap model for simple/medium tasks ──────────────────
        original_model = getattr(self._base, "model", "")
        if self._downgrade and complexity in ("simple", "medium"):
            selected_model = self._cheap
        elif complexity == "complex":
            selected_model = self._expensive
        else:
            selected_model = original_model or self._expensive

        # Apply model override
        self._set_model(selected_model)
        result = await self._base.run(task, ctx)
        confidence = _extract_confidence(result.get("result", ""))
        result["_model_used"] = selected_model
        result["_task_complexity"] = complexity

        # ── Upgrade retry on low-quality output ───────────────────────────────
        if self._upgrade and confidence < self._quality_threshold and selected_model != self._expensive:
            for attempt in range(self._max_retries):
                self._set_model(self._expensive)
                retry_result = await self._base.run(task, ctx)
                retry_conf = _extract_confidence(retry_result.get("result", ""))
                retry_result["_model_used"] = self._expensive
                retry_result["_task_complexity"] = complexity
                retry_result["_upgraded"] = True
                retry_result["_upgrade_reason"] = f"confidence {confidence:.2f} < threshold {self._quality_threshold}"
                if retry_conf >= self._quality_threshold:
                    self._set_model(original_model)
                    return retry_result
                confidence = retry_conf
            self._set_model(original_model)
            return retry_result  # type: ignore[possibly-undefined]

        self._set_model(original_model)
        return result

    def _set_model(self, model: str) -> None:
        """Swap the model on the underlying agent."""
        if not model:
            return
        try:
            object.__setattr__(self._base, "model", model)
        except (AttributeError, TypeError):
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


__all__ = ["AdaptiveAgent", "_task_complexity"]
