"""Sprint 17 — OTEL trace context, graph export, audit CSV/JSON, SLA tracker, rate limiter.

Tests:
  A. TraceContext — parse, create, child_span_id, round-trip header
  B. StepRuntime  — traceparent injected into context, propagates across calls
  C. graph_export — steps_to_mermaid, steps_to_dot, graph_to_mermaid
  D. Ledger       — export_run_csv shape and content
  E. NodeLatencyTracker — record, summary, percentiles, clear
  F. RateLimiter  — allow, token refill, per-key isolation
  G. Server routes — /otel/config, /graph/{run_id}, /audit/export, /sla, /rate-limit/status
  H. CLI          — graph and audit subcommands
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from typing import Any
from unittest.mock import MagicMock



# ─────────────────────────────────────────────────────────────────────────────
# A. TraceContext
# ─────────────────────────────────────────────────────────────────────────────

class TestTraceContext:
    def test_new_generates_valid_header(self):
        from meshflow.observability.trace_context import TraceContext
        ctx = TraceContext.new()
        header = ctx.to_header()
        assert header.startswith("00-")
        parts = header.split("-")
        assert len(parts) == 4
        assert len(parts[1]) == 32   # trace_id
        assert len(parts[2]) == 16   # span_id
        assert parts[3] == "01"

    def test_from_header_roundtrip(self):
        from meshflow.observability.trace_context import TraceContext
        ctx = TraceContext.new()
        header = ctx.to_header()
        ctx2 = TraceContext.from_header(header)
        assert ctx2 is not None
        assert ctx2.trace_id == ctx.trace_id
        assert ctx2.span_id == ctx.span_id

    def test_from_header_returns_none_on_garbage(self):
        from meshflow.observability.trace_context import TraceContext
        assert TraceContext.from_header("not-a-traceparent") is None
        assert TraceContext.from_header("") is None

    def test_child_span_id_differs_from_parent(self):
        from meshflow.observability.trace_context import TraceContext
        ctx = TraceContext.new()
        child = ctx.child_span_id()
        assert child != ctx.span_id
        assert len(child) == 16

    def test_extract_creates_new_context_when_absent(self):
        from meshflow.observability.trace_context import extract_trace_context
        ctx = extract_trace_context({})
        assert len(ctx.trace_id) == 32

    def test_extract_reuses_existing_header(self):
        from meshflow.observability.trace_context import TraceContext, extract_trace_context
        original = TraceContext.new()
        ctx = extract_trace_context({"traceparent": original.to_header()})
        assert ctx.trace_id == original.trace_id

    def test_inject_headers_returns_traceparent(self):
        from meshflow.observability.trace_context import TraceContext, inject_trace_headers
        ctx = TraceContext.new()
        h = inject_trace_headers(ctx)
        assert "traceparent" in h
        assert h["traceparent"] == ctx.to_header()

    def test_to_dict_contains_required_keys(self):
        from meshflow.observability.trace_context import TraceContext
        ctx = TraceContext.new()
        d = ctx.to_dict()
        assert "traceparent" in d
        assert "trace_id" in d
        assert "span_id" in d


# ─────────────────────────────────────────────────────────────────────────────
# B. StepRuntime traceparent injection
# ─────────────────────────────────────────────────────────────────────────────

class TestStepRuntimeTraceContext:
    async def test_trace_id_set_in_context(self):
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import Policy
        from meshflow.core.node import NodeInput, NodeOutput
        from meshflow.core.node import MeshNode, NodeKind

        async def _fn(inp: NodeInput) -> NodeOutput:
            return NodeOutput(content="ok")

        node = MeshNode(id="test_node", kind=NodeKind.NATIVE, _runner=_fn)
        policy = Policy(mode="dev", budget_usd=1.0, budget_tokens=1000)
        rt = StepRuntime(policy=policy, run_id="run-tc-test")

        ctx: dict[str, Any] = {}
        await rt.run(node, NodeInput(task="hello"), ctx)
        assert "_trace_id" in ctx
        assert len(ctx["_trace_id"]) == 32

    async def test_incoming_traceparent_preserved(self):
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import Policy
        from meshflow.core.node import NodeInput, NodeOutput
        from meshflow.core.node import MeshNode, NodeKind
        from meshflow.observability.trace_context import TraceContext

        async def _fn(inp: Any) -> NodeOutput:
            return NodeOutput(content="ok")

        node = MeshNode(id="node_b", kind=NodeKind.NATIVE, _runner=_fn)
        policy = Policy(mode="dev", budget_usd=1.0, budget_tokens=1000)
        rt = StepRuntime(policy=policy, run_id="run-tc-in")

        original = TraceContext.new()
        ctx: dict[str, Any] = {"_traceparent": original.to_header()}
        await rt.run(node, NodeInput(task="hello"), ctx)
        # trace_id must match the incoming context
        assert ctx.get("_trace_id") == original.trace_id

    async def test_traceparent_propagated_to_child_span(self):
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import Policy
        from meshflow.core.node import NodeInput, NodeOutput
        from meshflow.core.node import MeshNode, NodeKind

        async def _fn(inp: Any) -> NodeOutput:
            return NodeOutput(content="ok")

        node = MeshNode(id="node_c", kind=NodeKind.NATIVE, _runner=_fn)
        policy = Policy(mode="dev", budget_usd=1.0, budget_tokens=1000)
        rt = StepRuntime(policy=policy, run_id="run-tc-prop")

        ctx: dict[str, Any] = {}
        await rt.run(node, NodeInput(task="hello"), ctx)
        # After the step, _traceparent should be set for downstream calls
        assert "_traceparent" in ctx
        assert ctx["_traceparent"].startswith("00-")


# ─────────────────────────────────────────────────────────────────────────────
# C. Graph export
# ─────────────────────────────────────────────────────────────────────────────

_SAMPLE_STEPS = [
    {"node_id": "ingest", "verdict": "commit", "blocked": False,
     "tokens_used": 100, "cost_usd": 0.001, "timestamp": "2026-01-01T00:00:00"},
    {"node_id": "analyse", "verdict": "commit", "blocked": False,
     "tokens_used": 200, "cost_usd": 0.002, "timestamp": "2026-01-01T00:00:01"},
    {"node_id": "report", "verdict": "block", "blocked": True,
     "tokens_used": 50,  "cost_usd": 0.0,   "timestamp": "2026-01-01T00:00:02"},
]


class TestGraphExport:
    def test_mermaid_starts_with_flowchart(self):
        from meshflow.core.graph_export import steps_to_mermaid
        out = steps_to_mermaid(_SAMPLE_STEPS, "run-123")
        assert out.startswith("flowchart LR")

    def test_mermaid_contains_node_names(self):
        from meshflow.core.graph_export import steps_to_mermaid
        out = steps_to_mermaid(_SAMPLE_STEPS)
        assert "ingest" in out
        assert "analyse" in out
        assert "report" in out

    def test_mermaid_blocked_node_styled_red(self):
        from meshflow.core.graph_export import steps_to_mermaid
        out = steps_to_mermaid(_SAMPLE_STEPS)
        assert "fill:#ff6b6b" in out

    def test_mermaid_empty_steps_returns_fallback(self):
        from meshflow.core.graph_export import steps_to_mermaid
        out = steps_to_mermaid([])
        assert "flowchart" in out
        assert "empty" in out.lower() or "No steps" in out

    def test_dot_starts_with_digraph(self):
        from meshflow.core.graph_export import steps_to_dot
        out = steps_to_dot(_SAMPLE_STEPS, "run-dot")
        assert out.strip().startswith("digraph")

    def test_dot_contains_edges(self):
        from meshflow.core.graph_export import steps_to_dot
        out = steps_to_dot(_SAMPLE_STEPS)
        assert "->" in out

    def test_dot_blocked_node_red(self):
        from meshflow.core.graph_export import steps_to_dot
        out = steps_to_dot(_SAMPLE_STEPS)
        assert "#ff6b6b" in out

    def test_state_graph_to_mermaid(self):
        from meshflow.core.graph_export import graph_to_mermaid
        from meshflow.core.graph import StateGraph

        async def _noop(inp: Any) -> Any:
            return MagicMock(content="")

        g = StateGraph("run-g")
        g._nodes = {"a": MagicMock(), "b": MagicMock()}
        g._edges = {"a": [MagicMock(target="b", condition=None)]}
        g._entry = "a"
        g._terminals = {"b"}

        out = graph_to_mermaid(g)
        assert "flowchart LR" in out
        assert "a" in out
        assert "b" in out


# ─────────────────────────────────────────────────────────────────────────────
# D. Ledger CSV export
# ─────────────────────────────────────────────────────────────────────────────

class TestLedgerAuditExport:
    async def test_export_run_csv_empty_on_missing_run(self):
        from meshflow.core.ledger import ReplayLedger
        ledger = ReplayLedger(":memory:")
        csv = await ledger.export_run_csv("nonexistent-run")
        assert csv == ""

    async def test_export_run_csv_has_header_row(self):
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import Policy
        from meshflow.core.node import NodeInput, NodeOutput
        from meshflow.core.node import MeshNode, NodeKind

        async def _fn(inp: Any) -> NodeOutput:
            return NodeOutput(content="csv_test_output")

        ledger = ReplayLedger(":memory:")
        node = MeshNode(id="csv_node", kind=NodeKind.NATIVE, _runner=_fn)
        policy = Policy(mode="dev", budget_usd=1.0, budget_tokens=1000)
        rt = StepRuntime(policy=policy, run_id="run-csv-1", ledger=ledger)
        await rt.run(node, NodeInput(task="test"), {})

        csv = await ledger.export_run_csv("run-csv-1")
        assert csv
        lines = csv.strip().splitlines()
        assert "step_id" in lines[0]
        assert "node_id" in lines[0]
        assert "csv_node" in lines[1]

    async def test_export_run_json_valid(self):
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import Policy
        from meshflow.core.node import NodeInput, NodeOutput
        from meshflow.core.node import MeshNode, NodeKind

        async def _fn(inp: Any) -> NodeOutput:
            return NodeOutput(content="json_out")

        ledger = ReplayLedger(":memory:")
        node = MeshNode(id="json_node", kind=NodeKind.NATIVE, _runner=_fn)
        policy = Policy(mode="dev", budget_usd=1.0, budget_tokens=1000)
        rt = StepRuntime(policy=policy, run_id="run-json-1", ledger=ledger)
        await rt.run(node, NodeInput(task="test"), {})

        raw = await ledger.export_run("run-json-1")
        data = json.loads(raw)
        assert data["run_id"] == "run-json-1"
        assert len(data["steps"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# E. NodeLatencyTracker
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeLatencyTracker:
    def test_record_and_summary(self):
        from meshflow.observability.sla import NodeLatencyTracker
        t = NodeLatencyTracker()
        for ms in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]:
            t.record("my_node", ms)
        s = t.summary("my_node")
        assert s is not None
        assert s.count == 10
        assert s.min_ms == 10.0
        assert s.max_ms == 100.0
        assert s.p50_ms >= 50.0
        assert s.p95_ms >= 90.0

    def test_summary_none_for_unknown_node(self):
        from meshflow.observability.sla import NodeLatencyTracker
        t = NodeLatencyTracker()
        assert t.summary("ghost") is None

    def test_report_returns_list(self):
        from meshflow.observability.sla import NodeLatencyTracker
        t = NodeLatencyTracker()
        t.record("a", 50.0)
        t.record("b", 100.0)
        report = t.report()
        assert isinstance(report, list)
        assert len(report) == 2
        node_ids = {r["node_id"] for r in report}
        assert "a" in node_ids
        assert "b" in node_ids

    def test_clear_empties_tracker(self):
        from meshflow.observability.sla import NodeLatencyTracker
        t = NodeLatencyTracker()
        t.record("x", 10.0)
        t.clear()
        assert t.summary("x") is None
        assert t.report() == []

    def test_to_dict_has_required_fields(self):
        from meshflow.observability.sla import NodeLatencyTracker
        t = NodeLatencyTracker()
        for v in [1.0, 2.0, 3.0]:
            t.record("n", v)
        d = t.summary("n").to_dict()
        for key in ("node_id", "count", "p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms", "mean_ms"):
            assert key in d

    def test_max_samples_evicts_oldest(self):
        from meshflow.observability.sla import NodeLatencyTracker
        t = NodeLatencyTracker(max_samples=5)
        for i in range(10):
            t.record("n", float(i))
        s = t.summary("n")
        assert s.count == 5

    def test_sla_recorded_by_step_runtime(self):
        from meshflow.observability.sla import NodeLatencyTracker
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import Policy
        from meshflow.core.node import NodeInput, NodeOutput
        from meshflow.core.node import MeshNode, NodeKind
        import meshflow.observability.sla as sla_mod

        # Use a fresh tracker for this test
        fresh = NodeLatencyTracker()
        old = sla_mod._global_sla_tracker
        sla_mod._global_sla_tracker = fresh

        async def _fn(inp: Any) -> NodeOutput:
            return NodeOutput(content="sla_test")

        async def _run():
            node = MeshNode(id="sla_node", kind=NodeKind.NATIVE, _runner=_fn)
            policy = Policy(mode="dev", budget_usd=1.0, budget_tokens=1000)
            rt = StepRuntime(policy=policy, run_id="run-sla-1")
            await rt.run(node, NodeInput(task="test"), {})

        asyncio.run(_run())
        s = fresh.summary("sla_node")
        assert s is not None
        assert s.count == 1

        sla_mod._global_sla_tracker = old  # restore


# ─────────────────────────────────────────────────────────────────────────────
# F. RateLimiter
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimiter:
    def test_allows_within_capacity(self):
        from meshflow.observability.sla import RateLimiter
        rl = RateLimiter(rate=10.0, capacity=5.0)
        for _ in range(5):
            assert rl.allow("k") is True

    def test_blocks_when_exhausted(self):
        from meshflow.observability.sla import RateLimiter
        rl = RateLimiter(rate=1.0, capacity=2.0)
        assert rl.allow("k") is True
        assert rl.allow("k") is True
        assert rl.allow("k") is False  # exhausted

    def test_keys_are_isolated(self):
        from meshflow.observability.sla import RateLimiter
        rl = RateLimiter(rate=1.0, capacity=1.0)
        assert rl.allow("alice") is True
        assert rl.allow("alice") is False
        assert rl.allow("bob") is True   # bob has a separate bucket

    def test_tokens_refill_over_time(self):
        from meshflow.observability.sla import RateLimiter
        rl = RateLimiter(rate=100.0, capacity=1.0)
        rl.allow("k")           # exhaust
        time.sleep(0.02)        # wait 20ms → 2 tokens at 100/s
        assert rl.allow("k") is True

    def test_status_reports_remaining_tokens(self):
        from meshflow.observability.sla import RateLimiter
        rl = RateLimiter(rate=10.0, capacity=10.0)
        rl.allow("k")
        st = rl.status("k")
        assert st["tokens_remaining"] < 10.0
        assert st["capacity"] == 10.0

    def test_set_limits_updates_bucket(self):
        from meshflow.observability.sla import RateLimiter
        rl = RateLimiter(rate=1.0, capacity=1.0)
        rl.set_limits("k", rate=100.0, capacity=50.0)
        st = rl.status("k")
        assert st["capacity"] == 50.0

    def test_stats_returns_all_keys(self):
        from meshflow.observability.sla import RateLimiter
        rl = RateLimiter(rate=10.0, capacity=10.0)
        rl.allow("x")
        rl.allow("y")
        keys = {b["key"] for b in rl.stats()}
        assert "x" in keys and "y" in keys


# ─────────────────────────────────────────────────────────────────────────────
# G. Server routes
# ─────────────────────────────────────────────────────────────────────────────

class TestSprint17ServerRoutes:
    async def test_all_new_routes_registered(self):
        from meshflow.runtime.server import _build_app
        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        routes = {r.resource.canonical for r in app.router.routes()}
        for path in ("/otel/config", "/graph/{run_id}", "/audit/export", "/sla", "/rate-limit/status"):
            assert path in routes, f"Missing route: {path}"

    async def test_otel_config_returns_json(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer
        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/otel/config")
            assert resp.status == 200
            data = await resp.json()
            assert "otlp_enabled" in data
            assert "w3c_traceparent" in data
            assert data["w3c_traceparent"] is True

    async def test_graph_run_not_found_returns_mermaid_empty(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer
        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/graph/nonexistent-run-id")
            assert resp.status == 200
            text = await resp.text()
            assert "flowchart" in text

    async def test_audit_export_json_all_runs(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer
        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/audit/export")
            assert resp.status == 200
            data = await resp.json()
            assert "runs" in data

    async def test_sla_returns_list(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer
        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/sla")
            assert resp.status == 200
            data = await resp.json()
            assert "sla" in data

    async def test_rate_limit_status_returns_buckets(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer
        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/rate-limit/status")
            assert resp.status == 200
            data = await resp.json()
            assert "buckets" in data

    async def test_graph_dot_format(self):
        from meshflow.runtime.server import _build_app
        from aiohttp.test_utils import TestClient, TestServer
        app = await _build_app(api_keys=set(), ledger_path=":memory:")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/graph/any-run?format=dot")
            assert resp.status == 200
            text = await resp.text()
            assert "digraph" in text


# ─────────────────────────────────────────────────────────────────────────────
# H. CLI graph and audit subcommands
# ─────────────────────────────────────────────────────────────────────────────

class TestSprint17CLI:
    def _run(self, *argv: str) -> tuple[int, str]:
        import argparse
        import io
        from meshflow.cli.main import _cmd_graph, _cmd_audit

        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        code = 0
        try:
            if argv[0] == "graph":
                ns = argparse.Namespace(
                    run_id=_flag(argv, "--run-id", ""),
                    format=_flag(argv, "--format", "mermaid"),
                    db=_flag(argv, "--db", ":memory:"),
                    out=_flag(argv, "--out", ""),
                )
                _cmd_graph(ns)
            elif argv[0] == "audit":
                ns = argparse.Namespace(
                    audit_cmd="export",
                    run_id=_flag(argv, "--run-id", ""),
                    format=_flag(argv, "--format", "json"),
                    db=_flag(argv, "--db", ":memory:"),
                    out=_flag(argv, "--out", ""),
                )
                _cmd_audit(ns)
        except SystemExit as e:
            code = int(str(e))
        finally:
            sys.stdout = old
        return code, buf.getvalue()

    def test_graph_no_run_id_lists_runs(self):
        code, out = self._run("graph", "--db", ":memory:")
        assert code == 0
        assert "No runs" in out or "Available run IDs" in out or "run" in out.lower()

    def test_graph_invalid_run_id_exits_1(self):
        code, out = self._run("graph", "--run-id", "nonexistent", "--db", ":memory:")
        assert code == 1

    def test_audit_export_no_run_id_returns_json(self):
        code, out = self._run("audit", "--db", ":memory:")
        assert code == 0
        data = json.loads(out.strip())
        assert "runs" in data

    def test_audit_export_csv_for_real_run(self):
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import Policy
        from meshflow.core.node import NodeInput, NodeOutput
        from meshflow.core.node import MeshNode, NodeKind

        async def _fn(inp: Any) -> NodeOutput:
            return NodeOutput(content="cli_audit_test")

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            async def _setup():
                ledger = ReplayLedger(db_path)
                node = MeshNode(id="audit_node", kind=NodeKind.NATIVE, _runner=_fn)
                policy = Policy(mode="dev", budget_usd=1.0, budget_tokens=1000)
                rt = StepRuntime(policy=policy, run_id="run-audit-cli", ledger=ledger)
                await rt.run(node, NodeInput(task="test"), {})
            asyncio.run(_setup())

            code, out = self._run("audit", "--run-id", "run-audit-cli",
                                  "--format", "csv", "--db", db_path)
            assert code == 0
            assert "node_id" in out  # CSV header
            assert "audit_node" in out
        finally:
            os.unlink(db_path)


def _flag(argv: tuple, flag: str, default: str) -> str:
    args = list(argv)
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default
