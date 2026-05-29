"""Unit tests for the framework benchmarking engine."""

from __future__ import annotations

import pytest
from meshflow.core.bench import FrameworkBenchmark, BenchmarkResult


@pytest.mark.asyncio
async def test_framework_benchmark_runs_and_emits_results():
    bench = FrameworkBenchmark(num_nodes=3)
    res = await bench.run(iterations=5)

    assert isinstance(res, BenchmarkResult)
    assert res.iterations == 5
    assert res.num_nodes == 3
    assert res.total_time_ms > 0
    assert res.avg_run_time_ms > 0
    assert res.avg_per_node_ms == res.avg_run_time_ms / 3
    assert res.p50_ms > 0
    assert res.p95_ms > 0
    assert res.p99_ms > 0
