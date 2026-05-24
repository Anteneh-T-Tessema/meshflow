"""Sprint 44 — Prometheus metrics: per-agent counters, /metrics + /ready endpoints."""

from __future__ import annotations

import os
import sys
import json
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.observability.metrics import MetricsCollector
from meshflow.observability.genai import (
    configure_telemetry,
    get_span_store,
    record_agent_step,
    record_handoff,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset():
    MetricsCollector.reset()
    configure_telemetry(enabled=True)
    get_span_store().clear()
    yield
    MetricsCollector.reset()
    get_span_store().clear()


# ── MetricsCollector — record_agent_call ──────────────────────────────────────

class TestRecordAgentCall:
    def test_increments_call_count(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("billing", "executor", 100, 50, 0.001, False, 120.0)
        snap = mc.snapshot()
        assert snap["total_calls"] == 1

    def test_multiple_agents(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("billing", "executor", 100, 50, 0.001, False, 50.0)
        mc.record_agent_call("support", "executor", 80, 30, 0.0005, False, 60.0)
        snap = mc.snapshot()
        assert snap["total_calls"] == 2
        assert "billing" in snap["agents"]
        assert "support" in snap["agents"]

    def test_tokens_accumulated(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("bot", "executor", 100, 50, 0.0, False, 10.0)
        mc.record_agent_call("bot", "executor", 200, 100, 0.0, False, 10.0)
        snap = mc.snapshot()
        assert snap["total_tokens_in"] == 300
        assert snap["total_tokens_out"] == 150

    def test_blocked_tracked(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("bot", "executor", 0, 0, 0.0, True, 5.0)
        snap = mc.snapshot()
        assert snap["total_blocked"] == 1

    def test_cost_accumulated(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("bot", "executor", 100, 50, 0.001, False, 10.0)
        mc.record_agent_call("bot", "executor", 100, 50, 0.002, False, 10.0)
        snap = mc.snapshot()
        assert abs(snap["total_cost_usd"] - 0.003) < 1e-9

    def test_also_increments_aggregate_runs(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("bot", "executor", 10, 5, 0.0, False, 10.0)
        text = mc.prometheus_text()
        assert "meshflow_runs_total" in text


# ── MetricsCollector — record_handoff ─────────────────────────────────────────

class TestRecordHandoff:
    def test_handoff_counter(self):
        mc = MetricsCollector.get()
        mc.record_handoff("triage", "billing")
        mc.record_handoff("triage", "billing")
        mc.record_handoff("triage", "support")
        snap = mc.snapshot()
        assert snap["total_handoffs"] == 3

    def test_handoff_in_prometheus(self):
        mc = MetricsCollector.get()
        mc.record_handoff("a", "b")
        text = mc.prometheus_text()
        assert "meshflow_handoffs_total" in text
        assert 'from="a"' in text
        assert 'to="b"' in text


# ── MetricsCollector — regression alerts ──────────────────────────────────────

class TestRegressionAlerts:
    def test_set_and_render(self):
        mc = MetricsCollector.get()
        mc.set_regression_alerts("billing", 3)
        text = mc.prometheus_text()
        assert "meshflow_regression_alerts" in text
        assert 'agent="billing"' in text
        assert "3" in text


# ── prometheus_text format ────────────────────────────────────────────────────

class TestPrometheusText:
    def test_has_required_sections(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("bot", "executor", 100, 50, 0.001, False, 42.0)
        text = mc.prometheus_text()
        assert "# HELP meshflow_agent_calls_total" in text
        assert "# TYPE meshflow_agent_calls_total counter" in text
        assert "# HELP meshflow_agent_tokens_in_total" in text
        assert "# HELP meshflow_agent_tokens_out_total" in text
        assert "# HELP meshflow_agent_cost_usd_total" in text
        assert "# HELP meshflow_agent_blocked_total" in text
        assert "# HELP meshflow_agent_latency_ms" in text

    def test_per_agent_labels_in_calls(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("billing", "executor", 10, 5, 0.0, False, 10.0)
        text = mc.prometheus_text()
        assert 'agent="billing"' in text
        assert 'role="executor"' in text

    def test_per_agent_labels_in_tokens(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("billing", "executor", 100, 50, 0.0, False, 10.0)
        text = mc.prometheus_text()
        assert "meshflow_agent_tokens_in_total" in text
        assert "100" in text

    def test_per_agent_latency_quantiles(self):
        mc = MetricsCollector.get()
        for ms in [10.0, 20.0, 50.0, 100.0, 200.0]:
            mc.record_agent_call("bot", "executor", 0, 0, 0.0, False, ms)
        text = mc.prometheus_text()
        assert 'quantile="0.5"' in text
        assert 'quantile="0.95"' in text
        assert 'quantile="0.99"' in text

    def test_blocked_metric(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("bot", "executor", 0, 0, 0.0, True, 5.0)
        text = mc.prometheus_text()
        assert "meshflow_agent_blocked_total" in text

    def test_ends_with_newline(self):
        mc = MetricsCollector.get()
        text = mc.prometheus_text()
        assert text.endswith("\n")

    def test_no_duplicate_help_lines(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("a", "executor", 10, 5, 0.0, False, 10.0)
        mc.record_agent_call("b", "planner", 10, 5, 0.0, False, 10.0)
        text = mc.prometheus_text()
        help_lines = [l for l in text.splitlines() if l.startswith("# HELP meshflow_agent_calls_total")]
        assert len(help_lines) == 1


# ── genai.py → MetricsCollector wiring ────────────────────────────────────────

class TestGenAIWiring:
    def test_record_agent_step_increments_metrics(self):
        record_agent_step(
            agent_name="billing", role="executor", model="claude-sonnet-4-6",
            tokens_in=100, tokens_out=50, cost_usd=0.001, confidence=0.9, blocked=False,
        )
        mc = MetricsCollector.get()
        snap = mc.snapshot()
        assert snap["total_calls"] == 1
        assert snap["total_tokens_in"] == 100
        assert snap["total_tokens_out"] == 50

    def test_record_agent_step_blocked(self):
        record_agent_step(
            agent_name="bot", role="executor", model="gpt-4o",
            tokens_in=10, tokens_out=0, cost_usd=0.0, confidence=0.0, blocked=True,
        )
        snap = MetricsCollector.get().snapshot()
        assert snap["total_blocked"] == 1

    def test_record_handoff_increments_metrics(self):
        record_handoff("triage", "billing", reason="needs invoice")
        snap = MetricsCollector.get().snapshot()
        assert snap["total_handoffs"] == 1

    def test_multiple_steps_accumulate(self):
        for i in range(5):
            record_agent_step(
                agent_name="bot", role="executor", model="claude-sonnet-4-6",
                tokens_in=10, tokens_out=5, cost_usd=0.0001, confidence=0.9, blocked=False,
            )
        snap = MetricsCollector.get().snapshot()
        assert snap["total_calls"] == 5
        assert snap["total_tokens_in"] == 50

    def test_metrics_disabled_otel_still_records(self):
        configure_telemetry(enabled=False)
        record_agent_step(
            agent_name="bot", role="executor", model="claude-sonnet-4-6",
            tokens_in=10, tokens_out=5, cost_usd=0.0, confidence=0.9, blocked=False,
        )
        # MetricsCollector gets data even when OTEL span store is disabled
        snap = MetricsCollector.get().snapshot()
        assert snap["total_calls"] == 1
        configure_telemetry(enabled=True)


# ── A2A server /metrics + /ready endpoints ────────────────────────────────────

class TestA2AMetricsEndpoint:
    @pytest.fixture
    def server(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        agent = Agent(name="test-metrics", role="executor")
        srv = A2AServer(agent, host="127.0.0.1", port=0)
        # HTTPServer with port=0 assigns a random free port
        from http.server import HTTPServer
        handler = srv._make_handler()
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        import threading
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        yield f"http://127.0.0.1:{port}"
        httpd.shutdown()

    def _get(self, url: str) -> tuple[int, str]:
        resp = urllib.request.urlopen(url, timeout=5)
        return resp.status, resp.read().decode()

    def test_metrics_endpoint_returns_200(self, server):
        status, body = self._get(f"{server}/metrics")
        assert status == 200

    def test_metrics_content_type(self, server):
        resp = urllib.request.urlopen(f"{server}/metrics", timeout=5)
        ct = resp.headers.get("Content-Type", "")
        assert "text/plain" in ct

    def test_metrics_has_prometheus_format(self, server):
        _, body = self._get(f"{server}/metrics")
        assert "# HELP" in body
        assert "# TYPE" in body
        assert "meshflow_runs_total" in body

    def test_ready_endpoint_returns_200(self, server):
        status, body = self._get(f"{server}/ready")
        assert status == 200
        data = json.loads(body)
        assert data["status"] == "ready"

    def test_ready_has_agent_name(self, server):
        _, body = self._get(f"{server}/ready")
        data = json.loads(body)
        assert data["agent"] == "test-metrics"

    def test_health_still_works(self, server):
        status, body = self._get(f"{server}/health")
        assert status == 200
        assert json.loads(body)["status"] == "ok"


# ── AgentCard capabilities ─────────────────────────────────────────────────────

class TestAgentCardCapabilities:
    def test_card_includes_metrics(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        agent = Agent(name="x", role="executor")
        srv = A2AServer(agent, port=9999)
        card = srv.card()
        assert "metrics" in card.capabilities


# ── MetricsCollector singleton + reset ────────────────────────────────────────

class TestSingleton:
    def test_same_instance(self):
        a = MetricsCollector.get()
        b = MetricsCollector.get()
        assert a is b

    def test_reset_gives_fresh(self):
        mc = MetricsCollector.get()
        mc.record_agent_call("bot", "executor", 10, 5, 0.0, False, 10.0)
        MetricsCollector.reset()
        fresh = MetricsCollector.get()
        assert fresh.snapshot()["total_calls"] == 0

    def test_hitl_pending(self):
        mc = MetricsCollector.get()
        mc.set_hitl_pending(7)
        text = mc.prometheus_text()
        assert "meshflow_hitl_pending 7" in text

    def test_uncertainty_score(self):
        mc = MetricsCollector.get()
        mc.record_uncertainty("billing", 0.82)
        text = mc.prometheus_text()
        assert "meshflow_uncertainty_score" in text
        assert "billing" in text


# ── Public API ─────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_metrics_collector_importable(self):
        from meshflow.observability.metrics import MetricsCollector
        assert MetricsCollector is not None

    def test_otel_exporter_importable(self):
        from meshflow.observability.otel_exporter import (
            OTELExporter, get_global_exporter, from_env,
        )
        assert all(x is not None for x in [OTELExporter, get_global_exporter, from_env])

    def test_exporter_config(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter(endpoint="http://localhost:4318", service_name="test", enabled=False)
        cfg = exp.config()
        assert cfg["service_name"] == "test"
        assert cfg["enabled"] is False
        assert cfg["exported_count"] == 0
