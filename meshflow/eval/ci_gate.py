"""CI budget gate — blocks a build when token/cost regression exceeds threshold.

Closes the token-optimisation CI gap: a single function call compares a new
eval result against the stored baseline and exits non-zero (or raises) when
the regression exceeds the configured threshold.

Usage in CI (Python)::

    from meshflow.eval.ci_gate import CIBudgetGate

    gate = CIBudgetGate(
        baseline_path="eval_baseline.json",
        max_token_regression=0.10,   # fail if tokens increase > 10 %
        max_cost_regression=0.10,    # fail if cost   increases > 10 %
        max_quality_regression=0.05, # fail if pass_rate drops  > 5 pp
    )
    exit_code = gate.check(new_result_path="eval_current.json")
    sys.exit(exit_code)

Usage in GitHub Actions (see .github/workflows/cost-regression.yml)::

    meshflow eval <suite.yaml> --save-baseline eval_current.json
    python -m meshflow.eval.ci_gate \\
        --baseline eval_baseline.json \\
        --current  eval_current.json  \\
        --max-token-regression 0.10
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RegressionReport:
    """Detailed regression comparison between baseline and current run."""

    baseline_tokens: int
    current_tokens: int
    token_delta_pct: float

    baseline_cost_usd: float
    current_cost_usd: float
    cost_delta_pct: float

    baseline_pass_rate: float
    current_pass_rate: float
    pass_rate_delta_pp: float  # percentage points

    token_regression: bool
    cost_regression: bool
    quality_regression: bool

    @property
    def any_regression(self) -> bool:
        return self.token_regression or self.cost_regression or self.quality_regression

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_delta_pct":    round(self.token_delta_pct, 4),
            "cost_delta_pct":     round(self.cost_delta_pct, 4),
            "pass_rate_delta_pp": round(self.pass_rate_delta_pp, 4),
            "token_regression":   self.token_regression,
            "cost_regression":    self.cost_regression,
            "quality_regression": self.quality_regression,
            "any_regression":     self.any_regression,
            "baseline_tokens":    self.baseline_tokens,
            "current_tokens":     self.current_tokens,
            "baseline_cost_usd":  round(self.baseline_cost_usd, 6),
            "current_cost_usd":   round(self.current_cost_usd, 6),
            "baseline_pass_rate": round(self.baseline_pass_rate, 4),
            "current_pass_rate":  round(self.current_pass_rate, 4),
        }

    def summary_lines(self) -> list[str]:
        lines = [
            f"  Tokens : {self.baseline_tokens:,} → {self.current_tokens:,}  "
            f"({self.token_delta_pct:+.1%})  {'REGRESSION' if self.token_regression else 'OK'}",
            f"  Cost   : ${self.baseline_cost_usd:.5f} → ${self.current_cost_usd:.5f}  "
            f"({self.cost_delta_pct:+.1%})  {'REGRESSION' if self.cost_regression else 'OK'}",
            f"  Quality: {self.baseline_pass_rate:.1%} → {self.current_pass_rate:.1%}  "
            f"({self.pass_rate_delta_pp:+.1%})  {'REGRESSION' if self.quality_regression else 'OK'}",
        ]
        return lines


def _load_baseline(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Baseline not found: {path}")
    with open(p) as fh:
        return json.load(fh)


def _extract_metrics(data: dict[str, Any]) -> tuple[int, float, float]:
    """Return (total_tokens, total_cost_usd, pass_rate)."""
    tokens = int(data.get("total_tokens", data.get("tokens", 0)))
    cost = float(data.get("total_cost_usd", data.get("cost_usd", 0.0)))
    rate = float(data.get("pass_rate", data.get("pass_rate_pct", 1.0)))
    # Handle percentage (0–100) vs fraction (0–1)
    if rate > 1.5:
        rate = rate / 100.0
    return tokens, cost, rate


class CIBudgetGate:
    """Compares a new eval result against a stored baseline and fails if regressions exceed thresholds.

    Parameters
    ----------
    baseline_path:          Path to the JSON baseline produced by a prior run.
    max_token_regression:   Fraction (e.g. 0.10 = 10 %) by which tokens may increase.
    max_cost_regression:    Fraction by which cost may increase.
    max_quality_regression: Percentage-point drop in pass_rate that is acceptable.
    """

    def __init__(
        self,
        baseline_path: str = "eval_baseline.json",
        max_token_regression: float = 0.10,
        max_cost_regression: float = 0.10,
        max_quality_regression: float = 0.05,
    ) -> None:
        self._baseline_path = baseline_path
        self._max_tokens = max_token_regression
        self._max_cost = max_cost_regression
        self._max_quality = max_quality_regression

    def compare(self, current: dict[str, Any]) -> RegressionReport:
        """Compare *current* metrics against the baseline JSON."""
        baseline = _load_baseline(self._baseline_path)
        b_tok, b_cost, b_rate = _extract_metrics(baseline)
        c_tok, c_cost, c_rate = _extract_metrics(current)

        tok_delta = (c_tok - b_tok) / max(b_tok, 1)
        cost_delta = (c_cost - b_cost) / max(b_cost, 1e-9)
        rate_delta = c_rate - b_rate  # in pp

        return RegressionReport(
            baseline_tokens=b_tok,
            current_tokens=c_tok,
            token_delta_pct=tok_delta,
            baseline_cost_usd=b_cost,
            current_cost_usd=c_cost,
            cost_delta_pct=cost_delta,
            baseline_pass_rate=b_rate,
            current_pass_rate=c_rate,
            pass_rate_delta_pp=rate_delta,
            token_regression=tok_delta > self._max_tokens,
            cost_regression=cost_delta > self._max_cost,
            quality_regression=rate_delta < -self._max_quality,
        )

    def check(self, new_result_path: str, *, verbose: bool = True) -> int:
        """Load *new_result_path* JSON, compare, and return exit code (0=OK, 1=FAIL)."""
        try:
            current = _load_baseline(new_result_path)
        except FileNotFoundError as exc:
            print(f"[ci-gate] ERROR: {exc}", file=sys.stderr)
            return 2

        try:
            report = self.compare(current)
        except FileNotFoundError as exc:
            print(f"[ci-gate] SKIP: baseline not found ({exc}). "
                  "Run with --save-baseline to create one.", file=sys.stderr)
            return 0  # Don't fail if there's no baseline yet

        if verbose:
            print("\n  [ci-gate] Regression report:")
            for line in report.summary_lines():
                print(line)
            print()

        if report.any_regression:
            print("  [ci-gate] FAILED: regression detected.", file=sys.stderr)
            return 1

        if verbose:
            print("  [ci-gate] PASSED: no regressions.")
        return 0

    def check_dict(self, current: dict[str, Any]) -> RegressionReport:
        """Compare a metrics dict directly (no file I/O)."""
        return self.compare(current)


# ── CLI entry point ────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m meshflow.eval.ci_gate",
        description="MeshFlow CI budget gate — fail build on cost/quality regression",
    )
    parser.add_argument("--baseline", required=True, help="Baseline JSON path")
    parser.add_argument("--current",  required=True, help="Current eval result JSON path")
    parser.add_argument("--max-token-regression", type=float, default=0.10)
    parser.add_argument("--max-cost-regression",  type=float, default=0.10)
    parser.add_argument("--max-quality-regression", type=float, default=0.05)
    args = parser.parse_args()

    gate = CIBudgetGate(
        baseline_path=args.baseline,
        max_token_regression=args.max_token_regression,
        max_cost_regression=args.max_cost_regression,
        max_quality_regression=args.max_quality_regression,
    )
    sys.exit(gate.check(args.current))


if __name__ == "__main__":
    _cli()


__all__ = ["CIBudgetGate", "RegressionReport"]
