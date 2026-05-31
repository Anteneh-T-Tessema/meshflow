# Model Health Tracking

`ModelHealthTracker` records per-model success/failure outcomes in a rolling window and exposes a health score used by `ProviderRouter` for automatic fallback.

```python
from meshflow.agents.health import get_health_tracker

tracker = get_health_tracker()
tracker.record_success("claude-sonnet-4-6", latency_ms=320.0)
tracker.record_failure("claude-opus-4-7", error="timeout")

if tracker.is_degraded("claude-opus-4-7"):
    print("opus is degraded — router will skip it")

best = tracker.best_model(["claude-opus-4-7", "claude-sonnet-4-6"])
```

## `ModelHealthTracker`

```python
from meshflow.agents.health import ModelHealthTracker

tracker = ModelHealthTracker(
    window_size=50,          # rolling window length (outcomes kept per model)
    degraded_threshold=0.7,  # health score below this → model is degraded
)
```

### Recording Outcomes

```python
tracker.record_success("claude-sonnet-4-6", latency_ms=250.0)
tracker.record_failure("claude-sonnet-4-6", error="rate_limit", latency_ms=0.0)
```

Both methods are thread-safe. Each call appends an `_Outcome` to the model's fixed-size deque; the oldest outcome is automatically evicted when the window is full.

### Health Score

```python
score = tracker.health_score("claude-sonnet-4-6")
# Returns success fraction in [0.0, 1.0].
# Unseen models return 1.0 (optimistic default — do not penalise new models).
```

### `is_degraded()`

```python
degraded = tracker.is_degraded("claude-opus-4-7")
# True when health_score < degraded_threshold
```

### `ModelHealthSummary`

```python
summary = tracker.summary("claude-sonnet-4-6")
summary.health_score      # float, 0.0 – 1.0
summary.success_count     # int
summary.failure_count     # int
summary.p50_latency_ms    # float
summary.p95_latency_ms    # float
summary.is_degraded       # bool
summary.last_error        # str

print(summary.to_dict())
```

### All Summaries

```python
summaries = tracker.all_summaries()   # list[ModelHealthSummary], sorted by model name
for s in summaries:
    print(f"{s.model}: score={s.health_score:.2f} p95={s.p95_latency_ms:.0f}ms")
```

### `best_model()`

```python
best = tracker.best_model(["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"])
# Returns the model with the highest health score from the candidates list.
```

### Reset

```python
tracker.reset("claude-opus-4-7")  # reset one model
tracker.reset()                   # reset all models
```

## Global Singleton

```python
from meshflow.agents.health import get_health_tracker, reset_health_tracker

tracker = get_health_tracker()   # lazy-initialised thread-safe singleton
reset_health_tracker()           # replace singleton with a fresh tracker (useful in tests)
```

The global tracker is what `ProviderRouter.route_with_health()` consults by default. `BaseAgent.think()` also reads it automatically and swaps to a healthy fallback when the configured model is degraded.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHFLOW_HEALTH_WINDOW` | `50` | Number of outcomes in the rolling window |
| `MESHFLOW_HEALTH_DEGRADED_THRESHOLD` | `0.7` | Minimum health score before a model is considered degraded |

```bash
MESHFLOW_HEALTH_WINDOW=100 MESHFLOW_HEALTH_DEGRADED_THRESHOLD=0.8 python app.py
```

Lowering the threshold makes the router more tolerant; raising it triggers fallback sooner.
