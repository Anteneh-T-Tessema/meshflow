# Provider Router

`ProviderRouter` selects the right model and provider based on role, budget, and compliance — no manual configuration needed.

```python
from meshflow.agents.router import ProviderRouter, auto_provider, auto_model
from meshflow import Agent

# One-liner: auto-select provider for a given role
agent = Agent(name="planner", role="planner", provider=auto_provider("planner"))

# Budget-aware: stay under $0.01/run → picks haiku automatically
agent = Agent(name="batch", role="executor", provider=auto_provider(budget_usd=0.005))

# Compliance-aware: HIPAA/SOX/GDPR → always routes to opus
agent = Agent(name="reviewer", role="critic", provider=auto_provider(compliance="hipaa"))
```

## ProviderRouter

```python
from meshflow.agents.router import ProviderRouter

router = ProviderRouter()

# Returns (provider, model_id)
provider, model = router.route("executor", budget_usd=0.003)
provider, model = router.route("planner", compliance="hipaa")
```

### Default Routing Table

Routing rules are checked in this order:

| Rule | Condition | Model |
|------|-----------|-------|
| 1. Compliance gate | `hipaa`, `sox`, `gdpr`, `pci`, `nerc` | `claude-opus-4-7` |
| 2. Custom override | `set_rule()` applied for this role | custom |
| 3. Budget gate | `budget_usd < $0.01` | `claude-haiku-4-5-20251001` |
| 4. Role default | see table below | — |

**Role defaults:**

| Role | Default Model |
|------|--------------|
| `orchestrator` | `claude-sonnet-4-6` |
| `planner` | `claude-sonnet-4-6` |
| `critic` | `claude-sonnet-4-6` |
| `researcher` | `claude-sonnet-4-6` |
| `guardian` | `claude-opus-4-7` (safety non-negotiable) |
| `executor` | `claude-haiku-4-5-20251001` |
| any other | `claude-sonnet-4-6` |

### Custom Rules

```python
router = ProviderRouter()
router.set_rule("executor", model="claude-haiku-4-5-20251001", budget_ceiling=0.01)
provider, model = router.route("executor", budget_usd=0.003)
```

### Explain a Routing Decision

```python
explanation = router.explain("critic", budget_usd=0.05, compliance="")
# "model='claude-sonnet-4-6' (role='critic' default)"

explanation = router.explain("executor", budget_usd=0.002)
# "model='claude-haiku-4-5-20251001' (budget=$0.0020 < $0.01 → haiku)"
```

## `route_with_health()` — Auto-Fallback on Degraded Models

```python
router = ProviderRouter()
router.set_fallback_chain(
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)

# Skips any model whose health score is below the degraded threshold
provider, model = router.route_with_health("planner", budget_usd=0.5)
```

`route_with_health()` consults the global `ModelHealthTracker`. If the primary model is degraded it walks the fallback chain and returns the first healthy model. If all are degraded, it returns the model with the highest health score.

## `set_fallback_chain()`

```python
router.set_fallback_chain(
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)
# Returns self — chainable
```

The primary model is always prepended to the chain even if not listed. Fallback order is preserved.

## Convenience Functions

### `auto_provider()`

```python
from meshflow.agents.router import auto_provider

provider = auto_provider()                              # executor, $1.00 budget
provider = auto_provider(role="planner")
provider = auto_provider(role="critic", budget_usd=0.005)
provider = auto_provider(compliance="sox")
```

### `auto_model()`

Returns only the model ID string — useful when you want to inspect the selection without constructing a provider.

```python
from meshflow.agents.router import auto_model

model = auto_model("planner")                           # "claude-sonnet-4-6"
model = auto_model("executor", budget_usd=0.002)        # "claude-haiku-4-5-20251001"
model = auto_model("guardian", compliance="hipaa")      # "claude-opus-4-7"
```

## Latency-Constrained Routing

```python
provider, model = router.route_with_latency(
    "executor",
    budget_usd=0.5,
    max_p95_latency_ms=800.0,
    prefer="speed",   # or "quality"
)
```

`route_with_latency()` filters candidates where `health.is_degraded()` is True or where p95 latency exceeds `max_p95_latency_ms`. Among the remaining candidates it picks the fastest (`prefer="speed"`) or the highest health-score model (`prefer="quality"`).
