# MeshFlow FedRAMP / FISMA / NIST 800-53 Controls Mapping

Maps MeshFlow features to FedRAMP Moderate/High and NIST SP 800-53 Rev 5 controls.
Use this document as evidence during a FedRAMP authorization assessment.

---

## Quick-start

```python
from meshflow import Mesh, compliance_profile

# FedRAMP Moderate
mesh = Mesh(compliance="fedramp")

# FedRAMP High (stricter HITL threshold, lower cost cap)
mesh = Mesh(compliance="fedramp-high")

# With ZT Advanced tier (required for FedRAMP High)
import os
os.environ["MESHFLOW_ZT_TIER"] = "advanced"
os.environ["MESHFLOW_ZT_REGULATION"] = "fedramp-high"
```

Audit your ZT posture:
```bash
meshflow zt-audit --regulation fedramp-high
```

---

## NIST 800-53 Control Family Mapping

### AC — Access Control

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| AC-2 | Account management | `KeyStore` (PBKDF2 hashed keys, role-based: admin/operator/viewer, per-tenant) |
| AC-3 | Access enforcement | `GovernedStepExecutor` deny-by-default; `ContinuousAuthorizationEngine` per-step |
| AC-4 | Information flow enforcement | `TaintGraph` (IFC propagation), `DascGate` blocks cross-taint flows |
| AC-6 | Least privilege | `JITPrivilegeManager` (ZT Advanced); static RBAC (ZT Foundation/Enterprise) |
| AC-17 | Remote access | `OIDCMiddleware` (Okta/Auth0/Azure AD SSO); API key middleware |
| AC-24 | Access control decisions | `ContinuousAuthorizationEngine.authorize()` re-evaluates at each step |

### AU — Audit and Accountability

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| AU-2 | Event logging | `StepRuntime` logs every step: node_id, input, output, verdict, cost, tokens |
| AU-3 | Content of audit records | `StepRecord` captures: timestamp, agent_id, input_task, output_content, block_reason |
| AU-4 | Audit log storage capacity | Configurable backends: SQLite, Postgres, S3 (`ReplayLedger`) |
| AU-9 | Protection of audit info | SHA-256 tamper-evident hash chain (`entry_hash`, `prev_hash` on every record) |
| AU-10 | Non-repudiation | `ReplayLedger.verify_chain()` detects any post-write modification |
| AU-12 | Audit record generation | `AuditLedger` with SHA-256 chain; `SnapshotExporter` generates ZIP audit bundle |

### CA — Assessment, Authorization, Monitoring

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| CA-7 | Continuous monitoring | `SIEMStreamer` streams every step event to Splunk/Datadog; `Guardian` behavioral monitoring |
| CA-8 | Penetration testing | `meshflow red-team --regulation fedramp` (22 adversarial probes, 6 categories) |

### CM — Configuration Management

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| CM-2 | Baseline configuration | `WorkflowDefinition.from_yaml()` — version-controlled YAML configs |
| CM-3 | Configuration change control | Git-tracked policy files; `meshflow validate` pre-deploy check |
| CM-7 | Least functionality | `GovernedToolRegistry` allowlist; `Policy.deterministic_gate=True` |

### IA — Identification and Authentication

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| IA-2 | Identification and authentication | `AgentIdentityProvider` (DID-based cryptographic identity per agent) |
| IA-3 | Device identification | `OIDCMiddleware` with JWKS validation; `KeyStore` PBKDF2 API keys |
| IA-5 | Authenticator management | Short-lived tokens (`token_ttl_seconds=300` in FedRAMP ZT tier) |
| IA-8 | Non-org user identification | `OIDCMiddleware` Okta/Auth0/Azure AD/Google/Keycloak support |

### IR — Incident Response

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| IR-4 | Incident handling | `meshflow red-team` detects vulnerabilities; SIEM streaming enables real-time SOC response |
| IR-6 | Incident reporting | Webhook events on `policy_violation`, `step_blocked`, `anomaly_detected` |

### RA — Risk Assessment

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| RA-3 | Risk assessment | `AutoRiskClassifier` (4 tiers: LOW/MEDIUM/HIGH/CRITICAL, EMA failure rate) |
| RA-5 | Vulnerability scanning | `AIBillOfMaterials` CVE tracking; `meshflow red-team` adversarial probes |

### SC — System and Communications Protection

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| SC-4 | Information in shared resources | Multi-tenant isolation (`TenantStore`, `scoped_db_path()`, per-tenant ledger) |
| SC-7 | Boundary protection | `ComplianceGuard` real-time blocking; `DascGate` governance gate |
| SC-12 | Cryptographic key establishment | `VaultStore` (AES-256 Fernet, PBKDF2 key derivation, per-secret salt) |
| SC-28 | Protection of information at rest | `VaultStore` encrypts secrets; `output_compressed` gzip in ledger |

### SI — System and Information Integrity

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| SI-2 | Flaw remediation | `AIBillOfMaterials` dependency CVE tracking + OpenSSF scores |
| SI-3 | Malicious code protection | `PromptInjectionDetector` (6 categories, 30+ patterns); `SpotlightingGuardrail` |
| SI-4 | System monitoring | `Guardian` z-score behavioral anomaly detection; `SIEMStreamer` real-time forwarding |
| SI-7 | Software, firmware, and information integrity | SHA-256 hash chain on every `StepRecord`; `ReplayLedger.verify_chain()` |
| SI-10 | Information input validation | `GuardrailStack` (PIIBlock, Regex, KeywordBlock, JSONSchema, Toxicity) |
| SI-12 | Information management and retention | `audit_retention_days=2555` (7 years) in FedRAMP profile |

---

## Evidence collection commands

Run these quarterly and include output in your authorization package:

```bash
# 1. Export full audit snapshot (AU-9, AU-12)
meshflow snapshot export --output fedramp_audit_$(date +%Y%m).zip

# 2. Verify tamper-evident chain integrity (AU-9, AU-10)
meshflow dasc verify

# 3. Export audit ledger (AU-2, AU-3)
meshflow audit export --format json --output audit_records.json

# 4. ZT posture score (CA-7, AC-3)
meshflow zt-audit --regulation fedramp --json > zt_posture.json

# 5. Red-team results (CA-8, RA-5)
meshflow red-team --fail-on-risk medium --output redteam_results.json

# 6. SLA breach report (AU-12)
meshflow sla breaches --limit 1000

# 7. Compliance report (CA-7)
meshflow compliance report --framework fedramp
```

---

## Readiness by authorization level

| Control | FedRAMP Low | FedRAMP Moderate | FedRAMP High |
|---------|-------------|-----------------|--------------|
| Audit chain (SHA-256) | ✅ | ✅ | ✅ |
| Crypto agent identity | ✅ | ✅ | ✅ |
| RBAC deny-by-default | ✅ | ✅ | ✅ |
| SIEM streaming | ⬜ | ✅ (env: `MESHFLOW_ZT_TIER=enterprise`) | ✅ (required) |
| JIT privileges | ⬜ | ⬜ | ✅ (required) |
| Continuous auth | ⬜ | ⬜ | ✅ (required) |
| Hardware-bound creds | ⬜ | ⬜ | ✅ (target) |
| Multi-tenant isolation | ✅ | ✅ | ✅ |
| PHI/PII scrubbing | ✅ | ✅ | ✅ |
| Prompt injection detection | ✅ | ✅ | ✅ |
| Supply chain (AI-BOM) | ⬜ | ✅ | ✅ |
| Red-team testing | ⬜ | ✅ | ✅ |
| 7-year audit retention | ✅ | ✅ | ✅ |

**Legend:** ✅ = implemented and active by default with the `fedramp`/`fedramp-high` profile  ⬜ = available but requires explicit configuration
