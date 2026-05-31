# Metrics & OpenTelemetry

MeshFlow exports spans via OTLP and exposes Prometheus-compatible metrics with zero required dependencies.

## OTEL Span Export

```python
from meshflow import OTELExporter, set_global_exporter, configure_telemetry

# One-call setup
configure_telemetry(endpoint="http://localhost:4318")

# Or manually
exporter = OTELExporter(endpoint="http://tempo:4318", service_name="my-agents")
set_global_exporter(exporter)

# Check status
from meshflow import otel_is_enabled
print(otel_is_enabled())  # True
```

Via CLI:
```bash
meshflow serve --otlp-endpoint http://localhost:4318
```

## Manual Span Creation

```python
from meshflow import otel_span

async with otel_span("my-operation", attributes={"agent.name": "researcher"}) as span:
    result = await agent.run("research task")
    span.set_attribute("result.tokens", result["tokens"])
```

## GenAI Semantic Conventions

```python
from meshflow import record_agent_step, record_tool_call, record_guardrail, GenAI, MF

# Record spans with OpenTelemetry GenAI conventions
await record_agent_step(agent_id="researcher", task="summarize", tokens=150, cost=0.003)
await record_tool_call(tool_name="web_search", success=True, duration_ms=420)
await record_guardrail(name="PIIBlock", passed=True)

# Attribute key constants
GenAI.SYSTEM          # "gen_ai.system"
GenAI.INPUT_TOKENS    # "gen_ai.usage.input_tokens"
MF.AGENT_ID           # "meshflow.agent.id"
MF.TENANT_ID          # "meshflow.tenant.id"
```

## Span Store (in-process)

```python
from meshflow import get_span_store, SpanStore, GenAISpanRecord

store: SpanStore = get_span_store()
records: list[GenAISpanRecord] = store.list()
store.clear()
```

## Prometheus Metrics

```python
from meshflow import MetricsCollector

collector = MetricsCollector()
collector.increment("agent.runs", labels={"agent": "researcher"})
collector.gauge("agent.active", value=3)
collector.histogram("agent.latency_ms", value=245)

# Prometheus text format
print(collector.render())
```

## Live Exporter Status

```bash
# GET /otel/config returns:
{
  "enabled": true,
  "endpoint": "http://localhost:4318",
  "service_name": "meshflow",
  "spans_exported": 1847
}
```
