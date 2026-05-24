"""Sprint 41 — GenAI semantic conventions + live agent instrumentation.

Every agent step, handoff, tool call, and guardrail check emits a span with
standard OpenTelemetry GenAI semantic-convention attributes.  Works with any
OTLP backend (Datadog, Grafana, Jaeger, Honeycomb, Langfuse).

Zero-config when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.
Falls back to in-process span recording when it isn't.

GenAI semantic attributes emitted (OpenTelemetry GenAI SIG, 2025)::

    gen_ai.system              "anthropic" | "openai" | "google" | ...
    gen_ai.operation.name      "chat"
    gen_ai.request.model       "claude-sonnet-4-6"
    gen_ai.usage.input_tokens  150
    gen_ai.usage.output_tokens 200

MeshFlow-specific attributes::

    meshflow.agent.name        "researcher"
    meshflow.agent.role        "executor"
    meshflow.agent.confidence  0.9
    meshflow.agent.cost_usd    0.002
    meshflow.agent.blocked     false
    meshflow.handoff.from      "triage"
    meshflow.handoff.to        "billing"
    meshflow.handoff.reason    "needs deep analysis"
    meshflow.tool.name         "web_search"
    meshflow.tool.risk_tier    "external_io"
    meshflow.guardrail.name    "PIIBlockGuardrail"
    meshflow.guardrail.blocked true

Usage::

    from meshflow.observability.genai import configure_telemetry, get_span_store

    configure_telemetry(service_name="my-app")  # one-time setup

    # Spans are now emitted automatically on every Agent.run() call.
    # View them:
    spans = get_span_store().all()
    print(spans[0].attributes["gen_ai.usage.input_tokens"])
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator


# ── GenAI semantic convention keys ────────────────────────────────────────────

class GenAI:
    SYSTEM          = "gen_ai.system"
    OPERATION       = "gen_ai.operation.name"
    REQUEST_MODEL   = "gen_ai.request.model"
    REQUEST_MAXTOK  = "gen_ai.request.max_tokens"
    RESPONSE_MODEL  = "gen_ai.response.model"
    INPUT_TOKENS    = "gen_ai.usage.input_tokens"
    OUTPUT_TOKENS   = "gen_ai.usage.output_tokens"
    TOTAL_TOKENS    = "gen_ai.usage.total_tokens"


class MF:
    AGENT_NAME      = "meshflow.agent.name"
    AGENT_ROLE      = "meshflow.agent.role"
    AGENT_CONFIDENCE= "meshflow.agent.confidence"
    AGENT_COST      = "meshflow.agent.cost_usd"
    AGENT_BLOCKED   = "meshflow.agent.blocked"
    RUN_ID          = "meshflow.run_id"
    HANDOFF_FROM    = "meshflow.handoff.from"
    HANDOFF_TO      = "meshflow.handoff.to"
    HANDOFF_REASON  = "meshflow.handoff.reason"
    TOOL_NAME       = "meshflow.tool.name"
    TOOL_RISK       = "meshflow.tool.risk_tier"
    GUARDRAIL_NAME  = "meshflow.guardrail.name"
    GUARDRAIL_BLOCK = "meshflow.guardrail.blocked"
    HEALING_ATTEMPT = "meshflow.healing.attempt"
    HEALING_STRATEGY= "meshflow.healing.strategy"
    TASK_ID         = "meshflow.a2a.task_id"
    TASK_STATE      = "meshflow.a2a.task_state"


# ── Span record ────────────────────────────────────────────────────────────────

@dataclass
class GenAISpanRecord:
    """Lightweight in-process span record."""

    name: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_span_id: str = ""
    start_ns: int = field(default_factory=lambda: int(time.time() * 1e9))
    end_ns: int = 0
    status: str = "ok"           # "ok" | "error"
    error_message: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def finish(self, status: str = "ok", error: str = "") -> None:
        self.end_ns = int(time.time() * 1e9)
        self.status = status
        self.error_message = error

    @property
    def duration_ms(self) -> float:
        end = self.end_ns or int(time.time() * 1e9)
        return (end - self.start_ns) / 1_000_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "error": self.error_message,
            "attributes": dict(self.attributes),
        }


# ── In-process span store ─────────────────────────────────────────────────────

class SpanStore:
    """Thread-safe in-process store for emitted spans."""

    def __init__(self, max_spans: int = 10_000) -> None:
        import threading
        self._spans: list[GenAISpanRecord] = []
        self._lock = threading.Lock()
        self._max = max_spans

    def record(self, span: GenAISpanRecord) -> None:
        with self._lock:
            if len(self._spans) >= self._max:
                self._spans.pop(0)
            self._spans.append(span)

    def all(self) -> list[GenAISpanRecord]:
        with self._lock:
            return list(self._spans)

    def by_name(self, name: str) -> list[GenAISpanRecord]:
        return [s for s in self.all() if s.name == name]

    def by_trace(self, trace_id: str) -> list[GenAISpanRecord]:
        return [s for s in self.all() if s.trace_id == trace_id]

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._spans)

    def summary(self) -> dict[str, Any]:
        spans = self.all()
        if not spans:
            return {"total": 0}
        by_name: dict[str, int] = {}
        errors = 0
        total_ms = 0.0
        for s in spans:
            by_name[s.name] = by_name.get(s.name, 0) + 1
            if s.status == "error":
                errors += 1
            total_ms += s.duration_ms
        return {
            "total": len(spans),
            "errors": errors,
            "total_duration_ms": round(total_ms, 2),
            "by_name": by_name,
        }


# ── Global state ───────────────────────────────────────────────────────────────

_store = SpanStore()
_enabled: bool = bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("MESHFLOW_OTEL"))
_service_name: str = os.getenv("OTEL_SERVICE_NAME", "meshflow")
_current_trace_id: str = ""


def configure_telemetry(
    *,
    service_name: str = "meshflow",
    enabled: bool = True,
    otlp_endpoint: str = "",
) -> None:
    """One-call telemetry setup.  Call once at application startup.

    Parameters
    ----------
    service_name:   ``service.name`` attribute in all spans.
    enabled:        Set False to disable all span emission.
    otlp_endpoint:  OTLP/HTTP endpoint override (also reads
                    ``OTEL_EXPORTER_OTLP_ENDPOINT`` from env).
    """
    global _enabled, _service_name
    _enabled = enabled
    _service_name = service_name
    if otlp_endpoint:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_endpoint


def get_span_store() -> SpanStore:
    return _store


def is_enabled() -> bool:
    return _enabled


# ── Span context manager ───────────────────────────────────────────────────────

@contextmanager
def span(
    name: str,
    attributes: dict[str, Any] | None = None,
    parent_trace_id: str = "",
) -> Generator[GenAISpanRecord, None, None]:
    """Context manager that emits one GenAI span.

    Usage::

        with span("meshflow.agent.step", {GenAI.REQUEST_MODEL: "claude-sonnet-4-6"}) as s:
            s.attributes[GenAI.INPUT_TOKENS] = 150
            # ... run agent ...
    """
    rec = GenAISpanRecord(
        name=name,
        trace_id=parent_trace_id or _current_trace_id or uuid.uuid4().hex[:16],
        attributes=dict(attributes or {}),
    )
    try:
        yield rec
        rec.finish("ok")
    except Exception as exc:
        rec.finish("error", str(exc))
        raise
    finally:
        if _enabled:
            _store.record(rec)
            _maybe_export_otlp(rec)


def _maybe_export_otlp(rec: GenAISpanRecord) -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        from meshflow.observability.otel_exporter import get_global_exporter
        exporter = get_global_exporter()
        exporter.export_span(
            trace_id=rec.trace_id,
            span_id=rec.span_id,
            name=rec.name,
            start_ns=rec.start_ns,
            end_ns=rec.end_ns or int(time.time() * 1e9),
            attributes=rec.attributes,
            status=rec.status,
            parent_span_id=rec.parent_span_id,
        )
    except Exception:
        pass


# ── Convenience span emitters ─────────────────────────────────────────────────

def record_agent_step(
    agent_name: str,
    role: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    confidence: float,
    blocked: bool,
    run_id: str = "",
    trace_id: str = "",
) -> GenAISpanRecord:
    """Emit a completed agent-step span and return it."""
    rec = GenAISpanRecord(
        name="meshflow.agent.step",
        trace_id=trace_id or uuid.uuid4().hex[:16],
        attributes={
            GenAI.SYSTEM:         _infer_system(model),
            GenAI.OPERATION:      "chat",
            GenAI.REQUEST_MODEL:  model,
            GenAI.INPUT_TOKENS:   tokens_in,
            GenAI.OUTPUT_TOKENS:  tokens_out,
            GenAI.TOTAL_TOKENS:   tokens_in + tokens_out,
            MF.AGENT_NAME:        agent_name,
            MF.AGENT_ROLE:        role,
            MF.AGENT_COST:        cost_usd,
            MF.AGENT_CONFIDENCE:  confidence,
            MF.AGENT_BLOCKED:     blocked,
            MF.RUN_ID:            run_id,
        },
    )
    rec.finish("error" if blocked else "ok")
    if _enabled:
        _store.record(rec)
        _maybe_export_otlp(rec)
    return rec


def record_handoff(
    from_agent: str,
    to_agent: str,
    reason: str = "",
    trace_id: str = "",
) -> GenAISpanRecord:
    rec = GenAISpanRecord(
        name="meshflow.handoff",
        trace_id=trace_id or uuid.uuid4().hex[:16],
        attributes={
            MF.HANDOFF_FROM:   from_agent,
            MF.HANDOFF_TO:     to_agent,
            MF.HANDOFF_REASON: reason,
        },
    )
    rec.finish("ok")
    if _enabled:
        _store.record(rec)
        _maybe_export_otlp(rec)
    return rec


def record_tool_call(
    tool_name: str,
    agent_name: str,
    risk_tier: str = "",
    success: bool = True,
    trace_id: str = "",
) -> GenAISpanRecord:
    rec = GenAISpanRecord(
        name="meshflow.tool.call",
        trace_id=trace_id or uuid.uuid4().hex[:16],
        attributes={
            MF.TOOL_NAME:   tool_name,
            MF.AGENT_NAME:  agent_name,
            MF.TOOL_RISK:   risk_tier,
        },
    )
    rec.finish("ok" if success else "error")
    if _enabled:
        _store.record(rec)
        _maybe_export_otlp(rec)
    return rec


def record_guardrail(
    guardrail_name: str,
    agent_name: str,
    blocked: bool,
    trace_id: str = "",
) -> GenAISpanRecord:
    rec = GenAISpanRecord(
        name="meshflow.guardrail.check",
        trace_id=trace_id or uuid.uuid4().hex[:16],
        attributes={
            MF.GUARDRAIL_NAME:  guardrail_name,
            MF.AGENT_NAME:      agent_name,
            MF.GUARDRAIL_BLOCK: blocked,
        },
    )
    rec.finish("error" if blocked else "ok")
    if _enabled:
        _store.record(rec)
        _maybe_export_otlp(rec)
    return rec


def record_healing_attempt(
    agent_name: str,
    attempt: int,
    strategy: str,
    success: bool,
    trace_id: str = "",
) -> GenAISpanRecord:
    rec = GenAISpanRecord(
        name="meshflow.healing.attempt",
        trace_id=trace_id or uuid.uuid4().hex[:16],
        attributes={
            MF.AGENT_NAME:       agent_name,
            MF.HEALING_ATTEMPT:  attempt,
            MF.HEALING_STRATEGY: strategy,
        },
    )
    rec.finish("ok" if success else "error")
    if _enabled:
        _store.record(rec)
        _maybe_export_otlp(rec)
    return rec


# ── Helpers ────────────────────────────────────────────────────────────────────

def _infer_system(model: str) -> str:
    model_l = model.lower()
    if "claude" in model_l or "anthropic" in model_l:
        return "anthropic"
    if "gpt" in model_l or "openai" in model_l:
        return "openai"
    if "gemini" in model_l or "google" in model_l:
        return "google"
    if "llama" in model_l or "ollama" in model_l:
        return "meta"
    return "unknown"


__all__ = [
    "GenAI", "MF",
    "GenAISpanRecord", "SpanStore",
    "configure_telemetry", "get_span_store", "is_enabled",
    "span",
    "record_agent_step", "record_handoff", "record_tool_call",
    "record_guardrail", "record_healing_attempt",
]
