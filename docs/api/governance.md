# meshflow — Governance API Reference

Compliance, policy, vault, tenant isolation, SLA, and audit primitives.

## Compliance Profiles

```python
from meshflow import compliance_profile, list_profiles, ComplianceProfile

# One-line application
policy = compliance_profile("hipaa")
agent = Agent(name="clinical", role="executor", policy=policy)

# Available profiles
list_profiles()
# → ["hipaa", "sox", "gdpr", "pci", "nerc"]

# Inspect a profile
p: ComplianceProfile = compliance_profile("gdpr")
print(p.name, p.rules)
```

| Profile | Key enforcement |
|---------|----------------|
| `hipaa` | PHI masking, minimum necessary access, audit required |
| `sox` | Financial immutability, dual control, audit export |
| `gdpr` | PII detection, data minimization, right-to-erasure hooks |
| `pci` | PAN masking, no card data in logs, vault required |
| `nerc` | Critical infrastructure isolation, strict rate limits |

## Policy-as-Code Engine

```python
from meshflow import PolicyEngine, PolicyLoader, PolicyCondition, PolicyRule

# Load from YAML
loader = PolicyLoader()
engine = loader.load_file("policies/production.yaml")

# Evaluate
decision = engine.evaluate({"user_tier": "free", "cost_usd": 5.0})
print(decision.action)   # "DENY" | "ALLOW"
print(decision.reason)
```

YAML format:
```yaml
rules:
  - name: block-free-tier-expensive-calls
    conditions:
      - field: user_tier
        op: eq
        value: free
      - field: cost_usd
        op: gt
        value: 1.0
    action: DENY
    reason: "Free tier cost cap exceeded"
```

Condition operators: `eq`, `ne`, `gt`, `lt`, `gte`, `lte`, `contains`, `exists`, `not_exists`, `regex`

## VaultStore

```python
from meshflow import VaultStore, VaultSecret

vault = VaultStore("vault.db", master_password="change-me-in-prod")

# Store
vault.store("db_password", "s3cr3t!", metadata={"env": "prod"})

# Retrieve
secret: VaultSecret = vault.retrieve("db_password")
print(secret.value)

# Rotate
vault.rotate("db_password", "new-s3cr3t!")

# Audit
log: VaultAuditLog = vault.audit("db_password")

# CLI
# meshflow vault store db_password
# meshflow vault retrieve db_password
# meshflow vault rotate db_password
# meshflow vault list
# meshflow vault audit db_password
```

## Tenant Isolation

```python
from meshflow import TenantContext, TenantStore, TenantGuard, scoped_db_path

# Set current tenant (thread-local)
TenantContext.set("acme-corp")

# Scoped DB path — each tenant gets its own SQLite file
path = scoped_db_path("runs.db")  # → "runs_acme-corp.db"

# Tenant store
store = TenantStore("tenants.db")
store.create("acme-corp", plan="enterprise")
store.suspend("bad-actor")

# Guard (raises if tenant not active)
guard = TenantGuard()
guard.check()    # raises TenantSuspendedError if suspended

# CLI
# meshflow tenant create acme-corp --plan enterprise
# meshflow tenant list
# meshflow tenant suspend bad-actor
```

## SLA Tracking

```python
from meshflow import SLAContract, SLATracker, SLABreach, SLAStats

contract = SLAContract(
    agent_id="clinical-assistant",
    p50_ms=200,
    p95_ms=800,
    p99_ms=2000,
)

tracker = SLATracker("sla.db")
tracker.define(contract)
tracker.record("clinical-assistant", duration_ms=150)

stats: SLAStats = tracker.stats("clinical-assistant")
# stats.p50_ms, stats.p95_ms, stats.p99_ms
# breach detection requires ≥10 observations

breaches: list[SLABreach] = tracker.breaches("clinical-assistant")

# CLI
# meshflow sla define clinical-assistant --p95 800
# meshflow sla stats clinical-assistant
# meshflow sla breaches
```

## Compliance Snapshots

```python
from meshflow import SnapshotExporter, SnapshotBundle, SnapshotManifest

exporter = SnapshotExporter(
    ledger=ledger,
    vault=vault,
    tenant_store=tenant_store,
    sla_tracker=tracker,
    policy_store=policy_store,
)

bundle: SnapshotBundle = exporter.export()
bundle.save("compliance_bundle_2026-05.zip")

# Contents: manifest.json, audit_trail.csv, policies.json,
#           tenants.json, sla_stats.json (vault values never exported)

# CLI
# meshflow snapshot export --out compliance_bundle.zip
```

## Distributed Tracing

```python
from meshflow import TraceContext, Span, SpanKind, SpanStatus, Tracer, TraceStore

tracer = Tracer(TraceStore("traces.db"))
with tracer.start_span("my-operation", kind=SpanKind.INTERNAL) as span:
    span.set_attribute("agent.name", "researcher")
    # ... work ...
    span.set_status(SpanStatus.OK)

# CLI
# meshflow tracing show <trace_id>
# meshflow tracing count
```

## Agent Identity (Zero-Trust)

```python
from meshflow import AgentIdentity, AgentToken, sign_token, verify_token, decode_token

identity = AgentIdentity(agent_id="researcher", role="executor", tenant_id="acme")
token: AgentToken = sign_token(identity, secret="shared-secret")

# Verify on receiver side
verified: AgentIdentity = verify_token(token.value, secret="shared-secret")
payload = decode_token(token.value)
```
