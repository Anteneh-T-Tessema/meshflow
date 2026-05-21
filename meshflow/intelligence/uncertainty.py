"""L2.11 — Uncertainty Quantification: SAUP + UProp + calibration.

Three interlocking mechanisms:
1. SemanticConsistencyScorer — multi-angle consistency check
2. UncertaintyPropagator — UProp multiplication across handoffs
3. CalibrationTracker — corrects systematic overconfidence (EMA)

Adaptive response is graduated: warn → slow → verify → HITL → abort.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from meshflow.core.schemas import UncertaintyScore


@dataclass
class ConsistencyResult:
    score: float          # 0–1, higher is more consistent
    num_queries: int
    variance: float
    low_consistency: bool


@dataclass
class CalibrationRecord:
    agent_id: str
    stated_confidences: list[float] = field(default_factory=list)
    actual_accuracies: list[float] = field(default_factory=list)
    ema_bias: float = 0.0    # positive = overconfident


class SemanticConsistencyScorer:
    """Detects inconsistency in LLM outputs across rephrased queries.

    LLMs are systematically overconfident when verbalising their own confidence
    and give different answers to semantically equivalent questions.
    We compare token overlap across multiple phrasings.
    """

    def score(self, outputs: list[str]) -> ConsistencyResult:
        if len(outputs) < 2:
            return ConsistencyResult(score=1.0, num_queries=len(outputs), variance=0.0, low_consistency=False)

        # Compute pairwise Jaccard similarity on token sets
        token_sets = [set(o.lower().split()) for o in outputs]
        pairs = []
        for i in range(len(token_sets)):
            for j in range(i + 1, len(token_sets)):
                a, b = token_sets[i], token_sets[j]
                union = a | b
                if not union:
                    pairs.append(1.0)
                else:
                    pairs.append(len(a & b) / len(union))

        mean_sim = statistics.mean(pairs) if pairs else 1.0
        variance = statistics.variance(pairs) if len(pairs) > 1 else 0.0

        return ConsistencyResult(
            score=mean_sim,
            num_queries=len(outputs),
            variance=variance,
            low_consistency=mean_sim < 0.35,
        )


class UncertaintyPropagator:
    """UProp: uncertainty compounds multiplicatively across agent handoffs.

    Agent A at 0.7 uncertainty → Agent B receives 0.56 floor before producing a token.
    This prevents overconfident downstream agents from hiding upstream errors.
    """

    def propagate(self, upstream_calibrated: float, downstream_raw: float) -> float:
        """Compute downstream calibrated uncertainty after propagation."""
        floor = upstream_calibrated * 0.9  # 10% dampening — upstream error decays slightly
        return min(downstream_raw, floor)

    def chain(self, confidences: list[float]) -> float:
        """Compound confidence across a chain of agents."""
        result = 1.0
        for c in confidences:
            result *= max(0.0, min(1.0, c))
        return result


class CalibrationTracker:
    """Corrects for systematic LLM overconfidence using EMA.

    LLMs state 0.9 confidence when historical accuracy is 0.6 — this tracker
    learns the bias per agent and corrects stated confidence down automatically.
    """

    EMA_ALPHA = 0.2

    def __init__(self) -> None:
        self._records: dict[str, CalibrationRecord] = {}

    def record(self, agent_id: str, stated: float, actual: float) -> None:
        r = self._records.setdefault(agent_id, CalibrationRecord(agent_id))
        r.stated_confidences.append(stated)
        r.actual_accuracies.append(actual)
        bias = stated - actual
        r.ema_bias = self.EMA_ALPHA * bias + (1 - self.EMA_ALPHA) * r.ema_bias

    def calibrate(self, agent_id: str, stated: float) -> float:
        r = self._records.get(agent_id)
        if not r or len(r.stated_confidences) < 3:
            # No calibration data — apply conservative 10% discount
            return stated * 0.90
        corrected = stated - r.ema_bias
        return max(0.01, min(0.99, corrected))

    def bias(self, agent_id: str) -> float:
        r = self._records.get(agent_id)
        return r.ema_bias if r else 0.0


class MixtureOfModelsRouter:
    """Routes tasks to cheapest model tier that can handle them reliably.

    Calibration history per task_type × model_tier drives routing decisions.
    Avoids homogeneous model deployment — heterogeneous tiers improve both
    quality and cost.
    """

    def __init__(self) -> None:
        # task_type → {model_tier → [accuracy]}
        self._accuracy: dict[str, dict[str, list[float]]] = {}

    def record(self, task_type: str, model_tier: str, accuracy: float) -> None:
        self._accuracy.setdefault(task_type, {}).setdefault(model_tier, []).append(accuracy)

    def route(
        self,
        task_type: str,
        available_tiers: list[str],
        accuracy_threshold: float = 0.85,
    ) -> str:
        """Return cheapest tier that meets accuracy threshold, else most expensive."""
        task_data = self._accuracy.get(task_type, {})
        # Sort by assumed cost: haiku < sonnet < opus
        tier_cost = {"haiku": 1, "sonnet": 2, "opus": 3}
        sorted_tiers = sorted(
            available_tiers,
            key=lambda t: next((v for k, v in tier_cost.items() if k in t.lower()), 99),
        )
        for tier in sorted_tiers:
            hist = task_data.get(tier, [])
            if len(hist) >= 3 and statistics.mean(hist) >= accuracy_threshold:
                return tier
        return sorted_tiers[-1] if sorted_tiers else available_tiers[0]


class UncertaintyEngine:
    """Coordinates all three uncertainty mechanisms for a single agent turn."""

    WARN_THRESHOLD    = 0.70
    SLOW_THRESHOLD    = 0.55
    VERIFY_THRESHOLD  = 0.40
    HITL_THRESHOLD    = 0.25
    ABORT_THRESHOLD   = 0.10

    def __init__(self) -> None:
        self._consistency = SemanticConsistencyScorer()
        self._propagator  = UncertaintyPropagator()
        self._calibration = CalibrationTracker()
        self._router      = MixtureOfModelsRouter()
        self._chain: list[float] = []    # per-run confidence chain

    def evaluate(
        self,
        agent_id: str,
        outputs: list[str],
        stated_confidence: float,
        upstream_calibrated: float | None = None,
    ) -> UncertaintyScore:
        """Full uncertainty evaluation for one agent turn."""
        # 1. Semantic consistency
        consistency = self._consistency.score(outputs)

        # 2. Calibration correction
        calibrated = self._calibration.calibrate(agent_id, stated_confidence)

        # 3. Propagation from upstream
        if upstream_calibrated is not None:
            propagated = self._propagator.propagate(upstream_calibrated, calibrated)
        else:
            propagated = calibrated

        # 4. Composite score (weighted average)
        composite = (
            0.4 * propagated
            + 0.4 * consistency.score
            + 0.2 * calibrated
        )
        self._chain.append(composite)

        return UncertaintyScore(
            raw=stated_confidence,
            calibrated=calibrated,
            propagated=propagated,
            consistency=consistency.score,
            composite=composite,
            should_escalate=composite < self.HITL_THRESHOLD,
            should_abort=composite < self.ABORT_THRESHOLD,
        )

    def adaptive_response(self, score: UncertaintyScore) -> dict[str, Any]:
        c = score.composite
        if c < self.ABORT_THRESHOLD:
            return {"action": "abort", "reason": f"Uncertainty too high: {c:.2f}"}
        if c < self.HITL_THRESHOLD:
            return {"action": "escalate_human", "reason": f"Uncertainty requires human: {c:.2f}"}
        if c < self.VERIFY_THRESHOLD:
            return {"action": "verify_with_critic", "reason": f"Uncertainty warrants critic: {c:.2f}"}
        if c < self.SLOW_THRESHOLD:
            return {"action": "slow_down", "reason": f"Moderate uncertainty: {c:.2f}"}
        if c < self.WARN_THRESHOLD:
            return {"action": "warn", "reason": f"Low confidence flagged: {c:.2f}"}
        return {"action": "proceed", "reason": "Confidence acceptable"}

    def record_outcome(self, agent_id: str, stated: float, actual: float) -> None:
        self._calibration.record(agent_id, stated, actual)

    def chain_confidence(self) -> float:
        return self._propagator.chain(self._chain) if self._chain else 1.0
