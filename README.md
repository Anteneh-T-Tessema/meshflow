# MeshFlow

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.7.0-orange.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-81%20passing-brightgreen.svg)](tests/)
[![Status](https://img.shields.io/badge/status-Beta-yellow.svg)](README.md)

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

Requires Python 3.11+. No mandatory framework dependencies — LangGraph, CrewAI,
and AutoGen are only needed when you wrap their objects.

---

## Quick start

### Minimal — native agents, no API key needed

```python
import asyncio
from meshflow import Mesh

async def main():
    result = await Mesh().run("Summarise the benefits of multi-agent systems")
    print(result.output)
    print(f"cost=${result.total_cost_usd:.4f}  tokens={result.total_tokens}")

asyncio.run(main())
```

### With explicit policy

```python
import asyncio
from meshflow import Mesh, Policy

async def main():
    result = await Mesh(
        policy=Policy(
            budget_usd=2.00,
            enable_guardian=True,
            enable_uncertainty=True,
            enable_collusion_audit=True,
        )
    ).run("Analyse our Q2 revenue and draft an executive summary")

    print(result.output)
    print(f"run_id={result.run_id}  ledger_entries={result.ledger_entries}")

asyncio.run(main())
```

### Stream events as they emit

```python
import asyncio
from meshflow import Mesh

async def main():
    async for event in Mesh().stream("Research the top 3 LLM frameworks"):
        print(
            f"[{event.role:<12}] "
            f"confidence={event.uncertainty:.2f}  "
            f"cost=${event.cost_usd:.5f}"
        )
        if event.event_type == "step_blocked":
            print(f"  blocked by: {event.blocked_by}")

asyncio.run(main())
```

---

## Performance overhead

> **The 15 governance checks are entirely local — no extra LLM calls.**

A common concern when adding a governance layer is: "Does this mean every step now
makes 15 LLM API calls instead of one?" The answer is no. Each check is a fast,
in-process operation:

| Check | Implementation | Typical overhead |
| --- | --- | --- |
| Identity check | Dict lookup + DID validity flag | < 0.1 ms |
| Circuit breaker | Counter read | < 0.1 ms |
| Guardian scan | Regex match against 13 patterns | < 1 ms |
| Risk classification | Keyword matching + heuristics | < 1 ms |
| Budget pre-check | Arithmetic comparison | < 0.1 ms |
| Uncertainty scoring | Jaccard similarity over token sets | < 5 ms |
| Collusion recording | Append to in-memory list | < 0.1 ms |
| Audit ledger write | SQLite INSERT | < 2 ms |
| Behavioural monitor | Rolling z-score update | < 1 ms |
| Carbon accounting | Lookup table multiplication | < 0.1 ms |

**Total governance overhead per step: ~5–10 ms.**
The LLM call itself typically takes 500 ms–5 000 ms, so MeshFlow adds less than 1%
latency to each step and zero additional API tokens.

---

## State between frameworks

> **MeshFlow uses a shared JSON context dict as the universal handoff format.**

Different frameworks manage state very differently: LangGraph uses a typed state
dictionary, CrewAI returns strings or Task objects, AutoGen produces conversation
histories. MeshFlow normalises all of them through a simple, explicit contract.

Every node receives a `NodeInput` and returns a `NodeOutput`:

```python
@dataclass
class NodeInput:
    task: str                     # the original task string — unchanged across all nodes
    context: dict[str, Any]       # shared state — accumulated from all prior node outputs

@dataclass
class NodeOutput:
    content: str                  # primary text output — passed to the next node's context
    structured: dict[str, Any]    # optional additional fields merged into shared context
    tokens_used: int
    confidence: float
```

After each step, MeshFlow merges `NodeOutput.structured` into the shared `context` dict,
which becomes the next node's `NodeInput.context`. This means:

- A **CrewAI node** that returns `NodeOutput(content="market report ...", structured={"market_size": "$4.2B"})` makes `context["market_size"]` available to the downstream LangGraph node.
- A **LangGraph node** reads the task string and the full context dict and returns its own `NodeOutput`.

**State translation is explicit, not magic.** If you need a CrewAI `Task` object to
become a LangGraph typed state, you write that mapping in the node's runner function.
The `MeshNode.from_*` factories give you the right place to put that logic.

---

## Universal MeshNode — wrap anything

Every LangGraph graph, CrewAI crew, AutoGen agent, HTTP service, Python callable,
or human approver becomes a `MeshNode` before being submitted to the governance kernel.

```python
import asyncio
from meshflow import Mesh, MeshNode, WorkflowDefinition, Policy

async def main():
    wf = (
        WorkflowDefinition("acquisition_analysis", policy=Policy(budget_usd=5.00))
        .add_node(MeshNode.from_crewai("research",   my_crewai_crew))
        .add_node(MeshNode.from_langgraph("validate", my_langgraph_graph))
        .add_node(MeshNode.human_approval("approve"))
        .add_node(MeshNode.from_callable("summarize", my_python_fn))
        .add_edge("research",  "validate")
        .add_edge("validate",  "approve")
        .add_edge("approve",   "summarize")
    )

    result = await Mesh().run_workflow(wf, task="Analyse Acme Corp acquisition")
    print(result.output)
    print(f"ledger={result.ledger_db}  run_id={result.run_id}")

asyncio.run(main())
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
  human_approval_tier: irreversible    # pause before IRREVERSIBLE-tier nodes

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
import asyncio
from meshflow import Mesh, WorkflowDefinition

async def main():
    wf = WorkflowDefinition.from_yaml(
        "meshflow.yaml",
        node_registry={
            "crews.market_research": my_crew,
            "graphs.fact_check":     my_graph,
        },
    )
    result = await Mesh().run_workflow(wf, task="Q2 market analysis")
    print(result.output)

asyncio.run(main())
```

The YAML file is the artifact you commit to git. It is reproducible and inspectable
without running any code. `meshflow describe meshflow.yaml` prints the topology.

> **Note on parallel execution:** The MeshFlow YAML orchestrator currently runs nodes
> sequentially (one edge at a time). If a node is a LangGraph graph, that graph runs
> its own internal parallel branches without restriction — MeshFlow only sees it as a
> single call. Native fan-out / fan-in between MeshFlow nodes is on the roadmap.

---

## Replay any run

Every governed step is written to an append-only SQLite ledger automatically.

```python
import asyncio
from meshflow import ReplayLedger

async def main():
    ledger  = ReplayLedger("meshflow_runs.db")

    # Summary — cost, tokens, carbon, blocked steps
    summary = await ledger.run_summary(run_id)
    print(summary)

    # Full step-by-step history
    steps = await ledger.get_run(run_id)

    # Time-travel: inspect state at a specific checkpoint
    checkpoint = await ledger.get_checkpoint(run_id, step_index=3)

    # Export entire run as JSON for archiving
    json_dump = await ledger.export_run(run_id)

asyncio.run(main())
```

> **Storage backends:** The default ledger uses SQLite, which works well for
> single-node production deployments. For distributed or high-concurrency environments
> a PostgreSQL / S3 backend is on the roadmap. The `ReplayLedger` interface is
> intentionally simple so swapping backends requires no application code changes.

---

## CLI

```bash
# Run a workflow YAML to completion
meshflow run meshflow.yaml --task "analyse Q2 revenue"

# Stream governed events as they emit
meshflow stream meshflow.yaml --task "analyse Q2 revenue"

# Inspect a past run from the ledger
meshflow replay <run_id> --db meshflow_runs.db

# Export a run as JSON
meshflow replay <run_id> --json

# Run the conformance suite against a node adapter kind
meshflow conformance python          # or: native langgraph crewai autogen

# Print workflow topology without running
meshflow describe meshflow.yaml

# Start the language-agnostic HTTP runtime
meshflow serve --host 0.0.0.0 --port 8765
```

---

## Conformance suite

`meshflow conformance` certifies a node adapter at four levels. This is how
MeshFlow becomes a standard, not just a library: adapters declare conformance level,
not just "supports MeshFlow."

| Level | What is verified |
| --- | --- |
| L0 | Node executes and returns non-empty output |
| L1 | Exceptions are caught; runtime never raises to caller |
| L2 | DID provisioned; uncertainty score computed |
| L3 | Audit ledger written; HITL pause fires at threshold |

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

All checks are local in-process operations — **no extra LLM calls**.

| Layer | Plain English | Technical detail |
| --- | --- | --- |
| **Identity** | Every agent gets a cryptographic ID at spawn, destroyed at run end. If an agent's behaviour becomes risky mid-run, its ID is revoked immediately. | W3C Decentralised Identifiers (DIDs) + Verifiable Credentials. CAEP (Continuous Access Evaluation Profile) revokes at risk score > 0.85. |
| **Guardian** | Scans every input for prompt injection attempts, jailbreak triggers, and exfiltration patterns before the agent ever sees the text. Also detects if a tool chain could amplify into a denial-of-service loop. | 13 regex patterns (override/impersonation/jailbreak/exfiltration). Tool-chain amplification factor. Rolling z-score behavioural baseline. |
| **DascGate** | Before any irreversible action (deploy, delete, financial transaction), classifies its risk level and either blocks it or escalates to a human. Agents cannot lie about their own risk tier — a classifier overrides self-declaration. | `AutoRiskClassifier` overrides self-declared tiers via keyword matching + failure rate + taint status. Hash-chained SQLite audit ledger. |
| **Uncertainty** | Tracks how confident the pipeline actually is, not just how confident each agent claims to be. Corrects for agents that are historically overconfident. Triggers escalation or abort if confidence falls too low. | Semantic consistency (Jaccard similarity across rephrased queries). Multiplicative upstream propagation. EMA (exponential moving average) bias correction. Thresholds: warn → verify → HITL → abort. |
| **Collusion** | Detects when a group of agents starts coordinating in unexpected ways — either forming coalitions that override policy, drifting from their original objectives, or hiding information in subtle patterns. | Coalition Advantage metric (CA > 1.8 triggers alert). Objective drift via correlated negative slope. Steganographic channel detection via Pearson correlation of output lengths. |
| **Telemetry** | Every step, tool call, retrieval, and gate decision gets a distributed trace span. Plug into any OpenTelemetry-compatible backend (Jaeger, Grafana, Datadog). | OpenTelemetry spans with `run_id`, `agent_id`, cost, tokens, duration, verdict. |
| **MCP Gateway** | Validates, rate-limits, and cost-caps every Model Context Protocol tool call before execution. Tools must be registered with a signed manifest. | Signed manifest registry, per-tool rate limits, per-turn USD cost cap. |
| **MEM1** | Compresses and consolidates agent memory so long-running pipelines don't blow their token budget on context. Detects tampered memory entries. | RL-based consolidation scoring. HMAC tamper detection. 3.7× compression ratio. Token-budget enforcement. |
| **RAG** | Retrieval-augmented generation with quality scoring. Every retrieved chunk is typed as trusted or untrusted — untrusted chunks trigger the IFC taint check before influencing any downstream action. | Hybrid BM25 + TF-IDF vector + Reciprocal Rank Fusion. RAGAS-style faithfulness and relevance scoring. Corrective loop (max 2 retries). |
| **MARLIN** | Tracks the real-world environmental cost of every LLM call: how much carbon was emitted and how much water was consumed, broken down by model tier and AWS region. | gCO2eq per 1k tokens by model + region carbon intensity. Water intensity (L/kWh) per region. Enforces a configurable carbon budget. |
| **CORAL** | Learns from every previous run. If a similar task was attempted before, recommends the agent configuration and strategy that succeeded at lowest cost. | SQLite-backed pattern store. Cosine-similarity task matching. Records success rate, cost, tokens, and carbon per strategy. |

---

## HTTP runtime (language-agnostic)

Start MeshFlow as a JSON API server so any language can submit governed tasks:

```bash
meshflow serve --host 0.0.0.0 --port 8765
```

```bash
# Run a task
curl -s -X POST http://localhost:8765/run \
  -H "Content-Type: application/json" \
  -d '{"task": "Research carbon capture markets", "policy": {"budget_usd": 1.0}}'

# Health check
curl http://localhost:8765/health
# {"ok": true, "version": "0.7.0"}
```

The server returns a `RunResult` JSON object with output, cost, audit info, and run ID.

---

## Adapter reference

```python
from meshflow import MeshNode, RiskTier

# LangGraph — wrap a compiled StateGraph
# Internal parallel branches run normally; MeshFlow governs the node as a unit
MeshNode.from_langgraph("validator",    compiled_graph)

# CrewAI — wrap a Crew or Flow
MeshNode.from_crewai("research_team",   crew)

# AutoGen — wrap a ConversableAgent, optionally with a GroupChatManager
MeshNode.from_autogen("debate",         agent, manager=group_manager)

# Python — wrap any sync or async callable
MeshNode.from_callable("transform",     my_fn, risk=RiskTier.EXTERNAL_IO)

# HTTP — wrap an external JSON service
MeshNode.from_http("scorer",            "https://api.example.com/score")

# Human — HITL approval gate (blocks until human responds)
MeshNode.human_approval("sign_off",     prompt_fn=lambda t: input(f"Approve? {t[:80]}: "))
```

---

## Key concepts

| Concept | Description |
| --- | --- |
| `MeshNode` | Universal wrapper for any agent, crew, graph, callable, or service |
| `StepRuntime` | The governance kernel — 15 local checks applied to every node, ~5–10 ms overhead |
| `WorkflowDefinition` | Declarative, YAML-loadable, graph-topological workflow |
| `ReplayLedger` | Append-only SQLite run history with checkpoint and export |
| `Policy` | Single declaration point for budget, HITL, gate, guardian, and circuit breaker |
| `NodeInput` / `NodeOutput` | Shared handoff contract between nodes — the universal state format |
| `DascGate` | Deterministic safety gate that classifies and blocks/escalates risky actions |
| `Guardian` | Input scanner that blocks prompt injection and detects tool-chain abuse |
| Conformance L0–L3 | Tiered certification proving an adapter meets governance requirements |

---

## Development

```bash
git clone https://github.com/Anteneh-T-Tessema/meshflow
cd meshflow
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

make test            # run all tests
make check           # lint + typecheck + tests
make run-quickstart  # simulated run, no API key needed
make run-live        # real LLM run — requires ANTHROPIC_API_KEY in .env
```

### Environment

```bash
cp .env.example .env
# add: ANTHROPIC_API_KEY=sk-ant-...
```

### Project layout

```text
meshflow/
  core/
    node.py       — MeshNode universal abstraction
    runtime.py    — StepRuntime governed execution kernel
    workflow.py   — WorkflowDefinition + YAML loader
    ledger.py     — ReplayLedger append-only run history
    mesh.py       — Mesh orchestrator (run / stream / run_workflow)
    policy.py     — BudgetTracker, CircuitBreaker, PolicyEngine
    schemas.py    — All shared data types
  security/
    guardian.py   — Injection scanner, DoS detector, behavioural monitor
    dasc_gate.py  — Deterministic risk gate + hash-chained ledger
    identity.py   — W3C DID provisioning + CAEP revocation
  intelligence/
    uncertainty.py — Confidence tracking, propagation, calibration
    collusion.py   — Coalition + drift + steganographic channel detection
    mem1.py        — Memory consolidation with tamper detection
    rag.py         — Hybrid retrieval + quality evaluation
  observability/
    telemetry.py  — OpenTelemetry tracer
  mcp/
    gateway.py    — MCP signed manifest registry + rate limiter
  efficiency/
    environmental.py — Carbon + water cost tracking
    cross_run.py     — Cross-run learning and config recommendation
  cli/
    main.py       — CLI: run stream replay conformance serve describe
  runtime/
    server.py     — HTTP JSON runtime server
```

---

## Roadmap

- [ ] **Fan-out / fan-in** — parallel branch execution in the MeshFlow YAML orchestrator
  (LangGraph subgraphs already run their own parallel branches today)
- [ ] **Conditional edge routing** — expression-based branching between YAML nodes
- [ ] **Durable HITL** — pause / resume that survives process restarts
- [ ] **PostgreSQL / S3 ledger backend** — for high-concurrency and distributed deployments
- [ ] **Web UI** — live run inspection, step-by-step replay, cost breakdown
- [ ] **OTLP export** — push traces to Grafana, Jaeger, or Datadog
- [ ] **Run SBOM** — signed software bill of materials for every governed run
- [ ] **Conformance registry** — public leaderboard of certified framework adapters

---

## License

Apache 2.0 — see [LICENSE](LICENSE)
