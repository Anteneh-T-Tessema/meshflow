# AdvisorAgent

`AdvisorAgent` implements the Anthropic advisor-tool pattern — a high-intelligence advisor model inspects a task's complexity, optionally generates structured guidance, and a cost-efficient executor carries out the work with that guidance in its context.

---

## Quick start

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import AdvisorAgent, AdvisorConfig

agent = AdvisorAgent(
    name="writer",
    config=AdvisorConfig(
        advisor_model="claude-opus-4-8",
        executor_model="claude-sonnet-4-6",
        complexity_threshold=0.5,   # invoke advisor for tasks above this complexity score
        guidance_format="json",      # "text" | "json"
    ),
)
result = agent.run("Draft a HIPAA data processing agreement.")
print(result.output)
print(f"Advisor used: {result.advisor_used}")
print(f"Advisor guidance: {result.advisor_guidance.raw[:80]}")
print(f"Cost savings vs full Opus: ${result.cost_savings_vs_full_opus:.4f}")
```

---

## How it works

1. `AdvisorAgent` scores the incoming task's complexity (0–1) using a 5-factor scorer.
2. If `complexity ≥ complexity_threshold`: calls the **advisor model** for structured guidance.
3. The advisor's guidance is injected into the **executor model's** context as a `[Advisor guidance]` block.
4. The executor model carries out the task (with or without guidance).
5. Returns an `AdvisorResult` with the final output and advisor metadata.

The advisor is read-only — it cannot call tools or produce side effects.

---

## AdvisorConfig

| Field | Type | Default | Description |
|---|---|---|---|
| `advisor_model` | `str` | `"claude-opus-4-8"` | High-intelligence model for advice |
| `executor_model` | `str` | `"claude-sonnet-4-6"` | Cost-efficient model for execution |
| `complexity_threshold` | `float` | `0.5` | Tasks above this score trigger the advisor (0–1) |
| `max_advisor_tokens` | `int` | `512` | Token budget for the advisor call |
| `include_guidance_in_executor` | `bool` | `True` | Prepend `[Advisor guidance]` block to executor context |
| `guidance_format` | `str` | `"text"` | `"text"` or `"json"` (structured `approach`/`pitfalls`/`checklist`) |

---

## AdvisorGuidance (output)

`AdvisorGuidance` is returned inside `AdvisorResult` — it is **not** an input parameter.

| Field | Type | Description |
|---|---|---|
| `raw` | `str` | Raw text from the advisor call |
| `approach` | `str` | Recommended approach (populated when `guidance_format="json"`) |
| `pitfalls` | `list[str]` | Pitfalls to avoid |
| `checklist` | `list[str]` | Step-by-step checklist |
| `skipped` | `bool` | `True` when task complexity was below threshold |
| `advisor_cost_usd` | `float` | Cost of the advisor call |

---

## AdvisorResult

| Field | Type | Description |
|---|---|---|
| `output` | `str` | Final executor output |
| `advisor_guidance` | `AdvisorGuidance` | Guidance produced by the advisor |
| `advisor_used` | `bool` | Whether the advisor was invoked |
| `total_cost_usd` | `float` | Combined cost (advisor + executor) |
| `cost_savings_vs_full_opus` | `float` | Estimated USD saved vs running the full task on Opus |

---

## AdvisorRouter

`AdvisorRouter` can be passed as `model_router=` to a standard `Agent`. It routes each task to the advisor path when complexity is high enough:

```python
from meshflow import AdvisorRouter, Agent

router = AdvisorRouter(
    advisor_model="claude-opus-4-8",
    executor_model="claude-sonnet-4-6",
    complexity_threshold=0.5,
)
decision = router.route("What is the EU AI Act high-risk classification for HR software?")
print(f"use_advisor={decision.use_advisor}  tier={decision.tier}  complexity={decision.complexity:.2f}")

router.record_outcome(routing_id="r001", helpful=True)
print(router.report())  # {"total_routes": 1, "advisor_used": 1, ...}
```

---

## Exports

```python
from meshflow import AdvisorAgent, AdvisorConfig, AdvisorGuidance, AdvisorResult, AdvisorRouter
```
