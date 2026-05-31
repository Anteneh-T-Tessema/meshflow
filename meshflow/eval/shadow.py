"""Sprint 43 — Shadow runner + regression detector.

ShadowRunner: run two agent versions on the same input and compare outputs.
RegressionDetector: detect confidence drops, cost spikes, block-rate increases.

Usage::

    from meshflow.eval.shadow import ShadowRunner, shadow_run
    from meshflow.eval.shadow import RegressionDetector

    # Shadow run
    result = await shadow_run(primary_agent, shadow_agent, "What is my balance?")
    print(result.agreement, result.delta_confidence)

    # Regression detection
    detector = RegressionDetector()
    detector.set_baseline("billing-agent", "confidence", 0.88)
    alerts = detector.check("billing-agent", recent_results)
    for alert in alerts:
        print(alert.severity, alert.metric, alert.delta)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshflow.agents.builder import Agent


# ── Shadow result ─────────────────────────────────────────────────────────────

@dataclass
class ShadowResult:
    """Result of running two agents on the same task.

    Attributes
    ----------
    primary_output:      Output from the primary (production) agent.
    shadow_output:       Output from the shadow (candidate) agent.
    primary_agent:       Name of the primary agent.
    shadow_agent:        Name of the shadow agent.
    primary_tokens:      Tokens used by primary.
    shadow_tokens:       Tokens used by shadow.
    primary_confidence:  Stated confidence from primary.
    shadow_confidence:   Stated confidence from shadow.
    primary_blocked:     Whether primary was blocked.
    shadow_blocked:      Whether shadow was blocked.
    agreement:           True if outputs are sufficiently similar.
    similarity:          Character-level similarity score (0–1).
    delta_confidence:    shadow_confidence − primary_confidence.
    delta_tokens:        shadow_tokens − primary_tokens.
    """

    primary_output: str
    shadow_output: str
    primary_agent: str
    shadow_agent: str
    primary_tokens: int = 0
    shadow_tokens: int = 0
    primary_confidence: float = 1.0
    shadow_confidence: float = 1.0
    primary_blocked: bool = False
    shadow_blocked: bool = False
    agreement: bool = False
    similarity: float = 0.0
    delta_confidence: float = 0.0
    delta_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_agent":      self.primary_agent,
            "shadow_agent":       self.shadow_agent,
            "primary_output":     self.primary_output[:500],
            "shadow_output":      self.shadow_output[:500],
            "primary_tokens":     self.primary_tokens,
            "shadow_tokens":      self.shadow_tokens,
            "primary_confidence": self.primary_confidence,
            "shadow_confidence":  self.shadow_confidence,
            "primary_blocked":    self.primary_blocked,
            "shadow_blocked":     self.shadow_blocked,
            "agreement":          self.agreement,
            "similarity":         round(self.similarity, 4),
            "delta_confidence":   round(self.delta_confidence, 4),
            "delta_tokens":       self.delta_tokens,
        }


async def shadow_run(
    primary: "Agent",
    shadow: "Agent",
    task: str,
    context: dict[str, Any] | None = None,
    *,
    similarity_threshold: float = 0.6,
) -> ShadowResult:
    """Run primary and shadow agents on the same task; compare results.

    Both agents run concurrently.  Agreement is determined by normalised
    character-level overlap (longest-common-subsequence approximation).

    Parameters
    ----------
    primary:              Production agent.
    shadow:               Candidate agent to evaluate.
    task:                 Task passed to both.
    context:              Context dict (same for both).
    similarity_threshold: Minimum similarity to count as "agreement" (0–1).
    """
    import asyncio

    ctx = context or {}
    p_task = asyncio.create_task(primary.run(task, ctx))
    s_task = asyncio.create_task(shadow.run(task, ctx))
    p_result, s_result = await asyncio.gather(p_task, s_task)

    p_out = p_result.get("result", "")
    s_out = s_result.get("result", "")
    sim = _text_similarity(p_out, s_out)
    p_conf = p_result.get("stated_confidence", 1.0)
    s_conf = s_result.get("stated_confidence", 1.0)
    p_tok = p_result.get("tokens", 0)
    s_tok = s_result.get("tokens", 0)

    return ShadowResult(
        primary_output=p_out,
        shadow_output=s_out,
        primary_agent=primary.name,
        shadow_agent=shadow.name,
        primary_tokens=p_tok,
        shadow_tokens=s_tok,
        primary_confidence=p_conf,
        shadow_confidence=s_conf,
        primary_blocked=p_result.get("blocked", False),
        shadow_blocked=s_result.get("blocked", False),
        agreement=sim >= similarity_threshold,
        similarity=sim,
        delta_confidence=s_conf - p_conf,
        delta_tokens=s_tok - p_tok,
    )


# ── Regression detection ───────────────────────────────────────────────────────

@dataclass
class RegressionAlert:
    """A detected regression in an agent metric.

    Attributes
    ----------
    agent_name:      The agent that regressed.
    metric:          The metric that changed (``"confidence"`` / ``"cost"`` / …).
    baseline_value:  Expected (baseline) value.
    current_value:   Observed current value.
    delta:           current − baseline.
    severity:        ``"warning"`` (|delta| ≥ 5 %) or ``"critical"`` (≥ 20 %).
    """

    agent_name: str
    metric: str
    baseline_value: float
    current_value: float
    delta: float
    severity: str = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name":     self.agent_name,
            "metric":         self.metric,
            "baseline_value": round(self.baseline_value, 6),
            "current_value":  round(self.current_value, 6),
            "delta":          round(self.delta, 6),
            "severity":       self.severity,
        }


class RegressionDetector:
    """Detect regressions by comparing current run metrics to a stored baseline.

    Usage::

        detector = RegressionDetector()
        detector.set_baseline("my-agent", "confidence", 0.88)
        detector.set_baseline("my-agent", "block_rate",  0.02)

        # After collecting recent results:
        alerts = detector.check("my-agent", recent_results)
    """

    # Thresholds for severity classification
    WARNING_DELTA  = 0.05   # 5 % relative change
    CRITICAL_DELTA = 0.20   # 20 % relative change

    def __init__(self) -> None:
        # {agent_name: {metric: baseline_value}}
        self._baselines: dict[str, dict[str, float]] = {}

    def set_baseline(self, agent_name: str, metric: str, value: float) -> None:
        """Record a baseline value for *metric* of *agent_name*."""
        self._baselines.setdefault(agent_name, {})[metric] = value

    def get_baseline(self, agent_name: str, metric: str) -> float | None:
        return self._baselines.get(agent_name, {}).get(metric)

    def check(
        self,
        agent_name: str,
        results: list[dict[str, Any]],
    ) -> list[RegressionAlert]:
        """Compare *results* against stored baselines; return any alerts.

        Parameters
        ----------
        agent_name:  Agent whose metrics to check.
        results:     List of result dicts from ``Agent.run()``.
        """
        if not results:
            return []
        alerts: list[RegressionAlert] = []
        baselines = self._baselines.get(agent_name, {})
        if not baselines:
            return []

        current = self._aggregate(results)
        for metric, baseline_val in baselines.items():
            current_val = current.get(metric)
            if current_val is None:
                continue
            delta = current_val - baseline_val
            if baseline_val == 0:
                rel = abs(delta)
            else:
                rel = abs(delta / baseline_val)
            if rel < self.WARNING_DELTA:
                continue
            severity = "critical" if rel >= self.CRITICAL_DELTA else "warning"
            alerts.append(RegressionAlert(
                agent_name=agent_name,
                metric=metric,
                baseline_value=baseline_val,
                current_value=current_val,
                delta=delta,
                severity=severity,
            ))
        return alerts

    def report(self, agent_name: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Full report: current metrics vs. baselines, plus any alerts."""
        alerts = self.check(agent_name, results)
        current = self._aggregate(results) if results else {}
        return {
            "agent_name":  agent_name,
            "n_results":   len(results),
            "current":     {k: round(v, 6) for k, v in current.items()},
            "baselines":   {k: round(v, 6) for k, v in self._baselines.get(agent_name, {}).items()},
            "alerts":      [a.to_dict() for a in alerts],
            "has_regression": bool(alerts),
        }

    @staticmethod
    def _aggregate(results: list[dict[str, Any]]) -> dict[str, float]:
        """Compute aggregate metrics from a list of result dicts."""
        confidences = [r.get("stated_confidence", 1.0) for r in results]
        costs       = [r.get("cost_usd", 0.0) for r in results]
        tokens      = [r.get("tokens", 0) for r in results]
        blocked     = [1.0 if r.get("blocked") else 0.0 for r in results]
        return {
            "confidence": statistics.mean(confidences) if confidences else 0.0,
            "cost":       statistics.mean(costs)       if costs else 0.0,
            "tokens":     statistics.mean(tokens)      if tokens else 0.0,
            "block_rate": statistics.mean(blocked)     if blocked else 0.0,
        }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _text_similarity(a: str, b: str) -> float:
    """Normalised character-level similarity (simplified Sørensen–Dice)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    # Bigram-based similarity
    def bigrams(s: str) -> set[str]:
        return {s[i:i+2] for i in range(len(s) - 1)}
    ba, bb = bigrams(a.lower()), bigrams(b.lower())
    if not ba and not bb:
        return 1.0
    intersection = len(ba & bb)
    return 2.0 * intersection / (len(ba) + len(bb)) if (ba or bb) else 0.0


__all__ = [
    "ShadowResult", "shadow_run",
    "RegressionAlert", "RegressionDetector",
]
