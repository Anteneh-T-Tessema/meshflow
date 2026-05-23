"""Sprint 20 — deterministic tests.

20A: API Key Management (KeyStore, roles, /keys endpoints)
20B: Deployment artifacts (Helm chart files, Dockerfile, k8s probes)
20C: Policy-as-code (YAML loader, validation, ComplianceGuard from YAML)
20D: OTEL export pipeline (OTELExporter, span building, env factory)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 20A — API Key Management
# ══════════════════════════════════════════════════════════════════════════════


class TestKeyStore:
    def _store(self, tmp_path):
        from meshflow.security.api_keys import KeyStore
        return KeyStore(str(tmp_path / "test.db"))

    def test_create_returns_key_id_and_raw_key(self, tmp_path):
        store = self._store(tmp_path)
        key_id, raw_key = store.create("ci-bot")
        assert key_id.startswith("kid_")
        assert raw_key.startswith("mfk_")

    def test_raw_key_prefix(self, tmp_path):
        store = self._store(tmp_path)
        _, raw_key = store.create("test")
        assert raw_key.startswith("mfk_")
        assert len(raw_key) > 20

    def test_verify_valid_key(self, tmp_path):
        store = self._store(tmp_path)
        _, raw_key = store.create("mybot", role="operator")
        principal = store.verify(raw_key)
        assert principal is not None
        assert principal.name == "mybot"
        assert principal.role == "operator"

    def test_verify_invalid_key_returns_none(self, tmp_path):
        store = self._store(tmp_path)
        assert store.verify("mfk_invalid_garbage_key") is None

    def test_verify_updates_last_used_at(self, tmp_path):
        store = self._store(tmp_path)
        _, raw_key = store.create("tracker")
        p1 = store.verify(raw_key)
        assert p1.last_used_at != ""

    def test_revoke_prevents_verify(self, tmp_path):
        store = self._store(tmp_path)
        key_id, raw_key = store.create("tobevoked")
        assert store.verify(raw_key) is not None
        revoked = store.revoke(key_id)
        assert revoked is True
        assert store.verify(raw_key) is None

    def test_revoke_nonexistent_returns_false(self, tmp_path):
        store = self._store(tmp_path)
        assert store.revoke("kid_doesnotexist") is False

    def test_revoke_already_revoked_returns_false(self, tmp_path):
        store = self._store(tmp_path)
        key_id, _ = store.create("x")
        store.revoke(key_id)
        assert store.revoke(key_id) is False

    def test_list_excludes_revoked(self, tmp_path):
        store = self._store(tmp_path)
        kid1, _ = store.create("active")
        kid2, _ = store.create("inactive")
        store.revoke(kid2)
        keys = store.list()
        ids = {k.key_id for k in keys}
        assert kid1 in ids
        assert kid2 not in ids

    def test_list_tenant_filter(self, tmp_path):
        store = self._store(tmp_path)
        store.create("acme-bot", tenant_id="acme")
        store.create("global-bot", tenant_id="")
        acme_keys = store.list(tenant_id="acme")
        assert len(acme_keys) == 1
        assert acme_keys[0].name == "acme-bot"

    def test_invalid_role_raises(self, tmp_path):
        store = self._store(tmp_path)
        import pytest
        with pytest.raises(ValueError, match="Invalid role"):
            store.create("bad", role="superadmin")

    def test_roles_admin_operator_viewer_accepted(self, tmp_path):
        store = self._store(tmp_path)
        for role in ("admin", "operator", "viewer"):
            _, raw_key = store.create(f"bot-{role}", role=role)
            p = store.verify(raw_key)
            assert p.role == role

    def test_open_mode_when_no_keys(self, tmp_path):
        store = self._store(tmp_path)
        assert store.open_mode is True

    def test_open_mode_false_after_create(self, tmp_path):
        store = self._store(tmp_path)
        store.create("first")
        assert store.open_mode is False

    def test_key_record_to_dict(self, tmp_path):
        store = self._store(tmp_path)
        key_id, raw_key = store.create("dict-test", role="viewer", tenant_id="t1")
        p = store.verify(raw_key)
        d = p.to_dict()
        assert d["role"] == "viewer"
        assert d["tenant_id"] == "t1"
        assert "raw_key" not in d  # never expose the raw key

    def test_static_keys_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHFLOW_API_KEYS", "static-test-key-abc")
        from meshflow.security.api_keys import KeyStore
        store = KeyStore(str(tmp_path / "s.db"))
        p = store.verify("static-test-key-abc")
        assert p is not None
        assert p.role == "operator"
        assert p.key_id == "static"

    def test_static_wrong_key_not_verified(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHFLOW_API_KEYS", "real-key-xyz")
        from meshflow.security.api_keys import KeyStore
        store = KeyStore(str(tmp_path / "s2.db"))
        assert store.verify("wrong-key") is None

    def test_list_all_includes_revoked(self, tmp_path):
        store = self._store(tmp_path)
        kid, _ = store.create("revokeme")
        store.revoke(kid)
        all_keys = store.list_all()
        revoked = [k for k in all_keys if k.key_id == kid]
        assert len(revoked) == 1
        assert revoked[0].revoked is True

    def test_multiple_keys_different_hashes(self, tmp_path):
        store = self._store(tmp_path)
        _, k1 = store.create("a")
        _, k2 = store.create("b")
        assert k1 != k2
        assert store.verify(k1) is not None
        assert store.verify(k2) is not None


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 20B — Deployment Artifacts
# ══════════════════════════════════════════════════════════════════════════════


class TestDeploymentArtifacts:
    _BASE = Path(__file__).parent.parent

    def test_dockerfile_exists(self):
        assert (self._BASE / "Dockerfile").exists()

    def test_dockerfile_uses_health_live_probe(self):
        content = (self._BASE / "Dockerfile").read_text()
        assert "/health/live" in content

    def test_dockerfile_has_non_root_user(self):
        content = (self._BASE / "Dockerfile").read_text()
        assert "useradd" in content or "USER" in content

    def test_k8s_deployment_uses_health_live(self):
        content = (self._BASE / "k8s" / "deployment.yaml").read_text()
        assert "/health/live" in content

    def test_k8s_deployment_uses_health_ready(self):
        content = (self._BASE / "k8s" / "deployment.yaml").read_text()
        assert "/health/ready" in content

    def test_helm_chart_yaml_exists(self):
        assert (self._BASE / "k8s" / "helm" / "Chart.yaml").exists()

    def test_helm_values_yaml_exists(self):
        assert (self._BASE / "k8s" / "helm" / "values.yaml").exists()

    def test_helm_deployment_template_exists(self):
        assert (self._BASE / "k8s" / "helm" / "templates" / "deployment.yaml").exists()

    def test_helm_deployment_uses_health_live(self):
        content = (self._BASE / "k8s" / "helm" / "templates" / "deployment.yaml").read_text()
        assert "/health/live" in content

    def test_helm_deployment_uses_health_ready(self):
        content = (self._BASE / "k8s" / "helm" / "templates" / "deployment.yaml").read_text()
        assert "/health/ready" in content

    def test_helm_service_template_exists(self):
        assert (self._BASE / "k8s" / "helm" / "templates" / "service.yaml").exists()

    def test_helm_secret_template_exists(self):
        assert (self._BASE / "k8s" / "helm" / "templates" / "secret.yaml").exists()

    def test_helm_hpa_template_exists(self):
        assert (self._BASE / "k8s" / "helm" / "templates" / "hpa.yaml").exists()

    def test_helm_pvc_template_exists(self):
        assert (self._BASE / "k8s" / "helm" / "templates" / "pvc.yaml").exists()

    def test_helm_chart_version(self):
        content = (self._BASE / "k8s" / "helm" / "Chart.yaml").read_text()
        assert "0.20.0" in content

    def test_docker_compose_uses_health_live(self):
        content = (self._BASE / "docker-compose.yml").read_text()
        assert "/health/live" in content

    def test_docker_compose_has_redis_service(self):
        content = (self._BASE / "docker-compose.yml").read_text()
        assert "redis" in content

    def test_helm_values_has_autoscaling(self):
        content = (self._BASE / "k8s" / "helm" / "values.yaml").read_text()
        assert "autoscaling" in content

    def test_helm_values_has_ingress(self):
        content = (self._BASE / "k8s" / "helm" / "values.yaml").read_text()
        assert "ingress" in content


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 20C — Policy-as-code
# ══════════════════════════════════════════════════════════════════════════════


def _write_policy(tmp_path, content: str) -> str:
    p = tmp_path / "policy.yaml"
    p.write_text(content)
    return str(p)


class TestPolicyLoader:
    def test_load_policy_yaml_basic(self, tmp_path):
        path = _write_policy(tmp_path, "mode: standard\nbudget_usd: 2.5\n")
        from meshflow.core.policy_loader import load_policy_yaml
        policy = load_policy_yaml(path)
        assert policy is not None
        assert policy.budget_usd == 2.5

    def test_load_policy_default_mode(self, tmp_path):
        path = _write_policy(tmp_path, "budget_tokens: 100000\n")
        from meshflow.core.policy_loader import load_policy_yaml
        policy = load_policy_yaml(path)
        assert policy is not None

    def test_load_policy_legal_critical_mode(self, tmp_path):
        path = _write_policy(tmp_path, "mode: legal-critical\nbudget_usd: 0.5\n")
        from meshflow.core.policy_loader import load_policy_yaml
        policy = load_policy_yaml(path)
        assert policy is not None

    def test_load_guard_yaml_no_compliance_section(self, tmp_path):
        path = _write_policy(tmp_path, "mode: standard\n")
        from meshflow.core.policy_loader import load_guard_yaml
        guard = load_guard_yaml(path)
        assert guard is None

    def test_load_guard_yaml_with_frameworks(self, tmp_path):
        path = _write_policy(tmp_path, "compliance:\n  frameworks: [hipaa]\n  block_on_violation: true\n")
        from meshflow.core.policy_loader import load_guard_yaml
        guard = load_guard_yaml(path)
        assert guard is not None

    def test_load_guard_yaml_gdpr(self, tmp_path):
        path = _write_policy(tmp_path, "compliance:\n  frameworks: [gdpr]\n")
        from meshflow.core.policy_loader import load_guard_yaml
        guard = load_guard_yaml(path)
        assert guard is not None

    def test_load_guard_non_blocking(self, tmp_path):
        path = _write_policy(tmp_path, "compliance:\n  frameworks: [pci]\n  block_on_violation: false\n")
        from meshflow.core.policy_loader import load_guard_yaml
        guard = load_guard_yaml(path)
        assert guard is not None
        assert guard._block_on_violation is False

    def test_load_yaml_returns_tuple(self, tmp_path):
        path = _write_policy(tmp_path, "mode: standard\ncompliance:\n  frameworks: [sox]\n")
        from meshflow.core.policy_loader import load_yaml
        policy, guard = load_yaml(path)
        assert policy is not None
        assert guard is not None

    def test_validate_policy_yaml_valid(self, tmp_path):
        path = _write_policy(tmp_path, "mode: standard\nbudget_usd: 1.0\n")
        from meshflow.core.policy_loader import validate_policy_yaml
        issues = validate_policy_yaml(path)
        assert issues == []

    def test_validate_policy_yaml_unknown_mode(self, tmp_path):
        path = _write_policy(tmp_path, "mode: turbo-illegal\n")
        from meshflow.core.policy_loader import validate_policy_yaml
        issues = validate_policy_yaml(path)
        assert any("mode" in i or "Unknown" in i for i in issues)

    def test_validate_policy_yaml_unknown_framework(self, tmp_path):
        path = _write_policy(tmp_path, "compliance:\n  frameworks: [sec-13f]\n")
        from meshflow.core.policy_loader import validate_policy_yaml
        issues = validate_policy_yaml(path)
        assert any("framework" in i.lower() or "sec-13f" in i for i in issues)

    def test_validate_policy_yaml_missing_file(self, tmp_path):
        from meshflow.core.policy_loader import validate_policy_yaml
        issues = validate_policy_yaml(tmp_path / "nonexistent.yaml")
        assert len(issues) > 0

    def test_example_policy_file_exists(self):
        p = Path(__file__).parent.parent / "meshflow.policy.yaml"
        assert p.exists()

    def test_example_policy_file_validates(self):
        p = Path(__file__).parent.parent / "meshflow.policy.yaml"
        from meshflow.core.policy_loader import validate_policy_yaml
        issues = validate_policy_yaml(p)
        assert issues == []

    def test_load_guard_custom_max_input_chars(self, tmp_path):
        yaml_content = (
            "compliance:\n"
            "  frameworks: [hipaa]\n"
            "  rules:\n"
            "    hipaa_minimum_necessary:\n"
            "      max_input_chars: 10000\n"
        )
        path = _write_policy(tmp_path, yaml_content)
        from meshflow.core.policy_loader import load_guard_yaml
        guard = load_guard_yaml(path)
        assert guard is not None
        # The custom rule should be in extra_rules
        from meshflow.compliance.guard import HIPAAMinimumNecessary
        custom = [r for r in guard._rules if isinstance(r, HIPAAMinimumNecessary) and r.max_input_chars == 10000]
        assert len(custom) == 1

    def test_parse_simple_yaml_booleans(self, tmp_path):
        path = _write_policy(tmp_path, "enable_guardian: true\ndeterministic_gate: false\n")
        from meshflow.core.policy_loader import _read_yaml
        data = _read_yaml(path)
        assert data["enable_guardian"] is True
        assert data["deterministic_gate"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 20D — OTEL Export Pipeline
# ══════════════════════════════════════════════════════════════════════════════


class TestOTELExporter:
    def test_instantiate_default(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter()
        assert exp.config()["endpoint"] == "http://localhost:4318"
        assert exp.config()["service_name"] == "meshflow"

    def test_instantiate_custom(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter(endpoint="http://otel:4318", service_name="my-svc")
        assert exp.config()["service_name"] == "my-svc"

    def test_disabled_exporter_returns_true_without_sending(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter(enabled=False)
        result = exp.export_span(
            trace_id="aabbccdd",
            span_id="11223344",
            name="test:span",
            start_ns=1_000_000,
            end_ns=2_000_000,
        )
        assert result is True
        assert exp.exported_count == 0

    def test_export_span_increments_error_on_connection_refused(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter(endpoint="http://localhost:19999", enabled=True)
        result = exp.export_span(
            trace_id="aabbccdd",
            span_id="11223344",
            name="test:span",
            start_ns=1_000_000,
            end_ns=2_000_000,
        )
        assert result is False
        assert exp.error_count == 1
        assert exp.exported_count == 0

    def test_build_payload_structure(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter(service_name="test-svc")
        payload = exp._build_payload(
            trace_id="aabbccddeeff00112233445566778899",
            span_id="0011223344556677",
            name="step:node_a",
            start_ns=1_000_000_000,
            end_ns=2_000_000_000,
            attributes={"node.id": "node_a", "cost_usd": 0.001},
            status="ok",
            parent_span_id="",
        )
        assert "resourceSpans" in payload
        rs = payload["resourceSpans"][0]
        spans = rs["scopeSpans"][0]["spans"]
        assert len(spans) == 1
        assert spans[0]["name"] == "step:node_a"
        assert spans[0]["status"]["code"] == 1  # ok

    def test_build_payload_error_status(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter()
        payload = exp._build_payload(
            "aa", "bb", "fail:step", 100, 200, {}, "error", ""
        )
        span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert span["status"]["code"] == 2  # error

    def test_build_payload_parent_span_id(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter()
        payload = exp._build_payload(
            "aa", "bb", "child:step", 100, 200, {}, "ok", "parentspanid"
        )
        span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert "parentSpanId" in span

    def test_otlp_kv_string(self):
        from meshflow.observability.otel_exporter import _otlp_kv
        kv = _otlp_kv("key", "value")
        assert kv == {"key": "key", "value": {"stringValue": "value"}}

    def test_otlp_kv_int(self):
        from meshflow.observability.otel_exporter import _otlp_kv
        kv = _otlp_kv("count", 42)
        assert kv["value"]["intValue"] == "42"

    def test_otlp_kv_float(self):
        from meshflow.observability.otel_exporter import _otlp_kv
        kv = _otlp_kv("cost", 0.001)
        assert kv["value"]["doubleValue"] == 0.001

    def test_otlp_kv_bool(self):
        from meshflow.observability.otel_exporter import _otlp_kv
        kv = _otlp_kv("blocked", True)
        assert kv["value"]["boolValue"] is True

    def test_from_env_default(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
        from meshflow.observability import otel_exporter as oe
        oe.reset_global_exporter()
        exp = oe.from_env()
        assert exp.config()["endpoint"] == "http://localhost:4318"
        assert exp.config()["service_name"] == "meshflow"

    def test_from_env_custom(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://custom:4317")
        monkeypatch.setenv("OTEL_SERVICE_NAME", "my-service")
        from meshflow.observability import otel_exporter as oe
        oe.reset_global_exporter()
        exp = oe.from_env()
        assert exp.config()["endpoint"] == "http://custom:4317"
        assert exp.config()["service_name"] == "my-service"

    def test_from_env_headers(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=secret,x-team=platform")
        from meshflow.observability import otel_exporter as oe
        oe.reset_global_exporter()
        exp = oe.from_env()
        assert exp._headers.get("x-api-key") == "secret"
        assert exp._headers.get("x-team") == "platform"

    def test_global_singleton(self, monkeypatch):
        from meshflow.observability import otel_exporter as oe
        oe.reset_global_exporter()
        exp1 = oe.get_global_exporter()
        exp2 = oe.get_global_exporter()
        assert exp1 is exp2

    def test_set_global_exporter(self, monkeypatch):
        from meshflow.observability.otel_exporter import OTELExporter, set_global_exporter, get_global_exporter, reset_global_exporter
        reset_global_exporter()
        custom = OTELExporter(service_name="custom-svc")
        set_global_exporter(custom)
        assert get_global_exporter() is custom
        reset_global_exporter()

    def test_pad_trace_id(self):
        from meshflow.observability.otel_exporter import _pad_trace_id
        assert len(_pad_trace_id("abc")) == 32

    def test_pad_span_id(self):
        from meshflow.observability.otel_exporter import _pad_span_id
        assert len(_pad_span_id("abc")) == 16

    def test_now_ns_returns_int(self):
        from meshflow.observability.otel_exporter import now_ns
        t = now_ns()
        assert isinstance(t, int)
        assert t > 1_700_000_000_000_000_000  # after Nov 2023

    def test_export_span_success_via_mock(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter(endpoint="http://mock:4318")
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = exp.export_span(
                trace_id="aabb",
                span_id="ccdd",
                name="mock:span",
                start_ns=1000,
                end_ns=2000,
                attributes={"test": True},
            )
        assert result is True
        assert exp.exported_count == 1

    def test_service_name_in_resource_attrs(self):
        from meshflow.observability.otel_exporter import OTELExporter
        exp = OTELExporter(service_name="regulated-svc")
        payload = exp._build_payload("aa", "bb", "span", 0, 1, {}, "ok", "")
        resource_attrs = payload["resourceSpans"][0]["resource"]["attributes"]
        svc_attr = next((a for a in resource_attrs if a["key"] == "service.name"), None)
        assert svc_attr is not None
        assert svc_attr["value"]["stringValue"] == "regulated-svc"
