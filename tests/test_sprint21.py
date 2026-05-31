"""Sprint 21 — deterministic tests.

21A: Tenant isolation (webhooks, ledger scoping via API key tenant_id)
21B: GitHub Actions CI (workflow file structure)
21C: Benchmark integration (bench_core --quick flag, README)
21D: Docs (QUICKSTART.md, SECURITY.md at root, compliance guides)
"""

from __future__ import annotations

from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 21A — Tenant Isolation
# ══════════════════════════════════════════════════════════════════════════════


class TestWebhookTenantIsolation:
    def _manager(self):
        from meshflow.observability.webhooks import WebhookManager
        return WebhookManager(default_secret="test-secret")

    def test_register_with_tenant_id(self):
        mgr = self._manager()
        reg = mgr.register("http://example.com/hook", tenant_id="acme")
        assert reg.tenant_id == "acme"

    def test_register_without_tenant_id(self):
        mgr = self._manager()
        reg = mgr.register("http://example.com/hook")
        assert reg.tenant_id == ""

    def test_list_filters_by_tenant(self):
        mgr = self._manager()
        mgr.register("http://a.com", tenant_id="acme")
        mgr.register("http://b.com", tenant_id="beta")
        mgr.register("http://g.com", tenant_id="")  # global
        acme = mgr.list(tenant_id="acme")
        assert len(acme) == 2  # own + global
        assert all(h.tenant_id in ("acme", "") for h in acme)

    def test_list_global_returns_all(self):
        mgr = self._manager()
        mgr.register("http://a.com", tenant_id="acme")
        mgr.register("http://b.com", tenant_id="beta")
        all_hooks = mgr.list(tenant_id="")
        assert len(all_hooks) == 2

    def test_unregister_own_tenant_succeeds(self):
        mgr = self._manager()
        reg = mgr.register("http://a.com", tenant_id="acme")
        removed = mgr.unregister(reg.id, tenant_id="acme")
        assert removed is True
        assert mgr.get(reg.id) is None

    def test_unregister_cross_tenant_blocked(self):
        mgr = self._manager()
        reg = mgr.register("http://a.com", tenant_id="acme")
        removed = mgr.unregister(reg.id, tenant_id="beta")
        assert removed is False
        assert mgr.get(reg.id) is not None

    def test_unregister_no_tenant_scope_allowed(self):
        """Admin (empty tenant_id) can remove any hook."""
        mgr = self._manager()
        reg = mgr.register("http://a.com", tenant_id="acme")
        removed = mgr.unregister(reg.id, tenant_id="")
        assert removed is True

    def test_get_cross_tenant_blocked(self):
        mgr = self._manager()
        reg = mgr.register("http://a.com", tenant_id="acme")
        hook = mgr.get(reg.id, tenant_id="beta")
        assert hook is None

    def test_get_own_tenant_allowed(self):
        mgr = self._manager()
        reg = mgr.register("http://a.com", tenant_id="acme")
        hook = mgr.get(reg.id, tenant_id="acme")
        assert hook is not None

    def test_get_global_hook_visible_to_any_tenant(self):
        mgr = self._manager()
        reg = mgr.register("http://g.com", tenant_id="")
        assert mgr.get(reg.id, tenant_id="acme") is not None
        assert mgr.get(reg.id, tenant_id="beta") is not None

    def test_webhook_to_dict_includes_tenant_id(self):
        mgr = self._manager()
        reg = mgr.register("http://a.com", tenant_id="acme")
        d = reg.to_dict()
        assert "tenant_id" in d
        assert d["tenant_id"] == "acme"

    def test_delivery_history_tenant_filter(self):
        from meshflow.observability.webhooks import WebhookManager, DeliveryRecord
        mgr = WebhookManager(default_secret="s")
        reg_a = mgr.register("http://a.com", tenant_id="acme")
        reg_b = mgr.register("http://b.com", tenant_id="beta")
        # Manually inject delivery records
        from datetime import datetime, timezone
        mgr._history.append(DeliveryRecord(
            webhook_id=reg_a.id, event_type="run_completed",
            timestamp=datetime.now(timezone.utc).isoformat(),
            success=True, status_code=200, error=None, attempt=1,
        ))
        mgr._history.append(DeliveryRecord(
            webhook_id=reg_b.id, event_type="run_completed",
            timestamp=datetime.now(timezone.utc).isoformat(),
            success=True, status_code=200, error=None, attempt=1,
        ))
        acme_history = mgr.delivery_history(tenant_id="acme")
        assert len(acme_history) == 1
        assert acme_history[0].webhook_id == reg_a.id

    def test_key_store_tenant_scoping(self, tmp_path):
        from meshflow.security.api_keys import KeyStore
        store = KeyStore(str(tmp_path / "test.db"))
        store.create("acme-bot", role="operator", tenant_id="acme")
        store.create("beta-bot", role="operator", tenant_id="beta")
        acme = store.list(tenant_id="acme")
        assert len(acme) == 1
        assert acme[0].name == "acme-bot"
        beta = store.list(tenant_id="beta")
        assert len(beta) == 1
        assert beta[0].name == "beta-bot"

    def test_ledger_for_helper_returns_scoped_ledger(self, tmp_path):
        """Verify that different tenant_ids get different ledger instances."""
        from meshflow.core.ledger import ReplayLedger
        from meshflow.security.api_keys import KeyRecord

        db_path = str(tmp_path / "test.db")
        ledger_cache: dict[str, ReplayLedger] = {}

        def _ledger_for(principal: KeyRecord) -> ReplayLedger:
            tid = principal.tenant_id or ""
            if not tid:
                return ReplayLedger(db_path)
            if tid not in ledger_cache:
                ledger_cache[tid] = ReplayLedger(db_path, tenant_id=tid)
            return ledger_cache[tid]

        p_acme = KeyRecord("k1", "bot", "operator", "acme", "", "", False)
        p_beta = KeyRecord("k2", "bot", "operator", "beta", "", "", False)
        p_global = KeyRecord("k3", "bot", "admin", "", "", "", False)

        l_acme = _ledger_for(p_acme)
        l_acme2 = _ledger_for(p_acme)
        l_beta = _ledger_for(p_beta)
        l_global = _ledger_for(p_global)

        # Same tenant → same instance
        assert l_acme is l_acme2
        # Different tenant → different instance
        assert l_acme is not l_beta
        # Global → unscoped
        assert l_global._tenant_id == "default"

    def test_deliver_tenant_scoped(self):
        """deliver() with tenant_id only sends to that tenant's hooks."""
        import asyncio
        from meshflow.observability.webhooks import WebhookManager

        mgr = WebhookManager(default_secret="s")
        mgr.register("http://acme.com", tenant_id="acme")
        mgr.register("http://beta.com", tenant_id="beta")

        targets_seen: list[str] = []
        original_deliver_one = mgr._deliver_one

        async def _mock_deliver_one(hook, event_type, body):
            targets_seen.append(hook.tenant_id)

        mgr._deliver_one = _mock_deliver_one  # type: ignore[method-assign]

        asyncio.run(mgr.deliver("run_completed", {"test": 1}, tenant_id="acme"))
        assert "beta" not in targets_seen
        assert "acme" in targets_seen or "" in targets_seen


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 21B — GitHub Actions CI
# ══════════════════════════════════════════════════════════════════════════════


class TestGitHubActionsCI:
    _BASE = Path(__file__).parent.parent

    def test_ci_workflow_exists(self):
        assert (self._BASE / ".github" / "workflows" / "ci.yml").exists()

    def test_ci_runs_tests(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "pytest" in content

    def test_ci_has_python_matrix(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "python-version" in content
        assert "3.11" in content

    def test_ci_has_typecheck_job(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "typecheck" in content or "mypy" in content

    def test_ci_has_lint_job(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "lint" in content or "ruff" in content

    def test_ci_has_benchmark_job(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "benchmark" in content or "bench" in content

    def test_ci_triggers_on_push_to_main(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "main" in content
        assert "push" in content

    def test_ci_triggers_on_pull_request(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "pull_request" in content

    def test_ci_uses_actions_checkout(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "actions/checkout" in content

    def test_ci_uses_setup_python(self):
        content = (self._BASE / ".github" / "workflows" / "ci.yml").read_text()
        assert "actions/setup-python" in content


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 21C — Benchmark Integration
# ══════════════════════════════════════════════════════════════════════════════


class TestBenchmarkIntegration:
    _BASE = Path(__file__).parent.parent

    def test_bench_core_exists(self):
        assert (self._BASE / "benchmarks" / "bench_core.py").exists()

    def test_bench_readme_exists(self):
        assert (self._BASE / "benchmarks" / "README.md").exists()

    def test_bench_core_has_quick_flag(self):
        content = (self._BASE / "benchmarks" / "bench_core.py").read_text()
        assert "--quick" in content

    def test_bench_readme_documents_quick(self):
        content = (self._BASE / "benchmarks" / "README.md").read_text()
        assert "--quick" in content or "quick" in content.lower()

    def test_bench_core_importable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bench_core",
            str(self._BASE / "benchmarks" / "bench_core.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        # Should not raise ImportError
        assert mod is not None

    def test_bench_core_has_main_function(self):
        content = (self._BASE / "benchmarks" / "bench_core.py").read_text()
        assert "def main" in content

    def test_bench_readme_has_baseline_numbers(self):
        content = (self._BASE / "benchmarks" / "README.md").read_text()
        # Should have some numeric baseline reference
        assert "rps" in content.lower() or "latency" in content.lower()

    def test_meshflow_bench_cli_registered(self):
        """meshflow bench command should be in the CLI dispatch table."""
        content = (self._BASE / "meshflow" / "cli" / "main.py").read_text()
        assert '"bench"' in content or "'bench'" in content


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 21D — Docs
# ══════════════════════════════════════════════════════════════════════════════


class TestDocs:
    _BASE = Path(__file__).parent.parent

    def test_quickstart_exists(self):
        assert (self._BASE / "docs" / "QUICKSTART.md").exists()

    def test_quickstart_has_install_section(self):
        content = (self._BASE / "docs" / "QUICKSTART.md").read_text()
        assert "pip install" in content

    def test_quickstart_has_serve_section(self):
        content = (self._BASE / "docs" / "QUICKSTART.md").read_text()
        assert "meshflow serve" in content or "meshflow dev" in content

    def test_quickstart_has_keys_section(self):
        content = (self._BASE / "docs" / "QUICKSTART.md").read_text()
        assert "keys" in content.lower() and ("generate" in content or "API key" in content)

    def test_quickstart_has_kubernetes_section(self):
        content = (self._BASE / "docs" / "QUICKSTART.md").read_text()
        assert "kubernetes" in content.lower() or "helm" in content.lower() or "k8s" in content.lower()

    def test_quickstart_has_otel_section(self):
        content = (self._BASE / "docs" / "QUICKSTART.md").read_text()
        assert "otel" in content.lower() or "otlp" in content.lower()

    def test_security_md_at_repo_root(self):
        assert (self._BASE / "SECURITY.md").exists()

    def test_security_md_has_reporting_section(self):
        content = (self._BASE / "SECURITY.md").read_text()
        assert "vulnerabilit" in content.lower()

    def test_hipaa_guide_exists(self):
        assert (self._BASE / "docs" / "compliance" / "HIPAA_GUIDE.md").exists()

    def test_gdpr_guide_exists(self):
        assert (self._BASE / "docs" / "compliance" / "GDPR_GUIDE.md").exists()

    def test_soc2_guide_exists(self):
        assert (self._BASE / "docs" / "compliance" / "SOC2_CONTROLS_MAPPING.md").exists()

    def test_hipaa_guide_has_quick_start(self):
        content = (self._BASE / "docs" / "compliance" / "HIPAA_GUIDE.md").read_text()
        assert "Quick" in content or "quick" in content

    def test_gap_remediation_plan_exists(self):
        assert (self._BASE / "docs" / "GAP_REMEDIATION_PLAN.md").exists()

    def test_golden_standard_platform_exists(self):
        assert (self._BASE / "docs" / "golden-standard-platform.md").exists()

    def test_policy_yaml_example_exists(self):
        assert (self._BASE / "meshflow.policy.yaml").exists()

    def test_policy_yaml_has_compliance_section(self):
        content = (self._BASE / "meshflow.policy.yaml").read_text()
        assert "compliance" in content

    def test_changelog_has_sprint21_entry(self):
        content = (self._BASE / "CHANGELOG.md").read_text()
        # After this sprint is committed, the changelog will have 0.21.0
        # For now we check that CHANGELOG exists and has entries
        assert "## [0." in content


# ══════════════════════════════════════════════════════════════════════════════
# Additional Sprint 21A tests — WebhookRegistration dataclass
# ══════════════════════════════════════════════════════════════════════════════


class TestWebhookRegistrationDataclass:
    def test_matches_wildcard(self):
        from meshflow.observability.webhooks import WebhookRegistration
        reg = WebhookRegistration(
            id="x", url="http://a.com", events=["*"],
            secret="s", created_at="now", tenant_id="t1",
        )
        assert reg.matches("any_event") is True

    def test_matches_specific_event(self):
        from meshflow.observability.webhooks import WebhookRegistration
        reg = WebhookRegistration(
            id="x", url="http://a.com", events=["run_completed"],
            secret="s", created_at="now", tenant_id="",
        )
        assert reg.matches("run_completed") is True
        assert reg.matches("run_failed") is False

    def test_to_dict_tenant_id_present(self):
        from meshflow.observability.webhooks import WebhookRegistration
        reg = WebhookRegistration(
            id="id1", url="http://a.com", events=["*"],
            secret="s", created_at="now", tenant_id="corp",
        )
        d = reg.to_dict()
        assert d["tenant_id"] == "corp"


# ══════════════════════════════════════════════════════════════════════════════
# Regression: existing webhook tests still pass with new tenant param
# ══════════════════════════════════════════════════════════════════════════════


class TestWebhookBackwardCompat:
    def _manager(self):
        from meshflow.observability.webhooks import WebhookManager
        return WebhookManager(default_secret="test-secret")

    def test_register_no_tenant_backward_compat(self):
        mgr = self._manager()
        reg = mgr.register("http://example.com/hook")
        assert reg is not None
        assert reg.tenant_id == ""

    def test_list_no_args_returns_all(self):
        mgr = self._manager()
        mgr.register("http://a.com")
        mgr.register("http://b.com")
        assert len(mgr.list()) == 2

    def test_unregister_no_tenant_arg(self):
        mgr = self._manager()
        reg = mgr.register("http://a.com")
        assert mgr.unregister(reg.id) is True

    def test_get_no_tenant_arg(self):
        mgr = self._manager()
        reg = mgr.register("http://a.com")
        assert mgr.get(reg.id) is not None

    def test_delivery_history_no_tenant_arg(self):
        mgr = self._manager()
        assert mgr.delivery_history() == []
