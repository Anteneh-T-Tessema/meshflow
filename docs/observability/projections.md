# Event Projections

`EventProjector` turns the raw append-only ledger into four queryable read models.

```python
from meshflow import (
    EventProjector,
    AuditTrailProjection,
    NodeLatencyProjection,
    PolicyViolationProjection,
    WorkflowSummaryProjection,
    WorkflowSummary,
    NodeLatencyStats,
)

projector = EventProjector(ledger)

# Build all projections for a run
await projector.project("run-abc123")
```

## AuditTrailProjection

Chronological record of every governed step.

```python
trail: AuditTrailProjection = await projector.audit_trail("run-abc123")
for entry in trail.entries:
    print(entry.node_id, entry.verdict, entry.timestamp)
    print("  blocked:", entry.blocked, entry.block_reason)
    print("  cost:", entry.cost_usd, "tokens:", entry.tokens_used)
```

## NodeLatencyProjection

Per-node p50/p95/p99 latency statistics.

```python
latency: NodeLatencyProjection = await projector.node_latency("run-abc123")
stats: NodeLatencyStats = latency.stats_for("fetch-node")
print(f"p50={stats.p50_ms}ms  p95={stats.p95_ms}ms  p99={stats.p99_ms}ms")

# All nodes
for node_id, stats in latency.all_stats().items():
    print(node_id, stats.p95_ms)
```

## PolicyViolationProjection

All policy violations across a run.

```python
violations: PolicyViolationProjection = await projector.policy_violations("run-abc123")
for v in violations.violations:
    print(v.node_id, v.rule_name, v.reason, v.timestamp)

print("total violations:", violations.count)
print("blocked steps:", violations.blocked_count)
```

## WorkflowSummaryProjection

Aggregate metrics: cost, tokens, carbon, confidence, node count.

```python
summary_proj: WorkflowSummaryProjection = await projector.workflow_summary("run-abc123")
s: WorkflowSummary = summary_proj.summary

print(f"Steps:    {s.step_count}")
print(f"Cost:     ${s.total_cost_usd:.4f}")
print(f"Tokens:   {s.total_tokens}")
print(f"Carbon:   {s.total_carbon_gco2:.4f} gCO₂")
print(f"Blocked:  {s.blocked_steps}/{s.step_count}")
print(f"Duration: {s.duration_ms:.0f}ms")
```

## Full Report

```python
report = await projector.report("run-abc123")
# Returns dict with all four projections serialized
import json
print(json.dumps(report, indent=2))
```

## REST API

```bash
GET /graph/{run_id}    # workflow graph + node stats
GET /sla               # SLA stats across all agents
```
