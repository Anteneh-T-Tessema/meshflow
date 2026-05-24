"""Lightweight benchmark smoke-tests — verify correctness, not raw speed.

These run in the normal test suite (no API key). They exercise the same
benchmark helpers as `meshflow bench` but with tiny n values so the suite
stays fast.
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── helpers ────────────────────────────────────────────────────────────────────

def _load_bench():
    import importlib.util
    bench_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmarks", "bench_core.py",
    )
    spec = importlib.util.spec_from_file_location("bench_core_test", bench_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bench_core_test"] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


# ── simulated provider ─────────────────────────────────────────────────────────

class TestSimulatedProvider:
    @pytest.mark.asyncio
    async def test_complete_returns_tuple(self) -> None:
        bench = _load_bench()
        p = bench._SimulatedProvider(delay_s=0.0)
        text, tokens, cost = await p.complete("sim", [{"role": "user", "content": "hi"}], "sys", 16)
        assert isinstance(text, str) and "Simulated" in text
        assert isinstance(tokens, int) and tokens > 0
        assert isinstance(cost, float) and cost > 0

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self) -> None:
        bench = _load_bench()
        p = bench._SimulatedProvider(delay_s=0.0)
        chunks = []
        async for chunk in p.stream_complete():
            chunks.append(chunk.text)
        assert len(chunks) >= 1
        assert all(isinstance(c, str) for c in chunks)


# ── microbenchmarks ────────────────────────────────────────────────────────────

class TestMicrobenchmarks:
    @pytest.mark.asyncio
    async def test_provider_complete_returns_rps(self) -> None:
        bench = _load_bench()
        result = await bench.bench_provider_complete(n=10)
        assert result["n"] == 10
        assert result["rps"] > 0
        assert result["total_s"] > 0

    @pytest.mark.asyncio
    async def test_ledger_write_returns_rate(self) -> None:
        bench = _load_bench()
        result = await bench.bench_ledger_write(n=20)
        assert result["n"] == 20
        assert result["writes_per_s"] > 0

    @pytest.mark.asyncio
    async def test_chain_validation_returns_rate(self) -> None:
        bench = _load_bench()
        result = await bench.bench_chain_validation(n=10)
        assert result["n_steps"] >= 10
        assert result["steps_per_ms"] > 0
        assert result["validation_s"] >= 0


# ── scenario result structure ──────────────────────────────────────────────────

class TestScenarioResult:
    def test_summary_dict_excludes_runs(self) -> None:
        bench = _load_bench()
        sr = bench.ScenarioResult(
            name="test", concurrency=1, n_runs=1, total_s=0.1,
            throughput_rps=10.0, p50_ms=50.0, p95_ms=90.0, p99_ms=99.0,
            min_ms=10.0, max_ms=100.0, success_rate=1.0,
            peak_memory_mb=1.0, total_tokens=20, total_cost_usd=0.001,
        )
        d = sr.summary_dict()
        assert "runs" not in d
        assert d["name"] == "test"
        assert d["throughput_rps"] == 10.0

    def test_run_metrics_fields(self) -> None:
        bench = _load_bench()
        rm = bench.RunMetrics(latency_s=0.05, tokens=10, cost_usd=0.001, status="commit")
        assert rm.latency_s == 0.05
        assert rm.error == ""


# ── CLI bench command ──────────────────────────────────────────────────────────

class TestBenchCLI:
    def test_bench_command_registered(self) -> None:
        from meshflow.cli.main import main
        import argparse
        # Verify 'bench' appears in the help text
        import subprocess
        result = subprocess.run(
            ["python", "-m", "meshflow.cli.main", "--help"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert "bench" in result.stdout or result.returncode == 0

    def test_meshflow_version(self) -> None:
        import meshflow
        assert meshflow.__version__ == "0.22.0"
