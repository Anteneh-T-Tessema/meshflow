# Kubernetes / Helm

MeshFlow ships a production Helm chart at `k8s/helm/`.

## Install

```bash
helm install meshflow ./k8s/helm \
  --set apiKey=$ANTHROPIC_API_KEY \
  --set replicaCount=3 \
  --namespace meshflow \
  --create-namespace
```

## Key Helm values

| Value | Default | Description |
|-------|---------|-------------|
| `replicaCount` | `1` | Number of replicas |
| `apiKey` | `""` | `ANTHROPIC_API_KEY` (use Secrets in production) |
| `image.tag` | `"1.0.0"` | MeshFlow container image tag |
| `service.port` | `8000` | HTTP port |
| `persistence.enabled` | `true` | Enable SQLite PVC |
| `persistence.size` | `"10Gi"` | PVC size |
| `otel.endpoint` | `""` | OTLP endpoint URL |
| `resources.limits.memory` | `"1Gi"` | Container memory limit |

## Health probes (auto-configured)

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
```

`/health/live` — process is running  
`/health/ready` — ledger is open, policy loaded, provider reachable

## Using Kubernetes Secrets

```bash
kubectl create secret generic meshflow-keys \
  --from-literal=ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY

helm install meshflow ./k8s/helm \
  --set apiKeySecretName=meshflow-keys \
  --set apiKeySecretKey=ANTHROPIC_API_KEY
```

## Graceful shutdown

MeshFlow handles `SIGTERM` by:
1. Stopping new request acceptance
2. Draining in-flight requests (30s grace)
3. Flushing the webhook retry queue
4. Closing the ledger connection

Configure via `terminationGracePeriodSeconds` in the Helm values.

## Upgrade

```bash
helm upgrade meshflow ./k8s/helm --set image.tag=1.1.0
```
