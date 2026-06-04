"""Tests for cloud deployment targets (meshflow.deploy.targets)."""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.deploy.targets import (
    CloudDeployResult,
    AWSFargateTarget,
    AzureContainerAppsTarget,
    GCPCloudRunTarget,
    RailwayTarget,
    FlyTarget,
    KubernetesTarget,
    deploy,
    generate_iac,
    SUPPORTED_TARGETS,
)


# ── CloudDeployResult ──────────────────────────────────────────────────────────

class TestCloudDeployResult:
    def test_str_ok(self) -> None:
        r = CloudDeployResult(ok=True, target="gcp", name="my-svc", region="us-central1",
                              service_url="https://my-svc.run.app",
                              resource_id="my-svc-id", estimated_cost="$0")
        s = str(r)
        assert "✅" in s
        assert "gcp" in s
        assert "https://my-svc.run.app" in s

    def test_str_failed(self) -> None:
        r = CloudDeployResult(ok=False, target="aws", name="svc", region="us-east-1",
                              error="CLI not found")
        s = str(r)
        assert "❌" in s
        assert "CLI not found" in s

    def test_to_dict(self) -> None:
        r = CloudDeployResult(ok=True, target="fly", name="my-app", region="iad",
                              service_url="https://my-app.fly.dev")
        d = r.to_dict()
        assert d["ok"] is True
        assert d["target"] == "fly"
        assert d["service_url"] == "https://my-app.fly.dev"


# ── SUPPORTED_TARGETS ──────────────────────────────────────────────────────────

class TestSupportedTargets:
    def test_all_targets_listed(self) -> None:
        for t in ("aws", "azure", "gcp", "railway", "fly", "k8s"):
            assert t in SUPPORTED_TARGETS

    def test_unknown_target_returns_error(self) -> None:
        r = deploy(target="digitalocean", image="img:1.0", name="svc")
        assert r.ok is False
        assert "Unknown target" in r.error
        assert "digitalocean" in r.error


# ── IaC generation (no CLI required) ──────────────────────────────────────────

class TestIaCGeneration:
    IMAGE = "ghcr.io/anteneh-t-tessema/meshflow-mcp:1.13.0"
    ENV   = {"ANTHROPIC_API_KEY": "sk-ant-test", "MESHFLOW_DEFAULT_POLICY": "standard"}

    def test_aws_cloudformation(self) -> None:
        iac = AWSFargateTarget.lambda_template("my-fn", self.IMAGE, self.ENV)
        assert "AWS::Lambda::Function" in iac
        assert self.IMAGE in iac
        assert "ANTHROPIC_API_KEY" in iac

    def test_azure_bicep(self) -> None:
        iac = AzureContainerAppsTarget.bicep_template("my-app", self.IMAGE, self.ENV)
        assert "containerApps" in iac or "Microsoft.App" in iac
        assert self.IMAGE in iac

    def test_gcp_terraform(self) -> None:
        iac = GCPCloudRunTarget.terraform_template("my-svc", self.IMAGE, self.ENV,
                                                   "my-project", "us-central1")
        assert "google_cloud_run" in iac
        assert self.IMAGE in iac
        assert "us-central1" in iac

    def test_fly_toml(self) -> None:
        iac = FlyTarget.fly_toml("my-app", self.IMAGE, self.ENV, "iad")
        assert "[http_service]" in iac
        assert self.IMAGE in iac
        assert "auto_stop_machines" in iac

    def test_k8s_manifest(self) -> None:
        iac = KubernetesTarget.k8s_manifest("my-svc", self.IMAGE, "meshflow", 2, self.ENV)
        assert "kind: Deployment" in iac
        assert "kind: Service" in iac
        assert self.IMAGE in iac
        assert "replicas: 2" in iac

    def test_generate_iac_dispatch(self) -> None:
        for target in ("aws", "azure", "gcp", "fly", "k8s"):
            iac = generate_iac(target, self.IMAGE, name="svc", env=self.ENV,
                               project="proj", region="us-central1")
            assert len(iac) > 100, f"IaC for {target} is too short"
            assert "svc" in iac or target in iac.lower()

    def test_generate_iac_cli_flag(self) -> None:
        iac = generate_iac("k8s", self.IMAGE, name="meshflow-prod")
        assert "meshflow-prod" in iac

    def test_generate_iac_unknown_target(self) -> None:
        iac = generate_iac("heroku", self.IMAGE)
        assert "not available" in iac


# ── Deploy with missing CLI (graceful failure) ────────────────────────────────

class TestDeployMissingCLI:
    IMAGE = "ghcr.io/anteneh-t-tessema/meshflow-mcp:1.13.0"

    def _patch_which(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meshflow.deploy.targets.shutil.which", lambda _: None)

    def test_aws_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_which(monkeypatch)
        r = deploy("aws", self.IMAGE, name="svc")
        assert r.ok is False
        assert "AWS CLI" in r.error

    def test_azure_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_which(monkeypatch)
        r = deploy("azure", self.IMAGE, name="svc")
        assert r.ok is False
        assert "Azure CLI" in r.error

    def test_gcp_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_which(monkeypatch)
        r = deploy("gcp", self.IMAGE, name="svc")
        assert r.ok is False
        assert "gcloud" in r.error

    def test_railway_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_which(monkeypatch)
        r = deploy("railway", self.IMAGE, name="svc")
        assert r.ok is False
        assert "Railway CLI" in r.error

    def test_fly_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_which(monkeypatch)
        r = deploy("fly", self.IMAGE, name="svc")
        assert r.ok is False
        assert "flyctl" in r.error

    def test_k8s_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_which(monkeypatch)
        r = deploy("k8s", self.IMAGE, name="svc")
        assert r.ok is False
        assert "kubectl" in r.error


# ── deploy() default region logic ─────────────────────────────────────────────

class TestDefaultRegions:
    def test_aws_default_region(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, str] = {}
        def fake_deploy(self2, image, name, region, **kw):  # type: ignore
            captured["region"] = region
            return CloudDeployResult(ok=True, target="aws", name=name, region=region)
        monkeypatch.setattr(AWSFargateTarget, "deploy", fake_deploy)
        deploy("aws", "img:1", name="svc")
        assert captured["region"] == "us-east-1"

    def test_gcp_default_region(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, str] = {}
        def fake_deploy(self2, image, name, region, **kw):  # type: ignore
            captured["region"] = region
            return CloudDeployResult(ok=True, target="gcp", name=name, region=region)
        monkeypatch.setattr(GCPCloudRunTarget, "deploy", fake_deploy)
        deploy("gcp", "img:1", name="svc")
        assert captured["region"] == "us-central1"
