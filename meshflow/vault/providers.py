"""External secret provider backends for MeshFlow VaultStore.

Provides production-grade secret backends that integrate with enterprise
secret managers, replacing the local SQLite-backed VaultStore for
cloud and on-premises deployments.

Available providers
-------------------
AWSSecretsProvider        — AWS Secrets Manager (boto3 optional)
HashiCorpVaultProvider    — HashiCorp Vault HTTP API (stdlib urllib only)
EnvSecretsProvider        — Environment variables (for CI/CD pipelines)

All providers implement the same interface as VaultStore so they are
drop-in replacements::

    # Swap VaultStore for AWS Secrets Manager in one line
    from meshflow.vault.providers import AWSSecretsProvider
    vault = AWSSecretsProvider(prefix="meshflow/prod/")
    secret = vault.retrieve("anthropic-api-key")

Usage examples
--------------
AWS Secrets Manager::

    vault = AWSSecretsProvider(
        prefix="meshflow/prod/",
        region="us-east-1",
        # boto3 auto-discovers credentials from env / IAM role
    )
    vault.store("anthropic-api-key", "sk-ant-...", created_by="ops")
    secret = vault.retrieve("anthropic-api-key")
    print(secret.value)

HashiCorp Vault::

    vault = HashiCorpVaultProvider(
        address="https://vault.internal.example.com",
        token=os.environ["VAULT_TOKEN"],
        mount="secret",
        path_prefix="meshflow/prod",
    )
    vault.store("anthropic-api-key", "sk-ant-...", created_by="ops")
    secret = vault.retrieve("anthropic-api-key")

Environment variables (CI/CD)::

    # Set MESHFLOW_SECRET_ANTHROPIC_API_KEY=sk-ant-... in env
    vault = EnvSecretsProvider(prefix="MESHFLOW_SECRET_")
    secret = vault.retrieve("anthropic-api-key")
    # reads os.environ["MESHFLOW_SECRET_ANTHROPIC_API_KEY"]
"""

from __future__ import annotations

import os
import json
import time
import urllib.error
import urllib.request
import urllib.parse
from typing import Any, Optional

from meshflow.vault.store import VaultSecret


# ── AWSSecretsProvider ────────────────────────────────────────────────────────

class AWSSecretsProvider:
    """AWS Secrets Manager backend for MeshFlow vault.

    Requires ``boto3`` installed (``pip install boto3``).
    Credentials are auto-discovered from the standard AWS credential chain:
    environment variables, ``~/.aws/credentials``, EC2 instance profile, ECS
    task role, etc.

    Parameters
    ----------
    prefix:
        Path prefix prepended to all secret names.
        E.g. ``"meshflow/prod/"`` → secret name ``"api-key"`` becomes
        ``"meshflow/prod/api-key"`` in AWS Secrets Manager.
    region:
        AWS region. Defaults to ``AWS_DEFAULT_REGION`` env var or ``"us-east-1"``.
    kms_key_id:
        Optional KMS key ARN for customer-managed encryption.
    """

    def __init__(
        self,
        prefix: str = "meshflow/",
        region: str = "",
        kms_key_id: str = "",
    ) -> None:
        self.prefix = prefix.rstrip("/") + "/"
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.kms_key_id = kms_key_id
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3  # type: ignore[import]
                self._client = boto3.client("secretsmanager", region_name=self.region)
            except ImportError as exc:
                raise ImportError(
                    "boto3 is required for AWSSecretsProvider. "
                    "Install it with: pip install boto3"
                ) from exc
        return self._client

    def _full_name(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def store(
        self,
        name: str,
        value: str,
        category: str = "generic",
        description: str = "",
        created_by: str = "",
    ) -> str:
        client = self._get_client()
        full = self._full_name(name)
        tags = [
            {"Key": "meshflow:category", "Value": category},
            {"Key": "meshflow:created_by", "Value": created_by or "meshflow"},
        ]
        kwargs: dict[str, Any] = {
            "Name": full,
            "SecretString": value,
            "Description": description,
            "Tags": tags,
        }
        if self.kms_key_id:
            kwargs["KmsKeyId"] = self.kms_key_id
        try:
            client.create_secret(**kwargs)
        except client.exceptions.ResourceExistsException:
            client.put_secret_value(SecretId=full, SecretString=value)
        return full

    def retrieve(self, name: str, accessed_by: str = "") -> Optional[VaultSecret]:  # noqa: ARG002
        client = self._get_client()
        try:
            resp = client.get_secret_value(SecretId=self._full_name(name))
            return VaultSecret(
                secret_id=resp.get("ARN", ""),
                name=name,
                value=resp["SecretString"],
                category="generic",
                description="",
                created_by="",
                created_at=time.time(),
                rotated_at=None,
            )
        except Exception:
            return None

    def rotate(self, name: str, new_value: str, rotated_by: str = "") -> bool:  # noqa: ARG002
        try:
            client = self._get_client()
            client.put_secret_value(
                SecretId=self._full_name(name), SecretString=new_value
            )
            return True
        except Exception:
            return False

    def delete(self, name: str, deleted_by: str = "") -> bool:  # noqa: ARG002
        try:
            client = self._get_client()
            client.delete_secret(
                SecretId=self._full_name(name),
                ForceDeleteWithoutRecovery=False,
            )
            return True
        except Exception:
            return False

    def list_secrets(self, category: str = "") -> list[dict[str, Any]]:  # noqa: ARG002
        try:
            client = self._get_client()
            paginator = client.get_paginator("list_secrets")
            results = []
            for page in paginator.paginate(
                Filters=[{"Key": "name", "Values": [self.prefix]}]
            ):
                for s in page.get("SecretList", []):
                    results.append({
                        "name": s["Name"].removeprefix(self.prefix),
                        "description": s.get("Description", ""),
                        "created_at": s.get("CreatedDate", ""),
                        "arn": s.get("ARN", ""),
                    })
            return results
        except Exception:
            return []


# ── HashiCorpVaultProvider ────────────────────────────────────────────────────

class HashiCorpVaultProvider:
    """HashiCorp Vault KV v2 backend for MeshFlow vault.

    Uses Vault's HTTP API via stdlib ``urllib`` — no extra dependencies.

    Parameters
    ----------
    address:
        Vault server address, e.g. ``"https://vault.example.com"``.
        Defaults to ``VAULT_ADDR`` env var or ``"http://127.0.0.1:8200"``.
    token:
        Vault token. Defaults to ``VAULT_TOKEN`` env var.
    mount:
        KV v2 secrets engine mount path. Default: ``"secret"``.
    path_prefix:
        Path under the mount for all MeshFlow secrets.
        E.g. ``"meshflow/prod"`` → stored at ``secret/data/meshflow/prod/<name>``.
    namespace:
        Vault Enterprise namespace (optional).
    """

    def __init__(
        self,
        address: str = "",
        token: str = "",
        mount: str = "secret",
        path_prefix: str = "meshflow",
        namespace: str = "",
    ) -> None:
        self.address = (address or os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")).rstrip("/")
        self.token = token or os.environ.get("VAULT_TOKEN", "")
        self.mount = mount.strip("/")
        self.path_prefix = path_prefix.strip("/")
        self.namespace = namespace

    def _headers(self) -> dict[str, str]:
        h = {
            "X-Vault-Token": self.token,
            "Content-Type": "application/json",
        }
        if self.namespace:
            h["X-Vault-Namespace"] = self.namespace
        return h

    def _data_url(self, name: str) -> str:
        return f"{self.address}/v1/{self.mount}/data/{self.path_prefix}/{name}"

    def _metadata_url(self, name: str) -> str:
        return f"{self.address}/v1/{self.mount}/metadata/{self.path_prefix}/{name}"

    def _request(self, method: str, url: str, body: bytes | None = None) -> Any:
        req = urllib.request.Request(
            url, data=body, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def store(
        self,
        name: str,
        value: str,
        category: str = "generic",
        description: str = "",
        created_by: str = "",
    ) -> str:
        payload = json.dumps({
            "data": {
                "value": value,
                "category": category,
                "description": description,
                "created_by": created_by,
                "created_at": str(time.time()),
            }
        }).encode()
        self._request("POST", self._data_url(name), payload)
        return f"{self.mount}/{self.path_prefix}/{name}"

    def retrieve(self, name: str, accessed_by: str = "") -> Optional[VaultSecret]:  # noqa: ARG002
        resp = self._request("GET", self._data_url(name))
        if not resp:
            return None
        data = resp.get("data", {}).get("data", {})
        if not data or "value" not in data:
            return None
        return VaultSecret(
            secret_id=f"{self.mount}/{self.path_prefix}/{name}",
            name=name,
            value=data["value"],
            category=data.get("category", "generic"),
            description=data.get("description", ""),
            created_by=data.get("created_by", ""),
            created_at=float(data.get("created_at", time.time())),
            rotated_at=float(data["rotated_at"]) if data.get("rotated_at") else None,
        )

    def rotate(self, name: str, new_value: str, rotated_by: str = "") -> bool:
        try:
            existing = self.retrieve(name)
            payload = json.dumps({
                "data": {
                    "value": new_value,
                    "category": existing.category if existing else "generic",
                    "description": existing.description if existing else "",
                    "created_by": existing.created_by if existing else "",
                    "created_at": str(existing.created_at if existing else time.time()),
                    "rotated_by": rotated_by,
                    "rotated_at": str(time.time()),
                }
            }).encode()
            self._request("POST", self._data_url(name), payload)
            return True
        except Exception:
            return False

    def delete(self, name: str, deleted_by: str = "") -> bool:  # noqa: ARG002
        try:
            self._request("DELETE", self._metadata_url(name))
            return True
        except Exception:
            return False

    def list_secrets(self, category: str = "") -> list[dict[str, Any]]:  # noqa: ARG002
        try:
            url = f"{self.address}/v1/{self.mount}/metadata/{self.path_prefix}?list=true"
            resp = self._request("GET", url)
            if not resp:
                return []
            keys = resp.get("data", {}).get("keys", [])
            return [{"name": k.rstrip("/")} for k in keys if not k.endswith("/")]
        except Exception:
            return []


# ── EnvSecretsProvider ────────────────────────────────────────────────────────

class EnvSecretsProvider:
    """Read-only secrets provider backed by environment variables.

    Designed for CI/CD pipelines and local development where secrets are
    injected as environment variables. Secret names are upper-cased and
    prefixed.

    Example::

        # In environment:
        # MESHFLOW_SECRET_ANTHROPIC_API_KEY=sk-ant-...
        # MESHFLOW_SECRET_OPENAI_API_KEY=sk-...

        vault = EnvSecretsProvider(prefix="MESHFLOW_SECRET_")
        secret = vault.retrieve("anthropic-api-key")
        # Reads MESHFLOW_SECRET_ANTHROPIC_API_KEY
        print(secret.value)

    Parameters
    ----------
    prefix:
        Environment variable prefix. Default: ``"MESHFLOW_SECRET_"``.
    """

    def __init__(self, prefix: str = "MESHFLOW_SECRET_") -> None:
        self.prefix = prefix

    def _env_key(self, name: str) -> str:
        return self.prefix + name.upper().replace("-", "_").replace("/", "_")

    def store(self, name: str, value: str, **kwargs: Any) -> str:
        raise NotImplementedError(
            "EnvSecretsProvider is read-only. "
            "Set the environment variable directly: "
            f"{self._env_key(name)}=<value>"
        )

    def retrieve(self, name: str, accessed_by: str = "") -> Optional[VaultSecret]:  # noqa: ARG002
        value = os.environ.get(self._env_key(name))
        if value is None:
            return None
        return VaultSecret(
            secret_id=self._env_key(name),
            name=name,
            value=value,
            category="env",
            description=f"From environment variable {self._env_key(name)}",
            created_by="environment",
            created_at=0.0,
            rotated_at=None,
        )

    def rotate(self, name: str, new_value: str, rotated_by: str = "") -> bool:
        raise NotImplementedError("EnvSecretsProvider is read-only.")

    def delete(self, name: str, deleted_by: str = "") -> bool:
        raise NotImplementedError("EnvSecretsProvider is read-only.")

    def list_secrets(self, category: str = "") -> list[dict[str, Any]]:
        results = []
        for key, _ in os.environ.items():
            if key.startswith(self.prefix):
                name = key[len(self.prefix):].lower().replace("_", "-")
                results.append({"name": name, "source": key})
        return results


__all__ = ["AWSSecretsProvider", "HashiCorpVaultProvider", "EnvSecretsProvider"]
