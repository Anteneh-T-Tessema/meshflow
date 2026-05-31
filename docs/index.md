# MeshFlow

**The golden standard of multi-agent orchestration for regulated industries.**

MeshFlow gives you typed state graphs, pre-built agents, GroupChat, governed workflows, an eval framework, SwarmTRM neural consensus, and the only agentic framework built from the ground up for HIPAA, SOX, GDPR, and PCI compliance.

---

## Why MeshFlow?

| | MeshFlow | LangGraph | CrewAI | AutoGen |
|---|---|---|---|---|
| Typed state graphs | ✅ | ✅ | ❌ | ❌ |
| Built-in guardrails | ✅ | ❌ | ❌ | ❌ |
| HIPAA/SOX/GDPR profiles | ✅ | ❌ | ❌ | ❌ |
| Policy-as-code engine | ✅ | ❌ | ❌ | ❌ |
| Secret vault | ✅ | ❌ | ❌ | ❌ |
| Tenant isolation | ✅ | ❌ | ❌ | ❌ |
| Compliance snapshots | ✅ | ❌ | ❌ | ❌ |
| SLA tracking | ✅ | ❌ | ❌ | ❌ |
| Neural consensus (SwarmTRM) | ✅ | ❌ | ❌ | ❌ |
| A2A protocol | ✅ | ❌ | ❌ | ❌ |
| 4-tier agent memory | ✅ | ❌ | ❌ | ❌ |

---

## Install

```bash
pip install meshflow
```

---

## Hello, MeshFlow

```python
import meshflow

agent = meshflow.Agent(
    name="assistant",
    role="You are a helpful assistant.",
)

result = agent.run("What is the capital of France?")
print(result.output)  # Paris
```

---

## Core Concepts

- **[Agent](agents/building.md)** — the fundamental unit of work. Has a role, tools, memory, guardrails, and a provider.
- **[Team](agents/teams.md)** — multiple agents working together with a coordination pattern (supervised, parallel, sequential).
- **[StateGraph](orchestration/state-graphs.md)** — typed, deterministic workflow graph with conditional edges and HITL checkpoints.
- **[ComplianceProfile](governance/compliance.md)** — one-line policy application: `compliance_profile("hipaa")`.
- **[StepRuntime](governance/overview.md)** — the governed execution kernel. Every agent step passes through 15 governance checks.
- **[EvalSuite](eval/running.md)** — define, run, and regression-gate your agent quality in CI.

---

## [Quick Start →](QUICKSTART.md)
