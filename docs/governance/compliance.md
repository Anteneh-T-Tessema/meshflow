# Compliance Profiles

MeshFlow compliance profiles are named presets that auto-configure HITL thresholds, PHI scrubbing, audit retention, cost caps, and verifier domains for a specific regulatory regime.

```python
from meshflow.core.compliance import compliance_profile

profile = compliance_profile("hipaa")
print(profile.hitl_threshold)        # 0.70
print(profile.audit_retention_days)  # 2555  (7 years)
print(profile.phi_scrubbing)         # True
print(profile.require_evidence)      # True
```

## Attaching a Profile to a Mesh

```python
from meshflow import Mesh

mesh = Mesh(compliance="hipaa")   # shorthand — one line
```

Or convert to a full `Policy` object:

```python
policy = compliance_profile("hipaa").to_policy()
```

## Built-in Profiles

| Profile | HITL Threshold | Audit Retention | PHI Scrubbing | Evidence Required |
|---|---|---|---|---|
| `hipaa` | 0.70 | 7 years (2555 days) | Yes | Yes |
| `sox` | 0.75 | 7 years (2555 days) | No | Yes |
| `gdpr` | 0.72 | 3 years (1095 days) | Yes | Yes |
| `pci` / `pci-dss` | 0.80 | 1 year (365 days) | Yes | Yes |
| `nerc` | 0.85 | 3 years (1095 days) | No | Yes |
| `standard` | 0.90 | 90 days | No | No |
| `research` | 0.95 | 1 year (365 days) | No | No |

### What Each Profile Enforces

**HIPAA** — activates verifier domains `hipaa`, `phi_scrubbing`, `aml`; enables PHI scrubbing on all logs; enforces 7-year audit retention; `policy_mode=LEGAL_CRITICAL`.

**SOX** — activates `sox`, `erp_audit`, `aml` verifiers; enforces 7-year retention; every action requires attached Evidence objects; `policy_mode=LEGAL_CRITICAL`.

**GDPR** — activates `gdpr`, `phi_scrubbing`; enables PHI scrubbing; 3-year retention; supports right-to-erasure via `ReplayLedger.delete_run()` and `anonymize_run()`.

**PCI-DSS** — activates `pci_dss`, `aml`; strictest HITL threshold (0.80); enables PHI scrubbing; 1-year retention per PCI-DSS Requirement 10.

**NERC CIP** — activates `nerc_cip`; tightest human-approval threshold (0.85); 3-year retention for Critical Infrastructure Protection.

## `ComplianceProfile` Fields

```python
@dataclass
class ComplianceProfile:
    name: str                          # Display name, e.g. "HIPAA"
    hitl_threshold: float              # Confidence below which humans must approve
    verifier_domains: list[str]        # SwarmTRM verifier domains to activate
    audit_retention_days: int          # Minimum log retention in days
    phi_scrubbing: bool                # Auto-scrub PHI/PII from logs
    max_cost_usd_per_run: float        # Hard per-run cost ceiling
    max_tokens_per_step: int           # Token cap per LLM step
    require_evidence: bool             # Actions must carry Evidence objects
    policy_mode: PolicyMode            # Base policy mode
    extra_policy: dict[str, Any]       # Additional Policy fields to merge
```

## `list_profiles()` — Available Profile Names

```python
from meshflow.core.compliance import list_profiles

list_profiles()
# ['GDPR', 'HIPAA', 'NERC CIP', 'PCI-DSS', 'Research', 'SOX', 'Standard']
```

Returns deduplicated display names. Use the lowercase key (`"hipaa"`, `"pci-dss"`, `"nerc"`) when calling `compliance_profile()`.

## Custom Profile

Register a custom profile by adding to `PROFILES` before startup:

```python
from meshflow.core.compliance import PROFILES, ComplianceProfile
from meshflow.core.schemas import PolicyMode

PROFILES["finra"] = ComplianceProfile(
    name="FINRA",
    hitl_threshold=0.78,
    verifier_domains=["finra", "aml"],
    audit_retention_days=1825,   # 5 years
    phi_scrubbing=False,
    max_cost_usd_per_run=2.0,
    max_tokens_per_step=4096,
    require_evidence=True,
    policy_mode=PolicyMode.LEGAL_CRITICAL,
)

profile = compliance_profile("finra")
```
