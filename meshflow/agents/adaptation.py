"""Routing outcome tracking and self-improvement for AdaptiveModelTierRouter.

Three responsibilities:

1. **RouterOutcomeStore** — lightweight SQLite store (`:memory:` for tests)
   that persists :class:`RoutingOutcome` records.

2. **ThresholdOptimizer** — bucket analysis over recent outcomes; returns
   revised ``smart_threshold`` / ``large_threshold`` (composite 0–1) when
   there is sufficient evidence that the current boundaries are miscalibrated.

3. **RouterStats** / **TierStats** — aggregated view over the store for
   observability dashboards and :meth:`AdaptiveModelTierRouter.stats`.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Outcome record ────────────────────────────────────────────────────────────


@dataclass
class RoutingOutcome:
    """Single routing event with its observed quality signal.

    Attributes
    ----------
    outcome_id:       UUID string, generated automatically if empty.
    run_id:           Run or step ID that produced the outcome.
    task_hash:        sha256(task)[:12] — allows grouping similar tasks without
                      storing the full text.
    task_length:      Raw character count at route time.
    composite_score:  :class:`~meshflow.agents.scoring.TaskScore` composite at
                      route time (0–1).
    model:            Model identifier that was selected.
    tier:             Tier name (``"fast"``, ``"smart"``, ``"large"``, …).
    was_exploration:  True when the epsilon-greedy explorer chose this tier
                      rather than the greedy-optimal one.
    success:          False if the agent step raised an exception.
    quality_score:    CONFIDENCE:0.XX value from agent output, or ``None``.
    latency_ms:       Wall-clock duration of the step in milliseconds.
    actual_cost_usd:  Actual cost charged (from the ledger or step runtime).
    timestamp:        Unix timestamp at record time.
    """

    outcome_id: str
    run_id: str
    task_hash: str
    task_length: int
    composite_score: float
    model: str
    tier: str
    was_exploration: bool
    success: bool
    quality_score: float | None
    latency_ms: float
    actual_cost_usd: float
    timestamp: float = field(default_factory=time.time)

    # ── Derived ────────────────────────────────────────────────────────────

    @property
    def effective_success(self) -> bool:
        """Quality < 0.5 counts as failure even if no exception was raised."""
        if not self.success:
            return False
        if self.quality_score is not None and self.quality_score < 0.5:
            return False
        return True

    # ── Convenience constructors ──────────────────────────────────────────

    @classmethod
    def build(
        cls,
        run_id: str,
        task: str,
        composite_score: float,
        model: str,
        tier: str,
        *,
        was_exploration: bool = False,
        success: bool = True,
        quality_score: float | None = None,
        latency_ms: float = 0.0,
        actual_cost_usd: float = 0.0,
    ) -> "RoutingOutcome":
        return cls(
            outcome_id=str(uuid.uuid4()),
            run_id=run_id,
            task_hash=hashlib.sha256(task.encode()).hexdigest()[:12],
            task_length=len(task),
            composite_score=composite_score,
            model=model,
            tier=tier,
            was_exploration=was_exploration,
            success=success,
            quality_score=quality_score,
            latency_ms=latency_ms,
            actual_cost_usd=actual_cost_usd,
        )


# ── Store ─────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS routing_outcomes (
    outcome_id      TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    task_hash       TEXT NOT NULL,
    task_length     INTEGER NOT NULL,
    composite_score REAL NOT NULL,
    model           TEXT NOT NULL,
    tier            TEXT NOT NULL,
    was_exploration INTEGER NOT NULL DEFAULT 0,
    success         INTEGER NOT NULL DEFAULT 1,
    quality_score   REAL,
    latency_ms      REAL NOT NULL DEFAULT 0,
    actual_cost_usd REAL NOT NULL DEFAULT 0,
    timestamp       REAL NOT NULL
)
"""


class RouterOutcomeStore:
    """SQLite-backed store for :class:`RoutingOutcome` records.

    Pass ``path=":memory:"`` (or set ``MESHFLOW_MOCK=1``) for in-process
    testing without touching the filesystem.

    Parameters
    ----------
    path:   File path for SQLite database, or ``":memory:"`` for in-process.
    """

    def __init__(self, path: str | None = None) -> None:
        if path is None:
            path = ":memory:" if os.environ.get("MESHFLOW_MOCK") == "1" else "meshflow_routing.db"
        self._path = path
        self._mem_conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._mem_conn
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(_CREATE_TABLE)
        conn.commit()

    # ── Write ──────────────────────────────────────────────────────────────

    def record(self, outcome: RoutingOutcome) -> None:
        """Persist a routing outcome."""
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO routing_outcomes
               (outcome_id, run_id, task_hash, task_length, composite_score,
                model, tier, was_exploration, success, quality_score,
                latency_ms, actual_cost_usd, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                outcome.outcome_id, outcome.run_id, outcome.task_hash,
                outcome.task_length, outcome.composite_score,
                outcome.model, outcome.tier,
                int(outcome.was_exploration), int(outcome.success),
                outcome.quality_score,
                outcome.latency_ms, outcome.actual_cost_usd,
                outcome.timestamp,
            ),
        )
        conn.commit()

    # ── Read ───────────────────────────────────────────────────────────────

    def get_recent(self, n: int = 500) -> list[RoutingOutcome]:
        """Return the *n* most recent outcomes, newest first."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT outcome_id, run_id, task_hash, task_length, composite_score, "
            "model, tier, was_exploration, success, quality_score, "
            "latency_ms, actual_cost_usd, timestamp "
            "FROM routing_outcomes ORDER BY timestamp DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [_row_to_outcome(r) for r in rows]

    def get_tier_stats(self, tier: str) -> "TierStats":
        """Aggregate stats for a single tier."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT success, quality_score, latency_ms, actual_cost_usd "
            "FROM routing_outcomes WHERE tier=?",
            (tier,),
        ).fetchall()
        return _compute_tier_stats(tier, rows)

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM routing_outcomes").fetchone()[0]

    def count_explorations(self) -> int:
        conn = self._connect()
        return conn.execute(
            "SELECT COUNT(*) FROM routing_outcomes WHERE was_exploration=1"
        ).fetchone()[0]


def _row_to_outcome(row: tuple[Any, ...]) -> RoutingOutcome:
    return RoutingOutcome(
        outcome_id=row[0], run_id=row[1], task_hash=row[2],
        task_length=row[3], composite_score=row[4],
        model=row[5], tier=row[6],
        was_exploration=bool(row[7]), success=bool(row[8]),
        quality_score=row[9],
        latency_ms=row[10], actual_cost_usd=row[11],
        timestamp=row[12],
    )


# ── Stats ─────────────────────────────────────────────────────────────────────


@dataclass
class TierStats:
    """Aggregated quality/cost/latency metrics for one tier."""

    tier: str
    n: int
    success_rate: float       # fraction of effective_success == True
    avg_quality: float        # avg quality_score (only entries with score)
    avg_latency_ms: float
    avg_cost_usd: float


@dataclass
class RouterStats:
    """Full router health snapshot."""

    tiers: dict[str, TierStats]
    total_runs: int
    exploration_rate_actual: float    # fraction of runs that were exploratory
    last_adapted_at: float | None     # unix timestamp of last auto-adapt


def _compute_tier_stats(tier: str, rows: list[tuple[Any, ...]]) -> TierStats:
    if not rows:
        return TierStats(tier=tier, n=0, success_rate=0.0, avg_quality=0.0,
                         avg_latency_ms=0.0, avg_cost_usd=0.0)
    successes = 0
    quality_vals: list[float] = []
    latencies: list[float] = []
    costs: list[float] = []
    for success, quality, latency, cost in rows:
        eff = bool(success) and (quality is None or quality >= 0.5)
        if eff:
            successes += 1
        if quality is not None:
            quality_vals.append(quality)
        latencies.append(latency or 0.0)
        costs.append(cost or 0.0)
    n = len(rows)
    return TierStats(
        tier=tier,
        n=n,
        success_rate=successes / n,
        avg_quality=sum(quality_vals) / len(quality_vals) if quality_vals else 0.0,
        avg_latency_ms=sum(latencies) / n,
        avg_cost_usd=sum(costs) / n,
    )


# ── Threshold optimizer ───────────────────────────────────────────────────────


@dataclass
class ThresholdRecommendation:
    """Output of :class:`ThresholdOptimizer`.

    Attributes
    ----------
    smart_threshold:  Recommended composite score boundary (fast → smart).
    large_threshold:  Recommended composite score boundary (smart → large).
    confidence:       0–1 confidence based on sample count (< 0.3 → no-op).
    summary:          Human-readable explanation of the recommendation.
    runs_analyzed:    How many outcomes were included in the analysis.
    changed:          True when both thresholds differ from the inputs.
    """

    smart_threshold: float
    large_threshold: float
    confidence: float
    summary: str
    runs_analyzed: int
    changed: bool = False


class ThresholdOptimizer:
    """Analyse recent routing outcomes and recommend threshold adjustments.

    Algorithm
    ---------
    1. Load the last *max_outcomes* records from the store.
    2. Bin by ``composite_score`` into ``n_buckets`` equal-width buckets.
    3. For each bucket, compute per-tier ``effective_success`` rate.
    4. ``smart_threshold`` = highest composite where fast-tier
       ``success_rate < (1 - failure_threshold)``.
    5. ``large_threshold`` = highest composite where smart-tier
       ``success_rate < (1 - failure_threshold)``.
    6. Confidence = ``min(1.0, min_samples_in_any_used_bucket / min_samples_per_bucket)``.
    7. If confidence < 0.30, return current thresholds unchanged.

    Parameters
    ----------
    failure_threshold:       Max acceptable failure rate per tier (default 0.20).
    min_samples_per_bucket:  Minimum outcomes in a bucket before it counts.
    n_buckets:               Number of equal-width composite-score buckets.
    max_outcomes:            Maximum recent outcomes to analyse.
    """

    def __init__(
        self,
        failure_threshold: float = 0.20,
        min_samples_per_bucket: int = 10,
        n_buckets: int = 20,
        max_outcomes: int = 500,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.min_samples_per_bucket = min_samples_per_bucket
        self.n_buckets = n_buckets
        self.max_outcomes = max_outcomes

    def optimize(
        self,
        store: RouterOutcomeStore,
        current_smart: float,
        current_large: float,
    ) -> ThresholdRecommendation:
        outcomes = store.get_recent(self.max_outcomes)
        n = len(outcomes)

        if n < self.min_samples_per_bucket * 2:
            return ThresholdRecommendation(
                smart_threshold=current_smart,
                large_threshold=current_large,
                confidence=0.0,
                summary=f"Insufficient data ({n} outcomes, need {self.min_samples_per_bucket * 2}+). Thresholds unchanged.",
                runs_analyzed=n,
            )

        bucket_width = 1.0 / self.n_buckets
        # tier → bucket_idx → list of bool (effective_success)
        stats: dict[str, dict[int, list[bool]]] = {}
        for o in outcomes:
            bucket_idx = min(int(o.composite_score / bucket_width), self.n_buckets - 1)
            tier_buckets = stats.setdefault(o.tier, {})
            tier_buckets.setdefault(bucket_idx, []).append(o.effective_success)

        success_threshold = 1.0 - self.failure_threshold  # e.g. 0.80

        new_smart = self._find_boundary("fast", stats, success_threshold, current_smart, bucket_width)
        new_large = self._find_boundary("smart", stats, success_threshold, current_large, bucket_width)

        # Ensure ordering
        new_smart = min(new_smart, new_large - bucket_width)
        new_smart = max(0.05, new_smart)
        new_large = max(new_smart + bucket_width, new_large)
        new_large = min(0.95, new_large)

        # Confidence: how many buckets had sufficient samples?
        all_bucket_sizes = [
            len(v) for td in stats.values() for v in td.values()
        ]
        confidence = min(1.0, min(all_bucket_sizes) / self.min_samples_per_bucket) if all_bucket_sizes else 0.0

        if confidence < 0.30:
            return ThresholdRecommendation(
                smart_threshold=current_smart,
                large_threshold=current_large,
                confidence=confidence,
                summary=f"Low confidence ({confidence:.2f}). Thresholds unchanged.",
                runs_analyzed=n,
            )

        changed = abs(new_smart - current_smart) > 0.01 or abs(new_large - current_large) > 0.01
        summary_parts = []
        if abs(new_smart - current_smart) > 0.01:
            direction = "↑" if new_smart > current_smart else "↓"
            summary_parts.append(f"smart_threshold {current_smart:.2f}→{new_smart:.2f} {direction}")
        if abs(new_large - current_large) > 0.01:
            direction = "↑" if new_large > current_large else "↓"
            summary_parts.append(f"large_threshold {current_large:.2f}→{new_large:.2f} {direction}")
        summary = (
            f"Analyzed {n} outcomes (confidence={confidence:.2f}). "
            + ("; ".join(summary_parts) if summary_parts else "Thresholds stable.")
        )

        return ThresholdRecommendation(
            smart_threshold=round(new_smart, 3),
            large_threshold=round(new_large, 3),
            confidence=round(confidence, 3),
            summary=summary,
            runs_analyzed=n,
            changed=changed,
        )

    def _find_boundary(
        self,
        tier: str,
        stats: dict[str, dict[int, list[bool]]],
        success_threshold: float,
        current: float,
        bucket_width: float,
    ) -> float:
        """Find highest composite bucket where *tier* success rate < success_threshold."""
        tier_data = stats.get(tier, {})
        boundary = current
        for bucket_idx in sorted(tier_data.keys()):
            successes = tier_data[bucket_idx]
            if len(successes) < self.min_samples_per_bucket:
                continue
            rate = sum(successes) / len(successes)
            if rate < success_threshold:
                # This bucket has too many failures — boundary should be at least here
                boundary = max(boundary, (bucket_idx + 1) * bucket_width)
        return boundary


# ── CSV export ────────────────────────────────────────────────────────────────

    # (method added to RouterOutcomeStore below via monkey-patching is cleaner
    #  as a free function to avoid class re-declaration)

def export_outcomes_csv(store: "RouterOutcomeStore", path: str) -> int:
    """Export all routing outcomes from *store* to a CSV file.

    Returns the number of rows written.  The file can be opened in any
    spreadsheet tool or loaded into pandas for deeper analysis::

        import pandas as pd
        df = pd.read_csv("routing_outcomes.csv")
        df.groupby("tier")["quality_score"].mean()
    """
    import csv as _csv
    outcomes = store.get_recent(100_000)
    if not outcomes:
        return 0
    fields = [
        "outcome_id", "run_id", "task_hash", "task_length", "composite_score",
        "model", "tier", "was_exploration", "success", "quality_score",
        "latency_ms", "actual_cost_usd", "timestamp",
    ]
    with open(path, "w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for o in outcomes:
            writer.writerow({
                "outcome_id": o.outcome_id,
                "run_id": o.run_id,
                "task_hash": o.task_hash,
                "task_length": o.task_length,
                "composite_score": o.composite_score,
                "model": o.model,
                "tier": o.tier,
                "was_exploration": int(o.was_exploration),
                "success": int(o.success),
                "quality_score": o.quality_score if o.quality_score is not None else "",
                "latency_ms": o.latency_ms,
                "actual_cost_usd": o.actual_cost_usd,
                "timestamp": o.timestamp,
            })
    return len(outcomes)


# Attach as a method on RouterOutcomeStore for ergonomic access
RouterOutcomeStore.export_csv = lambda self, path: export_outcomes_csv(self, path)  # type: ignore[attr-defined]
