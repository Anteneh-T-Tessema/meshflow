"""Arize Phoenix connector for MeshFlow traces.

Exports MeshFlow step records to Arize Phoenix for LLM observability,
cost analysis, and prompt evaluation.

Zero external dependencies when Phoenix is not installed — the connector
silently no-ops. Install the optional extras to activate:

    pip install meshflow[phoenix]
    # or:
    pip install arize-phoenix-otel

Usage::

    import os
    os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "http://localhost:6006/v1/traces"

    from meshflow.observability.arize_phoenix import PhoenixExporter

    exporter = PhoenixExporter()
    exporter.export_run(run_id="run-abc123")

    # Or auto-wire into every Mesh.run():
    from meshflow.core.mesh import Mesh
    from meshflow.observability.arize_phoenix import auto_instrument

    auto_instrument()   # reads PHOENIX_COLLECTOR_ENDPOINT from env
    mesh = Mesh(agents=[...])
    result = await mesh.run("task")   # spans appear in Phoenix automatically

Environment variables::

    PHOENIX_COLLECTOR_ENDPOINT  — OTLP endpoint (default: http://localhost:6006/v1/traces)
    PHOENIX_API_KEY             — API key for Phoenix Cloud (optional)
    MESHFLOW_PHOENIX_PROJECT    — project name in Phoenix (default: meshflow)
"""

from __future__ import annotations

import os
import json
import urllib.request
from typing import Any


_ENDPOINT_ENV = "PHOENIX_COLLECTOR_ENDPOINT"
_APIKEY_ENV   = "PHOENIX_API_KEY"
_PROJECT_ENV  = "MESHFLOW_PHOENIX_PROJECT"

_DEFAULT_ENDPOINT = "http://localhost:6006/v1/traces"


class PhoenixExporter:
    """Export MeshFlow run traces to Arize Phoenix via OTLP/HTTP.

    Parameters
    ----------
    endpoint:
        OTLP collector endpoint. Defaults to ``PHOENIX_COLLECTOR_ENDPOINT``
        env var or ``http://localhost:6006/v1/traces``.
    api_key:
        Arize Phoenix Cloud API key. Defaults to ``PHOENIX_API_KEY`` env var.
    project:
        Phoenix project name. Defaults to ``MESHFLOW_PHOENIX_PROJECT`` or
        ``"meshflow"``.
    """

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        project: str = "",
    ) -> None:
        self.endpoint = endpoint or os.environ.get(_ENDPOINT_ENV, _DEFAULT_ENDPOINT)
        self.api_key  = api_key  or os.environ.get(_APIKEY_ENV, "")
        self.project  = project  or os.environ.get(_PROJECT_ENV, "meshflow")

    # ── Public API ─────────────────────────────────────────────────────────────

    def export_run(self, run_id: str, db: str = "meshflow_runs.db") -> bool:
        """Export all steps of *run_id* to Phoenix as OTLP spans.

        Returns ``True`` on success, ``False`` if the ledger has no records for
        this run or the Phoenix endpoint is unreachable.
        """
        try:
            import asyncio
            from meshflow.core.ledger import ReplayLedger
            ledger = ReplayLedger(db=db)
            steps: list[dict[str, Any]] = asyncio.run(ledger.get_run(run_id))
            if not steps:
                return False
            spans = [self._step_to_span(s, run_id) for s in steps]
            return self._send_spans(spans)
        except Exception:
            return False

    def export_step(self, step: dict[str, Any], run_id: str) -> bool:
        """Export a single step record to Phoenix."""
        try:
            span = self._step_to_span(step, run_id)
            return self._send_spans([span])
        except Exception:
            return False

    # ── Internals ──────────────────────────────────────────────────────────────

    def _step_to_span(self, step: dict[str, Any], run_id: str) -> dict[str, Any]:
        """Convert a MeshFlow StepRecord dict to an OTLP span dict."""
        import time

        # Derive timing — Phoenix needs nanoseconds
        ts_str = step.get("timestamp", "")
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            end_ns = int(dt.timestamp() * 1_000_000_000)
        except Exception:
            end_ns = int(time.time() * 1_000_000_000)

        duration_ms = float(step.get("duration_ms", 0))
        start_ns = end_ns - int(duration_ms * 1_000_000)

        # Build attributes — Phoenix understands OpenAI/Anthropic attribute keys
        attrs: dict[str, Any] = {
            # Standard OpenTelemetry LLM semantic conventions
            "llm.system":              "anthropic",
            "llm.request.model":       step.get("model", "unknown"),
            "llm.usage.total_tokens":  int(step.get("tokens_used", 0)),
            "llm.usage.prompt_tokens": int(step.get("tokens_used", 0)),
            # MeshFlow-specific
            "meshflow.run_id":         run_id,
            "meshflow.step_id":        step.get("step_id", ""),
            "meshflow.node_id":        step.get("node_id", ""),
            "meshflow.node_kind":      step.get("node_kind", ""),
            "meshflow.verdict":        step.get("verdict", ""),
            "meshflow.blocked":        bool(step.get("blocked", False)),
            "meshflow.block_reason":   step.get("block_reason", ""),
            "meshflow.cost_usd":       float(step.get("cost_usd", 0)),
            "meshflow.uncertainty":    float(step.get("uncertainty", 0)),
            "meshflow.carbon_gco2":    float(step.get("carbon_gco2", 0)),
            "meshflow.prev_hash":      step.get("prev_hash", ""),
            "meshflow.entry_hash":     step.get("entry_hash", ""),
            # Input / output for Phoenix eval
            "input.value":             str(step.get("input_task", ""))[:2000],
            "output.value":            str(step.get("output_content", ""))[:2000],
            # Phoenix project tag
            "project.name":            self.project,
        }

        trace_id = run_id.replace("-", "").ljust(32, "0")[:32]
        span_id  = step.get("step_id", "").replace("-", "").ljust(16, "0")[:16]

        return {
            "traceId":            trace_id,
            "spanId":             span_id,
            "name":               f"meshflow.step.{step.get('node_id', 'unknown')}",
            "kind":               2,  # SPAN_KIND_SERVER
            "startTimeUnixNano":  str(start_ns),
            "endTimeUnixNano":    str(end_ns),
            "status":             {"code": 1} if not step.get("blocked") else {"code": 2, "message": step.get("block_reason", "")},
            "attributes":         self._encode_attrs(attrs),
        }

    @staticmethod
    def _encode_attrs(attrs: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert Python dict to OTLP AttributeValue list."""
        result = []
        for key, val in attrs.items():
            if isinstance(val, bool):
                av = {"boolValue": val}
            elif isinstance(val, int):
                av = {"intValue": str(val)}
            elif isinstance(val, float):
                av = {"doubleValue": val}
            else:
                av = {"stringValue": str(val)}
            result.append({"key": key, "value": av})
        return result

    def _send_spans(self, spans: list[dict[str, Any]]) -> bool:
        """POST spans to the OTLP endpoint."""
        if not spans:
            return True

        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": self._encode_attrs({
                            "service.name": self.project,
                            "service.version": "1.0.0",
                            "meshflow.version": "1.0.0",
                        })
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "meshflow.observability", "version": "1.0.0"},
                            "spans": spans,
                        }
                    ],
                }
            ]
        }

        body = json.dumps(payload).encode()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        if self.api_key:
            headers["api_key"] = self.api_key

        try:
            req = urllib.request.Request(
                self.endpoint, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
            return True
        except Exception:
            return False


def auto_instrument(
    endpoint: str = "",
    api_key: str = "",
    project: str = "",
) -> PhoenixExporter:
    """Register a global PhoenixExporter that fires after every Mesh.run().

    Call once at application startup::

        from meshflow.observability.arize_phoenix import auto_instrument
        auto_instrument()   # reads env vars automatically

    After this, every ``await Mesh(...).run(task)`` exports traces to Phoenix.

    Returns the exporter instance so you can also call ``exporter.export_run()``
    manually.
    """
    exporter = PhoenixExporter(endpoint=endpoint, api_key=api_key, project=project)

    # Monkey-patch Mesh.run to fire the exporter after every call
    try:
        from meshflow.core.mesh import Mesh
        _original_run = Mesh.run

        async def _instrumented_run(self: Mesh, task: str, **kwargs: Any) -> Any:  # type: ignore[override]
            result = await _original_run(self, task, **kwargs)
            try:
                exporter.export_run(result.run_id)
            except Exception:
                pass
            return result

        Mesh.run = _instrumented_run  # type: ignore[method-assign]
    except Exception:
        pass

    return exporter


__all__ = ["PhoenixExporter", "auto_instrument"]
