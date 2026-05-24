"""Prometheus-compatible metrics collector for MeshFlow.

Exposes a /metrics endpoint in Prometheus text format (version 0.0.4).
No prometheus_client dependency required — the format is written directly.

Sprint 44 additions: per-agent labeled counters, handoff + latency metrics,
/ready endpoint support, and wiring to genai.py OTEL emitters.

Metrics:
  meshflow_runs_total{status}                      — counter
  meshflow_run_duration_seconds{q}                 — summary (p50, p95, p99)
  meshflow_tokens_total                            — counter
  meshflow_cost_usd_total                          — counter
  meshflow_blocks_total{reason}                    — counter
  meshflow_hitl_pending                            — gauge
  meshflow_uncertainty_score{agent_id}             — gauge (last value)
  meshflow_agent_calls_total{agent,role}           — counter  [Sprint 44]
  meshflow_agent_tokens_in_total{agent}            — counter  [Sprint 44]
  meshflow_agent_tokens_out_total{agent}           — counter  [Sprint 44]
  meshflow_agent_cost_usd_total{agent}             — counter  [Sprint 44]
  meshflow_agent_blocked_total{agent}              — counter  [Sprint 44]
  meshflow_agent_latency_ms{agent,quantile}        — summary  [Sprint 44]
  meshflow_handoffs_total{from,to}                 — counter  [Sprint 44]
  meshflow_regression_alerts{agent}                — gauge    [Sprint 44]
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any


class MetricsCollector:
    """Thread-safe in-process metrics store. Singleton per process."""

    _instance: "MetricsCollector | None" = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Legacy aggregate counters (kept for backwards compat)
        self._runs: dict[str, int] = defaultdict(int)      # status → count
        self._blocks: dict[str, int] = defaultdict(int)    # reason → count
        self._durations: list[float] = []
        self._tokens: int = 0
        self._cost_usd: float = 0.0
        self._hitl_pending: int = 0
        self._uncertainty: dict[str, float] = {}           # agent_id → last score
        # Per-agent labeled counters (Sprint 44)
        self._agent_calls: dict[str, int] = defaultdict(int)       # "name|role" → count
        self._agent_tokens_in: dict[str, int] = defaultdict(int)   # agent_name → total
        self._agent_tokens_out: dict[str, int] = defaultdict(int)
        self._agent_cost: dict[str, float] = defaultdict(float)
        self._agent_blocked: dict[str, int] = defaultdict(int)
        self._agent_latencies: dict[str, list[float]] = defaultdict(list)  # agent → [ms]
        self._handoffs: dict[str, int] = defaultdict(int)  # "from|to" → count
        self._regression_alerts: dict[str, int] = defaultdict(int) # agent → count

    @classmethod
    def get(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — primarily for tests."""
        with cls._class_lock:
            cls._instance = None

    # ── Legacy API (unchanged) ─────────────────────────────────────────────────

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

    # ── Sprint 44: Per-agent labeled metrics ───────────────────────────────────

    def record_agent_call(
        self,
        agent_name: str,
        role: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        blocked: bool,
        latency_ms: float,
    ) -> None:
        """Record one agent.run() invocation with per-agent label breakdown."""
        key = f"{agent_name}|{role}"
        with self._lock:
            self._agent_calls[key] += 1
            self._agent_tokens_in[agent_name] += tokens_in
            self._agent_tokens_out[agent_name] += tokens_out
            self._agent_cost[agent_name] += cost_usd
            if blocked:
                self._agent_blocked[agent_name] += 1
            lats = self._agent_latencies[agent_name]
            lats.append(latency_ms)
            if len(lats) > 5_000:
                self._agent_latencies[agent_name] = lats[-2_500:]
            # Also update aggregate counters
            self._runs["ok" if not blocked else "blocked"] += 1
            self._tokens += tokens_in + tokens_out
            self._cost_usd += cost_usd
            self._durations.append(latency_ms / 1000.0)

    def record_handoff(self, from_agent: str, to_agent: str) -> None:
        with self._lock:
            self._handoffs[f"{from_agent}|{to_agent}"] += 1

    def set_regression_alerts(self, agent_name: str, count: int) -> None:
        with self._lock:
            self._regression_alerts[agent_name] = count

    def snapshot(self) -> dict[str, Any]:
        """Return a plain-dict snapshot of all current metric values."""
        with self._lock:
            return {
                "total_calls": sum(self._agent_calls.values()),
                "total_tokens_in": sum(self._agent_tokens_in.values()),
                "total_tokens_out": sum(self._agent_tokens_out.values()),
                "total_cost_usd": self._cost_usd,
                "total_blocked": sum(self._agent_blocked.values()),
                "total_handoffs": sum(self._handoffs.values()),
                "agents": list(self._agent_tokens_in.keys()),
            }

    # ── Prometheus text rendering ──────────────────────────────────────────────

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
            agent_calls = dict(self._agent_calls)
            agent_tok_in = dict(self._agent_tokens_in)
            agent_tok_out = dict(self._agent_tokens_out)
            agent_cost = dict(self._agent_cost)
            agent_blocked = dict(self._agent_blocked)
            agent_lats = {k: list(v) for k, v in self._agent_latencies.items()}
            handoffs = dict(self._handoffs)
            reg_alerts = dict(self._regression_alerts)

        lines: list[str] = []

        # ── Legacy aggregate metrics ───────────────────────────────────────────

        lines.append("# HELP meshflow_runs_total Total completed runs by status")
        lines.append("# TYPE meshflow_runs_total counter")
        for status, count in runs.items():
            lines.append(f'meshflow_runs_total{{status="{status}"}} {count}')
        if not runs:
            lines.append('meshflow_runs_total{status="ok"} 0')

        lines.append("# HELP meshflow_run_duration_seconds Run duration in seconds")
        lines.append("# TYPE meshflow_run_duration_seconds summary")
        for q, pct in [(0.5, 50), (0.95, 95), (0.99, 99)]:
            val = self._percentile(durations, pct)
            lines.append(f'meshflow_run_duration_seconds{{quantile="{q}"}} {val:.4f}')
        lines.append(f"meshflow_run_duration_seconds_count {len(durations)}")
        lines.append(f"meshflow_run_duration_seconds_sum {sum(durations):.4f}")

        lines.append("# HELP meshflow_tokens_total Total LLM tokens consumed")
        lines.append("# TYPE meshflow_tokens_total counter")
        lines.append(f"meshflow_tokens_total {tokens}")

        lines.append("# HELP meshflow_cost_usd_total Total LLM cost in USD")
        lines.append("# TYPE meshflow_cost_usd_total counter")
        lines.append(f"meshflow_cost_usd_total {cost:.6f}")

        lines.append("# HELP meshflow_blocks_total Steps blocked by governance layers")
        lines.append("# TYPE meshflow_blocks_total counter")
        for reason, count in blocks.items():
            lines.append(f'meshflow_blocks_total{{reason="{reason}"}} {count}')
        if not blocks:
            lines.append('meshflow_blocks_total{reason="none"} 0')

        lines.append("# HELP meshflow_hitl_pending Runs currently paused for human approval")
        lines.append("# TYPE meshflow_hitl_pending gauge")
        lines.append(f"meshflow_hitl_pending {hitl}")

        lines.append("# HELP meshflow_uncertainty_score Last uncertainty composite score per agent")
        lines.append("# TYPE meshflow_uncertainty_score gauge")
        for agent_id, score in uncertainty.items():
            lines.append(f'meshflow_uncertainty_score{{agent_id="{agent_id}"}} {score:.4f}')

        # ── Sprint 44: Per-agent labeled metrics ───────────────────────────────

        lines.append("# HELP meshflow_agent_calls_total Total agent.run() calls by agent and role")
        lines.append("# TYPE meshflow_agent_calls_total counter")
        for key, count in agent_calls.items():
            name, role = key.split("|", 1)
            lines.append(f'meshflow_agent_calls_total{{agent="{name}",role="{role}"}} {count}')

        lines.append("# HELP meshflow_agent_tokens_in_total Input tokens consumed per agent")
        lines.append("# TYPE meshflow_agent_tokens_in_total counter")
        for name, count in agent_tok_in.items():
            lines.append(f'meshflow_agent_tokens_in_total{{agent="{name}"}} {count}')

        lines.append("# HELP meshflow_agent_tokens_out_total Output tokens produced per agent")
        lines.append("# TYPE meshflow_agent_tokens_out_total counter")
        for name, count in agent_tok_out.items():
            lines.append(f'meshflow_agent_tokens_out_total{{agent="{name}"}} {count}')

        lines.append("# HELP meshflow_agent_cost_usd_total LLM cost in USD per agent")
        lines.append("# TYPE meshflow_agent_cost_usd_total counter")
        for name, c in agent_cost.items():
            lines.append(f'meshflow_agent_cost_usd_total{{agent="{name}"}} {c:.6f}')

        lines.append("# HELP meshflow_agent_blocked_total Blocked calls per agent")
        lines.append("# TYPE meshflow_agent_blocked_total counter")
        for name, count in agent_blocked.items():
            lines.append(f'meshflow_agent_blocked_total{{agent="{name}"}} {count}')

        lines.append("# HELP meshflow_agent_latency_ms Agent run() latency in milliseconds")
        lines.append("# TYPE meshflow_agent_latency_ms summary")
        for name, lats in agent_lats.items():
            for q, pct in [(0.5, 50), (0.95, 95), (0.99, 99)]:
                val = self._percentile(lats, pct)
                lines.append(f'meshflow_agent_latency_ms{{agent="{name}",quantile="{q}"}} {val:.2f}')
            lines.append(f'meshflow_agent_latency_ms_count{{agent="{name}"}} {len(lats)}')
            lines.append(f'meshflow_agent_latency_ms_sum{{agent="{name}"}} {sum(lats):.2f}')

        lines.append("# HELP meshflow_handoffs_total Agent-to-agent handoff count")
        lines.append("# TYPE meshflow_handoffs_total counter")
        for key, count in handoffs.items():
            frm, to = key.split("|", 1)
            lines.append(f'meshflow_handoffs_total{{from="{frm}",to="{to}"}} {count}')

        lines.append("# HELP meshflow_regression_alerts Active regression alert count per agent")
        lines.append("# TYPE meshflow_regression_alerts gauge")
        for name, count in reg_alerts.items():
            lines.append(f'meshflow_regression_alerts{{agent="{name}"}} {count}')

        return "\n".join(lines) + "\n"
