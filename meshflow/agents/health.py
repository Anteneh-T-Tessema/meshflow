"""Model health tracking and auto-fallback for ProviderRouter.

ModelHealthTracker records per-model outcomes (success, failure, latency) and
exposes a health score (0.0–1.0) based on a rolling window.  ProviderRouter
can use this to avoid routing to degraded models.

Usage::

    from meshflow.agents.health import ModelHealthTracker, get_health_tracker

    tracker = get_health_tracker()
    tracker.record_success("claude-sonnet-4-6", latency_ms=320.0)
    tracker.record_failure("claude-opus-4-7", error="timeout")

    score = tracker.health_score("claude-sonnet-4-6")  # 1.0 if all recent OK
    best  = tracker.best_model(["claude-opus-4-7", "claude-sonnet-4-6"])
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


_WINDOW_SIZE = int(__import__("os").environ.get("MESHFLOW_HEALTH_WINDOW", "50"))
_DEGRADED_THRESHOLD = float(
    __import__("os").environ.get("MESHFLOW_HEALTH_DEGRADED_THRESHOLD", "0.7")
)


@dataclass
class _Outcome:
    ts: float
    ok: bool
    latency_ms: float
    error: str = ""


@dataclass
class ModelHealthSummary:
    model: str
    health_score: float      # 0.0 (all failures) – 1.0 (all successes)
    success_count: int
    failure_count: int
    p50_latency_ms: float
    p95_latency_ms: float
    is_degraded: bool        # health_score < threshold
    last_error: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "health_score": round(self.health_score, 3),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "p50_latency_ms": round(self.p50_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "is_degraded": self.is_degraded,
            "last_error": self.last_error,
        }


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, int(pct / 100 * len(sorted_vals)) - 1)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


class ModelHealthTracker:
    """Thread-safe rolling-window health tracker for LLM model endpoints.

    Each model gets a fixed-size deque (default 50 outcomes).  Health score
    is the fraction of successful calls in the window.
    """

    def __init__(
        self,
        window_size: int = _WINDOW_SIZE,
        degraded_threshold: float = _DEGRADED_THRESHOLD,
    ) -> None:
        self._window = window_size
        self._threshold = degraded_threshold
        self._lock = threading.Lock()
        self._outcomes: dict[str, deque[_Outcome]] = {}
        self._last_error: dict[str, str] = {}

    def _get_window(self, model: str) -> deque[_Outcome]:
        if model not in self._outcomes:
            self._outcomes[model] = deque(maxlen=self._window)
        return self._outcomes[model]

    def record_success(self, model: str, latency_ms: float = 0.0) -> None:
        with self._lock:
            self._get_window(model).append(
                _Outcome(ts=time.monotonic(), ok=True, latency_ms=latency_ms)
            )

    def record_failure(self, model: str, error: str = "", latency_ms: float = 0.0) -> None:
        with self._lock:
            self._get_window(model).append(
                _Outcome(ts=time.monotonic(), ok=False, latency_ms=latency_ms, error=error)
            )
            if error:
                self._last_error[model] = error

    def health_score(self, model: str) -> float:
        """Return success fraction for ``model`` in its rolling window.
        Returns 1.0 for unseen models (optimistic default).
        """
        with self._lock:
            window = self._outcomes.get(model)
            if not window:
                return 1.0
            return sum(1 for o in window if o.ok) / len(window)

    def is_degraded(self, model: str) -> bool:
        return self.health_score(model) < self._threshold

    def summary(self, model: str) -> ModelHealthSummary:
        with self._lock:
            window = list(self._outcomes.get(model, []))
        successes = sum(1 for o in window if o.ok)
        failures = len(window) - successes
        latencies = sorted(o.latency_ms for o in window)
        score = successes / len(window) if window else 1.0
        return ModelHealthSummary(
            model=model,
            health_score=score,
            success_count=successes,
            failure_count=failures,
            p50_latency_ms=_percentile(latencies, 50),
            p95_latency_ms=_percentile(latencies, 95),
            is_degraded=score < self._threshold,
            last_error=self._last_error.get(model, ""),
        )

    def all_summaries(self) -> list[ModelHealthSummary]:
        with self._lock:
            models = list(self._outcomes.keys())
        return [self.summary(m) for m in sorted(models)]

    def best_model(self, candidates: list[str]) -> str:
        """Return the healthiest model from ``candidates`` (highest score).
        Falls back to first candidate if all are equally unknown.
        """
        if not candidates:
            raise ValueError("candidates must be non-empty")
        return max(candidates, key=self.health_score)

    def reset(self, model: str | None = None) -> None:
        with self._lock:
            if model:
                self._outcomes.pop(model, None)
                self._last_error.pop(model, None)
            else:
                self._outcomes.clear()
                self._last_error.clear()


# ── Global singleton ──────────────────────────────────────────────────────────

_global_tracker: ModelHealthTracker | None = None
_tracker_lock = threading.Lock()


def get_health_tracker() -> ModelHealthTracker:
    global _global_tracker
    with _tracker_lock:
        if _global_tracker is None:
            _global_tracker = ModelHealthTracker()
    return _global_tracker


def reset_health_tracker() -> None:
    global _global_tracker
    with _tracker_lock:
        _global_tracker = None
