"""CloudAgentRegistry — register and sync agent definitions with meshflow.dev.

Usage::

    from meshflow.cloud import CloudAgentRegistry

    # Register (or update) an agent definition
    CloudAgentRegistry.register(
        name="HIPAA Intake Processor",
        slug="hipaa-intake-processor",
        role="executor",
        model="claude-sonnet-4-6",
        policy="hipaa",
        system_prompt="You are a HIPAA-compliant intake specialist…",
        tags="hipaa,intake,clinical",
    )

    # Bump run counter after a completed run
    CloudAgentRegistry.record_run("hipaa-intake-processor", run_count=1)

    # List all registered agents
    agents = CloudAgentRegistry.list()

    # Integrate with MeshFlowCloud.instrument() — auto-register every agent
    # that fires a STEP_COMPLETE event (pass register_agents=True):
    with cloud.instrument(register_agents=True):
        result = wf.run("process intake form")

Environment variables
---------------------

MESHFLOW_API_KEY      — required
MESHFLOW_CLOUD_URL    — optional; defaults to https://meshflow.dev
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_DEFAULT_BASE = "https://meshflow.dev"
_TIMEOUT_S    = 8


class CloudAgentRegistry:
    """Class-level interface for cloud agent registration."""

    @classmethod
    def _cfg(cls) -> tuple[str, str, bool]:
        key = os.environ.get("MESHFLOW_API_KEY", "")
        url = os.environ.get("MESHFLOW_CLOUD_URL", _DEFAULT_BASE).rstrip("/")
        ok  = os.environ.get("MESHFLOW_CLOUD_ENABLED", "1") != "0" and bool(key)
        return key, url, ok

    @classmethod
    def _post(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        key, url, enabled = cls._cfg()
        if not enabled:
            return None
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{url}/api/ingest/agents",
            data=data,
            headers={"Content-Type": "application/json", "x-meshflow-key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    @classmethod
    def _get(cls, path: str) -> Any:
        key, url, enabled = cls._cfg()
        if not enabled:
            return None
        req = urllib.request.Request(
            f"{url}{path}",
            headers={"x-meshflow-key": key, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    @classmethod
    def register(
        cls,
        name: str,
        slug: str,
        *,
        description: str = "",
        role: str = "executor",
        model: str = "claude-sonnet-4-6",
        policy: str = "standard",
        system_prompt: str = "",
        tags: str = "",
        deploy_target: str = "local",
        version: str = "1.0.0",
        status: str = "active",
    ) -> bool:
        """Upsert an agent definition in the cloud registry.

        Returns ``True`` on success, ``False`` on error or when disabled.
        """
        result = cls._post({
            "name":          name,
            "slug":          slug,
            "description":   description,
            "role":          role,
            "model":         model,
            "policy":        policy,
            "system_prompt": system_prompt,
            "tags":          tags,
            "deploy_target": deploy_target,
            "version":       version,
            "status":        status,
        })
        return result is not None and "id" in result

    @classmethod
    def record_run(cls, slug: str, run_count: int = 1) -> bool:
        """Increment the run counter for a registered agent.

        Call this after each successful run to keep dashboard stats current.
        """
        result = cls._post({"name": slug, "slug": slug, "run_count": run_count})
        return result is not None

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        """Return all agent definitions registered for this org."""
        result = cls._get("/api/ingest/agents")
        return result if isinstance(result, list) else []

    @classmethod
    def get(cls, slug: str) -> dict[str, Any] | None:
        """Fetch a single agent definition by slug."""
        return cls._get(f"/api/ingest/agents?slug={urllib.parse.quote(slug)}")
