# Product Hunt Launch — MeshFlow v1.10.0

## Submission Metadata

- **Product Name**: MeshFlow
- **Tagline**: Multi-agent pipelines that start cheap and get smarter every run
- **Category**: Developer Tools / AI
- **Website**: https://meshflow.dev
- **GitHub**: https://github.com/Anteneh-T-Tessema/meshflow

---

## Description (260 chars)

MeshFlow routes each task to the cheapest model that can handle it — local llama3.2 for simple tasks, cloud GPT-4o only when confidence is low. Self-improving: thresholds auto-adapt from CONFIDENCE signals. HIPAA/SOX/GDPR built in. 5111 tests.

---

## Maker's First Comment

Hey Product Hunt 👋

I'm Anteneh, the maker of MeshFlow. Thanks for checking it out.

**The problem I kept hitting:** You build a multi-agent pipeline, hardcode `gpt-4o` everywhere because it's the safe choice, and then watch your API bill climb while 70% of your tasks could have been handled by a free local model.

**What v1.10.0 adds:** A cascade router that starts with the cheapest model and escalates automatically — but only when the agent itself isn't confident enough.

```python
from meshflow import (
    Workflow, Agent, CostCap,
    AdaptiveModelTierRouter, ModelTier, CascadeRouter,
)

router = AdaptiveModelTierRouter(
    tiers=[
        ModelTier("fast",  "llama3.2",   max_tokens=512),   # local, $0.00
        ModelTier("smart", "mistral:7b", max_tokens=2048),  # local, $0.00
        ModelTier("large", "gpt-4o",     max_tokens=4096),  # pay only here
    ],
    adapt_every=50,  # auto-adjust thresholds every 50 routes
)

cascade = CascadeRouter(router, escalation_threshold=0.65)

wf = Workflow(cost_cap=CostCap(usd=0.50))
wf.add(Agent("analyst", model_router=cascade, cascade_threshold=0.65))
```

**Cost profile on a typical workload:**

| Tier | Tasks | Cost |
|---|---|---|
| fast (llama3.2, local) | ~70% | $0.00 |
| smart (mistral, local) | ~20% | $0.00 |
| large (gpt-4o, cloud) | ~10% | pay only these |

vs. always-gpt-4o: same quality, ~10× cheaper.

**What "self-improving" means in practice:**
- Every 50 routes, bucket-analyze which task complexity scores are failing the cheap model
- Auto-shift thresholds — no manual tuning, no redeployment
- Thresholds persist: `router.save("state.json")` / `AdaptiveModelTierRouter.load("state.json")`

**Everything else MeshFlow does:**
- SHA-256 tamper-evident audit chain on every agent step
- HIPAA / SOX / GDPR / PCI / NERC compliance profiles (one line to enable)
- Hard cost caps enforced in the kernel — `CostCap(usd=5.00)` can't be bypassed
- Real-time SSE streaming: `chunks_to_sse(wf.astream(task))` for FastAPI in 4 lines
- `Workflow.batch_run(tasks, max_concurrency=4)` for parallel execution
- Go SDK with full parity: multimodal, batch, streaming channel filters
- 5111 tests, Python 3.11 + 3.12

**Install:**
```bash
pip install meshflow
```

**Try it offline (zero API keys):**
```bash
MESHFLOW_MOCK=1 python examples/cascade_routing.py
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
- Docker: `docker pull ghcr.io/anteneh-t-tessema/meshflow-mcp:1.10.0`
- QUICKSTART.md: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md
