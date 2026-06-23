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
from dataclasses import dataclass
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

    def _get(self, path: str) -> dict[str, Any] | None:
        """GET request to path; returns JSON dict on success, None on error."""
        if not self._cfg.enabled:
            return None
        url  = f"{self._cfg.base_url}{path}"
        req  = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "x-meshflow-key": self._cfg.api_key,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.URLError:
            return None
        except Exception:
            return None

    async def _aget(self, path: str) -> dict[str, Any] | None:
        """Async wrapper — runs _get in a thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get, path)

    # ── Config Fetching ────────────────────────────────────────────────────────

    def get_policy(self) -> dict[str, Any] | None:
        """Fetch the organization's ZeroTrustPolicy from the cloud."""
        return self._get("/api/config/policy")

    def get_model_routers(self) -> list[dict[str, Any]] | None:
        """Fetch the organization's Model Routers from the cloud."""
        res = self._get("/api/config/routers")
        return res.get("routers") if isinstance(res, dict) else None


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

    # ── report_compliance ──────────────────────────────────────────────────────

    def report_compliance(
        self,
        framework: str,
        passed: bool,
        *,
        run_id: str | None = None,
        score: float | None = None,
        evidence: dict[str, Any] | None = None,
        generated_at: str | None = None,
    ) -> bool:
        """Push a compliance evidence report to /dashboard/compliance.

        Designed to be called after :class:`~meshflow.core.compliance.ComplianceProfile`
        or :class:`~meshflow.security.soc2.SOC2Checker` generates a report.

        Parameters
        ----------
        framework:
            Compliance framework: ``"hipaa"``, ``"sox"``, ``"gdpr"``, ``"pci"``,
            ``"nerc"``, ``"soc2"``, ``"eu_ai_act"``.
        passed:
            Whether the overall check passed.
        run_id:
            Optional: scope the report to a single run.
        score:
            0.0–1.0 overall compliance score. Computed from *evidence* if omitted.
        evidence:
            Dict mapping control IDs to ``{passed, title, details}`` dicts.
        generated_at:
            ISO-8601 timestamp of when the report was generated.
        """
        payload: dict[str, Any] = {"framework": framework, "passed": passed}
        if run_id:
            payload["run_id"] = run_id
        if score is not None:
            payload["score"] = score
        if evidence:
            payload["evidence"] = evidence
        if generated_at:
            payload["generated_at"] = generated_at
        return self._post("/api/ingest/compliance", payload)

    async def areport_compliance(self, **kwargs: Any) -> bool:
        """Async variant of :meth:`report_compliance`."""
        return await self._apost("/api/ingest/compliance", kwargs)

    # ── report_spans ───────────────────────────────────────────────────────────

    def report_spans(self, spans: list[dict[str, Any]]) -> bool:
        """Send a batch of trace spans to /dashboard/traces.

        Each span dict must include at minimum ``run_id``, ``agent_name``,
        ``span_type``, ``name``, ``started_at`` (ISO-8601), and ``duration_ms``.
        """
        if not spans:
            return True
        return self._post("/api/ingest/spans", {"spans": spans})

    async def areport_spans(self, spans: list[dict[str, Any]]) -> bool:
        if not spans:
            return True
        return await self._apost("/api/ingest/spans", {"spans": spans})

    # ── Auto-instrumentation context manager ──────────────────────────────────

    @contextmanager
    def instrument(self, *, register_agents: bool = False) -> Generator[None, None, None]:
        """Context manager that automatically reports every Workflow run and
        all per-step trace spans to the dashboard.

        Parameters
        ----------
        register_agents:
            When ``True``, every agent node seen in a ``STEP_COMPLETE`` event
            is upserted to the cloud Agent Registry so it appears in
            ``/dashboard/agents``.

        Usage::

            with cloud.instrument():
                result = wf.run("task")
            # run summary + span-level traces sent automatically

            with cloud.instrument(register_agents=True):
                result = wf.run("task")
            # also registers each agent node in /dashboard/agents
        """
        import datetime
        from meshflow.core.events import global_event_bus, EventKind
        from meshflow.core.workflow import WorkflowResult

        reported: list[str] = []
        # span accumulator: run_id -> list of span dicts
        _pending_spans: dict[str, list[dict[str, Any]]] = {}
        # step start times: (run_id, node_id) -> (started_iso, monotonic_start)
        _step_starts: dict[tuple[str, str], tuple[str, float]] = {}

        def _iso() -> str:
            return datetime.datetime.now(datetime.timezone.utc).isoformat()

        def _on_event(event: Any) -> None:
            kind    = getattr(event, "kind", None)
            run_id  = getattr(event, "run_id", "") or ""
            node_id = getattr(event, "node_id", "") or ""
            data    = getattr(event, "data", {}) or {}

            if kind == EventKind.STEP_START:
                _step_starts[(run_id, node_id)] = (_iso(), time.monotonic())

            elif kind == EventKind.STEP_COMPLETE:
                start_info = _step_starts.pop((run_id, node_id), None)
                started_at = start_info[0] if start_info else _iso()
                mono_start = start_info[1] if start_info else time.monotonic()
                duration_ms = int((time.monotonic() - mono_start) * 1000)

                span: dict[str, Any] = {
                    "run_id":      run_id,
                    "agent_name":  node_id,
                    "span_type":   data.get("kind", "step"),
                    "name":        node_id,
                    "started_at":  started_at,
                    "duration_ms": duration_ms,
                    "input_tokens":  int(data.get("tokens", 0)),
                    "output_tokens": 0,
                    "cost_usd":    float(data.get("cost_usd", 0.0)),
                    "status":      "ok",
                    "output_text": data.get("content_preview", ""),
                    "metadata":    {"uncertainty": data.get("uncertainty", 0.0)},
                }
                _pending_spans.setdefault(run_id, []).append(span)

                if register_agents and node_id:
                    try:
                        from meshflow.cloud.agent_registry import CloudAgentRegistry
                        CloudAgentRegistry.record_run(node_id, run_count=1)
                    except Exception:
                        pass

            elif kind == EventKind.WORKFLOW_COMPLETE:
                result = getattr(event, "data", {})
                if run_id not in reported:
                    reported.append(run_id)
                    # Report the run summary using event data or payload
                    payload = getattr(event, "payload", None)
                    if isinstance(payload, WorkflowResult):
                        self.report_run(payload)
                    else:
                        self._post("/api/ingest/run", {
                            "run_id":         run_id,
                            "workflow_name":  result.get("workflow_name", "unknown"),
                            "agent_count":    result.get("agent_count", 0),
                            "total_cost_usd": result.get("total_cost_usd", 0.0),
                            "total_tokens":   result.get("total_tokens", 0),
                            "cache_hit_rate": 0.0,
                            "policy":         "standard",
                            "status":         "completed",
                            "duration_ms":    result.get("duration_ms", 0),
                            "violations":     0,
                        })
                    # Flush spans for this run
                    spans = _pending_spans.pop(run_id, [])
                    if spans:
                        self.report_spans(spans)

        class _CBQueue:
            """Duck-typed asyncio.Queue injected into the bus's _queues list.

            WorkflowEventBus.emit() calls q.put_nowait(event) on every
            registered queue; we intercept that to drive a sync callback
            without touching the bus's public API.
            """
            maxsize = 0

            def put_nowait(_self, event: Any) -> None:  # noqa: N805
                if event is not None:
                    try:
                        _on_event(event)
                    except Exception:
                        pass

        cb_queue = _CBQueue()
        global_event_bus._queues.append(cb_queue)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            try:
                global_event_bus._queues.remove(cb_queue)  # type: ignore[attr-defined]
            except (ValueError, AttributeError):
                pass
            # Flush spans from runs that finished without WORKFLOW_COMPLETE
            for rid, spans in _pending_spans.items():
                if spans:
                    self.report_spans(spans)
            _pending_spans.clear()

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


def report_compliance(
    framework: str,
    passed: bool,
    **kwargs: Any,
) -> bool:
    """Module-level shorthand for :meth:`MeshFlowCloud.report_compliance`.

    Usage::

        from meshflow.cloud import cloud_report_compliance
        from meshflow import SOC2Checker

        report = SOC2Checker().check()
        cloud_report_compliance(
            framework="soc2",
            passed=report.passed,
            score=report.score,
            evidence={c.control_id: {"passed": c.passed, "details": c.details}
                      for c in report.controls},
        )
    """
    return get_cloud_client().report_compliance(framework, passed, **kwargs)
