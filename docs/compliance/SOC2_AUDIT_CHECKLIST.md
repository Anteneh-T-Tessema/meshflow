# MeshFlow SOC 2 Type II — Quarterly Evidence Collection Checklist

**Audit period:** rolling 12-month window (collect at end of each quarter)
**Responsible role:** Security / Compliance Engineer
**Ledger default path:** `meshflow_runs.db` (adjust `--db` flags if your deployment uses a different path)

All commands assume MeshFlow is installed in the active virtualenv (`.venv/bin/meshflow` per CLAUDE.md) and that production environment variables are loaded.

---

## Quarter-end evidence collection procedure

Run the commands below in order.  Save all output files to a single directory named `soc2_evidence_YYYY_QN/` and zip before uploading to the auditor portal.

---

## Section 1 — Compliance Snapshot (TSC CC7.3, PI1.4)

The snapshot ZIP is the single most important artefact — it bundles all active controls metadata into one signed package.

```bash
# Full compliance snapshot — identities, policy rules, SLA contracts,
# vault audit log, canary experiments, feature flags, alert history.
meshflow snapshot export \
  --output soc2_evidence_$(date +%Y_Q%q)/snapshot_$(date +%Y%m%d).zip \
  --description "SOC 2 Type II quarterly evidence — Q$(( ($(date +%-m)-1)/3+1 )) $(date +%Y)" \
  --created-by "compliance-engineer" \
  --flags-db  meshflow_flags.db \
  --policy-db meshflow_policy.db \
  --sla-db    meshflow_sla.db \
  --vault-db  meshflow_vault.db \
  --tenant-db meshflow_tenants.db
```

**Output:** `snapshot_YYYYMMDD.zip` containing:
- `manifest.json` — snapshot UUID, timestamp, record counts per section
- `identities.json` — all registered `AgentIdentity` records
- `policy_rules.json` — active policy-as-code rules
- `sla_contracts.json` — all SLA contracts (p50/p95/p99 thresholds)
- `sla_breaches.json` — all breach events in the period
- `vault_secrets_metadata.json` — secret names and categories (no values)
- `vault_audit.json` — every store/retrieve/rotate/delete event
- `feature_flags.json` — flag definitions and rollout percentages
- `canary_experiments.json` — canary experiment lifecycle events
- `alerts.json` — fired and resolved alerts

---

## Section 2 — Audit Trail Export (TSC CC7.3, PI1.4, CC1.5)

```bash
# Export all step records including SHA-256 hash chain fields (prev_hash, entry_hash)
meshflow audit export \
  --format json \
  --db meshflow_runs.db \
  --out soc2_evidence_$(date +%Y_Q%q)/audit_trail_$(date +%Y%m%d).json

# CSV variant for auditor spreadsheet tools
meshflow audit export \
  --format csv \
  --db meshflow_runs.db \
  --out soc2_evidence_$(date +%Y_Q%q)/audit_trail_$(date +%Y%m%d).csv
```

**Fields in each record:** `run_id`, `node_id`, `status`, `token_count`, `cost_usd`, `started_at`, `completed_at`, `prev_hash`, `entry_hash`, `compliance_framework`, `blocked`, `hitl_decision`.

**Verify the chain before submitting:**
```bash
# Outputs "CHAIN VALID" if all SHA-256 links are intact, or lists broken entries
meshflow dasc verify --db meshflow_dasc.db
```

---

## Section 3 — SLA Breach Report (TSC A1.1)

```bash
# All SLA breaches across all agents — no limit to capture full quarter
meshflow sla breaches \
  --limit 10000 \
  --db meshflow_sla.db \
  > soc2_evidence_$(date +%Y_Q%q)/sla_breaches_$(date +%Y%m%d).txt

# Per-agent p50/p95/p99 latency stats over the past 90 days (7776000 seconds)
meshflow sla stats <agent_name> \
  --window 7776000 \
  --db meshflow_sla.db

# List all active SLA contracts (thresholds agreed with stakeholders)
meshflow sla list --db meshflow_sla.db \
  > soc2_evidence_$(date +%Y_Q%q)/sla_contracts_$(date +%Y%m%d).txt
```

**What auditors look for:**
- Contracts exist for all production agents
- Breach events are recorded with `observed` vs `threshold` values
- Error rates do not systematically exceed `error_rate` thresholds

---

## Section 4 — Distributed Trace Export (TSC CC7.2)

```bash
# Show span tree for a specific trace (use a representative run_id from the period)
meshflow tracing show <trace_id> \
  --db meshflow_traces.db \
  > soc2_evidence_$(date +%Y_Q%q)/trace_sample_$(date +%Y%m%d).txt

# Confirm spans are being recorded (total count proves monitoring is active)
meshflow tracing count --db meshflow_traces.db

# Export a run's full span list as JSON for deeper analysis
meshflow tracing run <run_id> \
  --db meshflow_traces.db
```

**SpanKind values to verify are present in traces:**
- `root` — workflow entry point
- `agent` — per-agent execution
- `guardrail` — DascGate / ComplianceGuard checks
- `llm` — LLM calls (token counts in `attributes`)
- `tool` — tool invocations

---

## Section 5 — Agent Identity and Access Review (TSC CC6.1)

```bash
# Export all registered agent identities (active and revoked)
meshflow identity list \
  --json \
  --db meshflow_identity.db \
  > soc2_evidence_$(date +%Y_Q%q)/identities_$(date +%Y%m%d).json

# Active identities only (should match the count in the snapshot)
meshflow identity list --active-only --db meshflow_identity.db

# API key inventory — verify no unused keys remain active
meshflow keys list \
  --db meshflow_runs.db \
  > soc2_evidence_$(date +%Y_Q%q)/api_keys_$(date +%Y%m%d).txt
```

**Quarterly access review actions:**
- Revoke keys for departed team members: `meshflow keys revoke <key_id>`
- Revoke agent identities no longer in use: `meshflow identity revoke <agent_id>`
- Confirm all keys have a non-empty `last_used_at` or revoke them

---

## Section 6 — Compliance Framework Report (TSC CC9.2)

```bash
# SOX compliance report — segregation-of-duties and audit-log checks
meshflow compliance report \
  --framework sox \
  --format json \
  --db meshflow_runs.db \
  --out soc2_evidence_$(date +%Y_Q%q)/compliance_sox_$(date +%Y%m%d).json

# HIPAA compliance report (if processing health data)
meshflow compliance report \
  --framework hipaa \
  --format json \
  --db meshflow_runs.db \
  --out soc2_evidence_$(date +%Y_Q%q)/compliance_hipaa_$(date +%Y%m%d).json

# PCI compliance report (if processing payment flows)
meshflow compliance report \
  --framework pci \
  --format json \
  --db meshflow_runs.db \
  --out soc2_evidence_$(date +%Y_Q%q)/compliance_pci_$(date +%Y%m%d).json
```

---

## Section 7 — Metrics Snapshot (TSC CC7.2, A1.1)

Capture a Prometheus metrics snapshot from the live server at quarter-end.

```bash
# Live metrics — save raw Prometheus text output
curl -sf http://localhost:8765/metrics \
  > soc2_evidence_$(date +%Y_Q%q)/prometheus_metrics_$(date +%Y%m%d).txt

# Verify the /health and /ready endpoints are responding
curl -sf http://localhost:8765/health && echo "HEALTH OK"
curl -sf http://localhost:8765/ready  && echo "READY OK"
```

**Key metric families to confirm are present in the output:**
```
meshflow_runs_total{status="completed"}     # total successful runs
meshflow_runs_total{status="blocked"}       # security-blocked runs
meshflow_blocks_total{reason="..."}         # block reason breakdown
meshflow_agent_latency_ms{quantile="0.99"}  # worst-case latency
meshflow_cost_usd_total                     # cumulative AI cost
meshflow_hitl_pending                       # pending human approvals
```

---

## Section 8 — Vault Audit Log (TSC CC6.1, C1.1)

```bash
# Full vault audit log — every store/retrieve/rotate/delete event
meshflow vault audit \
  --limit 100000 \
  --db meshflow_vault.db \
  > soc2_evidence_$(date +%Y_Q%q)/vault_audit_$(date +%Y%m%d).txt

# Verify no secrets have been stored without a category
meshflow vault list \
  --db meshflow_vault.db
```

---

## Section 9 — Security Scan Evidence (TSC CC7.3)

```bash
# Run injection scan on a representative sample of agent outputs
# (pipe a sample payload from audit export)
meshflow security scan --json \
  "$(jq -r '.[0].output' audit_trail_*.json | head -c 2000)"

# Secrets scan — confirm no credentials leaked into ledger
meshflow security secrets --json \
  "$(jq -r '.[0].output' audit_trail_*.json | head -c 2000)"
```

---

## Section 10 — Production Readiness Check (TSC CC5.3)

```bash
# Doctor check — validates ledger integrity, schema version, API key config
meshflow doctor \
  --db meshflow_runs.db \
  --json \
  > soc2_evidence_$(date +%Y_Q%q)/doctor_$(date +%Y%m%d).json

# Dependency inventory — freeze for software bill of materials
pip list --format=freeze \
  > soc2_evidence_$(date +%Y_Q%q)/requirements_freeze_$(date +%Y%m%d).txt

# Container image digest — confirm production image hash
docker inspect meshflow:latest --format '{{.Id}}' \
  > soc2_evidence_$(date +%Y_Q%q)/image_digest_$(date +%Y%m%d).txt
```

---

## Evidence Package Checklist

At the end of each quarter, verify the following files exist in `soc2_evidence_YYYY_QN/`:

- [ ] `snapshot_YYYYMMDD.zip` — compliance snapshot bundle
- [ ] `audit_trail_YYYYMMDD.json` — full step records with hash chain
- [ ] `audit_trail_YYYYMMDD.csv` — CSV copy for auditor tooling
- [ ] `sla_breaches_YYYYMMDD.txt` — SLA breach log
- [ ] `sla_contracts_YYYYMMDD.txt` — active SLA contracts
- [ ] `trace_sample_YYYYMMDD.txt` — representative trace with span hierarchy
- [ ] `identities_YYYYMMDD.json` — agent identity registry export
- [ ] `api_keys_YYYYMMDD.txt` — API key inventory (no raw key values)
- [ ] `compliance_sox_YYYYMMDD.json` — SOX compliance report
- [ ] `compliance_hipaa_YYYYMMDD.json` — HIPAA compliance report (if applicable)
- [ ] `prometheus_metrics_YYYYMMDD.txt` — Prometheus metrics snapshot
- [ ] `vault_audit_YYYYMMDD.txt` — vault access audit log
- [ ] `doctor_YYYYMMDD.json` — production readiness check output
- [ ] `requirements_freeze_YYYYMMDD.txt` — Python dependency SBOM
- [ ] `image_digest_YYYYMMDD.txt` — container image hash

**Archive and upload:**
```bash
zip -r soc2_evidence_$(date +%Y_Q%q).zip soc2_evidence_$(date +%Y_Q%q)/
# Upload to auditor-provided secure portal or S3 bucket
meshflow replay <any_run_id> --archive-s3 s3://your-audit-bucket/soc2/
```

---

*This checklist is reviewed each quarter.  Assigned owner: Security Engineer.*
*Last reviewed: June 2026.*
