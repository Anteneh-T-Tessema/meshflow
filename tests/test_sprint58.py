"""Sprint 58 — Canary Agent Router tests."""

from __future__ import annotations

import argparse
import io
import subprocess
import time
import unittest
from unittest.mock import patch

import meshflow
from meshflow.canary.router import (
    CanaryConfig,
    CanaryOutcome,
    CanaryStats,
    CanaryStore,
    CanaryRouter,
)


# ── CanaryConfig ──────────────────────────────────────────────────────────────

class TestCanaryConfig(unittest.TestCase):
    def _make(self, status="active") -> CanaryConfig:
        return CanaryConfig(
            experiment_id="exp-1",
            name="test-exp",
            stable_agent="v1",
            canary_agent="v2",
            split=0.1,
            min_requests=10,
            promote_threshold=0.95,
            rollback_threshold=0.80,
            status=status,
            created_at=time.time(),
        )

    def test_is_active_true(self):
        self.assertTrue(self._make("active").is_active)

    def test_is_active_false_promoted(self):
        self.assertFalse(self._make("promoted").is_active)

    def test_is_active_false_rolled_back(self):
        self.assertFalse(self._make("rolled_back").is_active)

    def test_is_active_false_paused(self):
        self.assertFalse(self._make("paused").is_active)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in (
            "experiment_id", "name", "stable_agent", "canary_agent",
            "split", "min_requests", "promote_threshold", "rollback_threshold",
            "status", "created_at",
        ):
            self.assertIn(k, d)

    def test_to_dict_split_value(self):
        self.assertAlmostEqual(self._make().to_dict()["split"], 0.1)


# ── CanaryOutcome ─────────────────────────────────────────────────────────────

class TestCanaryOutcome(unittest.TestCase):
    def _make(self, success=True) -> CanaryOutcome:
        return CanaryOutcome("oid", "exp-1", "canary", success, 120.0, time.time())

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ("outcome_id", "experiment_id", "cohort", "success", "latency_ms", "ts"):
            self.assertIn(k, d)

    def test_to_dict_success_true(self):
        self.assertTrue(self._make(True).to_dict()["success"])

    def test_to_dict_success_false(self):
        self.assertFalse(self._make(False).to_dict()["success"])


# ── CanaryStats ───────────────────────────────────────────────────────────────

class TestCanaryStats(unittest.TestCase):
    def _make(self) -> CanaryStats:
        return CanaryStats("canary", 10, 9, 1, 0.9, 0.1, 50.0)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ("cohort", "total", "successes", "errors", "success_rate", "error_rate", "avg_latency"):
            self.assertIn(k, d)

    def test_to_dict_values(self):
        d = self._make().to_dict()
        self.assertEqual(d["total"], 10)
        self.assertAlmostEqual(d["success_rate"], 0.9)


# ── CanaryStore ───────────────────────────────────────────────────────────────

class TestCanaryStore(unittest.TestCase):
    def setUp(self):
        self.store = CanaryStore(":memory:")

    def _exp(self, name="exp-A", **kw):
        return self.store.create_experiment(
            name=name,
            stable_agent="v1",
            canary_agent="v2",
            **kw,
        )

    def test_create_returns_config(self):
        exp = self._exp()
        self.assertIsInstance(exp, CanaryConfig)

    def test_create_stores_name(self):
        exp = self._exp("billing-exp")
        self.assertEqual(exp.name, "billing-exp")

    def test_create_default_status_active(self):
        exp = self._exp()
        self.assertEqual(exp.status, "active")

    def test_create_split_stored(self):
        exp = self._exp(split=0.2)
        fetched = self.store.get_experiment(exp.experiment_id)
        self.assertAlmostEqual(fetched.split, 0.2)

    def test_create_invalid_split_raises(self):
        with self.assertRaises(ValueError):
            self._exp(split=1.5)

    def test_create_invalid_split_negative_raises(self):
        with self.assertRaises(ValueError):
            self._exp(split=-0.1)

    def test_create_invalid_threshold_order_raises(self):
        with self.assertRaises(ValueError):
            self._exp(promote_threshold=0.5, rollback_threshold=0.9)

    def test_get_experiment_known(self):
        exp = self._exp()
        fetched = self.store.get_experiment(exp.experiment_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.experiment_id, exp.experiment_id)

    def test_get_experiment_unknown_none(self):
        self.assertIsNone(self.store.get_experiment("no-such"))

    def test_get_by_name_known(self):
        self._exp("alpha")
        fetched = self.store.get_by_name("alpha")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "alpha")

    def test_get_by_name_unknown_none(self):
        self.assertIsNone(self.store.get_by_name("no-such"))

    def test_list_experiments_all(self):
        self._exp("exp-A")
        self._exp("exp-B")
        exps = self.store.list_experiments()
        self.assertEqual(len(exps), 2)

    def test_list_experiments_filter_active(self):
        exp = self._exp("exp-A")
        self._exp("exp-B")
        self.store.update_status(exp.experiment_id, "promoted")
        active = self.store.list_experiments(status="active")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].name, "exp-B")

    def test_update_status_valid(self):
        exp = self._exp()
        ok = self.store.update_status(exp.experiment_id, "promoted")
        self.assertTrue(ok)
        self.assertEqual(self.store.get_experiment(exp.experiment_id).status, "promoted")

    def test_update_status_invalid_raises(self):
        exp = self._exp()
        with self.assertRaises(ValueError):
            self.store.update_status(exp.experiment_id, "bad-status")

    def test_update_status_unknown_returns_false(self):
        ok = self.store.update_status("no-such", "promoted")
        self.assertFalse(ok)

    def test_delete_experiment_known(self):
        exp = self._exp()
        ok = self.store.delete_experiment(exp.experiment_id)
        self.assertTrue(ok)
        self.assertIsNone(self.store.get_experiment(exp.experiment_id))

    def test_delete_experiment_cascades_outcomes(self):
        exp = self._exp()
        self.store.record_outcome(exp.experiment_id, "canary", True, 100.0)
        self.store.delete_experiment(exp.experiment_id)
        # outcome_count on deleted experiment returns 0 (table entry gone)
        self.assertEqual(self.store.outcome_count(exp.experiment_id), 0)

    def test_delete_experiment_unknown_returns_false(self):
        self.assertFalse(self.store.delete_experiment("no-such"))

    def test_record_outcome_returns_outcome(self):
        exp = self._exp()
        out = self.store.record_outcome(exp.experiment_id, "canary", True, 50.0)
        self.assertIsInstance(out, CanaryOutcome)
        self.assertEqual(out.cohort, "canary")

    def test_outcome_count_total(self):
        exp = self._exp()
        for _ in range(5):
            self.store.record_outcome(exp.experiment_id, "canary", True)
        self.assertEqual(self.store.outcome_count(exp.experiment_id), 5)

    def test_outcome_count_cohort(self):
        exp = self._exp()
        self.store.record_outcome(exp.experiment_id, "canary", True)
        self.store.record_outcome(exp.experiment_id, "stable", True)
        self.assertEqual(self.store.outcome_count(exp.experiment_id, "canary"), 1)

    def test_cohort_stats_empty(self):
        exp = self._exp()
        stats = self.store.cohort_stats(exp.experiment_id, "canary")
        self.assertEqual(stats.total, 0)
        self.assertEqual(stats.success_rate, 0.0)

    def test_cohort_stats_with_data(self):
        exp = self._exp()
        for i in range(10):
            self.store.record_outcome(exp.experiment_id, "canary", i < 9, float(i * 10))
        stats = self.store.cohort_stats(exp.experiment_id, "canary")
        self.assertEqual(stats.total, 10)
        self.assertEqual(stats.successes, 9)
        self.assertEqual(stats.errors, 1)
        self.assertAlmostEqual(stats.success_rate, 0.9)
        self.assertAlmostEqual(stats.error_rate, 0.1)

    def test_cohort_stats_avg_latency(self):
        exp = self._exp()
        self.store.record_outcome(exp.experiment_id, "canary", True, 100.0)
        self.store.record_outcome(exp.experiment_id, "canary", True, 200.0)
        stats = self.store.cohort_stats(exp.experiment_id, "canary")
        self.assertAlmostEqual(stats.avg_latency, 150.0)


# ── CanaryRouter ──────────────────────────────────────────────────────────────

class TestCanaryRouter(unittest.TestCase):
    def setUp(self):
        self.store = CanaryStore(":memory:")
        self.router = CanaryRouter(self.store, seed=42)
        self.exp = self.store.create_experiment(
            name="test",
            stable_agent="v1",
            canary_agent="v2",
            split=0.5,
            min_requests=5,
            promote_threshold=0.90,
            rollback_threshold=0.60,
        )

    def test_route_returns_stable_or_canary(self):
        result = self.router.route(self.exp.experiment_id)
        self.assertIn(result, ("stable", "canary"))

    def test_route_inactive_experiment_returns_stable(self):
        self.store.update_status(self.exp.experiment_id, "promoted")
        self.assertEqual(self.router.route(self.exp.experiment_id), "stable")

    def test_route_unknown_experiment_returns_stable(self):
        self.assertEqual(self.router.route("no-such"), "stable")

    def test_route_split_zero_always_stable(self):
        exp2 = self.store.create_experiment("zero", "v1", "v2", split=0.0)
        router = CanaryRouter(self.store, seed=1)
        for _ in range(20):
            self.assertEqual(router.route(exp2.experiment_id), "stable")

    def test_route_split_one_always_canary(self):
        exp2 = self.store.create_experiment("full", "v1", "v2", split=1.0)
        router = CanaryRouter(self.store, seed=1)
        for _ in range(20):
            self.assertEqual(router.route(exp2.experiment_id), "canary")

    def test_route_deterministic_with_seed(self):
        r1 = CanaryRouter(self.store, seed=99)
        r2 = CanaryRouter(self.store, seed=99)
        results1 = [r1.route(self.exp.experiment_id) for _ in range(10)]
        results2 = [r2.route(self.exp.experiment_id) for _ in range(10)]
        self.assertEqual(results1, results2)

    def test_record_outcome_delegates(self):
        out = self.router.record_outcome(self.exp.experiment_id, "canary", True, 100.0)
        self.assertIsInstance(out, CanaryOutcome)

    def test_stats_returns_both_cohorts(self):
        stats = self.router.stats(self.exp.experiment_id)
        self.assertIn("stable", stats)
        self.assertIn("canary", stats)

    def test_should_promote_false_insufficient_data(self):
        self.assertFalse(self.router.should_promote(self.exp.experiment_id))

    def test_should_promote_true(self):
        for _ in range(10):
            self.store.record_outcome(self.exp.experiment_id, "canary", True, 50.0)
        self.assertTrue(self.router.should_promote(self.exp.experiment_id))

    def test_should_promote_false_below_threshold(self):
        for i in range(10):
            self.store.record_outcome(self.exp.experiment_id, "canary", i < 8, 50.0)
        self.assertFalse(self.router.should_promote(self.exp.experiment_id))

    def test_should_rollback_false_insufficient_data(self):
        self.assertFalse(self.router.should_rollback(self.exp.experiment_id))

    def test_should_rollback_true(self):
        for i in range(10):
            self.store.record_outcome(self.exp.experiment_id, "canary", i < 4, 50.0)
        self.assertTrue(self.router.should_rollback(self.exp.experiment_id))

    def test_should_rollback_false_above_threshold(self):
        for _ in range(10):
            self.store.record_outcome(self.exp.experiment_id, "canary", True, 50.0)
        self.assertFalse(self.router.should_rollback(self.exp.experiment_id))

    def test_should_promote_inactive_returns_false(self):
        self.store.update_status(self.exp.experiment_id, "paused")
        self.assertFalse(self.router.should_promote(self.exp.experiment_id))

    def test_should_rollback_inactive_returns_false(self):
        self.store.update_status(self.exp.experiment_id, "paused")
        self.assertFalse(self.router.should_rollback(self.exp.experiment_id))

    def test_promote_updates_status(self):
        ok = self.router.promote(self.exp.experiment_id)
        self.assertTrue(ok)
        self.assertEqual(
            self.store.get_experiment(self.exp.experiment_id).status, "promoted"
        )

    def test_rollback_updates_status(self):
        ok = self.router.rollback(self.exp.experiment_id)
        self.assertTrue(ok)
        self.assertEqual(
            self.store.get_experiment(self.exp.experiment_id).status, "rolled_back"
        )

    def test_pause_updates_status(self):
        ok = self.router.pause(self.exp.experiment_id)
        self.assertTrue(ok)
        self.assertEqual(
            self.store.get_experiment(self.exp.experiment_id).status, "paused"
        )

    def test_promote_unknown_returns_false(self):
        self.assertFalse(self.router.promote("no-such"))

    def test_rollback_unknown_returns_false(self):
        self.assertFalse(self.router.rollback("no-such"))


# ── CLI tests ─────────────────────────────────────────────────────────────────

def _args(cmd, **kw):
    ns = argparse.Namespace(canary_cmd=cmd, db=":memory:", json_output=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestCanaryCLI(unittest.TestCase):
    def test_create_prints_id(self):
        from meshflow.cli.main import _cmd_canary
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_canary(_args(
                "create",
                name="billing-v2",
                stable_agent="billing-v1",
                canary_agent="billing-v2",
                split=0.1,
                min_requests=10,
                promote_threshold=0.95,
                rollback_threshold=0.80,
            ))
        self.assertIn("created", out.getvalue())

    def test_list_empty(self):
        from meshflow.cli.main import _cmd_canary
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_canary(_args("list", status=""))
        self.assertIn("No canary", out.getvalue())

    def test_list_json_output(self):
        from meshflow.cli.main import _cmd_canary
        import json
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_canary(_args("list", status="", json_output=True))
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)

    def test_status_unknown_exits(self):
        from meshflow.cli.main import _cmd_canary
        with self.assertRaises(SystemExit):
            _cmd_canary(_args("status", name="no-such"))

    def test_promote_unknown_exits(self):
        from meshflow.cli.main import _cmd_canary
        with self.assertRaises(SystemExit):
            _cmd_canary(_args("promote", name="no-such"))

    def test_rollback_unknown_exits(self):
        from meshflow.cli.main import _cmd_canary
        with self.assertRaises(SystemExit):
            _cmd_canary(_args("rollback", name="no-such"))

    def test_pause_unknown_exits(self):
        from meshflow.cli.main import _cmd_canary
        with self.assertRaises(SystemExit):
            _cmd_canary(_args("pause", name="no-such"))

    def test_status_shows_cohorts(self):
        from meshflow.cli.main import _cmd_canary
        from meshflow.canary.router import CanaryStore

        store = CanaryStore(":memory:")
        exp = store.create_experiment("my-exp", "v1", "v2", split=0.2)
        for _ in range(5):
            store.record_outcome(exp.experiment_id, "canary", True, 50.0)

        with patch("meshflow.canary.router.CanaryStore", return_value=store):
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                _cmd_canary(_args("status", name="my-exp"))
        self.assertIn("canary", out.getvalue())

    def test_promote_success(self):
        from meshflow.cli.main import _cmd_canary
        from meshflow.canary.router import CanaryStore

        store = CanaryStore(":memory:")
        store.create_experiment("promo-exp", "v1", "v2")

        with patch("meshflow.canary.router.CanaryStore", return_value=store):
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                _cmd_canary(_args("promote", name="promo-exp"))
        self.assertIn("promoted", out.getvalue())


# ── Subprocess ────────────────────────────────────────────────────────────────

class TestSubprocessHelp(unittest.TestCase):
    def test_canary_help(self):
        r = subprocess.run(
            ["meshflow", "canary", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertIn(r.returncode, (0, 1))

    def test_canary_create_help(self):
        r = subprocess.run(
            ["meshflow", "canary", "create", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertIn(r.returncode, (0, 1))


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")

    def test_canary_config_exported(self):
        self.assertIs(meshflow.CanaryConfig, CanaryConfig)

    def test_canary_outcome_exported(self):
        self.assertIs(meshflow.CanaryOutcome, CanaryOutcome)

    def test_canary_stats_exported(self):
        self.assertIs(meshflow.CanaryStats, CanaryStats)

    def test_canary_store_exported(self):
        self.assertIs(meshflow.CanaryStore, CanaryStore)

    def test_canary_router_exported(self):
        self.assertIs(meshflow.CanaryRouter, CanaryRouter)

    def test_all_contains_canary(self):
        for name in ("CanaryConfig", "CanaryOutcome", "CanaryStats", "CanaryStore", "CanaryRouter"):
            self.assertIn(name, meshflow.__all__)

    def test_sprint57_exports_intact(self):
        for name in ("AgentIdentity", "AgentToken", "IdentityStore", "sign_token", "verify_token"):
            self.assertTrue(hasattr(meshflow, name), f"missing: {name}")

    def test_sprint56_exports_intact(self):
        for name in ("LineageNode", "LineageEdge", "LineageGraph"):
            self.assertTrue(hasattr(meshflow, name), f"missing: {name}")

    def test_sprint55_exports_intact(self):
        for name in ("DistributedLock", "LockStore", "LockAcquisitionError"):
            self.assertTrue(hasattr(meshflow, name), f"missing: {name}")


if __name__ == "__main__":
    unittest.main()
