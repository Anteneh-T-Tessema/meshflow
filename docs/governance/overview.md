# Governance Overview

MeshFlow's governance kernel is the `StepRuntime` — every agent step passes through 15 sequential checks before execution completes.

## The 15-Step Governance Kernel

1. **Identity verification** — agent token validation (zero-trust)
2. **Tenant scoping** — isolate data by tenant
3. **Rate limiting** — per-agent and per-team token-bucket
4. **Budget check** — cost quota enforcement before execution
5. **Policy evaluation** — policy-as-code rules (DENY wins)
6. **Compliance profile** — framework-specific rules (HIPAA/SOX/GDPR/PCI/NERC)
7. **Input guardrails** — PII block, injection detection, keyword filter
8. **Sensitive data scan** — 23 PHI/PII + credential patterns
9. **Risk classification** — AutoRiskClassifier (4 tiers, EMA failure rate)
10. **Taint propagation** — information flow control (DASC)
11. **Tool permission check** — GovernedToolRegistry audit
12. **Execution** — actual LLM call
13. **Output guardrails** — length, toxicity, JSON schema, regex
14. **Audit ledger** — SHA-256 hash chain append
15. **SLA record** — latency sample recorded for p50/p95/p99

## Applying a Compliance Profile

```python
from meshflow import Agent, compliance_profile

agent = Agent(
    name="clinical-assistant",
    role="You answer clinical questions.",
    policy=compliance_profile("hipaa"),
)
```

Built-in profiles:

| Profile | Key rules |
|---------|-----------|
| `hipaa` | PHI masking, minimum necessary access, audit trail required |
| `sox`   | Financial data immutability, dual control, audit export |
| `gdpr`  | PII detection, data minimization, right-to-erasure hooks |
| `pci`   | PAN masking, no card data in logs, encrypted vault required |
| `nerc`  | Critical infrastructure isolation, strict rate limits |

## Compliance Snapshots

Export a full compliance artifact bundle (GDPR Art.30, HIPAA §164.312) at any time:

```bash
meshflow snapshot export --out compliance_bundle.zip
```

The bundle includes: audit trail, policy definitions, tenant list, SLA stats, and a signed manifest — all in one ZIP.
