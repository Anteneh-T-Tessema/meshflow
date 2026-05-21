# MeshFlow

**Governed multi-agent orchestration** — run any agent from any framework under a unified policy, audit, identity, and security control plane.

---

## What MeshFlow Is

MeshFlow is not a replacement for LangGraph, CrewAI, or AutoGen.

It is a **governed wrapper** that runs agents — native or imported from other frameworks — under a unified control plane that enforces policy, tracks identity, applies deterministic safety gates, and produces a tamper-evident audit trail at every step.

```text
┌─────────────────────────────────────────────────────────┐
│                    MeshFlow Runtime                      │
│                                                         │
│  GovernedStepExecutor (applied to EVERY agent step):    │
│  Guardian → PolicyEngine → Agent.step() → Uncertainty   │
│  → DascGate → CAEP → Collusion → Telemetry → Eco        │
│                                                         │
│  Agent Pipeline (native or imported):                   │
│  Planner → Researcher → Executor → Critic ↩retry        │
│  CrewAI agent / LangGraph runnable / AutoGen agent /    │
│  any async callable                                     │
└─────────────────────────────────────────────────────────┘
```

**Every agent step passes through 14 governance checks — regardless of which framework the agent came from.**

---

## Comparison

| | LangGraph | CrewAI | AutoGen | **MeshFlow** |
| --- | --- | --- | --- | --- |
| Graph orchestration | ★★★★★ | ★★★ | ★★ | ★★★ |
| Role/task ergonomics | ★★★ | ★★★★★ | ★★★ | ★★★ |
| Conversational agents | ★★ | ★★ | ★★★★★ | ★★ |
| Policy enforcement | plug in yourself | plug in yourself | plug in yourself | **built-in** |
| Deterministic safety gate | ❌ | ❌ | ❌ | **DascGate** |
| Agent identity (DID/VC) | ❌ | ❌ | ❌ | **W3C DIDs** |
| Collusion detection | ❌ | ❌ | ❌ | **3 detectors** |
| Tamper-evident audit ledger | ❌ | ❌ | ❌ | **hash-chained** |
| Environmental cost tracking | ❌ | ❌ | ❌ | **MARLIN-style** |
| Cross-run learning | ❌ | ❌ | ❌ | **CORAL-style** |
| Maturity | Production | Production | Production | **Beta** |

**Use LangGraph** when you need the richest graph runtime and ecosystem.  
**Use CrewAI** when you want the fastest time-to-working-crew.  
**Use AutoGen** when agents need to talk conversationally.  
**Use MeshFlow** when you need governance, audit, and security as first-class concerns — or when you need agents from multiple frameworks under one control plane.

---

## Install

```bash
pip install meshflow
pip install meshflow[dev]   # + pytest, ruff, mypy
```

---

## Three lines to run

```python
from meshflow import Mesh, Policy
import asyncio

result = await Mesh().run("Research the top 3 LLM frameworks and compare them")
print(result.output)
```

## Stream governed events

```python
async for event in Mesh().stream("Analyse our Q2 revenue"):
    print(f"[{event.role}] confidence={event.uncertainty:.2f}  ${event.cost_usd:.4f}")
    if event.event_type == "step_blocked":
        print(f"  blocked by: {event.blocked_by}")
```

## Import from any framework

```python
from meshflow.agents.adapters import from_crewai, from_autogen, from_langgraph

mesh = Mesh(agents=[
    from_crewai(my_crew_agent),
    from_autogen(my_autogen_agent),
    from_langgraph(my_lg_runnable),
])
result = await mesh.run("Research and summarise the competitive landscape")
```

## YAML config

```yaml
# meshflow.yaml
policy:
  budget_usd: 1.00
  timeout_s: 120
  enable_guardian: true
agents:
  - role: planner
    model: claude-sonnet-4-6
  - role: researcher
    model: claude-sonnet-4-6
  - role: executor
    model: claude-haiku-4-5-20251001
  - role: critic
    model: claude-sonnet-4-6
```

```python
mesh = Mesh.from_yaml("meshflow.yaml")
```

## HTTP runtime (any language)

```bash
python -m meshflow.runtime.server --port 8000
```

```bash
curl -X POST http://localhost:8000/run \
  -d '{"task": "Summarise Q2 report", "policy": {"budget_usd": 0.25}}'
```

---

## Governance layers

| Layer | What it does |
| --- | --- |
| **L2.5 DascGate** | Deterministic risk gate. `AutoRiskClassifier` overrides self-declared tiers. Hash-chained ledger. |
| **L2.7 Guardian** | Injection scanner, tool-chain DoS detector, behavioural monitor. Fires before DascGate. |
| **L2.8 Telemetry** | OTEL spans on every step, MCP call, RAG retrieval, dasc decision. |
| **L2.9 MCPGateway** | Signed manifest registry, rate limiting, per-turn cost cap. |
| **L2.10 Identity** | W3C DIDs + VCs. CAEP revocation at risk > 0.85. JIT provisioning, revoked at run end. |
| **L2.11 Uncertainty** | SAUP + UProp propagation + EMA calibration. Adaptive: warn → verify → HITL → abort. |
| **L2.12 Collusion** | Coalition Advantage, objective drift, steganographic channel detection. |
| **L3.1 MEM1** | RL-based memory consolidation. HMAC tamper detection. 3.7x compression. |
| **L4.1 RAG** | Hybrid BM25 + vector + RRF. RAGAS evaluation. Chunks typed as Evidence (trusted/untrusted). |

---

## Development

```bash
git clone https://github.com/Anteneh-T-Tessema/meshflow
pip install -e ".[dev]"
make test        # 41 tests
make check       # lint + typecheck + test
make run-quickstart  # no API key needed
```

---

## Licence

Apache 2.0
