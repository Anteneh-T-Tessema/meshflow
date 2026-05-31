# Docker Deployment

MeshFlow ships a `DockerDeployer` and CLI for containerized deployments.

## Quick start

```bash
# Build and run with Docker
docker run -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -p 8000:8000 \
  meshflow/meshflow:1.0.0 \
  meshflow serve --host 0.0.0.0 --port 8000
```

## meshflow deploy CLI

```bash
meshflow deploy --image meshflow/meshflow:1.0.0 \
  --env ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  --port 8000 \
  --name meshflow-prod
```

## DockerDeployer API

```python
from meshflow import DockerDeployer, DeployResult

deployer = DockerDeployer()
result: DeployResult = deployer.run(
    image="meshflow/meshflow:1.0.0",
    env={"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]},
    ports={"8000": "8000"},
    name="meshflow-prod",
    detach=True,
)
print(result.container_id, result.url)
```

## Compose example

```yaml
version: "3.9"
services:
  meshflow:
    image: meshflow/meshflow:1.0.0
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - MESHFLOW_DB_PATH=/data/runs.db
      - MESHFLOW_OTLP_ENDPOINT=http://otel-collector:4318
    ports:
      - "8000:8000"
    volumes:
      - meshflow_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/live"]
      interval: 30s
      timeout: 5s
      retries: 3
volumes:
  meshflow_data:
```

## Multi-stage Dockerfile

```dockerfile
FROM python:3.12-slim AS base
RUN pip install "meshflow[full]==1.0.0"

FROM base AS app
WORKDIR /app
COPY workflow.yaml .
EXPOSE 8000
CMD ["meshflow", "serve", "--host", "0.0.0.0", "--port", "8000"]
```
