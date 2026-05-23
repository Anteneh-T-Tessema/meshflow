"""Eval baseline — golden-set versioning and regression diff for CI.

Usage:
    from meshflow.eval.baseline import EvalBaseline

    # After running an eval suite, save as the golden baseline:
    result = await suite.run(agent)
    baseline = EvalBaseline.from_result(result)
    baseline.save("evals/baseline.json")

    # On the next CI run, compare against it:
    old = EvalBaseline.load("evals/baseline.json")
    new = EvalBaseline.from_result(await suite.run(agent))
    diff = old.diff(new)
    print(diff.report())
    if diff.has_regressions:
        sys.exit(1)

CLI:
    meshflow eval evals.yaml --save-baseline baseline.json
    meshflow eval evals.yaml --compare-baseline baseline.json --fail-on-regression
    meshflow eval diff baseline_a.json baseline_b.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ScenarioBaseline:
    """Per-scenario snapshot stored in a baseline file."""

    name: str
    passed: bool
    score: float
    checks: dict[str, bool]
    tokens: int
    duration_ms: float


@dataclass
class EvalBaseline:
    """Serialisable snapshot of an EvalResult used as a golden reference.

    Attributes
    ----------
    suite_name  : Name of the EvalSuite.
    timestamp   : ISO-8601 timestamp when the baseline was captured.
    pass_rate   : Fraction of scenarios that passed (0–1).
    weighted_score : Weighted aggregate score (0–1).
    total_tokens   : Total tokens consumed by the run.
    scenarios   : Per-scenario snapshots keyed by scenario name.
    """

    suite_name: str
    timestamp: str
    pass_rate: float
    weighted_score: float
    total_tokens: int
    scenarios: dict[str, ScenarioBaseline] = field(default_factory=dict)

    # ── Factories ─────────────────────────────────────────────────────────────

    @classmethod
    def from_result(cls, result: Any) -> "EvalBaseline":
        """Build a baseline from an EvalResult."""
        scenarios = {
            sr.scenario_name: ScenarioBaseline(
                name=sr.scenario_name,
                passed=sr.passed,
                score=sr.score,
                checks=dict(sr.checks),
                tokens=sr.tokens,
                duration_ms=sr.duration_ms,
            )
            for sr in result.scenarios
        }
        return cls(
            suite_name=result.suite_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            pass_rate=result.pass_rate,
            weighted_score=result.weighted_score,
            total_tokens=result.total_tokens,
            scenarios=scenarios,
        )

    @classmethod
    def load(cls, path: str | Path) -> "EvalBaseline":
        """Load a baseline from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        scenarios = {
            name: ScenarioBaseline(**sc)
            for name, sc in data.get("scenarios", {}).items()
        }
        return cls(
            suite_name=data["suite_name"],
            timestamp=data["timestamp"],
            pass_rate=data["pass_rate"],
            weighted_score=data["weighted_score"],
            total_tokens=data["total_tokens"],
            scenarios=scenarios,
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Write the baseline to a JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "timestamp": self.timestamp,
            "pass_rate": self.pass_rate,
            "weighted_score": self.weighted_score,
            "total_tokens": self.total_tokens,
            "scenarios": {
                name: {
                    "name": sc.name,
                    "passed": sc.passed,
                    "score": sc.score,
                    "checks": sc.checks,
                    "tokens": sc.tokens,
                    "duration_ms": sc.duration_ms,
                }
                for name, sc in self.scenarios.items()
            },
        }

    # ── Diff ──────────────────────────────────────────────────────────────────

    def diff(self, newer: "EvalBaseline") -> "BaselineDiff":
        """Compute a regression diff between this baseline and *newer*.

        Parameters
        ----------
        newer : The newer EvalBaseline (e.g. from the current CI run).

        Returns
        -------
        BaselineDiff with regressions, improvements, and score deltas.
        """
        regressions: list[str] = []
        improvements: list[str] = []
        score_deltas: dict[str, float] = {}
        new_scenarios: list[str] = []
        removed_scenarios: list[str] = []

        all_names = set(self.scenarios) | set(newer.scenarios)

        for name in sorted(all_names):
            old_sc = self.scenarios.get(name)
            new_sc = newer.scenarios.get(name)

            if old_sc is None:
                new_scenarios.append(name)
                continue
            if new_sc is None:
                removed_scenarios.append(name)
                continue

            delta = new_sc.score - old_sc.score
            if abs(delta) > 0.001:
                score_deltas[name] = round(delta, 4)

            if old_sc.passed and not new_sc.passed:
                regressions.append(name)
            elif not old_sc.passed and new_sc.passed:
                improvements.append(name)

        return BaselineDiff(
            suite_name=self.suite_name,
            old_timestamp=self.timestamp,
            new_timestamp=newer.timestamp,
            pass_rate_delta=round(newer.pass_rate - self.pass_rate, 4),
            score_delta=round(newer.weighted_score - self.weighted_score, 4),
            regressions=regressions,
            improvements=improvements,
            score_deltas=score_deltas,
            new_scenarios=new_scenarios,
            removed_scenarios=removed_scenarios,
        )


@dataclass
class BaselineDiff:
    """Result of comparing two EvalBaselines."""

    suite_name: str
    old_timestamp: str
    new_timestamp: str
    pass_rate_delta: float       # positive = improvement
    score_delta: float
    regressions: list[str]       # PASS → FAIL
    improvements: list[str]      # FAIL → PASS
    score_deltas: dict[str, float]
    new_scenarios: list[str]
    removed_scenarios: list[str]

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    def report(self, verbose: bool = True) -> str:
        lines = [
            "",
            f"{'=' * 60}",
            f"  Eval Baseline Diff — {self.suite_name}",
            f"{'=' * 60}",
            f"  Baseline : {self.old_timestamp[:19]}",
            f"  Current  : {self.new_timestamp[:19]}",
            f"  Pass rate delta : {self.pass_rate_delta:+.1%}",
            f"  Score delta     : {self.score_delta:+.4f}",
            f"{'─' * 60}",
        ]

        if self.regressions:
            lines.append(f"  REGRESSIONS ({len(self.regressions)}) — PASS → FAIL:")
            for name in self.regressions:
                delta = self.score_deltas.get(name, 0.0)
                lines.append(f"    ✗  {name}  (Δscore={delta:+.3f})")
        else:
            lines.append("  No regressions.")

        if self.improvements:
            lines.append(f"  Improvements ({len(self.improvements)}) — FAIL → PASS:")
            for name in self.improvements:
                delta = self.score_deltas.get(name, 0.0)
                lines.append(f"    ✓  {name}  (Δscore={delta:+.3f})")

        if verbose and self.score_deltas:
            changed = [(n, d) for n, d in self.score_deltas.items()
                       if n not in self.regressions and n not in self.improvements]
            if changed:
                lines.append("  Score changes (non-pass/fail):")
                for name, delta in sorted(changed, key=lambda x: x[1]):
                    lines.append(f"    {'↑' if delta > 0 else '↓'}  {name}  ({delta:+.3f})")

        if self.new_scenarios:
            lines.append(f"  New scenarios : {', '.join(self.new_scenarios)}")
        if self.removed_scenarios:
            lines.append(f"  Removed       : {', '.join(self.removed_scenarios)}")

        verdict = "REGRESSION DETECTED" if self.has_regressions else "OK — no regressions"
        lines += [f"{'=' * 60}", f"  Verdict: {verdict}", f"{'=' * 60}", ""]
        return "\n".join(lines)
