"""Cascade routing — FrugalGPT pattern: cheap first, escalate only when needed.

Sprint 84: CascadeRouter wraps AdaptiveModelTierRouter and automatically
retries with the next tier when CONFIDENCE is below the threshold.

Cost profile for a typical workload:
  ~70% of tasks → fast tier (local, $0.00)
  ~20% of tasks → smart tier (local, $0.00)
  ~10% of tasks → large tier (cloud, pay only when needed)

Run offline:
    MESHFLOW_MOCK=1 python examples/cascade_routing.py
"""
from __future__ import annotations

import os
os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow import (
    AdaptiveModelTierRouter,
    Agent,
    CascadeRouter,
    ModelTier,
    RouterOutcomeStore,
    Workflow,
    extract_confidence,
)


# ── Build the cascade router ──────────────────────────────────────────────────

store = RouterOutcomeStore(path=":memory:")

base = AdaptiveModelTierRouter(
    tiers=[
        ModelTier("fast",  "llama3.2",                       max_tokens=512,  is_local=True),
        ModelTier("smart", "mistral:7b",                     max_tokens=2048, is_local=True),
        ModelTier("large", "meta.llama3-70b-instruct-v1:0",  max_tokens=4096),
    ],
    adapt_every=100,
    exploration_rate=0.05,
    store=store,
)

cascade = CascadeRouter(
    base,
    escalation_threshold=0.65,   # retry when CONFIDENCE < 0.65
    max_escalations=2,           # try up to 2 additional tiers
)


# ── How the cascade works ─────────────────────────────────────────────────────

print("── Cascade routing demo ──────────────────────────────────────────────────")
print(f"  escalation_threshold : {cascade.escalation_threshold}")
print(f"  max_escalations      : {cascade.max_escalations}")
print(f"  tiers                : {[t.name + '→' + t.model for t in cascade.tiers()]}")
print()


# ── Simulate routing + escalation without a live LLM ─────────────────────────

print("── Simulated cascade decisions ───────────────────────────────────────────")

scenarios = [
    ("Short summarisation — fast tier should handle it",    "Summarize this in one sentence.",          0.85),
    ("Medium analysis — might need smart tier",             "Compare three cloud providers on cost.",    0.55),
    ("Complex reasoning — probably needs large tier",       "Explain quantum entanglement to a CEO.",   0.35),
]

for label, task, simulated_confidence in scenarios:
    result = cascade.route(task, run_id=f"sim-{label[:10]}")
    tier = result.tier
    model = result.model
    escalations = 0

    # Simulate: if confidence < threshold, escalate
    conf = simulated_confidence
    while conf < cascade.escalation_threshold:
        escalated = cascade.escalate(f"sim-{label[:10]}")
        if escalated is None:
            break
        tier = escalated.tier
        model = escalated.model
        escalations += 1
        conf = simulated_confidence + escalations * 0.25   # improves with better model

    cascade.record_outcome(
        f"sim-{label[:10]}",
        success=True,
        quality=conf,
        latency_ms=200.0 + escalations * 300.0,
        actual_cost_usd=0.0 if result.is_local else 0.012 * escalations,
    )

    print(f"  [{tier:6s}] ({escalations} escalation{'s' if escalations != 1 else ' '})  {label}")
    print(f"           model={model}  final_confidence={conf:.2f}")

print()


# ── Workflow integration ──────────────────────────────────────────────────────

print("── Workflow with cascade router ──────────────────────────────────────────")
wf = Workflow()
wf.add(
    Agent(
        "analyst",
        model_router=cascade,
        cascade_threshold=0.65,   # matches cascade.escalation_threshold
    )
)

result = wf.run("Provide a brief summary of AI regulation trends.")
print(f"  Status      : {result.status}")
print(f"  Total cost  : ${result.total_cost_usd:.4f}")
print(f"  Total tokens: {result.total_tokens}")
print()


# ── Cost comparison: cascade vs always-large ──────────────────────────────────

print("── Cost savings estimate ─────────────────────────────────────────────────")
print("  Assuming 1000 tasks/day with the distribution above:")
print()

tasks_per_day = 1000
dist = {"fast": 0.70, "smart": 0.20, "large": 0.10}
cloud_cost_per_task = 0.012

cascade_cost = dist["large"] * tasks_per_day * cloud_cost_per_task
always_large_cost = tasks_per_day * cloud_cost_per_task

print(f"  Always-large model  : ${always_large_cost:.2f}/day")
print(f"  Cascade (10% cloud) : ${cascade_cost:.2f}/day")
print(f"  Savings             : ${always_large_cost - cascade_cost:.2f}/day ({(1 - cascade_cost/always_large_cost):.0%})")
print()
print("  Done.\n")
