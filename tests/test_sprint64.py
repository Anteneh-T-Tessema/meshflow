"""Sprint 64 — Agent SLA Tracker tests."""
import subprocess
import time
import unittest

import meshflow
from meshflow.sla.tracker import (
    SLABreach, SLAStore, SLATracker,
    _percentile,
)


# ── _percentile ───────────────────────────────────────────────────────────────

class TestPercentile(unittest.TestCase):

    def test_empty_returns_zero(self):
        self.assertEqual(_percentile([], 50), 0.0)

    def test_single_value(self):
        self.assertEqual(_percentile([42.0], 50), 42.0)
        self.assertEqual(_percentile([42.0], 99), 42.0)

    def test_p50_two_values(self):
        self.assertAlmostEqual(_percentile([0.0, 100.0], 50), 50.0)

    def test_p100_returns_max(self):
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(_percentile(data, 100), 50.0)

    def test_p0_returns_min(self):
        data = [10.0, 20.0, 30.0]
        self.assertEqual(_percentile(data, 0), 10.0)

    def test_sorted_order_irrelevant(self):
        data = [50.0, 10.0, 90.0, 30.0, 70.0]
        self.assertAlmostEqual(_percentile(data, 50), _percentile(sorted(data), 50))

    def test_p95_reasonable(self):
        data = list(range(1, 101))
        p95 = _percentile([float(x) for x in data], 95)
        self.assertGreater(p95, 90)
        self.assertLessEqual(p95, 100)


# ── SLAStore ──────────────────────────────────────────────────────────────────

class TestSLAStore(unittest.TestCase):

    def setUp(self):
        self.store = SLAStore(":memory:")

    def test_define_contract(self):
        c = self.store.define_contract("agent-a", p50_ms=100, p95_ms=300, p99_ms=500)
        self.assertIsNotNone(c.contract_id)
        self.assertEqual(c.agent_name, "agent-a")
        self.assertTrue(c.enabled)

    def test_define_contract_invalid_order_raises(self):
        with self.assertRaises(ValueError):
            self.store.define_contract("x", p50_ms=300, p95_ms=100, p99_ms=500)

    def test_define_contract_invalid_error_rate_raises(self):
        with self.assertRaises(ValueError):
            self.store.define_contract("x", p50_ms=100, p95_ms=200, p99_ms=300, error_rate=1.5)

    def test_get_contract(self):
        self.store.define_contract("agent-b", 100, 200, 300)
        c = self.store.get_contract("agent-b")
        self.assertIsNotNone(c)
        self.assertEqual(c.p50_ms, 100)

    def test_get_contract_unknown_returns_none(self):
        self.assertIsNone(self.store.get_contract("no-such-agent"))

    def test_list_contracts(self):
        self.store.define_contract("a1", 100, 200, 300)
        self.store.define_contract("a2", 150, 250, 350)
        contracts = self.store.list_contracts()
        self.assertEqual(len(contracts), 2)

    def test_record_observation(self):
        obs = self.store.record_observation("agent-c", 120.5)
        self.assertIsNotNone(obs.obs_id)
        self.assertEqual(obs.agent_name, "agent-c")
        self.assertEqual(obs.latency_ms, 120.5)
        self.assertTrue(obs.success)

    def test_record_failed_observation(self):
        obs = self.store.record_observation("agent-c", 200.0, success=False)
        self.assertFalse(obs.success)

    def test_observations_within_window(self):
        now = time.time()
        self.store.record_observation("agent-d", 100, ts=now - 10)
        self.store.record_observation("agent-d", 200, ts=now - 100)
        obs = self.store.observations("agent-d", window_s=60, now=now)
        self.assertEqual(len(obs), 1)

    def test_observations_outside_window_excluded(self):
        now = time.time()
        self.store.record_observation("agent-e", 100, ts=now - 7200)
        obs = self.store.observations("agent-e", window_s=3600, now=now)
        self.assertEqual(len(obs), 0)

    def test_save_and_list_breaches(self):
        breach = SLABreach(
            breach_id="b1",
            contract_id="c1",
            agent_name="agent-f",
            breach_type="p95",
            observed=400.0,
            threshold=300.0,
            ts=time.time(),
        )
        self.store.save_breach(breach)
        breaches = self.store.list_breaches()
        self.assertEqual(len(breaches), 1)
        self.assertEqual(breaches[0].breach_type, "p95")

    def test_list_breaches_filter_by_agent(self):
        for i, agent in enumerate(("agent-1", "agent-2")):
            self.store.save_breach(SLABreach(f"b{i}", "c", agent, "p99", 500, 300, time.time()))
        b1 = self.store.list_breaches(agent_name="agent-1")
        self.assertEqual(len(b1), 1)

    def test_contract_to_dict(self):
        c = self.store.define_contract("agent-g", 100, 200, 300)
        d = c.to_dict()
        for key in ("contract_id", "agent_name", "p50_ms", "p95_ms", "p99_ms", "error_rate", "enabled"):
            self.assertIn(key, d)


# ── SLATracker ────────────────────────────────────────────────────────────────

class TestSLATracker(unittest.TestCase):

    def setUp(self):
        self.store = SLAStore(":memory:")
        self.tracker = SLATracker(self.store)

    def test_record_returns_observation(self):
        obs, breaches = self.tracker.record("agent-x", 150.0)
        self.assertIsNotNone(obs.obs_id)
        self.assertIsInstance(breaches, list)

    def test_stats_no_data(self):
        stats = self.tracker.stats("no-data-agent")
        self.assertEqual(stats.total, 0)
        self.assertEqual(stats.p50_ms, 0.0)

    def test_stats_with_data(self):
        for ms in [100, 200, 300, 150, 250]:
            self.store.record_observation("ag", float(ms))
        stats = self.tracker.stats("ag")
        self.assertEqual(stats.total, 5)
        self.assertGreater(stats.p50_ms, 0)
        self.assertGreaterEqual(stats.p95_ms, stats.p50_ms)
        self.assertGreaterEqual(stats.p99_ms, stats.p95_ms)

    def test_error_rate_calculation(self):
        for _ in range(8):
            self.store.record_observation("err-agent", 100.0, success=True)
        for _ in range(2):
            self.store.record_observation("err-agent", 100.0, success=False)
        stats = self.tracker.stats("err-agent")
        self.assertAlmostEqual(stats.error_rate, 0.2)

    def test_no_breach_before_10_observations(self):
        self.store.define_contract("breach-test", p50_ms=1, p95_ms=2, p99_ms=3)
        for i in range(9):
            self.tracker.record("breach-test", 1000.0)  # way over threshold
        # Still no breach because < 10 observations
        breaches = self.store.list_breaches(agent_name="breach-test")
        self.assertEqual(len(breaches), 0)

    def test_breach_detected_after_10_observations(self):
        self.store.define_contract("breach-agent", p50_ms=50, p95_ms=100, p99_ms=200)
        now = time.time()
        for i in range(11):
            self.store.record_observation("breach-agent", 500.0, ts=now - i)
        breaches = self.tracker._check_breaches("breach-agent", now=now)
        self.assertGreater(len(breaches), 0)

    def test_no_breach_when_within_sla(self):
        self.store.define_contract("happy-agent", p50_ms=500, p95_ms=1000, p99_ms=2000)
        now = time.time()
        for i in range(15):
            self.store.record_observation("happy-agent", 100.0, ts=now - i)
        breaches = self.tracker._check_breaches("happy-agent", now=now)
        self.assertEqual(len(breaches), 0)

    def test_breach_saved_to_store(self):
        self.store.define_contract("stored-breach", p50_ms=1, p95_ms=2, p99_ms=3)
        now = time.time()
        for i in range(11):
            self.store.record_observation("stored-breach", 999.0, ts=now - i)
        self.tracker._check_breaches("stored-breach", now=now)
        breaches = self.store.list_breaches(agent_name="stored-breach")
        self.assertGreater(len(breaches), 0)

    def test_no_breach_without_contract(self):
        obs, breaches = self.tracker.record("no-contract-agent", 9999.0)
        self.assertEqual(breaches, [])

    def test_stats_respects_window(self):
        now = time.time()
        self.store.record_observation("win-agent", 100.0, ts=now - 100)
        self.store.record_observation("win-agent", 200.0, ts=now - 7200)
        stats = self.tracker.stats("win-agent", window_s=3600, now=now)
        self.assertEqual(stats.total, 1)


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestSLACLI(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True,
        )

    def test_sla_define_cli(self):
        result = self._run("sla", "define", "test-agent",
                           "--p50", "100", "--p95", "300", "--p99", "500",
                           "--db", ":memory:")
        self.assertEqual(result.returncode, 0)
        self.assertIn("test-agent", result.stdout)

    def test_sla_list_cli(self):
        result = self._run("sla", "list", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)

    def test_sla_stats_no_data(self):
        result = self._run("sla", "stats", "agent-x", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)

    def test_sla_breaches_empty(self):
        result = self._run("sla", "breaches", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)


# ── Public exports ────────────────────────────────────────────────────────────

class TestSLAExports(unittest.TestCase):

    def test_sla_contract_exported(self):
        self.assertTrue(hasattr(meshflow, "SLAContract"))

    def test_latency_record_exported(self):
        self.assertTrue(hasattr(meshflow, "LatencyRecord"))

    def test_sla_stats_exported(self):
        self.assertTrue(hasattr(meshflow, "SLAStats"))

    def test_sla_breach_exported(self):
        self.assertTrue(hasattr(meshflow, "SLABreach"))

    def test_sla_store_exported(self):
        self.assertTrue(hasattr(meshflow, "SLAStore"))

    def test_sla_tracker_exported(self):
        self.assertTrue(hasattr(meshflow, "SLATracker"))

    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
