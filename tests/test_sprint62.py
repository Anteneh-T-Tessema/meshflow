"""Sprint 62 — Distributed Tracing tests."""
import subprocess
import time
import unittest

import meshflow
from meshflow.tracing.context import (
    Span, SpanKind, SpanStatus, TraceContext, TraceStore, Tracer,
)


# ── TraceContext ──────────────────────────────────────────────────────────────

class TestTraceContext(unittest.TestCase):

    def test_new_root_has_unique_ids(self):
        ctx1 = TraceContext.new_root()
        ctx2 = TraceContext.new_root()
        self.assertNotEqual(ctx1.trace_id, ctx2.trace_id)
        self.assertNotEqual(ctx1.span_id, ctx2.span_id)

    def test_child_inherits_trace_id(self):
        root = TraceContext.new_root()
        child = TraceContext.child(root)
        self.assertEqual(child.trace_id, root.trace_id)
        self.assertNotEqual(child.span_id, root.span_id)

    def test_traceparent_format(self):
        ctx = TraceContext(trace_id="a" * 32, span_id="b" * 16, sampled=True)
        tp = ctx.traceparent()
        self.assertTrue(tp.startswith("00-"))
        parts = tp.split("-")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[3], "01")

    def test_traceparent_not_sampled(self):
        ctx = TraceContext(trace_id="a" * 32, span_id="b" * 16, sampled=False)
        self.assertIn("-00", ctx.traceparent())

    def test_from_traceparent_roundtrip(self):
        ctx = TraceContext.new_root()
        tp = ctx.traceparent()
        restored = TraceContext.from_traceparent(tp)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.trace_id, ctx.trace_id)
        self.assertEqual(restored.span_id, ctx.span_id)

    def test_from_traceparent_invalid_returns_none(self):
        self.assertIsNone(TraceContext.from_traceparent("bad-format"))

    def test_sampled_default_true(self):
        ctx = TraceContext.new_root()
        self.assertTrue(ctx.sampled)

    def test_immutable(self):
        ctx = TraceContext.new_root()
        with self.assertRaises(Exception):
            ctx.trace_id = "new"


# ── Span ──────────────────────────────────────────────────────────────────────

class TestSpan(unittest.TestCase):

    def _span(self, name="test-span", kind=SpanKind.INTERNAL):
        return Span(
            span_id="abc123",
            trace_id="trace001",
            name=name,
            kind=kind,
            start_ts=time.time(),
        )

    def test_duration_none_before_finish(self):
        span = self._span()
        self.assertIsNone(span.duration_ms)

    def test_not_finished_before_finish(self):
        self.assertFalse(self._span().is_finished)

    def test_finish_sets_end_ts(self):
        span = self._span()
        span.finish()
        self.assertIsNotNone(span.end_ts)

    def test_finish_calculates_duration(self):
        span = self._span()
        time.sleep(0.01)
        span.finish()
        self.assertGreater(span.duration_ms, 0)

    def test_finish_with_error(self):
        span = self._span()
        span.finish(status=SpanStatus.ERROR, error="something failed")
        self.assertEqual(span.status, SpanStatus.ERROR)
        self.assertEqual(span.error, "something failed")

    def test_to_dict_has_all_fields(self):
        span = self._span()
        span.finish()
        d = span.to_dict()
        for key in ("span_id", "trace_id", "parent_id", "name", "kind",
                    "status", "start_ts", "end_ts", "duration_ms"):
            self.assertIn(key, d)

    def test_span_kind_values(self):
        for kind in SpanKind:
            self.assertIsInstance(kind.value, str)


# ── TraceStore ────────────────────────────────────────────────────────────────

class TestTraceStore(unittest.TestCase):

    def setUp(self):
        self.store = TraceStore(":memory:")

    def _make_span(self, trace_id="trace-1", name="span", run_id=""):
        return Span(
            span_id=f"span-{time.time_ns()}",
            trace_id=trace_id,
            name=name,
            kind=SpanKind.INTERNAL,
            start_ts=time.time(),
            run_id=run_id,
        )

    def test_save_and_get(self):
        span = self._make_span()
        span.finish()
        self.store.save(span)
        fetched = self.store.get(span.span_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, span.name)

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.store.get("no-such-span"))

    def test_get_trace(self):
        for i in range(3):
            s = self._make_span(name=f"span-{i}")
            s.finish()
            self.store.save(s)
        spans = self.store.get_trace("trace-1")
        self.assertEqual(len(spans), 3)

    def test_get_trace_empty(self):
        self.assertEqual(self.store.get_trace("unknown-trace"), [])

    def test_get_for_run(self):
        for i in range(2):
            s = self._make_span(run_id="run-x", name=f"s{i}")
            s.finish()
            self.store.save(s)
        spans = self.store.get_for_run("run-x")
        self.assertEqual(len(spans), 2)

    def test_count_all(self):
        self.assertEqual(self.store.count(), 0)
        s = self._make_span(); s.finish(); self.store.save(s)
        self.assertEqual(self.store.count(), 1)

    def test_count_by_trace(self):
        s1 = self._make_span(trace_id="t1"); s1.finish(); self.store.save(s1)
        s2 = self._make_span(trace_id="t2"); s2.finish(); self.store.save(s2)
        self.assertEqual(self.store.count(trace_id="t1"), 1)

    def test_save_updates_existing(self):
        span = self._make_span()
        self.store.save(span)
        span.finish()
        self.store.save(span)  # update with end_ts
        fetched = self.store.get(span.span_id)
        self.assertIsNotNone(fetched.end_ts)


# ── Tracer ────────────────────────────────────────────────────────────────────

class TestTracer(unittest.TestCase):

    def setUp(self):
        self.store = TraceStore(":memory:")
        self.tracer = Tracer(self.store)

    def test_start_root_span(self):
        span, ctx = self.tracer.start_span("root", kind=SpanKind.ROOT)
        self.assertIsNotNone(span.span_id)
        self.assertIsNone(span.parent_id)
        self.assertEqual(span.kind, SpanKind.ROOT)

    def test_start_child_span(self):
        _, parent_ctx = self.tracer.start_span("root")
        child_span, child_ctx = self.tracer.start_span("child", parent=parent_ctx)
        self.assertEqual(child_span.trace_id, parent_ctx.trace_id)
        self.assertEqual(child_span.parent_id, parent_ctx.span_id)

    def test_finish_span(self):
        span, ctx = self.tracer.start_span("op")
        finished = self.tracer.finish_span(span)
        self.assertIsNotNone(finished.end_ts)
        self.assertEqual(finished.status, SpanStatus.OK)

    def test_finish_with_error(self):
        span, _ = self.tracer.start_span("failing-op")
        finished = self.tracer.finish_span(span, status=SpanStatus.ERROR, error="boom")
        self.assertEqual(finished.status, SpanStatus.ERROR)

    def test_get_trace_returns_all_spans(self):
        _, ctx = self.tracer.start_span("root")
        self.tracer.start_span("child", parent=ctx)
        spans = self.tracer.get_trace(ctx.trace_id)
        self.assertEqual(len(spans), 2)

    def test_span_with_attributes(self):
        span, _ = self.tracer.start_span("annotated", attributes={"user": "alice", "env": "prod"})
        fetched = self.store.get(span.span_id)
        self.assertEqual(fetched.attributes.get("user"), "alice")

    def test_span_with_agent_name(self):
        span, _ = self.tracer.start_span("agent-op", agent_name="MyAgent")
        fetched = self.store.get(span.span_id)
        self.assertEqual(fetched.agent_name, "MyAgent")

    def test_span_with_run_id(self):
        span, _ = self.tracer.start_span("op", run_id="run-42")
        fetched = self.store.get(span.span_id)
        self.assertEqual(fetched.run_id, "run-42")


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestTracingCLI(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True,
        )

    def test_tracing_count_cli(self):
        result = self._run("tracing", "count", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)
        self.assertIn("0", result.stdout)

    def test_tracing_show_unknown(self):
        result = self._run("tracing", "show", "nonexistent-trace-id", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No spans", result.stdout)


# ── Public exports ────────────────────────────────────────────────────────────

class TestTracingExports(unittest.TestCase):

    def test_trace_context_exported(self):
        self.assertTrue(hasattr(meshflow, "TraceContext"))

    def test_span_exported(self):
        self.assertTrue(hasattr(meshflow, "Span"))

    def test_span_kind_exported(self):
        self.assertTrue(hasattr(meshflow, "SpanKind"))

    def test_span_status_exported(self):
        self.assertTrue(hasattr(meshflow, "SpanStatus"))

    def test_trace_store_exported(self):
        self.assertTrue(hasattr(meshflow, "TraceStore"))

    def test_tracer_exported(self):
        self.assertTrue(hasattr(meshflow, "Tracer"))

    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
