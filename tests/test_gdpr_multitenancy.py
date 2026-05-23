"""Tests for GDPR ledger operations and multi-tenancy."""
from __future__ import annotations

import datetime
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ledger(tenant_id: str = "default"):
    from meshflow.core.ledger import ReplayLedger
    return ReplayLedger(":memory:", tenant_id=tenant_id)


async def _seed_run(ledger, run_id: str = "run-001"):
    """Insert one StepRecord for testing."""
    from meshflow.core.runtime import StepRecord
    record = StepRecord(
        run_id=run_id,
        step_id=f"step-{run_id}",
        node_id="node-a",
        node_kind="python",
        input_task="classify patient record",
        output_content="PHI detected: name, DOB",
        verdict="commit",
        blocked=False,
        block_reason="",
        uncertainty=0.1,
        cost_usd=0.002,
        tokens_used=150,
        carbon_gco2=0.001,
        duration_ms=120.0,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    await ledger.write(record)


# ── delete_run ────────────────────────────────────────────────────────────────


class TestDeleteRun:
    @pytest.mark.asyncio
    async def test_delete_removes_step_records(self) -> None:
        ledger = _make_ledger()
        await _seed_run(ledger, "run-del-1")
        deleted = await ledger.delete_run("run-del-1")
        assert deleted >= 1
        runs = await ledger.list_runs()
        assert "run-del-1" not in runs

    @pytest.mark.asyncio
    async def test_delete_nonexistent_run_returns_zero(self) -> None:
        ledger = _make_ledger()
        deleted = await ledger.delete_run("never-existed")
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_other_runs(self) -> None:
        ledger = _make_ledger()
        await _seed_run(ledger, "run-keep")
        await _seed_run(ledger, "run-del")
        await ledger.delete_run("run-del")
        runs = await ledger.list_runs()
        assert "run-keep" in runs
        assert "run-del" not in runs


# ── anonymize_run ─────────────────────────────────────────────────────────────


class TestAnonymizeRun:
    @pytest.mark.asyncio
    async def test_anonymize_redacts_content(self) -> None:
        ledger = _make_ledger()
        await _seed_run(ledger, "run-anon")
        await ledger.anonymize_run("run-anon")
        steps = await ledger.get_run("run-anon")
        for step in steps:
            assert step.get("input_task") == "[REDACTED]"
            assert step.get("output_content") == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_anonymize_preserves_structure(self) -> None:
        ledger = _make_ledger()
        await _seed_run(ledger, "run-struct")
        count = await ledger.anonymize_run("run-struct")
        assert count >= 1
        runs = await ledger.list_runs()
        assert "run-struct" in runs


# ── delete_tenant ─────────────────────────────────────────────────────────────


class TestDeleteTenant:
    @pytest.mark.asyncio
    async def test_delete_tenant_removes_all_runs(self) -> None:
        ledger = _make_ledger(tenant_id="acme")
        await _seed_run(ledger, "acme-run-1")
        await _seed_run(ledger, "acme-run-2")
        deleted = await ledger.delete_tenant("acme")
        assert deleted >= 2
        runs = await ledger.list_runs()
        assert len(runs) == 0

    @pytest.mark.asyncio
    async def test_delete_tenant_does_not_affect_other_tenant(self) -> None:
        import tempfile
        import os
        from meshflow.core.ledger import ReplayLedger

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            ledger_a = ReplayLedger(db_path, tenant_id="tenant-a")
            ledger_b = ReplayLedger(db_path, tenant_id="tenant-b")

            await _seed_run(ledger_a, "a-run")
            await _seed_run(ledger_b, "b-run")

            await ledger_a.delete_tenant("tenant-a")

            b_runs = await ledger_b.list_runs()
            assert "b-run" in b_runs
        finally:
            os.unlink(db_path)


# ── Multi-tenancy isolation ───────────────────────────────────────────────────


class TestMultiTenancy:
    @pytest.mark.asyncio
    async def test_tenants_see_only_their_runs(self) -> None:
        import tempfile
        import os
        from meshflow.core.ledger import ReplayLedger

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            l_alpha = ReplayLedger(db_path, tenant_id="alpha")
            l_beta = ReplayLedger(db_path, tenant_id="beta")

            await _seed_run(l_alpha, "alpha-1")
            await _seed_run(l_alpha, "alpha-2")
            await _seed_run(l_beta, "beta-1")

            alpha_runs = await l_alpha.list_runs()
            beta_runs = await l_beta.list_runs()

            assert "alpha-1" in alpha_runs
            assert "alpha-2" in alpha_runs
            assert "beta-1" not in alpha_runs

            assert "beta-1" in beta_runs
            assert "alpha-1" not in beta_runs
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_default_tenant_id(self) -> None:
        from meshflow.core.ledger import ReplayLedger
        ledger = ReplayLedger(":memory:")
        assert ledger._tenant_id == "default"

    @pytest.mark.asyncio
    async def test_custom_tenant_id_stored(self) -> None:
        from meshflow.core.ledger import ReplayLedger
        ledger = ReplayLedger(":memory:", tenant_id="healthcare-corp")
        assert ledger._tenant_id == "healthcare-corp"


# ── Schema migrations ─────────────────────────────────────────────────────────


class TestSchemaMigrations:
    def test_migrations_list_is_ordered(self) -> None:
        from meshflow.core.ledger import _MIGRATIONS
        versions = [m[0] for m in _MIGRATIONS]
        assert versions == sorted(versions), "Migrations must be in ascending version order"

    def test_migrations_are_alter_or_create(self) -> None:
        from meshflow.core.ledger import _MIGRATIONS
        for version, sql in _MIGRATIONS:
            upper = sql.upper()
            assert "ALTER TABLE" in upper or "CREATE" in upper, \
                f"Migration {version} should be ALTER TABLE or CREATE"

    @pytest.mark.asyncio
    async def test_ledger_applies_migrations_on_connect(self) -> None:
        from meshflow.core.ledger import ReplayLedger
        ledger = ReplayLedger(":memory:")
        runs = await ledger.list_runs()
        assert isinstance(runs, list)
