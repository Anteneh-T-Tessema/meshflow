# MeshFlow Developer Quickstart

Get governed multi-agent workflows running in 5 minutes.

## Install

```bash
pip install meshflow
# With server support:
pip install "meshflow[server]"   # adds aiohttp
# With crypto (API key management, agent identity):
pip install "meshflow[crypto]"   # adds cryptography
```

Or install from source:

```bash
git clone https://github.com/Anteneh-T-Tessema/meshflow
cd meshflow
pip install -e .
```

---

## 1. Your first governed run

```python
import asyncio
from meshflow.core.mesh import Mesh
from meshflow.core.schemas import policy_for_mode

async def main():
    policy = policy_for_mode("standard", budget_usd=0.10, max_steps=5)
    mesh = Mesh(policy=policy)
    result = await mesh.run("Summarise the key risks in this contract: ...")
    print(result.output)
    print(f"Cost: ${result.total_cost_usd:.4f}  |  Steps: {result.ledger_entries}")

asyncio.run(main())
```

---

## 2. Multi-agent team

```python
from meshflow import Agent, Team

researcher = Agent("researcher", role="Research specialist")
writer     = Agent("writer",     role="Content writer")
critic     = Agent("critic",     role="Quality reviewer")

team = Team([researcher, writer, critic], pattern="supervised")
result = asyncio.run(team.run("Write a market analysis for LLM governance tools"))
```

---

## 3. Policy-as-code (YAML)

```yaml
# meshflow.policy.yaml
mode: legal-critical
budget_usd: 5.0
compliance:
  frameworks: [hipaa, sox]
  block_on_violation: true
```

```python
from meshflow.core.policy_loader import load_policy_yaml, load_guard_yaml

policy = load_policy_yaml("meshflow.policy.yaml")
guard  = load_guard_yaml("meshflow.policy.yaml")
result = await mesh.run(task, policy=policy)
```

---

## 4. Start the HTTP server

```bash
# Dev mode (no auth, in-memory ledger)
meshflow dev

# Production mode
export MESHFLOW_API_KEYS="mfk_your_key_here"
meshflow serve --host 0.0.0.0 --port 8000 --ledger runs.db

# With policy file
meshflow serve --policy-file meshflow.policy.yaml
```

### Generate API keys

```bash
# Create an admin key (first key bootstraps the system)
meshflow keys generate "bootstrap-admin" --role admin --db runs.db

# Create an operator key for CI
meshflow keys generate "ci-pipeline" --role operator --db runs.db
```

---

## 5. Key endpoints

```bash
BASE=http://localhost:8000
KEY=mfk_your_key_here

# Run a task
curl -X POST $BASE/run \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"task": "Analyse the attached contract", "policy": {"mode": "legal-critical"}}'

# Stream events (NDJSON)
curl -N -X POST $BASE/stream \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"task": "Research market trends"}'

# Check who you are
curl $BASE/keys/whoami -H "Authorization: Bearer $KEY"

# Compliance report
curl "$BASE/compliance/report?framework=hipaa&format=text" \
  -H "Authorization: Bearer $KEY"

# Register a webhook
curl -X POST $BASE/webhooks \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://hooks.example.com/meshflow", "events": ["policy_violation", "run_failed"]}'
```

---

## 6. Compliance modes

| Mode | Use case |
|---|---|
| `standard` | General AI tasks, default guardrails |
| `regulated` | Financial services, additional controls |
| `legal-critical` | Contract review, legal advice — maximum oversight |
| `hipaa` | Healthcare, PHI scrubbing, audit trail |

```python
policy = policy_for_mode("hipaa", budget_usd=2.0)
```

---

## 7. Human-in-the-loop

```python
from meshflow.core.schemas import HumanInLoopConfig, RiskTier

policy = policy_for_mode("legal-critical")
# HITL triggers automatically when risk_tier >= IRREVERSIBLE
# Approve via API:
# POST /hitl/{run_id}/approve  {"reviewer_id": "alice", "notes": "Reviewed and approved"}
```

---

## 8. Deploy to Kubernetes

```bash
# Using Helm
helm install meshflow ./k8s/helm \
  --set apiKeys="mfk_your_key" \
  --set webhookSecret="your-secret"

# Using raw manifests
kubectl apply -f k8s/deployment.yaml
```

---

## 9. OTEL tracing

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=my-meshflow
meshflow serve --port 8000
# Spans are now exported per step to your OTLP collector
```

---

## Resources

- [HIPAA Deployment Guide](compliance/HIPAA_GUIDE.md)
- [GDPR Guide](compliance/GDPR_GUIDE.md)
- [SOC2 Controls Mapping](compliance/SOC2_CONTROLS_MAPPING.md)
- [Security Policy](compliance/SECURITY.md)
- [Golden Standard Platform Plan](golden-standard-platform.md)
- [Benchmarks](../benchmarks/README.md)
