# MeshFlow GDPR Compliance Guide

MeshFlow provides built-in data subject rights primitives for GDPR Article 17
(right to erasure) and Article 20 (right to data portability).

---

## Lawful Basis for Processing

MeshFlow logs agent reasoning steps and outputs.  Before deploying to process
personal data of EU/EEA data subjects, establish a lawful basis:

- **Legitimate interests** (Art. 6(1)(f)) — for internal tooling where data
  subjects would reasonably expect processing.
- **Contract** (Art. 6(1)(b)) — where the agent task directly fulfils a
  contractual obligation to the data subject.
- **Consent** (Art. 6(1)(a)) — obtain freely given, specific, informed, and
  unambiguous consent where other bases do not apply.

---

## Right to Erasure (Art. 17)

Delete all ledger records for a specific run:

```python
from meshflow.core.ledger import ReplayLedger

ledger = ReplayLedger(db_path="runs.db")
await ledger.delete_run("run-abc-123")
```

Delete all records for a tenant (organisation-level erasure):

```python
await ledger.delete_tenant("tenant-acme")
```

> After deletion, the run IDs are removed from the index and the rows are
> permanently deleted from the backing store.  This operation is irreversible.
> Retain deletion records (run ID + timestamp + legal basis) separately for
> demonstrating compliance.

---

## Right to Portability (Art. 20)

Export a run's full trace as structured JSON:

```bash
meshflow trace <run-id> --export run_<run-id>.json
```

The exported JSON contains all step records including timestamps, inputs,
outputs, and hash chain values in a machine-readable format that can be
transmitted to another controller.

---

## Data Minimisation (Art. 5(1)(c))

Use `Policy.max_output_chars` to limit the volume of personal data retained
in audit records:

```python
from meshflow.core.schemas import Policy

policy = Policy(max_output_chars=500)  # retain only first 500 chars of output
```

Large outputs (>10 KB) are automatically compressed in the ledger.  This does
not reduce data subject rights obligations but reduces storage footprint.

---

## Anonymisation

Where erasure is not possible (e.g. the run is part of an immutable financial
audit trail), replace outputs with anonymised placeholders:

```python
await ledger.anonymize_run("run-abc-123")
```

This replaces `output_content` with `[ANONYMIZED]` while preserving the
structural integrity of the hash chain for audit purposes.  Anonymised data
falls outside the GDPR's scope per Recital 26.

---

## Multi-Tenancy and Data Isolation

Use per-tenant ledger scoping to enforce data isolation:

```python
tenant_ledger = ReplayLedger(db_path="runs.db", tenant_id="acme")
```

All writes and reads are automatically scoped to the `acme` tenant.  Cross-
tenant access is not possible through the standard API.

---

## Data Retention

Implement a retention policy using a scheduled job:

```python
import asyncio
from datetime import datetime, timedelta
from meshflow.core.ledger import ReplayLedger

async def purge_old_runs(db_path: str, retention_days: int = 90) -> None:
    ledger = ReplayLedger(db_path=db_path)
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    runs = await ledger.list_runs()
    for run_id in runs:
        trace = await ledger.get_trace(run_id)
        if trace and trace["summary"]["timestamps"]["start"] < cutoff.isoformat():
            await ledger.delete_run(run_id)
```

---

## Data Processing Agreement (DPA)

When using MeshFlow as a processor on behalf of another controller:

1. Execute a DPA covering the categories of data, purposes, and sub-processors.
2. List LLM API providers (Anthropic, OpenAI, Google, AWS) as sub-processors.
3. Ensure sub-processor DPAs cover EU Standard Contractual Clauses (SCCs) for
   international transfers if the provider is based outside the EEA.

---

## Transfer Mechanisms

If using cloud-hosted LLM APIs, data may be transferred outside the EEA.
Use the appropriate transfer mechanism:

- **SCCs** — execute the EU Commission's standard contractual clauses with the
  LLM provider.
- **Adequacy decision** — US providers covered by the EU-US Data Privacy
  Framework (where applicable).
- **Binding Corporate Rules** — for intra-group transfers within a multinational.

---

## Records of Processing Activities (Art. 30)

Include MeshFlow in your RoPA:

| Field | Value |
|---|---|
| Processing activity | AI agent task execution and audit logging |
| Controller | Your organisation |
| Processor | MeshFlow deployment (self-hosted or managed) |
| Sub-processors | Anthropic, OpenAI, Google, AWS (as applicable) |
| Data categories | Task inputs, agent outputs, timestamps, cost metadata |
| Retention period | Per your retention policy (default: indefinite) |
| Transfer safeguards | SCCs / adequacy decision (per LLM provider) |
