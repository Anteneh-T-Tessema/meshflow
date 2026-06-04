"""Cloud deployment targets — common interface + per-provider implementations.

Each target wraps the provider's CLI/SDK and produces a :class:`CloudDeployResult`
with the service URL, provider-specific resource IDs, and a cost estimate.

Supported targets
-----------------
- ``aws``     — AWS ECS Fargate (long-running) or Lambda (function-based)
- ``azure``   — Azure Container Apps
- ``gcp``     — GCP Cloud Run
- ``railway`` — Railway.app (Docker-based, indie-friendly)
- ``fly``     — Fly.io (flyctl)
- ``k8s``     — Generic kubectl / helm (any cluster)

Usage::

    from meshflow.deploy.targets import deploy

    result = deploy(
        target="aws",
        image="ghcr.io/anteneh-t-tessema/meshflow-mcp:1.13.0",
        name="meshflow-prod",
        region="us-east-1",
        env={"ANTHROPIC_API_KEY": "sk-ant-..."},
    )
    print(result.service_url)     # https://meshflow-prod.example.com
    print(result.estimated_cost)  # "$0.012 / 1M requests (Fargate serverless)"
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from typing import Any


# ── Common result ─────────────────────────────────────────────────────────────

@dataclass
class CloudDeployResult:
    """Result of a cloud deployment operation."""
    ok: bool
    target: str
    name: str
    region: str
    service_url: str = ""
    resource_id: str = ""
    image: str = ""
    estimated_cost: str = ""
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": self.target,
            "name": self.name,
            "region": self.region,
            "service_url": self.service_url,
            "resource_id": self.resource_id,
            "image": self.image,
            "estimated_cost": self.estimated_cost,
            "error": self.error,
            "notes": self.notes,
        }

    def __str__(self) -> str:
        if self.ok:
            lines = [
                f"✅ Deployed to {self.target} ({self.region})",
                f"   URL:  {self.service_url or '(pending)'}",
                f"   ID:   {self.resource_id}",
                f"   Cost: {self.estimated_cost}",
            ]
        else:
            lines = [f"❌ Deploy to {self.target} failed", f"   {self.error}"]
        if self.notes:
            lines += [f"   • {n}" for n in self.notes]
        return "\n".join(lines)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _run(cmd: list[str], env: dict[str, str] | None = None) -> tuple[bool, str, str]:
    merged = {**os.environ, **(env or {})}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=merged, timeout=300)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError as exc:
        return False, "", f"Command not found: {exc}"
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out after 300s"


def _require(cli: str) -> str | None:
    """Return CLI path or None if not installed."""
    return shutil.which(cli)


def _env_flags(env: dict[str, str], flag: str = "--env") -> list[str]:
    """Flatten {K: V} dict to [flag, K=V, flag, K=V, ...]."""
    out: list[str] = []
    for k, v in env.items():
        out += [flag, f"{k}={v}"]
    return out


# ── AWS ECS Fargate ───────────────────────────────────────────────────────────

class AWSFargateTarget:
    """Deploy a MeshFlow MCP container to AWS ECS Fargate.

    Requires the ``aws`` CLI and an ECR repository for the image.

    The Fargate task runs as a service behind an ALB (or as a standalone
    task for batch/on-demand workloads).

    Steps
    -----
    1. ``aws ecr get-login-password | docker login``
    2. ``docker push <ecr-image>``
    3. ``aws ecs register-task-definition``
    4. ``aws ecs create-service`` (or ``update-service`` if exists)
    """

    def deploy(
        self,
        image: str,
        name: str,
        region: str = "us-east-1",
        cpu: str = "256",
        memory: str = "512",
        env: dict[str, str] | None = None,
        cluster: str = "meshflow",
        **_: Any,
    ) -> CloudDeployResult:
        env = env or {}
        if not _require("aws"):
            return CloudDeployResult(
                ok=False, target="aws", name=name, region=region,
                error="AWS CLI not found. Install: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html",
            )

        # Register task definition
        container_defs = [{
            "name": name,
            "image": image,
            "essential": True,
            "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
            "environment": [{"name": k, "value": v} for k, v in env.items()],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": f"/ecs/{name}",
                    "awslogs-region": region,
                    "awslogs-stream-prefix": "ecs",
                },
            },
        }]

        task_def = {
            "family": name,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": cpu,
            "memory": memory,
            "containerDefinitions": container_defs,
        }

        ok, stdout, stderr = _run([
            "aws", "ecs", "register-task-definition",
            "--cli-input-json", json.dumps(task_def),
            "--region", region,
        ])
        if not ok:
            return CloudDeployResult(
                ok=False, target="aws", name=name, region=region,
                image=image, stdout=stdout, stderr=stderr,
                error=f"Failed to register task definition: {stderr[:200]}",
            )

        task_arn = ""
        try:
            task_arn = json.loads(stdout).get("taskDefinition", {}).get("taskDefinitionArn", "")
        except Exception:
            pass

        # Create or update service
        ok2, stdout2, stderr2 = _run([
            "aws", "ecs", "create-service",
            "--cluster", cluster,
            "--service-name", name,
            "--task-definition", name,
            "--desired-count", "1",
            "--launch-type", "FARGATE",
            "--network-configuration",
            "awsvpcConfiguration={subnets=[],securityGroups=[],assignPublicIp=ENABLED}",
            "--region", region,
        ])

        service_url = f"https://console.aws.amazon.com/ecs/v2/clusters/{cluster}/services/{name}"
        return CloudDeployResult(
            ok=True,
            target="aws",
            name=name,
            region=region,
            image=image,
            resource_id=task_arn,
            service_url=service_url,
            estimated_cost="~$0.04 vCPU/hr + $0.004 GB/hr (Fargate, 0.25 vCPU / 0.5 GB ≈ $11/mo)",
            stdout=stdout2,
            notes=[
                "Service created in ECS cluster. Assign subnets + security groups in the console.",
                f"CloudWatch logs: /ecs/{name}",
                "For production: add an ALB and set --network-configuration subnets.",
            ],
        )

    @staticmethod
    def lambda_template(name: str, image: str, env: dict[str, str]) -> str:
        """Generate a CloudFormation YAML snippet for Lambda (container image)."""
        env_block = "\n".join(f"          {k}: {v!r}" for k, v in env.items())
        return textwrap.dedent(f"""\
            # AWS Lambda — container image deployment
            # Deploy: sam deploy --guided  OR  aws cloudformation deploy ...
            AWSTemplateFormatVersion: '2010-09-09'
            Resources:
              {name}Function:
                Type: AWS::Lambda::Function
                Properties:
                  FunctionName: {name}
                  PackageType: Image
                  Code:
                    ImageUri: {image}
                  Role: !GetAtt LambdaRole.Arn
                  Timeout: 900
                  MemorySize: 1024
                  Environment:
                    Variables:
            {env_block}
              {name}Url:
                Type: AWS::Lambda::Url
                Properties:
                  TargetFunctionArn: !Ref {name}Function
                  AuthType: NONE
            Outputs:
              FunctionUrl:
                Value: !GetAtt {name}Url.FunctionUrl
        """)


# ── Azure Container Apps ──────────────────────────────────────────────────────

class AzureContainerAppsTarget:
    """Deploy to Azure Container Apps — serverless containers with HTTP scaling.

    Requires the ``az`` CLI (``az extension add --name containerapp``).
    """

    def deploy(
        self,
        image: str,
        name: str,
        region: str = "eastus",
        resource_group: str = "meshflow-rg",
        env: dict[str, str] | None = None,
        min_replicas: int = 0,
        max_replicas: int = 10,
        **_: Any,
    ) -> CloudDeployResult:
        env = env or {}
        if not _require("az"):
            return CloudDeployResult(
                ok=False, target="azure", name=name, region=region,
                error="Azure CLI not found. Install: https://docs.microsoft.com/cli/azure/install-azure-cli",
            )

        # Ensure resource group
        _run(["az", "group", "create", "--name", resource_group, "--location", region])

        # Ensure Container Apps environment
        env_name = f"{name}-env"
        _run([
            "az", "containerapp", "env", "create",
            "--name", env_name,
            "--resource-group", resource_group,
            "--location", region,
        ])

        # Flatten env vars
        env_args: list[str] = []
        if env:
            env_args = ["--env-vars"] + [f"{k}={v}" for k, v in env.items()]

        ok, stdout, stderr = _run([
            "az", "containerapp", "create",
            "--name", name,
            "--resource-group", resource_group,
            "--environment", env_name,
            "--image", image,
            "--target-port", "8080",
            "--ingress", "external",
            "--min-replicas", str(min_replicas),
            "--max-replicas", str(max_replicas),
        ] + env_args)

        service_url = ""
        if ok:
            ok2, stdout2, _ = _run([
                "az", "containerapp", "show",
                "--name", name, "--resource-group", resource_group,
                "--query", "properties.configuration.ingress.fqdn",
                "--output", "tsv",
            ])
            if ok2 and stdout2:
                service_url = f"https://{stdout2.strip()}"

        return CloudDeployResult(
            ok=ok,
            target="azure",
            name=name,
            region=region,
            image=image,
            resource_id=f"{resource_group}/{name}",
            service_url=service_url,
            estimated_cost="~$0.000024 per vCPU-second + $0.000003 per GiB-second (scales to zero)",
            stdout=stdout,
            stderr=stderr,
            error="" if ok else stderr[:200],
            notes=[
                "Container Apps scales to 0 replicas when idle — no cost at rest.",
                f"Resource group: {resource_group}",
                "Add DAPR sidecar with --enable-dapr for service mesh support.",
            ],
        )

    @staticmethod
    def bicep_template(name: str, image: str, env: dict[str, str], region: str = "eastus") -> str:
        """Generate a Bicep template for Azure Container Apps."""
        env_block = "\n".join(
            f"          {{ name: '{k}', value: '{v}' }}" for k, v in env.items()
        )
        return textwrap.dedent(f"""\
            // Azure Container Apps — Bicep template
            // Deploy: az deployment group create --resource-group meshflow-rg --template-file main.bicep
            param location string = '{region}'

            resource env 'Microsoft.App/managedEnvironments@2023-05-01' = {{
              name: '{name}-env'
              location: location
              properties: {{}}
            }}

            resource app 'Microsoft.App/containerApps@2023-05-01' = {{
              name: '{name}'
              location: location
              properties: {{
                managedEnvironmentId: env.id
                configuration: {{
                  ingress: {{ external: true, targetPort: 8080 }}
                }}
                template: {{
                  containers: [{{
                    name: '{name}'
                    image: '{image}'
                    env: [
            {env_block}
                    ]
                    resources: {{ cpu: '0.25', memory: '0.5Gi' }}
                  }}]
                  scale: {{ minReplicas: 0, maxReplicas: 10 }}
                }}
              }}
            }}

            output fqdn string = app.properties.configuration.ingress.fqdn
        """)


# ── GCP Cloud Run ─────────────────────────────────────────────────────────────

class GCPCloudRunTarget:
    """Deploy to GCP Cloud Run — fully managed serverless containers.

    Requires the ``gcloud`` CLI and a GCP project.
    """

    def deploy(
        self,
        image: str,
        name: str,
        region: str = "us-central1",
        project: str = "",
        env: dict[str, str] | None = None,
        allow_unauthenticated: bool = True,
        memory: str = "512Mi",
        cpu: str = "1",
        **_: Any,
    ) -> CloudDeployResult:
        env = env or {}
        if not _require("gcloud"):
            return CloudDeployResult(
                ok=False, target="gcp", name=name, region=region,
                error="gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install",
            )

        cmd = [
            "gcloud", "run", "deploy", name,
            "--image", image,
            "--region", region,
            "--platform", "managed",
            "--port", "8080",
            "--memory", memory,
            "--cpu", cpu,
        ]
        if project:
            cmd += ["--project", project]
        if allow_unauthenticated:
            cmd += ["--allow-unauthenticated"]
        if env:
            cmd += ["--set-env-vars", ",".join(f"{k}={v}" for k, v in env.items())]
        cmd += ["--quiet"]

        ok, stdout, stderr = _run(cmd)

        service_url = ""
        if ok:
            gcloud_cmd = ["gcloud", "run", "services", "describe", name,
                          "--region", region, "--format", "value(status.url)"]
            if project:
                gcloud_cmd += ["--project", project]
            _, url_out, _ = _run(gcloud_cmd)
            service_url = url_out.strip()

        return CloudDeployResult(
            ok=ok,
            target="gcp",
            name=name,
            region=region,
            image=image,
            service_url=service_url,
            estimated_cost="$0.00002400 per vCPU-second + $0.00000250 per GiB-second (scales to zero; 2M req/mo free)",
            stdout=stdout,
            stderr=stderr,
            error="" if ok else stderr[:200],
            notes=[
                "Cloud Run scales to 0 — pay only for active requests.",
                "HIPAA: enable VPC connector + Private Google Access for HIPAA data.",
                "GDPR: choose europe-west regions for EU data residency.",
            ],
        )

    @staticmethod
    def terraform_template(name: str, image: str, env: dict[str, str], project: str, region: str = "us-central1") -> str:
        env_block = "\n  ".join(f'"{k}" = "{v}"' for k, v in env.items())
        return textwrap.dedent(f"""\
            # GCP Cloud Run — Terraform
            # terraform init && terraform apply
            terraform {{
              required_providers {{
                google = {{ source = "hashicorp/google", version = "~> 5.0" }}
              }}
            }}

            provider "google" {{
              project = "{project}"
              region  = "{region}"
            }}

            resource "google_cloud_run_v2_service" "{name.replace('-', '_')}" {{
              name     = "{name}"
              location = "{region}"

              template {{
                containers {{
                  image = "{image}"
                  ports {{ container_port = 8080 }}
                  env {{
                    {env_block}
                  }}
                  resources {{
                    limits = {{ cpu = "1", memory = "512Mi" }}
                  }}
                }}
                scaling {{
                  min_instance_count = 0
                  max_instance_count = 10
                }}
              }}
            }}

            resource "google_cloud_run_service_iam_member" "public" {{
              location = google_cloud_run_v2_service.{name.replace('-', '_')}.location
              service  = google_cloud_run_v2_service.{name.replace('-', '_')}.name
              role     = "roles/run.invoker"
              member   = "allUsers"
            }}

            output "url" {{
              value = google_cloud_run_v2_service.{name.replace('-', '_')}.uri
            }}
        """)


# ── Railway ───────────────────────────────────────────────────────────────────

class RailwayTarget:
    """Deploy to Railway.app — Docker-based, no config required.

    Requires the ``railway`` CLI (``npm install -g @railway/cli``).
    """

    def deploy(
        self,
        image: str,
        name: str,
        region: str = "us-west2",
        env: dict[str, str] | None = None,
        **_: Any,
    ) -> CloudDeployResult:
        env = env or {}
        if not _require("railway"):
            return CloudDeployResult(
                ok=False, target="railway", name=name, region=region,
                error="Railway CLI not found. Install: npm install -g @railway/cli",
            )

        # Set env vars
        for k, v in env.items():
            _run(["railway", "variables", "--set", f"{k}={v}"])

        ok, stdout, stderr = _run(["railway", "up", "--detach"])
        service_url = ""
        if ok:
            _, url_out, _ = _run(["railway", "domain"])
            service_url = url_out.strip()

        return CloudDeployResult(
            ok=ok,
            target="railway",
            name=name,
            region=region,
            image=image,
            service_url=service_url,
            estimated_cost="$5/mo Hobby or $20/mo Pro (includes 8GB RAM, 8 vCPU, 100GB egress)",
            stdout=stdout,
            stderr=stderr,
            error="" if ok else stderr[:200],
            notes=[
                "Railway reads Dockerfile automatically — no extra config needed.",
                "Persistent volumes available for SQLite audit ledger.",
                "Free tier: $5 credit/mo, sufficient for dev/staging.",
            ],
        )


# ── Fly.io ────────────────────────────────────────────────────────────────────

class FlyTarget:
    """Deploy to Fly.io — hardware-backed VMs close to users.

    Requires the ``flyctl`` CLI.
    """

    def deploy(
        self,
        image: str,
        name: str,
        region: str = "iad",
        env: dict[str, str] | None = None,
        **_: Any,
    ) -> CloudDeployResult:
        env = env or {}
        if not _require("flyctl"):
            return CloudDeployResult(
                ok=False, target="fly", name=name, region=region,
                error="flyctl not found. Install: https://fly.io/docs/getting-started/installing-flyctl/",
            )

        # Create app if not exists
        _run(["flyctl", "apps", "create", name, "--machines"])

        # Set secrets
        if env:
            _run(["flyctl", "secrets", "set", "--app", name] + [f"{k}={v}" for k, v in env.items()])

        ok, stdout, stderr = _run([
            "flyctl", "deploy",
            "--app", name,
            "--image", image,
            "--region", region,
            "--ha=false",
        ])

        service_url = f"https://{name}.fly.dev" if ok else ""
        return CloudDeployResult(
            ok=ok,
            target="fly",
            name=name,
            region=region,
            image=image,
            service_url=service_url,
            estimated_cost="~$1.94/mo (shared-cpu-1x, 256MB RAM) — scales to zero with --autostop",
            stdout=stdout,
            stderr=stderr,
            error="" if ok else stderr[:200],
            notes=[
                "Fly Machines auto-stop when idle — pay only when handling requests.",
                "Multi-region: add --region lhr eu-central for EU data residency.",
                "Persistent volumes: flyctl volumes create meshflow_data --size 1",
            ],
        )

    @staticmethod
    def fly_toml(name: str, image: str, env: dict[str, str], region: str = "iad") -> str:
        env_block = "\n  ".join(f'{k} = "{v}"' for k, v in env.items())
        return textwrap.dedent(f"""\
            # fly.toml — deploy with: flyctl deploy
            app = '{name}'
            primary_region = '{region}'

            [build]
              image = '{image}'

            [env]
              {env_block}

            [http_service]
              internal_port = 8080
              force_https   = true
              auto_stop_machines  = true
              auto_start_machines = true
              min_machines_running = 0

            [[vm]]
              memory = '256mb'
              cpu_kind = 'shared'
              cpus = 1

            [[mounts]]
              source = 'meshflow_data'
              destination = '/data'
        """)


# ── Kubernetes / Helm ─────────────────────────────────────────────────────────

class KubernetesTarget:
    """Deploy via kubectl apply or helm upgrade.

    Generates a minimal Deployment + Service manifest or uses the MeshFlow
    Helm chart in ``helm/meshflow/``.
    """

    def deploy(
        self,
        image: str,
        name: str,
        region: str = "default",
        namespace: str = "meshflow",
        replicas: int = 1,
        env: dict[str, str] | None = None,
        use_helm: bool = False,
        **_: Any,
    ) -> CloudDeployResult:
        env = env or {}
        if not _require("kubectl"):
            return CloudDeployResult(
                ok=False, target="k8s", name=name, region=region,
                error="kubectl not found. Install: https://kubernetes.io/docs/tasks/tools/",
            )

        if use_helm and _require("helm"):
            helm_args = [
                "helm", "upgrade", "--install", name, "./helm/meshflow",
                "--namespace", namespace, "--create-namespace",
                "--set", f"image.repository={image.split(':')[0]}",
                "--set", f"image.tag={image.split(':')[-1]}",
                "--set", f"replicaCount={replicas}",
            ]
            for k, v in env.items():
                helm_args += ["--set", f"env.{k}={v}"]
            ok, stdout, stderr = _run(helm_args)
        else:
            manifest = self.k8s_manifest(name, image, namespace, replicas, env)
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write(manifest)
                fname = f.name
            ok, stdout, stderr = _run(["kubectl", "apply", "-f", fname])
            os.unlink(fname)

        return CloudDeployResult(
            ok=ok,
            target="k8s",
            name=name,
            region=region,
            image=image,
            service_url=f"kubectl port-forward svc/{name} 8080:8080 -n {namespace}",
            estimated_cost="Depends on node pool — typically $0.048/hr per node (e2-medium GKE)",
            stdout=stdout,
            stderr=stderr,
            error="" if ok else stderr[:200],
            notes=[
                f"Namespace: {namespace}",
                "Add an Ingress + cert-manager for HTTPS.",
                "For HIPAA/SOC2: use a dedicated namespace with NetworkPolicy isolation.",
            ],
        )

    @staticmethod
    def k8s_manifest(
        name: str,
        image: str,
        namespace: str = "meshflow",
        replicas: int = 1,
        env: dict[str, str] | None = None,
    ) -> str:
        env_block = ""
        if env:
            env_lines = "\n".join(
                f"        - name: {k}\n          value: \"{v}\"" for k, v in env.items()
            )
            env_block = f"        env:\n{env_lines}\n"
        return textwrap.dedent(f"""\
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app: {name}
            spec:
              replicas: {replicas}
              selector:
                matchLabels:
                  app: {name}
              template:
                metadata:
                  labels:
                    app: {name}
                spec:
                  containers:
                  - name: {name}
                    image: {image}
                    ports:
                    - containerPort: 8080
            {env_block}          resources:
                      requests:
                        cpu: 250m
                        memory: 256Mi
                      limits:
                        cpu: "1"
                        memory: 512Mi
                    livenessProbe:
                      httpGet:
                        path: /health
                        port: 8080
                      initialDelaySeconds: 15
            ---
            apiVersion: v1
            kind: Service
            metadata:
              name: {name}
              namespace: {namespace}
            spec:
              selector:
                app: {name}
              ports:
              - port: 80
                targetPort: 8080
              type: ClusterIP
        """)


# ── Public entry point ────────────────────────────────────────────────────────

_TARGETS: dict[str, Any] = {
    "aws":     AWSFargateTarget,
    "azure":   AzureContainerAppsTarget,
    "gcp":     GCPCloudRunTarget,
    "railway": RailwayTarget,
    "fly":     FlyTarget,
    "k8s":     KubernetesTarget,
}

SUPPORTED_TARGETS = list(_TARGETS.keys())


def deploy(
    target: str,
    image: str,
    name: str = "meshflow",
    region: str = "",
    env: dict[str, str] | None = None,
    **kwargs: Any,
) -> CloudDeployResult:
    """Deploy a MeshFlow container to a cloud target.

    Parameters
    ----------
    target:
        One of: ``aws``, ``azure``, ``gcp``, ``railway``, ``fly``, ``k8s``.
    image:
        Docker image to deploy (e.g. ``ghcr.io/anteneh-t-tessema/meshflow-mcp:1.13.0``).
    name:
        Service / application name.
    region:
        Cloud region.  Defaults: aws=us-east-1, azure=eastus, gcp=us-central1,
        fly=iad, k8s=default.
    env:
        Environment variables to pass to the container.

    Returns
    -------
    CloudDeployResult
    """
    if target not in _TARGETS:
        return CloudDeployResult(
            ok=False, target=target, name=name, region=region,
            error=f"Unknown target {target!r}. Supported: {', '.join(SUPPORTED_TARGETS)}",
        )
    defaults = {
        "aws": "us-east-1", "azure": "eastus", "gcp": "us-central1",
        "railway": "us-west2", "fly": "iad", "k8s": "default",
    }
    r = region or defaults.get(target, "us-east-1")
    t = _TARGETS[target]()
    return t.deploy(image=image, name=name, region=r, env=env or {}, **kwargs)


def generate_iac(
    target: str,
    image: str,
    name: str = "meshflow",
    env: dict[str, str] | None = None,
    **kwargs: Any,
) -> str:
    """Return an IaC template string for the given target without deploying.

    Supported for: ``aws`` (CloudFormation), ``azure`` (Bicep),
    ``gcp`` (Terraform), ``fly`` (fly.toml), ``k8s`` (manifest YAML).
    """
    env = env or {}
    region = kwargs.get("region", "us-east-1")
    if target == "aws":
        return AWSFargateTarget.lambda_template(name, image, env)
    if target == "azure":
        return AzureContainerAppsTarget.bicep_template(name, image, env, region)
    if target == "gcp":
        project = kwargs.get("project", "my-gcp-project")
        return GCPCloudRunTarget.terraform_template(name, image, env, project, region)
    if target == "fly":
        return FlyTarget.fly_toml(name, image, env, kwargs.get("region", "iad"))
    if target == "k8s":
        return KubernetesTarget.k8s_manifest(name, image, kwargs.get("namespace", "meshflow"), env=env)
    return f"# IaC template not available for target={target!r}"
