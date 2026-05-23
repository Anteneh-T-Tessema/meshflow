# MeshFlow SOC 2 Controls Mapping

This document maps MeshFlow's built-in controls to the SOC 2 Trust Services
Criteria (TSC) under the AICPA's 2017 Trust Services Criteria framework.

---

## CC1 — Control Environment

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC1.1 — COSO Principle 1: Integrity and ethical values | DID-based agent identity; collusion detection | `meshflow/security/identity.py`, `meshflow/intelligence/collusion.py` |
| CC1.2 — Board oversight | Policy mode enforcement; HITL gates | `meshflow/core/policy.py` |
| CC1.3 — Organisational structure | Agent role assignment; team patterns | `meshflow/agents/team.py` |
| CC1.4 — Competence commitment | Schema-validated tool calls; type enforcement | `meshflow/agents/base.py` |
| CC1.5 — Accountability | Immutable SHA-256 audit chain; chain validation | `meshflow/core/ledger.py` |

---

## CC2 — Communication and Information

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC2.1 — Information relevant to financial reporting | Tamper-evident ledger; all steps recorded | `meshflow/core/ledger.py` |
| CC2.2 — Internal communication | `/traces/{run_id}` audit endpoint; CLI trace command | `meshflow/runtime/server.py`, `meshflow/cli/main.py` |
| CC2.3 — External communication | Webhook HITL notifications with HMAC-SHA256 | `meshflow/core/hitl.py` |

---

## CC3 — Risk Assessment

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC3.1 — Risk identification | Uncertainty scoring; risk tier classification | `meshflow/core/schemas.py` (`RiskTier`) |
| CC3.2 — Risk analysis | DascGate policy enforcement; budget/token caps | `meshflow/security/dasc_gate.py` |
| CC3.3 — Risk mitigation | Guardian safety layer; collusion detection | `meshflow/security/guardian.py`, `meshflow/intelligence/collusion.py` |
| CC3.4 — Change management | Schema migration registry with version tracking | `meshflow/core/ledger.py` (`_MIGRATIONS`) |

---

## CC4 — Monitoring Activities

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC4.1 — Monitoring controls | Prometheus metrics endpoint `/metrics` | `meshflow/observability/metrics.py` |
| CC4.2 — Evaluating deficiencies | Collusion alerts; uncertainty threshold alerts | `meshflow/intelligence/collusion.py` |

---

## CC5 — Control Activities

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC5.1 — Control selection | Policy presets (dev/standard/regulated/legal-critical/hipaa) | `meshflow/core/schemas.py` |
| CC5.2 — Technology controls | API key authentication; Bearer token validation | `meshflow/runtime/server.py` |
| CC5.3 — Deployment controls | Docker multi-stage build; K8s health probes | `Dockerfile`, `k8s/deployment.yaml` |

---

## CC6 — Logical and Physical Access

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC6.1 — Access registration | `MESHFLOW_API_KEYS` env var; key rotation support | `meshflow/runtime/server.py` |
| CC6.2 — Authentication | Bearer token or X-API-Key header auth | `meshflow/runtime/server.py` (`_check_auth`) |
| CC6.3 — Access removal | `delete_tenant()` removes all tenant data | `meshflow/core/ledger.py` |
| CC6.6 — Logical access boundaries | Multi-tenancy; per-tenant ledger scoping | `meshflow/core/ledger.py` (`tenant_id`) |
| CC6.7 — Data transmission | TLS support (`--tls-cert`, `--tls-key` flags) | `meshflow/runtime/server.py` |
| CC6.8 — Malicious software | PHI scrubbing; input sanitisation; shell blocklist | `meshflow/security/phi_scrubber.py`, `meshflow/tools/builtins.py` |

---

## CC7 — System Operations

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC7.1 — Configuration management | Environment variable based config; policy presets | `meshflow/core/schemas.py` |
| CC7.2 — Vulnerability management | Tool call schema validation; safe eval (no `eval()`) | `meshflow/agents/base.py`, `meshflow/tools/builtins.py` |
| CC7.3 — Incident identification | Collusion detection; uncertainty alerts; HITL gates | `meshflow/intelligence/collusion.py` |
| CC7.4 — Incident response | HITL approve/reject; `anonymize_run()` | `meshflow/core/ledger.py`, `meshflow/core/hitl.py` |
| CC7.5 — Disaster recovery | Pluggable ledger backends (SQLite, PostgreSQL) | `meshflow/core/ledger.py` |

---

## CC8 — Change Management

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC8.1 — Change authorisation | Schema migration registry; versioned migrations | `meshflow/core/ledger.py` (`_MIGRATIONS`) |

---

## CC9 — Risk Mitigation

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| CC9.1 — Risk mitigation activities | Budget caps; max step limits; timeout enforcement | `meshflow/core/policy.py` |
| CC9.2 — Business disruption risk | K8s HPA (2–10 replicas); health probes | `k8s/deployment.yaml` |

---

## Availability (A1)

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| A1.1 — Availability commitments | Health endpoint `/health`; liveness + readiness probes | `meshflow/runtime/server.py`, `k8s/deployment.yaml` |
| A1.2 — Environmental safeguards | Docker restart policy; K8s deployment with PVC | `docker-compose.yml`, `k8s/deployment.yaml` |
| A1.3 — Recovery procedures | Durable ledger (file/postgres); replay on restart | `meshflow/core/ledger.py` |

---

## Confidentiality (C1)

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| C1.1 — Confidential information | PHI scrubber; output compression; tenant isolation | `meshflow/security/phi_scrubber.py` |
| C1.2 — Confidential information disposal | `delete_run()`, `delete_tenant()`, `anonymize_run()` | `meshflow/core/ledger.py` |

---

## Privacy (P Series)

| Criterion | MeshFlow Control | Evidence Location |
|---|---|---|
| P4.1 — Data minimisation | `max_output_chars` policy field | `meshflow/core/schemas.py` |
| P4.2 — Retention | `delete_run()` for erasure requests | `meshflow/core/ledger.py` |
| P6.1 — Data subject requests | GDPR guide; `anonymize_run()` | `docs/compliance/GDPR_GUIDE.md` |
| P8.1 — PHI handling | `scrub_phi` policy flag; `PHIScrubber` | `meshflow/security/phi_scrubber.py` |

---

## Audit Evidence Collection

To support a SOC 2 audit, collect the following artefacts:

1. **Ledger export** — `meshflow trace <run-id> --export` for sampled runs.
2. **Chain validation** — `meshflow trace <run-id>` showing `CHAIN VALID`.
3. **Prometheus metrics snapshot** — GET `/metrics` at audit date.
4. **Policy configuration** — export the `Policy` objects used in production.
5. **HITL decision log** — export HITL approve/reject decisions from the ledger.
6. **Dependency audit** — `pip list --format=freeze` from the production image.
7. **Container image digest** — `docker inspect meshflow:latest --format '{{.Id}}'`.
