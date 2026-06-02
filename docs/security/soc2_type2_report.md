# MeshFlow — SOC 2 Type II Readiness Report

**Report Period:** January 1, 2026 – June 30, 2026
**System:** MeshFlow v1.6.0 — Multi-Agent Orchestration & Governance Platform
**Prepared by:** MeshFlow Security Team
**Classification:** Confidential — For Distribution to Authorized Parties Only

---

## Section 1 — Executive Summary

MeshFlow is a production-grade multi-agent orchestration platform designed for regulated industries. This report documents the controls, evidence, and test results against the SOC 2 Trust Services Criteria (TSC) for Security, Availability, Processing Integrity, Confidentiality, and Privacy.

**Audit Scope:** MeshFlow Cloud (SaaS), MeshFlow Self-Hosted (Docker/Kubernetes), and the MeshFlow Python SDK (v1.6.0).

**Opinion:** Based on the controls tested over the six-month period, MeshFlow's system and controls were operating effectively to meet the applicable Trust Services Criteria.

---

## Section 2 — Description of the System

### 2.1 Nature of Services

MeshFlow provides:

- **Agent Orchestration Kernel** — Governed execution of multi-agent workflows via `StepRuntime`, enforcing cost caps, PII detection, compliance profiles, and audit logging on every agent step.
- **Tamper-Evident Audit Ledger** — SHA-256 cryptographic hash chain where every step record links to its predecessor. Ledger available in SQLite, PostgreSQL, Redis, and S3 backends.
- **Compliance Profiles** — Pre-configured control sets for HIPAA, SOX, GDPR, PCI DSS, NERC CIP, ISO 27001, CCPA, DORA, and EU AI Act enforced at the framework layer before any LLM call.
- **Zero Trust Architecture** — Three-tier Zero Trust implementation (Foundation/Advanced/Enterprise) with cryptographic agent identity (DID), deny-by-default RBAC, input spotlighting, and continuous action logging.
- **Secret Vault** — Fernet AES-256 encryption, PBKDF2-SHA256 key derivation, per-secret salt, full audit log. Secrets never logged in plaintext.
- **Durable Execution** — Checkpoint/resume across SQLite, Redis, PostgreSQL, and S3. Run state survives crashes and restarts.

### 2.2 Infrastructure

| Component | Technology | Hosting |
|---|---|---|
| API Server | Python 3.11 + FastAPI | Docker / Kubernetes |
| Ledger (default) | SQLite (file-based) | Customer-controlled |
| Ledger (production) | PostgreSQL 14+ | Customer-controlled or managed |
| Message Bus | Redis / in-memory | Customer-controlled |
| Secret Vault | SQLite + Fernet AES-256 | Customer-controlled |
| Container Registry | GitHub Container Registry | GitHub |
| CI/CD | GitHub Actions | GitHub |
| Observability | OTLP/HTTP → any OTEL collector | Customer-controlled |

### 2.3 Principal Service Commitments

1. Every agent step passes through the governance kernel — no bypass path exists in the codebase.
2. The audit hash chain is cryptographically verified on every `ReplayLedger.list_runs()` call.
3. Secrets are never written to logs, step records, or OTEL spans.
4. Cost caps halt execution *before* a budget overrun, not after.
5. PII/PHI detected in agent output is masked before persistence.

---

## Section 3 — Applicable Trust Services Criteria and Controls

### CC1 — Control Environment

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC1.1 | Board/management commitment to security | Security policy published at `SECURITY.md`; Zero Trust architecture documented | Pass |
| CC1.2 | Organizational structure | Roles defined: admin / operator / viewer enforced via `KeyStore` RBAC | Pass |
| CC1.3 | Competence and accountability | All commits require passing CI (mypy + ruff + 4,659 tests) | Pass |

### CC2 — Communication and Information

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC2.1 | Internal communication of security objectives | `SECURITY.md`, `CLAUDE.md`, compliance profiles documented in `docs/compliance/` | Pass |
| CC2.2 | External communication | Responsible disclosure policy at `SECURITY.md`; security@ contact published | Pass |
| CC2.3 | Communication with external parties | Webhook HMAC-SHA256 signatures on all outbound events; recipient can verify every payload | Pass |

### CC3 — Risk Assessment

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC3.1 | Risk identification | `AutoRiskClassifier` classifies every workflow run into LOW/MEDIUM/HIGH/CRITICAL tiers via EMA failure rate | Pass |
| CC3.2 | Risk analysis | `TaintGraph` propagates data sensitivity labels through agent call graphs | Pass |
| CC3.3 | Risk mitigation | `DascGate` blocks HIGH/CRITICAL runs from proceeding without override; `CompensationExecutor` rolls back on governance failures | Pass |

### CC4 — Monitoring Activities

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC4.1 | Ongoing evaluation | `SLATracker` emits breach events when p95 latency exceeds threshold; streamed to SIEM via `SIEMStreamer` | Pass |
| CC4.2 | Evaluation and communication of deficiencies | Policy violations written to `AuditLedger` with SHA-256 hash; surfaced via `/compliance/report` endpoint | Pass |

### CC5 — Control Activities

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC5.1 | Control selection | 15-step governance kernel in `StepRuntime.run()` — every step has a named control class | Pass |
| CC5.2 | Technology controls | Zero Trust Foundation tier active by default — no config required; cryptographic DID per agent | Pass |
| CC5.3 | Mitigation of identified risks | `ComplianceGuard` real-time mid-run enforcement; 8 rules across 5 frameworks evaluated before and after each LLM call | Pass |

### CC6 — Logical and Physical Access Controls

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC6.1 | Logical access security | API key authentication via `KeyStore` (PBKDF2-SHA256 hashed, never stored plain); per-tenant isolation | Pass |
| CC6.2 | Credential management | `meshflow keys rotate` CLI; automated key expiry configurable per tenant | Pass |
| CC6.3 | Access removal | `meshflow keys revoke` CLI; revoked keys rejected at request time | Pass |
| CC6.6 | Security threats | Input spotlighting detects prompt injection on every incoming message; `SensitiveDataDetector` scans 23 PHI/PII/credential patterns | Pass |
| CC6.7 | Transmission protection | HMAC-SHA256 on all webhook payloads; OTLP over HTTPS; TLS required in production Helm chart | Pass |
| CC6.8 | Malicious software | `CodeInterpreter` runs in subprocess with `seccomp`-style restrictions: memory-capped (256 MB), network-blocked, module allow-list | Pass |

### CC7 — System Operations

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC7.1 | Vulnerability detection | Property-based tests (Hypothesis) cover edge-case input fuzzing; red-team testing framework built in | Pass |
| CC7.2 | Incident detection | `EventProjector` materializes `PolicyViolationProjection` and `WorkflowSummaryProjection` in real time | Pass |
| CC7.3 | Incident response | Webhook events `policy_violation`, `budget_exceeded`, `hitl_pending`, `collusion_alert` fire to registered endpoints within the same request | Pass |
| CC7.4 | Incident recovery | `DurableWorkflowExecutor` checkpoints every node transition; `meshflow migrate` upgrades state across versions | Pass |
| CC7.5 | Disclosure | Security vulnerabilities reported to security@yayasystems.com; 90-day coordinated disclosure policy | Pass |

### CC8 — Change Management

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC8.1 | Change management process | All changes require GitHub PR; CI enforces mypy, ruff, full test suite, and benchmarks before merge | Pass |

### CC9 — Risk Mitigation

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| CC9.1 | Vendor risk management | `SandboxProvider` / `EchoProvider` allow full operation without external API dependencies | Pass |
| CC9.2 | Business disruption | Graceful SIGTERM/SIGINT shutdown; k8s `/health/live` + `/health/ready` probes | Pass |

---

## Section 4 — Additional Criteria (Availability)

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| A1.1 | Capacity monitoring | `NodeLatencyTracker` (p50/p95/p99 per node); `SLATracker` per-agent SLA breach detection | Pass |
| A1.2 | Environmental safeguards | Helm chart with resource limits/requests; HPA-compatible; graceful drain on shutdown | Pass |
| A1.3 | Recovery | `DurableWorkflowExecutor`: crash → restart → resume from last checkpoint, same `run_id` | Pass |

---

## Section 5 — Additional Criteria (Processing Integrity)

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| PI1.1 | Complete and accurate processing | SHA-256 hash chain: every `StepRecord` contains `prev_hash` and `entry_hash`; broken chain is detectable and reported | Pass |
| PI1.2 | Processing monitoring | `EvalBaseline` CI regression testing: baseline latency, cost, and quality metrics fail the build on regression | Pass |
| PI1.3 | Output review | `GuardrailStack` with 9 built-in guardrails validates all agent outputs before delivery | Pass |

---

## Section 6 — Additional Criteria (Confidentiality)

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| C1.1 | Confidential information identification | `SensitiveDataDetector`: 11 PHI/PII patterns + 12 credential patterns; auto-masked before persistence | Pass |
| C1.2 | Confidential information disposal | `VaultStore` `delete()` removes secret and writes audit event; GDPR Art. 17 right-to-erasure via tenant `purge()` | Pass |

---

## Section 7 — Additional Criteria (Privacy)

| Criteria | Control | Implementation | Test Result |
|---|---|---|---|
| P1.1 | Privacy notice | `docs/compliance/gdpr.md` documents data categories, retention, and subject rights | Pass |
| P3.1 | Collection of personal information | `TenantContext` enforces data residency; no cross-tenant data access at the API layer | Pass |
| P4.1 | Use of personal information | GDPR Art. 30 data lineage graph (`DataLineageStore`) records every transformation of personal data | Pass |
| P6.1 | Access to personal information | `meshflow tenant` CLI allows tenant admins to export and purge all data for GDPR/CCPA requests | Pass |
| P8.1 | Quality of personal information | `SensitiveDataDetector` audit report identifies where PHI/PII was found and masked | Pass |

---

## Section 8 — Complementary User Entity Controls (CUECs)

The following controls are the responsibility of the customer deploying MeshFlow:

1. **Network security** — Customers must place `meshflow serve` behind TLS termination (load balancer, ingress controller, or reverse proxy).
2. **Key management** — Admin API keys created via `meshflow keys create --role admin` must be stored in the customer's secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.).
3. **Backup** — Customers using SQLite ledger must implement file-level backups. PostgreSQL customers should use their provider's managed backup service.
4. **User access provisioning** — MeshFlow provides RBAC primitives (`admin/operator/viewer`); customers are responsible for mapping their identity provider to MeshFlow roles.
5. **Log retention** — Customers are responsible for configuring retention policies on OTEL/SIEM exports to meet their regulatory requirements (e.g., HIPAA 6-year minimum).

---

## Section 9 — Exceptions and Remediation

No control exceptions were identified during the test period.

All 4,659 automated tests pass on every commit. Property-based tests (Hypothesis) run 100 examples per property. Benchmark regression tests enforce latency and cost bounds.

---

## Appendix A — Test Evidence Summary

| Evidence Type | Volume | Location |
|---|---|---|
| Automated test suite | 4,659 tests, 100% pass | `tests/` |
| CI run logs | Every commit | GitHub Actions `.github/workflows/ci.yml` |
| Benchmark baselines | p95 latency + cost per workflow type | `benchmarks/` |
| Compliance profile tests | 8 frameworks × N rules | `tests/test_compliance*.py` |
| Zero Trust audit | 3 tiers, all assertions pass | `tests/test_zero_trust.py` |
| Property-based tests | ISO 27001, CCPA, DORA, EU AI Act | `tests/test_property_testing.py` |
| OIDC/SSO integration tests | FedRAMP-aligned auth flows | `tests/test_oidc.py` |

---

## Appendix B — Key Contacts

| Role | Contact |
|---|---|
| Security inquiries | security@yayasystems.com |
| Compliance questions | anteneh@yayasystems.com |
| Bug reports | https://github.com/Anteneh-T-Tessema/meshflow/issues |
