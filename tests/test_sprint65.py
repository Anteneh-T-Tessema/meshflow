"""Sprint 65 — Compliance Snapshot tests."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile

import meshflow
from meshflow.snapshot.bundle import SnapshotBundle, SnapshotExporter, SnapshotManifest
from meshflow.flags.store import FlagStore
from meshflow.policy.engine import PolicyStore
from meshflow.sla.tracker import SLAStore
from meshflow.vault.store import VaultStore
from meshflow.tenant.store import TenantStore


# ── SnapshotManifest ──────────────────────────────────────────────────────────

class TestSnapshotManifest(unittest.TestCase):

    def _manifest(self):
        import time, uuid
        return SnapshotManifest(
            snapshot_id=str(uuid.uuid4()),
            created_at=time.time(),
            created_by="test",
            description="test snapshot",
            sections=[],
            record_counts={},
        )

    def test_to_dict_has_all_fields(self):
        m = self._manifest()
        d = m.to_dict()
        for key in ("snapshot_id", "created_at", "created_by", "description", "sections", "record_counts"):
            self.assertIn(key, d)

    def test_sections_list(self):
        m = self._manifest()
        self.assertIsInstance(m.sections, list)


# ── SnapshotBundle ────────────────────────────────────────────────────────────

class TestSnapshotBundle(unittest.TestCase):

    def _bundle(self):
        import time, uuid
        manifest = SnapshotManifest(
            snapshot_id=str(uuid.uuid4()),
            created_at=time.time(),
            created_by="test",
            description="test",
            sections=[],
            record_counts={},
        )
        return SnapshotBundle(manifest=manifest)

    def test_add_section(self):
        b = self._bundle()
        b.add_section("agents", [{"id": "a1"}, {"id": "a2"}])
        self.assertIn("agents", b.sections)
        self.assertEqual(len(b.sections["agents"]), 2)

    def test_add_section_updates_manifest(self):
        b = self._bundle()
        b.add_section("locks", [{"lock": "l1"}])
        self.assertIn("locks", b.manifest.sections)
        self.assertEqual(b.manifest.record_counts["locks"], 1)

    def test_total_records(self):
        b = self._bundle()
        b.add_section("s1", [{"x": 1}, {"x": 2}])
        b.add_section("s2", [{"y": 1}])
        self.assertEqual(b.total_records(), 3)

    def test_total_records_empty(self):
        self.assertEqual(self._bundle().total_records(), 0)

    def test_to_zip_bytes_is_valid_zip(self):
        b = self._bundle()
        b.add_section("agents", [{"id": "a1"}])
        raw = b.to_zip_bytes()
        self.assertIsInstance(raw, bytes)
        buf = io.BytesIO(raw)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        self.assertIn("manifest.json", names)
        self.assertIn("agents.json", names)

    def test_zip_contains_manifest_json(self):
        b = self._bundle()
        raw = b.to_zip_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            manifest_data = json.loads(zf.read("manifest.json"))
        self.assertIn("snapshot_id", manifest_data)
        self.assertIn("created_by", manifest_data)

    def test_zip_section_content(self):
        b = self._bundle()
        b.add_section("flags", [{"name": "feature_x", "enabled": True}])
        raw = b.to_zip_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            flags_data = json.loads(zf.read("flags.json"))
        self.assertEqual(len(flags_data), 1)
        self.assertEqual(flags_data[0]["name"], "feature_x")

    def test_multiple_sections_in_zip(self):
        b = self._bundle()
        for name in ("a", "b", "c"):
            b.add_section(name, [{"x": 1}])
        raw = b.to_zip_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
        self.assertIn("a.json", names)
        self.assertIn("b.json", names)
        self.assertIn("c.json", names)


# ── SnapshotExporter ──────────────────────────────────────────────────────────

class TestSnapshotExporter(unittest.TestCase):

    def test_export_no_stores(self):
        exporter = SnapshotExporter()
        bundle = exporter.export(created_by="test-user")
        self.assertIsNotNone(bundle.manifest.snapshot_id)
        self.assertEqual(bundle.manifest.created_by, "test-user")
        self.assertEqual(bundle.total_records(), 0)

    def test_export_with_flag_store(self):
        flags = FlagStore(":memory:")
        flags.define("feature_x", "bool", True)
        exporter = SnapshotExporter(flag_store=flags)
        bundle = exporter.export()
        self.assertIn("feature_flags", bundle.sections)
        self.assertEqual(len(bundle.sections["feature_flags"]), 1)

    def test_export_with_policy_store(self):
        import uuid, time
        from meshflow.policy.engine import PolicyRule, PolicyAction
        ps = PolicyStore(":memory:")
        rule = PolicyRule(
            rule_id=str(uuid.uuid4()),
            name="r1",
            action=PolicyAction.DENY,
            conditions=[],
        )
        ps.add_rule(rule)
        exporter = SnapshotExporter(policy_store=ps)
        bundle = exporter.export()
        self.assertIn("policy_rules", bundle.sections)
        self.assertEqual(len(bundle.sections["policy_rules"]), 1)

    def test_export_with_sla_store(self):
        sla = SLAStore(":memory:")
        sla.define_contract("agent-x", 100, 200, 300)
        exporter = SnapshotExporter(sla_store=sla)
        bundle = exporter.export()
        self.assertIn("sla_contracts", bundle.sections)
        self.assertEqual(len(bundle.sections["sla_contracts"]), 1)

    def test_export_vault_no_values(self):
        vault = VaultStore(":memory:", passphrase="pw")
        vault.store("secret_key", "super_secret_value")
        exporter = SnapshotExporter(vault_store=vault)
        bundle = exporter.export()
        self.assertIn("vault_secrets_metadata", bundle.sections)
        for item in bundle.sections["vault_secrets_metadata"]:
            self.assertNotIn("value", item)
            self.assertNotIn("super_secret_value", json.dumps(item))

    def test_export_with_tenant_store(self):
        ts = TenantStore(":memory:")
        ts.create("Acme", "acme")
        exporter = SnapshotExporter(tenant_store=ts)
        bundle = exporter.export()
        self.assertIn("tenants", bundle.sections)
        self.assertEqual(len(bundle.sections["tenants"]), 1)

    def test_export_with_description(self):
        exporter = SnapshotExporter()
        bundle = exporter.export(description="Annual SOX audit 2026")
        self.assertEqual(bundle.manifest.description, "Annual SOX audit 2026")

    def test_export_default_description(self):
        exporter = SnapshotExporter()
        bundle = exporter.export()
        self.assertIn("Compliance snapshot", bundle.manifest.description)

    def test_export_to_file(self):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            path = f.name
        try:
            exporter = SnapshotExporter()
            bundle = exporter.export_to_file(path)
            with zipfile.ZipFile(path) as zf:
                self.assertIn("manifest.json", zf.namelist())
        finally:
            import os
            os.unlink(path)

    def test_store_error_produces_empty_section(self):
        class BadStore:
            def list_flags(self): raise RuntimeError("DB offline")
        exporter = SnapshotExporter(flag_store=BadStore())
        bundle = exporter.export()
        self.assertIn("feature_flags", bundle.sections)
        self.assertEqual(bundle.sections["feature_flags"], [])

    def test_all_stores_combined(self):
        flags = FlagStore(":memory:")
        flags.define("f1", "bool", True)
        policy = PolicyStore(":memory:")
        sla = SLAStore(":memory:")
        sla.define_contract("my-agent", 100, 200, 300)
        vault = VaultStore(":memory:", passphrase="pw")
        vault.store("key", "val")
        tenants = TenantStore(":memory:")
        tenants.create("Corp", "corp")

        exporter = SnapshotExporter(
            flag_store=flags,
            policy_store=policy,
            sla_store=sla,
            vault_store=vault,
            tenant_store=tenants,
        )
        bundle = exporter.export()
        self.assertGreater(len(bundle.sections), 3)
        self.assertGreater(bundle.total_records(), 0)


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestSnapshotCLI(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True,
        )

    def test_snapshot_export_cli(self):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            path = f.name
        try:
            result = self._run(
                "snapshot", "export",
                "--output", path,
                "--flags-db", ":memory:",
                "--policy-db", ":memory:",
                "--sla-db", ":memory:",
                "--vault-db", ":memory:",
                "--tenant-db", ":memory:",
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("exported", result.stdout.lower())
        finally:
            import os
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


# ── Public exports ────────────────────────────────────────────────────────────

class TestSnapshotExports(unittest.TestCase):

    def test_snapshot_manifest_exported(self):
        self.assertTrue(hasattr(meshflow, "SnapshotManifest"))

    def test_snapshot_bundle_exported(self):
        self.assertTrue(hasattr(meshflow, "SnapshotBundle"))

    def test_snapshot_exporter_exported(self):
        self.assertTrue(hasattr(meshflow, "SnapshotExporter"))

    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
