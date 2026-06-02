# MeshFlow × Haystack Integration

Run any Haystack pipeline through MeshFlow's governance kernel — GDPR/HIPAA compliance, PHI detection, tamper-evident audit trail, and cost governance built in.

## Why

Haystack handles retrieval quality. MeshFlow handles what happens to that retrieved content once it's in your agent pipeline: who accessed it, whether it contained PHI, what policy was applied, and what the audit trail says.

The two layers don't overlap. Together they cover the full production stack for regulated-industry RAG deployments.

---

## Installation

```bash
pip install meshflow haystack-ai   # Haystack v2
# or
pip install meshflow farm-haystack  # Haystack v1
```

MeshFlow's Haystack adapter has zero required dependencies — it works with any object that has a `.run(inputs: dict) -> dict` method.

---

## Quickstart (5 minutes)

```python
from meshflow.integrations.haystack import governed_haystack_pipeline
from meshflow import Workflow, Agent

# Wrap your existing Haystack pipeline — no rewrite required
adapter = governed_haystack_pipeline(
    haystack_pipeline=my_pipeline,   # any Haystack Pipeline object
    compliance_profile="gdpr",       # "gdpr" | "hipaa" | "iso27001" | ...
    pii_scan=True,                   # scan retrieved docs for PHI/PII
)

# Use it like any other MeshFlow node
result = (
    Workflow()
    .add(adapter, Agent("summariser"))
    .run("Retrieve clinical notes for patient 42 and summarise risks")
)

print(result.summary())
# Governed. GDPR-compliant. Audited. PHI detected and masked.
```

---

## What the adapter does

Every call through `governed_haystack_pipeline` passes through the full StepRuntime kernel:

| Layer | What happens |
|---|---|
| **PHI/PII scan** | `SensitiveDataDetector` scans retrieved documents for 11 PHI/PII patterns and 12 credential patterns before they reach any downstream agent |
| **Masking** | Detected PII is masked in both the answer text and document content (`mask_pii=True`) or the step is blocked entirely (`block_on_pii=True`) |
| **Compliance profile** | `compliance_profile="gdpr"` applies GDPR Art. 30 data lineage tracking; `"hipaa"` applies HIPAA minimum-necessary enforcement |
| **Audit ledger** | Every retrieval step writes a `StepRecord` to the `ReplayLedger` with a SHA-256 hash-chained entry |
| **Tool-call enforcement** | If the Haystack pipeline calls external tools mid-execution, the `ToolCallInterceptor` evaluates each call against policy before dispatch |
| **Cost governance** | Token usage from the pipeline is charged against the workflow's `CostCap` |

---

## API reference

### `governed_haystack_pipeline()`

```python
from meshflow.integrations.haystack import governed_haystack_pipeline

adapter = governed_haystack_pipeline(
    haystack_pipeline,           # required — any object with .run(inputs) -> dict
    node_id="haystack",          # label in the audit ledger
    compliance_profile="gdpr",   # compliance profile applied at the kernel level
    pii_scan=True,               # scan retrieved content for PHI/PII
    mask_pii=True,               # mask detected PII (False = flag only)
    block_on_pii=False,          # block the step entirely if PII is found
    query_key="query",           # key used to pass the task into pipeline.run()
    answer_key="answers",        # key in the result dict that holds the answer
)
```

Returns a `HaystackStepAdapter` — a `MeshNode` you can add to any `Workflow` or `WorkflowDefinition`.

### `HaystackStepAdapter`

```python
from meshflow.integrations.haystack import HaystackStepAdapter

adapter = HaystackStepAdapter(
    pipeline=my_pipeline,
    node_id="clinical-retriever",
    compliance_profile="hipaa",
    pii_scan=True,
    mask_pii=True,
    block_on_pii=True,           # for strict HIPAA — block rather than mask
)
```

The adapter is a `MeshNode` subclass. It can be used anywhere a `MeshNode` is accepted, including `WorkflowDefinition.add_node()`.

### `HaystackResult`

The structured output returned in `NodeOutput.structured`:

```python
{
    "answers": [...],        # original pipeline answer list
    "documents": [...],      # retrieved documents (masked if pii_scan=True)
}
```

`NodeOutput.metadata` contains:

```python
{
    "pii_detected": True,        # whether PHI/PII was found
    "pii_kinds": ["ssn", "email"]  # categories detected
}
```

---

## Compliance profiles

| Profile | What it enforces |
|---|---|
| `"gdpr"` | Art. 30 processing register; data subject rights via tenant purge |
| `"hipaa"` | Minimum necessary standard; PHI access audit |
| `"iso27001"` | Access control logging; information classification |
| `"ccpa"` | Consumer data access and deletion rights |
| `"eu_ai_act"` | Art. 9 risk management system documentation |

```python
# HIPAA — block the step entirely if PHI is found in retrieved content
adapter = governed_haystack_pipeline(
    pipeline=ehr_pipeline,
    compliance_profile="hipaa",
    pii_scan=True,
    mask_pii=False,
    block_on_pii=True,
)
```

---

## Multi-node workflow example

```python
from meshflow import Workflow, Agent, CostCap
from meshflow.integrations.haystack import governed_haystack_pipeline

retriever = governed_haystack_pipeline(
    haystack_pipeline=ehr_pipeline,
    node_id="ehr-retriever",
    compliance_profile="hipaa",
    pii_scan=True,
    block_on_pii=True,
)

result = (
    Workflow(cost_cap=CostCap(usd=2.00))
    .add(
        retriever,
        Agent("clinical-summariser"),
        Agent("risk-analyst"),
    )
    .run("Summarise all adverse drug reactions for patient cohort Q2 2026")
)

print(result.summary())
print(f"Run ID: {result.run_id}")
# meshflow audit export --run-id {result.run_id} --format json
```

---

## Offline / test usage

The adapter works without Haystack installed. Pass any object with `.run(inputs: dict) -> dict`:

```python
class MockPipeline:
    def run(self, inputs: dict) -> dict:
        return {"answers": [{"answer": "mock answer for testing"}]}

adapter = governed_haystack_pipeline(MockPipeline(), pii_scan=False)
```

This is how the MeshFlow test suite exercises the integration — no Haystack dependency in CI.

---

## Haystack v1 vs v2

| Version | Result format | Adapter behaviour |
|---|---|---|
| **v1** | `{"answers": [{"answer": "..."}], "documents": [...]}` | Reads `answers` key directly |
| **v2** | Component-keyed: `{"llm": {"replies": [...]}, "retrieved_documents": [...]}` | Falls back to JSON dump of full result when `answers` key absent |

For v2 pipelines with custom output keys, set `answer_key` to match:

```python
adapter = governed_haystack_pipeline(
    pipeline=my_v2_pipeline,
    answer_key="llm",   # or whichever component holds the final answer
)
```

---

## Verifying the audit trail

After a run, verify the tamper-evident chain and inspect tool calls:

```bash
# Export the full run as JSON (flat array, audit chain spec format)
meshflow audit export --run-id <run_id> --format json --out run.json

# Verify the hash chain independently (no MeshFlow import required)
python meshflow_verify_chain.py run.json

# Or via the CLI
meshflow audit verify-chain --run-id <run_id>
```

Each step record includes `metadata.tool_calls` — every tool call attempted during the retrieval step, whether it was allowed or blocked, and which policy rule applied.

---

## deepset partnership

MeshFlow and deepset are exploring a joint reference architecture for EU-regulated RAG deployments. If you're building a clinical NLP, legal AI, or financial document system using Haystack + MeshFlow, we'd like to hear from you.

Contact: anteneh@yayasystems.com
