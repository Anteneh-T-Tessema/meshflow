"""OTLP/HTTP trace exporter — ships MeshFlow step spans to any OTEL collector.

Sends spans as OTLP/HTTP JSON to a configured endpoint (Jaeger, Grafana Tempo,
Honeycomb, OTEL Collector, etc.) with zero external dependencies — uses
``urllib.request`` for transport.

Environment variables::

    OTEL_EXPORTER_OTLP_ENDPOINT  — OTLP/HTTP endpoint (default: http://localhost:4318)
    OTEL_SERVICE_NAME             — service name reported in all spans (default: meshflow)
    OTEL_EXPORTER_OTLP_HEADERS   — comma-separated key=value pairs, e.g. x-api-key=secret

Usage::

    from meshflow.observability.otel_exporter import get_global_exporter

    exporter = get_global_exporter()
    exporter.export_span(
        trace_id="aabbccddeeff0011",
        span_id="1122334455667788",
        name="step:agent_a",
        start_ns=1_700_000_000_000_000_000,
        end_ns=1_700_000_001_000_000_000,
        attributes={"node_id": "agent_a", "cost_usd": 0.001},
        status="ok",
    )
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

_DEFAULT_ENDPOINT = "http://localhost:4318"
_DEFAULT_SERVICE = "meshflow"
_TIMEOUT = 5.0


class OTELExporter:
    """OTLP/HTTP JSON span exporter.

    Parameters
    ----------
    endpoint:
        Base URL of the OTLP/HTTP endpoint, e.g. ``http://localhost:4318``.
        Spans are posted to ``{endpoint}/v1/traces``.
    service_name:
        The ``service.name`` resource attribute sent with every span.
    headers:
        Extra HTTP headers (e.g. authentication) to include in every request.
    enabled:
        Set to False to turn the exporter into a no-op.
    """

    def __init__(
        self,
        endpoint: str = _DEFAULT_ENDPOINT,
        service_name: str = _DEFAULT_SERVICE,
        headers: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._service_name = service_name
        self._headers = headers or {}
        self._enabled = enabled
        self._exported_count = 0
        self._error_count = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def export_span(
        self,
        trace_id: str,
        span_id: str,
        name: str,
        start_ns: int,
        end_ns: int,
        attributes: dict[str, Any] | None = None,
        status: str = "ok",
        parent_span_id: str = "",
    ) -> bool:
        """Export a single span.  Returns True on success.

        Parameters
        ----------
        trace_id:   128-bit hex trace ID (32 hex chars, no dashes)
        span_id:    64-bit hex span ID (16 hex chars)
        name:       span operation name, e.g. "step:agent_a"
        start_ns:   epoch nanoseconds
        end_ns:     epoch nanoseconds
        attributes: key→value map; values are coerced to OTLP AttributeValue
        status:     "ok" | "error" | "unset"
        parent_span_id: optional 64-bit hex parent span ID
        """
        if not self._enabled:
            return True
        payload = self._build_payload(
            trace_id, span_id, name, start_ns, end_ns,
            attributes or {}, status, parent_span_id,
        )
        return self._send(payload)

    @property
    def exported_count(self) -> int:
        return self._exported_count

    @property
    def error_count(self) -> int:
        return self._error_count

    def config(self) -> dict[str, Any]:
        return {
            "endpoint": self._endpoint,
            "service_name": self._service_name,
            "enabled": self._enabled,
            "exported_count": self._exported_count,
            "error_count": self._error_count,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_payload(
        self,
        trace_id: str,
        span_id: str,
        name: str,
        start_ns: int,
        end_ns: int,
        attributes: dict[str, Any],
        status: str,
        parent_span_id: str,
    ) -> dict[str, Any]:
        """Build OTLP/HTTP JSON proto payload."""
        otlp_attrs = [_otlp_kv(k, v) for k, v in attributes.items()]
        span: dict[str, Any] = {
            "traceId": _pad_trace_id(trace_id),
            "spanId": _pad_span_id(span_id),
            "name": name,
            "kind": 2,  # SPAN_KIND_SERVER
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(end_ns),
            "attributes": otlp_attrs,
            "status": {
                "code": {"ok": 1, "error": 2}.get(status.lower(), 0),
                "message": status,
            },
        }
        if parent_span_id:
            span["parentSpanId"] = _pad_span_id(parent_span_id)

        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            _otlp_kv("service.name", self._service_name),
                            _otlp_kv("meshflow.version", "0.20.0"),
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "meshflow.runtime", "version": "0.20.0"},
                            "spans": [span],
                        }
                    ],
                }
            ]
        }

    def _send(self, payload: dict[str, Any]) -> bool:
        url = f"{self._endpoint}/v1/traces"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **self._headers,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT):
                self._exported_count += 1
                return True
        except Exception:
            self._error_count += 1
            return False


# ── Factory ───────────────────────────────────────────────────────────────────


def from_env() -> OTELExporter:
    """Create an ``OTELExporter`` from environment variables.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` — base URL (default: http://localhost:4318)
    ``OTEL_SERVICE_NAME``           — service name (default: meshflow)
    ``OTEL_EXPORTER_OTLP_HEADERS``  — comma-separated key=value pairs
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_ENDPOINT)
    service = os.environ.get("OTEL_SERVICE_NAME", _DEFAULT_SERVICE)
    raw_headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    headers: dict[str, str] = {}
    for pair in raw_headers.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            headers[k.strip()] = v.strip()
    enabled = bool(endpoint)
    return OTELExporter(endpoint=endpoint, service_name=service, headers=headers, enabled=enabled)


# ── Global singleton ──────────────────────────────────────────────────────────

_GLOBAL: OTELExporter | None = None


def get_global_exporter() -> OTELExporter:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = from_env()
    return _GLOBAL


def set_global_exporter(exporter: OTELExporter) -> None:
    global _GLOBAL
    _GLOBAL = exporter


def reset_global_exporter() -> None:
    global _GLOBAL
    _GLOBAL = None


# ── Span helpers ──────────────────────────────────────────────────────────────


def now_ns() -> int:
    """Current time as epoch nanoseconds."""
    return int(time.time() * 1_000_000_000)


def _pad_trace_id(tid: str) -> str:
    tid = tid.replace("-", "")
    return tid.zfill(32)[-32:]


def _pad_span_id(sid: str) -> str:
    sid = sid.replace("-", "")
    return sid.zfill(16)[-16:]


def _otlp_kv(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}
