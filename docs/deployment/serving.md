# Serving the HTTP API

`meshflow serve` starts a FastAPI server exposing REST, SSE, and WebSocket endpoints.

## Start

```bash
meshflow serve \
  --host 0.0.0.0 \
  --port 8000 \
  --db meshflow_runs.db \
  --policy-file policies/production.yaml \
  --otlp-endpoint http://localhost:4318
```

For development with colored output:
```bash
meshflow dev
```

## Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health/live` | Liveness probe — 200 if process alive |
| `GET` | `/health/ready` | Readiness probe — 200 if ready to serve |
| `GET` | `/events` | SSE stream of all workflow events |
| `GET` | `/ws/bus` | WebSocket message bus |
| `GET` | `/runs` | List recent run IDs |
| `GET` | `/runs/{run_id}` | Get all steps for a run |
| `POST` | `/runs/{run_id}/approve` | Approve a paused HITL checkpoint |
| `GET` | `/eval-results` | List stored eval results |
| `GET` | `/plugins` | Discovered plugins |
| `GET` | `/analytics` | Workflow analytics (last N runs) |
| `GET` | `/otel/config` | Live OTEL exporter status |
| `GET` | `/compliance/report` | Generate compliance report |
| `GET` | `/graph/{run_id}` | Workflow graph for a run |
| `GET` | `/sla` | SLA stats across all agents |

## CLI client

```bash
meshflow logs              # recent run history
meshflow replay <run_id>   # step-through debugger
meshflow approve <run_id>  # approve HITL checkpoint
```

## Python client

```python
from meshflow import MeshFlowClient

client = MeshFlowClient("http://localhost:8000", api_key="mf-...")
result = client.run_agent("assistant", "What is 2 + 2?")
print(result.output)

# SSE streaming
for event in client.stream_events():
    print(event.event_type, event.data)
```

## API keys

```bash
meshflow keys generate --name prod-key --role operator
# → mf-xxxxxxxxxxxxxxxx

# Use in client
MeshFlowClient("http://...", api_key="mf-xxxxxxxxxxxxxxxx")
# Or via header: Authorization: Bearer mf-...
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `MESHFLOW_HOST` | Default host (overrides --host) |
| `MESHFLOW_PORT` | Default port (overrides --port) |
| `MESHFLOW_DB_PATH` | Ledger database path |
| `MESHFLOW_POLICY_FILE` | Policy YAML path |
| `MESHFLOW_OTLP_ENDPOINT` | OTLP span export endpoint |
