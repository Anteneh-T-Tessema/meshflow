# MeshFlow HIPAA Deployment Guide

MeshFlow supports HIPAA-compliant deployments through the `hipaa` policy mode,
PHI scrubbing, immutable audit chains, and mandatory human review gates.

---

## Quick Start

```python
from meshflow.core.mesh import Mesh
from meshflow.core.schemas import policy_for_mode

policy = policy_for_mode("hipaa", budget_usd=5.0)

async with Mesh(policy=policy) as mesh:
    result = await mesh.run("Summarise patient discharge summary", context={...})
```

The `hipaa` preset enforces:

| Control | Setting |
|---|---|
| Human-in-the-loop | Required for all irreversible outputs |
| PHI scrubbing | Enabled — outputs sanitised before ledger write |
| Immutable audit | SHA-256 chained entries, tamper-evident |
| Collusion detection | Enabled |
| Budget cap | $2.00 USD (override via `budget_usd=`) |

---

## PHI Scrubbing

Set `scrub_phi=True` on any `Policy` object, or use the `hipaa` mode which
enables it automatically.  The `PHIScrubber` replaces all 18 HIPAA Safe Harbor
identifier categories detectable by regex before any output reaches the ledger.

Identifier categories covered:
- Names, addresses, geographic subdivisions ≤ county
- Dates (except year) for individuals ≥ 90
- Phone, fax, email, SSN, MRN, NPI, DEA
- Account/certificate/license numbers
- Web URLs, IP addresses, device identifiers
- Biometric data labels, photograph descriptions
- Full-face photographs (text descriptions only)

> **Note**: PHI scrubbing operates on text output only. Images, audio, or binary
> attachments must be handled separately via your data pipeline.

---

## Immutable Audit Trail

Every step is SHA-256 chained.  The chain can be verified from the CLI:

```bash
meshflow trace <run-id>
```

The `CHAIN VALID` indicator confirms no ledger tampering since the run completed.

For long-term retention, export the ledger entry as JSON:

```bash
meshflow trace <run-id> --export audit_<run-id>.json
```

Store exported files in WORM storage (S3 Object Lock, Azure Blob immutability
policy, or equivalent).

---

## Human-in-the-Loop (HITL) Gates

The `hipaa` preset requires human review for all outputs classified as
`IRREVERSIBLE` risk tier.  Paused runs are visible via:

```bash
meshflow hitl list
```

Approve or reject:

```bash
meshflow hitl approve <run-id> --reviewer alice --notes "Reviewed and approved"
meshflow hitl reject  <run-id> --reviewer alice --notes "Escalated to physician"
```

Configure automatic escalation by setting a timeout in the server environment:

```
MESHFLOW_HITL_TIMEOUT_S=3600   # 1 hour before auto-escalation
```

---

## Network and Transport Security

Run the server with TLS:

```bash
meshflow serve \
  --tls-cert /etc/meshflow/tls/cert.pem \
  --tls-key  /etc/meshflow/tls/key.pem \
  --host 0.0.0.0 --port 8443
```

Restrict API access with `MESHFLOW_API_KEYS` (comma-separated).  Keys must be
distributed to authorised clients only and rotated at least quarterly.

---

## Kubernetes Deployment (HIPAA)

1. Store secrets in Kubernetes Secrets (or a vault integration):

```bash
kubectl create secret generic meshflow-secrets \
  --from-literal=api-keys="$(openssl rand -hex 32)" \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY"
```

2. Apply the manifests:

```bash
kubectl apply -f k8s/
```

3. Enable network policies to restrict egress to LLM API endpoints only.

4. Use a `ReadWriteOnce` PVC backed by encrypted storage (AWS EBS with KMS,
   GCE PD with CMEK, or equivalent).

---

## Business Associate Agreement (BAA)

Before processing real PHI:

1. Execute a BAA with Anthropic if using Claude models.
2. Execute a BAA with OpenAI if using GPT models.
3. Ensure your hosting provider (AWS/GCP/Azure) has a BAA in place covering
   compute and storage services used by MeshFlow.

MeshFlow itself is a data processor under HIPAA.  Your organisation is the
covered entity or business associate responsible for executing appropriate BAAs.

---

## Minimum Necessary Standard

Configure `Policy.max_output_chars` to limit the volume of PHI retained in
audit records.  The `hipaa` preset does not cap output length by default —
set an appropriate limit for your use case:

```python
policy = policy_for_mode("hipaa", max_output_chars=2000)
```

---

## Incident Response

If a potential PHI breach is detected:

1. Use `ReplayLedger.anonymize_run(run_id)` to replace stored outputs with
   `[ANONYMIZED]` markers while preserving the audit chain structure.
2. Preserve the original hash chain for forensic analysis before anonymizing.
3. Notify affected individuals within 60 days per the HIPAA Breach Notification
   Rule (45 CFR §164.400–414).
