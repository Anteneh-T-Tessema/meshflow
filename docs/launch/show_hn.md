# Show HN: MeshFlow — multi-agent pipelines that get cheaper every run

**HN title:** Show HN: MeshFlow – agents that start with the cheapest model and escalate only when they're not confident enough

---

Hi HN,

I built MeshFlow because every multi-agent framework I tried had the same problem: you hardcode `model="gpt-4o"` everywhere, pay cloud prices for every task, and have no idea which agent is responsible for which cost.

Today I'm launching v1.10.0, which adds a self-improving routing system. The core idea is simple:

**Start with the cheapest model. Escalate only when confidence is too low.**

```python
from meshflow import (
    Workflow, Agent,
    AdaptiveModelTierRouter, ModelTier,
    CascadeRouter,
)

router = AdaptiveModelTierRouter(
    tiers=[
        ModelTier("fast",  "llama3.2",   max_tokens=512),   # local, $0.00
        ModelTier("smart", "mistral:7b", max_tokens=2048),  # local, $0.00
        ModelTier("large", "gpt-4o",     max_tokens=4096),  # cloud, pay only when needed
    ],
    adapt_every=50,        # auto-adjust routing thresholds every 50 runs
    exploration_rate=0.10, # epsilon-greedy exploration to gather data
)

cascade = CascadeRouter(router, escalation_threshold=0.65, max_escalations=2)

wf = Workflow(cost_cap=CostCap(usd=0.50))
wf.add(Agent("analyst", model_router=cascade, cascade_threshold=0.65))

result = wf.run("Summarise the quarterly report")
# → llama3.2 answers CONFIDENCE:0.90 → done, $0.00
# → if CONFIDENCE:0.40 → retries with mistral → $0.00
# → if still low → retries with gpt-4o → pay only now
```

**What "self-improving" actually means:**

Agents already emit `CONFIDENCE:0.XX` markers on their last line (this existed before — I reused it). After each step:

1. The router records (task, model, tier, confidence, latency, cost)
2. Every 50 routes, a `ThresholdOptimizer` runs bucket analysis on recent outcomes
3. If llama3.2 is consistently scoring < 0.5 confidence for tasks scoring 0.3-0.4 on a 5-factor complexity scale, the router raises the threshold so those tasks route to mistral automatically
4. The thresholds survive process restarts — `router.save("state.json")` / `AdaptiveModelTierRouter.load("state.json")`

No training data required. No external ML service. Pure Python, stdlib only.

**The 5-factor task scorer** replaces raw character count with a composite 0-1 score:

```
composite = (
    0.35 × length_score            # chars / 2000
  + 0.20 × question_density        # ambiguity signal
  + 0.20 × conjunction_density     # nuance signal ("however", "whereas")
  + 0.15 × technical_term_density  # domain keywords
  + 0.10 × tool_pressure           # tool count
) × task_type_multiplier           # code=1.2, analysis=1.1, summary=0.85
```

A 150-char SQL query scores higher than a 500-char pleasantry.

**The rest of the stack** (unchanged, but worth knowing):

- SHA-256 tamper-evident audit chain on every step
- HIPAA / SOX / GDPR / PCI / NERC compliance profiles
- Hard cost caps: `CostCap(usd=5.00)` — never exceed, ever
- SQLite/Redis/Postgres/S3 durable checkpoints
- `Workflow.stream()` / `wf.astream()` / `chunks_to_sse()` for FastAPI SSE
- `Workflow.batch_run(tasks, max_concurrency=4)`
- Go SDK with full parity (multimodal, batch, streaming channel filters)
- 5111 tests, CI green on Python 3.11 + 3.12

**Cost profile on a typical workload:**

| Tier | % of tasks | Cost |
|------|-----------|------|
| fast (llama3.2) | ~70% | $0.00 |
| smart (mistral) | ~20% | $0.00 |
| large (gpt-4o) | ~10% | pay only these |

vs. always-gpt-4o: pay 10× more for the same quality.

---

**Try it:**

```bash
pip install meshflow
```

```python
# Offline demo (no API keys)
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import Workflow, Agent
wf = Workflow()
wf.add(Agent("demo"))
print(wf.run("Hello").output)
```

**Links:**
- GitHub: https://github.com/Anteneh-T-Tessema/meshflow
- Docs: https://meshflow.dev/docs
- QUICKSTART.md in the repo
- `examples/adaptive_routing.py` — full working example with the cascade router
- `examples/cascade_routing.py` — cost savings simulation

---

What I'd love feedback on:

1. **The CONFIDENCE marker approach** — agents emit `CONFIDENCE:0.XX` on their last line, and the router uses that as a quality signal. Does this feel too fragile? The alternative is an LLM-judge (extra call, extra cost).

2. **The composite scoring formula** — the 5 weights (0.35 / 0.20 / 0.20 / 0.15 / 0.10) were tuned manually. Has anyone done principled work on task complexity scoring for routing?

3. **Epsilon-greedy with annealing** — I chose this over UCB1 because it requires no prior distribution. The exploration rate decays as `ε × 200 / (n + 200)`. Is there a better schedule for cold-start?

Thanks for reading.
