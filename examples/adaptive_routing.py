"""Adaptive model routing — self-improving mixed-model pipeline.

Demonstrates Sprint 83 features:

1. ModelRegistry — register custom/proxy models once, eliminating ad-hoc
   pattern matching for model names the router wouldn't recognise.

2. TaskScorer — multi-dimensional composite score (length + complexity +
   task type + tool pressure) replaces raw character count.

3. AdaptiveModelTierRouter — routes by composite score, records outcomes,
   and auto-adapts thresholds every N runs based on CONFIDENCE markers.

4. explain() / stats() — inspect routing decisions and per-tier health.

Run offline:
    MESHFLOW_MOCK=1 python examples/adaptive_routing.py
"""
from __future__ import annotations

import os

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow import (
    DEFAULT_REGISTRY,
    AdaptiveModelTierRouter,
    ModelSpec,
    ModelTier,
    RouterOutcomeStore,
    TaskScorer,
    Workflow,
    Agent,
    CostCap,
    extract_confidence,
    score_task,
)


# ── 1. Register custom models ─────────────────────────────────────────────────

# A corporate Ollama fine-tune whose name auto-detection wouldn't recognise.
DEFAULT_REGISTRY.register(ModelSpec(
    model_id="corp-finance-llm",
    is_local=True,
    quality_estimate=0.78,
    latency_ms_estimate=180.0,
    tags=["finance", "local"],
))

# LiteLLM proxy forwarding to a local GPU cluster.
DEFAULT_REGISTRY.register(ModelSpec(
    model_id="http://gpu-cluster.internal/v1",
    is_local=True,
    quality_estimate=0.85,
    latency_ms_estimate=300.0,
    tags=["local", "proxy"],
))

print("── Registered models ──────────────────────────────────────────────────")
for spec in DEFAULT_REGISTRY.all():
    tag = "(local)" if spec.is_local else "(cloud)"
    print(f"  {spec.model_id:45s}  {tag}  quality={spec.quality_estimate:.2f}")


# ── 2. Multi-dimensional task scoring ────────────────────────────────────────

print("\n── Task scoring ───────────────────────────────────────────────────────")
tasks = [
    ("Hello, how are you?",                            []),
    ("Summarize this document briefly.",               []),
    ("def fibonacci(n): return n if n <= 1 else ...", ["code_runner"]),
    (
        "Analyse and compare the strategic implications of quantum computing "
        "versus classical AI. However, despite the uncertainty, evaluate trade-offs "
        "and recommend a path forward for a Fortune 500 CTO.",
        ["web_search", "data_retrieval"]
    ),
    ("x" * 1500 + " — write a comprehensive analysis",  ["rag", "db_query", "api_call"]),
]
for task, tools in tasks:
    s = score_task(task, tools)
    print(f"  [{s.task_type:9s}] composite={s.composite:.3f}  len={s.length:5d}  tools={s.tool_count}  "
          f"  {task[:55]!r}")


# ── 3. Build the adaptive router ──────────────────────────────────────────────

store = RouterOutcomeStore(path=":memory:")

router = AdaptiveModelTierRouter(
    tiers=[
        ModelTier("fast",  "corp-finance-llm",           max_tokens=512,  is_local=True),
        ModelTier("smart", "http://gpu-cluster.internal/v1", max_tokens=2048, is_local=True),
        ModelTier("large", "meta.llama3-70b-instruct-v1:0",  max_tokens=4096),
    ],
    smart_threshold=0.33,
    large_threshold=0.67,
    adapt_every=20,           # adapt thresholds after every 20 routes
    exploration_rate=0.10,    # 10% exploration, decays with experience
    store=store,
    adapt_mode="auto",
)


# ── 4. explain() — see the routing rationale before running ──────────────────

print("\n── Routing explanations ───────────────────────────────────────────────")
for task, tools in tasks[:3]:
    print(router.explain(task, tools))
    print()


# ── 5. Simulate 25 runs to accumulate outcomes ───────────────────────────────

print("── Simulating 25 routing runs ─────────────────────────────────────────")
for i, (task, tools) in enumerate((tasks * 5)[:25]):
    result = router.route(task, tools, run_id=f"sim-{i}")
    # Simulate: local models succeed with high confidence, cloud varies
    quality = 0.85 if result.is_local else (0.70 + (i % 3) * 0.08)
    router.record_outcome(
        f"sim-{i}",
        success=True,
        quality=quality,
        latency_ms=200.0 if result.is_local else 800.0,
        actual_cost_usd=0.0 if result.is_local else 0.012,
    )
print(f"  Completed 25 routes — store has {store.count()} outcomes")


# ── 6. stats() — per-tier health ─────────────────────────────────────────────

print("\n── Router stats ───────────────────────────────────────────────────────")
s = router.stats()
print(f"  Total runs : {s.total_runs}")
print(f"  Exploration: {s.exploration_rate_actual:.1%} of routes were exploratory")
for tier_name, ts in s.tiers.items():
    if ts.n > 0:
        print(f"  [{tier_name:6s}]  n={ts.n:3d}  success={ts.success_rate:.0%}  "
              f"avg_quality={ts.avg_quality:.2f}  avg_latency={ts.avg_latency_ms:.0f}ms")


# ── 7. Manual adapt — inspect recommendation ─────────────────────────────────

print("\n── Manual adaptation ──────────────────────────────────────────────────")
rec = router.adapt()
print(f"  Confidence   : {rec.confidence:.2f}")
print(f"  Summary      : {rec.summary}")
print(f"  smart_threshold → {router._smart:.3f}")
print(f"  large_threshold → {router._large:.3f}")


# ── 8. Workflow integration with estimate_cost ────────────────────────────────

print("\n── Workflow estimate_cost with adaptive router ─────────────────────────")
wf = Workflow(cost_cap=CostCap(usd=0.50))
wf.add(
    Agent("planner",    model_router=router),
    Agent("researcher", model_router=router),
    Agent("writer",     model_router=router),
)

short_task = "Summarize the quarterly results."
long_task  = (
    "Analyse the competitive landscape across quantum computing, classical AI, "
    "and neuromorphic chips for enterprise deployments. Compare regulatory risk, "
    "talent availability, and 5-year ROI. Produce a board-ready recommendation. " * 3
)

for label, task in [("Short", short_task), ("Long", long_task)]:
    est = wf.estimate_cost(task)
    cloud = est.cloud_agents
    print(f"\n  {label} task ({len(task)} chars):")
    print(est)
    if cloud:
        print(f"  ⚠  Cloud agents: {cloud}  — estimated cost: ${est.total_usd:.4f}")
    else:
        print(f"  ✓  All local — $0.00 guaranteed")


# ── 9. Offline sandbox run ────────────────────────────────────────────────────

print("\n── Sandbox run (MESHFLOW_MOCK=1) ──────────────────────────────────────")
result = wf.run(short_task)
print(f"  Status       : {result.status}")
print(f"  Agent costs  : {result.agent_costs}")
print(f"  Cloud agents : {result.cloud_agents}")
print(f"  Total cost   : ${result.total_cost_usd:.4f}")

print("\n  Done.\n")
