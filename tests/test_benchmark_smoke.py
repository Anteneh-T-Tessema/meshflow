"""Benchmark runs must measure the configured provider through the runtime."""

import pytest

from benchmarks import bench_core


@pytest.mark.asyncio
async def test_benchmark_dispatches_simulated_provider_and_accounts_usage(monkeypatch):
    calls = []
    original = bench_core._SimulatedProvider.complete

    async def complete(self, *args, **kwargs):
        calls.append(1)
        return await original(self, *args, **kwargs)

    monkeypatch.setenv("MESHFLOW_MOCK", "1")
    monkeypatch.setattr(bench_core._SimulatedProvider, "complete", complete)
    result = await bench_core._run_scenario(
        "provider-regression", concurrency=2, total_runs=3, provider_delay_s=0
    )
    assert len(calls) == 3
    assert result.success_rate == 1.0
    assert result.total_tokens == 60
    assert result.total_cost_usd == pytest.approx(0.0003)
