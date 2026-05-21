"""Policy enforcement engine — runtime budget, circuit breaker, reliability tracking.

All constraints declared in Policy are enforced here, not scattered across agents.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from meshflow.core.schemas import CircuitBreakerConfig, Policy, RunStatus


class BudgetExceededError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class TimeoutError(Exception):
    pass


@dataclass
class BudgetTracker:
    """Tracks spend across cost, tokens, carbon, and time."""
    policy: Policy
    _usd: float = 0.0
    _tokens: int = 0
    _carbon_g: float = 0.0
    _start: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self._start = time.monotonic()

    def charge(self, usd: float, tokens: int, carbon_g: float = 0.0) -> None:
        self._usd += usd
        self._tokens += tokens
        self._carbon_g += carbon_g
        self._check()

    def elapsed_s(self) -> float:
        return time.monotonic() - self._start

    def remaining_usd(self) -> float:
        return self.policy.budget_usd - self._usd

    def remaining_tokens(self) -> int:
        return self.policy.budget_tokens - self._tokens

    def pre_check(self) -> None:
        """Raise BudgetExceededError if any budget is already at/over limit."""
        self._check()

    def _check(self) -> None:
        if self._usd > self.policy.budget_usd:
            raise BudgetExceededError(
                f"USD budget exceeded: ${self._usd:.4f} > ${self.policy.budget_usd:.4f}"
            )
        if self._tokens > self.policy.budget_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {self._tokens:,} > {self.policy.budget_tokens:,}"
            )
        if self.elapsed_s() > self.policy.timeout_s:
            raise TimeoutError(
                f"Run timeout: {self.elapsed_s():.1f}s > {self.policy.timeout_s:.1f}s"
            )
        if (
            self.policy.enable_environmental
            and self._carbon_g > self.policy.carbon_budget_g
        ):
            raise BudgetExceededError(
                f"Carbon budget exceeded: {self._carbon_g:.1f}g > {self.policy.carbon_budget_g:.1f}g"
            )

    def summary(self) -> dict[str, Any]:
        return {
            "usd_spent": round(self._usd, 6),
            "usd_budget": self.policy.budget_usd,
            "tokens_used": self._tokens,
            "token_budget": self.policy.budget_tokens,
            "carbon_g": round(self._carbon_g, 3),
            "elapsed_s": round(self.elapsed_s(), 2),
        }


class CircuitBreaker:
    """Per-agent circuit breaker — prevents infinite retry loops.

    States: CLOSED (normal) → OPEN (tripped) → HALF_OPEN (testing recovery)
    """

    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._config = config
        self._failures: dict[str, deque[float]] = {}
        self._open_since: dict[str, float] = {}
        self._state: dict[str, str] = {}  # "closed" | "open" | "half_open"

    def _agent_state(self, agent_id: str) -> str:
        return self._state.get(agent_id, "closed")

    def record_failure(self, agent_id: str) -> None:
        now = time.monotonic()
        if agent_id not in self._failures:
            self._failures[agent_id] = deque()
        q = self._failures[agent_id]
        q.append(now)
        # Prune outside window
        cutoff = now - self._config.failure_window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self._config.failure_threshold:
            self._state[agent_id] = "open"
            self._open_since[agent_id] = now

    def record_success(self, agent_id: str) -> None:
        if self._agent_state(agent_id) == "half_open":
            self._state[agent_id] = "closed"
            self._failures.pop(agent_id, None)

    def allow(self, agent_id: str) -> bool:
        state = self._agent_state(agent_id)
        if state == "closed":
            return True
        if state == "open":
            elapsed = time.monotonic() - self._open_since.get(agent_id, 0)
            if elapsed >= self._config.half_open_after_s:
                self._state[agent_id] = "half_open"
                return True
            return False
        return True  # half_open: allow one probe


class ReliabilityBudget:
    """Tracks compound reliability across the agent chain.

    At 99% per-step reliability, a 10-step chain has 90.4% success probability.
    The budget signals when the accumulated risk exceeds acceptable thresholds.
    """

    def __init__(self, target_reliability: float = 0.90) -> None:
        self._target = target_reliability
        self._compound: float = 1.0
        self._steps: int = 0

    def step(self, step_reliability: float) -> float:
        """Record a step and return current compound reliability."""
        self._compound *= max(0.0, min(1.0, step_reliability))
        self._steps += 1
        return self._compound

    def is_acceptable(self) -> bool:
        return self._compound >= self._target

    def compound(self) -> float:
        return self._compound


class PolicyEngine:
    """Unified policy enforcement — wraps BudgetTracker, CircuitBreaker, and ReliabilityBudget."""

    def __init__(self, policy: Policy, run_id: str) -> None:
        self.policy = policy
        self.run_id = run_id
        self.budget = BudgetTracker(policy=policy)
        self.circuit = CircuitBreaker(policy.circuit_breaker)
        self.reliability = ReliabilityBudget()
        self._step_count: int = 0

    def pre_step(self, agent_id: str) -> None:
        """Call before each agent step — checks circuit breaker and step count."""
        if not self.circuit.allow(agent_id):
            raise CircuitOpenError(f"Circuit open for agent '{agent_id}'")
        self._step_count += 1
        if self._step_count > self.policy.max_steps:
            raise BudgetExceededError(
                f"Max steps exceeded: {self._step_count} > {self.policy.max_steps}"
            )

    def post_step(
        self,
        agent_id: str,
        success: bool,
        usd: float = 0.0,
        tokens: int = 0,
        carbon_g: float = 0.0,
        step_reliability: float = 1.0,
    ) -> None:
        """Call after each agent step — records outcome and charges budget."""
        if success:
            self.circuit.record_success(agent_id)
        else:
            self.circuit.record_failure(agent_id)
        self.budget.charge(usd, tokens, carbon_g)
        self.reliability.step(step_reliability)

    def check_complexity(self, task: str, agent_count: int) -> dict[str, Any]:
        """Complexity router — determines if multi-agent is actually needed.

        A single-agent approach wins on latency + cost ~70% of the time.
        Returns a recommendation the caller can act on.
        """
        words = len(task.split())
        single_agent_indicators = [
            words < 50,
            agent_count > 6,
            "simple" in task.lower(),
            "quick" in task.lower(),
            "summarize" in task.lower() and words < 100,
        ]
        single_wins = sum(single_agent_indicators)
        if single_wins >= 2:
            return {
                "recommendation": "single_agent",
                "reason": "Task complexity does not justify multi-agent overhead",
                "confidence": min(0.5 + single_wins * 0.1, 0.95),
            }
        return {
            "recommendation": "multi_agent",
            "reason": "Task complexity warrants orchestration",
            "confidence": 0.75,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "steps": self._step_count,
            "compound_reliability": round(self.reliability.compound(), 4),
            **self.budget.summary(),
        }
