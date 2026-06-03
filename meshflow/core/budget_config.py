"""ThinkingBudget + EffortBudget — Claude extended thinking and effort controls.

Surfaces Claude's native thinking token budget and effort level (low/medium/high)
as first-class MeshFlow concepts, tracked alongside USD cost in WorkflowResult.

Classes
-------
ThinkingBudget     — token-level budget for extended thinking
EffortBudget       — high-level effort tier (maps to thinking token budgets)
BudgetConfig       — unified budget config (combines CostCap + thinking + effort)
BudgetUsage        — actual spend recorded after a run
BudgetViolation    — raised when a budget is exceeded

Integration
-----------
Pass a BudgetConfig to Workflow::

    from meshflow import Workflow, Agent
    from meshflow.core.budget_config import BudgetConfig, ThinkingBudget, EffortBudget

    wf = Workflow(
        budget=BudgetConfig(
            usd_cap=0.50,
            thinking=ThinkingBudget(tokens=4000, enabled=True),
            effort=EffortBudget(level="medium"),
        )
    )
    result = wf.run("Explain RL from first principles.")
    print(result.thinking_tokens_used)   # how many thinking tokens were consumed
    print(result.effort_level)           # resolved effort level
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Effort levels ─────────────────────────────────────────────────────────────

_EFFORT_TOKEN_MAP: dict[str, int] = {
    "low":    1_024,
    "medium": 4_096,
    "high":   16_000,
    "max":    32_000,
}

EffortLevel = Literal["low", "medium", "high", "max"]


# ── ThinkingBudget ─────────────────────────────────────────────────────────────

@dataclass
class ThinkingBudget:
    """Token-level budget for Claude extended thinking.

    Attributes
    ----------
    tokens:
        Maximum tokens Claude may spend on internal reasoning per step.
        Corresponds to ``thinking.budget_tokens`` in the Anthropic API.
    enabled:
        When False, extended thinking is disabled even if the model supports it.
    track:
        When True (default), thinking token usage is recorded in
        ``WorkflowResult.thinking_tokens_used``.
    """
    tokens: int = 2_000
    enabled: bool = True
    track: bool = True

    def to_anthropic_param(self) -> dict[str, object]:
        """Return the dict expected by ``anthropic.messages.create(thinking=...)``."""
        if not self.enabled:
            return {"type": "disabled"}
        return {"type": "enabled", "budget_tokens": self.tokens}

    def __post_init__(self) -> None:
        if self.tokens < 0:
            raise ValueError("ThinkingBudget.tokens must be non-negative")


# ── EffortBudget ──────────────────────────────────────────────────────────────

@dataclass
class EffortBudget:
    """High-level effort tier — maps to a thinking token budget.

    Parameters
    ----------
    level:
        One of ``"low"`` (1 024 tokens), ``"medium"`` (4 096), ``"high"``
        (16 000), ``"max"`` (32 000).

    ``EffortBudget("high")`` is equivalent to
    ``ThinkingBudget(tokens=16_000, enabled=True)``.
    """
    level: EffortLevel = "medium"

    def to_thinking_budget(self) -> ThinkingBudget:
        """Resolve to a concrete :class:`ThinkingBudget`."""
        return ThinkingBudget(tokens=_EFFORT_TOKEN_MAP[self.level])

    @property
    def tokens(self) -> int:
        return _EFFORT_TOKEN_MAP[self.level]

    def __post_init__(self) -> None:
        if self.level not in _EFFORT_TOKEN_MAP:
            raise ValueError(
                f"EffortBudget.level must be one of {list(_EFFORT_TOKEN_MAP)}; "
                f"got {self.level!r}"
            )


# ── BudgetConfig ──────────────────────────────────────────────────────────────

@dataclass
class BudgetConfig:
    """Unified budget configuration for a Workflow run.

    Combines:
    - USD cost cap (mirrors :class:`~meshflow.core.workflow.CostCap`)
    - Extended thinking token budget
    - Effort level

    All three constraints are enforced independently; exceeding any one
    raises :class:`BudgetViolation` (if ``raise_on_exceed=True``).

    Parameters
    ----------
    usd_cap:
        Hard USD limit per run.  0.0 means no USD limit.
    thinking:
        :class:`ThinkingBudget` — token budget for extended reasoning.
        Takes precedence over ``effort`` if both are set.
    effort:
        :class:`EffortBudget` — convenience shorthand for thinking budget.
    raise_on_exceed:
        When True (default), raise :class:`BudgetViolation` if any limit is
        exceeded.  When False, log a warning and continue.
    """
    usd_cap: float = 0.0
    thinking: ThinkingBudget | None = None
    effort: EffortBudget | None = None
    raise_on_exceed: bool = True

    def resolved_thinking_budget(self) -> ThinkingBudget | None:
        """Return the effective ThinkingBudget (thinking > effort > None)."""
        if self.thinking is not None:
            return self.thinking
        if self.effort is not None:
            return self.effort.to_thinking_budget()
        return None

    def check_usd(self, spent: float) -> None:
        """Raise BudgetViolation if *spent* exceeds usd_cap."""
        if self.usd_cap > 0 and spent > self.usd_cap:
            if self.raise_on_exceed:
                raise BudgetViolation(
                    f"USD budget exceeded: spent ${spent:.4f} > cap ${self.usd_cap:.4f}"
                )

    def check_thinking_tokens(self, used: int) -> None:
        """Raise BudgetViolation if thinking token usage exceeds budget."""
        tb = self.resolved_thinking_budget()
        if tb is not None and tb.enabled and used > tb.tokens:
            if self.raise_on_exceed:
                raise BudgetViolation(
                    f"Thinking token budget exceeded: used {used} > budget {tb.tokens}"
                )


# ── BudgetUsage ───────────────────────────────────────────────────────────────

@dataclass
class BudgetUsage:
    """Actual budget consumption recorded after a run.

    Stored in ``WorkflowResult.budget_usage`` when a :class:`BudgetConfig` is
    attached to the Workflow.
    """
    usd_spent: float = 0.0
    thinking_tokens_used: int = 0
    output_tokens_used: int = 0
    input_tokens_used: int = 0
    effort_level: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens_used + self.output_tokens_used + self.thinking_tokens_used

    def to_dict(self) -> dict[str, object]:
        return {
            "usd_spent": self.usd_spent,
            "thinking_tokens_used": self.thinking_tokens_used,
            "output_tokens_used": self.output_tokens_used,
            "input_tokens_used": self.input_tokens_used,
            "total_tokens": self.total_tokens,
            "effort_level": self.effort_level,
        }


# ── BudgetViolation ───────────────────────────────────────────────────────────

class BudgetViolation(RuntimeError):
    """Raised when any budget constraint (USD, thinking tokens, effort) is exceeded."""
