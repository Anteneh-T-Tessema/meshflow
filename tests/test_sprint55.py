"""Sprint 55 — Distributed Locking tests."""

from __future__ import annotations

import json
import subprocess
import time
import threading
import unittest

import meshflow
from meshflow.locking.store import LockRecord, LockStore
from meshflow.locking.lock import DistributedLock, LockAcquisitionError


# ── LockRecord ────────────────────────────────────────────────────────────────

class TestLockRecord(unittest.TestCase):
    def _make(self, expires_offset: float = 30.0) -> LockRecord:
        now = time.time()
        return LockRecord("res-1", "lock-id", "owner-1", now, now + expires_offset, expires_offset)

    def test_is_expired_false_when_fresh(self):
        self.assertFalse(self._make(30.0).is_expired)

    def test_is_expired_true_when_past(self):
        self.assertTrue(self._make(-1.0).is_expired)

    def test_remaining_positive(self):
        self.assertGreater(self._make(30.0).remaining_s, 0)

    def test_remaining_zero_when_expired(self):
        self.assertEqual(self._make(-1.0).remaining_s, 0.0)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ("resource_id", "lock_id", "owner", "acquired_at",
                   "expires_at", "ttl_s", "remaining_s"):
            self.assertIn(k, d)


# ── LockStore ─────────────────────────────────────────────────────────────────

class TestLockStoreTryAcquire(unittest.TestCase):
    def setUp(self):
        self.store = LockStore(":memory:")

    def test_acquire_returns_record(self):
        r = self.store.try_acquire("res-1", "owner-A")
        self.assertIsInstance(r, LockRecord)

    def test_acquire_sets_owner(self):
        r = self.store.try_acquire("res-1", "owner-A")
        self.assertEqual(r.owner, "owner-A")

    def test_acquire_sets_resource_id(self):
        r = self.store.try_acquire("res-1", "owner-A")
        self.assertEqual(r.resource_id, "res-1")

    def test_acquire_second_time_returns_none(self):
        self.store.try_acquire("res-1", "owner-A")
        r2 = self.store.try_acquire("res-1", "owner-B")
        self.assertIsNone(r2)

    def test_acquire_different_resources_both_succeed(self):
        r1 = self.store.try_acquire("res-1", "owner-A")
        r2 = self.store.try_acquire("res-2", "owner-A")
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

    def test_acquire_after_expiry_succeeds(self):
        now = time.time()
        self.store.try_acquire("res-1", "owner-A", ttl_s=5.0, now=now)
        # Simulate time passing past TTL
        r2 = self.store.try_acquire("res-1", "owner-B", ttl_s=30.0, now=now + 10)
        self.assertIsNotNone(r2)

    def test_acquire_ttl_stored(self):
        r = self.store.try_acquire("res-1", "owner-A", ttl_s=60.0)
        self.assertEqual(r.ttl_s, 60.0)


class TestLockStoreRelease(unittest.TestCase):
    def setUp(self):
        self.store = LockStore(":memory:")

    def test_release_by_owner_returns_true(self):
        self.store.try_acquire("res-1", "owner-A")
        self.assertTrue(self.store.release("res-1", "owner-A"))

    def test_release_by_wrong_owner_returns_false(self):
        self.store.try_acquire("res-1", "owner-A")
        self.assertFalse(self.store.release("res-1", "owner-B"))

    def test_release_allows_reacquire(self):
        self.store.try_acquire("res-1", "owner-A")
        self.store.release("res-1", "owner-A")
        r2 = self.store.try_acquire("res-1", "owner-B")
        self.assertIsNotNone(r2)

    def test_force_release_ignores_owner(self):
        self.store.try_acquire("res-1", "owner-A")
        ok = self.store.force_release("res-1")
        self.assertTrue(ok)
        self.assertIsNone(self.store.get("res-1"))

    def test_force_release_unknown_returns_false(self):
        self.assertFalse(self.store.force_release("no-such"))

    def test_release_unknown_returns_false(self):
        self.assertFalse(self.store.release("no-such", "owner"))


class TestLockStoreExtend(unittest.TestCase):
    def setUp(self):
        self.store = LockStore(":memory:")

    def test_extend_increases_expiry(self):
        now = time.time()
        r = self.store.try_acquire("res-1", "owner-A", ttl_s=10.0, now=now)
        original_expiry = r.expires_at
        ok = self.store.extend("res-1", "owner-A", 20.0, now=now)
        self.assertTrue(ok)
        fetched = self.store.get("res-1")
        self.assertAlmostEqual(fetched.expires_at, original_expiry + 20.0, places=3)

    def test_extend_wrong_owner_returns_false(self):
        self.store.try_acquire("res-1", "owner-A", ttl_s=30.0)
        self.assertFalse(self.store.extend("res-1", "owner-B", 10.0))

    def test_extend_expired_returns_false(self):
        now = time.time()
        self.store.try_acquire("res-1", "owner-A", ttl_s=5.0, now=now - 10)
        ok = self.store.extend("res-1", "owner-A", 10.0, now=now)
        self.assertFalse(ok)


class TestLockStoreQuery(unittest.TestCase):
    def setUp(self):
        self.store = LockStore(":memory:")

    def test_get_returns_record(self):
        self.store.try_acquire("res-1", "owner-A")
        r = self.store.get("res-1")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_id, "res-1")

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.store.get("no-such"))

    def test_is_locked_true(self):
        self.store.try_acquire("res-1", "owner-A")
        self.assertTrue(self.store.is_locked("res-1"))

    def test_is_locked_false_after_release(self):
        self.store.try_acquire("res-1", "owner-A")
        self.store.release("res-1", "owner-A")
        self.assertFalse(self.store.is_locked("res-1"))

    def test_list_locks_returns_active(self):
        self.store.try_acquire("res-1", "owner-A")
        self.store.try_acquire("res-2", "owner-B")
        locks = self.store.list_locks()
        self.assertEqual(len(locks), 2)

    def test_count(self):
        self.store.try_acquire("res-1", "owner-A")
        self.store.try_acquire("res-2", "owner-B")
        self.assertEqual(self.store.count(), 2)

    def test_purge_expired_removes_old(self):
        now = time.time()
        self.store.try_acquire("res-1", "owner-A", ttl_s=5.0, now=now - 10)
        self.store.try_acquire("res-2", "owner-B", ttl_s=30.0, now=now)
        n = self.store.purge_expired(now=now)
        self.assertEqual(n, 1)
        self.assertEqual(self.store.count(), 1)


# ── DistributedLock ───────────────────────────────────────────────────────────

class TestDistributedLockBasic(unittest.TestCase):
    def _store(self):
        return LockStore(":memory:")

    def test_context_manager_acquires(self):
        store = self._store()
        with DistributedLock("res", "owner-A", store=store) as lk:
            self.assertTrue(lk.is_held)
            self.assertTrue(store.is_locked("res"))

    def test_context_manager_releases_on_exit(self):
        store = self._store()
        with DistributedLock("res", "owner-A", store=store):
            pass
        self.assertFalse(store.is_locked("res"))

    def test_context_manager_releases_on_exception(self):
        store = self._store()
        try:
            with DistributedLock("res", "owner-A", store=store):
                raise ValueError("oops")
        except ValueError:
            pass
        self.assertFalse(store.is_locked("res"))

    def test_acquire_nonblocking_returns_false_when_held(self):
        store = self._store()
        store.try_acquire("res", "other-owner", ttl_s=60)
        lk = DistributedLock("res", "owner-A", store=store)
        acquired = lk.acquire(blocking=False)
        self.assertFalse(acquired)

    def test_acquire_nonblocking_returns_true_when_free(self):
        store = self._store()
        lk = DistributedLock("res", "owner-A", store=store)
        acquired = lk.acquire(blocking=False)
        self.assertTrue(acquired)
        lk.release()

    def test_extend_works_while_held(self):
        store = self._store()
        with DistributedLock("res", "owner-A", ttl_s=10, store=store) as lk:
            ok = lk.extend(20.0)
            self.assertTrue(ok)

    def test_exclusive_access_two_instances(self):
        store = self._store()
        lk1 = DistributedLock("res", "owner-1", store=store)
        lk2 = DistributedLock("res", "owner-2", store=store)
        lk1.acquire(blocking=False)
        acquired2 = lk2.acquire(blocking=False)
        self.assertFalse(acquired2)
        lk1.release()

    def test_lock_acquisition_error_raised_by_context_manager(self):
        store = self._store()
        store.try_acquire("res", "blocker", ttl_s=60)
        # __enter__ raises when it cannot acquire within ttl_s
        lk = DistributedLock("res", "owner-A", ttl_s=0.05, store=store,
                             retry_interval_s=0.01)
        with self.assertRaises(LockAcquisitionError):
            lk.__enter__()

    def test_acquire_blocking_timeout_returns_false(self):
        store = self._store()
        store.try_acquire("res", "blocker", ttl_s=60)
        lk = DistributedLock("res", "owner-A", store=store, retry_interval_s=0.01)
        acquired = lk.acquire(blocking=True, timeout=0.05)
        self.assertFalse(acquired)

    def test_is_held_false_before_acquire(self):
        lk = DistributedLock("res", "owner-A")
        self.assertFalse(lk.is_held)

    def test_record_set_after_acquire(self):
        store = self._store()
        lk = DistributedLock("res", "owner-A", store=store)
        lk.acquire(blocking=False)
        self.assertIsNotNone(lk.record)
        lk.release()

    def test_lock_acquisition_error_attributes(self):
        err = LockAcquisitionError("my-resource", "my-owner")
        self.assertEqual(err.resource_id, "my-resource")
        self.assertEqual(err.owner, "my-owner")


class TestDistributedLockThreading(unittest.TestCase):
    def test_only_one_thread_holds_lock(self):
        store = LockStore(":memory:")
        held_simultaneously = []
        errors = []
        lock_count = [0]
        results = []

        def worker(owner_id):
            lk = DistributedLock("shared-res", f"worker-{owner_id}", ttl_s=5, store=store)
            acquired = lk.acquire(blocking=True, timeout=2.0)
            if acquired:
                lock_count[0] += 1
                if lock_count[0] > 1:
                    errors.append("concurrent hold detected")
                results.append(owner_id)
                time.sleep(0.02)
                lock_count[0] -= 1
                lk.release()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(errors), 0, f"Concurrency errors: {errors}")
        self.assertEqual(len(results), 5)


# ── CLI tests ─────────────────────────────────────────────────────────────────

class TestLocksCLI(unittest.TestCase):
    def _args_list(self, **kw):
        import argparse
        ns = argparse.Namespace(locks_cmd="list", db=":memory:",
                                show_all=False, json_output=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _args_status(self, resource_id):
        import argparse
        return argparse.Namespace(locks_cmd="status", db=":memory:",
                                  resource_id=resource_id)

    def _args_release(self, resource_id):
        import argparse
        return argparse.Namespace(locks_cmd="release", db=":memory:",
                                  resource_id=resource_id)

    def _args_purge(self):
        import argparse
        return argparse.Namespace(locks_cmd="purge", db=":memory:")

    def test_list_empty(self):
        from meshflow.cli.main import _cmd_locks
        import io
        with patch_stdout() as out:
            _cmd_locks(self._args_list())
        self.assertIn("No active locks", out.getvalue())

    def test_status_unlocked(self):
        from meshflow.cli.main import _cmd_locks
        import io
        with patch_stdout() as out:
            _cmd_locks(self._args_status("not-locked"))
        self.assertIn("not locked", out.getvalue())

    def test_release_unknown_exits(self):
        from meshflow.cli.main import _cmd_locks
        with self.assertRaises(SystemExit):
            _cmd_locks(self._args_release("no-such"))

    def test_purge_prints_count(self):
        from meshflow.cli.main import _cmd_locks
        import io
        with patch_stdout() as out:
            _cmd_locks(self._args_purge())
        self.assertIn("Purged", out.getvalue())

    def test_list_json_output(self):
        from meshflow.cli.main import _cmd_locks
        import io
        store = LockStore(":memory:")
        store.try_acquire("res-1", "owner-A")

        from unittest.mock import patch
        with patch("meshflow.locking.store.LockStore", return_value=store):
            with patch_stdout() as out:
                _cmd_locks(self._args_list(json_output=True))
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)


def patch_stdout():
    import io
    from unittest.mock import patch
    return patch("sys.stdout", new_callable=io.StringIO)


# ── Subprocess help ───────────────────────────────────────────────────────────

class TestSubprocessHelp(unittest.TestCase):
    def test_locks_help(self):
        result = subprocess.run(
            ["meshflow", "locks", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertIn(result.returncode, (0, 1))


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports(unittest.TestCase):
    def test_version(self):
        self.assertEqual(meshflow.__version__, "0.65.0")

    def test_lock_record_exported(self):
        self.assertIs(meshflow.LockRecord, LockRecord)

    def test_lock_store_exported(self):
        self.assertIs(meshflow.LockStore, LockStore)

    def test_distributed_lock_exported(self):
        self.assertIs(meshflow.DistributedLock, DistributedLock)

    def test_lock_acquisition_error_exported(self):
        self.assertIs(meshflow.LockAcquisitionError, LockAcquisitionError)

    def test_all_contains_locking(self):
        for name in ("LockRecord", "LockStore", "DistributedLock", "LockAcquisitionError"):
            self.assertIn(name, meshflow.__all__)

    def test_sprint54_exports_intact(self):
        for name in ("MetricStore", "AlertEngine", "AlertRule"):
            self.assertTrue(hasattr(meshflow, name))


if __name__ == "__main__":
    unittest.main()
