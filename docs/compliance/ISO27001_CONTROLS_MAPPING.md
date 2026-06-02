# MeshFlow ISO 27001 / CCPA / DORA / EU AI Act Controls Mapping

Maps MeshFlow features to ISO/IEC 27001:2022, CCPA, EU DORA, and EU AI Act requirements.

---

## Quick-start

```python
from meshflow import Mesh

mesh = Mesh(compliance="iso27001")   # ISO/IEC 27001:2022
mesh = Mesh(compliance="ccpa")       # California Consumer Privacy Act
mesh = Mesh(compliance="dora")       # EU Digital Operational Resilience Act
mesh = Mesh(compliance="eu-ai-act")  # EU AI Act (high-risk systems)
mesh = Mesh(compliance="basel-iii")  # Basel III (financial risk)
```

```bash
meshflow zt-audit --regulation iso27001
meshflow zt-audit --regulation dora
```

---

## ISO/IEC 27001:2022 — Annex A Controls

### A.5 — Organisational Controls

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| A.5.9 | Inventory of information assets | `AIBillOfMaterials` — model inventory, tool inventory, dependency CVE tracking |
| A.5.10 | Acceptable use of assets | `Policy` — configurable acceptable use rules; `meshflow serve --policy-file` |
| A.5.14 | Information transfer | `OIDCMiddleware` — authenticated API access; TLS via `meshflow serve --tls-cert` |
| A.5.23 | Information security for cloud | `VaultStore` (AES-256), `AWSSecretsProvider`, `HashiCorpVaultProvider` |
| A.5.36 | Compliance with policies | `ComplianceGuard` real-time enforcement; `compliance_profile("iso27001")` |
| A.5.37 | Documented operating procedures | `WorkflowDefinition.from_yaml()` — YAML-as-code operational procedures |

### A.6 — People Controls

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| A.6.8 | Information security event reporting | `SIEMStreamer` (Splunk/Datadog); webhook events on violations |

### A.7 — Physical Controls

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| A.7.14 | Secure disposal | `ReplayLedger.delete_tenant()` — GDPR/ISO 27001 right-to-erasure |

### A.8 — Technological Controls

| Control | Requirement | MeshFlow Implementation |
|---------|-------------|------------------------|
| A.8.2 | Privileged access rights | `KeyStore` (admin/operator/viewer RBAC), `JITPrivilegeManager` (ZT Advanced) |
| A.8.3 | Information access restriction | `GovernedToolRegistry` allowlist; `ContinuousAuthorizationEngine` per-step |
| A.8.4 | Access to source code | `config_signing=True` in ZT Enterprise; signed YAML configs |
| A.8.7 | Protection against malware | `PromptInjectionDetector`; `SpotlightingGuardrail`; `PIIBlockGuardrail` |
| A.8.9 | Configuration management | `WorkflowDefinition.from_yaml()` — version-controlled configs |
| A.8.11 | Data masking | `SensitiveDataDetector.mask()` — 23 PHI/PII + credential patterns |
| A.8.12 | Data leakage prevention | `output_pii_filter=True` in ZT Enterprise; PII scrubbing before ledger write |
| A.8.15 | Logging | `ReplayLedger` — immutable SHA-256 chain; `AuditLedger` |
| A.8.16 | Monitoring activities | `Guardian` behavioral monitoring; `SIEMStreamer` real-time streaming |
| A.8.17 | Clock synchronisation | UTC timestamps on all `StepRecord` entries |
| A.8.18 | Use of privileged utility programs | `GovernedToolRegistry` — allowlist, audit trail on every invocation |
| A.8.20 | Networks security | Identity-based isolation via `ZeroTrustOrchestrator`; `meshflow serve --tls-cert` |
| A.8.25 | Secure development lifecycle | `meshflow red-team`; CI ZT gate (`.github/actions/zt-audit/`) |
| A.8.26 | Application security requirements | `GuardrailStack` (input validation, length, toxicity, injection) |
| A.8.28 | Secure coding | `CodeInterpreter` sandbox; subprocess isolation with memory cap |
| A.8.32 | Change management | `meshflow blue-green` — staged promotion with health checks |
| A.8.33 | Test information | `SandboxProvider` / `EchoProvider` — offline testing without live data |
| A.8.34 | Protection of information during audit | `ReplayLedger` append-only; HMAC-signed webhook exports |

---

## CCPA (California Consumer Privacy Act)

| Requirement | MeshFlow Implementation |
|-------------|------------------------|
| Right to Know | `ReplayLedger.get_run()` — retrieve all data processed for a user session |
| Right to Delete | `ReplayLedger.delete_run()` / `delete_tenant()` — CCPA Right to Erasure |
| Right to Opt-Out | `compliance_profile("ccpa")` — restricts data sharing, enables PII scrubbing |
| Data Minimisation | `SensitiveDataDetector` — detects and masks PII before processing/storage |
| Security Obligations | SHA-256 hash chain; AES-256 vault; `OIDCMiddleware` authentication |

**Evidence command:**
```bash
meshflow compliance report --framework gdpr   # covers CCPA equivalents
```

---

## EU DORA (Digital Operational Resilience Act)

DORA applies to financial entities' use of ICT services, including AI agents.

| Requirement | Article | MeshFlow Implementation |
|-------------|---------|------------------------|
| ICT risk management | Art. 6 | `DascGate` risk classification; `AutoRiskClassifier` 4-tier |
| ICT-related incident reporting | Art. 19 | `SIEMStreamer` → Splunk/Datadog; webhook on `policy_violation` |
| Digital operational resilience testing | Art. 26 | `meshflow red-team` — 22 adversarial probes |
| ICT third-party risk management | Art. 28 | `AIBillOfMaterials` — supply chain visibility; OpenSSF scores |
| Information sharing | Art. 45 | Structured `StepRecord` JSON — sharable audit evidence |
| Incident classification | Art. 18 | `_severity()` in SIEM events: high/medium/info classification |
| Recovery | Art. 12 | `DurableWorkflowExecutor` — crash recovery; `meshflow blue-green rollback` |
| Audit retention | Art. 25 | `audit_retention_days=1825` (5 years) in DORA profile |

---

## EU AI Act (High-Risk AI Systems)

| Requirement | Article | MeshFlow Implementation |
|-------------|---------|------------------------|
| Risk management system | Art. 9 | `ComplianceGuard` + `DascGate` + `AutoRiskClassifier` |
| Data governance | Art. 10 | `SensitiveDataDetector`; `TaintGraph` data provenance |
| Technical documentation | Art. 11 | `ReplayLedger` full provenance; `SnapshotExporter` ZIP bundle |
| Record-keeping | Art. 12 | SHA-256 chain; `audit_retention_days=3650` (10 years) |
| Transparency | Art. 13 | `StepRecord.input_task` + `output_content` audit trail |
| Human oversight | Art. 14 | `HumanInLoopConfig` + `interrupt()` + HITL webhook notifications |
| Accuracy and robustness | Art. 15 | `UncertaintyEngine`; `ConfidenceGuardrail`; `EvalSuite` regression testing |
| Conformity assessment | Art. 43 | `meshflow zt-audit --regulation eu-ai-act`; compliance snapshot export |

---

## Basel III (Financial Risk Management)

| Pillar | Requirement | MeshFlow Implementation |
|--------|-------------|------------------------|
| Pillar 1: Minimum capital | Model risk | `UncertaintyEngine` uncertainty scoring; `ConfidenceGuardrail` |
| Pillar 2: Supervisory review | Audit trail | `ReplayLedger` SHA-256 chain; `AuditLedger` |
| Pillar 3: Market discipline | Transparency | `StepRecord` full I/O logging; `ReplayLedger.export_run_csv()` |
| Operational risk | ICT controls | `DascGate` + `ComplianceGuard` + `SIEMStreamer` |
| Data retention | 7 years | `audit_retention_days=2555` in Basel III profile |

---

## Evidence collection

```bash
# ISO 27001 A.8.15 — Audit log export
meshflow audit export --format json --output iso27001_logs.json

# CCPA — Tenant data export for Subject Access Request
meshflow snapshot export

# DORA — Incident report
meshflow compliance report --framework dora

# EU AI Act — Technical documentation bundle  
meshflow snapshot export --output eu_ai_act_bundle.zip

# All frameworks — ZT posture assessment
meshflow zt-audit --regulation iso27001 --json > zt_posture.json
```
