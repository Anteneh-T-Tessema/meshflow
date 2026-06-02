"""Mixed-model pipeline — local fast agents + cloud 70B only when needed.

Shows the Sprint 82 pattern:
  1. estimate_cost() before spending any money
  2. ModelTierRouter routes each task to the right model tier
  3. CostCap guards against runaway cloud spend
  4. result.agent_costs / result.cloud_agents show exactly who spent what

Run offline (no API keys required):
    MESHFLOW_MOCK=1 python examples/mixed_model_pipeline.py
"""
from __future__ import annotations

import os

from meshflow import (
    Agent,
    CostCap,
    CostEstimate,
    ModelTier,
    ModelTierRouter,
    Workflow,
    model_is_local,
)


def build_router() -> ModelTierRouter:
    return ModelTierRouter(
        tiers=[
            ModelTier("fast",  "llama3.2",                       max_tokens=512),
            ModelTier("smart", "mistral:7b",                     max_tokens=2048),
            ModelTier("large", "meta.llama3-70b-instruct-v1:0",  max_tokens=4096),
        ],
        smart_threshold=300,   # chars → bump to mistral
        large_threshold=800,   # chars → bump to Bedrock 70B
    )


def show_estimate(wf: Workflow, task: str) -> CostEstimate:
    est = wf.estimate_cost(task)
    print("\n── Cost estimate ──────────────────────────────────────────")
    print(est)
    if est.cloud_agents:
        print(f"\n  ⚠  Cloud agents: {', '.join(est.cloud_agents)}")
        print(f"     Estimated total: ${est.total_usd:.4f}")
    else:
        print("\n  ✓  All agents are local — $0.00 guaranteed")
    print()
    return est


def main() -> None:
    router = build_router()

    # ── Pipeline definition ───────────────────────────────────────────────────
    wf = Workflow(cost_cap=CostCap(usd=0.50))
    wf.add(
        Agent("planner",    model_router=router),   # llama3.2  for short tasks
        Agent("researcher", model_router=router),   # mistral   if task is meaty
        Agent("writer",     model_router=router),   # 70B only  for long/complex
    )

    # ── Short task: stays fully local ─────────────────────────────────────────
    short_task = "Summarise the key benefits of multi-agent governance."
    print(f"Task: {short_task!r}")
    est = show_estimate(wf, short_task)
    assert est.total_usd == 0.0, "Short task should be $0 (all local)"

    # ── Long task: bumps to cloud for writer ──────────────────────────────────
    long_task = (
        "Analyse the competitive landscape for enterprise multi-agent orchestration "
        "frameworks including LangGraph, CrewAI, AutoGen, and MeshFlow. Evaluate "
        "each on governance, compliance, cost attribution, and production readiness. "
        "Produce a structured report with a recommendation for a Fortune 500 CTO. "
        "Include risk factors, migration costs, and integration complexity. " * 3
    )
    print(f"Task (first 80 chars): {long_task[:80]!r}")
    est_long = show_estimate(wf, long_task)
    # writer → 70B Bedrock → cloud
    if est_long.cloud_agents:
        print(f"  Will use cloud for: {est_long.cloud_agents}")
    else:
        print("  All local even for long task (routing thresholds not hit in mock mode)")

    # ── Preset: fully local (Ollama-only) ────────────────────────────────────
    print("── Local-only preset ──────────────────────────────────────────────────")
    local_router = ModelTierRouter(tiers=ModelTierRouter.PRESET_LOCAL)
    wf_local = Workflow()
    wf_local.add(
        Agent("drafter",  model_router=local_router),
        Agent("reviewer", model_router=local_router),
    )
    est_local = wf_local.estimate_cost(long_task)
    assert est_local.total_usd == 0.0
    for ln in est_local.lines:
        assert model_is_local(ln.model), f"{ln.model} should be local"
    print("  All models local:", [ln.model for ln in est_local.lines])

    # ── Preset: hybrid (local fast, Bedrock large) ────────────────────────────
    print("\n── Hybrid Bedrock preset ──────────────────────────────────────────────")
    hybrid_router = ModelTierRouter(tiers=ModelTierRouter.PRESET_HYBRID_BEDROCK)
    wf_hybrid = Workflow(cost_cap=CostCap(usd=1.00))
    wf_hybrid.add(
        Agent("analyst", model_router=hybrid_router),
        Agent("writer",  model_router=hybrid_router),
    )
    est_hybrid = wf_hybrid.estimate_cost(long_task)
    print(est_hybrid)

    # ── Offline sandbox run (no real API calls) ───────────────────────────────
    if os.environ.get("MESHFLOW_MOCK") == "1":
        print("\n── Sandbox run (MESHFLOW_MOCK=1) ──────────────────────────────────────")
        result = wf.run(short_task)
        print(f"  Status:       {result.status}")
        print(f"  Agent costs:  {result.agent_costs}")
        print(f"  Cloud agents: {result.cloud_agents}")
        print(f"  Total cost:   ${result.total_cost_usd:.4f}")
        print(f"  Total tokens: {result.total_tokens}")
    else:
        print("\nSkipping live run (set MESHFLOW_MOCK=1 for offline sandbox execution).")

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
