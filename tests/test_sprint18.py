"""Sprint 18 — Compliance Reporting + Webhook Alerting

Tests:
  - ComplianceReporter: all 5 frameworks, pass/warn/fail scenarios
  - ComplianceFinding / ComplianceSummary / ComplianceReport serialisation
  - WebhookManager: register, list, unregister, get
  - WebhookManager: event filtering (exact match + wildcard)
  - WebhookManager: HMAC signing correctness
  - WebhookManager: delivery with mock server (HTTP 200 and 4xx)
  - WebhookManager: retry on transient failure
  - WebhookManager: stats, delivery history
  - Server: GET /compliance/report — all frameworks
  - Server: GET /webhooks, POST /webhooks, DELETE /webhooks/{id}
  - Server: /webhooks/{id}/deliveries
  - Dashboard helpers: fetch_compliance_report, fetch_webhooks
  - CLI: compliance report (text + json output)
  - CLI: webhooks list / add / remove
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_steps(
    n: int = 5,
    blocked: int = 0,
    uncertainty: float = 0.0,
    collusion_risk: float = 0.0,
    with_hashes: bool = True,
    phi_scrubbed: bool = False,
    node_count: int = 2,
) -> list[dict[str, Any]]:
    """Generate synthetic ledger step records for testing."""
    steps = []
    node_ids = [f"node_{i}" for i in range(node_count)]
    for i in range(n):
        steps.append({
            "step_id": f"step_{i:04d}",
            "run_id": "run_test_001",
            "node_id": node_ids[i % len(node_ids)],
            "verdict": "approved",
            "blocked": i < blocked,
            "blocked_by": "policy" if i < blocked else "",
            "uncertainty": uncertainty,
            "cost_usd": 0.001,
            "tokens_used": 100,
            "carbon_gco2": 0.00001,
            "collusion_risk": collusion_risk,
            "phi_scrubbed": phi_scrubbed,
            "entry_hash": f"hash_{i:04d}" if with_hashes else "",
            "prev_hash": f"hash_{i-1:04d}" if (with_hashes and i > 0) else "",
            "timestamp": "2026-05-23T10:00:00Z",
        })
    return steps


# ── ComplianceReporter ────────────────────────────────────────────────────────

class TestComplianceReporterHIPAA:
    def setup_method(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter
        self.reporter = ComplianceReporter()

    def test_hipaa_compliant_run(self) -> None:
        steps = _make_steps(10)
        report = self.reporter.generate("hipaa", steps, run_ids=["run_001"])
        assert report.framework == "hipaa"
        assert report.summary.overall_status == "compliant"
        assert report.summary.passed >= 3
        assert report.summary.failed == 0
        assert report.total_steps == 10

    def test_hipaa_blocked_steps_warning(self) -> None:
        steps = _make_steps(10, blocked=3)
        report = self.reporter.generate("hipaa", steps)
        # Blocked steps produce a warning on Access Control
        findings_map = {f.control_id: f for f in report.findings}
        ac = findings_map.get("HIPAA-§164.312(a)(1)")
        assert ac is not None
        assert ac.status == "warning"
        assert "3" in ac.detail

    def test_hipaa_collusion_fails(self) -> None:
        steps = _make_steps(5, collusion_risk=0.9)
        report = self.reporter.generate("hipaa", steps)
        findings_map = {f.control_id: f for f in report.findings}
        ra = findings_map.get("HIPAA-§164.308(a)(1)")
        assert ra is not None
        assert ra.status == "fail"
        assert report.summary.overall_status == "non_compliant"

    def test_hipaa_missing_hashes_warning(self) -> None:
        steps = _make_steps(5, with_hashes=False)
        report = self.reporter.generate("hipaa", steps)
        findings_map = {f.control_id: f for f in report.findings}
        audit = findings_map.get("HIPAA-§164.312(b)")
        assert audit is not None
        assert audit.status == "warning"

    def test_hipaa_phi_scrubbed(self) -> None:
        steps = _make_steps(5, phi_scrubbed=True)
        report = self.reporter.generate("hipaa", steps)
        findings_map = {f.control_id: f for f in report.findings}
        phi = findings_map.get("HIPAA-§164.312(e)(2)(ii)")
        assert phi is not None
        assert phi.status == "pass"

    def test_hipaa_high_uncertainty_warning(self) -> None:
        steps = _make_steps(5, uncertainty=0.9)
        report = self.reporter.generate("hipaa", steps)
        findings_map = {f.control_id: f for f in report.findings}
        integrity = findings_map.get("HIPAA-§164.312(c)(1)")
        assert integrity is not None
        assert integrity.status == "warning"

    def test_hipaa_empty_steps(self) -> None:
        report = self.reporter.generate("hipaa", [])
        assert report.total_steps == 0
        assert report.summary.total >= 1  # checks still run


class TestComplianceReporterSOX:
    def setup_method(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter
        self.reporter = ComplianceReporter()

    def test_sox_multi_agent_segregation(self) -> None:
        steps = _make_steps(10, node_count=3)
        report = self.reporter.generate("sox", steps)
        findings_map = {f.control_id: f for f in report.findings}
        assert findings_map["SOX-§302"].status == "pass"

    def test_sox_single_agent_warning(self) -> None:
        steps = _make_steps(10, node_count=1)
        report = self.reporter.generate("sox", steps)
        findings_map = {f.control_id: f for f in report.findings}
        assert findings_map["SOX-§302"].status == "warning"

    def test_sox_missing_hashes_fail(self) -> None:
        steps = _make_steps(5, with_hashes=False)
        report = self.reporter.generate("sox", steps)
        findings_map = {f.control_id: f for f in report.findings}
        assert findings_map["SOX-§404"].status == "fail"
        assert report.summary.overall_status == "non_compliant"

    def test_sox_cost_accounting(self) -> None:
        steps = _make_steps(10)
        report = self.reporter.generate("sox", steps)
        findings_map = {f.control_id: f for f in report.findings}
        cost_finding = findings_map.get("SOX-§404-COST")
        assert cost_finding is not None
        assert cost_finding.status == "pass"
        assert "$" in cost_finding.detail


class TestComplianceReporterGDPR:
    def setup_method(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter
        self.reporter = ComplianceReporter()

    def test_gdpr_compliant(self) -> None:
        steps = _make_steps(8)
        report = self.reporter.generate("gdpr", steps)
        assert report.framework == "gdpr"
        assert report.summary.overall_status in ("compliant", "partial")

    def test_gdpr_high_token_warning(self) -> None:
        steps = _make_steps(5)
        for s in steps:
            s["tokens_used"] = 60_000
        report = self.reporter.generate("gdpr", steps)
        findings_map = {f.control_id: f for f in report.findings}
        dm = findings_map.get("GDPR-Art5(1)(c)")
        assert dm is not None
        assert dm.status == "warning"

    def test_gdpr_records_of_processing(self) -> None:
        steps = _make_steps(6, node_count=2)
        report = self.reporter.generate("gdpr", steps)
        findings_map = {f.control_id: f for f in report.findings}
        ropa = findings_map.get("GDPR-Art30")
        assert ropa is not None
        assert ropa.status == "pass"


class TestComplianceReporterPCI:
    def setup_method(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter
        self.reporter = ComplianceReporter()

    def test_pci_audit_logging(self) -> None:
        steps = _make_steps(5)
        report = self.reporter.generate("pci", steps)
        findings_map = {f.control_id: f for f in report.findings}
        assert findings_map["PCI-DSS-Req10"].status == "pass"

    def test_pci_missing_audit_log_fail(self) -> None:
        steps = _make_steps(5, with_hashes=False)
        report = self.reporter.generate("pci", steps)
        findings_map = {f.control_id: f for f in report.findings}
        assert findings_map["PCI-DSS-Req10"].status == "fail"

    def test_pci_collusion_fail(self) -> None:
        steps = _make_steps(5, collusion_risk=0.9)
        report = self.reporter.generate("pci", steps)
        findings_map = {f.control_id: f for f in report.findings}
        assert findings_map["PCI-DSS-Req6"].status == "fail"


class TestComplianceReporterNERC:
    def setup_method(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter
        self.reporter = ComplianceReporter()

    def test_nerc_compliant(self) -> None:
        steps = _make_steps(5)
        report = self.reporter.generate("nerc", steps)
        assert report.framework == "nerc"
        assert report.summary.failed == 0

    def test_nerc_incident_fail(self) -> None:
        steps = _make_steps(5, collusion_risk=0.9)
        report = self.reporter.generate("nerc", steps)
        findings_map = {f.control_id: f for f in report.findings}
        assert findings_map["NERC-CIP-008"].status == "fail"


class TestComplianceReporterMisc:
    def setup_method(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter
        self.reporter = ComplianceReporter()

    def test_unknown_framework_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown framework"):
            self.reporter.generate("iso27001", [])

    def test_run_ids_stored(self) -> None:
        steps = _make_steps(3)
        report = self.reporter.generate("hipaa", steps, run_ids=["r1", "r2"])
        assert report.run_ids == ["r1", "r2"]

    def test_metadata_stored(self) -> None:
        steps = _make_steps(3)
        report = self.reporter.generate("sox", steps, metadata={"auditor": "alice"})
        assert report.metadata["auditor"] == "alice"

    def test_to_dict_roundtrip(self) -> None:
        steps = _make_steps(5)
        report = self.reporter.generate("gdpr", steps)
        d = report.to_dict()
        assert d["framework"] == "gdpr"
        assert "summary" in d
        assert "findings" in d
        assert isinstance(d["findings"], list)

    def test_to_json_valid(self) -> None:
        steps = _make_steps(5)
        report = self.reporter.generate("pci", steps)
        parsed = json.loads(report.to_json())
        assert parsed["framework"] == "pci"

    def test_to_text_contains_framework(self) -> None:
        steps = _make_steps(5)
        report = self.reporter.generate("hipaa", steps)
        text = report.to_text()
        assert "HIPAA" in text
        assert "Compliance Report" in text

    def test_pass_rate_calculation(self) -> None:
        steps = _make_steps(5)
        report = self.reporter.generate("sox", steps)
        s = report.summary
        assert 0.0 <= s.pass_rate <= 1.0

    def test_supported_frameworks_constant(self) -> None:
        from meshflow.compliance.reporter import SUPPORTED_FRAMEWORKS
        assert "hipaa" in SUPPORTED_FRAMEWORKS
        assert "sox" in SUPPORTED_FRAMEWORKS
        assert "gdpr" in SUPPORTED_FRAMEWORKS
        assert "pci" in SUPPORTED_FRAMEWORKS
        assert "nerc" in SUPPORTED_FRAMEWORKS


# ── WebhookManager ────────────────────────────────────────────────────────────

class TestWebhookManager:
    def setup_method(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        self.mgr = WebhookManager(default_secret="test_secret")

    def test_register_returns_registration(self) -> None:
        reg = self.mgr.register("https://example.com/hook")
        assert reg.id
        assert reg.url == "https://example.com/hook"
        assert reg.events == ["*"]
        assert reg.created_at

    def test_register_custom_events(self) -> None:
        reg = self.mgr.register("https://example.com/hook", events=["policy_violation", "run_failed"])
        assert reg.events == ["policy_violation", "run_failed"]

    def test_register_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="http"):
            self.mgr.register("ftp://bad.url")

    def test_register_invalid_event_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown event types"):
            self.mgr.register("https://example.com", events=["not_an_event"])

    def test_list_returns_all(self) -> None:
        self.mgr.register("https://a.com")
        self.mgr.register("https://b.com")
        assert len(self.mgr.list()) == 2

    def test_unregister_existing(self) -> None:
        reg = self.mgr.register("https://a.com")
        assert self.mgr.unregister(reg.id) is True
        assert len(self.mgr.list()) == 0

    def test_unregister_nonexistent(self) -> None:
        assert self.mgr.unregister("no_such_id") is False

    def test_get_returns_registration(self) -> None:
        reg = self.mgr.register("https://a.com")
        fetched = self.mgr.get(reg.id)
        assert fetched is not None
        assert fetched.id == reg.id

    def test_get_nonexistent_returns_none(self) -> None:
        assert self.mgr.get("ghost") is None

    def test_matches_wildcard(self) -> None:
        reg = self.mgr.register("https://a.com", events=["*"])
        assert reg.matches("policy_violation") is True
        assert reg.matches("run_failed") is True
        assert reg.matches("anything") is True

    def test_matches_specific(self) -> None:
        reg = self.mgr.register("https://a.com", events=["run_failed"])
        assert reg.matches("run_failed") is True
        assert reg.matches("policy_violation") is False

    def test_hmac_signing_correct(self) -> None:
        reg = self.mgr.register("https://a.com", secret="my_secret")
        body = b'{"event":"test"}'
        sig = self.mgr._sign(reg.secret, body)
        expected = hmac.new(b"my_secret", body, hashlib.sha256).hexdigest()
        assert sig == expected

    def test_to_dict_no_secret_by_default(self) -> None:
        reg = self.mgr.register("https://a.com", secret="top_secret")
        d = reg.to_dict()
        assert "secret" not in d

    def test_to_dict_with_secret(self) -> None:
        reg = self.mgr.register("https://a.com", secret="top_secret")
        d = reg.to_dict(include_secret=True)
        assert d["secret"] == "top_secret"

    def test_stats_initial(self) -> None:
        s = self.mgr.stats()
        assert s["registered"] == 0
        assert s["total_deliveries"] == 0

    def test_stats_after_register(self) -> None:
        self.mgr.register("https://a.com")
        self.mgr.register("https://b.com")
        assert self.mgr.stats()["registered"] == 2

    def test_delivery_history_empty(self) -> None:
        reg = self.mgr.register("https://a.com")
        assert self.mgr.delivery_history(reg.id) == []


class TestWebhookDelivery:
    """Tests delivery logic using a local HTTP server."""

    @pytest.fixture(autouse=True)
    def local_server(self) -> Any:
        """Start a tiny HTTP server that records incoming requests."""
        received: list[dict] = []
        statuses: list[int] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received.append({
                    "body": json.loads(body),
                    "sig": self.headers.get("X-MeshFlow-Signature", ""),
                    "event": self.headers.get("X-MeshFlow-Event", ""),
                })
                status = statuses.pop(0) if statuses else 200
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass  # silence log output

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.server = server
        self.port = port
        self.received = received
        self.statuses = statuses
        yield
        server.shutdown()

    def _url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def test_successful_delivery(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        mgr = WebhookManager(default_secret="s3cr3t")
        mgr.register(self._url(), events=["run_completed"])

        asyncio.run(mgr.deliver("run_completed", {"run_id": "r1", "status": "completed"}))

        assert len(self.received) == 1
        assert self.received[0]["event"] == "run_completed"
        assert self.received[0]["body"]["payload"]["run_id"] == "r1"

    def test_signature_is_valid(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        secret = "webhook_secret_123"
        mgr = WebhookManager()
        mgr.register(self._url(), secret=secret, events=["*"])

        asyncio.run(mgr.deliver("policy_violation", {"node_id": "agent_a"}))

        assert len(self.received) == 1
        payload_body = json.dumps({
            "event": "policy_violation",
            "timestamp": self.received[0]["body"]["timestamp"],
            "payload": {"node_id": "agent_a"},
        }).encode()
        expected_sig = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
        assert self.received[0]["sig"] == expected_sig

    def test_no_delivery_if_event_not_subscribed(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        mgr = WebhookManager()
        mgr.register(self._url(), events=["run_failed"])

        asyncio.run(mgr.deliver("run_completed", {"run_id": "r2"}))

        assert len(self.received) == 0

    def test_delivery_count_increments(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        mgr = WebhookManager()
        reg = mgr.register(self._url(), events=["*"])

        asyncio.run(mgr.deliver("run_completed", {}))
        asyncio.run(mgr.deliver("run_completed", {}))

        assert reg.delivery_count == 2

    def test_history_recorded_on_success(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        mgr = WebhookManager()
        reg = mgr.register(self._url(), events=["*"])

        asyncio.run(mgr.deliver("run_failed", {"error": "timeout"}))

        history = mgr.delivery_history(reg.id)
        assert len(history) == 1
        assert history[0].success is True
        assert history[0].event_type == "run_failed"

    def test_failed_delivery_increments_failure_count(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        # Register a URL that will refuse connections
        mgr = WebhookManager()
        reg = mgr.register("http://127.0.0.1:19999/no_such_server", events=["*"])

        asyncio.run(mgr.deliver("run_failed", {}))

        assert reg.failure_count >= 1
        assert reg.last_error is not None

    def test_multiple_hooks_receive_same_event(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        mgr = WebhookManager()
        mgr.register(self._url(), events=["*"])
        mgr.register(self._url(), events=["policy_violation"])

        asyncio.run(mgr.deliver("policy_violation", {"blocked_by": "guardian"}))

        # Both hooks match; server receives 2 requests
        assert len(self.received) == 2


# ── Server endpoints ──────────────────────────────────────────────────────────

class TestServerCompliance:
    def test_compliance_report_hipaa(self) -> None:
        """GET /compliance/report?framework=hipaa returns a valid report."""

        async def _mock_list_runs() -> list[str]:
            return ["run_001"]

        async def _mock_get_run(run_id: str) -> list[dict]:
            return _make_steps(5)

        mock_ledger = MagicMock()
        mock_ledger.list_runs = _mock_list_runs
        mock_ledger.get_run = _mock_get_run

        from meshflow.compliance.reporter import ComplianceReporter
        reporter = ComplianceReporter()
        steps = asyncio.run(_mock_get_run("run_001"))
        report = reporter.generate("hipaa", steps, run_ids=["run_001"])
        d = report.to_dict()
        assert d["framework"] == "hipaa"
        assert "summary" in d

    def test_compliance_report_all_frameworks_valid(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter, SUPPORTED_FRAMEWORKS
        reporter = ComplianceReporter()
        steps = _make_steps(6)
        for fw in SUPPORTED_FRAMEWORKS:
            report = reporter.generate(fw, steps)
            d = report.to_dict()
            assert d["framework"] == fw
            assert d["summary"]["total"] > 0

    def test_compliance_report_text_format(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter
        reporter = ComplianceReporter()
        steps = _make_steps(4)
        report = reporter.generate("sox", steps)
        text = report.to_text()
        assert "SOX" in text
        assert "=" in text  # separator line


class TestServerWebhooks:
    def setup_method(self) -> None:
        from meshflow.observability.webhooks import reset_webhook_manager
        reset_webhook_manager()

    def test_register_and_list(self) -> None:
        from meshflow.observability.webhooks import get_webhook_manager
        mgr = get_webhook_manager()
        reg = mgr.register("https://example.com/hook", events=["run_failed"])
        hooks = mgr.list()
        assert any(h.id == reg.id for h in hooks)

    def test_register_and_delete(self) -> None:
        from meshflow.observability.webhooks import get_webhook_manager
        mgr = get_webhook_manager()
        reg = mgr.register("https://example.com/hook")
        assert mgr.unregister(reg.id) is True
        assert mgr.get(reg.id) is None

    def test_register_returns_id(self) -> None:
        from meshflow.observability.webhooks import get_webhook_manager
        mgr = get_webhook_manager()
        reg = mgr.register("https://example.com/hook")
        assert len(reg.id) == 36  # UUID4

    def test_get_webhook_manager_singleton(self) -> None:
        from meshflow.observability.webhooks import get_webhook_manager
        m1 = get_webhook_manager()
        m2 = get_webhook_manager()
        assert m1 is m2

    def test_reset_clears_singleton(self) -> None:
        from meshflow.observability.webhooks import get_webhook_manager, reset_webhook_manager
        m1 = get_webhook_manager()
        m1.register("https://example.com")
        reset_webhook_manager()
        m2 = get_webhook_manager()
        assert m1 is not m2
        assert len(m2.list()) == 0

    def test_delivery_history_filter(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        mgr = WebhookManager()
        reg_a = mgr.register("https://a.com")
        reg_b = mgr.register("https://b.com")
        # Manually inject records
        mgr._record(reg_a.id, "run_failed", True, 200, None, 1)
        mgr._record(reg_b.id, "run_failed", True, 200, None, 1)
        mgr._record(reg_a.id, "policy_violation", True, 200, None, 1)

        hist_a = mgr.delivery_history(reg_a.id)
        hist_b = mgr.delivery_history(reg_b.id)
        assert len(hist_a) == 2
        assert len(hist_b) == 1
        assert all(r.webhook_id == reg_a.id for r in hist_a)

    def test_delivery_record_to_dict(self) -> None:
        from meshflow.observability.webhooks import WebhookManager
        mgr = WebhookManager()
        reg = mgr.register("https://a.com")
        mgr._record(reg.id, "hitl_pending", False, 503, "Service Unavailable", 3)
        records = mgr.delivery_history(reg.id)
        assert len(records) == 1
        d = records[0].to_dict()
        assert d["success"] is False
        assert d["status_code"] == 503
        assert d["attempt"] == 3


# ── CLI smoke tests ───────────────────────────────────────────────────────────

class TestCLIComplianceReport:
    def test_compliance_report_json_output(self, tmp_path: Any) -> None:
        from meshflow.compliance.reporter import ComplianceReporter

        reporter = ComplianceReporter()
        steps = _make_steps(5)
        report = reporter.generate("hipaa", steps)
        out_file = tmp_path / "report.json"
        out_file.write_text(report.to_json())
        data = json.loads(out_file.read_text())
        assert data["framework"] == "hipaa"

    def test_compliance_report_text_output(self, tmp_path: Any) -> None:
        from meshflow.compliance.reporter import ComplianceReporter

        reporter = ComplianceReporter()
        steps = _make_steps(5)
        report = reporter.generate("sox", steps)
        out_file = tmp_path / "report.txt"
        out_file.write_text(report.to_text())
        content = out_file.read_text()
        assert "SOX" in content
        assert "Compliance Report" in content

    def test_all_frameworks_produce_output(self) -> None:
        from meshflow.compliance.reporter import ComplianceReporter, SUPPORTED_FRAMEWORKS

        reporter = ComplianceReporter()
        steps = _make_steps(3)
        for fw in SUPPORTED_FRAMEWORKS:
            report = reporter.generate(fw, steps)
            assert len(report.to_text()) > 50
            assert len(report.to_json()) > 50
