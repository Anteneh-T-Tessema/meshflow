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
  'resumable workflow', 'agent governance', 'prompt caching', 'MCP server',
  'zero trust', 'ZT', 'spotlighting', 'JIT privilege', 'AI-BOM',
  'agent identity', 'prompt injection prevention'.
slash_command: /meshflow
context: fork
---

# MeshFlow — Production-Safe Multi-Agent Orchestration

MeshFlow is the infrastructure layer for production agent deployments.
Compliant, cost-governed, and durable — out of the box, not bolted on.

**The 7-line promise:**

```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=5.00))
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('Write a competitive analysis of our market')

# Compliant. Durable. Audited. Cost-capped. Done.
```

---

## Patterns — match these to what the user asks

### "build an agent" / "create an agent"

```python
from meshflow import Agent, tool, RiskTier

@tool(name="web_search", risk=RiskTier.EXTERNAL_IO)
async def web_search(query: str) -> str:
    return results  # real implementation

agent = Agent(
    name="researcher",
    role="researcher",
    model="claude-sonnet-4-6",   # auto-inferred from model name
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

planner  = Agent(name="planner",  role="planner")
coder    = Agent(name="coder",    role="executor")
reviewer = Agent(name="reviewer", role="critic")

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
    policy=compliance_profile("hipaa"),  # or "sox", "gdpr", "pci", "nerc"
    input_guardrails=[PIIBlockGuardrail()],
    output_guardrails=[PIIBlockGuardrail()],
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

### "CrewAI" / "crew" / "task-based agents"

```python
from meshflow import Agent, Task, Crew, Process

researcher = Agent(name="researcher", role="researcher")
writer     = Agent(name="writer",     role="executor")

task1 = Task(description="Research AI safety in 2026", agent=researcher)
task2 = Task(description="Write a 500-word summary", agent=writer,
             context=[task1])

crew = Crew(agents=[researcher, writer], tasks=[task1, task2],
            process=Process.sequential)
result = crew.kickoff()
print(result.raw)
```

### "durable" / "resume" / "checkpoint" / "crash recovery"

```python
from meshflow import DurableWorkflowExecutor

# Backends: sqlite (default), redis, postgres, s3
exe = DurableWorkflowExecutor(run_id="my-run", backend="redis",
                               redis_url="redis://localhost")
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

### "cost cap" / "token budget" / "expensive" / "control costs"

```python
from meshflow import Agent, CostCapGuardrail, ModelRouter

agent = Agent(
    name="cost-aware",
    role="researcher",
    output_guardrails=[CostCapGuardrail(max_usd=0.10)],
    model_router=ModelRouter(),  # auto-routes simple tasks to cheaper models
)

# Pre-run cost estimate
from meshflow import TokenBudgetPlanner
plan = TokenBudgetPlanner.plan_budget(system="You are a researcher.",
                                       messages=[{"role": "user", "content": "task"}])
print(f"Estimated: ${plan['estimated_cost_usd']:.4f}")
```

### "guardrails" / "safe output" / "block PII" / "validate"

```python
from meshflow import (Agent, PIIBlockGuardrail, LengthGuardrail,
                       RegexGuardrail, ConfidenceGuardrail, ToxicityGuardrail)

agent = Agent(
    name="safe-agent",
    role="executor",
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

### "zero trust" / "ZT" / "secure agents" / "spotlighting" / "JIT privilege" / "AI-BOM" / "agent identity" / "prompt injection prevention"

Foundation Zero Trust tier is **on by default** on every `Mesh.run()` and `Workflow.run()` call — no configuration needed. Upgrade to Enterprise or Advanced in one line.

**ZeroTrustOrchestrator — select a tier explicitly:**

```python
from meshflow import Mesh
from meshflow.zero_trust import ZeroTrustOrchestrator, ZeroTrustTier

mesh = Mesh()
zt   = ZeroTrustOrchestrator.for_tier(ZeroTrustTier.ENTERPRISE)
result = await zt.run(mesh, "Analyse Q2 revenue and flag anomalies")
```

**SpotlightingGuardrail — defend against prompt injection in untrusted content:**

```python
from meshflow import Agent
from meshflow.zero_trust import SpotlightingGuardrail

agent = Agent(
    name="doc-processor",
    role="executor",
    input_guardrails=[SpotlightingGuardrail()],  # wraps external content in XML tags
)                                                 # so injected instructions can't escape context
```

**JITPrivilegeManager — grant the minimum privilege needed, revoke immediately after:**

```python
from meshflow.zero_trust import JITPrivilegeManager

jit = JITPrivilegeManager()

async with jit.grant("agent-id-123", permissions=["db:read", "file:write"],
                      reason="invoice reconciliation", ttl_seconds=30) as token:
    result = await agent.run("Reconcile invoice 4821", auth_token=token)
# permissions auto-revoked after the block exits or TTL expires
```

**Audit a deployment against a ZT tier:**

```bash
meshflow zt-audit --tier enterprise
```

### "streaming" / "stream tokens" / "real-time output"

```python
from meshflow import Agent

agent = Agent(name="streamer", role="executor")
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
    mcps=["https://mcp.example.com/sse"],  # tools auto-discovered
)
```

### "wrap LangGraph" / "govern AutoGen" / "add governance"

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
Agent(name="a", model="claude-sonnet-4-6")   # → Anthropic
Agent(name="b", model="gpt-4o")              # → OpenAI
Agent(name="c", model="gemini-2.0-flash")    # → Google Gemini
Agent(name="d", model="llama3.2")            # → local Ollama (no key)
Agent(name="e", model="groq/llama-3.1-70b") # → LiteLLM

from meshflow import LLM
agent = Agent(name="f", llm=LLM("gpt-4o", api_key="sk-..."))
```

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

## What every run gets automatically

- ✅ SHA-256 tamper-evident audit ledger
- ✅ Cost cap enforcement via `CostCap`
- ✅ Compliance profile rules (HIPAA/SOX/GDPR/PCI/NERC)
- ✅ SLA latency recording
- ✅ Rate limiting per agent + tenant
- ✅ Webhook events on policy violations / HITL

Zero configuration. All governance is on by default.

## Install

```bash
pip install meshflow                 # core
pip install "meshflow[openai]"       # + OpenAI
pip install "meshflow[full]"         # all providers + RAG + OTEL
```
