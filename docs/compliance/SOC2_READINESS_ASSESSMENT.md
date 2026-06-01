# MeshFlow SOC 2 Type II — Readiness Self-Assessment

**Assessment date:** June 2026
**Assessor:** Engineering / Compliance — MeshFlow v1.2
**Purpose:** Identify gaps before the first formal auditor engagement.
**Rating scale:**
- **Implemented** — control exists, is tested, and evidence is collectible today.
- **Partial** — control is partially implemented or evidence collection requires manual steps.
- **Gap** — control is not implemented; remediation required before audit window closes.

---

## Scorecard

| # | Criterion | MeshFlow Control | Rating | Evidence Location | Notes |
|---|---|---|---|---|---|
| 1 | **CC6.1** — Logical access — authentication | `KeyStore` PBKDF2 + `IdentityStore` HMAC-HS256 tokens | **Implemented** | `meshflow keys list`, `meshflow identity list --json` | 100k PBKDF2 rounds; timing-safe `hmac.compare_digest` |
| 2 | **CC6.1** — Access provisioning and de-provisioning | `KeyStore.revoke()`, `IdentityStore.revoke()` — soft-delete with audit trail | **Implemented** | `meshflow keys revoke`, `meshflow identity revoke` | Quarterly access review procedure in `SOC2_AUDIT_CHECKLIST.md` |
| 3 | **CC6.2** — Unique user authentication | API key roles (`admin/operator/viewer`); per-key `last_used_at` tracking | **Implemented** | `meshflow keys list` — includes `role` and `last_used_at` | Static-key fallback via `MESHFLOW_API_KEYS` is operator-level only |
| 4 | **CC6.6** — Encryption in transit | `meshflow serve --tls-cert --tls-key` enables TLS; HMAC-signed webhook payloads | **Partial** | `openssl s_client` verification; `meshflow webhooks add --secret` | TLS flags exist but mutual TLS (mTLS) for inter-service calls is not enforced |
| 5 | **CC7.2** — System monitoring | `MeshFlowTracer` OTEL spans; `MetricsCollector` Prometheus `/metrics`; `NodeLatencyTracker` | **Implemented** | GET `/metrics`; `meshflow tracing show <trace_id>` | 17 labeled metric families including per-agent p50/p95/p99 |
| 6 | **CC7.2** — Log retention | SQLite WAL-mode `trace_spans` table; `ReplayLedger` step records | **Partial** | `meshflow tracing count`; `meshflow audit export` | No automated log rotation or archival policy configured by default; operators must set retention |
| 7 | **CC7.3** — Security event detection | `Guardian` (injection scanner + tool chain DoS + behavioural anomaly); `AutoRiskClassifier`; DascGate | **Implemented** | Guardian `alerts()` in logs; `meshflow dasc ledger` | 13 injection patterns; z-score > 3.0 threshold alerting |
| 8 | **CC7.3** — Tamper-evident audit chain | `StepRecord.entry_hash` + `prev_hash` SHA-256 chain in `ReplayLedger` and `AuditLedger` | **Implemented** | `meshflow dasc verify` — outputs "CHAIN VALID" | Chain spans across all governed steps; migration columns added in schema v1 and v2 |
| 9 | **CC7.4** — Incident response | HITL approve/reject gates; `anonymize_run()`; `delete_run()` | **Partial** | `meshflow resume <run_id>`; `meshflow approve <run_id> <node_id>` | No documented incident response runbook; HITL gates exist but IR playbook is not yet written |
| 10 | **CC9.2** — Vendor risk — agent identity | DID-style UUIDs per agent; capability scoping; capability list in every token | **Implemented** | `meshflow identity list --active-only --json` | Vendor/third-party agents must register before use; capabilities cannot be self-elevated |
| 11 | **CC9.2** — Third-party tool governance | `ToolChainAnalyzer` in `Guardian`; DascGate Tier 1–4 classification for all tool calls | **Implemented** | DascGate ledger; Guardian alert log | `_DANGEROUS_COMBOS` amplification detection catches MCP tool chain abuse |
| 12 | **A1.1** — Performance monitoring — SLA | `SLATracker` p50/p95/p99 per agent; `SLABreach` records; `SLAContract` definitions | **Implemented** | `meshflow sla stats <agent>`; `meshflow sla breaches` | p50 ≤ p95 ≤ p99 constraint enforced at contract creation |
| 13 | **A1.1** — Capacity planning | `WorkflowAnalytics` reads `ReplayLedger`; Prometheus `meshflow_cost_usd_total` and token counters | **Partial** | `meshflow analytics --format json`; GET `/metrics` | Analytics exist but no formal capacity planning process or documented growth thresholds |
| 14 | **PI1.4** — Data integrity — processing | `StepRecord` hash chain; schema-validated `NodeInput`/`NodeOutput` | **Implemented** | `meshflow schema NodeInput`; `meshflow dasc verify` | Hash chain is end-to-end from first step to last; chain breaks are detectable |
| 15 | **PI1.4** — Completeness — audit export | `meshflow audit export` serialises all fields including `prev_hash`/`entry_hash`; CSV + JSON | **Implemented** | `meshflow audit export --format json --out evidence.json` | `SnapshotExporter` bundles all store data into a single ZIP with a signed manifest |
| 16 | **C1.1** — Encryption at rest | `meshflow vault store` AES-derived encryption; PHI scrubber for data in ledger | **Partial** | `meshflow vault list`; `meshflow security secrets` | Vault uses passphrase-derived AES; the main `ReplayLedger` SQLite file is not encrypted at rest by default |
| 17 | **C1.2** — Data disposal | `delete_run()`, `anonymize_run()`, `lineage delete <subject>` (GDPR erasure) | **Implemented** | `meshflow lineage delete <name> --yes` | `anonymize_run()` replaces PII fields in place; `delete_tenant()` purges all tenant data |
| 18 | **CC8.1** — Change management | `_MIGRATIONS` registry in `meshflow/core/ledger.py`; versioned schema migrations applied idempotently on startup | **Partial** | `meshflow doctor --json` reports schema version | No formal change control board process; migrations are code-level only, no approval workflow |
| 19 | **CC5.3** — Deployment security | Docker multi-stage build; K8s HPA (2–10 replicas); health/readiness probes; `meshflow doctor` | **Implemented** | `meshflow doctor --json`; `kubectl get hpa` | `Dockerfile` uses non-root user; K8s `deployment.yaml` includes liveness and readiness probes |
| 20 | **CC2.3** — External communication controls | HMAC-SHA256 webhook signatures; webhook dead-letter queue with retry | **Implemented** | `meshflow webhooks queue`; `meshflow webhooks dead` | Signature format: `X-MeshFlow-Signature: sha256=<hmac>`; secrets stored separately from delivery URLs |

---

## Summary

| Rating | Count | Percentage |
|---|---|---|
| Implemented | 13 | 65% |
| Partial | 6 | 30% |
| Gap | 0 | 0% |
| **Total** | **20** | |

No outright gaps were identified.  The six Partial items require focused remediation before the formal audit window.

---

## Remediation Plan for Partial Items

### Item 4 — Mutual TLS for inter-service calls

- **Target:** Implemented
- **Owner:** Infrastructure
- **Action:** Configure mTLS between MeshFlow server instances and any sidecar proxies. Generate per-service certificates via cert-manager or Vault PKI. Document in deployment guide.
- **Due:** Q3 2026

### Item 6 — Log retention policy

- **Target:** Implemented
- **Owner:** Compliance Engineer
- **Action:** Define and document retention windows (recommended: 12 months for `trace_spans`, 24 months for `step_records`). Add a `meshflow schedule add --cron "0 2 * * 0" --agent log-rotation-agent` scheduled task to archive and purge. Configure S3 archive via `meshflow replay <run_id> --archive-s3`.
- **Due:** Q3 2026

### Item 9 — Incident response runbook

- **Target:** Implemented
- **Owner:** Security Engineer
- **Action:** Write `docs/compliance/INCIDENT_RESPONSE_PLAYBOOK.md` covering: detection (Guardian alert → PagerDuty), containment (`meshflow identity revoke` + `meshflow keys revoke`), eradication (`meshflow dasc verify` + ledger inspection), recovery (HITL approve/reject), and post-incident review. Map to NIST SP 800-61.
- **Due:** Q3 2026

### Item 13 — Capacity planning process

- **Target:** Implemented
- **Owner:** Engineering Lead
- **Action:** Define documented growth thresholds in `meshflow.policy.yaml` using `max_steps`, `max_tokens_per_step`, and `budget_usd`. Add Grafana dashboard panels for `meshflow_cost_usd_total` rate-of-change. Establish monthly capacity review meeting cadence.
- **Due:** Q3 2026

### Item 16 — Encryption at rest for main ledger

- **Target:** Implemented
- **Owner:** Infrastructure
- **Action:** Enable SQLCipher or filesystem-level encryption (LUKS on Linux, FileVault on macOS) for the host volume containing `meshflow_runs.db`. Document the key management procedure. Alternatively, migrate the ledger backend to PostgreSQL with Transparent Data Encryption (TDE).
- **Due:** Q4 2026

### Item 18 — Change control process

- **Target:** Implemented
- **Owner:** Engineering Manager
- **Action:** Formalise the PR review process: require two approvals for any change to `meshflow/core/ledger.py`, `meshflow/security/`, or `meshflow/compliance/`. Document in `CONTRIBUTING.md`. Add a pre-commit hook that requires a JIRA/GitHub issue reference for changes to those paths.
- **Due:** Q3 2026

---

## Next Assessment Date

Q3 2026 (target: before first formal auditor kick-off meeting).

*Assessor signature:* Engineering / Compliance Team — MeshFlow v1.2 — June 2026
