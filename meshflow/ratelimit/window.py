"""Sprint 48 — Sliding-window rate limiter for per-agent and per-team limits.

Tracks two independent counters per key:
  - requests per window (call velocity)
  - tokens per window   (LLM throughput)

Uses a deque of (timestamp, tokens) tuples so old entries fall off
automatically when the window slides forward.  Thread-safe via a per-key lock.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ── Rate limit policy ─────────────────────────────────────────────────────────

@dataclass
class RateLimitPolicy:
    """Limits applied to a single key (agent name, team, or '*' wildcard).

    Parameters
    ----------
    max_requests:   Maximum requests allowed within *window_s* seconds.
                    0 means unlimited.
    max_tokens:     Maximum LLM tokens allowed within *window_s* seconds.
                    0 means unlimited.
    window_s:       Sliding window duration in seconds (default 60).
    warn_at:        Fraction of limit at which a warning is surfaced (0.80 = 80%).
    """

    max_requests: int = 0
    max_tokens: int = 0
    window_s: float = 60.0
    warn_at: float = 0.80

    def has_request_limit(self) -> bool:
        return self.max_requests > 0

    def has_token_limit(self) -> bool:
        return self.max_tokens > 0

    def is_unlimited(self) -> bool:
        return not self.has_request_limit() and not self.has_token_limit()


# ── Per-key sliding window ────────────────────────────────────────────────────

@dataclass
class _Window:
    policy: RateLimitPolicy
    lock: threading.Lock = field(default_factory=threading.Lock)
    # deque of (timestamp, tokens) — tokens=0 for request-only entries
    events: deque = field(default_factory=deque)

    def _evict(self, now: float) -> None:
        cutoff = now - self.policy.window_s
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def _count(self) -> tuple[int, int]:
        """Return (request_count, token_count) within the current window."""
        rc = len(self.events)
        tc = sum(e[1] for e in self.events)
        return rc, tc

    def record(self, tokens: int = 0, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.monotonic()
        with self.lock:
            self._evict(ts)
            self.events.append((ts, tokens))

    def check(self, tokens: int = 0, now: Optional[float] = None) -> "RateLimitResult":
        ts = now if now is not None else time.monotonic()
        with self.lock:
            self._evict(ts)
            rc, tc = self._count()
            p = self.policy

            # Request limit check
            if p.has_request_limit() and rc >= p.max_requests:
                return RateLimitResult(
                    allowed=False,
                    reason=f"request rate exceeded: {rc}/{p.max_requests} in {p.window_s}s",
                    requests_used=rc,
                    requests_limit=p.max_requests,
                    tokens_used=tc,
                    tokens_limit=p.max_tokens,
                    window_s=p.window_s,
                )

            # Token limit check
            if p.has_token_limit() and tc + tokens > p.max_tokens:
                return RateLimitResult(
                    allowed=False,
                    reason=f"token rate exceeded: {tc + tokens}/{p.max_tokens} in {p.window_s}s",
                    requests_used=rc,
                    requests_limit=p.max_requests,
                    tokens_used=tc,
                    tokens_limit=p.max_tokens,
                    window_s=p.window_s,
                )

            # Near-limit warnings
            near_limit = False
            if p.has_request_limit() and rc >= p.max_requests * p.warn_at:
                near_limit = True
            if p.has_token_limit() and (tc + tokens) >= p.max_tokens * p.warn_at:
                near_limit = True

            return RateLimitResult(
                allowed=True,
                requests_used=rc,
                requests_limit=p.max_requests,
                tokens_used=tc,
                tokens_limit=p.max_tokens,
                window_s=p.window_s,
                near_limit=near_limit,
            )


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class RateLimitResult:
    """Outcome of a rate limit check."""

    allowed: bool
    reason: str = ""
    requests_used: int = 0
    requests_limit: int = 0
    tokens_used: int = 0
    tokens_limit: int = 0
    window_s: float = 60.0
    near_limit: bool = False

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "requests_used": self.requests_used,
            "requests_limit": self.requests_limit,
            "tokens_used": self.tokens_used,
            "tokens_limit": self.tokens_limit,
            "window_s": self.window_s,
            "near_limit": self.near_limit,
        }


# ── Store ─────────────────────────────────────────────────────────────────────

class RateLimitStore:
    """In-memory registry of sliding-window policies keyed by agent/team name.

    Key resolution order:
      1. Exact match on ``key``
      2. Wildcard ``"*"``
      3. No policy → unlimited

    Usage::

        store = RateLimitStore()
        store.set_policy("billing-agent", RateLimitPolicy(max_requests=60, window_s=60))
        store.set_policy("*", RateLimitPolicy(max_requests=1000, window_s=60))

        result = store.check("billing-agent", tokens=512)
        if result.allowed:
            store.record("billing-agent", tokens=512)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._policies: dict[str, RateLimitPolicy] = {}
        self._windows: dict[str, _Window] = {}

    def set_policy(self, key: str, policy: RateLimitPolicy) -> None:
        with self._lock:
            self._policies[key] = policy
            # reset window when policy changes
            self._windows[key] = _Window(policy=policy)

    def remove_policy(self, key: str) -> bool:
        with self._lock:
            removed = key in self._policies
            self._policies.pop(key, None)
            self._windows.pop(key, None)
            return removed

    def _get_window(self, key: str) -> Optional[_Window]:
        # exact match first, then wildcard
        if key in self._windows:
            return self._windows[key]
        if "*" in self._windows:
            return self._windows["*"]
        return None

    def check(self, key: str, tokens: int = 0, now: Optional[float] = None) -> RateLimitResult:
        """Pre-flight check — does NOT record the call."""
        window = self._get_window(key)
        if window is None:
            return RateLimitResult(allowed=True, reason="no policy")
        return window.check(tokens=tokens, now=now)

    def record(self, key: str, tokens: int = 0, now: Optional[float] = None) -> None:
        """Record a completed call — call after check() returns allowed=True."""
        window = self._get_window(key)
        if window is not None:
            window.record(tokens=tokens, now=now)

    def check_and_record(self, key: str, tokens: int = 0, now: Optional[float] = None) -> RateLimitResult:
        """Atomic check + record (check passes iff recording is also safe)."""
        window = self._get_window(key)
        if window is None:
            return RateLimitResult(allowed=True, reason="no policy")
        # Inline the check logic to avoid re-acquiring window.lock (deadlock risk)
        ts = now if now is not None else time.monotonic()
        with window.lock:
            window._evict(ts)
            rc, tc = window._count()
            p = window.policy

            if p.has_request_limit() and rc >= p.max_requests:
                return RateLimitResult(
                    allowed=False,
                    reason=f"request rate exceeded: {rc}/{p.max_requests} in {p.window_s}s",
                    requests_used=rc, requests_limit=p.max_requests,
                    tokens_used=tc, tokens_limit=p.max_tokens, window_s=p.window_s,
                )
            if p.has_token_limit() and tc + tokens > p.max_tokens:
                return RateLimitResult(
                    allowed=False,
                    reason=f"token rate exceeded: {tc + tokens}/{p.max_tokens} in {p.window_s}s",
                    requests_used=rc, requests_limit=p.max_requests,
                    tokens_used=tc, tokens_limit=p.max_tokens, window_s=p.window_s,
                )

            near_limit = (
                (p.has_request_limit() and rc >= p.max_requests * p.warn_at)
                or (p.has_token_limit() and (tc + tokens) >= p.max_tokens * p.warn_at)
            )
            window.events.append((ts, tokens))
            return RateLimitResult(
                allowed=True,
                requests_used=rc + 1,
                requests_limit=p.max_requests,
                tokens_used=tc + tokens,
                tokens_limit=p.max_tokens,
                window_s=p.window_s,
                near_limit=near_limit,
            )

    def status(self, key: str, now: Optional[float] = None) -> Optional[dict]:
        window = self._get_window(key)
        if window is None:
            return None
        ts = now if now is not None else time.monotonic()
        with window.lock:
            window._evict(ts)
            rc, tc = window._count()
            p = window.policy
        return {
            "key": key,
            "requests_used": rc,
            "requests_limit": p.max_requests,
            "tokens_used": tc,
            "tokens_limit": p.max_tokens,
            "window_s": p.window_s,
        }

    def policies(self) -> dict[str, RateLimitPolicy]:
        with self._lock:
            return dict(self._policies)

    def reset(self, key: str) -> None:
        with self._lock:
            if key in self._windows:
                self._windows[key].events.clear()


# ── Global singleton ──────────────────────────────────────────────────────────

_global_store: Optional[RateLimitStore] = None
_gs_lock = threading.Lock()


def get_rate_limit_store() -> RateLimitStore:
    global _global_store
    with _gs_lock:
        if _global_store is None:
            _global_store = RateLimitStore()
        return _global_store


def reset_rate_limit_store() -> None:
    global _global_store
    with _gs_lock:
        _global_store = None
