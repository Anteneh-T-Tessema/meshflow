"""Sprint 72 — Production Deployment tests.

Covers Doctor, EnvGenerator, DockerDeployer (mocked), and CLI commands.
All tests run without Docker — Docker calls are mocked or skipped.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

import meshflow
from meshflow.deploy.doctor import Doctor, DoctorReport, CheckResult, CheckStatus
from meshflow.deploy.env_generator import EnvGenerator, ValidationIssue
from meshflow.deploy.deployer import DockerDeployer, DeployResult


# ══════════════════════════════════════════════════════════════════════════════
#  Doctor
# ══════════════════════════════════════════════════════════════════════════════

class TestDoctor:

    def test_run_returns_doctor_report(self):
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        assert isinstance(report, DoctorReport)

    def test_report_has_checks(self):
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        assert len(report.checks) > 0

    def test_report_has_duration(self):
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        assert report.duration_ms >= 0

    def test_check_result_fields(self):
        c = CheckResult(
            name="Test check",
            status=CheckStatus.PASS,
            message="All good",
        )
        assert c.name == "Test check"
        assert c.status == CheckStatus.PASS
        assert c.icon == "✓"

    def test_check_icons(self):
        assert CheckResult("x", CheckStatus.PASS,  "").icon == "✓"
        assert CheckResult("x", CheckStatus.WARN,  "").icon == "⚠"
        assert CheckResult("x", CheckStatus.FAIL,  "").icon == "✗"
        assert CheckResult("x", CheckStatus.SKIP,  "").icon == "–"

    def test_check_result_to_dict(self):
        c = CheckResult("Python version", CheckStatus.PASS, "3.12", fix_hint="")
        d = c.to_dict()
        assert d["name"] == "Python version"
        assert d["status"] == "pass"
        assert "message" in d

    def test_report_ok_when_no_failures(self):
        report = DoctorReport(checks=[
            CheckResult("a", CheckStatus.PASS, "ok"),
            CheckResult("b", CheckStatus.WARN, "warning"),
        ])
        assert report.ok is True

    def test_report_not_ok_when_failure(self):
        report = DoctorReport(checks=[
            CheckResult("a", CheckStatus.PASS, "ok"),
            CheckResult("b", CheckStatus.FAIL, "broken"),
        ])
        assert report.ok is False

    def test_report_failures_list(self):
        report = DoctorReport(checks=[
            CheckResult("a", CheckStatus.PASS, ""),
            CheckResult("b", CheckStatus.FAIL, "broken"),
        ])
        assert len(report.failures) == 1
        assert report.failures[0].name == "b"

    def test_report_warnings_list(self):
        report = DoctorReport(checks=[
            CheckResult("a", CheckStatus.WARN, "meh"),
            CheckResult("b", CheckStatus.PASS, "ok"),
        ])
        assert len(report.warnings) == 1

    def test_report_summary_string(self):
        report = DoctorReport(checks=[
            CheckResult("Python version", CheckStatus.PASS, "3.12"),
        ])
        s = report.summary()
        assert "Python version" in s
        assert "MeshFlow Doctor" in s

    def test_report_to_dict(self):
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        d = report.to_dict()
        assert "ok" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)

    def test_python_version_check_passes(self):
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        pv = next((c for c in report.checks if "Python" in c.name), None)
        assert pv is not None
        # We're running on Python 3.11+ so this should pass
        assert pv.status == CheckStatus.PASS

    def test_db_write_check_passes_with_memory(self):
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        db_check = next((c for c in report.checks if "Database" in c.name), None)
        assert db_check is not None
        assert db_check.status == CheckStatus.PASS

    def test_db_write_check_fails_with_bad_path(self, tmp_path):
        bad_path = str(tmp_path / "nonexistent_dir" / "runs.db")
        doc = Doctor(db_path=bad_path)
        report = doc.run()
        db_check = next((c for c in report.checks if "Database" in c.name), None)
        assert db_check is not None
        assert db_check.status == CheckStatus.FAIL

    def test_webhook_secret_warn_when_not_set(self, monkeypatch):
        monkeypatch.delenv("MESHFLOW_WEBHOOK_SECRET", raising=False)
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        ws = next((c for c in report.checks if "Webhook" in c.name), None)
        assert ws is not None
        assert ws.status == CheckStatus.WARN

    def test_webhook_secret_warn_when_default(self, monkeypatch):
        monkeypatch.setenv("MESHFLOW_WEBHOOK_SECRET", "change-me")
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        ws = next((c for c in report.checks if "Webhook" in c.name), None)
        assert ws is not None
        assert ws.status == CheckStatus.WARN

    def test_webhook_secret_pass_when_strong(self, monkeypatch):
        monkeypatch.setenv("MESHFLOW_WEBHOOK_SECRET", "a" * 40)
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        ws = next((c for c in report.checks if "Webhook" in c.name), None)
        assert ws is not None
        assert ws.status == CheckStatus.PASS

    def test_llm_provider_fail_when_no_keys(self, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                    "AWS_ACCESS_KEY_ID", "AZURE_OPENAI_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        llm = next((c for c in report.checks if "LLM" in c.name), None)
        assert llm is not None
        assert llm.status == CheckStatus.FAIL

    def test_llm_provider_pass_when_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        llm = next((c for c in report.checks if "LLM" in c.name), None)
        assert llm is not None
        assert llm.status == CheckStatus.PASS

    def test_policy_file_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("MESHFLOW_POLICY_FILE", raising=False)
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        pf = next((c for c in report.checks if "Policy" in c.name), None)
        assert pf is not None
        assert pf.status == CheckStatus.SKIP

    def test_policy_file_fail_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MESHFLOW_POLICY_FILE", str(tmp_path / "missing.yaml"))
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        pf = next((c for c in report.checks if "Policy" in c.name), None)
        assert pf is not None
        assert pf.status == CheckStatus.FAIL

    def test_policy_file_pass_when_valid_yaml(self, monkeypatch, tmp_path):
        policy = tmp_path / "policy.yaml"
        policy.write_text("rules:\n  - name: test\n    action: allow\n")
        monkeypatch.setenv("MESHFLOW_POLICY_FILE", str(policy))
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        pf = next((c for c in report.checks if "Policy" in c.name), None)
        assert pf is not None
        assert pf.status in (CheckStatus.PASS, CheckStatus.SKIP)  # SKIP if yaml not installed

    def test_disk_space_check_present(self):
        doc = Doctor(db_path=":memory:", data_dir=".")
        report = doc.run()
        ds = next((c for c in report.checks if "Disk" in c.name), None)
        assert ds is not None
        assert ds.status in (CheckStatus.PASS, CheckStatus.WARN, CheckStatus.SKIP)

    def test_dependencies_check_present(self):
        doc = Doctor(db_path=":memory:")
        report = doc.run()
        dep = next((c for c in report.checks if "Dependencies" in c.name), None)
        assert dep is not None


# ══════════════════════════════════════════════════════════════════════════════
#  EnvGenerator
# ══════════════════════════════════════════════════════════════════════════════

class TestEnvGenerator:

    def test_render_returns_string(self):
        gen = EnvGenerator()
        out = gen.render()
        assert isinstance(out, str)
        assert len(out) > 0

    def test_render_contains_all_sections(self):
        gen = EnvGenerator()
        out = gen.render()
        for section in ("LLM Providers", "Server", "Security", "Persistence",
                        "Policy", "Observability"):
            assert section in out

    def test_render_contains_key_vars(self):
        gen = EnvGenerator()
        out = gen.render()
        for key in ("ANTHROPIC_API_KEY", "MESHFLOW_HOST", "MESHFLOW_PORT",
                    "MESHFLOW_WEBHOOK_SECRET"):
            assert key in out

    def test_render_uses_env_values(self, monkeypatch):
        monkeypatch.setenv("MESHFLOW_PORT", "9999")
        gen = EnvGenerator()
        out = gen.render()
        assert "MESHFLOW_PORT=9999" in out

    def test_render_override_wins(self, monkeypatch):
        monkeypatch.setenv("MESHFLOW_PORT", "8000")
        gen = EnvGenerator()
        gen.set("MESHFLOW_PORT", "7777")
        out = gen.render()
        assert "MESHFLOW_PORT=7777" in out

    def test_render_auto_generates_webhook_secret(self, monkeypatch):
        monkeypatch.delenv("MESHFLOW_WEBHOOK_SECRET", raising=False)
        gen = EnvGenerator(auto_generate_secrets=True)
        out = gen.render()
        # Should have a generated value, not empty
        line = next(l for l in out.splitlines() if l.startswith("MESHFLOW_WEBHOOK_SECRET="))
        value = line.split("=", 1)[1]
        assert len(value) >= 32

    def test_render_no_auto_generate_leaves_empty(self, monkeypatch):
        monkeypatch.delenv("MESHFLOW_WEBHOOK_SECRET", raising=False)
        gen = EnvGenerator(auto_generate_secrets=False)
        out = gen.render()
        line = next((l for l in out.splitlines() if l.startswith("MESHFLOW_WEBHOOK_SECRET=")), "")
        value = line.split("=", 1)[1] if "=" in line else ""
        assert value == ""

    def test_write_creates_file(self, tmp_path):
        gen = EnvGenerator()
        path = str(tmp_path / ".env")
        gen.write(path)
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "MESHFLOW_HOST" in content

    def test_write_raises_if_exists_no_overwrite(self, tmp_path):
        gen = EnvGenerator()
        path = str(tmp_path / ".env")
        gen.write(path)
        with pytest.raises(FileExistsError):
            gen.write(path, overwrite=False)

    def test_write_overwrites_when_flag_set(self, tmp_path):
        gen = EnvGenerator()
        path = str(tmp_path / ".env")
        gen.write(path)
        gen.set("MESHFLOW_PORT", "9999")
        gen.write(path, overwrite=True)
        with open(path) as f:
            content = f.read()
        assert "9999" in content

    def test_validate_nonexistent_file_returns_error(self):
        gen = EnvGenerator()
        issues = gen.validate("/nonexistent/path/.env")
        assert any(i.severity == "error" for i in issues)

    def test_validate_valid_file_no_errors(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MESHFLOW_WEBHOOK_SECRET", raising=False)
        gen = EnvGenerator()
        path = str(tmp_path / ".env")
        gen.write(path)
        # Inject a proper webhook secret so it passes validation
        with open(path, "a") as f:
            f.write(f"\nMESHFLOW_WEBHOOK_SECRET={'x' * 40}\n")
        issues = gen.validate(path)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

    def test_validate_insecure_secret_warns(self, tmp_path):
        path = str(tmp_path / ".env")
        with open(path, "w") as f:
            f.write("MESHFLOW_WEBHOOK_SECRET=change-me\n")
        gen = EnvGenerator()
        issues = gen.validate(path)
        assert any("MESHFLOW_WEBHOOK_SECRET" in str(i) for i in issues)

    def test_validate_unknown_var_warns(self, tmp_path):
        path = str(tmp_path / ".env")
        with open(path, "w") as f:
            f.write("TOTALLY_UNKNOWN_VAR=foo\n")
        gen = EnvGenerator()
        issues = gen.validate(path)
        assert any("TOTALLY_UNKNOWN_VAR" in str(i) for i in issues)

    def test_validation_issue_str(self):
        issue = ValidationIssue("MY_KEY", "error", "is missing")
        assert "MY_KEY" in str(issue)
        assert "ERROR" in str(issue)

    def test_set_returns_self_for_chaining(self):
        gen = EnvGenerator()
        result = gen.set("MESHFLOW_PORT", "9000")
        assert result is gen


# ══════════════════════════════════════════════════════════════════════════════
#  DockerDeployer (no Docker required — tests mock or check graceful failure)
# ══════════════════════════════════════════════════════════════════════════════

class TestDockerDeployer:

    def test_deploy_result_fields(self):
        r = DeployResult(ok=True, command="docker build", stdout="sha256:abc",
                         stderr="", duration_ms=1234.5, image_id="abc123")
        assert r.ok is True
        assert r.image_id == "abc123"
        assert r.duration_ms == pytest.approx(1234.5)

    def test_deploy_result_to_dict(self):
        r = DeployResult(ok=False, command="docker run", stdout="",
                         stderr="error", duration_ms=100.0, error="daemon not running")
        d = r.to_dict()
        assert d["ok"] is False
        assert "error" in d
        assert "command" in d

    def test_deployer_find_dockerfile(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.12\n")
        dep = DockerDeployer(context=str(tmp_path))
        assert dep._dockerfile.endswith("Dockerfile")

    def test_deployer_no_dockerfile_empty_string(self, tmp_path):
        dep = DockerDeployer(context=str(tmp_path))
        assert dep._dockerfile == ""

    def test_build_fails_gracefully_without_docker(self, monkeypatch):
        """When docker is not in PATH, build() returns ok=False with a clear error."""
        import shutil
        original = shutil.which

        def no_docker(name, *args, **kwargs):
            if name == "docker":
                return None
            return original(name, *args, **kwargs)

        monkeypatch.setattr(shutil, "which", no_docker)
        dep = DockerDeployer()
        result = dep.build()
        assert result.ok is False
        assert "docker" in result.error.lower()

    def test_run_fails_gracefully_without_docker(self, monkeypatch):
        import shutil
        original = shutil.which

        def no_docker(name, *args, **kwargs):
            if name == "docker":
                return None
            return original(name, *args, **kwargs)

        monkeypatch.setattr(shutil, "which", no_docker)
        dep = DockerDeployer()
        result = dep.run()
        assert result.ok is False

    def test_compose_up_fails_gracefully_without_compose(self, monkeypatch):
        import shutil
        import subprocess

        monkeypatch.setattr(shutil, "which", lambda name: None)

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("docker not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        dep = DockerDeployer()
        result = dep.compose_up()
        assert result.ok is False

    def test_status_without_docker_returns_not_running(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)
        dep = DockerDeployer()
        st = dep.status()
        assert st["running"] is False

    def test_deployer_tag_default(self):
        dep = DockerDeployer()
        assert dep._tag == "meshflow:latest"

    def test_deployer_custom_tag(self):
        dep = DockerDeployer(tag="myrepo/meshflow:v1.2.3")
        assert dep._tag == "myrepo/meshflow:v1.2.3"


# ══════════════════════════════════════════════════════════════════════════════
#  Helm chart
# ══════════════════════════════════════════════════════════════════════════════

class TestHelmChart:

    def _chart_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), "helm", "meshflow")

    def test_chart_yaml_exists(self):
        assert os.path.exists(os.path.join(self._chart_dir(), "Chart.yaml"))

    def test_values_yaml_exists(self):
        assert os.path.exists(os.path.join(self._chart_dir(), "values.yaml"))

    def test_deployment_template_exists(self):
        assert os.path.exists(
            os.path.join(self._chart_dir(), "templates", "deployment.yaml")
        )

    def test_service_template_exists(self):
        assert os.path.exists(
            os.path.join(self._chart_dir(), "templates", "service.yaml")
        )

    def test_pvc_template_exists(self):
        assert os.path.exists(
            os.path.join(self._chart_dir(), "templates", "pvc.yaml")
        )

    def test_secret_template_exists(self):
        assert os.path.exists(
            os.path.join(self._chart_dir(), "templates", "secret.yaml")
        )

    def test_chart_yaml_has_name(self):
        import yaml as _yaml  # type: ignore[import]
        pytest.importorskip("yaml")
        with open(os.path.join(self._chart_dir(), "Chart.yaml")) as f:
            chart = _yaml.safe_load(f)
        assert chart["name"] == "meshflow"
        assert "version" in chart
        assert "appVersion" in chart

    def test_values_has_required_sections(self):
        import yaml as _yaml  # type: ignore[import]
        pytest.importorskip("yaml")
        with open(os.path.join(self._chart_dir(), "values.yaml")) as f:
            vals = _yaml.safe_load(f)
        for key in ("image", "service", "persistence", "resources",
                    "livenessProbe", "readinessProbe"):
            assert key in vals, f"missing key: {key}"

    def test_deployment_template_has_liveness_probe(self):
        with open(os.path.join(self._chart_dir(), "templates", "deployment.yaml")) as f:
            content = f.read()
        assert "livenessProbe" in content

    def test_deployment_template_has_persistence(self):
        with open(os.path.join(self._chart_dir(), "templates", "deployment.yaml")) as f:
            content = f.read()
        assert "persistentVolumeClaim" in content


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_deploy_classes_exported(self):
        for sym in ("Doctor", "DoctorReport", "CheckResult", "CheckStatus",
                    "EnvGenerator", "ValidationIssue",
                    "DockerDeployer", "DeployResult"):
            assert hasattr(meshflow, sym), f"missing: {sym}"

    def test_all_in___all__(self):
        for sym in ("Doctor", "DoctorReport", "CheckResult", "CheckStatus",
                    "EnvGenerator", "ValidationIssue",
                    "DockerDeployer", "DeployResult"):
            assert sym in meshflow.__all__, f"{sym} missing from __all__"

    def test_version_bumped(self):
        assert meshflow.__version__ >= "0.77.0"
