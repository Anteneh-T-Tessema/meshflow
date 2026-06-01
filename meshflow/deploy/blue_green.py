"""Blue/green agent deployments — zero-downtime agent version promotion.

Implements the deployment pattern from the ZT guide (Part III — Recovery,
Enterprise tier): "Automated rollback with health checks."

Architecture
------------
- Each running agent version is a named **slot**: ``blue`` or ``green``
- The **active** slot serves all traffic; the **standby** slot runs the new version
- A ``BlueGreenRouter`` shifts traffic gradually (0% → 10% → 50% → 100%) with
  health checks at each step
- Automatic rollback if health checks fail during promotion

Usage::

    from meshflow.deploy.blue_green import BlueGreenRouter, AgentDeployment

    router = BlueGreenRouter()

    # Register current version (blue)
    router.register("blue", AgentDeployment(
        name="analyst-v1",
        config_path="agents/analyst.yaml",
        version="1.0.0",
    ))

    # Deploy new version (green)
    router.register("green", AgentDeployment(
        name="analyst-v2",
        config_path="agents/analyst-v2.yaml",
        version="2.0.0",
    ))

    # Promote green → 100% (with automatic health checks)
    result = await router.promote("green", steps=[0.1, 0.5, 1.0])
    print(result.success, result.active_slot)

CLI::

    meshflow deploy --config analyst.yaml --strategy blue-green --version 2.0.0
    meshflow deploy status
    meshflow deploy rollback
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentDeployment:
    """A versioned agent deployment descriptor."""

    name: str
    version: str = "1.0.0"
    config_path: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    healthy: bool = True
    deployed_at: float = field(default_factory=time.time)
    health_checks_passed: int = 0
    health_checks_failed: int = 0
    request_count: int = 0
    error_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def error_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.error_count / self.request_count

    @property
    def config_hash(self) -> str:
        raw = json.dumps(self.config, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":                  self.name,
            "version":               self.version,
            "config_path":           self.config_path,
            "config_hash":           self.config_hash,
            "healthy":               self.healthy,
            "deployed_at":           self.deployed_at,
            "health_checks_passed":  self.health_checks_passed,
            "health_checks_failed":  self.health_checks_failed,
            "request_count":         self.request_count,
            "error_rate":            round(self.error_rate, 4),
        }


@dataclass
class PromotionResult:
    success: bool
    active_slot: str
    previous_slot: str
    steps_completed: int
    rolled_back: bool = False
    rollback_reason: str = ""
    duration_s: float = 0.0
    health_at_each_step: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success":         self.success,
            "active_slot":     self.active_slot,
            "previous_slot":   self.previous_slot,
            "steps_completed": self.steps_completed,
            "rolled_back":     self.rolled_back,
            "rollback_reason": self.rollback_reason,
            "duration_s":      round(self.duration_s, 2),
        }


class BlueGreenRouter:
    """Routes agent traffic between blue and green deployment slots.

    Parameters
    ----------
    health_check_fn:    Optional async callable(deployment) → bool for custom checks.
    max_error_rate:     Error rate threshold above which promotion is halted (default 5%).
    health_check_rps:   Synthetic requests/sec during health check window (default 10).
    health_window_s:    Seconds to observe health at each step (default 5).
    """

    def __init__(
        self,
        health_check_fn: Any = None,
        max_error_rate: float = 0.05,
        health_window_s: float = 5.0,
    ) -> None:
        self._slots: dict[str, AgentDeployment] = {}
        self._active_slot: str = "blue"
        self._traffic_split: float = 0.0   # fraction sent to standby slot
        self._health_fn = health_check_fn
        self._max_error_rate = max_error_rate
        self._health_window = health_window_s

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, slot: str, deployment: AgentDeployment) -> None:
        """Register a deployment in a slot. slot must be 'blue' or 'green'."""
        if slot not in ("blue", "green"):
            raise ValueError(f"slot must be 'blue' or 'green', got {slot!r}")
        self._slots[slot] = deployment
        if not self._slots.get(self._active_slot):
            self._active_slot = slot

    # ── Traffic routing ───────────────────────────────────────────────────────

    def route(self) -> AgentDeployment:
        """Return the deployment that should serve the current request.

        Uses weighted random routing based on the current traffic split.
        """
        standby = self._standby_slot
        if standby and standby in self._slots and random.random() < self._traffic_split:
            return self._slots[standby]
        active = self._slots.get(self._active_slot)
        if active is None:
            raise RuntimeError("No active deployment registered")
        return active

    @property
    def _standby_slot(self) -> str:
        return "green" if self._active_slot == "blue" else "blue"

    # ── Promotion ─────────────────────────────────────────────────────────────

    async def promote(
        self,
        target_slot: str,
        steps: list[float] | None = None,
    ) -> PromotionResult:
        """Gradually promote *target_slot* to active, rolling back on health failure.

        Parameters
        ----------
        target_slot:  The slot to promote ('blue' or 'green').
        steps:        Traffic fractions to test at each step (default [0.1, 0.5, 1.0]).
        """
        steps = steps or [0.1, 0.5, 1.0]
        previous_slot = self._active_slot
        start = time.monotonic()
        health_log: list[dict[str, Any]] = []

        if target_slot not in self._slots:
            return PromotionResult(
                success=False,
                active_slot=self._active_slot,
                previous_slot=previous_slot,
                steps_completed=0,
                rollback_reason=f"slot {target_slot!r} not registered",
                duration_s=time.monotonic() - start,
            )

        for i, split in enumerate(steps):
            self._traffic_split = split
            # The target_slot becomes the standby during ramp; set active appropriately
            if target_slot == self._standby_slot:
                pass  # standby is already target_slot
            else:
                # swap so target is in standby position
                self._active_slot = self._standby_slot

            # Observe health during the window
            healthy, reason = await self._observe_health(target_slot)
            health_log.append({
                "step": i + 1,
                "split_pct": int(split * 100),
                "healthy": healthy,
                "reason": reason,
            })

            if not healthy:
                # Rollback
                self._active_slot = previous_slot
                self._traffic_split = 0.0
                return PromotionResult(
                    success=False,
                    active_slot=self._active_slot,
                    previous_slot=previous_slot,
                    steps_completed=i,
                    rolled_back=True,
                    rollback_reason=reason,
                    duration_s=time.monotonic() - start,
                    health_at_each_step=health_log,
                )

        # Full promotion
        self._active_slot = target_slot
        self._traffic_split = 0.0
        return PromotionResult(
            success=True,
            active_slot=self._active_slot,
            previous_slot=previous_slot,
            steps_completed=len(steps),
            duration_s=time.monotonic() - start,
            health_at_each_step=health_log,
        )

    async def _observe_health(self, slot: str) -> tuple[bool, str]:
        """Run health checks for the given slot."""
        deployment = self._slots.get(slot)
        if deployment is None:
            return False, f"slot {slot!r} not registered"

        # Custom health check function
        if self._health_fn:
            try:
                ok = await asyncio.wait_for(
                    asyncio.coroutine(self._health_fn)(deployment)
                    if not asyncio.iscoroutinefunction(self._health_fn)
                    else self._health_fn(deployment),
                    timeout=10.0,
                )
                if not ok:
                    return False, "custom_health_check_failed"
            except Exception as e:
                return False, f"health_check_error:{e}"

        # Built-in: observe error rate during the health window
        await asyncio.sleep(self._health_window)

        if deployment.error_rate > self._max_error_rate:
            return False, f"error_rate_{deployment.error_rate:.1%}_exceeds_{self._max_error_rate:.1%}"

        deployment.health_checks_passed += 1
        return True, "ok"

    def rollback(self) -> str:
        """Immediately roll back to the previous slot."""
        previous = self._standby_slot
        if previous in self._slots:
            self._active_slot = previous
            self._traffic_split = 0.0
            return previous
        return self._active_slot

    def status(self) -> dict[str, Any]:
        return {
            "active_slot":   self._active_slot,
            "standby_slot":  self._standby_slot,
            "traffic_split": self._traffic_split,
            "slots": {
                slot: dep.to_dict()
                for slot, dep in self._slots.items()
            },
        }


# ── State persistence ─────────────────────────────────────────────────────────

class DeploymentStore:
    """Persist deployment state to a local JSON file."""

    def __init__(self, path: str = ".meshflow_deploy.json") -> None:
        self._path = Path(path)

    def save(self, router: BlueGreenRouter) -> None:
        self._path.write_text(json.dumps(router.status(), indent=2))

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


__all__ = [
    "BlueGreenRouter",
    "AgentDeployment",
    "PromotionResult",
    "DeploymentStore",
]
