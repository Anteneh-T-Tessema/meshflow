# SLA Contracts

`SLATracker` records per-agent latency observations, computes p50/p95/p99 percentiles, and automatically detects contract breaches — providing the operational accountability evidence required by regulated service agreements.

```python
from meshflow.sla.tracker import SLAStore, SLATracker

store   = SLAStore("meshflow_sla.db")
tracker = SLATracker(store)

# Record a latency observation
obs, breaches = tracker.record("phi_agent", latency_ms=340.5, success=True)
if breaches:
    for b in breaches:
        print(f"BREACH: {b.breach_type} — observed {b.observed:.1f}ms, threshold {b.threshold:.1f}ms")
```

## `SLAContract` Fields

```python
@dataclass
class SLAContract:
    contract_id: str
    agent_name:  str
    p50_ms:      float    # median latency threshold
    p95_ms:      float    # 95th percentile threshold
    p99_ms:      float    # 99th percentile threshold
    error_rate:  float    # max acceptable error rate (0.0–1.0)
    window_s:    float    # observation window in seconds (default 3600)
    enabled:     bool
    created_at:  float
```

Thresholds must satisfy `p50 ≤ p95 ≤ p99`. `error_rate` must be `0.0–1.0`.

## Defining a Contract

```python
contract = store.define_contract(
    agent_name="phi_agent",
    p50_ms=500.0,
    p95_ms=1200.0,
    p99_ms=3000.0,
    error_rate=0.01,    # 1% max error rate
    window_s=3600.0,    # evaluate over a 1-hour rolling window
)
```

Calling `define_contract` again for the same `agent_name` replaces the existing contract (`INSERT OR REPLACE`).

## `SLATracker.record()` — Observation and Breach Detection

```python
obs, breaches = tracker.record(
    agent_name="phi_agent",
    latency_ms=1450.0,
    success=True,         # False to count as an error
    now=None,             # inject a timestamp for testing
)
```

Returns `(LatencyRecord, list[SLABreach])`. Breaches are evaluated automatically after each observation. **Breach detection requires at least 10 observations** in the window — this prevents spurious alerts during ramp-up.

## `SLATracker.stats()` — Percentile Statistics

```python
s = tracker.stats("phi_agent", window_s=3600.0)
print(s.p50_ms, s.p95_ms, s.p99_ms)
print(s.error_rate, s.total)
# SLAStats: agent_name, total, p50_ms, p95_ms, p99_ms, avg_ms, error_rate, window_s
```

## `SLABreach` Fields

```python
@dataclass
class SLABreach:
    breach_id:   str
    contract_id: str
    agent_name:  str
    breach_type: str     # "p50" | "p95" | "p99" | "error_rate"
    observed:    float   # actual measured value
    threshold:   float   # contract limit that was exceeded
    ts:          float   # Unix timestamp of detection
```

## `meshflow sla` CLI

```bash
# Define an SLA contract
meshflow sla define phi_agent \
  --p50 500 --p95 1200 --p99 3000 \
  --error-rate 0.01 \
  --window 3600

# Show current percentile stats
meshflow sla stats phi_agent --window 3600

# List recent breaches
meshflow sla breaches --agent phi_agent --limit 20

# List all contracts
meshflow sla list
```

All commands accept `--db meshflow_sla.db` to override the default database path.

## Compliance Context

SLA contracts provide evidence for:

- **HIPAA** — demonstrating that patient-data workflows meet response-time commitments in BAAs.
- **SOX** — documenting that financial processing agents meet latency SLAs for internal controls.
- **SLA audit deliverables** — `SLABreach` records are included in compliance snapshots (see [Snapshots](snapshots.md)).

Breach records are persistent and append-only. Disabling a contract (`enabled=False`) stops future breach detection but does not delete historical breach records.
