# Product Hunt Launch — MeshFlow v1.13.0

## Submission Metadata

- **Product Name**: MeshFlow
- **Tagline**: Open-source multi-agent orchestration with governance, evals, and durable workers built in
- **Category**: Developer Tools / AI
- **Website**: https://meshflow.dev
- **GitHub**: https://github.com/Anteneh-T-Tessema/meshflow

---

## Description (260 chars)

MeshFlow is a governed agent runtime for regulated industries — HIPAA/SOX/GDPR compliance, SHA-256 audit chain, cost caps, SOC 2 assertion, and StructuredJudge evals baked into the kernel. LangGraph/CrewAI/AutoGen/AutoGen 0.4+/OpenAI Agents SDK parity. 5,711 tests.

---

## Maker's First Comment

Hey Product Hunt 👋

I'm Anteneh, the maker of MeshFlow. Thanks for checking it out.

**The problem I kept hitting:** You build a multi-agent pipeline, hardcode `gpt-4o` everywhere because it's the safe choice, and then watch your API bill climb while 70% of your tasks could have been handled by a free local model.

**What v1.13.0 adds** (sprints 95–102):

```python
# 1. Functional API (@task / @entrypoint — LangGraph-style)
from meshflow import task, entrypoint

@task
async def analyse(text: str) -> str:
    return f"analysis of: {text}"

# 2. SpawnableAgent — specialised child agents spawned at runtime
from meshflow.agents.spawnable import SpawnableAgent, SpawnConfig, SpawnRule

agent = SpawnableAgent("orchestrator", spawn_config=SpawnConfig(rules=[
    SpawnRule("code",     keywords=["code", "function"], role="executor"),
    SpawnRule("research", keywords=["find", "analyse"],  role="researcher"),
], parallel=True))
result = agent.run("Write a Python function and analyse its complexity.")

# 3. StructuredJudge evals with CI regression gate
from meshflow.eval.judge_v2 import StructuredJudge, EvalCI, RubricCriterion

judge = StructuredJudge(criteria=[
    RubricCriterion("accuracy",  weight=0.6),
    RubricCriterion("conciseness", weight=0.4),
])
ci = EvalCI(judge=judge, baseline_pass_rate=0.85, raise_on_regression=True)

# 4. @traceable + LangfuseExporter
from meshflow.observability.traceable import traceable, LangfuseExporter
traceable.set_exporter(LangfuseExporter(public_key="pk-...", secret_key="sk-..."))

@traceable(run_type="chain")
async def my_pipeline(task: str) -> str:
    return await wf.run_async(task)

# 5. Durable Workers with SQLite persistence
from meshflow.workers import durable_task, WorkerDaemon, CronTrigger

@durable_task(max_retries=3)
async def process_report(report_id: str) -> str:
    return f"processed {report_id}"

daemon = WorkerDaemon()
daemon.register(process_report)
CronTrigger(cron="0 9 * * *").add(process_report, report_id="daily")
```

**Cost profile on a typical workload:**

| Tier | Tasks | Cost |
|---|---|---|
| fast (llama3.2, local) | ~70% | $0.00 |
| smart (mistral, local) | ~20% | $0.00 |
| large (gpt-4o, cloud) | ~10% | pay only these |

vs. always-gpt-4o: same quality, ~10× cheaper.

**New in v1.13.0:**
- `AdvisorAgent` / `AdvisorRouter` — advisor-tool pattern, adaptive use threshold
- `DynamicWorkflow` — runtime agent spawning from planner output
- `ContextCompactor` — Claude-native + sliding-window + summary strategies
- `ToolStreamEvent` hierarchy — granular tool call lifecycle streaming
- `BudgetConfig` — `ThinkingBudget` + `EffortBudget` enforced in kernel
- `meshflow-forensic` — standalone forensic audit + EU AI Act compliance pip package
- `SOC2Assertion` — programmatic SOC 2 Type II assertion; CI-ready
- `CostRegressionError` — CI gate raises when per-run cost exceeds baseline
- `competitive_bench.py` — MeshFlow vs LangGraph / CrewAI / AutoGen benchmarks
- AutoGen 0.4+ parity: `AssistantAgent`, `SocietyOfMind`, `MagenticOne`, `AgentRuntime`
- OpenAI Agents SDK parity: `Agent`, `Runner`, `handoff`, `FunctionTool`, `as_tool()`
- `SpawnableAgent` — runtime specialised child agent spawning
- `StructuredJudge` / `TrajectoryEval` / `RAGEval` / `EvalCI` regression gate
- `@traceable` + `LangfuseExporter` for distributed tracing
- `MCPRouter` multi-server MCP with per-server authorization
- `@durable_task` / `WorkerDaemon` / `CronTrigger` with SQLite job persistence

**The full stack:**
- SHA-256 tamper-evident audit chain on every agent step
- HIPAA / SOX / GDPR / PCI / NERC compliance profiles (one line to enable)
- Hard cost caps enforced in the kernel — `CostCap(usd=5.00)` can't be bypassed
- Self-improving `AdaptiveModelTierRouter` — llama3.2 → mistral → gpt-4o, pay only when needed
- Real-time SSE streaming: `chunks_to_sse(wf.astream(task))` for FastAPI in 4 lines
- `Workflow.batch_run(tasks, max_concurrency=4)` for parallel execution
- `Crew.from_yaml()` / `Crew.train()` / `Crew.replay()` (CrewAI full parity)
- A2A agent-to-agent protocol with `/.well-known/agent-card` discovery
- Code interpreter (sandboxed subprocess, module allow-list)
- Multi-modal: image, document, audio
- 5,711 tests, Python 3.11 + 3.12

**Install:**
```bash
pip install meshflow
```

**Try it offline (zero API keys):**
```bash
MESHFLOW_MOCK=1 python examples/sprint95_features_demo.py
```

Questions welcome — I read everything.

---

## Gallery Captions

**Image 1 — Cascade routing:**
```
Task: "Summarise Q3 results"
→ [fast] llama3.2    CONFIDENCE:0.91  $0.0000  ✓ done in 1 call

Task: "Analyse GDPR Article 17 implications"
→ [fast] llama3.2    CONFIDENCE:0.38  escalating...
→ [smart] mistral    CONFIDENCE:0.71  $0.0000  ✓ done in 2 calls

Task: "Draft a data processing agreement"
→ [fast]  llama3.2   CONFIDENCE:0.29  escalating...
→ [smart] mistral    CONFIDENCE:0.44  escalating...
→ [large] gpt-4o     CONFIDENCE:0.88  $0.021   ✓ done in 3 calls
```

**Image 2 — Self-adaptation after 200 routes:**
```
meshflow routing-report --db meshflow_routing.db

  smart_threshold : 0.33 → 0.28  ↓ (fast tier handles more)
  large_threshold : 0.67 → 0.71  ↑ (smart tier handles more)
  cost saved      : $2.40 vs always-gpt-4o  (80% reduction)
```

**Image 3 — Per-agent cost attribution:**
```python
result.agent_costs   # {"planner": 0.0, "researcher": 0.0, "writer": 0.021}
result.cloud_agents  # ["writer"]
result.total_cost_usd  # 0.021
```

**Image 4 — FastAPI SSE endpoint (4 lines):**
```python
@app.get("/stream")
async def stream(task: str):
    return StreamingResponse(
        chunks_to_sse(wf.astream(task)),
        media_type="text/event-stream",
    )
```

---

## Topics
- Developer Tools
- Artificial Intelligence
- Open Source
- Python
- LLM

## Links
- GitHub: https://github.com/Anteneh-T-Tessema/meshflow
- Docs: https://meshflow.dev/docs
- PyPI: https://pypi.org/project/meshflow/
- Docker: `docker pull ghcr.io/anteneh-t-tessema/meshflow-mcp:1.13.0`
- QUICKSTART.md: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md
