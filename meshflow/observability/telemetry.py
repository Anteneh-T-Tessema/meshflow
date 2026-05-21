"""L2.8 — OpenTelemetry observability: one trace_id across every layer.

Every agent turn, MCP call, RAG retrieval, memory operation, and dasc-gate
decision emits a span. One trace_id from entry prompt to final output.
Exportable to any OTLP backend (Jaeger, Grafana Tempo, Langfuse, Honeycomb).
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode


# ── Span names — consistent across all MeshFlow layers ───────────────────────
class SpanName:
    MESH_RUN         = "meshflow.run"
    AGENT_STEP       = "meshflow.agent.step"
    DASC_GATE        = "meshflow.dasc_gate.evaluate"
    GUARDIAN_SCAN    = "meshflow.guardian.scan"
    MCP_CALL         = "meshflow.mcp.call"
    RAG_RETRIEVE     = "meshflow.rag.retrieve"
    MEMORY_READ      = "meshflow.memory.read"
    MEMORY_WRITE     = "meshflow.memory.write"
    IDENTITY_VERIFY  = "meshflow.identity.verify"
    UNCERTAINTY_EVAL = "meshflow.uncertainty.evaluate"
    COLLUSION_AUDIT  = "meshflow.collusion.audit"
    CHECKPOINT       = "meshflow.graph.checkpoint"


@dataclass
class SpanRecord:
    """Lightweight in-process span record — used when OTLP is not configured."""
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def duration_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.monotonic() - self.start_time) * 1000


class MeshFlowTracer:
    """Wraps OpenTelemetry tracer with MeshFlow-specific helpers.

    Usage:
        tracer = MeshFlowTracer(service_name="meshflow", export_to_console=False)
        with tracer.span(SpanName.AGENT_STEP, run_id="...", agent_id="...") as span:
            span.set_attribute("agent.role", "executor")
            ...
    """

    def __init__(
        self,
        service_name: str = "meshflow",
        export_to_console: bool = False,
        otlp_endpoint: str = "",
    ) -> None:
        self._service_name = service_name
        self._in_memory = InMemorySpanExporter()
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(self._in_memory))
        if export_to_console:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        if otlp_endpoint:
            self._add_otlp(provider, otlp_endpoint)
        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer(service_name)
        self._records: list[SpanRecord] = []

    def _add_otlp(self, provider: TracerProvider, endpoint: str) -> None:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            pass  # OTLP not available — fall back to in-memory only

    @contextmanager
    def span(
        self,
        name: str,
        run_id: str = "",
        agent_id: str = "",
        trace_id: str = "",
        **attributes: Any,
    ) -> Generator[trace.Span, None, None]:
        """Context manager that creates a span with standard MeshFlow attributes."""
        with self._tracer.start_as_current_span(name) as otel_span:
            if run_id:
                otel_span.set_attribute("meshflow.run_id", run_id)
            if agent_id:
                otel_span.set_attribute("meshflow.agent_id", agent_id)
            if trace_id:
                otel_span.set_attribute("meshflow.trace_id", trace_id)
            for k, v in attributes.items():
                otel_span.set_attribute(k, str(v))
            try:
                yield otel_span
                otel_span.set_status(Status(StatusCode.OK))
            except Exception as e:
                otel_span.set_status(Status(StatusCode.ERROR, str(e)))
                otel_span.record_exception(e)
                raise

    def record_agent_step(
        self,
        run_id: str,
        agent_id: str,
        role: str,
        tokens: int,
        cost_usd: float,
        duration_ms: float,
        success: bool,
    ) -> None:
        record = SpanRecord(
            name=SpanName.AGENT_STEP,
            trace_id=run_id,
            span_id=agent_id,
            attributes={
                "agent.role": role,
                "agent.tokens": tokens,
                "agent.cost_usd": cost_usd,
                "agent.duration_ms": duration_ms,
                "agent.success": success,
            },
        )
        record.end_time = time.monotonic()
        record.status = "ok" if success else "error"
        self._records.append(record)

    def record_mcp_call(
        self,
        run_id: str,
        tool_name: str,
        agent_id: str,
        latency_ms: float,
        blocked: bool,
        block_reason: str = "",
    ) -> None:
        record = SpanRecord(
            name=SpanName.MCP_CALL,
            trace_id=run_id,
            span_id=f"{agent_id}:{tool_name}",
            attributes={
                "mcp.tool_name": tool_name,
                "mcp.agent_id": agent_id,
                "mcp.latency_ms": latency_ms,
                "mcp.blocked": blocked,
                "mcp.block_reason": block_reason,
            },
        )
        record.end_time = time.monotonic()
        record.status = "error" if blocked else "ok"
        self._records.append(record)

    def record_dasc_decision(
        self,
        run_id: str,
        intent_id: str,
        agent_id: str,
        effective_tier: int,
        verdict: str,
    ) -> None:
        record = SpanRecord(
            name=SpanName.DASC_GATE,
            trace_id=run_id,
            span_id=intent_id,
            attributes={
                "dasc.agent_id": agent_id,
                "dasc.effective_tier": effective_tier,
                "dasc.verdict": verdict,
            },
        )
        record.end_time = time.monotonic()
        record.status = "ok" if verdict == "commit" else "error"
        self._records.append(record)

    def record_rag_retrieval(
        self,
        run_id: str,
        query: str,
        num_chunks: int,
        faithfulness: float,
        latency_ms: float,
    ) -> None:
        record = SpanRecord(
            name=SpanName.RAG_RETRIEVE,
            trace_id=run_id,
            span_id=f"rag:{hash(query) & 0xFFFF:04x}",
            attributes={
                "rag.query_len": len(query),
                "rag.chunks_returned": num_chunks,
                "rag.faithfulness": faithfulness,
                "rag.latency_ms": latency_ms,
            },
        )
        record.end_time = time.monotonic()
        self._records.append(record)

    def spans(self) -> list[SpanRecord]:
        return list(self._records)

    def span_count(self) -> int:
        return len(self._records)

    def export_summary(self) -> dict[str, Any]:
        if not self._records:
            return {"spans": 0}
        by_name: dict[str, int] = {}
        total_ms = 0.0
        errors = 0
        for r in self._records:
            by_name[r.name] = by_name.get(r.name, 0) + 1
            total_ms += r.duration_ms()
            if r.status == "error":
                errors += 1
        return {
            "total_spans": len(self._records),
            "error_spans": errors,
            "total_duration_ms": round(total_ms, 2),
            "by_span_type": by_name,
        }


# ── Module-level default tracer (lazy-init) ───────────────────────────────────
_default_tracer: MeshFlowTracer | None = None


def get_tracer() -> MeshFlowTracer:
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = MeshFlowTracer()
    return _default_tracer
