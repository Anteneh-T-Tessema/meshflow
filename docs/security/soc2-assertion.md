# SOC 2 Checker Module

`meshflow.compliance.soc2` provides a programmatic SOC 2 Type II controls checker. It maps MeshFlow runtime controls to the AICPA Trust Services Criteria and generates a machine-readable report that can be attached to an external SOC 2 audit package.

> **Note:** The class is `SOC2Checker` (not `SOC2Assertion`).

---

## Quick start

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow.compliance.soc2 import SOC2Checker, SOC2Report

checker = SOC2Checker()
report: SOC2Report = checker.run()

print(f"Pass rate: {report.pass_rate:.1%}")
for control in report.controls:
    status = "✅" if control.passed else "❌"
    print(f"  {status}  {control.category}  {control.test}")
```

---

## Trust Services Criteria coverage

| Criteria | ID | MeshFlow control |
|---|---|---|
| Security | CC6.1 | `DascGate` policy enforcement on every step |
| Security | CC6.2 | Tenant isolation — `TenantContext` enforced in `StepRuntime` |
| Security | CC6.3 | Secret vault — `VaultProvider` (AWS/HashiCorp/env) |
| Availability | A1.1 | `SLATracker` p50/p95/p99 latency + breach detection |
| Availability | A1.2 | `ModelHealthTracker` automatic provider fallback |
| Processing Integrity | PI1.1 | SHA-256 tamper-evident audit chain on every `StepRecord` |
| Processing Integrity | PI1.2 | `ReplayLedger` — every run replayable from ledger |
| Confidentiality | C1.1 | `PIIBlocker` guardrail — blocks PII before LLM call |
| Confidentiality | C1.2 | `SensitiveDataDetector` on outputs |
| Privacy | P3.1 | GDPR data-subject deletion via `GDPRComplianceProfile` |

---

## SOC2Report

```python
report.controls               # list[ControlResult] — all 18 controls
report.pass_rate              # float — fraction passing
report.to_json()              # machine-readable JSON export
report.print_summary()        # human-readable terminal output
report.save("soc2.json")      # write JSON to file
```

---

## ControlResult

| Field | Type | Description |
|---|---|---|
| `category` | `str` | TSC category (e.g. `"CC"`, `"A"`, `"PI"`, `"C"`, `"P"`) |
| `control_id` | `str` | Identifier (e.g. `"CC6.1"`) |
| `test` | `str` | What was verified |
| `status` | `str` | `"PASS"`, `"FAIL"`, `"WARN"`, or `"SKIP"` |
| `passed` | `bool` | `True` when `status == "PASS"` |
| `evidence` | `str` | What code or config provides the evidence |

---

## CI integration

Add the SOC 2 checker to your CI pipeline to catch compliance regressions:

```python
# ci_soc2.py
from meshflow.compliance.soc2 import SOC2Checker

report = SOC2Checker().run()
failing = [c for c in report.controls if not c.passed]
if failing:
    for c in failing:
        print(f"FAIL [{c.control_id}]: {c.test}")
    raise SystemExit(1)

print(f"SOC 2 PASSED — {len(report.controls)} controls, {report.pass_rate:.0%} pass rate")
```

```bash
python ci_soc2.py
```

---

## Exports

```python
from meshflow.compliance.soc2 import SOC2Checker, SOC2Report, ControlResult
```

See also: [SOC 2 Type II Report](soc2_type2_report.md) | [SOC 2 Controls Mapping](../compliance/SOC2_CONTROLS_MAPPING.md)
