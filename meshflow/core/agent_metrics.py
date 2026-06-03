"""Per-agent call/token/latency metrics with a structured report.

AgentMetrics is a lightweight, thread-safe collector that wraps any
callable step and records timing, token consumption, and confidence for
every call.  It is designed to be composed with Agent steps rather than
requiring changes to the Agent class itself.

Usage::

    from meshflow.core.agent_metrics import AgentMetrics

    metrics = AgentMetrics()

    # Record a call manually (e.g. inside a custom step):
    with metrics.record("researcher"):
        result = researcher_agent.run(task)

    # Or record a completed outcome:
    metrics.add("researcher", tokens=512, cost_usd=0.003, confidence=0.88, latency_s=1.2)

    print(metrics.report())
    # AgentMetrics report
    # -------------------
    # researcher   calls=1  avg_latency=1.20s  total_tokens=512  total_cost=$0.0030  avg_conf=0.88
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator


@dataclass
class _CallRecord:
    agent_name: str
    latency_s: float
    tokens: int
    cost_usd: float
    confidence: float


@dataclass
class AgentMetricsSummary:
    """Summary statistics for a single agent."""

    agent_name: str
    calls: int
    total_tokens: int
    total_cost_usd: float
    avg_latency_s: float
    p95_latency_s: float
    avg_confidence: float

    def __str__(self) -> str:
        return (
            f"{self.agent_name:<20} calls={self.calls}  "
            f"avg_latency={self.avg_latency_s:.2f}s  "
            f"p95_latency={self.p95_latency_s:.2f}s  "
            f"total_tokens={self.total_tokens}  "
            f"total_cost=${self.total_cost_usd:.4f}  "
            f"avg_conf={self.avg_confidence:.2f}"
        )


@dataclass
class AgentMetricsReport:
    """Full metrics report across all agents."""

    summaries: list[AgentMetricsSummary] = field(default_factory=list)
    total_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_s: float = 0.0

    def __str__(self) -> str:
        lines = [
            "AgentMetrics report",
            "-" * 19,
        ]
        for s in self.summaries:
            lines.append(str(s))
        lines.append(
            f"\nTotals: calls={self.total_calls}  "
            f"tokens={self.total_tokens}  "
            f"cost=${self.total_cost_usd:.4f}  "
            f"duration={self.total_duration_s:.2f}s"
        )
        return "\n".join(lines)


class AgentMetrics:
    """Thread-safe per-agent metrics collector.

    Parameters
    ----------
    enabled:
        Set to ``False`` to disable all recording (no-op). Useful for
        production paths where you want zero overhead.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._records: list[_CallRecord] = []
        self._lock = threading.Lock()
        self._start_time = time.monotonic()

    # ── Write ─────────────────────────────────────────────────────────────────

    def add(
        self,
        agent_name: str,
        *,
        tokens: int = 0,
        cost_usd: float = 0.0,
        confidence: float = 0.0,
        latency_s: float = 0.0,
    ) -> None:
        """Record a completed agent call."""
        if not self._enabled:
            return
        rec = _CallRecord(
            agent_name=agent_name,
            latency_s=latency_s,
            tokens=tokens,
            cost_usd=cost_usd,
            confidence=confidence,
        )
        with self._lock:
            self._records.append(rec)

    @contextmanager
    def record(self, agent_name: str, tokens: int = 0, cost_usd: float = 0.0) -> Generator[None, None, None]:
        """Context manager that times the block and records the result.

        Usage::

            with metrics.record("planner", tokens=300, cost_usd=0.002):
                result = planner.run(task)
        """
        if not self._enabled:
            yield
            return
        t0 = time.monotonic()
        try:
            yield
        finally:
            latency = time.monotonic() - t0
            self.add(agent_name, tokens=tokens, cost_usd=cost_usd, latency_s=latency)

    def record_result(self, agent_name: str, result: Any) -> None:
        """Record metrics from a WorkflowResult or RunResult object."""
        if not self._enabled:
            return
        tokens = getattr(result, "total_tokens", 0) or getattr(result, "tokens", 0)
        cost = getattr(result, "total_cost_usd", 0.0) or getattr(result, "cost_usd", 0.0)
        latency = getattr(result, "duration_s", 0.0)
        confidence = 0.0
        # Try to extract confidence from output
        output = getattr(result, "output", "") or ""
        if output:
            try:
                from meshflow.agents.team import _parse_confidence
                confidence = _parse_confidence(str(output))
            except Exception:
                pass
        self.add(agent_name, tokens=int(tokens), cost_usd=float(cost),
                 confidence=confidence, latency_s=float(latency))

    # ── Read ──────────────────────────────────────────────────────────────────

    def summary(self, agent_name: str) -> AgentMetricsSummary | None:
        """Return summary stats for a single agent, or None if not seen."""
        with self._lock:
            recs = [r for r in self._records if r.agent_name == agent_name]
        if not recs:
            return None
        return self._summarise(agent_name, recs)

    def report(self) -> AgentMetricsReport:
        """Return a full report across all tracked agents."""
        with self._lock:
            records = list(self._records)

        by_agent: dict[str, list[_CallRecord]] = {}
        for r in records:
            by_agent.setdefault(r.agent_name, []).append(r)

        summaries = [
            self._summarise(name, recs)
            for name, recs in sorted(by_agent.items())
        ]

        return AgentMetricsReport(
            summaries=summaries,
            total_calls=len(records),
            total_tokens=sum(r.tokens for r in records),
            total_cost_usd=round(sum(r.cost_usd for r in records), 6),
            total_duration_s=round(time.monotonic() - self._start_time, 2),
        )

    @staticmethod
    def _summarise(name: str, recs: list[_CallRecord]) -> AgentMetricsSummary:
        latencies = sorted(r.latency_s for r in recs)
        n = len(latencies)
        avg_lat = sum(latencies) / n if n else 0.0
        p95_idx = min(int(n * 0.95), n - 1)
        p95_lat = latencies[p95_idx] if latencies else 0.0
        avg_conf = sum(r.confidence for r in recs) / n if n else 0.0
        return AgentMetricsSummary(
            agent_name=name,
            calls=n,
            total_tokens=sum(r.tokens for r in recs),
            total_cost_usd=round(sum(r.cost_usd for r in recs), 6),
            avg_latency_s=round(avg_lat, 3),
            p95_latency_s=round(p95_lat, 3),
            avg_confidence=round(avg_conf, 3),
        )

    def reset(self) -> None:
        """Clear all recorded data."""
        with self._lock:
            self._records.clear()
        self._start_time = time.monotonic()

    @property
    def agent_names(self) -> list[str]:
        """Names of all agents that have been recorded."""
        with self._lock:
            return sorted({r.agent_name for r in self._records})

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)
