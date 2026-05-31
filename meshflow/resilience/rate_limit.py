"""Rate limit handler — exponential backoff on HTTP 429 responses.

Wraps any async callable (an LLM provider call) with 429-aware retry logic.
Separate from the generic CircuitBreaker — 429 is a transient "slow down"
signal, not a failure: the call should be retried after an appropriate wait.

Usage (wrapping a provider call)::

    from meshflow.resilience.rate_limit import with_rate_limit_retry, RateLimitPolicy

    policy = RateLimitPolicy(
        max_retries=5,
        base_delay_s=1.0,
        max_delay_s=60.0,
        jitter=True,
    )

    result = await with_rate_limit_retry(
        fn=provider.complete,
        policy=policy,
        model="claude-sonnet-4-6",
        messages=[...],
        system="...",
        max_tokens=1024,
    )

Global default policy::

    from meshflow.resilience.rate_limit import get_default_policy, set_default_policy
    set_default_policy(RateLimitPolicy(max_retries=6, base_delay_s=2.0))
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


# ── Policy ────────────────────────────────────────────────────────────────────

@dataclass
class RateLimitPolicy:
    """Configuration for 429-aware exponential backoff.

    Parameters
    ----------
    max_retries:   Maximum number of retry attempts (0 = fail immediately on 429).
    base_delay_s:  Starting wait time in seconds.
    max_delay_s:   Cap on wait time (avoids unbounded waits).
    multiplier:    Exponential growth factor (default 2.0 → doubles each attempt).
    jitter:        When True, add ±25% random jitter to avoid thundering herds.
    """

    max_retries: int = 5
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    multiplier: float = 2.0
    jitter: bool = True

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the wait time for the Nth retry (0-indexed)."""
        raw = self.base_delay_s * (self.multiplier ** attempt)
        capped = min(raw, self.max_delay_s)
        if self.jitter:
            capped *= (0.75 + random.random() * 0.5)  # ±25 %
        return round(capped, 3)


# ── Rate limit error detection ────────────────────────────────────────────────

def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True if *exc* represents an HTTP 429 / rate-limit condition."""
    cls_name = type(exc).__name__.lower()
    msg = str(exc).lower()

    # Anthropic SDK
    if "ratelimit" in cls_name or "rate_limit" in cls_name:
        return True
    # OpenAI SDK
    if "rateerror" in cls_name:
        return True
    # HTTP status check in message
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return True
    # httpx / requests status code
    status = getattr(exc, "status_code", None) or getattr(exc, "response", {})
    if hasattr(status, "status_code"):
        status = status.status_code
    if status == 429:
        return True
    return False


# ── Core retry wrapper ────────────────────────────────────────────────────────

async def with_rate_limit_retry(
    fn: Callable,
    *args: Any,
    policy: RateLimitPolicy | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``await fn(*args, **kwargs)`` with 429-aware exponential backoff.

    Parameters
    ----------
    fn:      Any async callable (e.g. ``provider.complete``).
    policy:  :class:`RateLimitPolicy`. Defaults to :func:`get_default_policy`.
    *args, **kwargs: Forwarded to *fn*.

    Raises
    ------
    The original exception after *max_retries* attempts are exhausted, or
    immediately if the error is NOT a rate-limit error.
    """
    p = policy or get_default_policy()
    last_exc: BaseException | None = None

    for attempt in range(p.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:
            if not _is_rate_limit_error(exc):
                raise  # non-429 errors are not retried
            last_exc = exc
            if attempt >= p.max_retries:
                break
            delay = p.delay_for_attempt(attempt)
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc


# ── Rate limit statistics ─────────────────────────────────────────────────────

@dataclass
class _RateLimitStats:
    total_429s: int = 0
    retries_succeeded: int = 0
    retries_exhausted: int = 0
    last_429_at: float = 0.0
    total_wait_s: float = 0.0


class RateLimitTracker:
    """Thread-safe tracker of 429 events for observability."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._per_model: dict[str, _RateLimitStats] = {}

    def record_429(self, model: str, wait_s: float) -> None:
        with self._lock:
            s = self._per_model.setdefault(model, _RateLimitStats())
            s.total_429s += 1
            s.total_wait_s += wait_s
            s.last_429_at = time.time()

    def record_success_after_retry(self, model: str) -> None:
        with self._lock:
            s = self._per_model.setdefault(model, _RateLimitStats())
            s.retries_succeeded += 1

    def record_exhausted(self, model: str) -> None:
        with self._lock:
            s = self._per_model.setdefault(model, _RateLimitStats())
            s.retries_exhausted += 1

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                model: {
                    "total_429s":         s.total_429s,
                    "retries_succeeded":  s.retries_succeeded,
                    "retries_exhausted":  s.retries_exhausted,
                    "total_wait_s":       round(s.total_wait_s, 2),
                    "last_429_at":        s.last_429_at,
                }
                for model, s in self._per_model.items()
            }


# ── Global singletons ──────────────────────────────────────────────────────────

_default_policy: RateLimitPolicy = RateLimitPolicy()
_default_policy_lock = threading.Lock()
_global_tracker: RateLimitTracker = RateLimitTracker()


def get_default_policy() -> RateLimitPolicy:
    with _default_policy_lock:
        return _default_policy


def set_default_policy(policy: RateLimitPolicy) -> None:
    global _default_policy
    with _default_policy_lock:
        _default_policy = policy


def get_rate_limit_tracker() -> RateLimitTracker:
    return _global_tracker


__all__ = [
    "RateLimitPolicy",
    "RateLimitTracker",
    "with_rate_limit_retry",
    "get_default_policy",
    "set_default_policy",
    "get_rate_limit_tracker",
    "_is_rate_limit_error",
]
