# Webhooks & Alerts

MeshFlow sends HMAC-signed webhook events for governance, cost, and HITL notifications.

## Webhook events

| Event | Trigger |
|-------|---------|
| `policy_violation` | A step is blocked by a policy rule |
| `budget_exceeded` | Cost cap guardrail fires |
| `hitl_pending` | Workflow pauses for human approval |
| `collusion_alert` | SwarmTRM detects agent collusion |
| `sla_breach` | Agent latency exceeds SLA contract |

## Register a webhook

```python
from meshflow.core.webhooks import WebhookManager

wm = WebhookManager(ledger=ledger)
wm.register(
    url="https://your-app.com/meshflow-events",
    secret="hmac-secret",
    events=["policy_violation", "hitl_pending"],
    tenant_id="acme",
)
```

Via CLI:
```bash
meshflow webhooks add https://your-app.com/events --secret hmac-secret \
  --events policy_violation hitl_pending
meshflow webhooks list
meshflow webhooks remove <id>
```

## Payload format

```json
{
  "event": "policy_violation",
  "run_id": "run-abc123",
  "node_id": "classify-step",
  "tenant_id": "acme",
  "timestamp": "2026-05-30T14:23:01Z",
  "reason": "Free tier cost cap exceeded",
  "rule_name": "block-free-tier-expensive-calls"
}
```

## Signature verification

```python
import hmac, hashlib

def verify(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

# Header: X-MeshFlow-Signature: sha256=<hex>
```

## Durable retry queue

`WebhookRetryQueue` ensures delivery with 3 automatic retries and exponential backoff:

```python
from meshflow import WebhookRetryQueue, WebhookReliableDeliverer

queue = WebhookRetryQueue("webhook_queue.db")
deliverer = WebhookReliableDeliverer(queue)

# Enqueue (non-blocking)
await deliverer.send(url="https://...", payload=event_dict, secret="hmac-secret")
```

## Alert engine

```python
from meshflow import AlertEngine, AlertRule, MetricStore

store = MetricStore()
engine = AlertEngine(store)

rule = AlertRule(
    name="high-cost-alert",
    metric="agent.cost_usd",
    threshold=1.0,
    window_seconds=60,
    webhook_url="https://your-app.com/alerts",
)
engine.add_rule(rule)
engine.evaluate()   # check all rules against recent metrics
```
