"""@traceable decorator and LangfuseExporter — LangSmith-compatible observability.

``@traceable`` wraps any function (sync or async) and emits a structured
trace span whenever it is called.  Spans are forwarded to the configured
exporter (default: in-process ``SpanStore``; optional: Langfuse, OTLP).

Usage::

    from meshflow.observability.traceable import traceable

    @traceable(name="summarise", run_type="chain")
    async def summarise(text: str) -> str:
        return agent.run(f"Summarise: {text}")

    # Runs are automatically traced and forwarded to the exporter.

    # Configure Langfuse export:
    from meshflow.observability.traceable import LangfuseExporter, set_exporter
    set_exporter(LangfuseExporter(public_key="pk-...", secret_key="sk-..."))

LangSmith compatibility
-----------------------
``@traceable`` accepts the same keyword arguments as the LangSmith decorator
so that existing LangSmith-instrumented code can be migrated by changing
only the import line.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Generator

# ── Trace span dataclass ──────────────────────────────────────────────────────

@dataclass
class TraceSpan:
    """A single recorded invocation."""

    span_id:    str
    name:       str
    run_type:   str           # "chain" | "llm" | "tool" | "retriever" | "agent"
    inputs:     dict[str, Any]
    outputs:    dict[str, Any] = field(default_factory=dict)
    error:      str | None    = None
    start_time: float         = field(default_factory=time.time)
    end_time:   float | None  = None
    latency_ms: float         = 0.0
    parent_id:  str | None    = None
    metadata:   dict[str, Any] = field(default_factory=dict)
    tags:       list[str]     = field(default_factory=list)

    def finish(self, output: Any = None, error: str | None = None) -> None:
        self.end_time  = time.time()
        self.latency_ms = round((self.end_time - self.start_time) * 1000, 2)
        if output is not None:
            self.outputs = {"output": output} if not isinstance(output, dict) else output
        if error:
            self.error = error


# ── Exporter protocol ─────────────────────────────────────────────────────────

class TraceExporter:
    """Base exporter — override :meth:`export` to send spans anywhere."""

    def export(self, span: TraceSpan) -> None:
        pass

    async def aexport(self, span: TraceSpan) -> None:
        self.export(span)


class _InProcessExporter(TraceExporter):
    """Default exporter: stores spans in the in-process SpanStore."""

    def export(self, span: TraceSpan) -> None:
        try:
            from meshflow.observability.genai import get_span_store
            store = get_span_store()
            # Map to the existing SpanRecord format
            store.add_raw(span.__dict__)
        except Exception:
            pass  # never crash user code


class LangfuseExporter(TraceExporter):
    """Export traces to Langfuse (https://langfuse.com).

    Requires ``langfuse`` (``pip install langfuse``).

    Parameters
    ----------
    public_key:
        Langfuse project public key.
    secret_key:
        Langfuse project secret key.
    host:
        Langfuse host. Defaults to ``https://cloud.langfuse.com``.
    """

    def __init__(
        self,
        public_key: str = "",
        secret_key: str = "",
        host: str = "https://cloud.langfuse.com",
    ) -> None:
        import os
        self._pk   = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        self._sk   = secret_key or os.environ.get("LANGFUSE_SECRET_KEY", "")
        self._host = host or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from langfuse import Langfuse  # type: ignore[import]
                self._client = Langfuse(
                    public_key=self._pk,
                    secret_key=self._sk,
                    host=self._host,
                )
            except ImportError as exc:
                raise ImportError(
                    "LangfuseExporter requires langfuse: pip install langfuse"
                ) from exc
        return self._client

    def export(self, span: TraceSpan) -> None:
        try:
            lf = self._get_client()
            trace = lf.trace(
                id=span.span_id,
                name=span.name,
                input=span.inputs,
                output=span.outputs,
                metadata=span.metadata,
                tags=span.tags,
            )
            if span.run_type == "llm":
                trace.generation(
                    name=span.name,
                    input=span.inputs,
                    output=span.outputs,
                    usage=span.metadata.get("usage"),
                )
            lf.flush()
        except Exception:
            pass  # non-fatal — Langfuse errors should never crash user code


class MeshFlowCloudExporter(TraceExporter):
    """Export spans as AgentRun records to the meshflow.dev dashboard."""

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        from meshflow.cloud import MeshFlowCloud
        self._cloud = MeshFlowCloud(api_key=api_key, base_url=base_url or "")

    def export(self, span: TraceSpan) -> None:
        if span.run_type in ("agent", "chain"):
            self._cloud.report_run({
                "run_id":        span.span_id,
                "workflow_name": span.name,
                "agent_count":   1,
                "total_cost_usd": span.metadata.get("cost_usd", 0.0),
                "total_tokens":  span.metadata.get("tokens", 0),
                "status":        "failed" if span.error else "completed",
                "duration_ms":   int(span.latency_ms),
                "violations":    0,
            })


# ── Global exporter registry ──────────────────────────────────────────────────

_exporters: list[TraceExporter] = [_InProcessExporter()]


def set_exporter(exporter: TraceExporter, *, replace: bool = True) -> None:
    """Set the global trace exporter.

    Parameters
    ----------
    exporter:
        The exporter to install.
    replace:
        If True (default), replace all existing exporters.
        If False, add to the existing list.
    """
    global _exporters
    if replace:
        _exporters = [exporter]
    else:
        _exporters.append(exporter)


def add_exporter(exporter: TraceExporter) -> None:
    """Add an exporter without replacing existing ones."""
    set_exporter(exporter, replace=False)


def _emit(span: TraceSpan) -> None:
    for exp in _exporters:
        try:
            exp.export(span)
        except Exception:
            pass


# ── @traceable ────────────────────────────────────────────────────────────────

_current_span_id: Any = None  # simple single-level parent tracking


def traceable(
    fn: Callable | None = None,
    *,
    name: str = "",
    run_type: str = "chain",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Decorator that records a trace span for every function invocation.

    Can be used bare or with keyword arguments::

        @traceable
        async def my_chain(x: str) -> str: ...

        @traceable(name="summarise", run_type="llm", tags=["prod"])
        async def summarise(text: str) -> str: ...

    LangSmith-compatible arguments:
    --------------------------------
    name      — display name in the trace UI (defaults to function name)
    run_type  — "chain" | "llm" | "tool" | "retriever" | "agent" | "prompt"
    tags      — list of string tags
    metadata  — additional key-value metadata
    """
    if fn is not None:
        return _wrap(fn, name=name, run_type=run_type, tags=tags or [], metadata=metadata or {})

    def _decorator(f: Callable) -> Callable:
        return _wrap(f, name=name, run_type=run_type, tags=tags or [], metadata=metadata or {})

    return _decorator


def _wrap(
    fn: Callable,
    name: str,
    run_type: str,
    tags: list[str],
    metadata: dict[str, Any],
) -> Callable:
    span_name = name or fn.__name__

    @functools.wraps(fn)
    async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
        global _current_span_id
        span = TraceSpan(
            span_id=str(uuid.uuid4()),
            name=span_name,
            run_type=run_type,
            inputs=_capture_inputs(fn, args, kwargs),
            parent_id=_current_span_id,
            metadata=dict(metadata),
            tags=list(tags),
        )
        prev = _current_span_id
        _current_span_id = span.span_id
        try:
            result = await fn(*args, **kwargs)
            span.finish(result)
            return result
        except Exception as exc:
            span.finish(error=str(exc))
            raise
        finally:
            _current_span_id = prev
            _emit(span)

    @functools.wraps(fn)
    def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        global _current_span_id
        span = TraceSpan(
            span_id=str(uuid.uuid4()),
            name=span_name,
            run_type=run_type,
            inputs=_capture_inputs(fn, args, kwargs),
            parent_id=_current_span_id,
            metadata=dict(metadata),
            tags=list(tags),
        )
        prev = _current_span_id
        _current_span_id = span.span_id
        try:
            result = fn(*args, **kwargs)
            span.finish(result)
            return result
        except Exception as exc:
            span.finish(error=str(exc))
            raise
        finally:
            _current_span_id = prev
            _emit(span)

    if inspect.iscoroutinefunction(fn):
        return _async_wrapper
    return _sync_wrapper


def _capture_inputs(fn: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
    """Map positional args to their parameter names."""
    try:
        sig    = inspect.signature(fn)
        params = list(sig.parameters.keys())
        inputs = {params[i]: args[i] for i in range(min(len(args), len(params)))}
        inputs.update(kwargs)
        return {k: _safe_repr(v) for k, v in inputs.items()}
    except Exception:
        return {"args": str(args)[:200]}


def _safe_repr(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    if isinstance(v, (list, tuple)) and len(v) <= 10:
        return [_safe_repr(x) for x in v]
    return str(v)[:200]


# ── Context manager for manual span creation ─────────────────────────────────

@contextmanager
def trace_span(
    name: str,
    run_type: str = "chain",
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Generator[TraceSpan, None, None]:
    """Context manager for manual span creation.

    Usage::

        with trace_span("my_step", run_type="tool") as span:
            result = do_work()
            span.metadata["tokens"] = 100
    """
    global _current_span_id
    span = TraceSpan(
        span_id=str(uuid.uuid4()),
        name=name,
        run_type=run_type,
        inputs={},
        parent_id=_current_span_id,
        metadata=metadata or {},
        tags=tags or [],
    )
    prev = _current_span_id
    _current_span_id = span.span_id
    try:
        yield span
        span.finish()
    except Exception as exc:
        span.finish(error=str(exc))
        raise
    finally:
        _current_span_id = prev
        _emit(span)
