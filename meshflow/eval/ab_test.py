"""A/B testing — compare two agent configurations on the same eval set.

Runs both variants against identical scenarios, grades outputs with LLMJudge,
and reports the winner with a simple effect-size metric.

Usage::

    from meshflow.eval.ab_test import ABTest, ABVariant
    from meshflow.agents.base import EchoProvider

    control  = ABVariant("gpt-4o",  provider=EchoProvider("control answer"))
    variant  = ABVariant("claude-haiku", provider=EchoProvider("variant answer"))

    ab = ABTest(control=control, variant=variant)
    result = await ab.run(["What is HIPAA?", "Explain SOC 2 Type II."])

    print(result.winner)          # "control" | "variant" | "tie"
    print(result.delta)           # avg score difference (variant − control)
    print(result.summary())
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ABVariant:
    """One side of an A/B test."""

    name: str
    agent: Any = None
    provider: Any = None
    model: str = "claude-haiku-4-5"
    system_prompt: str = ""


@dataclass
class ABTurnResult:
    """Per-scenario scores for both variants."""

    scenario: str
    control_output: str
    variant_output: str
    control_score: float
    variant_score: float
    control_reasoning: str
    variant_reasoning: str

    @property
    def delta(self) -> float:
        return round(self.variant_score - self.control_score, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario[:200],
            "control_score": self.control_score,
            "variant_score": self.variant_score,
            "delta": self.delta,
            "control_output": self.control_output[:200],
            "variant_output": self.variant_output[:200],
        }


@dataclass
class ABTestResult:
    """Aggregated result of an A/B test."""

    control_name: str
    variant_name: str
    turn_results: list[ABTurnResult]
    total_duration_ms: float

    @property
    def control_avg(self) -> float:
        if not self.turn_results:
            return 0.0
        return round(sum(t.control_score for t in self.turn_results) / len(self.turn_results), 4)

    @property
    def variant_avg(self) -> float:
        if not self.turn_results:
            return 0.0
        return round(sum(t.variant_score for t in self.turn_results) / len(self.turn_results), 4)

    @property
    def delta(self) -> float:
        return round(self.variant_avg - self.control_avg, 4)

    @property
    def winner(self) -> str:
        d = self.delta
        if abs(d) < 0.02:  # < 2 percentage points → tie
            return "tie"
        return self.variant_name if d > 0 else self.control_name

    @property
    def effect_size(self) -> float:
        """Cohen's d approximation over per-scenario deltas."""
        deltas = [t.delta for t in self.turn_results]
        if len(deltas) < 2:
            return abs(self.delta)
        mean = sum(deltas) / len(deltas)
        variance = sum((d - mean) ** 2 for d in deltas) / (len(deltas) - 1)
        std = math.sqrt(variance) if variance > 0 else 1e-9
        return round(abs(mean) / std, 4)

    @property
    def control_win_rate(self) -> float:
        if not self.turn_results:
            return 0.0
        wins = sum(1 for t in self.turn_results if t.control_score > t.variant_score)
        return round(wins / len(self.turn_results), 4)

    @property
    def variant_win_rate(self) -> float:
        if not self.turn_results:
            return 0.0
        wins = sum(1 for t in self.turn_results if t.variant_score > t.control_score)
        return round(wins / len(self.turn_results), 4)

    def summary(self) -> str:
        w = self.winner
        label = "TIE" if w == "tie" else f"{w.upper()} WINS"
        return (
            f"[{label}] {self.control_name}={self.control_avg:.3f} vs "
            f"{self.variant_name}={self.variant_avg:.3f} "
            f"(Δ={self.delta:+.3f}, effect={self.effect_size:.2f}) "
            f"over {len(self.turn_results)} scenarios"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "control": self.control_name,
            "variant": self.variant_name,
            "winner": self.winner,
            "control_avg": self.control_avg,
            "variant_avg": self.variant_avg,
            "delta": self.delta,
            "effect_size": self.effect_size,
            "control_win_rate": self.control_win_rate,
            "variant_win_rate": self.variant_win_rate,
            "n_scenarios": len(self.turn_results),
            "total_duration_ms": round(self.total_duration_ms, 1),
            "turns": [t.to_dict() for t in self.turn_results],
        }


class ABTest:
    """Compare two agent variants on the same set of scenarios.

    Parameters
    ----------
    control:   The baseline variant (current production config).
    variant:   The challenger variant (new config being tested).
    judge:     Optional :class:`~meshflow.eval.judge.LLMJudge`.
               Auto-created with EchoProvider if ANTHROPIC_API_KEY is absent.
    rubric:    Default judging rubric applied to all scenarios.
    """

    def __init__(
        self,
        control: ABVariant,
        variant: ABVariant,
        *,
        judge: Any = None,
        rubric: str = "",
    ) -> None:
        self._control = control
        self._variant = variant
        self._judge = judge
        self._rubric = rubric

    def _get_judge(self) -> Any:
        if self._judge is not None:
            return self._judge
        from meshflow.eval.judge import LLMJudge
        return LLMJudge()

    async def _get_output(self, variant: ABVariant, scenario: str) -> str:
        if variant.agent is not None:
            result = await variant.agent.run(scenario)
            return result.get("result", "") if isinstance(result, dict) else str(result)

        if variant.provider is not None:
            content, _, _ = await variant.provider.complete(
                model=variant.model,
                messages=[{"role": "user", "content": scenario}],
                system=variant.system_prompt or "You are a helpful assistant.",
                max_tokens=1024,
            )
            return content

        from meshflow.agents.providers import auto_detect_provider
        prov = auto_detect_provider()
        content, _, _ = await prov.complete(
            model=variant.model,
            messages=[{"role": "user", "content": scenario}],
            system=variant.system_prompt or "You are a helpful assistant.",
            max_tokens=1024,
        )
        return content

    async def run(
        self,
        scenarios: list[str],
        *,
        rubric: str = "",
    ) -> ABTestResult:
        """Run *scenarios* through both variants and return :class:`ABTestResult`.

        Parameters
        ----------
        scenarios: List of task/prompt strings to evaluate.
        rubric:    Per-run rubric override (falls back to instance rubric).
        """
        import asyncio

        judge = self._get_judge()
        effective_rubric = rubric or self._rubric
        t0 = time.monotonic()
        turn_results: list[ABTurnResult] = []

        for scenario in scenarios:
            # Run both variants concurrently
            ctrl_out, var_out = await asyncio.gather(
                self._get_output(self._control, scenario),
                self._get_output(self._variant, scenario),
            )

            # Score both concurrently
            ctrl_score, var_score = await asyncio.gather(
                judge.score(scenario, ctrl_out, rubric=effective_rubric),
                judge.score(scenario, var_out, rubric=effective_rubric),
            )

            turn_results.append(ABTurnResult(
                scenario=scenario,
                control_output=ctrl_out,
                variant_output=var_out,
                control_score=ctrl_score.score,
                variant_score=var_score.score,
                control_reasoning=ctrl_score.reasoning,
                variant_reasoning=var_score.reasoning,
            ))

        total_ms = (time.monotonic() - t0) * 1000
        return ABTestResult(
            control_name=self._control.name,
            variant_name=self._variant.name,
            turn_results=turn_results,
            total_duration_ms=total_ms,
        )
