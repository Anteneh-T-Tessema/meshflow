# Dev.to Post

**Title:** 7 lines to a production-safe multi-agent AI workflow — what we built and why
**Tags:** python, ai, opensource, webdev
**Cover image:** The 7-line code snippet on dark background

---

## Post

```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=5.00))
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('Write a competitive analysis of our market')

# Compliant. Durable. Audited. Cost-capped. Done.
```

That's it. Seven lines. The workflow has a SHA-256 tamper-evident audit chain, a hard cost cap that stops before overage (not after), HIPAA/SOX/GDPR compliance enforcement, and crash recovery across SQLite/Redis/Postgres/S3.

None of that required configuration. It's infrastructure — always on, built in.

```bash
pip install meshflow
```

---

## The problem we're solving

79% of enterprises have adopted AI agents. Only 11% run them in production.

The gap exists because every framework is optimized for demos. When you try to ship agents to a regulated environment, you hit the same four walls every time:

**Wall 1: Security**
The `python_repl` runs in the host process. If an agent writes and executes code, it has full filesystem and network access. [CVE-2025-59528](https://nvd.nist.gov/vuln/detail/CVE-2025-59528) — CVSS 10.0 RCE in a production-deployed agent framework — is what happens when you skip this.

MeshFlow's `python_repl` runs in a memory-capped (256 MB), network-blocked subprocess:
```python
agent = Agent(name="coder", role="executor", tools=["python_repl"])
# Automatically: resource.RLIMIT_AS, no network, 5s timeout
```

**Wall 2: Cost**
There's no native cost cap in LangGraph or CrewAI. Agents run until the model decides to stop. If the model hallucinates a termination condition, it loops. We've talked to teams who burned $15K in a weekend.

```python
wf = Workflow(cost_cap=CostCap(usd=5.00))  # stops BEFORE hitting $5, not after
```

**Wall 3: Compliance**
Under HIPAA §164.312(b) and SOC 2 CC7.2, you need to prove what your system did and that the logs weren't tampered with. "We have logs" fails this bar. A cryptographic hash chain doesn't.

```python
# Every step: prev_hash + entry_hash (SHA-256)
await ledger.verify_chain("run-abc123")  # breaks if any entry modified
```

**Wall 4: Durability**
A five-minute workflow that crashes at step 4 starts over at step 1. For workflows that call external APIs or write to databases, starting over isn't just slow — it's a correctness problem.

```python
exe = DurableWorkflowExecutor(run_id="my-run", backend="redis")
# Same run_id on restart = resume from last checkpoint
```

---

## What's in the box

MeshFlow's core is the `StepRuntime` — a 15-step governed execution kernel that wraps every single agent step:

1. Identity verification
2. Tenant scoping
3. Rate limiting
4. **Budget check** (hard cost cap)
5. **Policy evaluation** (YAML policy-as-code, DENY wins)
6. **Compliance profile** (HIPAA/SOX/GDPR/PCI/NERC)
7. **Input guardrails** (PII, injection, keywords)
8. Sensitive data scan
9. Risk classification
10. Taint propagation
11. Tool permission check
12. **LLM call** (the actual work)
13. **Output guardrails** (length, toxicity, JSON schema)
14. **Audit ledger write** (SHA-256 hash chain)
15. SLA record

Steps 1–11 before your LLM call. Steps 13–15 after. Zero configuration.

---

## Framework compatibility

MeshFlow doesn't replace your existing setup — it wraps it:

```python
from meshflow import govern, from_langgraph, from_crewai, from_autogen

# One line — adds governance to any existing app
governed = govern(your_existing_langgraph_graph)
governed = from_crewai(your_crew)
governed = from_autogen(your_agent)
```

It also has its own full implementations of LangGraph-compatible `StateGraph`, CrewAI-compatible `Crew`/`Task`/`Process`, and AutoGen-compatible `GroupChat` — so you can start fresh or migrate incrementally.

---

## Token cost optimization

This is the part most teams are missing: multi-agent systems waste 40–60% of their token budget on solvable problems.

MeshFlow ships a complete token optimization layer:

```python
from meshflow import Agent, ModelRouter, SlidingWindowPruner

agent = Agent(
    name="smart-agent",
    role="researcher",
    model_router=ModelRouter(),   # routes cheap tasks to nano models automatically
    context_pruner=SlidingWindowPruner(max_messages=20),  # keeps context lean
)
```

Plus automatic `cache_control` on every system prompt (Anthropic: 10% of normal price on cached tokens). Combined, these typically reduce LLM spend 70–85% in production.

---

## Sandbox mode (no API key needed)

```python
# Full run, full trace, zero real tokens
wf = Workflow(mode="sandbox")
result = wf.run("test task")

# Or via environment
import os; os.environ["MESHFLOW_MOCK"] = "1"
```

Try it right now:

```bash
python -c "
import os; os.environ['MESHFLOW_MOCK'] = '1'
from meshflow import Workflow, CostCap, Agent
wf = Workflow(cost_cap=CostCap(usd=5.00), mode='sandbox')
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('What makes production AI different from prototype AI?')
print(result.output)
print(result.summary())
"
```

---

## The full stack

One `pip install` gets you:

- **Agents** — `Agent`, `Team`, `Supervisor`, `AdversarialTeam`, `ReActAgent`, `CriticAgent`, `AgentSession`
- **Orchestration** — `StateGraph` (LangGraph-compatible), `Crew`/`Task` (CrewAI-compatible), `Flow` (event-driven), `DurableWorkflowExecutor`
- **Governance** — `StepRuntime`, `ReplayLedger`, compliance profiles, policy-as-code engine, secret vault, tenant isolation, SLA tracking
- **Security** — `PIIBlockGuardrail`, `PromptInjectionGuardrail`, `SecretScanGuardrail`, `DascGate`, subprocess sandbox
- **Memory** — 4-tier `AgentMemory`, `VectorStore`, `AgentKnowledge`, `HybridRetriever`, `SelfCorrectingRAG`
- **Eval** — `EvalSuite`, `LLMJudge`, `ABTest`, `QualityGate`, cost regression CI gate
- **Observability** — OTEL span export, `MetricsCollector`, `EventProjector`, visual trace studio
- **Protocols** — A2A (Agent-to-Agent), MCP server/client, TypeScript client SDK, Go SDK
- **Deployment** — `meshflow serve` (FastAPI + SSE + WebSocket), Helm chart, k8s probes, `Doctor`

4,379 tests. Apache 2.0. No platform tax.

---

## Install

```bash
pip install meshflow                    # core
pip install "meshflow[openai]"          # + OpenAI
pip install "meshflow[full]"            # everything
```

- **GitHub:** https://github.com/Anteneh-T-Tessema/meshflow
- **Docs:** https://meshflow.dev
- **PyPI:** https://pypi.org/project/meshflow/

What's blocking you from shipping agents to production? I'd genuinely like to know — it directly shapes the roadmap.
