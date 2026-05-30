"""Quality regression gate — fail CI when agent quality drops.

Complements :class:`~meshflow.eval.ci_gate.CIBudgetGate` (which gates on cost)
by gating on judge scores.  Used in ``.github/workflows/`` to block merges that
degrade output quality.

Usage::

    # In CI: save baseline
    gate = QualityGate(baseline_path="quality_baseline.json", threshold=0.05)
    gate.save_baseline({"avg_score": 0.82, "pass_rate": 0.91, "n": 40})

    # On each PR: compare current run
    report = gate.compare({"avg_score": 0.78, "pass_rate": 0.88, "n": 40})
    sys.exit(gate.exit_code(report))   # 0 = ok, 1 = regression

CLI::

    python -m meshflow.eval.quality_gate --baseline quality_baseline.json \\
           --current current_scores.json --avg-drop 0.05 --pass-rate-drop 0.03
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass
class QualityReport:
    """Outcome of comparing current scores against baseline."""

    baseline_avg: float
    current_avg: float
    baseline_pass_rate: float
    current_pass_rate: float
    avg_regression: bool
    pass_rate_regression: bool
    avg_delta: float
    pass_rate_delta: float
    n_scenarios: int

    @property
    def any_regression(self) -> bool:
        return self.avg_regression or self.pass_rate_regression

    @property
    def passed(self) -> bool:
        return not self.any_regression

    def summary_lines(self) -> list[str]:
        lines = [
            f"  avg_score:  {self.baseline_avg:.4f} → {self.current_avg:.4f} "
            f"(Δ={self.avg_delta:+.4f}) {'⚠ REGRESSION' if self.avg_regression else '✓'}",
            f"  pass_rate:  {self.baseline_pass_rate:.4f} → {self.current_pass_rate:.4f} "
            f"(Δ={self.pass_rate_delta:+.4f}) {'⚠ REGRESSION' if self.pass_rate_regression else '✓'}",
            f"  scenarios:  {self.n_scenarios}",
        ]
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_avg": self.baseline_avg,
            "current_avg": self.current_avg,
            "baseline_pass_rate": self.baseline_pass_rate,
            "current_pass_rate": self.current_pass_rate,
            "avg_delta": self.avg_delta,
            "pass_rate_delta": self.pass_rate_delta,
            "avg_regression": self.avg_regression,
            "pass_rate_regression": self.pass_rate_regression,
            "any_regression": self.any_regression,
            "n_scenarios": self.n_scenarios,
        }


class QualityGate:
    """Compare judge scores against a saved baseline and flag regressions.

    Parameters
    ----------
    baseline_path:
        Path to a JSON file containing baseline metrics.  Created by
        :meth:`save_baseline`.
    avg_drop_threshold:
        Maximum allowed drop in ``avg_score`` before flagging a regression.
        Default 0.05 (5 percentage points).
    pass_rate_drop_threshold:
        Maximum allowed drop in ``pass_rate`` before flagging a regression.
        Default 0.05.
    """

    def __init__(
        self,
        baseline_path: str = "quality_baseline.json",
        *,
        avg_drop_threshold: float = 0.05,
        pass_rate_drop_threshold: float = 0.05,
    ) -> None:
        self._path = baseline_path
        self._avg_thr = avg_drop_threshold
        self._pr_thr = pass_rate_drop_threshold

    # ── Baseline management ────────────────────────────────────────────────────

    def save_baseline(self, metrics: dict[str, Any]) -> None:
        """Write *metrics* to the baseline file.

        Minimum required keys: ``avg_score``, ``pass_rate``.
        """
        with open(self._path, "w") as fh:
            json.dump(metrics, fh, indent=2)

    def load_baseline(self) -> dict[str, Any] | None:
        if not os.path.exists(self._path):
            return None
        with open(self._path) as fh:
            return json.load(fh)

    # ── Comparison ─────────────────────────────────────────────────────────────

    def compare(self, current: dict[str, Any]) -> QualityReport:
        """Compare *current* metrics against the saved baseline.

        Parameters
        ----------
        current:
            Dict with keys ``avg_score`` and ``pass_rate`` (both floats 0–1).
        """
        baseline = self.load_baseline()
        if baseline is None:
            # No baseline yet — treat as no regression
            avg = float(current.get("avg_score", 0))
            pr = float(current.get("pass_rate", 0))
            return QualityReport(
                baseline_avg=avg,
                current_avg=avg,
                baseline_pass_rate=pr,
                current_pass_rate=pr,
                avg_regression=False,
                pass_rate_regression=False,
                avg_delta=0.0,
                pass_rate_delta=0.0,
                n_scenarios=int(current.get("n", 0)),
            )

        b_avg = float(baseline.get("avg_score", 0))
        b_pr = float(baseline.get("pass_rate", 0))
        c_avg = float(current.get("avg_score", 0))
        c_pr = float(current.get("pass_rate", 0))

        avg_delta = round(c_avg - b_avg, 6)
        pr_delta = round(c_pr - b_pr, 6)

        return QualityReport(
            baseline_avg=b_avg,
            current_avg=c_avg,
            baseline_pass_rate=b_pr,
            current_pass_rate=c_pr,
            avg_regression=avg_delta < -self._avg_thr,
            pass_rate_regression=pr_delta < -self._pr_thr,
            avg_delta=avg_delta,
            pass_rate_delta=pr_delta,
            n_scenarios=int(current.get("n", 0)),
        )

    def exit_code(self, report: QualityReport) -> int:
        """Return 0 if no regression, 1 if regression detected (for ``sys.exit``)."""
        return 1 if report.any_regression else 0

    def check(
        self,
        current: dict[str, Any],
        *,
        verbose: bool = True,
        update_baseline_on_pass: bool = False,
    ) -> int:
        """Run the full gate check and print a summary.

        Returns the exit code (0 = pass, 1 = regression).
        """
        report = self.compare(current)
        if verbose:
            status = "PASS" if report.passed else "QUALITY REGRESSION DETECTED"
            print(f"\n  Quality Gate: {status}")
            for line in report.summary_lines():
                print(line)
            print()

        if report.passed and update_baseline_on_pass:
            self.save_baseline(current)
            if verbose:
                print(f"  Baseline updated → {self._path}")

        return self.exit_code(report)

    async def check_suite(
        self,
        suite_result: Any,
        *,
        verbose: bool = True,
        update_baseline_on_pass: bool = False,
    ) -> int:
        """Run the gate on a :class:`~meshflow.eval.judge.JudgeSuiteResult`.

        Extracts ``avg_score``, ``pass_rate``, and ``n`` automatically.
        """
        metrics = {
            "avg_score": suite_result.avg_score,
            "pass_rate": suite_result.pass_rate,
            "n": len(suite_result.scores),
        }
        return self.check(
            metrics,
            verbose=verbose,
            update_baseline_on_pass=update_baseline_on_pass,
        )


def _cli() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="MeshFlow quality regression gate")
    parser.add_argument("--baseline", required=True, help="Baseline JSON file")
    parser.add_argument("--current", required=True, help="Current scores JSON file")
    parser.add_argument("--avg-drop", type=float, default=0.05,
                        help="Max allowed avg_score drop (default 0.05)")
    parser.add_argument("--pass-rate-drop", type=float, default=0.05,
                        help="Max allowed pass_rate drop (default 0.05)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Update baseline if current passes")
    args = parser.parse_args()

    with open(args.current) as fh:
        current = json.load(fh)

    gate = QualityGate(
        baseline_path=args.baseline,
        avg_drop_threshold=args.avg_drop,
        pass_rate_drop_threshold=args.pass_rate_drop,
    )
    code = gate.check(current, update_baseline_on_pass=args.update_baseline)
    sys.exit(code)


if __name__ == "__main__":
    _cli()
