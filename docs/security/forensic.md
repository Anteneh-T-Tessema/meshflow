# meshflow-forensic

`meshflow-forensic` is a standalone pip package that provides deep audit, taint analysis, and EU AI Act compliance reporting for MeshFlow runs. It can be installed independently of the main MeshFlow package — useful for audit infrastructure that must not depend on the full agent runtime.

---

## Install

```bash
pip install meshflow-forensic
```

> **Note:** The package is in `packages/meshflow-forensic/` in the repo. Until published to PyPI, install directly:
> ```bash
> pip install ./packages/meshflow-forensic
> ```
> The standalone package cannot currently be pip-installed from the repo because `packages/meshflow-forensic/README.md` is missing — it is referenced in `pyproject.toml` but not committed. Create an empty `README.md` in that directory to unblock the install.

---

## Quick start

```python
import asyncio
from meshflow_forensic import DascGate, Intent, RiskTier, ForensicReport

async def run():
    gate = DascGate.create(run_id="my_run")

    # Evaluate agent intents through the gate
    v1 = await gate.evaluate(Intent(action="read_db",   agent_id="analyst",  risk_tier=RiskTier.READ_ONLY))
    v2 = await gate.evaluate(Intent(action="write_file", agent_id="writer",  risk_tier=RiskTier.EXTERNAL_IO))
    print(f"Verdicts: {v1!r}, {v2!r}")           # 'COMMIT', 'COMMIT'

    # Verify hash chain
    print(f"Chain valid: {gate.verify_ledger()}")  # True

    # Generate forensic report
    report = ForensicReport.from_gate(gate)
    print(f"Total steps: {report.total_entries}")
    print(report.to_json())                        # machine-readable export

asyncio.run(run())
```

---

## DascGate

`DascGate` is the deterministic policy kernel. `evaluate()` is **async**:

```python
import asyncio
from meshflow_forensic import DascGate, Intent, RiskTier, ActionVerdict

async def run():
    gate = DascGate.create(run_id="my_run")
    v = await gate.evaluate(Intent(
        action="generate_report",
        agent_id="analyst",
        risk_tier=RiskTier.INTERNAL,
    ))
    print(v)   # 'COMMIT', 'ESCALATE', or 'REJECT'
    print(gate.ledger_count())    # 1
    print(gate.verify_ledger())   # True

asyncio.run(run())
```

`ActionVerdict` is a `str` subclass:

| Value | Meaning |
|---|---|
| `'COMMIT'` | Step is safe; written to tamper-evident ledger |
| `'ESCALATE'` | Irreversible tier detected; requires HITL approval |
| `'REJECT'` | Tainted input or policy violation; blocked and logged |

---

## Taint graph

`meshflow-forensic` maintains a taint propagation graph — if a step's input contains tainted data (e.g. user-supplied content that was never sanitised), downstream steps are automatically marked as tainted:

```python
from meshflow_forensic import TaintGraph

graph = TaintGraph()
graph.add_step("step_1", tainted=True)
graph.add_step("step_2", depends_on="step_1")

print(graph.is_tainted("step_2"))   # True — propagated from step_1
```

---

## EU AI Act compliance check

`EUAIActChecker` takes the `DascGate` instance (not the report):

```python
import asyncio
from meshflow_forensic import DascGate, Intent, RiskTier, EUAIActChecker, HighRiskCategory

async def run():
    gate = DascGate.create(run_id="r1")
    await gate.evaluate(Intent(action="classify", agent_id="hr_bot", risk_tier=RiskTier.INTERNAL))

    checker = EUAIActChecker(gate)
    result = checker.check(HighRiskCategory.EMPLOYMENT)
    print(f"Overall: {result.overall}")       # 'COMPLIANT', 'NON_COMPLIANT', 'PARTIAL'
    print(f"Pass rate: {result.pass_rate:.0%}")
    print(f"Gaps: {result.gaps}")

    all_results = checker.check_all()         # dict[str, EUAIActResult] — 8 categories

asyncio.run(run())
```

Checked categories: `EMPLOYMENT`, `CREDIT`, `EDUCATION`, `BIOMETRIC`, `INFRASTRUCTURE`, `LAW_ENFORCEMENT`, `MIGRATION`, `JUSTICE`.

---

## ForensicReport fields

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `total_entries` | `int` | Total ledger entries |
| `chain_valid` | `bool` | SHA-256 hash chain integrity |
| `verdict_counts` | `dict[str, int]` | e.g. `{"COMMIT": 5, "REJECT": 1}` |
| `tainted_agents` | `list[str]` | Agent IDs that processed tainted input |
| `timeline` | `IncidentTimeline` | Ordered sequence of `IncidentEvent` objects |
| `to_json()` | `str` | Machine-readable JSON export |

---

## Exports

```python
from meshflow_forensic import (
    DascGate, AutoRiskClassifier, TaintGraph, CompensationExecutor, AuditLedger,
    Intent, ActionVerdict, LedgerEntry, RiskTier, ForensicPolicy,
    ForensicReport, IncidentTimeline,
    EUAIActChecker, EUAIActResult, HighRiskCategory,
)
```
