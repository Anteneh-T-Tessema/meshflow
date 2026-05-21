# MeshFlow

**The control plane for multi-agent systems.**

```text
Use LangGraph to build graphs.
Use CrewAI to build crews.
Use AutoGen to build agent conversations.
Use MeshFlow to govern, orchestrate, audit, and standardize them all.
```

MeshFlow is not a replacement for LangGraph, CrewAI, or AutoGen. It is the governance
and orchestration standard **above** them — a unified control plane that runs agents
from any framework under one policy, identity, audit, and security layer.

```text
Policy → Identity → Risk Gate → Runtime → Observability → Audit → Learning
```

---

## The problem MeshFlow solves

Building multi-agent systems is now easy. Governing them is not.

When you run a LangGraph graph, a CrewAI crew, and an AutoGen conversation in the same
pipeline, you have no unified answer to:

- Which agent made this decision?
- Was this action within policy?
- What did this agent cost, and in what region?
- Was the output tampered with?
- Which tool call touched external state?
- Can I replay from step 4?
- Was this flagged for human approval?

MeshFlow wraps every agent step — regardless of origin — in a governed execution kernel
that answers all of these questions consistently and automatically.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                         MeshFlow Control Plane                          │
│                                                                         │
│  ┌──────────┐   ┌──────────────────────────────────────────────────┐   │
│  │  Policy  │──▶│              StepRuntime Kernel                  │   │
│  │ (budget, │   │                                                  │   │
│  │  HITL,   │   │  pre_step:  identity · circuit-breaker ·        │   │
│  │  gate)   │   │             guardian scan · risk gate ·          │   │
│  └──────────┘   │             budget check · HITL escalation       │   │
│                 │                                                  │   │
│  ┌──────────┐   │  execute:   node.run() · OTEL span ·             │   │
│  │  Ledger  │◀──│             checkpoint                           │   │
│  │ (replay) │   │                                                  │   │
│  └──────────┘   │  post_step: uncertainty · cost accounting ·      │   │
│                 │             audit write · memory · collusion ·   │   │
│                 │             CAEP revocation · behaviour monitor  │   │
│                 └──────────────────────────────────────────────────┘   │
│                                        │                               │
│         ┌──────────┬──────────┬────────┴─────┬──────────┬──────────┐  │
│         │ native   │LangGraph │    CrewAI    │ AutoGen  │  human   │  │
│         │ agents   │  graph   │    crew      │  agents  │ approval │  │
│         └──────────┴──────────┴──────────────┴──────────┴──────────┘  │
│                          Universal MeshNode                            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Comparison

| | LangGraph | CrewAI | AutoGen | **MeshFlow** |
| --- | --- | --- | --- | --- |
| Graph orchestration | ★★★★★ | ★★★ | ★★ | ★★★ |
| Role/task ergonomics | ★★★ | ★★★★★ | ★★★ | ★★★ |
| Conversational agents | ★★ | ★★ | ★★★★★ | ★★ |
| Wraps other frameworks | ❌ | ❌ | ❌ | **yes — any framework** |
| Policy enforcement | plug in yourself | plug in yourself | plug in yourself | **built-in** |
| Deterministic safety gate | ❌ | ❌ | ❌ | **DascGate** |
| Agent identity (DID/VC) | ❌ | ❌ | ❌ | **W3C DIDs** |
| Collusion detection | ❌ | ❌ | ❌ | **3 detectors** |
| Tamper-evident audit ledger | ❌ | ❌ | ❌ | **hash-chained** |
| Replay from checkpoint | ❌ | ❌ | ❌ | **ReplayLedger** |
| Conformance certification | ❌ | ❌ | ❌ | **L0–L3 suite** |
| Environmental cost tracking | ❌ | ❌ | ❌ | **MARLIN-style** |
| Maturity | Production | Production | Production | **Beta** |

---

## Install

```bash
pip install meshflow
pip install meshflow[dev]          # + pytest ruff mypy
```

---

## Three lines to run

```python
from meshflow import Mesh, Policy
result = await Mesh().run("Research the top 3 LLM frameworks and compare them")
print(result.output)
```

---

## Universal MeshNode — wrap anything

Every LangGraph graph, CrewAI crew, AutoGen agent, HTTP service, Python callable,
or human approver becomes a `MeshNode` before being submitted to the governance kernel.

```python
from meshflow import Mesh, MeshNode, WorkflowDefinition, Policy

# Wrap any external framework object
wf = (
    WorkflowDefinition("acquisition_analysis", policy=Policy(budget_usd=5.00))
    .add_node(MeshNode.from_crewai("research",  my_crewai_crew))
    .add_node(MeshNode.from_langgraph("validate", my_langgraph_graph))
    .add_node(MeshNode.human_approval("approve"))
    .add_node(MeshNode.from_callable("summarize", my_python_fn))
    .add_edge("research", "validate")
    .add_edge("validate", "approve")
    .add_edge("approve",  "summarize")
)

result = await Mesh().run_workflow(wf, task="Analyse Acme Corp acquisition")
print(result.output)
print(f"Ledger: {result.ledger_db}  run_id={result.run_id}")
```

Every step in this pipeline — regardless of which framework ran it — passes through
the identical StepRuntime governance kernel. One path. Every node. No exceptions.

---

## YAML workflow (reproducible, git-committable)

```yaml
# meshflow.yaml
name: research_pipeline
version: "1"

policy:
  budget_usd: 1.00
  max_steps: 20
  enable_guardian: true
  human_approval_tier: irreversible    # pause on IRREVERSIBLE-tier nodes

nodes:
  planner:
    kind: native
    role: planner

  research_crew:
    kind: crewai
    ref: crews.market_research         # resolved from node_registry at runtime

  validator:
    kind: langgraph
    ref: graphs.fact_check

  final_writer:
    kind: native
    role: executor

edges:
  - planner -> research_crew
  - research_crew -> validator
  - validator -> final_writer

terminal:
  - final_writer
```

```python
from meshflow import Mesh, WorkflowDefinition

wf = WorkflowDefinition.from_yaml(
    "meshflow.yaml",
    node_registry={
        "crews.market_research": my_crew,
        "graphs.fact_check":     my_graph,
    },
)
result = await Mesh().run_workflow(wf, task="Q2 market analysis")
```

---

## Stream governed events

```python
async for event in Mesh().stream("Analyse our Q2 revenue"):
    print(f"[{event.role}] confidence={event.uncertainty:.2f}  ${event.cost_usd:.4f}")
    if event.event_type == "step_blocked":
        print(f"  blocked by: {event.blocked_by}")
```

---

## Replay any run

```python
from meshflow import ReplayLedger

ledger  = ReplayLedger("meshflow_runs.db")
summary = await ledger.run_summary(run_id)
steps   = await ledger.get_run(run_id)

# Time-travel: inspect the state at step N
checkpoint = await ledger.get_checkpoint(run_id, step_index=3)
```

---

## CLI

```bash
# Run a workflow YAML
meshflow run meshflow.yaml --task "analyse Q2 revenue"

# Stream governed events as they emit
meshflow stream meshflow.yaml

# Inspect a past run
meshflow replay <run_id> --db meshflow_runs.db

# Replay as JSON for programmatic inspection
meshflow replay <run_id> --json

# Run the conformance suite against a node adapter kind
meshflow conformance python    # or: native langgraph crewai autogen

# Print workflow topology without running
meshflow describe meshflow.yaml

# Start the HTTP runtime (multi-language access)
meshflow serve --host 0.0.0.0 --port 8765
```

---

## Conformance suite

MeshFlow ships a conformance suite that certifies an adapter at four levels:

| Level | Requirement |
| --- | --- |
| L0 | Node executes and returns non-empty output |
| L1 | Handles exceptions; runtime does not raise |
| L2 | Identity provisioned; uncertainty scored |
| L3 | Audit ledger written; HITL pause fires |

```bash
$ meshflow conformance python

============================================================
  MeshFlow Conformance Report — kind: python
============================================================

  L0 [PASS] Basic — node executes and returns non-empty output
    [+] basic_execution           echo:conformance test task

  L1 [PASS] Reliable — handles exceptions, respects timeout
    [+] exception_handling        node_exception:synthetic_failure

  L2 [PASS] Governed — budget accounting, identity, uncertainty
    [+] identity_propagation      DID provisioned: True
    [+] uncertainty_scoring       uncertainty=0.128

  L3 [PASS] Auditable — ledger entries, HITL pause/resume
    [+] audit_ledger_writes       4 records written
    [+] hitl_pause                paused=True

============================================================
  Score     : 6/6 checks passed
  Level     : L3
  Verdict   : CONFORMANT
============================================================
```

---

## Governance layers

| Layer | What it does |
| --- | --- |
| **Identity (L2.10)** | W3C DIDs + VCs. CAEP revocation at risk > 0.85. JIT provisioning per run. |
| **Guardian (L2.7)** | Injection scanner (13 patterns), tool-chain DoS detector, behavioural monitor. |
| **DascGate (L2.5)** | Deterministic risk gate. `AutoRiskClassifier` overrides self-declared tiers. Hash-chained ledger. |
| **Uncertainty (L2.11)** | SAUP + UProp propagation + EMA calibration. Adaptive: warn → verify → HITL → abort. |
| **Collusion (L2.12)** | Coalition Advantage metric, objective drift tracker, steganographic channel detector. |
| **Telemetry (L2.8)** | OTEL spans on every step, MCP call, RAG retrieval, gate decision. |
| **MCP Gateway (L2.9)** | Signed manifest registry, rate limiting, per-turn cost cap. |
| **MEM1 (L3.1)** | RL-based memory consolidation. HMAC tamper detection. 3.7× compression. |
| **RAG (L4.1)** | Hybrid BM25 + vector + RRF. RAGAS evaluation. Chunks typed as Evidence (trusted/untrusted). |
| **MARLIN (V7)** | Environmental cost: carbon (gCO2eq) and water (mL) per model/region. |
| **CORAL (V7)** | Cross-run learning via SQLite pattern store. |

---

## HTTP runtime (multi-language access)

```bash
meshflow serve --host 0.0.0.0 --port 8765
```

```http
POST /run
Content-Type: application/json

{"task": "Research the market for carbon capture technology", "policy": {"budget_usd": 1.0}}
```

Any language can submit tasks and receive governed `RunResult` JSON.

---

## Adapter reference

```python
from meshflow import MeshNode

# LangGraph
MeshNode.from_langgraph("validator",  compiled_graph)

# CrewAI
MeshNode.from_crewai("research_team", crew)

# AutoGen
MeshNode.from_autogen("debate",       agent, manager=group_manager)

# Any Python callable (sync or async)
MeshNode.from_callable("transform",   my_fn, risk=RiskTier.EXTERNAL_IO)

# External HTTP service
MeshNode.from_http("scorer",          "https://api.example.com/score")

# Human approval gate
MeshNode.human_approval("sign_off",   prompt_fn=lambda t: input(f"Approve? {t[:80]}: "))
```

---

## Development

```bash
git clone https://github.com/Anteneh-T-Tessema/meshflow
pip install -e ".[dev]"
make test        # 81 tests
make check       # lint + typecheck + test
make run-quickstart
```

---

## Roadmap

- [ ] Parallel branch execution (fan-out / fan-in)
- [ ] Conditional edge routing (expression-based)
- [ ] Durable pause / resume across process restarts
- [ ] Web UI for live run inspection and replay
- [ ] OpenTelemetry OTLP export to Grafana / Jaeger
- [ ] Signed SBOM for every run (supply-chain audit)
- [ ] `meshflow conformance` registry and public leaderboard

---

## License

Apache 2.0 — see [LICENSE](LICENSE)
