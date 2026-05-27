"""Named registry of CircuitBreaker instances — process-wide singleton.

Usage
-----
    from meshflow.resilience import get_circuit_registry

    registry = get_circuit_registry()
    breaker = registry.get_or_create("openai", config=CircuitBreakerConfig(failure_threshold=3))
    result  = breaker.call(my_fn, arg)
"""

from __future__ import annotations

import threading
from typing import Optional

from .breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerStats


class CircuitBreakerRegistry:
    """Thread-safe registry of named ``CircuitBreaker`` instances."""

    def __init__(self) -> None:
        self._lock     = threading.Lock()
        self._breakers: dict[str, CircuitBreaker] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
        *,
        overwrite: bool = False,
    ) -> CircuitBreaker:
        """Create and register a named breaker.

        Parameters
        ----------
        overwrite:
            When ``True``, replace an existing breaker with the same name.
            When ``False`` (default), return the existing breaker unchanged.
        """
        with self._lock:
            if name in self._breakers and not overwrite:
                return self._breakers[name]
            breaker = CircuitBreaker(name, config)
            self._breakers[name] = breaker
            return breaker

    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Return an existing breaker or ``None``."""
        with self._lock:
            return self._breakers.get(name)

    def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreaker:
        """Return the named breaker, creating it with *config* if absent."""
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]

    def deregister(self, name: str) -> bool:
        """Remove a breaker from the registry. Returns True if it existed."""
        with self._lock:
            return self._breakers.pop(name, None) is not None

    # ── Bulk operations ───────────────────────────────────────────────────────

    def all_stats(self) -> list[CircuitBreakerStats]:
        """Return a stats snapshot for every registered breaker."""
        with self._lock:
            breakers = list(self._breakers.values())
        return [b.stats for b in breakers]

    def reset_all(self) -> int:
        """Force every registered breaker to CLOSED. Returns count reset."""
        with self._lock:
            breakers = list(self._breakers.values())
        for b in breakers:
            b.reset()
        return len(breakers)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._breakers.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._breakers)

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._breakers


# ── Global singleton ──────────────────────────────────────────────────────────

_registry_lock = threading.Lock()
_global_registry: Optional[CircuitBreakerRegistry] = None


def get_circuit_registry() -> CircuitBreakerRegistry:
    """Return the process-wide ``CircuitBreakerRegistry`` (created on first call)."""
    global _global_registry
    with _registry_lock:
        if _global_registry is None:
            _global_registry = CircuitBreakerRegistry()
        return _global_registry


def reset_circuit_registry() -> None:
    """Replace the global registry with a fresh empty one (test helper)."""
    global _global_registry
    with _registry_lock:
        _global_registry = CircuitBreakerRegistry()
