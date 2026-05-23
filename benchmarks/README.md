# MeshFlow Benchmarks

Measures throughput, p50/p95/p99 latency, and peak memory using simulated
providers — no API key required.

## Quick start

```bash
# All concurrency levels (10, 100, 1000)
python benchmarks/bench_core.py

# Quick smoke test (concurrency 10 only — used in CI)
python benchmarks/bench_core.py --quick

# Specific levels + JSON output
python benchmarks/bench_core.py --concurrency 10 100 --output results.json

# Via meshflow CLI
meshflow bench --quick
meshflow bench --concurrency 10 100

# Via Makefile
make bench
make bench-fast   # concurrency 10 50 only
```

## What it measures

| Benchmark | Description |
|---|---|
| **Concurrency sweep** | Throughput (rps), p50/p95/p99 latency, success rate at each concurrency level |
| **provider.complete** | Raw calls/s through the simulated provider |
| **ledger.write** | SQLite write throughput for `StepRecord` entries |
| **chain.validate** | Hash-chain validation steps per ms |
| **Memory profile** | Peak RSS at concurrency=50, 200 runs |

## Interpreting results

- **rps** — runs completed per second wall-clock; higher is better.
- **p99ms** — 99th percentile latency; the long tail matters for SLA budgets.
- **peak_memory_mb** — RSS peak during the scenario; keep under your container limit.
- **success_rate** — should be 1.0; any failures indicate governance layer errors.

## Expected baseline (M2 MacBook Pro, Python 3.11)

| concurrency | rps | p50ms | p99ms | mem_mb |
|---|---|---|---|---|
| 10 | ~400 | ~24 | ~35 | ~45 |
| 100 | ~1 200 | ~82 | ~120 | ~90 |
| 1 000 | ~2 000 | ~480 | ~800 | ~210 |

Results vary with Python version, OS scheduler, and machine load.
