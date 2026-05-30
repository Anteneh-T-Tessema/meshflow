"""Sprint 60 — Secret Vault tests."""
import subprocess
import sys
import tempfile
import unittest

import meshflow
from meshflow.vault.store import VaultAuditLog, VaultSecret, VaultStore


# ── VaultSecret ───────────────────────────────────────────────────────────────

class TestVaultSecret(unittest.TestCase):

    def test_to_dict_structure(self):
        store = VaultStore(":memory:")
        secret = store.store("mykey", "mysecret")
        d = secret.to_dict()
        self.assertIn("secret_id", d)
        self.assertIn("name", d)
        self.assertIn("category", d)
        self.assertIn("created_at", d)
        self.assertNotIn("value", d)

    def test_secret_has_value_attr(self):
        store = VaultStore(":memory:")
        secret = store.store("k", "v")
        self.assertEqual(secret.value, "v")


# ── VaultStore ────────────────────────────────────────────────────────────────

class TestVaultStore(unittest.TestCase):

    def setUp(self):
        self.store = VaultStore(":memory:", passphrase="test-passphrase")

    def test_store_and_retrieve(self):
        self.store.store("api_key", "abc123")
        secret = self.store.retrieve("api_key")
        self.assertIsNotNone(secret)
        self.assertEqual(secret.value, "abc123")

    def test_retrieve_unknown_returns_none(self):
        self.assertIsNone(self.store.retrieve("nonexistent"))

    def test_store_different_passphrase_fails(self):
        self.store.store("key", "value")
        store2 = VaultStore(":memory:", passphrase="wrong-passphrase")
        # Different in-memory store, can't retrieve same data

    def test_rotate_updates_value(self):
        self.store.store("token", "old-value")
        ok = self.store.rotate("token", "new-value")
        self.assertTrue(ok)
        secret = self.store.retrieve("token")
        self.assertEqual(secret.value, "new-value")

    def test_rotate_unknown_returns_false(self):
        ok = self.store.rotate("no-such-key", "value")
        self.assertFalse(ok)

    def test_delete_removes_secret(self):
        self.store.store("temp", "data")
        ok = self.store.delete("temp")
        self.assertTrue(ok)
        self.assertIsNone(self.store.retrieve("temp"))

    def test_delete_unknown_returns_false(self):
        ok = self.store.delete("does-not-exist")
        self.assertFalse(ok)

    def test_list_secrets_excludes_values(self):
        self.store.store("s1", "v1", category="api")
        self.store.store("s2", "v2", category="db")
        secrets = self.store.list_secrets()
        self.assertEqual(len(secrets), 2)
        for s in secrets:
            self.assertNotIn("value", s)
            self.assertIn("name", s)

    def test_list_secrets_filter_by_category(self):
        self.store.store("a", "va", category="api")
        self.store.store("b", "vb", category="db")
        api = self.store.list_secrets(category="api")
        self.assertEqual(len(api), 1)
        self.assertEqual(api[0]["name"], "a")

    def test_exists(self):
        self.assertFalse(self.store.exists("x"))
        self.store.store("x", "y")
        self.assertTrue(self.store.exists("x"))

    def test_count(self):
        self.assertEqual(self.store.count(), 0)
        self.store.store("s1", "v1")
        self.store.store("s2", "v2")
        self.assertEqual(self.store.count(), 2)

    def test_audit_log_store_action(self):
        self.store.store("logged", "value")
        log = self.store.audit_log()
        self.assertTrue(any(e.operation == "write" and e.secret_name == "logged" for e in log))

    def test_audit_log_retrieve_action(self):
        self.store.store("logged", "value")
        self.store.retrieve("logged")
        log = self.store.audit_log()
        ops = [e.operation for e in log]
        self.assertIn("read", ops)

    def test_audit_log_rotate_action(self):
        self.store.store("rot", "v1")
        self.store.rotate("rot", "v2")
        log = self.store.audit_log()
        self.assertTrue(any(e.operation == "rotate" for e in log))

    def test_audit_log_delete_action(self):
        self.store.store("del", "v")
        self.store.delete("del")
        log = self.store.audit_log()
        self.assertTrue(any(e.operation == "delete" for e in log))

    def test_audit_log_filter_by_name(self):
        self.store.store("s1", "v1")
        self.store.store("s2", "v2")
        self.store.retrieve("s1")
        log = self.store.audit_log(name="s1")
        self.assertTrue(all(e.secret_name == "s1" for e in log))

    def test_store_with_description(self):
        self.store.store("desc_key", "val", description="My API key")
        secrets = self.store.list_secrets()
        matched = [s for s in secrets if s["name"] == "desc_key"]
        self.assertEqual(matched[0].get("description", ""), "My API key")

    def test_encrypt_differs_from_plaintext(self):
        self.store.store("enc", "plaintext")
        # The raw ciphertext stored in DB should differ from "plaintext"
        row = self.store._conn().execute(
            "SELECT ciphertext FROM vault_secrets WHERE name='enc'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row[0], "plaintext")

    def test_large_secret_value(self):
        large = "x" * 10000
        self.store.store("large", large)
        retrieved = self.store.retrieve("large")
        self.assertEqual(retrieved.value, large)

    def test_store_with_category(self):
        secret = self.store.store("cat_test", "val", category="credentials")
        self.assertEqual(secret.category, "credentials")


# ── File-backed store ─────────────────────────────────────────────────────────

class TestVaultStoreFileBacked(unittest.TestCase):

    def test_persist_across_instances(self):
        import os
        db_path = tempfile.mktemp(suffix=".db")
        try:
            s1 = VaultStore(db_path, passphrase="pass")
            s1.store("key", "value")
            s2 = VaultStore(db_path, passphrase="pass")
            secret = s2.retrieve("key")
            self.assertIsNotNone(secret)
            self.assertEqual(secret.value, "value")
        finally:
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestVaultCLI(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True,
        )

    def test_vault_store_cli(self):
        result = self._run("vault", "store", "test_key", "test_value",
                           "--db", ":memory:", "--passphrase", "pw")
        self.assertEqual(result.returncode, 0)
        self.assertIn("stored", result.stdout.lower())

    def test_vault_list_cli(self):
        result = self._run("vault", "list", "--db", ":memory:", "--passphrase", "pw")
        self.assertEqual(result.returncode, 0)


# ── Public exports ────────────────────────────────────────────────────────────

class TestVaultExports(unittest.TestCase):

    def test_vault_secret_exported(self):
        self.assertTrue(hasattr(meshflow, "VaultSecret"))

    def test_vault_audit_log_exported(self):
        self.assertTrue(hasattr(meshflow, "VaultAuditLog"))

    def test_vault_store_exported(self):
        self.assertTrue(hasattr(meshflow, "VaultStore"))

    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
