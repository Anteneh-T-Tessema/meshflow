# Production Deployment Case Studies

Three reference architectures for production MeshFlow deployments, each targeting a different regulated-industry scenario. All examples run offline with `MESHFLOW_MOCK=1`.

---

## Case Study 1: Healthcare — HIPAA-Compliant Clinical Documentation Pipeline

**Scenario:** A regional health system routes incoming clinical notes through a multi-agent pipeline that extracts structured data, flags PHI, and generates ICD-10 billing codes — all subject to full HIPAA audit logging and a hard cost cap.

### Architecture

```
                  ┌────────────────────────────────────────────────┐
Clinical Note ───►│  PIIBlockerGuardrail  →  Researcher Agent      │
                  │  (strips PHI before LLM call)    │             │
                  │                                   ▼             │
                  │                            Extractor Agent      │
                  │                            (ICD-10 codes)       │
                  │                                   │             │
                  │                                   ▼             │
                  │                            Writer Agent         │
                  │                            (billing summary)    │
                  └──────────────── HIPAA Profile + Audit Ledger ──┘
```

### Code

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import Workflow, Agent, CostCap
from meshflow.compliance import compliance_profile

wf = Workflow(
    compliance_profile=compliance_profile("hipaa"),
    cost_cap=CostCap(usd=0.10),   # hard cap per note — enforced in StepRuntime
)

wf.add(Agent("extractor", role="researcher",
             model="claude-haiku-4-5-20251001"))
wf.add(Agent("coder",     role="executor",
             model="claude-sonnet-4-6"))
wf.add(Agent("summariser", role="executor",
             model="claude-haiku-4-5-20251001"))

result = wf.run(
    "Patient: Jane D. DOB 1972-03-15. "
    "Presenting with acute chest pain. BP 145/92. "
    "Extract ICD-10 codes and generate billing summary."
)
print(result.output)
print(f"Audit entries: {result.audit_entries}")
print(f"PHI blocked:   {result.pii_events}")
```

### Key configuration

| Setting | Value | Reason |
|---|---|---|
| `compliance_profile("hipaa")` | HIPAA | Enables PHI detection, 6-year audit retention |
| `CostCap(usd=0.10)` | $0.10/note | Budget predictability for high-volume processing |
| `PIIBlockerGuardrail` | auto-enabled by HIPAA profile | Strips PHI before any LLM call |
| Audit ledger | SQLite (dev) → Postgres (prod) | Tamper-evident SHA-256 chain |

### Production deployment (Kubernetes)

```yaml
# values.yaml (excerpt)
meshflow:
  complianceProfile: hipaa
  costCapUsd: "0.10"
  auditBackend: postgres
  postgresUrl: "${MESHFLOW_DB_URL}"
  replicaCount: 3
  resources:
    requests: { cpu: "500m", memory: "512Mi" }
    limits:   { cpu: "2",    memory: "2Gi" }
```

```bash
helm install meshflow-hipaa ./charts/meshflow -f values.yaml
```

---

## Case Study 2: Financial Services — SOX-Compliant Multi-Agent Audit Reporter

**Scenario:** A mid-size bank runs a nightly agent pipeline that scans transaction logs, detects anomalies, and generates SOX-compliant audit reports — with a human-in-the-loop checkpoint before any report is finalised.

### Architecture

```
Transaction Logs ─► Analyst Agent ─► Anomaly Detector ─► HITL Checkpoint
                                                                │
                                                    Approved?  │  Rejected?
                                                         ▼          ▼
                                                  Report Writer    Escalation
                                                  (SOX summary)    (Slack alert)
```

### Code

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import Workflow, Agent, CostCap, interrupt, Command
from meshflow.compliance import compliance_profile
from meshflow.workers import durable_task, CronTrigger, WorkerDaemon

@durable_task(max_retries=2)
async def nightly_audit(date: str) -> str:
    wf = Workflow(
        compliance_profile=compliance_profile("sox"),
        cost_cap=CostCap(usd=2.00),
    )
    wf.add(Agent("analyst",   role="researcher", model="claude-sonnet-4-6"))
    wf.add(Agent("detector",  role="critic",     model="claude-sonnet-4-6"))
    wf.add(Agent("reporter",  role="executor",   model="claude-sonnet-4-6"))

    result = await wf.run_async(
        f"Analyse transaction logs for {date}. "
        "Identify anomalies. Draft SOX Section 302 certification narrative."
    )

    # HITL checkpoint — pause for human review before finalising
    approval = interrupt(
        message=f"Review draft audit report for {date}",
        payload=result.output,
    )
    if approval.approved:
        return result.output
    else:
        raise ValueError(f"Report rejected: {approval.notes}")

daemon = WorkerDaemon()
daemon.register(nightly_audit)
CronTrigger(cron="0 2 * * *").add(nightly_audit, date="$(date +%Y-%m-%d)")
daemon.start()
```

### Key configuration

| Setting | Value | Reason |
|---|---|---|
| `compliance_profile("sox")` | SOX | Enables Section 302/404 controls, CFO sign-off hooks |
| `@durable_task(max_retries=2)` | SQLite job store | Survives restarts — nightly job must complete |
| `interrupt()` | HITL checkpoint | SOX requires human sign-off before report is filed |
| `CronTrigger("0 2 * * *")` | 2 AM nightly | Off-hours run avoids peak load |

### Audit trail

Every step produces a `StepRecord` in the `ReplayLedger`. The full run is replayable:

```bash
meshflow replay --run-id <run_id> --output replay.json
meshflow-forensic audit meshflow_runs.db --html sox_audit.html
```

---

## Case Study 3: Legal Technology — GDPR Data-Subject Request Processor

**Scenario:** A legal-tech SaaS automates GDPR Article 17 ("right to erasure") requests — an AdvisorAgent reviews each deletion plan before execution, and the DynamicWorkflow adapts based on which data stores are affected.

### Architecture

```
DSR Request ─► Classifier Agent ─► DynamicWorkflow (spawns per-store agents)
                                          │
                    ┌─────────────────────┼──────────────────────┐
                    ▼                     ▼                      ▼
             DB Deletion Agent   S3 Purge Agent         Email Archive Agent
                    │                     │                      │
                    └─────────────────────┴──────────────────────┘
                                          │
                                  AdvisorAgent (review plan)
                                          │
                                  Confirmation + Audit Report
```

### Code

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import AdvisorAgent, AdvisorConfig, AdvisorGuidance
from meshflow.compliance import compliance_profile
from meshflow.core.dynamic_workflow import DynamicWorkflow

# Advisor ensures every deletion plan is reviewed before execution
advisor_cfg = AdvisorConfig(
    guidance=AdvisorGuidance(
        content="Verify the deletion scope covers all six GDPR-required data categories.",
        checklist=[
            "Personal identifiers covered?",
            "Backup stores included?",
            "Downstream systems notified?",
            "30-day compliance window respected?",
        ],
    ),
    use_threshold=0.0,   # always invoke advisor for deletion requests
)

agent = AdvisorAgent(
    name="dsr_processor",
    role="executor",
    advisor_config=advisor_cfg,
)

# DynamicWorkflow spawns a specialist per data store identified by the classifier
wf = DynamicWorkflow(
    planner_model="claude-sonnet-4-6",
    max_agents=8,
    cost_cap=None,   # no cap — compliance > cost for DSR
)
wf.compliance_profile = compliance_profile("gdpr")

result = wf.run(
    "Process GDPR Article 17 erasure request for user_id=42. "
    "Data stores: PostgreSQL (users, orders), S3 (documents), SendGrid (email history)."
)
print(result.output)
print(f"Agents spawned: {result.agents_spawned}")
print(f"Advisor review: {result.step_results[0].advisor_used}")
```

### Key configuration

| Setting | Value | Reason |
|---|---|---|
| `compliance_profile("gdpr")` | GDPR | Enables Art. 17 deletion tracking, DPA audit hooks |
| `AdvisorConfig(use_threshold=0.0)` | Always advise | Zero tolerance for missed data in erasure requests |
| `DynamicWorkflow(max_agents=8)` | Up to 8 | One agent per data store; topology determined at runtime |
| `meshflow-forensic` | GDPR report | Generates Art. 30 Records of Processing Activities |

### Post-execution report

```bash
meshflow-forensic audit meshflow_runs.db \
  --format gdpr \
  --html gdpr_erasure_report.html
```

The HTML report includes: data categories processed, systems touched, DascGate verdicts, and an EU AI Act Art. 13 transparency summary.

---

## Common patterns across all three

| Pattern | Healthcare | Finance | Legal |
|---|---|---|---|
| Compliance profile | `hipaa` | `sox` | `gdpr` |
| Cost cap | `$0.10/note` | `$2.00/run` | None |
| HITL checkpoint | No | Yes (pre-filing) | Advisory (AdvisorAgent) |
| Durable execution | Optional | Required | Optional |
| Forensic report | PHI events | Section 302 | Art. 17 erasure |
| Audit retention | 6 years (HIPAA) | 7 years (SOX) | Duration of processing |
