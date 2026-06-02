# Reddit Launch Posts

Four subreddits, four angles. Post in this order: r/MachineLearning first (most technical), then the others within the same hour.

---

## r/MachineLearning

**Title:** MeshFlow: production-safe multi-agent orchestration — SHA-256 audit chain, HIPAA/SOX/GDPR built in, 70-85% token cost reduction [Open Source]

---

79% of enterprises have adopted AI agents. Only 11% run them in production.

We've spent the past year building agent systems for banks, clinical operations teams, and engineering orgs. The problem isn't that agents don't work — they work fine. The problem is that every framework leaves compliance, cost governance, and crash recovery as exercises for the team. After the framework fails them in production.

We built MeshFlow to close that gap.

**The core idea:** treat governance as infrastructure, not middleware. Every agent step passes through a 15-step kernel that handles identity, rate limiting, budget enforcement, compliance profiles, input/output guardrails, PII detection, risk classification, tool permission, the LLM call itself, audit ledger write, and SLA recording — in that order, always, without configuration.

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

**What's technically interesting:**

**Token optimization layer** — five compounding mechanisms that reduce LLM spend 70-85%:
- `cache_control` on every system prompt and tool definition (Anthropic: 10% of normal price on cached tokens)
- `ModelRouter`: task-type classification routes simple tasks to nano models (keyword + token-count heuristic, zero LLM call)
- `ContextCompactor`: sliding window summarization activates at configurable token threshold
- `RAGTokenBudget`: hard `max_chars` cap on knowledge injection with truncate/drop/tail strategies
- `ContextDeduplicator`: shared context sent once for N parallel agents, not N times

**SHA-256 audit chain** — each step record stores `prev_hash` (SHA-256 of the previous record) and `entry_hash` (SHA-256 of its own canonical fields). Modify any log entry and `verify_chain()` breaks. This is the artifact HIPAA §164.312(b) and SOC 2 CC7.2 actually want.

**Durable execution** — `DurableWorkflowExecutor` with five backends (memory, SQLite, Redis, Postgres, S3). Same `run_id` on restart resumes from last checkpoint. For workflows with side effects, this is a correctness requirement, not just a convenience.

**ReplayLedger interactive API** — `diff(run_a, run_b)` returns a structured `RunDiff` (changed nodes, cost delta, token delta). `fork(run_id, from_step=3)` creates a new run copying steps 0–2. `load_state(run_id, step_index)` for time-travel inspection. The ledger is append-only by design.

**Framework-agnostic** — `govern(your_langgraph_graph)`, `from_crewai(your_crew)`, `from_autogen(your_agent)` add governance to any existing system without rewriting it.

4,616 passing tests. Apache 2.0. `pip install meshflow`.

GitHub: https://github.com/Anteneh-T-Tessema/meshflow
Docs: https://meshflow.dev

Happy to answer technical questions about the architecture.

---

## r/LangChain

**Title:** Built MeshFlow on top of LangGraph-compatible StateGraph — adds HIPAA/SOX compliance, cost caps, and audit chain without changing your graph code

---

If you're running LangGraph in production, you've probably hit the same gaps we did:

- No native cost cap (runaway loops are a real risk)
- No compliance layer for regulated industries
- No tamper-evident audit trail
- LangSmith is great for debugging, but it's a separate paid platform

We built MeshFlow to be the governance layer that wraps any LangGraph-compatible workflow. You don't have to rewrite your graphs:

```python
from meshflow import govern

# Your existing LangGraph graph
governed = govern(your_langgraph_graph, policy=compliance_profile("hipaa"))
result = await governed.run({"messages": [], "task": "summarize"})
```

Or use MeshFlow's native `StateGraph` (LangGraph-compatible API):

```python
from meshflow import StateGraph, END, interrupt, Command
from typing import TypedDict

class State(TypedDict):
    messages: list[str]
    approved: bool

def review_step(state: State) -> State:
    decision = interrupt("Approve sending this email?")  # HITL
    return {"approved": decision.approved}

graph = (
    StateGraph(State)
    .add_node("review", review_step)
    .add_edge("review", END)
    .set_entry_point("review")
    .compile()
)
```

**What you get that LangGraph doesn't provide:**

- SHA-256 tamper-evident audit chain on every step
- HIPAA/SOX/GDPR compliance profiles (one line: `compliance_profile("hipaa")`)
- Hard cost cap: `CostCap(usd=5.00)` — stops before overage, not after
- `ReplayLedger.diff(run_a, run_b)` — structured state diff between any two runs
- `ReplayLedger.fork(run_id, from_step=3)` — branch from any checkpoint
- 70-85% token cost reduction via prompt caching + ModelRouter
- No LangSmith required — full observability built in, self-hosted

```bash
pip install meshflow
```

GitHub: https://github.com/Anteneh-T-Tessema/meshflow

---

## r/LocalLLaMA

**Title:** MeshFlow: run governed multi-agent workflows on any local model — Ollama, LiteLLM, Bedrock — with cost caps and audit trails [Open Source]

---

Built a framework that works with any model you're running locally or self-hosted. No vendor lock-in, no required API keys.

```python
from meshflow import Agent, Workflow, CostCap

# Local Ollama — no API key
researcher = Agent(name="researcher", model="llama3.2")
analyst    = Agent(name="analyst",    model="mistral")
writer     = Agent(name="writer",     model="llama3.1:70b")

wf = Workflow(cost_cap=CostCap(usd=0.00))  # local = zero cost
wf.add(researcher, analyst, writer)
result = wf.run("Write a technical analysis of transformer attention mechanisms")
```

Works with:
- **Ollama** — `model="llama3.2"`, `model="mistral"`, `model="codellama"`, etc.
- **LiteLLM** — `model="groq/llama-3.1-70b"`, any of 100+ models
- **AWS Bedrock** — `model="anthropic.claude-v2"`
- **Azure OpenAI** — your own endpoint
- **Any OpenAI-compatible endpoint** — `OpenAICompatibleProvider(base_url="...")`

Full sandbox mode for local testing — full run, full trace, zero model calls:

```python
wf = Workflow(mode="sandbox")
result = wf.run("test this workflow")
# Full audit trace, zero model calls, zero cost
```

The governance layer (audit chain, cost caps, compliance profiles, guardrails) works identically regardless of which model you use. HIPAA compliance enforcement doesn't care if you're running Llama or Claude.

```bash
pip install meshflow
pip install "meshflow[full]"  # adds Bedrock, Gemini, LiteLLM
```

GitHub: https://github.com/Anteneh-T-Tessema/meshflow
Docs: https://meshflow.dev

---

## r/Python

**Title:** Show r/Python: MeshFlow — 7 lines to a production-safe multi-agent AI workflow [pip install meshflow]

---

Hi r/Python,

Sharing something we've been building: MeshFlow, a production-safe multi-agent orchestration framework.

```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=5.00))
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('Write a competitive analysis of our market')
print(result.output)
print(result.summary())  # ✅ completed  steps=3  cost=$0.0023  tokens=1847
```

```bash
pip install meshflow
```

**Try it with no API key (sandbox mode):**

```bash
python -c "
import os; os.environ['MESHFLOW_MOCK'] = '1'
from meshflow import Workflow, CostCap, Agent
wf = Workflow(cost_cap=CostCap(usd=5.00), mode='sandbox')
wf.add(Agent('researcher'), Agent('analyst'))
print(wf.run('What makes Python great?').summary())
"
```

**What makes it different from LangChain/CrewAI:**

Every step automatically gets:
- A SHA-256 tamper-evident audit chain (compliance artifact for HIPAA/SOC2)
- Hard cost cap enforcement before overage, not after
- HIPAA/SOX/GDPR compliance profiles via `compliance_profile("hipaa")`
- Crash recovery via durable checkpoints (SQLite/Redis/Postgres/S3)
- 70-85% token cost reduction via prompt caching + smart model routing

The Pythonic API tries to be as clean as possible:

```python
# Agents — declarative dataclass
from meshflow import Agent, tool, RiskTier

@tool(name="search", risk=RiskTier.EXTERNAL_IO)
async def search(query: str) -> str:
    return results

agent = Agent(
    name="researcher",
    role="researcher",
    model="claude-sonnet-4-6",  # or "gpt-4o", "llama3.2" — auto-detected
    tools=[search],
    memory=True,
    input_guardrails=[PIIBlockGuardrail()],
)

# Streaming
async for chunk in agent.stream("Research AI safety"):
    if chunk.is_token:
        print(chunk.content, end="", flush=True)

# Typed output (Pydantic)
class Report(BaseModel):
    title: str
    findings: list[str]
    confidence: float

report: Report = await agent.run_typed("Analyze this data", Report)
```

4,616 passing tests across Python 3.11/3.12/3.13. Apache 2.0. 

GitHub: https://github.com/Anteneh-T-Tessema/meshflow
PyPI: https://pypi.org/project/meshflow/
Docs: https://meshflow.dev
