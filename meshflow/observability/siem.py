"""SIEM Streaming — real-time event forwarding to enterprise security platforms.

Closes the ZT Advanced tier ``siem_streaming`` control by streaming governed
step events to Splunk HEC, Datadog Log Management, or any generic HTTP SIEM.

All network calls are fire-and-forget in daemon threads — SIEM failures never
block agent execution.

Supported backends
------------------
- **Splunk HEC** — HTTP Event Collector (``MESHFLOW_SIEM_SPLUNK_URL`` +
  ``MESHFLOW_SIEM_SPLUNK_TOKEN``)
- **Datadog** — Log Management API (``MESHFLOW_SIEM_DATADOG_API_KEY``,
  optional ``MESHFLOW_SIEM_DATADOG_SITE``)
- **Generic HTTP** — any SIEM with a webhook/REST endpoint
  (``MESHFLOW_SIEM_HTTP_URL``)

Auto-detection: ``SIEMStreamer.from_env()`` scans all three env-var sets and
returns the first configured backend, or a no-op streamer when nothing is set.

Usage (automatic via ZT Advanced tier)::

    # In the environment:
    MESHFLOW_ZT_TIER=advanced
    MESHFLOW_SIEM_SPLUNK_URL=https://splunk.corp:8088/services/collector
    MESHFLOW_SIEM_SPLUNK_TOKEN=Splunk abc123

    # Every Mesh.run() step streams to Splunk automatically.

Usage (manual)::

    from meshflow.observability.siem import SIEMStreamer

    streamer = SIEMStreamer.from_env()
    streamer.emit("step_blocked", {
        "agent_id": "analyst",
        "block_reason": "dasc:high_risk",
        "run_id": "run-abc",
    })
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any


# ── Event schema ───────────────────────────────────────────────────────────────

_SIEM_EVENTS = frozenset({
    "step_complete",
    "step_blocked",
    "policy_violation",
    "hitl_pending",
    "hitl_resolved",
    "anomaly_detected",
    "jit_grant_issued",
    "jit_grant_revoked",
    "auth_denied",
    "injection_detected",
    "run_complete",
    "run_started",
})


def _build_event(event_type: str, data: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    return {
        "source":     "meshflow",
        "event_type": event_type,
        "timestamp":  time.time(),
        "run_id":     run_id or data.get("run_id", ""),
        "severity":   _severity(event_type),
        "data":       data,
    }


def _severity(event_type: str) -> str:
    if event_type in ("step_blocked", "policy_violation", "injection_detected", "auth_denied"):
        return "high"
    if event_type in ("anomaly_detected", "hitl_pending", "jit_grant_revoked"):
        return "medium"
    return "info"


# ── Backend base ───────────────────────────────────────────────────────────────

class SIEMBackend:
    """Abstract SIEM backend. Subclass and implement ``_send()``."""

    def emit(self, event_type: str, data: dict[str, Any], run_id: str = "") -> None:
        """Fire-and-forget emit in a daemon thread."""
        if event_type not in _SIEM_EVENTS:
            return
        payload = _build_event(event_type, data, run_id)
        threading.Thread(
            target=self._send_safe,
            args=(payload,),
            daemon=True,
            name=f"siem-{event_type}",
        ).start()

    def _send_safe(self, payload: dict[str, Any]) -> None:
        try:
            self._send(payload)
        except Exception:
            pass  # SIEM must never surface errors

    def _send(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def is_configured(self) -> bool:
        return True


class _NoopSIEM(SIEMBackend):
    """No-op backend when no SIEM is configured."""

    def emit(self, *_: Any, **__: Any) -> None:
        pass

    def is_configured(self) -> bool:
        return False


# ── Splunk HEC ─────────────────────────────────────────────────────────────────

class SplunkHECBackend(SIEMBackend):
    """Splunk HTTP Event Collector backend.

    Env vars:
      MESHFLOW_SIEM_SPLUNK_URL    — HEC endpoint, e.g. https://splunk:8088/services/collector
      MESHFLOW_SIEM_SPLUNK_TOKEN  — HEC token (without 'Splunk ' prefix)
      MESHFLOW_SIEM_SPLUNK_INDEX  — optional target index (default: main)
      MESHFLOW_SIEM_SPLUNK_SOURCE — optional sourcetype (default: meshflow:agent)
    """

    def __init__(
        self,
        url: str,
        token: str,
        index: str = "main",
        source: str = "meshflow:agent",
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._index = index
        self._source = source

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps({
            "time":       payload["timestamp"],
            "host":       "meshflow",
            "source":     self._source,
            "sourcetype": "_json",
            "index":      self._index,
            "event":      payload,
        }).encode()
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "Authorization": f"Splunk {self._token}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass

    @classmethod
    def from_env(cls) -> "SplunkHECBackend | None":
        url   = os.environ.get("MESHFLOW_SIEM_SPLUNK_URL", "")
        token = os.environ.get("MESHFLOW_SIEM_SPLUNK_TOKEN", "")
        if not (url and token):
            return None
        return cls(
            url=url,
            token=token,
            index=os.environ.get("MESHFLOW_SIEM_SPLUNK_INDEX", "main"),
            source=os.environ.get("MESHFLOW_SIEM_SPLUNK_SOURCE", "meshflow:agent"),
        )


# ── Datadog Log Management ─────────────────────────────────────────────────────

class DatadogLogsBackend(SIEMBackend):
    """Datadog Log Management backend.

    Env vars:
      MESHFLOW_SIEM_DATADOG_API_KEY — Datadog API key
      MESHFLOW_SIEM_DATADOG_SITE    — optional site (datadoghq.com | datadoghq.eu | ...)
    """

    def __init__(self, api_key: str, site: str = "datadoghq.com") -> None:
        self._api_key = api_key
        self._url = f"https://http-intake.logs.{site}/api/v2/logs"

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps([{
            "ddsource":  "meshflow",
            "ddtags":    f"env:production,severity:{payload['severity']}",
            "hostname":  "meshflow",
            "service":   "meshflow-agents",
            "message":   json.dumps(payload),
        }]).encode()
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "DD-API-KEY":   self._api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass

    @classmethod
    def from_env(cls) -> "DatadogLogsBackend | None":
        api_key = os.environ.get("MESHFLOW_SIEM_DATADOG_API_KEY", "")
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            site=os.environ.get("MESHFLOW_SIEM_DATADOG_SITE", "datadoghq.com"),
        )


# ── Generic HTTP SIEM ──────────────────────────────────────────────────────────

class GenericHTTPBackend(SIEMBackend):
    """Generic webhook/HTTP backend for any SIEM with a REST endpoint.

    Env vars:
      MESHFLOW_SIEM_HTTP_URL     — destination URL
      MESHFLOW_SIEM_HTTP_HEADERS — optional JSON dict of extra headers
    """

    def __init__(self, url: str, extra_headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = {"Content-Type": "application/json", **(extra_headers or {})}

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._url,
            data=body,
            headers=self._headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass

    @classmethod
    def from_env(cls) -> "GenericHTTPBackend | None":
        url = os.environ.get("MESHFLOW_SIEM_HTTP_URL", "")
        if not url:
            return None
        raw_headers = os.environ.get("MESHFLOW_SIEM_HTTP_HEADERS", "")
        extra: dict[str, str] = {}
        if raw_headers:
            try:
                extra = json.loads(raw_headers)
            except Exception:
                pass
        return cls(url=url, extra_headers=extra)


# ── Multi-backend streamer ─────────────────────────────────────────────────────

class SIEMStreamer:
    """Fan-out streamer that forwards events to all configured SIEM backends.

    Use ``SIEMStreamer.from_env()`` to auto-detect backends from environment
    variables. Attach to StepRuntime via ``siem=`` parameter (done automatically
    when ZT Advanced tier is active).
    """

    def __init__(self, backends: list[SIEMBackend] | None = None) -> None:
        self._backends: list[SIEMBackend] = backends or []

    def emit(self, event_type: str, data: dict[str, Any], run_id: str = "") -> None:
        for b in self._backends:
            b.emit(event_type, data, run_id)

    def add(self, backend: SIEMBackend) -> "SIEMStreamer":
        self._backends.append(backend)
        return self

    def is_configured(self) -> bool:
        return bool(self._backends)

    def backend_names(self) -> list[str]:
        return [type(b).__name__ for b in self._backends]

    @classmethod
    def from_env(cls) -> "SIEMStreamer":
        """Auto-detect all configured SIEM backends from environment variables."""
        backends: list[SIEMBackend] = []
        for factory in (
            SplunkHECBackend.from_env,
            DatadogLogsBackend.from_env,
            GenericHTTPBackend.from_env,
        ):
            b = factory()
            if b is not None:
                backends.append(b)
        return cls(backends=backends)

    @classmethod
    def noop(cls) -> "SIEMStreamer":
        return cls(backends=[_NoopSIEM()])


# ── Module-level singleton ────────────────────────────────────────────────────

_GLOBAL: SIEMStreamer | None = None


def get_siem_streamer() -> SIEMStreamer:
    """Return (or lazily create) the global SIEMStreamer from env."""
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = SIEMStreamer.from_env()
    return _GLOBAL


__all__ = [
    "SIEMStreamer",
    "SplunkHECBackend",
    "DatadogLogsBackend",
    "GenericHTTPBackend",
    "get_siem_streamer",
]
