# Distributed Tracing

MeshFlow emits W3C-compatible trace spans that flow across agent-to-agent calls and persist to SQLite for post-hoc audit.

```python
from meshflow.tracing import TraceStore, Tracer, SpanKind, SpanStatus

store = TraceStore("meshflow_traces.db")
tracer = Tracer(store)

# Start a root span
span, ctx = tracer.start_span("my-workflow", kind=SpanKind.ROOT, agent_name="orchestrator")

# Start a child span, passing the parent context
child, child_ctx = tracer.start_span("llm-call", kind=SpanKind.LLM, parent=ctx)
tracer.finish_span(child)

tracer.finish_span(span)

# Retrieve all spans for this trace
spans = tracer.get_trace(ctx.trace_id)
```

## `TraceContext`

Immutable W3C `traceparent`-compatible carrier that flows between services.

```python
from meshflow.tracing import TraceContext

# Create a new root context (new trace_id)
ctx = TraceContext.new_root()
print(ctx.trace_id)        # 32-char hex
print(ctx.span_id)         # 16-char hex
print(ctx.traceparent())   # "00-<trace_id>-<span_id>-01"

# Propagate across an HTTP call
headers = {"traceparent": ctx.traceparent()}

# Parse inbound header on the receiving side
incoming = TraceContext.from_traceparent(request.headers["traceparent"])

# Derive a child context (same trace_id, new span_id)
child_ctx = TraceContext.child(ctx)
```

| Field | Type | Description |
|---|---|---|
| `trace_id` | str | 32-char hex; shared across the entire distributed trace |
| `span_id` | str | 16-char hex; unique to this context |
| `sampled` | bool | Maps to the `01`/`00` traceparent flag |

## `Span`

A timed unit of work within a trace.

| Field | Type | Description |
|---|---|---|
| `span_id` | str | 16-char hex identifier |
| `trace_id` | str | Parent trace |
| `parent_id` | str or None | `span_id` of the parent span |
| `name` | str | Human-readable operation name |
| `kind` | `SpanKind` | Span classification (see below) |
| `start_ts` | float | Unix timestamp (seconds) |
| `end_ts` | float or None | Set by `finish()` |
| `status` | `SpanStatus` | `OK`, `ERROR`, or `UNSET` |
| `agent_name` | str | Agent that produced this span |
| `run_id` | str | Workflow run identifier |
| `error` | str | Error message if `status=ERROR` |
| `attributes` | dict | Arbitrary key-value metadata |
| `duration_ms` | float or None | Computed from `end_ts - start_ts` |
| `is_finished` | bool | `end_ts is not None` |

```python
span.finish(status=SpanStatus.ERROR, error="Timeout after 5 s")
span.to_dict()  # serialisable dict
```

## `SpanKind`

| Value | String | Use case |
|---|---|---|
| `SpanKind.ROOT` | `"root"` | Top-level workflow entry point |
| `SpanKind.AGENT` | `"agent"` | Agent `run()` call |
| `SpanKind.TOOL` | `"tool"` | Tool invocation |
| `SpanKind.LLM` | `"llm"` | Direct LLM API call |
| `SpanKind.A2A` | `"a2a"` | Agent-to-agent handoff |
| `SpanKind.GUARDRAIL` | `"guardrail"` | Policy / guardrail check |
| `SpanKind.INTERNAL` | `"internal"` | Internal bookkeeping |

## `SpanStatus`

| Value | Meaning |
|---|---|
| `SpanStatus.OK` | Completed successfully |
| `SpanStatus.ERROR` | Failed; `span.error` contains details |
| `SpanStatus.UNSET` | Not yet finished |

## `TraceStore`

SQLite-backed span repository. All reads/writes are thread-safe via WAL mode.

```python
store = TraceStore("meshflow_traces.db")
store = TraceStore(":memory:")  # in-process; useful for tests
```

| Method | Returns | Description |
|---|---|---|
| `save(span)` | None | Insert or replace a span |
| `get(span_id)` | `Span \| None` | Fetch one span by ID |
| `get_trace(trace_id)` | `list[Span]` | All spans for a trace, ordered by `start_ts` |
| `get_for_run(run_id)` | `list[Span]` | All spans for a workflow run |
| `count(trace_id="")` | int | Total span count (optionally scoped to a trace) |

## `Tracer`

Creates and finishes spans, propagating `TraceContext` through the hierarchy.

```python
tracer = Tracer(store)

span, ctx = tracer.start_span(
    name="summarise",
    kind=SpanKind.AGENT,
    parent=parent_ctx,   # None for a new root trace
    agent_name="summariser",
    run_id="run-abc123",
    attributes={"input_len": 1500},
)

# ... do work ...

tracer.finish_span(span, status=SpanStatus.OK)
tracer.finish_span(span, status=SpanStatus.ERROR, error="Model timeout")

all_spans = tracer.get_trace(ctx.trace_id)
```

## CLI

```bash
# Show all spans for a trace
meshflow tracing show <trace_id>

# Run a workflow and display its trace
meshflow tracing run my_workflow.py

# Count spans in the default database
meshflow tracing count
```
