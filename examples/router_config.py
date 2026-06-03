"""Router persistence, YAML config, and report — Sprint 85.

Shows how to:
1. Define a router in code, run it, then save its learned state.
2. Load the state on the next restart — thresholds survive the restart.
3. Export the config as YAML for teammates / version control.
4. Load the YAML config (and override individual fields).
5. Export outcome history to CSV for external analysis.
6. Print a RouterReport with tier distribution and cost savings.

Run offline:
    MESHFLOW_MOCK=1 python examples/router_config.py
"""
from __future__ import annotations

import os, json, tempfile, pathlib

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow import (
    AdaptiveModelTierRouter,
    ModelTier,
    RouterOutcomeStore,
)
from meshflow.agents.adaptation import export_outcomes_csv

# ── 1. Build and run a router ─────────────────────────────────────────────────

store = RouterOutcomeStore(path=":memory:")
router = AdaptiveModelTierRouter(
    tiers=[
        ModelTier("fast",  "llama3.2",   max_tokens=512,  is_local=True),
        ModelTier("smart", "mistral:7b", max_tokens=2048, is_local=True),
        ModelTier("large", "gpt-4o",     max_tokens=4096),
    ],
    smart_threshold=0.33,
    large_threshold=0.67,
    adapt_every=100,
    exploration_rate=0.05,
    adapt_mode="manual",
    store=store,
)

tasks = [
    "Summarise the key points.",
    "What is 2 + 2?",
    "Analyse and compare quantum computing vs classical AI for Fortune 500 CTO.",
    "def fibonacci(n): return ...",
    "Hello!",
    "However, despite the uncertainty, evaluate the trade-offs and strategic implications "
    "of deploying large-scale distributed systems in regulated environments. " * 3,
]

print("── Routing 6 tasks ───────────────────────────────────────────────────────")
for i, task in enumerate(tasks):
    run_id = f"demo-{i}"
    result = router.route(task, run_id=run_id)
    quality = 0.85 if result.is_local else 0.78
    router.record_outcome(run_id, success=True, quality=quality,
                          latency_ms=150.0, actual_cost_usd=0.0 if result.is_local else 0.008)
    tag = "(local)" if result.is_local else "(cloud)"
    print(f"  [{result.tier:6s}] {tag}  {task[:60]!r}")

# ── 2. Print a report ─────────────────────────────────────────────────────────

print("\n── Router report ─────────────────────────────────────────────────────────")
print(router.report())

# ── 3. Save state to JSON ─────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    state_path = str(pathlib.Path(tmp) / "router_state.json")
    router.save(state_path)
    print(f"── Saved state to {state_path}")
    snap = json.load(open(state_path))
    print(f"   smart_threshold : {snap['smart_threshold']}")
    print(f"   large_threshold : {snap['large_threshold']}")
    print(f"   route_count     : {snap['route_count']}")
    print(f"   tiers           : {[t['name'] for t in snap['tiers']]}")

    # ── 4. Load state — simulates a process restart ───────────────────────────
    print(f"\n── Loading state (simulated restart) ─────────────────────────────────")
    router2 = AdaptiveModelTierRouter.load(
        state_path,
        store=RouterOutcomeStore(path=":memory:"),   # fresh store; thresholds restored
    )
    print(f"   Restored smart_threshold : {router2._smart:.3f}")
    print(f"   Restored large_threshold : {router2._large:.3f}")
    print(f"   Restored route_count     : {router2._route_count}")

    # ── 5. YAML round-trip ────────────────────────────────────────────────────
    yaml_path = str(pathlib.Path(tmp) / "router.yaml")
    router.to_yaml(yaml_path)
    print(f"\n── YAML config ({yaml_path}) ──────────────────────────────────────")
    print(open(yaml_path).read())

    router3 = AdaptiveModelTierRouter.from_yaml(
        yaml_path,
        exploration_rate=0.0,            # override: no exploration in prod
        store=RouterOutcomeStore(path=":memory:"),
    )
    print(f"   Loaded from YAML: smart={router3._smart:.3f}  large={router3._large:.3f}  tiers={[t.name for t in router3.tiers()]}")

    # ── 6. CSV export ─────────────────────────────────────────────────────────
    csv_path = str(pathlib.Path(tmp) / "outcomes.csv")
    n = export_outcomes_csv(store, csv_path)
    print(f"\n── Exported {n} outcomes to CSV")
    if n > 0:
        lines = open(csv_path).readlines()
        print(f"   Header: {lines[0].strip()}")
        print(f"   Row 1 : {lines[1].strip()}")

print("\n── CLI equivalent commands ───────────────────────────────────────────────")
print("  meshflow routing-report --db meshflow_routing.db")
print("  meshflow routing-report --db meshflow_routing.db --state router_state.json")
print("  meshflow routing-report --db meshflow_routing.db --export outcomes.csv")
print("  meshflow routing-report --db meshflow_routing.db --json")
print()
print("  Done.\n")
