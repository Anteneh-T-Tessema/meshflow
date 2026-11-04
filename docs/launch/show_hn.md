# Show HN: MeshFlow — production multi-agent orchestration for regulated industries

**HN title:** Show HN: MeshFlow – open-source agent orchestration with HIPAA/SOX/GDPR built in, 5,888 tests

---

Hi HN,

I've been building MeshFlow for the past year. It started as "I want a multi-agent framework that doesn't explode my API bill" and turned into something bigger: a governed agent runtime designed for the kinds of problems where audit trails, compliance profiles, and cost predictability actually matter.

**What it is:**

A Python framework for building multi-agent pipelines that run through a governed execution kernel on every step — cost caps, PII blocking, SHA-256 tamper-evident audit chain, and HIPAA/SOX/GDPR compliance profiles built in at the runtime level rather than bolted on after.

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"   # offline demo, no keys needed
from meshflow import Workflow, Agent, CostCap

wf = Workflow(cost_cap=CostCap(usd=0.50))
wf.add(Agent("researcher", role="researcher"))
wf.add(Agent("writer",     role="executor"))
result = wf.run("Write a short market analysis of the EV sector.")
print(result.output)
```

**What's different from LangGraph / CrewAI / AutoGen:**

1. **Governance is the kernel, not a plugin.** Every step goes through `StepRuntime` which enforces policy gates, budget tracking, and PII blocking before the LLM call returns. You can't bypass it.

2. **Self-improving cost routing.** `AdaptiveModelTierRouter` starts with the cheapest model (local llama3.2, $0.00) and escalates only when agent confidence is too low. Thresholds adapt automatically every 50 routes based on actual outcomes — no training data required.

3. **Framework parity, not lock-in.** The same governance kernel wraps LangGraph graphs, CrewAI Crews, and AutoGen conversations. You can migrate incrementally.

**v1.15.0 highlights (Guardrails, Merkle Ledger & Wasm Policies):**

- **Active Guardrail Firewalls** — pre-execution prompt and tool scanners automatically hook into LangGraph graph config, CrewAI agents, and AutoGen agents to block injections, PII leakage, and behavioral anomalies.
- **Merkle Tree Ledger** — pipelined group commits to SQLite or Postgres with background `asyncio.Task` queue flushing and O(log n) Merkle path verification per transaction step.
- **Collusion Detection v2** — Shannon entropy profiling with structural boilerplate masking, bigram perplexity anomaly checking, and role-aware sensitivity thresholds.
- **Wasm Policy Engine** — compiled OPA/Rego or Rust Wasm bytecode policy evaluation running on `wasmtime` or `wasmer` for absolute compliance rules parity across Python, TS, Go, Rust, and Java.
- **Developer Playground & Dashboard** — `meshflow playground` interactive REPL for quick agent trials, and a real-time WebSocket-powered monitoring dashboard for live tracing, guardrail violations, and token cost tracking.

**v1.14.0 highlights (Cloud Platform SDK Parity):**

- **PromptHub & DatasetHub** — dynamic versioned prompts and CI evaluation datasets hosted on the cloud.
- **Cross-SDK parity** — complete features across TypeScript, Go, Rust, Java, and Python SDKs.
- **Durable Workers** — `@durable_task`, `WorkerDaemon`, and `CronTrigger` with SQLite task queue persistence.
- **StructuredJudge & EvalCI** — rubric-based trajectory evaluation with CI build failure on cost or safety regression.
- **Adapters Parity** — full compatibility with AutoGen 0.4+, OpenAI Agents SDK, CrewAI, and LangGraph.
- **Functional API** — `@traceable` decorator and LangfuseExporter support.

**The rest of the stack:**

- 4-tier agent memory (Working → Episodic → BM25 semantic → Procedural)
- HITL human approval checkpoints with `interrupt()` / `Command`
- SQLite / Postgres / S3 durable checkpoint/resume
- `Workflow.stream()` / `wf.astream()` / SSE helpers for FastAPI
- `Crew.train()` / `Crew.replay()` / `Crew.from_yaml()` (CrewAI parity)
- `Pipeline` — chain multiple Crews with typed handoffs
- Agent-to-Agent (A2A) protocol with `/.well-known/agent-card` discovery
- Code interpreter (sandboxed subprocess, module allow-list)
- Multi-modal: image, document, audio inputs
- BaseStore / SQLiteStore cross-session shared memory (LangGraph parity)
- 5,888 tests, CI green on Python 3.11 + 3.12

**Cost profile on a typical workload with the cascade router:**

| Tier | % of tasks | Cost |
|------|-----------|------|
| fast (llama3.2, local) | ~70% | $0.00 |
| smart (mistral, local) | ~20% | $0.00 |
| large (gpt-4o, cloud) | ~10% | pay only these |

vs. always-gpt-4o: same quality, ~10× cheaper.

---

**Try it (offline, no API keys):**

```bash
pip install meshflow
```

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import Workflow, Agent
wf = Workflow()
wf.add(Agent("demo"))
print(wf.run("Hello, world!").output)
```

**Links:**
- GitHub: https://github.com/Anteneh-T-Tessema/meshflow
- Docs: https://meshflow.dev/docs
- QUICKSTART.md in the repo
- `examples/` — 30+ working examples

---

What I'd love feedback on:

1. **SpawnableAgent pattern** — spawning specialized child agents based on keyword matching feels powerful but also like it could get unruly fast. Has anyone found a principled way to design spawn rule sets that don't explode in production?

2. **EvalCI regression gate** — the `EvalCI` class raises `EvalRegressionError` in CI when pass-rate drops below the baseline. But what's the right baseline drift threshold? 2%? 5%? We're defaulting to 5% but it feels arbitrary.

3. **The governance-as-kernel trade-off** — making StepRuntime mandatory means every step has overhead (hash chain, policy check, budget tick). On benchmarks this is ~2.4ms per step (bare p50=0.05ms → governed p50=2.39ms). Is that acceptable for your use case, or is it a dealbreaker?

Thanks for reading.
