# MeshFlow

**v1.13.0 — The golden standard of multi-agent orchestration for regulated industries.**

MeshFlow gives you typed state graphs, pre-built agents, GroupChat, governed workflows, an eval framework, SwarmTRM neural consensus, and the only agentic framework built from the ground up for HIPAA, SOX, GDPR, and PCI compliance. **5,711 tests. CI green on Python 3.11 + 3.12.**

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

## What's new in v1.13.0 (Sprints 95–102)

- **[AdvisorAgent](agents/advisor.md)** — Anthropic advisor-tool pattern: a read-only advisor inspects drafts and injects structured guidance before the final response.
- **[Budgets](governance/budgets.md)** — `ThinkingBudget` + `EffortBudget` + `BudgetConfig` for fine-grained token and effort control enforced at the kernel.
- **[DynamicWorkflow](orchestration/dynamic-workflow.md)** — runtime agent spawning: the planner's output determines which specialists are created and run.
- **[ContextCompactor](agents/compactor.md)** — Claude-native, sliding-window, and rolling-summary strategies to keep long sessions within the context window.
- **[Tool Streaming](agents/tool-streaming.md)** — granular `ToolStreamEvent` hierarchy for observing tool call lifecycle in real time.
- **[meshflow-forensic](security/forensic.md)** — standalone pip package for deep audit, taint propagation, and EU AI Act compliance reporting.
- **[SOC 2 Assertion](security/soc2-assertion.md)** — programmatic SOC 2 Type II assertion engine mapping MeshFlow controls to AICPA Trust Services Criteria.
- **Competitive benchmarks** — `benchmarks/competitive_bench.py` measures MeshFlow vs LangGraph / CrewAI / AutoGen on latency and governance overhead.
- **Cost regression gate** — `meshflow.eval.cost_regression` raises `CostRegressionError` in CI when per-run cost exceeds baseline.

---

## [Quick Start →](QUICKSTART.md)
