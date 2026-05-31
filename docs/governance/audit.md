# Audit Trail

`ReplayLedger` is the tamper-evident, append-only record of every governed step. It is the primary compliance artifact for HIPAA audit requests, SOX internal controls, and GDPR processing records.

```python
from meshflow.core.ledger import ReplayLedger

ledger = ReplayLedger("meshflow_runs.db")

steps   = await ledger.get_run("run-abc-123")
summary = await ledger.run_summary("run-abc-123")
run_ids = await ledger.list_runs()
```

PostgreSQL is selected automatically when the path is a DSN:

```python
ledger = ReplayLedger("postgresql://user:pass@host/db", tenant_id="acme")
```

## `write` / `get_run` / `list_runs`

```python
await ledger.write(step_record)                       # append one step
steps   = await ledger.get_run("run-abc-123")         # list[dict] ordered by id
run_ids = await ledger.list_runs()                    # most recent first
summary = await ledger.run_summary("run-abc-123")
# {run_id, steps, nodes, total_cost_usd, total_tokens,
#  total_carbon_gco2, blocked_steps, verdicts, timestamps}
```

## `verify_chain()` — Tamper-Evident Hash Chain

Every step record stores a SHA-256 `entry_hash` computed from its canonical fields, and a `prev_hash` linking it to the previous record. `verify_chain()` recomputes both and flags any mismatch.

```python
result = await ledger.verify_chain("run-abc-123")
# {
#   "run_id": "run-abc-123",
#   "valid": True,
#   "steps_verified": 12,
#   "errors": []          # non-empty if any record was modified after write
# }
```

A non-empty `errors` list indicates tampering or data corruption. Each error identifies the step index, step ID, and which hash mismatched.

## `export_run()` and `export_run_csv()`

```python
json_str = await ledger.export_run("run-abc-123")
# Full run as JSON — suitable for S3 archival

csv_str = await ledger.export_run_csv("run-abc-123")
# CSV with columns: run_id, step_id, node_id, node_kind, verdict,
# blocked, block_reason, uncertainty, cost_usd, tokens_used,
# carbon_gco2, duration_ms, timestamp, entry_hash, prev_hash
```

Archive to S3 in write-once mode (returns `LedgerArchiveResult` with `uri`, `sha256`, `bytes_written`):

```python
result = await ledger.archive_run("run-abc-123", "s3://my-bucket/audit/")
```

## `diff(run_id_a, run_id_b)` — Run Comparison

```python
diff = await ledger.diff("run-abc-123", "run-def-456")
# RunDiff fields:
#   only_in_a: list[str]          — node_ids only in run A
#   only_in_b: list[str]          — node_ids only in run B
#   common: list[str]             — node_ids in both
#   changed: list[dict]           — common nodes with differing output/verdict
#   cost_delta_usd: float         — run_b cost minus run_a cost
#   token_delta: int              — run_b tokens minus run_a tokens
```

## `fork(run_id, from_step)` — Time-Travel Branching

Creates a new run by copying steps `0 … from_step-1` from an existing run, then returns the new run ID. Resume execution from that checkpoint with a different model or prompt.

```python
new_run_id = await ledger.fork("run-abc-123", from_step=4)
# new_run_id is a fresh UUID; contains steps 0-3 from the original
```

`from_step=0` creates an empty run. `from_step=-1` copies all steps.

## `load_state(run_id, step_index)` — Single-Step Inspection

```python
step = await ledger.load_state("run-abc-123", step_index=3)
# Returns the full step record dict at index 3, or None if out of range
```

## GDPR Right-to-Erasure

```python
rows_deleted = await ledger.delete_run("run-abc-123")
rows_redacted = await ledger.anonymize_run("run-abc-123")
# anonymize_run replaces input_task and output_content with [REDACTED]
# while preserving hash chain structure and metadata for audit purposes
```

## `meshflow replay` CLI

```bash
# Inspect a run step by step
meshflow replay run-abc-123 --db meshflow_runs.db

# Output as JSON
meshflow replay run-abc-123 --json

# Diff two runs
meshflow replay run-abc-123 --diff run-def-456

# Fork from step 4 and re-run with a different model
meshflow replay run-abc-123 --fork-at 4 --model claude-opus-4

# Rewind to step 2 and replay
meshflow replay run-abc-123 --rewind 2

# Branch comparison across models
meshflow replay run-abc-123 --branch-compare \
  --forks "gpt4:model=gpt-4o,claude:model=claude-sonnet-4-6"
```

## `meshflow audit` CLI

```bash
# Export audit trail as JSON
meshflow audit export --run-id run-abc-123 --format json

# Export all runs as CSV for compliance filing
meshflow audit export --format csv --out audit_2026Q1.csv

# Verify hash chain integrity
meshflow audit export --run-id run-abc-123 --format json | python -m meshflow.core.ledger verify
```
