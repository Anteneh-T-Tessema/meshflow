"""MeshFlow Cloud telemetry client — report runs, evals, MCP calls, and worker
jobs to the meshflow.dev dashboard.

Set your API key via environment variable or pass it explicitly::

    export MESHFLOW_API_KEY=mf_sk_...

    from meshflow.cloud import MeshFlowCloud

    cloud = MeshFlowCloud()                              # reads MESHFLOW_API_KEY
    cloud = MeshFlowCloud(api_key="mf_sk_...",           # explicit key
                          base_url="https://meshflow.dev")  # or self-hosted

Ingest methods
--------------

All methods are available both sync and async::

    # Sync
    cloud.report_run(result)
    cloud.report_eval(suite="regression", scenario="summarise_hipaa",
                      metric="faithfulness", score=0.92, passed=True)
    cloud.report_mcp_call(server="filesystem", tool="read_file", latency_ms=12)
    cloud.report_worker_job(job_id="job-abc", workflow="daily-report",
                            status="completed", duration_ms=4200)

    # Async
    await cloud.areport_run(result)
    await cloud.areport_eval(...)

Auto-instrumentation
--------------------

Wrap the MeshFlow context so every run reports automatically::

    with cloud.instrument():
        result = workflow.run("analyse quarterly earnings")
    # run was reported to the dashboard automatically

Environment variables
---------------------

MESHFLOW_API_KEY        — required; your API key from /dashboard/api-keys
MESHFLOW_CLOUD_URL      — optional; default https://meshflow.dev
MESHFLOW_CLOUD_ENABLED  — set to "0" to disable all reporting (e.g. in CI)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator


_DEFAULT_BASE = "https://meshflow.dev"
_TIMEOUT_S    = 8


@dataclass
class CloudConfig:
    api_key:  str
    base_url: str = _DEFAULT_BASE
    enabled:  bool = True
    timeout:  int  = _TIMEOUT_S


class MeshFlowCloud:
    """Lightweight cloud telemetry client for meshflow.dev.

    Parameters
    ----------
    api_key:
        Your MeshFlow Cloud API key (``mf_sk_...``).  Falls back to the
        ``MESHFLOW_API_KEY`` environment variable.
    base_url:
        Dashboard base URL.  Defaults to ``https://meshflow.dev``.
    enabled:
        Set to ``False`` to disable all HTTP calls (useful in CI/local dev).
        Also respects the ``MESHFLOW_CLOUD_ENABLED=0`` env var.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        enabled: bool = True,
    ) -> None:
        resolved_key = api_key or os.environ.get("MESHFLOW_API_KEY", "")
        resolved_url = base_url or os.environ.get("MESHFLOW_CLOUD_URL", _DEFAULT_BASE)
        is_enabled   = enabled and os.environ.get("MESHFLOW_CLOUD_ENABLED", "1") != "0"
        self._cfg = CloudConfig(
            api_key=resolved_key,
            base_url=resolved_url.rstrip("/"),
            enabled=is_enabled and bool(resolved_key),
        )

    # ── Low-level HTTP ─────────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict[str, Any]) -> bool:
        """POST payload to path; returns True on success, False on error."""
        if not self._cfg.enabled:
            return True
        url  = f"{self._cfg.base_url}{path}"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-meshflow-key": self._cfg.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout):
                return True
        except urllib.error.URLError:
            return False  # silently drop on network error
        except Exception:
            return False

    async def _apost(self, path: str, payload: dict[str, Any]) -> bool:
        """Async wrapper — runs _post in a thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._post, path, payload)

    # ── report_run ─────────────────────────────────────────────────────────────

    def report_run(self, result: Any, workflow_name: str = "") -> bool:
        """Report a workflow run to the dashboard.

        Accepts a :class:`~meshflow.core.workflow.WorkflowResult` or any object
        with ``run_id``, ``total_cost_usd``, ``total_tokens``, ``status``, etc.
        """
        payload = _extract_run_payload(result, workflow_name)
        return self._post("/api/ingest/run", payload)

    async def areport_run(self, result: Any, workflow_name: str = "") -> bool:
        payload = _extract_run_payload(result, workflow_name)
        return await self._apost("/api/ingest/run", payload)

    # ── report_eval ────────────────────────────────────────────────────────────

    def report_eval(
        self,
        *,
        run_id: str,
        suite: str = "default",
        scenario: str,
        metric: str = "overall",
        score: float,
        passed: bool | None = None,
        reasoning: str | None = None,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
    ) -> bool:
        """Report one eval result to /dashboard/evals."""
        if passed is None:
            passed = score >= 0.8
        return self._post("/api/ingest/eval", {
            "run_id":     run_id,
            "suite":      suite,
            "scenario":   scenario,
            "metric":     metric,
            "score":      score,
            "passed":     passed,
            "reasoning":  reasoning,
            "cost_usd":   cost_usd,
            "latency_ms": latency_ms,
        })

    async def areport_eval(self, **kwargs: Any) -> bool:
        return await self._apost("/api/ingest/eval", _eval_payload(**kwargs))

    # ── report_mcp_call ────────────────────────────────────────────────────────

    def report_mcp_call(
        self,
        *,
        server_name: str,
        tool_name: str,
        transport: str = "stdio",
        endpoint: str | None = None,
        latency_ms: int = 0,
        success: bool = True,
        cost_usd: float = 0.0,
        tool_count: int = 0,
    ) -> bool:
        """Report one MCP tool call to /dashboard/mcp."""
        return self._post("/api/ingest/mcp", {
            "server_name": server_name,
            "tool_name":   tool_name,
            "transport":   transport,
            "endpoint":    endpoint,
            "latency_ms":  latency_ms,
            "success":     success,
            "cost_usd":    cost_usd,
            "tool_count":  tool_count,
        })

    async def areport_mcp_call(self, **kwargs: Any) -> bool:
        return await self._apost("/api/ingest/mcp", kwargs)

    # ── report_worker_job ──────────────────────────────────────────────────────

    def report_worker_job(
        self,
        *,
        job_id: str,
        workflow_name: str,
        status: str,
        retries: int = 0,
        max_retries: int = 3,
        duration_ms: int = 0,
        error_msg: str | None = None,
        scheduled_for: str | None = None,
    ) -> bool:
        """Upsert a worker job status event to /dashboard/workers."""
        return self._post("/api/ingest/worker", {
            "job_id":        job_id,
            "workflow_name": workflow_name,
            "status":        status,
            "retries":       retries,
            "max_retries":   max_retries,
            "duration_ms":   duration_ms,
            "error_msg":     error_msg,
            "scheduled_for": scheduled_for,
        })

    async def areport_worker_job(self, **kwargs: Any) -> bool:
        return await self._apost("/api/ingest/worker", kwargs)

    # ── Auto-instrumentation context manager ──────────────────────────────────

    @contextmanager
    def instrument(self) -> Generator[None, None, None]:
        """Context manager that automatically reports every Workflow run.

        Usage::

            with cloud.instrument():
                result = wf.run("task")
            # result was automatically sent to the dashboard
        """
        from meshflow.core.events import global_event_bus, EventKind
        from meshflow.core.workflow import WorkflowResult

        reported: list[str] = []

        def _on_event(event: Any) -> None:
            if getattr(event, "kind", None) == EventKind.RUN_COMPLETED:
                result = getattr(event, "payload", None)
                if isinstance(result, WorkflowResult) and result.run_id not in reported:
                    reported.append(result.run_id)
                    self.report_run(result)

        sub_id = global_event_bus.subscribe(_on_event)
        try:
            yield
        finally:
            try:
                global_event_bus.unsubscribe(sub_id)
            except Exception:
                pass

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    @property
    def base_url(self) -> str:
        return self._cfg.base_url


# ── Payload helpers ────────────────────────────────────────────────────────────

def _extract_run_payload(result: Any, workflow_name: str = "") -> dict[str, Any]:
    """Build the /api/ingest/run payload from a WorkflowResult or plain dict."""
    if isinstance(result, dict):
        return result
    run_id   = getattr(result, "run_id",          "") or ""
    wf_name  = getattr(result, "workflow_name",   workflow_name) or workflow_name or "unknown"
    status   = "completed" if getattr(result, "completed", True) else "failed"
    return {
        "run_id":        run_id,
        "workflow_name": wf_name,
        "agent_count":   len(getattr(result, "steps", [])),
        "total_cost_usd": getattr(result, "total_cost_usd", 0.0),
        "total_tokens":  getattr(result, "total_tokens", 0),
        "cache_hit_rate": 0.0,
        "policy":        "standard",
        "status":        status,
        "duration_ms":   int(getattr(result, "duration_s", 0.0) * 1000),
        "violations":    0,
    }


def _eval_payload(**kwargs: Any) -> dict[str, Any]:
    if "passed" not in kwargs:
        kwargs["passed"] = kwargs.get("score", 0) >= 0.8
    return kwargs


# ── Module-level default instance ─────────────────────────────────────────────

_default_client: MeshFlowCloud | None = None


def get_cloud_client() -> MeshFlowCloud:
    """Return (or lazily create) the module-level :class:`MeshFlowCloud` instance."""
    global _default_client
    if _default_client is None:
        _default_client = MeshFlowCloud()
    return _default_client


def report_run(result: Any, workflow_name: str = "") -> bool:
    """Module-level shorthand for :meth:`MeshFlowCloud.report_run`."""
    return get_cloud_client().report_run(result, workflow_name)


def report_eval(**kwargs: Any) -> bool:
    """Module-level shorthand for :meth:`MeshFlowCloud.report_eval`."""
    return get_cloud_client().report_eval(**kwargs)


def report_mcp_call(**kwargs: Any) -> bool:
    """Module-level shorthand for :meth:`MeshFlowCloud.report_mcp_call`."""
    return get_cloud_client().report_mcp_call(**kwargs)


def report_worker_job(**kwargs: Any) -> bool:
    """Module-level shorthand for :meth:`MeshFlowCloud.report_worker_job`."""
    return get_cloud_client().report_worker_job(**kwargs)
