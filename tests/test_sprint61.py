"""Sprint 61 — Tenant Isolation tests."""
import subprocess
import threading
import unittest

import meshflow
from meshflow.tenant.store import (
    TenantContext, TenantGuard, TenantStore, scoped_db_path,
)


# ── scoped_db_path ────────────────────────────────────────────────────────────

class TestScopedDbPath(unittest.TestCase):

    def test_appends_tenant_prefix(self):
        result = scoped_db_path("meshflow_flags.db", "abc12345-xxxx")
        self.assertEqual(result, "meshflow_flags_abc12345.db")

    def test_memory_passthrough(self):
        self.assertEqual(scoped_db_path(":memory:", "any-id"), ":memory:")

    def test_no_db_extension(self):
        result = scoped_db_path("meshflow_data", "abc12345")
        self.assertEqual(result, "meshflow_data_abc12345")

    def test_uses_first_8_chars(self):
        result = scoped_db_path("x.db", "abcdefgh12345678")
        self.assertEqual(result, "x_abcdefgh.db")


# ── TenantContext ─────────────────────────────────────────────────────────────

class TestTenantContext(unittest.TestCase):

    def tearDown(self):
        TenantContext.clear()

    def test_get_returns_none_when_unset(self):
        TenantContext.clear()
        self.assertIsNone(TenantContext.get())

    def test_set_and_get(self):
        TenantContext.set("tenant-123")
        self.assertEqual(TenantContext.get(), "tenant-123")

    def test_clear_resets(self):
        TenantContext.set("tenant-456")
        TenantContext.clear()
        self.assertIsNone(TenantContext.get())

    def test_require_raises_when_unset(self):
        TenantContext.clear()
        with self.assertRaises(RuntimeError):
            TenantContext.require()

    def test_require_returns_id_when_set(self):
        TenantContext.set("my-tenant")
        self.assertEqual(TenantContext.require(), "my-tenant")

    def test_thread_local_isolation(self):
        results = {}
        def worker(tid, name):
            TenantContext.set(tid)
            import time; time.sleep(0.01)
            results[name] = TenantContext.get()

        t1 = threading.Thread(target=worker, args=("tid-1", "t1"))
        t2 = threading.Thread(target=worker, args=("tid-2", "t2"))
        t1.start(); t2.start()
        t1.join(); t2.join()
        self.assertEqual(results["t1"], "tid-1")
        self.assertEqual(results["t2"], "tid-2")


# ── TenantStore ───────────────────────────────────────────────────────────────

class TestTenantStore(unittest.TestCase):

    def setUp(self):
        self.store = TenantStore(":memory:")

    def test_create_tenant(self):
        tenant = self.store.create("Acme Corp", "acme")
        self.assertIsNotNone(tenant.tenant_id)
        self.assertEqual(tenant.name, "Acme Corp")
        self.assertEqual(tenant.slug, "acme")
        self.assertEqual(tenant.plan, "free")
        self.assertEqual(tenant.status, "active")

    def test_create_with_plan(self):
        tenant = self.store.create("BigCo", "bigco", plan="enterprise")
        self.assertEqual(tenant.plan, "enterprise")

    def test_create_invalid_plan_raises(self):
        with self.assertRaises(ValueError):
            self.store.create("Bad", "bad", plan="unknown")

    def test_get_by_id(self):
        t = self.store.create("Test", "test")
        fetched = self.store.get(t.tenant_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Test")

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.store.get("not-a-real-id"))

    def test_get_by_slug(self):
        self.store.create("Alpha", "alpha")
        t = self.store.get_by_slug("alpha")
        self.assertIsNotNone(t)
        self.assertEqual(t.name, "Alpha")

    def test_get_by_slug_unknown_returns_none(self):
        self.assertIsNone(self.store.get_by_slug("nope"))

    def test_list_tenants(self):
        self.store.create("A", "a")
        self.store.create("B", "b")
        tenants = self.store.list_tenants()
        self.assertEqual(len(tenants), 2)

    def test_list_by_status(self):
        t = self.store.create("X", "x")
        self.store.update_status(t.tenant_id, "suspended")
        active = self.store.list_tenants(status="active")
        suspended = self.store.list_tenants(status="suspended")
        self.assertEqual(len(active), 0)
        self.assertEqual(len(suspended), 1)

    def test_update_status_active(self):
        t = self.store.create("C", "c")
        ok = self.store.update_status(t.tenant_id, "suspended")
        self.assertTrue(ok)
        fetched = self.store.get(t.tenant_id)
        self.assertEqual(fetched.status, "suspended")

    def test_update_status_invalid_raises(self):
        t = self.store.create("D", "d")
        with self.assertRaises(ValueError):
            self.store.update_status(t.tenant_id, "invalid-status")

    def test_update_plan(self):
        t = self.store.create("E", "e")
        ok = self.store.update_plan(t.tenant_id, "pro")
        self.assertTrue(ok)
        fetched = self.store.get(t.tenant_id)
        self.assertEqual(fetched.plan, "pro")

    def test_update_plan_invalid_raises(self):
        t = self.store.create("F", "f")
        with self.assertRaises(ValueError):
            self.store.update_plan(t.tenant_id, "platinum")

    def test_delete_tenant(self):
        t = self.store.create("G", "g")
        ok = self.store.delete(t.tenant_id)
        self.assertTrue(ok)
        self.assertIsNone(self.store.get(t.tenant_id))

    def test_count(self):
        self.assertEqual(self.store.count(), 0)
        self.store.create("H", "h")
        self.assertEqual(self.store.count(), 1)

    def test_count_by_status(self):
        t = self.store.create("I", "i")
        self.store.update_status(t.tenant_id, "suspended")
        self.assertEqual(self.store.count(status="active"), 0)
        self.assertEqual(self.store.count(status="suspended"), 1)

    def test_tenant_is_active(self):
        t = self.store.create("J", "j")
        self.assertTrue(t.is_active)

    def test_to_dict(self):
        t = self.store.create("K", "k")
        d = t.to_dict()
        for key in ("tenant_id", "name", "slug", "plan", "status", "metadata", "created_at"):
            self.assertIn(key, d)


# ── TenantGuard ───────────────────────────────────────────────────────────────

class TestTenantGuard(unittest.TestCase):

    def setUp(self):
        self.store = TenantStore(":memory:")
        self.guard = TenantGuard(self.store)

    def tearDown(self):
        TenantContext.clear()

    def test_check_active_tenant_passes(self):
        t = self.store.create("Good", "good")
        tenant = self.guard.check(tenant_id=t.tenant_id)
        self.assertEqual(tenant.slug, "good")

    def test_check_no_context_raises(self):
        TenantContext.clear()
        with self.assertRaises(PermissionError):
            self.guard.check()

    def test_check_unknown_tenant_raises(self):
        with self.assertRaises(PermissionError):
            self.guard.check(tenant_id="no-such-id")

    def test_check_suspended_tenant_raises(self):
        t = self.store.create("Suspended", "suspended")
        self.store.update_status(t.tenant_id, "suspended")
        with self.assertRaises(PermissionError):
            self.guard.check(tenant_id=t.tenant_id)

    def test_check_uses_thread_local_context(self):
        t = self.store.create("Ctx", "ctx")
        TenantContext.set(t.tenant_id)
        tenant = self.guard.check()
        self.assertEqual(tenant.slug, "ctx")


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestTenantCLI(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True,
        )

    def test_tenant_create_cli(self):
        result = self._run("tenant", "create", "Test Corp", "testcorp",
                           "--plan", "free", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)
        self.assertIn("testcorp", result.stdout)

    def test_tenant_list_cli(self):
        result = self._run("tenant", "list", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)


# ── Public exports ────────────────────────────────────────────────────────────

class TestTenantExports(unittest.TestCase):

    def test_tenant_exported(self):
        self.assertTrue(hasattr(meshflow, "Tenant"))

    def test_tenant_context_exported(self):
        self.assertTrue(hasattr(meshflow, "TenantContext"))

    def test_tenant_store_exported(self):
        self.assertTrue(hasattr(meshflow, "TenantStore"))

    def test_tenant_guard_exported(self):
        self.assertTrue(hasattr(meshflow, "TenantGuard"))

    def test_scoped_db_path_exported(self):
        self.assertTrue(hasattr(meshflow, "scoped_db_path"))

    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
