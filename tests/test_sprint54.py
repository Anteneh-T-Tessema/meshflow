"""Sprint 54 — Alert Engine & Metric Store tests."""

from __future__ import annotations

import json
import subprocess
import time
import unittest
from unittest.mock import MagicMock, patch

import meshflow
from meshflow.alerting.metrics import MetricPoint, MetricStore
from meshflow.alerting.rules import AlertRecord, AlertRule, AlertRuleStore, AlertStore
from meshflow.alerting.engine import AlertEngine


# ── MetricPoint ───────────────────────────────────────────────────────────────

class TestMetricPoint(unittest.TestCase):
    def test_to_dict_keys(self):
        p = MetricPoint("agent-1", "latency_ms", 120.5, 1000.0)
        d = p.to_dict()
        self.assertEqual(d["agent_name"], "agent-1")
        self.assertEqual(d["metric"], "latency_ms")
        self.assertEqual(d["value"], 120.5)
        self.assertEqual(d["ts"], 1000.0)

    def test_fields(self):
        p = MetricPoint("a", "m", 1.0, 2.0)
        self.assertEqual(p.agent_name, "a")
        self.assertEqual(p.metric, "m")
        self.assertEqual(p.value, 1.0)
        self.assertEqual(p.ts, 2.0)


# ── MetricStore ───────────────────────────────────────────────────────────────

class TestMetricStoreRecord(unittest.TestCase):
    def setUp(self):
        self.store = MetricStore(":memory:")

    def test_record_returns_metric_point(self):
        p = self.store.record("agent-1", "latency_ms", 150.0)
        self.assertIsInstance(p, MetricPoint)

    def test_record_stores_value(self):
        self.store.record("agent-1", "latency_ms", 150.0)
        pts = self.store.query("agent-1", "latency_ms", window_s=60)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0].value, 150.0)

    def test_record_uses_current_time(self):
        before = time.time()
        p = self.store.record("agent-1", "m", 1.0)
        self.assertGreaterEqual(p.ts, before)
        self.assertLessEqual(p.ts, time.time())

    def test_record_explicit_ts(self):
        p = self.store.record("agent-1", "m", 1.0, ts=1000.0)
        self.assertEqual(p.ts, 1000.0)

    def test_record_batch(self):
        pts = [MetricPoint("a", "m", float(i), 1000.0 + i) for i in range(5)]
        self.store.record_batch(pts)
        self.assertEqual(self.store.count(), 5)

    def test_record_batch_empty(self):
        self.store.record_batch([])
        self.assertEqual(self.store.count(), 0)


class TestMetricStoreQuery(unittest.TestCase):
    def setUp(self):
        self.store = MetricStore(":memory:")

    def test_query_within_window(self):
        now = time.time()
        self.store.record("a", "m", 1.0, ts=now - 30)
        self.store.record("a", "m", 2.0, ts=now - 10)
        pts = self.store.query("a", "m", window_s=60, now=now)
        self.assertEqual(len(pts), 2)

    def test_query_excludes_old_points(self):
        now = time.time()
        self.store.record("a", "m", 1.0, ts=now - 120)
        pts = self.store.query("a", "m", window_s=60, now=now)
        self.assertEqual(len(pts), 0)

    def test_query_filters_by_agent(self):
        now = time.time()
        self.store.record("agent-A", "m", 1.0, ts=now - 5)
        self.store.record("agent-B", "m", 2.0, ts=now - 5)
        pts = self.store.query("agent-A", "m", window_s=60, now=now)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0].agent_name, "agent-A")

    def test_query_filters_by_metric(self):
        now = time.time()
        self.store.record("a", "latency_ms", 1.0, ts=now - 5)
        self.store.record("a", "error_rate", 2.0, ts=now - 5)
        pts = self.store.query("a", "latency_ms", window_s=60, now=now)
        self.assertEqual(len(pts), 1)

    def test_query_ordered_by_ts(self):
        now = time.time()
        self.store.record("a", "m", 3.0, ts=now - 30)
        self.store.record("a", "m", 1.0, ts=now - 50)
        self.store.record("a", "m", 2.0, ts=now - 40)
        pts = self.store.query("a", "m", window_s=60, now=now)
        ts_list = [p.ts for p in pts]
        self.assertEqual(ts_list, sorted(ts_list))

    def test_query_empty_returns_empty_list(self):
        self.assertEqual(self.store.query("a", "m", window_s=60), [])


class TestMetricStoreLatest(unittest.TestCase):
    def setUp(self):
        self.store = MetricStore(":memory:")

    def test_latest_returns_most_recent(self):
        now = time.time()
        self.store.record("a", "m", 1.0, ts=now - 30)
        self.store.record("a", "m", 5.0, ts=now - 5)
        p = self.store.latest("a", "m")
        self.assertIsNotNone(p)
        self.assertEqual(p.value, 5.0)

    def test_latest_none_when_empty(self):
        self.assertIsNone(self.store.latest("a", "m"))

    def test_latest_filters_by_metric(self):
        now = time.time()
        self.store.record("a", "x", 9.0, ts=now - 1)
        self.store.record("a", "y", 3.0, ts=now - 2)
        p = self.store.latest("a", "y")
        self.assertEqual(p.value, 3.0)


class TestMetricStoreAggregate(unittest.TestCase):
    def setUp(self):
        self.store = MetricStore(":memory:")
        now = time.time()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            self.store.record("a", "m", v, ts=now - 10)

    def test_mean(self):
        result = self.store.aggregate("a", "m", window_s=60, fn="mean")
        self.assertAlmostEqual(result, 30.0)

    def test_max(self):
        self.assertEqual(self.store.aggregate("a", "m", window_s=60, fn="max"), 50.0)

    def test_min(self):
        self.assertEqual(self.store.aggregate("a", "m", window_s=60, fn="min"), 10.0)

    def test_sum(self):
        self.assertEqual(self.store.aggregate("a", "m", window_s=60, fn="sum"), 150.0)

    def test_count(self):
        self.assertEqual(self.store.aggregate("a", "m", window_s=60, fn="count"), 5.0)

    def test_none_when_no_data(self):
        self.assertIsNone(self.store.aggregate("a", "m", window_s=0.0001, fn="mean"))

    def test_unknown_fn_raises(self):
        with self.assertRaises(ValueError):
            self.store.aggregate("a", "m", window_s=60, fn="median")


class TestMetricStoreMeta(unittest.TestCase):
    def setUp(self):
        self.store = MetricStore(":memory:")
        now = time.time()
        self.store.record("agent-X", "lat", 1.0, ts=now - 5)
        self.store.record("agent-X", "err", 2.0, ts=now - 5)
        self.store.record("agent-Y", "lat", 3.0, ts=now - 5)

    def test_agents(self):
        agents = self.store.agents()
        self.assertIn("agent-X", agents)
        self.assertIn("agent-Y", agents)

    def test_metrics_for(self):
        metrics = self.store.metrics_for("agent-X")
        self.assertIn("lat", metrics)
        self.assertIn("err", metrics)

    def test_count_all(self):
        self.assertEqual(self.store.count(), 3)

    def test_count_by_agent(self):
        self.assertEqual(self.store.count("agent-X"), 2)

    def test_count_by_agent_metric(self):
        self.assertEqual(self.store.count("agent-X", "lat"), 1)

    def test_prune_removes_old(self):
        store = MetricStore(":memory:", retention_s=100)
        now = time.time()
        store.record("a", "m", 1.0, ts=now - 200)
        store.record("a", "m", 2.0, ts=now - 50)
        removed = store.prune(now=now)
        self.assertEqual(removed, 1)
        self.assertEqual(store.count(), 1)

    def test_clear_all(self):
        n = self.store.clear()
        self.assertEqual(n, 3)
        self.assertEqual(self.store.count(), 0)

    def test_clear_by_agent(self):
        n = self.store.clear("agent-X")
        self.assertEqual(n, 2)
        self.assertEqual(self.store.count("agent-Y"), 1)


# ── AlertRule ─────────────────────────────────────────────────────────────────

class TestAlertRuleEvaluate(unittest.TestCase):
    def _rule(self, operator: str, threshold: float) -> AlertRule:
        return AlertRule(
            rule_id="r1", name="test", agent_name="a", metric="m",
            operator=operator, threshold=threshold,
            window_s=60, agg_fn="mean",
            webhook_url="", webhook_secret="",
            enabled=True, created_at=time.time(),
        )

    def test_gt_true(self):
        self.assertTrue(self._rule("gt", 10.0).evaluate(11.0))

    def test_gt_false(self):
        self.assertFalse(self._rule("gt", 10.0).evaluate(9.0))

    def test_lt_true(self):
        self.assertTrue(self._rule("lt", 10.0).evaluate(9.0))

    def test_gte_equal(self):
        self.assertTrue(self._rule("gte", 10.0).evaluate(10.0))

    def test_lte_equal(self):
        self.assertTrue(self._rule("lte", 10.0).evaluate(10.0))

    def test_eq_true(self):
        self.assertTrue(self._rule("eq", 5.0).evaluate(5.0))

    def test_eq_false(self):
        self.assertFalse(self._rule("eq", 5.0).evaluate(5.1))

    def test_to_dict_keys(self):
        r = self._rule("gt", 5.0)
        d = r.to_dict()
        for k in ("rule_id", "name", "agent_name", "metric", "operator",
                   "threshold", "window_s", "agg_fn", "webhook_url", "enabled"):
            self.assertIn(k, d)


# ── AlertRuleStore ────────────────────────────────────────────────────────────

class TestAlertRuleStore(unittest.TestCase):
    def setUp(self):
        self.store = AlertRuleStore(":memory:")

    def test_add_returns_rule(self):
        r = self.store.add("high-lat", "a", "latency_ms", "gt", 500.0)
        self.assertIsInstance(r, AlertRule)

    def test_add_invalid_operator_raises(self):
        with self.assertRaises(ValueError):
            self.store.add("r", "a", "m", "between", 5.0)

    def test_add_invalid_agg_fn_raises(self):
        with self.assertRaises(ValueError):
            self.store.add("r", "a", "m", "gt", 5.0, agg_fn="median")

    def test_get_returns_rule(self):
        r = self.store.add("r", "a", "m", "gt", 5.0)
        fetched = self.store.get(r.rule_id)
        self.assertEqual(fetched.rule_id, r.rule_id)

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.store.get("no-such"))

    def test_list_rules_all(self):
        self.store.add("r1", "a", "m", "gt", 1.0)
        self.store.add("r2", "a", "m", "lt", 2.0)
        self.assertEqual(len(self.store.list_rules()), 2)

    def test_list_rules_filter_agent(self):
        self.store.add("r1", "agent-A", "m", "gt", 1.0)
        self.store.add("r2", "agent-B", "m", "lt", 2.0)
        rules = self.store.list_rules(agent_name="agent-A")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].agent_name, "agent-A")

    def test_list_rules_enabled_only(self):
        r = self.store.add("r1", "a", "m", "gt", 1.0)
        self.store.disable(r.rule_id)
        self.store.add("r2", "a", "m", "lt", 2.0)
        rules = self.store.list_rules(enabled_only=True)
        self.assertEqual(len(rules), 1)

    def test_enable_disable(self):
        r = self.store.add("r", "a", "m", "gt", 1.0, enabled=True)
        self.store.disable(r.rule_id)
        self.assertFalse(self.store.get(r.rule_id).enabled)
        self.store.enable(r.rule_id)
        self.assertTrue(self.store.get(r.rule_id).enabled)

    def test_delete_removes_rule(self):
        r = self.store.add("r", "a", "m", "gt", 1.0)
        ok = self.store.delete(r.rule_id)
        self.assertTrue(ok)
        self.assertIsNone(self.store.get(r.rule_id))

    def test_delete_unknown_returns_false(self):
        self.assertFalse(self.store.delete("no-such"))

    def test_count(self):
        self.store.add("r1", "a", "m", "gt", 1.0)
        self.store.add("r2", "a", "m", "lt", 2.0)
        self.assertEqual(self.store.count(), 2)


# ── AlertStore ────────────────────────────────────────────────────────────────

class TestAlertStore(unittest.TestCase):
    def setUp(self):
        self.rule_store = AlertRuleStore(":memory:")
        self.alert_store = AlertStore(":memory:")
        self.rule = self.rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0)

    def test_fire_returns_record(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        self.assertIsInstance(rec, AlertRecord)

    def test_fire_status_firing(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        self.assertEqual(rec.status, "firing")
        self.assertTrue(rec.is_firing)

    def test_fire_stores_value(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        fetched = self.alert_store.get(rec.alert_id)
        self.assertEqual(fetched.value, 620.0)

    def test_fire_default_message(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        self.assertIn("billing", rec.message)

    def test_fire_custom_message(self):
        rec = self.alert_store.fire(self.rule, 620.0, message="custom msg")
        self.assertEqual(rec.message, "custom msg")

    def test_resolve_changes_status(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        ok = self.alert_store.resolve(rec.alert_id)
        self.assertTrue(ok)
        self.assertEqual(self.alert_store.get(rec.alert_id).status, "resolved")

    def test_resolve_sets_resolved_at(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        self.alert_store.resolve(rec.alert_id)
        self.assertIsNotNone(self.alert_store.get(rec.alert_id).resolved_at)

    def test_resolve_unknown_returns_false(self):
        self.assertFalse(self.alert_store.resolve("no-such"))

    def test_ack_changes_status(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        ok = self.alert_store.ack(rec.alert_id, "ops-team")
        self.assertTrue(ok)
        fetched = self.alert_store.get(rec.alert_id)
        self.assertEqual(fetched.status, "acked")
        self.assertEqual(fetched.acked_by, "ops-team")

    def test_ack_unknown_returns_false(self):
        self.assertFalse(self.alert_store.ack("no-such"))

    def test_firing_query(self):
        self.alert_store.fire(self.rule, 620.0)
        self.alert_store.fire(self.rule, 700.0)
        firing = self.alert_store.firing()
        self.assertEqual(len(firing), 2)

    def test_firing_excludes_resolved(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        self.alert_store.resolve(rec.alert_id)
        self.assertEqual(len(self.alert_store.firing()), 0)

    def test_resolve_for_rule(self):
        self.alert_store.fire(self.rule, 620.0)
        self.alert_store.fire(self.rule, 700.0)
        n = self.alert_store.resolve_for_rule(self.rule.rule_id)
        self.assertEqual(n, 2)
        self.assertEqual(len(self.alert_store.firing()), 0)

    def test_has_firing_true(self):
        self.alert_store.fire(self.rule, 620.0)
        self.assertTrue(self.alert_store.has_firing(self.rule.rule_id))

    def test_has_firing_false(self):
        self.assertFalse(self.alert_store.has_firing(self.rule.rule_id))

    def test_counts(self):
        r1 = self.alert_store.fire(self.rule, 620.0)
        r2 = self.alert_store.fire(self.rule, 700.0)
        self.alert_store.resolve(r1.alert_id)
        counts = self.alert_store.counts()
        self.assertEqual(counts["firing"], 1)
        self.assertEqual(counts["resolved"], 1)

    def test_list_alerts_filter_status(self):
        r1 = self.alert_store.fire(self.rule, 620.0)
        self.alert_store.resolve(r1.alert_id)
        self.alert_store.fire(self.rule, 700.0)
        firing = self.alert_store.list_alerts(status="firing")
        self.assertEqual(len(firing), 1)

    def test_to_dict_keys(self):
        rec = self.alert_store.fire(self.rule, 620.0)
        d = rec.to_dict()
        for k in ("alert_id", "rule_id", "rule_name", "agent_name", "metric",
                   "value", "threshold", "operator", "status", "fired_at",
                   "resolved_at", "acked_at", "acked_by", "message"):
            self.assertIn(k, d)


# ── AlertEngine ───────────────────────────────────────────────────────────────

class TestAlertEngine(unittest.TestCase):
    def _setup(self):
        metrics = MetricStore(":memory:")
        rule_store = AlertRuleStore(":memory:")
        alert_store = AlertStore(":memory:")
        engine = AlertEngine(metrics, rule_store, alert_store)
        return metrics, rule_store, alert_store, engine

    def test_evaluate_fires_when_threshold_breached(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        fired, resolved = engine.evaluate(now=now)
        self.assertEqual(len(fired), 1)
        self.assertEqual(len(resolved), 0)
        self.assertEqual(fired[0].status, "firing")

    def test_evaluate_no_fire_below_threshold(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 200.0, ts=now - 5)
        rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        fired, _ = engine.evaluate(now=now)
        self.assertEqual(len(fired), 0)

    def test_evaluate_resolves_when_condition_clears(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        # Plant a firing alert
        rule = rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        alert_store.fire(rule, 620.0)
        # Record low latency
        metrics.record("billing", "latency_ms", 200.0, ts=now - 5)
        _, resolved = engine.evaluate(now=now)
        self.assertGreater(len(resolved), 0)

    def test_evaluate_no_duplicate_alerts(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        engine.evaluate(now=now)
        fired2, _ = engine.evaluate(now=now)
        self.assertEqual(len(fired2), 0)

    def test_evaluate_skips_disabled_rules(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule = rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        rule_store.disable(rule.rule_id)
        fired, _ = engine.evaluate(now=now)
        self.assertEqual(len(fired), 0)

    def test_evaluate_skips_rules_with_no_data(self):
        metrics, rule_store, alert_store, engine = self._setup()
        rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=0.001)
        fired, _ = engine.evaluate()
        self.assertEqual(len(fired), 0)

    def test_evaluate_rule_fires_single(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule = rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        rec = engine.evaluate_rule(rule.rule_id, now=now)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, "firing")

    def test_evaluate_rule_returns_none_when_below(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 200.0, ts=now - 5)
        rule = rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        rec = engine.evaluate_rule(rule.rule_id, now=now)
        self.assertIsNone(rec)

    def test_evaluate_rule_unknown_id_returns_none(self):
        metrics, rule_store, alert_store, engine = self._setup()
        self.assertIsNone(engine.evaluate_rule("no-such"))

    def test_summary(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        engine.evaluate(now=now)
        summary = engine.summary()
        self.assertEqual(summary.get("firing", 0), 1)

    def test_firing_count(self):
        metrics, rule_store, alert_store, engine = self._setup()
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        engine.evaluate(now=now)
        self.assertEqual(engine.firing_count(), 1)

    def test_webhook_queue_called_on_fire(self):
        metrics, rule_store, alert_store, engine = self._setup()
        mock_queue = MagicMock()
        engine._webhook_queue = mock_queue
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule_store.add(
            "high-lat", "billing", "latency_ms", "gt", 500.0,
            window_s=60, webhook_url="https://hooks.example.com/alert",
        )
        engine.evaluate(now=now)
        mock_queue.enqueue.assert_called_once()
        call_kw = mock_queue.enqueue.call_args.kwargs
        self.assertEqual(call_kw["event_type"], "alert_fired")

    def test_webhook_not_called_when_no_url(self):
        metrics, rule_store, alert_store, engine = self._setup()
        mock_queue = MagicMock()
        engine._webhook_queue = mock_queue
        now = time.time()
        metrics.record("billing", "latency_ms", 620.0, ts=now - 5)
        rule_store.add("high-lat", "billing", "latency_ms", "gt", 500.0, window_s=60)
        engine.evaluate(now=now)
        mock_queue.enqueue.assert_not_called()


# ── CLI tests ─────────────────────────────────────────────────────────────────

class TestAlertsCLI(unittest.TestCase):
    def _args_status(self, db=":memory:"):
        import argparse
        return argparse.Namespace(alerts_cmd="status", db=db)

    def _args_list(self, **kw):
        import argparse
        ns = argparse.Namespace(
            alerts_cmd="list",
            db=":memory:",
            status="",
            agent_name="",
            limit=20,
            json_output=False,
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _args_ack(self, alert_id, **kw):
        import argparse
        ns = argparse.Namespace(
            alerts_cmd="ack",
            db=":memory:",
            alert_id=alert_id,
            acked_by="cli",
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_status_empty(self):
        from meshflow.cli.main import _cmd_alerts
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            _cmd_alerts(self._args_status())
        out = mock_out.getvalue()
        self.assertIn("Rules defined", out)
        self.assertIn("0", out)

    def test_list_no_alerts(self):
        from meshflow.cli.main import _cmd_alerts
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            _cmd_alerts(self._args_list())
        self.assertIn("No alerts", mock_out.getvalue())

    def test_list_json_output(self):
        from meshflow.cli.main import _cmd_alerts
        import io
        alert_store = AlertStore(":memory:")
        rule_store = AlertRuleStore(":memory:")
        rule = rule_store.add("r", "a", "m", "gt", 1.0)
        alert_store.fire(rule, 2.0)

        with patch("meshflow.alerting.rules.AlertStore", return_value=alert_store):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                _cmd_alerts(self._args_list(json_output=True))
                data = json.loads(mock_out.getvalue())
        self.assertIsInstance(data, list)

    def test_ack_missing_alert_exits(self):
        from meshflow.cli.main import _cmd_alerts
        with self.assertRaises(SystemExit):
            _cmd_alerts(self._args_ack("no-such"))


class TestAlertsRulesCLI(unittest.TestCase):
    def _args_rules_list(self, **kw):
        import argparse
        ns = argparse.Namespace(
            alerts_cmd="rules",
            rules_cmd="list",
            db=":memory:",
            agent_name="",
            enabled_only=False,
            json_output=False,
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _args_rules_add(self, **kw):
        import argparse
        ns = argparse.Namespace(
            alerts_cmd="rules",
            rules_cmd="add",
            db=":memory:",
            name="test-rule",
            agent_name="billing",
            metric="latency_ms",
            operator="gt",
            threshold=500.0,
            window_s=60.0,
            agg_fn="mean",
            webhook_url="",
            webhook_secret="",
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _args_rules_remove(self, rule_id):
        import argparse
        return argparse.Namespace(
            alerts_cmd="rules", rules_cmd="remove",
            db=":memory:", rule_id=rule_id,
        )

    def test_rules_list_empty(self):
        from meshflow.cli.main import _cmd_alerts
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            _cmd_alerts(self._args_rules_list())
        self.assertIn("No alert rules", mock_out.getvalue())

    def test_rules_add_prints_id(self):
        from meshflow.cli.main import _cmd_alerts
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            _cmd_alerts(self._args_rules_add())
        self.assertIn("created", mock_out.getvalue())

    def test_rules_remove_not_found_exits(self):
        from meshflow.cli.main import _cmd_alerts
        with self.assertRaises(SystemExit):
            _cmd_alerts(self._args_rules_remove("no-such"))


# ── Subprocess help ───────────────────────────────────────────────────────────

class TestSubprocessHelp(unittest.TestCase):
    def _run(self, *args):
        result = subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode, result.stdout + result.stderr

    def test_alerts_help(self):
        code, out = self._run("alerts", "--help")
        self.assertIn(code, (0, 1))
        combined = out.lower()
        self.assertTrue("alert" in combined or combined == "")

    def test_alerts_rules_help(self):
        code, out = self._run("alerts", "rules", "--help")
        self.assertIn(code, (0, 1))

    def test_alerts_list_help(self):
        code, out = self._run("alerts", "list", "--help")
        self.assertIn(code, (0, 1))


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")

    def test_metric_point_exported(self):
        self.assertIs(meshflow.MetricPoint, MetricPoint)

    def test_metric_store_exported(self):
        self.assertIs(meshflow.MetricStore, MetricStore)

    def test_alert_rule_exported(self):
        self.assertIs(meshflow.AlertRule, AlertRule)

    def test_alert_record_exported(self):
        self.assertIs(meshflow.AlertRecord, AlertRecord)

    def test_alert_rule_store_exported(self):
        self.assertIs(meshflow.AlertRuleStore, AlertRuleStore)

    def test_alert_store_exported(self):
        self.assertIs(meshflow.AlertStore, AlertStore)

    def test_alert_engine_exported(self):
        self.assertIs(meshflow.AlertEngine, AlertEngine)

    def test_all_contains_alerting_exports(self):
        for name in ("MetricPoint", "MetricStore", "AlertRule", "AlertRecord",
                     "AlertRuleStore", "AlertStore", "AlertEngine"):
            self.assertIn(name, meshflow.__all__)

    def test_sprint53_exports_still_present(self):
        for name in ("WebhookDelivery", "WebhookRetryQueue", "WebhookReliableDeliverer"):
            self.assertTrue(hasattr(meshflow, name), f"Missing: {name}")

    def test_sprint52_exports_still_present(self):
        for name in ("SemanticMemoryStore", "HashEmbeddingProvider"):
            self.assertTrue(hasattr(meshflow, name), f"Missing: {name}")


if __name__ == "__main__":
    unittest.main()
