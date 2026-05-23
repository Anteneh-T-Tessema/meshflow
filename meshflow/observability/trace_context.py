"""W3C Trace Context (RFC 7230) propagation helpers.

Parses and generates ``traceparent`` / ``tracestate`` headers so MeshFlow
can participate in distributed traces originating from any OTEL-instrumented
caller (API gateway, service mesh, browser).

Format: ``00-<trace-id-hex32>-<parent-id-hex16>-<flags-hex2>``
"""
from __future__ import annotations

import os
import re
import struct
import uuid
from dataclasses import dataclass, field

_TRACEPARENT_RE = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)


@dataclass
class TraceContext:
    trace_id: str        # 32-hex char W3C trace ID
    span_id: str         # 16-hex char span ID (the *parent* span of this request)
    flags: str = "01"    # sampled=1 by default
    tracestate: str = "" # vendor-specific state string (passed through opaquely)

    @classmethod
    def new(cls) -> "TraceContext":
        """Generate a brand-new root trace context."""
        trace_id = uuid.uuid4().hex + uuid.uuid4().hex[:0]  # 32 hex chars
        # Use a proper 32-char trace_id
        trace_id = "%032x" % int.from_bytes(os.urandom(16), "big")
        span_id  = "%016x" % int.from_bytes(os.urandom(8),  "big")
        return cls(trace_id=trace_id, span_id=span_id)

    @classmethod
    def from_header(cls, traceparent: str, tracestate: str = "") -> "TraceContext | None":
        """Parse a ``traceparent`` header; return None if malformed."""
        m = _TRACEPARENT_RE.match(traceparent.strip().lower())
        if not m:
            return None
        return cls(
            trace_id=m.group(1),
            span_id=m.group(2),
            flags=m.group(3),
            tracestate=tracestate,
        )

    def to_header(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"

    def child_span_id(self) -> str:
        """Generate a new span ID for a child span of this context."""
        return "%016x" % int.from_bytes(os.urandom(8), "big")

    def to_dict(self) -> dict[str, str]:
        d = {"traceparent": self.to_header(), "trace_id": self.trace_id, "span_id": self.span_id}
        if self.tracestate:
            d["tracestate"] = self.tracestate
        return d


def extract_trace_context(headers: dict[str, str]) -> TraceContext:
    """Extract W3C trace context from HTTP headers, creating a new one if absent."""
    raw = headers.get("traceparent") or headers.get("Traceparent") or ""
    tracestate = headers.get("tracestate") or headers.get("Tracestate") or ""
    ctx = TraceContext.from_header(raw, tracestate) if raw else None
    return ctx if ctx is not None else TraceContext.new()


def inject_trace_headers(ctx: TraceContext) -> dict[str, str]:
    """Return headers dict to forward to downstream services."""
    h = {"traceparent": ctx.to_header()}
    if ctx.tracestate:
        h["tracestate"] = ctx.tracestate
    return h
