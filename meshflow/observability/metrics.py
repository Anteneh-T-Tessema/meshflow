"""Prometheus-compatible metrics collector for MeshFlow.

Exposes a /metrics endpoint in Prometheus text format (version 0.0.4).
No prometheus_client dependency required — the format is written directly.

Metrics:
  meshflow_runs_total{status}            — counter
  meshflow_run_duration_seconds{q}       — summary (p50, p95, p99)
  meshflow_tokens_total                  — counter
  meshflow_cost_usd_total                — counter
  meshflow_blocks_total{reason}          — counter
  meshflow_hitl_pending                  — gauge
  meshflow_uncertainty_score{agent_id}   — gauge (last value)
"""

from __future__ import annotations

import threading
from collections import defaultdict


class MetricsCollector:
    """Thread-safe in-process metrics store. Singleton per process."""

    _instance: "MetricsCollector | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._runs: dict[str, int] = defaultdict(int)  # status → count
        self._blocks: dict[str, int] = defaultdict(int)  # reason → count
        self._durations: list[float] = []
        self._tokens: int = 0
        self._cost_usd: float = 0.0
        self._hitl_pending: int = 0
        self._uncertainty: dict[str, float] = {}  # agent_id → last score
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def record_run(
        self,
        status: str,
        duration_s: float,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            self._runs[status] += 1
            self._durations.append(duration_s)
            if len(self._durations) > 10_000:
                self._durations = self._durations[-5_000:]
            self._tokens += tokens
            self._cost_usd += cost_usd

    def record_block(self, reason: str) -> None:
        prefix = reason.split(":")[0] if ":" in reason else reason
        with self._lock:
            self._blocks[prefix] += 1

    def record_uncertainty(self, agent_id: str, score: float) -> None:
        with self._lock:
            self._uncertainty[agent_id] = score

    def set_hitl_pending(self, count: int) -> None:
        with self._lock:
            self._hitl_pending = count

    def _percentile(self, data: list[float], p: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]

    def prometheus_text(self) -> str:
        with self._lock:
            runs = dict(self._runs)
            blocks = dict(self._blocks)
            durations = list(self._durations)
            tokens = self._tokens
            cost = self._cost_usd
            hitl = self._hitl_pending
            uncertainty = dict(self._uncertainty)

        lines: list[str] = []

        # meshflow_runs_total
        lines.append("# HELP meshflow_runs_total Total completed runs by status")
        lines.append("# TYPE meshflow_runs_total counter")
        for status, count in runs.items():
            lines.append(f'meshflow_runs_total{{status="{status}"}} {count}')
        if not runs:
            lines.append('meshflow_runs_total{status="ok"} 0')

        # meshflow_run_duration_seconds (summary)
        lines.append("# HELP meshflow_run_duration_seconds Run duration in seconds")
        lines.append("# TYPE meshflow_run_duration_seconds summary")
        for q, pct in [(0.5, 50), (0.95, 95), (0.99, 99)]:
            val = self._percentile(durations, pct)
            lines.append(f'meshflow_run_duration_seconds{{quantile="{q}"}} {val:.4f}')
        lines.append(f"meshflow_run_duration_seconds_count {len(durations)}")
        lines.append(f"meshflow_run_duration_seconds_sum {sum(durations):.4f}")

        # meshflow_tokens_total
        lines.append("# HELP meshflow_tokens_total Total LLM tokens consumed")
        lines.append("# TYPE meshflow_tokens_total counter")
        lines.append(f"meshflow_tokens_total {tokens}")

        # meshflow_cost_usd_total
        lines.append("# HELP meshflow_cost_usd_total Total LLM cost in USD")
        lines.append("# TYPE meshflow_cost_usd_total counter")
        lines.append(f"meshflow_cost_usd_total {cost:.6f}")

        # meshflow_blocks_total
        lines.append("# HELP meshflow_blocks_total Steps blocked by governance layers")
        lines.append("# TYPE meshflow_blocks_total counter")
        for reason, count in blocks.items():
            lines.append(f'meshflow_blocks_total{{reason="{reason}"}} {count}')
        if not blocks:
            lines.append('meshflow_blocks_total{reason="none"} 0')

        # meshflow_hitl_pending
        lines.append("# HELP meshflow_hitl_pending Runs currently paused for human approval")
        lines.append("# TYPE meshflow_hitl_pending gauge")
        lines.append(f"meshflow_hitl_pending {hitl}")

        # meshflow_uncertainty_score
        lines.append("# HELP meshflow_uncertainty_score Last uncertainty composite score per agent")
        lines.append("# TYPE meshflow_uncertainty_score gauge")
        for agent_id, score in uncertainty.items():
            lines.append(f'meshflow_uncertainty_score{{agent_id="{agent_id}"}} {score:.4f}')

        return "\n".join(lines) + "\n"
