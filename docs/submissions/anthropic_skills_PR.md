---
name: meshflow
version: "1.0"
description: >
  Invoke when the user wants to build, run, orchestrate, govern, debug, or
  optimize any multi-agent workflow, agentic pipeline, or LLM-powered system.
  Triggers on: 'build an agent', 'multi-agent', 'orchestrate agents', 'agent
  team', 'durable workflow', 'cost cap', 'token budget', 'guardrails', 'HITL',
  'human in the loop', 'parallel agents', 'agent crew', 'compliance', 'HIPAA',
  'SOC2', 'GDPR', 'audit trail', 'rate limiting agents', 'ReAct agent',
  'LangGraph', 'CrewAI', 'AutoGen', 'governed', 'production agents',
  'resumable workflow', 'agent governance', 'prompt caching', 'MCP server'.
slash_command: /meshflow
context: fork
homepage: https://github.com/Anteneh-T-Tessema/meshflow
pypi: https://pypi.org/project/meshflow/
license: Apache-2.0
author: Anteneh Tessema <anteneh@yayasystems.com>
---

# MeshFlow — Production-Safe Multi-Agent Orchestration

MeshFlow is the infrastructure layer for production Claude-powered agent
deployments. Compliant, cost-governed, and durable — out of the box, not
bolted on.

**The 7-line promise:**

```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=5.00))
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('Write a competitive analysis of our market')

# Compliant. Durable. Audited. Cost-capped. Done.
```

```bash
pip install meshflow
```

---

## Why this skill exists

79% of enterprises have adopted AI agents. Only 11% run them in production.

The gap is compliance, cost governance, and tamper-evident audit trails — the
exact things platform, legal-ops, and security teams need before they will
approve production deployment. MeshFlow closes that gap for Claude-powered
systems in one import.

---

## Patterns — match these to what the user asks

### "build an agent" / "create a Claude agent"

```python
from meshflow import Agent, tool, RiskTier

@tool(name="web_search", description="Search the web", risk=RiskTier.EXTERNAL_IO)
async def web_search(query: str) -> str:
    return results  # real implementation

agent = Agent(
    name="researcher",
    role="researcher",
    model="claude-sonnet-4-6",   # Anthropic auto-detected from model name
    tools=[web_search],
    memory=True,
    knowledge=["docs/"],         # auto-RAG injected at every step
)
result = await agent.run("Research the latest AI safety papers")
print(result["result"], result["cost_usd"], result["tokens"])
```

### "agent team" / "multi-agent" / "collaborate"

```python
from meshflow import Agent, Team

planner  = Agent(name="planner",  role="planner",  model="claude-haiku-4-5")
coder    = Agent(name="coder",    role="executor",  model="claude-sonnet-4-6")
reviewer = Agent(name="reviewer", role="critic",   model="claude-sonnet-4-6")

team = Team([planner, coder, reviewer], pattern="supervised")
result = await team.run("Build a REST API for user authentication")
```

Patterns: `"sequential"`, `"supervised"`, `"parallel"`, `"hierarchical"`, `"reflective"`

### "compliance" / "HIPAA" / "SOC2" / "GDPR" / "regulated industry"

```python
from meshflow import Agent, compliance_profile, PIIBlockGuardrail

agent = Agent(
    name="clinical-assistant",
    role="executor",
    model="claude-sonnet-4-6",
    policy=compliance_profile("hipaa"),  # or "sox", "gdpr", "pci", "nerc"
    input_guardrails=[PIIBlockGuardrail()],
    output_guardrails=[PIIBlockGuardrail()],
)
```

### "cost cap" / "token budget" / "expensive" / "control costs"

```python
from meshflow import Agent, CostCapGuardrail, ModelRouter, TokenBudgetPlanner

agent = Agent(
    name="cost-aware",
    role="researcher",
    model="claude-sonnet-4-6",
    output_guardrails=[CostCapGuardrail(max_usd=0.10)],
    model_router=ModelRouter(),  # auto-routes simple tasks to claude-haiku
)

# Pre-run cost estimate
plan = TokenBudgetPlanner.plan_budget(
    system="You are a researcher.",
    messages=[{"role": "user", "content": "task"}]
)
print(f"Estimated: ${plan['estimated_cost_usd']:.4f}")
```

### "guardrails" / "safe output" / "block PII" / "validate"

```python
from meshflow import (Agent, PIIBlockGuardrail, LengthGuardrail,
                       RegexGuardrail, ConfidenceGuardrail, ToxicityGuardrail)

agent = Agent(
    name="safe-agent",
    role="executor",
    model="claude-sonnet-4-6",
    input_guardrails=[
        PIIBlockGuardrail(),
        RegexGuardrail(r"(DROP TABLE|DELETE FROM)", mode="forbid"),
    ],
    output_guardrails=[
        LengthGuardrail(max_chars=2000),
        ConfidenceGuardrail(min_confidence=0.75),
        ToxicityGuardrail(),
    ],
)
```

### "state graph" / "LangGraph" / "workflow graph"

```python
from typing import TypedDict
from meshflow import StateGraph, END

class State(TypedDict):
    messages: list[str]
    result: str

def process(state: State) -> State:
    return {"result": f"processed: {state['messages'][-1]}"}

def route(state: State) -> str:
    return END if state["result"] else "process"

graph = (
    StateGraph(State)
    .add_node("process", process)
    .add_conditional_edges("process", route, {END: END, "process": "process"})
    .set_entry_point("process")
    .compile()
)
result = graph.invoke({"messages": ["hello"], "result": ""})
```

### "durable" / "resume" / "checkpoint" / "crash recovery"

```python
from meshflow import DurableWorkflowExecutor

# Backends: sqlite (default), redis, postgres, s3
exe = DurableWorkflowExecutor(
    run_id="my-run",
    backend="redis",
    redis_url="redis://localhost",
)
# Same run_id on re-run = resume from last checkpoint automatically
```

### "human in the loop" / "HITL" / "approval gate"

```python
from meshflow import StateGraph, interrupt, Command

def risky_step(state):
    decision = interrupt("Approve deleting 10K records?")
    if decision.approved:
        return {"deleted": True}
    return {"deleted": False}

# Resume after human approves via CLI or API:
# meshflow approve <run_id>
# or: graph.invoke(Command(resume={"approved": True}), config={"run_id": run_id})
```

### "streaming" / "stream tokens" / "real-time output"

```python
from meshflow import Agent

agent = Agent(name="streamer", role="executor", model="claude-sonnet-4-6")
async for chunk in agent.stream("Write a poem about AI governance"):
    if chunk.is_token:
        print(chunk.content, end="", flush=True)
    elif chunk.is_done:
        print(f"\n[cost: ${chunk.cost_usd:.4f}  tokens: {chunk.tokens}]")
```

### "eval" / "test agents" / "quality gate" / "regression"

```python
from meshflow import run_eval

result = await run_eval(agent, "evals.yaml")
print(f"{result.pass_rate:.0%} pass rate  ${result.total_cost_usd:.4f}")
```

```bash
meshflow eval run evals.yaml --save-baseline baseline.json
meshflow eval run evals.yaml --compare-baseline baseline.json --fail-on-regression
```

### "MCP" / "MCP server" / "tool server" / "connect tools"

```python
from meshflow import Agent

agent = Agent(
    name="mcp-agent",
    role="executor",
    model="claude-sonnet-4-6",
    mcps=["https://mcp.example.com/sse"],  # tools auto-discovered
)
```

### "wrap LangGraph" / "govern AutoGen" / "add governance to existing agent"

```python
from meshflow import govern, from_langgraph, from_crewai, from_autogen

governed = govern(your_existing_app)      # any framework
governed = from_langgraph(your_graph)     # LangGraph
governed = from_crewai(your_crew)         # CrewAI
governed = from_autogen(your_agent)       # AutoGen
```

### "serve" / "HTTP API" / "REST endpoint"

```bash
meshflow serve --host 0.0.0.0 --port 8000 --policy-file policies/prod.yaml
```

```python
from meshflow import MeshFlowClient
client = MeshFlowClient("http://localhost:8000", api_key="mf-...")
result = client.run_agent("researcher", "Summarize the latest papers")
```

### "sandbox" / "test mode" / "no API key" / "mock"

```python
# Zero real token spend, full trace, no API key required
wf = Workflow(mode="sandbox")
result = wf.run("test task")

# Or via environment:
import os; os.environ["MESHFLOW_MOCK"] = "1"
```

---

## Provider selection (auto-detected from model name)

```python
Agent(name="a", model="claude-sonnet-4-6")    # → Anthropic (default)
Agent(name="b", model="claude-haiku-4-5")     # → Anthropic (fast/cheap tier)
Agent(name="c", model="claude-opus-4-7")      # → Anthropic (max capability)
Agent(name="d", model="gpt-4o")               # → OpenAI
Agent(name="e", model="gemini-2.0-flash")     # → Google Gemini
Agent(name="f", model="llama3.2")             # → local Ollama (no key)
```

---

## Pre-built agents (skip from scratch)

```python
from meshflow import agents

researcher  = agents.ResearchAgent()
coder       = agents.CoderAgent()
critic      = agents.CriticAgent()
planner     = agents.PlannerAgent()
summarizer  = agents.SummarizerAgent()
guardian    = agents.GuardianAgent()
```

---

## Key CLI commands

```bash
meshflow run workflow.yaml          # run YAML workflow
meshflow serve                      # start HTTP API
meshflow eval run evals.yaml        # evaluations
meshflow replay <run_id>            # debug past run
meshflow replay <run_id> --diff <b> # compare two runs
meshflow doctor                     # pre-deploy check
meshflow keys generate              # create API keys
meshflow vault store <key>          # store a secret
meshflow snapshot export            # ZIP audit bundle
```

---

## What every Claude + MeshFlow run gets automatically

- SHA-256 tamper-evident audit ledger on every step
- Hard cost cap enforcement via `CostCap` — no runaway Claude API spend
- Compliance profile rules (HIPAA/SOX/GDPR/PCI/NERC) — one line to apply
- Durable execution (crash recovery) via SQLite/Redis/Postgres/S3 checkpoints
- PII blocking, toxicity, confidence, cost cap, JSON schema guardrails
- Per-agent, per-team, per-tenant token-bucket rate limits
- p50/p95/p99 latency recorded for every agent step
- HMAC-signed webhook events (HITL pending, policy violation, budget exceeded)
- OpenTelemetry spans pushed to Grafana, Jaeger, Datadog, or Arize Phoenix

Zero configuration. All governance is on by default.

---

## Install

```bash
pip install meshflow                 # core — Anthropic built in
pip install "meshflow[openai]"       # + OpenAI
pip install "meshflow[full]"         # all providers + RAG + OTEL
```

Requires Python 3.11+. `ANTHROPIC_API_KEY` is the only required secret for
Claude models; use `mode="sandbox"` or `MESHFLOW_MOCK=1` for zero-key local
development.
