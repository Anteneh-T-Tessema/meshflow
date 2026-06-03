"""Cost regression CI gate — automated defense against token cost regressions.

Tracks per-workflow token and USD spend across versions.  A regression is
detected when cost increases by more than a configured threshold compared to
a stored baseline.

Classes
-------
CostBaseline        — stores a named cost snapshot
CostRegressionGate  — compares current run against baseline; raises on regression
CostRegressionError — raised when spend regresses beyond threshold
CostRegressionReport— structured diff report

CLI usage::

    # Save current spend as the new baseline
    meshflow eval cost-baseline set --name v1.13 --workflow my_workflow.yaml

    # Compare a new run against the baseline
    meshflow eval cost-baseline compare --name v1.13 --workflow my_workflow.yaml

Python usage::

    from meshflow.eval.cost_regression import CostRegressionGate, CostBaseline

    gate = CostRegressionGate(
        baseline_path="cost_baselines.json",
        usd_threshold=0.10,      # fail if cost increases by > $0.10
        token_threshold_pct=0.15, # fail if tokens increase by > 15%
    )

    # After a workflow run:
    gate.check("my_pipeline", total_cost_usd=0.042, total_tokens=1200)
    # Raises CostRegressionError if cost has increased beyond threshold
    # vs the stored baseline for "my_pipeline"

    # Record a new baseline:
    gate.record("my_pipeline", total_cost_usd=0.038, total_tokens=1100)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── CostBaseline ──────────────────────────────────────────────────────────────

@dataclass
class CostBaseline:
    """Stored cost snapshot for one workflow/pipeline."""
    name: str
    total_cost_usd: float
    total_tokens: int
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meshflow_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ── CostRegressionError ───────────────────────────────────────────────────────

class CostRegressionError(RuntimeError):
    """Raised by :class:`CostRegressionGate` when spend regresses beyond threshold.

    Attributes
    ----------
    name:           Workflow name.
    baseline_usd:   Baseline cost.
    current_usd:    Current run cost.
    delta_usd:      Absolute increase (current - baseline).
    delta_pct:      Percentage increase.
    threshold_usd:  Configured USD threshold.
    """

    def __init__(
        self,
        name: str,
        baseline_usd: float,
        current_usd: float,
        delta_usd: float,
        delta_pct: float,
        threshold_usd: float,
        threshold_pct: float,
        dimension: str = "usd",
    ) -> None:
        self.name = name
        self.baseline_usd = baseline_usd
        self.current_usd = current_usd
        self.delta_usd = delta_usd
        self.delta_pct = delta_pct
        self.threshold_usd = threshold_usd
        self.threshold_pct = threshold_pct
        self.dimension = dimension
        super().__init__(
            f"Cost regression in '{name}': "
            f"${baseline_usd:.4f} → ${current_usd:.4f} "
            f"(+${delta_usd:.4f}, +{delta_pct:.1%}) "
            f"exceeds threshold (${threshold_usd:.4f} / {threshold_pct:.0%})"
        )


# ── CostRegressionReport ──────────────────────────────────────────────────────

@dataclass
class CostRegressionReport:
    """Structured diff between current run and baseline."""
    name: str
    has_baseline: bool
    regressed: bool
    baseline_usd: float
    current_usd: float
    delta_usd: float
    delta_pct: float
    baseline_tokens: int
    current_tokens: int
    token_delta: int
    token_delta_pct: float
    threshold_usd: float
    threshold_token_pct: float
    verdict: str    # "PASS" | "REGRESSION" | "NO_BASELINE"

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def summary(self) -> str:
        if not self.has_baseline:
            return f"[{self.name}] No baseline — recorded current as new baseline."
        sign = "+" if self.delta_usd >= 0 else ""
        tok_sign = "+" if self.token_delta >= 0 else ""
        status = "✓ PASS" if not self.regressed else "✗ REGRESSION"
        return (
            f"[{self.name}] {status}  "
            f"USD: ${self.baseline_usd:.4f} → ${self.current_usd:.4f} "
            f"({sign}{self.delta_pct:.1%})  "
            f"Tokens: {self.baseline_tokens:,} → {self.current_tokens:,} "
            f"({tok_sign}{self.token_delta_pct:.1%})"
        )


# ── CostRegressionGate ────────────────────────────────────────────────────────

class CostRegressionGate:
    """Compares workflow cost against stored baselines and raises on regression.

    Parameters
    ----------
    baseline_path:
        JSON file where baselines are persisted.
        Default: ``"meshflow_cost_baselines.json"`` in the working directory.
    usd_threshold:
        Absolute USD increase that triggers a regression (default: $0.05).
    token_threshold_pct:
        Relative token increase that triggers a regression (default: 20%).
    raise_on_regression:
        When True (default), raise :class:`CostRegressionError` on regression.
        When False, return the report without raising.
    auto_record_new:
        When True (default), automatically record current run as baseline
        when no baseline exists for the workflow name.
    """

    def __init__(
        self,
        baseline_path: str = "meshflow_cost_baselines.json",
        usd_threshold: float = 0.05,
        token_threshold_pct: float = 0.20,
        raise_on_regression: bool = True,
        auto_record_new: bool = True,
    ) -> None:
        self.baseline_path = baseline_path
        self.usd_threshold = usd_threshold
        self.token_threshold_pct = token_threshold_pct
        self.raise_on_regression = raise_on_regression
        self.auto_record_new = auto_record_new
        self._baselines: dict[str, CostBaseline] = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def check(
        self,
        name: str,
        total_cost_usd: float,
        total_tokens: int,
        metadata: dict[str, Any] | None = None,
    ) -> CostRegressionReport:
        """Check current spend against baseline for *name*.

        Parameters
        ----------
        name:           Workflow / pipeline identifier.
        total_cost_usd: Current run cost in USD.
        total_tokens:   Current run total token count.
        metadata:       Optional metadata to attach (version, commit, etc.).

        Returns
        -------
        :class:`CostRegressionReport`

        Raises
        ------
        :class:`CostRegressionError`
            When cost regresses beyond threshold and ``raise_on_regression=True``.
        """
        baseline = self._baselines.get(name)

        if baseline is None:
            if self.auto_record_new:
                self.record(name, total_cost_usd, total_tokens, metadata)
            return CostRegressionReport(
                name=name, has_baseline=False, regressed=False,
                baseline_usd=0.0, current_usd=total_cost_usd,
                delta_usd=0.0, delta_pct=0.0,
                baseline_tokens=0, current_tokens=total_tokens,
                token_delta=0, token_delta_pct=0.0,
                threshold_usd=self.usd_threshold,
                threshold_token_pct=self.token_threshold_pct,
                verdict="NO_BASELINE",
            )

        delta_usd = total_cost_usd - baseline.total_cost_usd
        delta_pct = delta_usd / baseline.total_cost_usd if baseline.total_cost_usd > 0 else 0.0
        token_delta = total_tokens - baseline.total_tokens
        token_delta_pct = (
            token_delta / baseline.total_tokens if baseline.total_tokens > 0 else 0.0
        )

        usd_regressed   = delta_usd > self.usd_threshold
        token_regressed = token_delta_pct > self.token_threshold_pct
        regressed       = usd_regressed or token_regressed

        report = CostRegressionReport(
            name=name, has_baseline=True, regressed=regressed,
            baseline_usd=baseline.total_cost_usd,
            current_usd=total_cost_usd,
            delta_usd=delta_usd, delta_pct=delta_pct,
            baseline_tokens=baseline.total_tokens,
            current_tokens=total_tokens,
            token_delta=token_delta, token_delta_pct=token_delta_pct,
            threshold_usd=self.usd_threshold,
            threshold_token_pct=self.token_threshold_pct,
            verdict="REGRESSION" if regressed else "PASS",
        )

        if regressed and self.raise_on_regression:
            dimension = "usd" if usd_regressed else "tokens"
            raise CostRegressionError(
                name=name,
                baseline_usd=baseline.total_cost_usd,
                current_usd=total_cost_usd,
                delta_usd=delta_usd,
                delta_pct=delta_pct,
                threshold_usd=self.usd_threshold,
                threshold_pct=self.token_threshold_pct,
                dimension=dimension,
            )

        return report

    def record(
        self,
        name: str,
        total_cost_usd: float,
        total_tokens: int,
        metadata: dict[str, Any] | None = None,
    ) -> CostBaseline:
        """Store *name* as the new baseline."""
        try:
            import meshflow
            version = meshflow.__version__
        except Exception:
            version = ""

        baseline = CostBaseline(
            name=name,
            total_cost_usd=total_cost_usd,
            total_tokens=total_tokens,
            meshflow_version=version,
            metadata=metadata or {},
        )
        self._baselines[name] = baseline
        self._save()
        return baseline

    def delete(self, name: str) -> bool:
        """Remove a stored baseline.  Returns True if it existed."""
        if name in self._baselines:
            del self._baselines[name]
            self._save()
            return True
        return False

    def list_baselines(self) -> list[CostBaseline]:
        return list(self._baselines.values())

    def get(self, name: str) -> CostBaseline | None:
        return self._baselines.get(name)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, CostBaseline]:
        if not os.path.exists(self.baseline_path):
            return {}
        try:
            with open(self.baseline_path) as fh:
                data = json.load(fh)
            return {
                name: CostBaseline(**entry)
                for name, entry in data.items()
            }
        except Exception:
            return {}

    def _save(self) -> None:
        data = {name: asdict(bl) for name, bl in self._baselines.items()}
        with open(self.baseline_path, "w") as fh:
            json.dump(data, fh, indent=2)
