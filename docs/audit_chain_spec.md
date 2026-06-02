# MeshFlow Audit Chain Specification

**Version:** 1.0
**Status:** Stable

This document defines the canonical format for MeshFlow's tamper-evident audit
chain so that third-party tooling — SIEM integrations, compliance verifiers, and
independent auditors — can verify chain integrity without importing any MeshFlow
code.

---

## 1. Overview

Every agent step produces a `StepRecord`. Records are written to the
`ReplayLedger` (SQLite / PostgreSQL / S3 backends). Each record contains two
hash fields:

| Field | Description |
|---|---|
| `prev_hash` | SHA-256 of the *previous* record's canonical fields (empty string `""` for the first record in a run) |
| `entry_hash` | SHA-256 of *this* record's canonical fields |

Modifying any field in any record breaks the chain. The break is detectable by
any verifier that can read the raw records and recompute hashes.

---

## 2. Canonical Field Set

The following fields are included in the hash computation, **in this order**,
with the constraints noted:

| Field | Type | Constraint |
|---|---|---|
| `run_id` | string | as stored |
| `step_id` | string | as stored |
| `node_id` | string | as stored |
| `input_task` | string | **first 200 characters only** |
| `output_content` | string | **first 200 characters only** |
| `verdict` | string | one of `"commit"` / `"reject"` / `"escalate"` |
| `blocked` | boolean | `true` or `false` |
| `timestamp` | string | ISO 8601 UTC, as stored |
| `prev_hash` | string | SHA-256 hex of the previous record, or `""` |

---

## 3. Hash Algorithm

```
entry_hash = SHA-256( JSON_CANONICAL( canonical_fields ) )
```

Where `JSON_CANONICAL` means:

1. Serialize the canonical field set as a JSON object with **`sort_keys=True`**.
2. Encode the resulting JSON string as **UTF-8**.
3. Apply **SHA-256**.
4. Hex-encode the digest (lowercase, no prefix).

### Python reference (3 lines)

```python
import hashlib, json

def compute_entry_hash(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
```

### Canonical fields dict (Python)

```python
canonical = {
    "run_id":         record["run_id"],
    "step_id":        record["step_id"],
    "node_id":        record["node_id"],
    "input_task":     str(record["input_task"])[:200],
    "output_content": str(record["output_content"])[:200],
    "verdict":        record["verdict"],
    "blocked":        bool(record["blocked"]),
    "timestamp":      record["timestamp"],
    "prev_hash":      record["prev_hash"],
}
```

---

## 4. Chain Verification Rules

Given the ordered list of `StepRecord`s for a single `run_id`:

1. For record `i=0`: `prev_hash` MUST equal `""`.
2. For record `i>0`: `prev_hash` MUST equal `entry_hash` of record `i-1`.
3. For every record: `entry_hash` MUST equal `compute_entry_hash(canonical_fields)`.

Any violation indicates the record was modified after it was written.

---

## 5. Reference Verifier

A self-contained stdlib-only verifier in ≤ 50 lines. Reads records from a
`meshflow export` JSON file (produced by `meshflow audit export --run-id <id>`).

```python
#!/usr/bin/env python3
"""meshflow_verify_chain.py — standalone audit chain verifier.

Usage:
    python meshflow_verify_chain.py run_export.json

Input: JSON array of step records as produced by:
    meshflow audit export --run-id <run_id> --format json

Exit code 0 = chain valid. Exit code 1 = chain broken.
"""
import hashlib, json, sys

def compute_entry_hash(r: dict) -> str:
    fields = {
        "run_id":         r["run_id"],
        "step_id":        r["step_id"],
        "node_id":        r["node_id"],
        "input_task":     str(r.get("input_task", ""))[:200],
        "output_content": str(r.get("output_content", ""))[:200],
        "verdict":        r.get("verdict", ""),
        "blocked":        bool(r.get("blocked", False)),
        "timestamp":      r.get("timestamp", ""),
        "prev_hash":      r.get("prev_hash", ""),
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

def verify(path: str) -> bool:
    records = json.loads(open(path).read())
    prev_hash = ""
    errors = []
    for i, r in enumerate(records):
        step_id = r.get("step_id", f"[{i}]")
        stored_prev = r.get("prev_hash", "")
        if stored_prev != prev_hash:
            errors.append(
                f"step {i} ({step_id}): prev_hash mismatch "
                f"(expected {prev_hash[:12]}... got {stored_prev[:12]}...)"
            )
        expected = compute_entry_hash(r)
        stored = r.get("entry_hash", "")
        if stored != expected:
            errors.append(f"step {i} ({step_id}): entry_hash mismatch — record was modified")
        prev_hash = stored if stored else expected
    if errors:
        print(f"CHAIN INVALID — {len(errors)} error(s):")
        for e in errors:
            print(f"  {e}")
        return False
    print(f"CHAIN VALID — {len(records)} steps verified.")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: meshflow_verify_chain.py <export.json>")
        sys.exit(2)
    sys.exit(0 if verify(sys.argv[1]) else 1)
```

---

## 6. Fork Semantics

`ReplayLedger.fork(run_id, from_step, new_run_id=None)` creates a new run by
copying steps `0 … from_step-1` from `run_id`. The forked run:

- Receives a new `run_id` (caller-supplied or auto-generated UUID).
- Each copied record receives a **new `step_id`** bound to `new_run_id`.
- The hash chain is **not** carried over — the new run's chain starts fresh
  from step 0, with `prev_hash=""` for the first copied record.

This means `fork()` branches produce an independent verifiable chain from the
branch point. To trace provenance, the caller should record
`metadata.forked_from_run_id` and `metadata.forked_from_step` in the first
post-fork step.

---

## 7. Export Format

`meshflow audit export --run-id <id> --format json` produces a JSON array:

```json
[
  {
    "run_id":         "abc-123",
    "step_id":        "abc-123-step-0",
    "node_id":        "researcher",
    "node_kind":      "native",
    "input_task":     "Summarise Q2 earnings",
    "output_content": "Revenue was $4.2B...",
    "verdict":        "commit",
    "blocked":        false,
    "block_reason":   "",
    "uncertainty":    0.12,
    "cost_usd":       0.0043,
    "tokens_used":    812,
    "carbon_gco2":    0.00021,
    "duration_ms":    1340.2,
    "timestamp":      "2026-06-02T14:31:00.000Z",
    "prev_hash":      "",
    "entry_hash":     "e3b0c44298fc1c149afb...",
    "metadata":       {}
  }
]
```

All fields not involved in the hash computation (`node_kind`, `block_reason`,
`uncertainty`, `cost_usd`, `tokens_used`, `carbon_gco2`, `duration_ms`) are
informational and not part of tamper-evidence.

---

## 8. Compatibility

| MeshFlow version | Chain spec version | Notes |
|---|---|---|
| v1.0 – v1.5 | pre-spec | `prev_hash`/`entry_hash` added via migration `0001`/`0002` |
| v1.6+ | 1.0 | this document |

Older records that predate migration `0001` will have `prev_hash=""` and
`entry_hash=""`. A verifier should treat consecutive empty-hash records as
unverifiable, not as tampered, and report the count separately.
