"""L2.8 — OpenTelemetry observability: one trace_id across every layer.

Every agent turn, MCP call, RAG retrieval, memory operation, and dasc-gate
decision emits a span. One trace_id from entry prompt to final output.
Exportable to any OTLP backend (Jaeger, Grafana Tempo, Langfuse, Honeycomb).
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Status, StatusCode
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    # Stub types so type annotations elsewhere still resolve at import time
    trace = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[assignment]
    BatchSpanProcessor = None  # type: ignore[assignment]
    ConsoleSpanExporter = None  # type: ignore[assignment]
    InMemorySpanExporter = None  # type: ignore[assignment]
    Status = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]


def _make_status(ok: bool, message: str = "") -> Any:
    """Build an OTEL Status object, or None when opentelemetry is not installed."""
    if not _OTEL_AVAILABLE:
        return None
    if ok:
        return Status(StatusCode.OK)
    return Status(StatusCode.ERROR, message)


# ── Span names — consistent across all MeshFlow layers ───────────────────────
class SpanName:
    MESH_RUN = "meshflow.run"
    AGENT_STEP = "meshflow.agent.step"
    DASC_GATE = "meshflow.dasc_gate.evaluate"
    GUARDIAN_SCAN = "meshflow.guardian.scan"
    MCP_CALL = "meshflow.mcp.call"
    RAG_RETRIEVE = "meshflow.rag.retrieve"
    MEMORY_READ = "meshflow.memory.read"
    MEMORY_WRITE = "meshflow.memory.write"
    IDENTITY_VERIFY = "meshflow.identity.verify"
    UNCERTAINTY_EVAL = "meshflow.uncertainty.evaluate"
    COLLUSION_AUDIT = "meshflow.collusion.audit"
    CHECKPOINT = "meshflow.graph.checkpoint"


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
        otlp_protocol: str = "",
    ) -> None:
        self._service_name = service_name
        self._otlp_error = ""
        self._records: list[SpanRecord] = []
        self._otel_available = _OTEL_AVAILABLE

        if not _OTEL_AVAILABLE:
            self._provider = None
            self._tracer = None
            self._in_memory = None
            self._otlp_endpoint = ""
            self._otlp_protocol = ""
            return

        self._in_memory = InMemorySpanExporter()
        self._otlp_endpoint = self._resolve_otlp_endpoint(otlp_endpoint)
        self._otlp_protocol = self._resolve_otlp_protocol(otlp_protocol)
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(self._in_memory))
        if export_to_console:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        if self._otlp_endpoint:
            self._add_otlp(provider, self._otlp_endpoint, self._otlp_protocol)
        self._provider = provider
        self._tracer = provider.get_tracer(service_name)

    @property
    def otlp_enabled(self) -> bool:
        """True when an OTLP span exporter was configured successfully."""
        return bool(self._otlp_endpoint and not self._otlp_error)

    @property
    def otlp_endpoint(self) -> str:
        return self._otlp_endpoint

    @property
    def otlp_protocol(self) -> str:
        return self._otlp_protocol

    @property
    def otlp_error(self) -> str:
        return self._otlp_error

    def _resolve_otlp_endpoint(self, endpoint: str) -> str:
        return (
            endpoint
            or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        )

    def _resolve_otlp_protocol(self, protocol: str) -> str:
        configured = (
            protocol
            or os.getenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "")
            or os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "")
            or "grpc"
        )
        return configured.lower().replace("_", "-")

    def _resolve_otlp_headers(self) -> dict[str, str]:
        raw = os.getenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "") or os.getenv(
            "OTEL_EXPORTER_OTLP_HEADERS", ""
        )
        headers: dict[str, str] = {}
        for pair in raw.split(","):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            key = key.strip()
            if key:
                headers[key] = value.strip()
        return headers

    def _add_otlp(self, provider: TracerProvider, endpoint: str, protocol: str) -> None:
        headers = self._resolve_otlp_headers() or None
        try:
            if protocol in {"http", "http/protobuf"}:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter as HTTPOTLPSpanExporter,
                )

                http_exporter = HTTPOTLPSpanExporter(
                    endpoint=self._http_trace_endpoint(endpoint),
                    headers=headers,
                )
                provider.add_span_processor(BatchSpanProcessor(http_exporter))
            elif protocol == "grpc":
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter as GRPCOTLPSpanExporter,
                )

                grpc_exporter = GRPCOTLPSpanExporter(endpoint=endpoint, headers=headers)
                provider.add_span_processor(BatchSpanProcessor(grpc_exporter))
            else:
                self._otlp_error = f"unsupported_otlp_protocol:{protocol}"
                return
        except Exception as exc:
            self._otlp_error = str(exc)

    def _http_trace_endpoint(self, endpoint: str) -> str:
        if os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
            return endpoint
        return endpoint.rstrip("/") + "/v1/traces"

    def _set_attributes(self, otel_span: trace.Span, attributes: dict[str, Any]) -> None:
        for k, v in attributes.items():
            if v is None:
                continue
            if isinstance(v, (str, bool, int, float)):
                otel_span.set_attribute(k, v)
            else:
                otel_span.set_attribute(k, str(v))

    def _emit_otel_span(
        self,
        name: str,
        attributes: dict[str, Any],
        *,
        run_id: str = "",
        agent_id: str = "",
        status: Any = None,
    ) -> None:
        if not self._otel_available or self._tracer is None:
            return
        with self._tracer.start_as_current_span(name) as otel_span:
            standard_attrs: dict[str, Any] = {}
            if run_id:
                standard_attrs["meshflow.run_id"] = run_id
            if agent_id:
                standard_attrs["meshflow.agent_id"] = agent_id
            self._set_attributes(otel_span, standard_attrs)
            self._set_attributes(otel_span, attributes)
            otel_span.set_status(status or Status(StatusCode.OK))

    @contextmanager
    def span(
        self,
        name: str,
        run_id: str = "",
        agent_id: str = "",
        trace_id: str = "",
        **attributes: Any,
    ) -> Generator[Any, None, None]:
        """Context manager that creates a span with standard MeshFlow attributes.

        When opentelemetry-sdk is not installed, this is a no-op context manager.
        """
        if not self._otel_available or self._tracer is None:
            yield None
            return
        with self._tracer.start_as_current_span(name) as otel_span:
            if run_id:
                otel_span.set_attribute("meshflow.run_id", run_id)
            if agent_id:
                otel_span.set_attribute("meshflow.agent_id", agent_id)
            if trace_id:
                otel_span.set_attribute("meshflow.trace_id", trace_id)
            self._set_attributes(otel_span, attributes)
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
        self._emit_otel_span(
            SpanName.AGENT_STEP,
            record.attributes,
            run_id=run_id,
            agent_id=agent_id,
            status=_make_status(success),
        )

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
        self._emit_otel_span(
            SpanName.MCP_CALL,
            record.attributes,
            run_id=run_id,
            agent_id=agent_id,
            status=_make_status(not blocked, block_reason),
        )

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
        self._emit_otel_span(
            SpanName.DASC_GATE,
            {**record.attributes, "dasc.intent_id": intent_id},
            run_id=run_id,
            agent_id=agent_id,
            status=_make_status(verdict == "commit", verdict),
        )

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
        self._emit_otel_span(SpanName.RAG_RETRIEVE, record.attributes, run_id=run_id)

    def spans(self) -> list[SpanRecord]:
        return list(self._records)

    def span_count(self) -> int:
        return len(self._records)

    def otel_spans(self) -> list[Any]:
        if not self._otel_available or self._in_memory is None:
            return []
        self.force_flush()
        return list(self._in_memory.get_finished_spans())

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

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        if self._provider is None:
            return True
        return self._provider.force_flush(timeout_millis=timeout_millis)

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()


# ── Module-level default tracer (lazy-init) ───────────────────────────────────
_default_tracer: MeshFlowTracer | None = None


def get_tracer() -> MeshFlowTracer:
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = MeshFlowTracer()
    return _default_tracer
