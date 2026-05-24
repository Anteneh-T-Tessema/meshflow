"""SLA monitoring — per-node latency percentiles and API-level rate limiting.

NodeLatencyTracker accumulates wall-clock durations per node_id and computes
p50 / p95 / p99 percentiles on demand using a simple sorted-list reservoir.
RateLimiter implements a per-key token-bucket algorithm.
"""
from __future__ import annotations

import bisect
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any


# ── Latency tracker ───────────────────────────────────────────────────────────

_MAX_SAMPLES = int(os.environ.get("MESHFLOW_SLA_MAX_SAMPLES", "10000"))


@dataclass
class NodeSLASummary:
    node_id: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "count": self.count,
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "min_ms": round(self.min_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "mean_ms": round(self.mean_ms, 2),
        }


class NodeLatencyTracker:
    """Thread-safe per-node latency recorder."""

    def __init__(self, max_samples: int = _MAX_SAMPLES) -> None:
        self._max = max_samples
        self._lock = threading.Lock()
        self._samples: dict[str, list[float]] = {}  # node_id → sorted list of ms

    def record(self, node_id: str, duration_ms: float) -> None:
        with self._lock:
            bucket = self._samples.setdefault(node_id, [])
            bisect.insort(bucket, duration_ms)
            if len(bucket) > self._max:
                bucket.pop(0)  # evict oldest (smallest) when over capacity

    def summary(self, node_id: str) -> NodeSLASummary | None:
        with self._lock:
            samples = list(self._samples.get(node_id, []))
        if not samples:
            return None
        return _percentile_summary(node_id, samples)

    def report(self) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = {k: list(v) for k, v in self._samples.items()}
        return [_percentile_summary(nid, s).to_dict() for nid, s in sorted(snapshot.items()) if s]

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()

    def node_ids(self) -> list[str]:
        with self._lock:
            return list(self._samples.keys())


def _percentile_summary(node_id: str, samples: list[float]) -> NodeSLASummary:
    n = len(samples)

    def _p(pct: float) -> float:
        idx = max(0, int(pct / 100 * n) - 1)
        return samples[min(idx, n - 1)]

    return NodeSLASummary(
        node_id=node_id,
        count=n,
        p50_ms=_p(50),
        p95_ms=_p(95),
        p99_ms=_p(99),
        min_ms=samples[0],
        max_ms=samples[-1],
        mean_ms=sum(samples) / n,
    )


# Global singleton — imported by StepRuntime and the server
_global_sla_tracker: NodeLatencyTracker | None = None
_tracker_lock = threading.Lock()


def get_sla_tracker() -> NodeLatencyTracker:
    global _global_sla_tracker
    with _tracker_lock:
        if _global_sla_tracker is None:
            _global_sla_tracker = NodeLatencyTracker()
    return _global_sla_tracker


# ── Token-bucket rate limiter ─────────────────────────────────────────────────

@dataclass
class _Bucket:
    capacity: float
    rate: float           # tokens per second
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Per-tenant token-bucket rate limiter.

    Each tenant gets ``capacity`` tokens; tokens refill at ``rate`` per second.
    ``allow(tenant_id)`` returns True if the request should proceed (consumes 1 token).

    Per-tenant overrides are read from environment variables at first access:
      MESHFLOW_RATE_LIMIT_TENANT_<TENANT_ID>_RPS=120
      MESHFLOW_RATE_LIMIT_TENANT_<TENANT_ID>_BURST=120
    Tenant IDs are upper-cased and non-alphanumeric characters replaced with ``_``.
    """

    def __init__(self, rate: float = 60.0, capacity: float = 60.0) -> None:
        self._default_rate = rate
        self._default_capacity = capacity
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def _tenant_limits(self, tenant_id: str) -> tuple[float, float]:
        """Return (rate, capacity) for a tenant from env overrides, or defaults."""
        if not tenant_id or tenant_id in ("", "anonymous", "global"):
            return self._default_rate, self._default_capacity
        # e.g. MESHFLOW_RATE_LIMIT_TENANT_ACME_CORP_RPS
        env_key = "MESHFLOW_RATE_LIMIT_TENANT_" + "".join(
            c if c.isalnum() else "_" for c in tenant_id.upper()
        )
        rate = float(os.environ.get(f"{env_key}_RPS", str(self._default_rate)))
        burst = float(os.environ.get(f"{env_key}_BURST", str(self._default_capacity)))
        return rate, burst

    def _get_bucket(self, tenant_id: str) -> _Bucket:
        if tenant_id not in self._buckets:
            rate, capacity = self._tenant_limits(tenant_id)
            self._buckets[tenant_id] = _Bucket(
                capacity=capacity,
                rate=rate,
                tokens=capacity,
            )
        return self._buckets[tenant_id]

    def _refill(self, bucket: _Bucket) -> None:
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.rate)
        bucket.last_refill = now

    def allow(self, tenant_id: str = "anonymous") -> bool:
        with self._lock:
            bucket = self._get_bucket(tenant_id)
            self._refill(bucket)
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False

    def status(self, tenant_id: str) -> dict[str, Any]:
        with self._lock:
            bucket = self._get_bucket(tenant_id)
            self._refill(bucket)
            return {
                "tenant_id": tenant_id,
                "tokens_remaining": round(bucket.tokens, 2),
                "capacity": bucket.capacity,
                "rate_per_s": bucket.rate,
            }

    def set_limits(self, tenant_id: str, rate: float, capacity: float) -> None:
        with self._lock:
            self._buckets[tenant_id] = _Bucket(
                capacity=capacity, rate=rate, tokens=capacity
            )

    def stats(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.status(t) for t in self._buckets]


# Global singleton
_global_rate_limiter: RateLimiter | None = None
_rl_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    global _global_rate_limiter
    with _rl_lock:
        if _global_rate_limiter is None:
            rate = float(os.environ.get("MESHFLOW_RATE_LIMIT_RPS", "60"))
            capacity = float(os.environ.get("MESHFLOW_RATE_LIMIT_BURST", "60"))
            _global_rate_limiter = RateLimiter(rate=rate, capacity=capacity)
    return _global_rate_limiter
