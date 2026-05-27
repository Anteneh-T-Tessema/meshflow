"""Sprint 50 — Circuit Breaker pattern for resilient agent calls.

The circuit breaker has three states:

  CLOSED    — normal operation; failures are counted in a sliding window.
  OPEN      — circuit has tripped; calls are rejected immediately without
               reaching the downstream service.
  HALF_OPEN — recovery probe; a limited number of trial calls are allowed.
               Success closes the circuit; failure re-opens it.

Usage
-----
    from meshflow.resilience import CircuitBreaker, CircuitBreakerConfig

    breaker = CircuitBreaker("openai", CircuitBreakerConfig(failure_threshold=5))

    try:
        result = breaker.call(my_llm_call, prompt)
    except CircuitBreakerOpenError:
        result = fallback_response()
    except Exception as exc:
        # downstream failure — breaker recorded it automatically
        raise
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Type


# ── State ─────────────────────────────────────────────────────────────────────

class CircuitBreakerState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class CircuitBreakerConfig:
    """Configuration for a single circuit breaker.

    Parameters
    ----------
    failure_threshold:
        Number of failures within *window_s* that trips the circuit (CLOSED →
        OPEN).  Default 5.
    recovery_timeout:
        Seconds the circuit stays OPEN before transitioning to HALF_OPEN to
        probe recovery.  Default 60.
    half_open_max_calls:
        Maximum concurrent trial calls allowed while in HALF_OPEN.  Default 1.
    success_threshold:
        Consecutive successes in HALF_OPEN required to close the circuit.
        Default 1.
    window_s:
        Rolling window duration in seconds used to count failures.  Default 60.
    exclude_exceptions:
        Tuple of exception types that do *not* count as failures (e.g.
        ``ValueError`` for bad user input that isn't the service's fault).
    """

    failure_threshold:    int             = 5
    recovery_timeout:     float           = 60.0
    half_open_max_calls:  int             = 1
    success_threshold:    int             = 1
    window_s:             float           = 60.0
    exclude_exceptions:   tuple[Type[BaseException], ...] = field(default_factory=tuple)


# ── Errors ────────────────────────────────────────────────────────────────────

class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted against an OPEN circuit breaker."""

    def __init__(self, name: str, retry_after: float) -> None:
        self.name = name
        self.retry_after = retry_after   # seconds until HALF_OPEN probe
        super().__init__(
            f"Circuit '{name}' is OPEN — retry after {retry_after:.1f}s"
        )


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class CircuitBreakerStats:
    """Read-only snapshot of circuit breaker counters."""

    name:             str
    state:            CircuitBreakerState
    failure_count:    int     # failures in current window
    success_count:    int     # consecutive successes since last failure / HALF_OPEN entry
    last_failure_at:  float | None
    last_success_at:  float | None
    opened_at:        float | None
    total_calls:      int
    total_failures:   int
    total_successes:  int
    total_rejected:   int     # calls rejected while OPEN


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """Thread-safe circuit breaker.

    Parameters
    ----------
    name:   Logical name (used in ``CircuitBreakerOpenError`` and registry).
    config: ``CircuitBreakerConfig`` instance; defaults are used if omitted.
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._lock = threading.Lock()
        self._state = CircuitBreakerState.CLOSED

        # Sliding window: deque of failure timestamps
        self._failure_window: deque[float] = deque()

        # Counters
        self._consecutive_successes = 0
        self._half_open_in_flight   = 0
        self._last_failure_at:  float | None = None
        self._last_success_at:  float | None = None
        self._opened_at:        float | None = None
        self._total_calls     = 0
        self._total_failures  = 0
        self._total_successes = 0
        self._total_rejected  = 0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            return self._evaluate_state()

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute *fn* within the circuit breaker.

        Raises
        ------
        CircuitBreakerOpenError
            When the circuit is OPEN and the call is rejected.
        Any exception raised by *fn*
            Recorded as a failure (unless listed in ``exclude_exceptions``).
        """
        with self._lock:
            state = self._evaluate_state()
            self._total_calls += 1

            if state == CircuitBreakerState.OPEN:
                self._total_rejected += 1
                retry_after = self._retry_after()
                raise CircuitBreakerOpenError(self.name, retry_after)

            if state == CircuitBreakerState.HALF_OPEN:
                if self._half_open_in_flight >= self.config.half_open_max_calls:
                    self._total_rejected += 1
                    raise CircuitBreakerOpenError(self.name, 0.0)
                self._half_open_in_flight += 1

        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            if not isinstance(exc, tuple(self.config.exclude_exceptions) or ()):  # type: ignore[arg-type]
                self._on_failure()
            else:
                self._on_success()
            raise
        else:
            self._on_success()
            return result
        finally:
            with self._lock:
                if self._state == CircuitBreakerState.HALF_OPEN:
                    self._half_open_in_flight = max(0, self._half_open_in_flight - 1)

    def record_success(self) -> None:
        """Manually record a success (for use outside of ``call()``)."""
        self._on_success()

    def record_failure(self, exc: BaseException | None = None) -> None:
        """Manually record a failure (for use outside of ``call()``)."""
        if exc is not None and self.config.exclude_exceptions:
            if isinstance(exc, self.config.exclude_exceptions):
                return
        self._on_failure()

    def reset(self) -> None:
        """Force the circuit to CLOSED and clear all counters."""
        with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._failure_window.clear()
            self._consecutive_successes = 0
            self._half_open_in_flight   = 0
            self._last_failure_at  = None
            self._opened_at        = None

    def trip(self) -> None:
        """Force the circuit to OPEN immediately."""
        with self._lock:
            self._state     = CircuitBreakerState.OPEN
            self._opened_at = time.monotonic()

    @property
    def stats(self) -> CircuitBreakerStats:
        with self._lock:
            state = self._evaluate_state()
            self._evict_window()
            return CircuitBreakerStats(
                name=self.name,
                state=state,
                failure_count=len(self._failure_window),
                success_count=self._consecutive_successes,
                last_failure_at=self._last_failure_at,
                last_success_at=self._last_success_at,
                opened_at=self._opened_at,
                total_calls=self._total_calls,
                total_failures=self._total_failures,
                total_successes=self._total_successes,
                total_rejected=self._total_rejected,
            )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _evaluate_state(self) -> CircuitBreakerState:
        """Evaluate the current state, applying time-based transitions."""
        if self._state == CircuitBreakerState.OPEN:
            if time.monotonic() - (self._opened_at or 0) >= self.config.recovery_timeout:
                self._state = CircuitBreakerState.HALF_OPEN
                self._consecutive_successes = 0
                self._half_open_in_flight   = 0
        return self._state

    def _evict_window(self) -> None:
        now = time.monotonic()
        cutoff = now - self.config.window_s
        while self._failure_window and self._failure_window[0] < cutoff:
            self._failure_window.popleft()

    def _on_success(self) -> None:
        with self._lock:
            self._last_success_at = time.monotonic()
            self._total_successes += 1
            self._consecutive_successes += 1

            if self._state == CircuitBreakerState.HALF_OPEN:
                if self._consecutive_successes >= self.config.success_threshold:
                    self._state = CircuitBreakerState.CLOSED
                    self._failure_window.clear()
                    self._opened_at = None
            elif self._state == CircuitBreakerState.CLOSED:
                # Reset consecutive failure streak on success
                pass

    def _on_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._last_failure_at = now
            self._total_failures += 1
            self._consecutive_successes = 0

            if self._state == CircuitBreakerState.HALF_OPEN:
                # Probe failed — re-open
                self._state     = CircuitBreakerState.OPEN
                self._opened_at = now
                self._failure_window.clear()
                return

            # CLOSED: add to sliding window
            self._evict_window()
            self._failure_window.append(now)

            if len(self._failure_window) >= self.config.failure_threshold:
                self._state     = CircuitBreakerState.OPEN
                self._opened_at = now

    def _retry_after(self) -> float:
        if self._opened_at is None:
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        return max(0.0, self.config.recovery_timeout - elapsed)
