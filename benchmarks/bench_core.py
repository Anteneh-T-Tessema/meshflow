"""MeshFlow performance benchmarks.

Measures throughput, p50/p95/p99 latency, and peak memory for 10 / 100 / 1 000
concurrent simulated runs using the in-process Mesh + simulated providers so no
API key is required.

Usage::

    python benchmarks/bench_core.py               # all scenarios
    python benchmarks/bench_core.py --concurrency 10 100
    python benchmarks/bench_core.py --output results.json

Results are printed as an ASCII table and optionally saved as JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import statistics
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from typing import Any

# Make sure the project root is importable when running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Simulated provider (zero API cost) ───────────────────────────────────────

class _SimulatedProvider:
    """Drop-in provider that echoes the task with a fixed delay."""

    def __init__(self, delay_s: float = 0.01) -> None:
        self._delay = delay_s

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        await asyncio.sleep(self._delay)
        task = messages[-1].get("content", "") if messages else ""
        return f"Simulated: {task[:80]}", 20, 0.0001

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self.complete(model, messages, system, max_tokens)

    async def stream_complete(self, *args: Any, **kwargs: Any):  # noqa: ANN201
        for word in ["Simulated", " response"]:
            from meshflow.core.schemas import TokenChunk
            yield TokenChunk(text=word, agent_id="sim", step_id="s", run_id="r")
            await asyncio.sleep(0.001)


# ── Result structures ─────────────────────────────────────────────────────────

@dataclass
class RunMetrics:
    latency_s: float
    tokens: int
    cost_usd: float
    status: str
    error: str = ""


@dataclass
class ScenarioResult:
    name: str
    concurrency: int
    n_runs: int
    total_s: float
    throughput_rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    success_rate: float
    peak_memory_mb: float
    total_tokens: int
    total_cost_usd: float
    runs: list[RunMetrics] = field(default_factory=list, repr=False)

    def summary_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("runs")
        return d


# ── Core benchmark runner ─────────────────────────────────────────────────────

async def _single_run(mesh: Any, task: str) -> RunMetrics:
    t0 = time.perf_counter()
    try:
        result = await mesh.run(task)
        return RunMetrics(
            latency_s=time.perf_counter() - t0,
            tokens=result.total_tokens,
            cost_usd=result.total_cost_usd,
            status=result.status,
        )
    except Exception as exc:
        return RunMetrics(
            latency_s=time.perf_counter() - t0,
            tokens=0,
            cost_usd=0.0,
            status="error",
            error=str(exc)[:120],
        )


async def _run_scenario(
    name: str,
    concurrency: int,
    total_runs: int,
    task: str = "What is 2 + 2?",
    provider_delay_s: float = 0.01,
) -> ScenarioResult:
    from meshflow.core.mesh import Mesh
    from meshflow.core.schemas import Policy

    provider = _SimulatedProvider(delay_s=provider_delay_s)
    policy = Policy(budget_usd=10.0, max_steps=1)

    gc.collect()
    tracemalloc.start()
    wall_start = time.perf_counter()

    sem = asyncio.Semaphore(concurrency)
    results: list[RunMetrics] = []

    async def _bounded(task_text: str) -> None:
        async with sem:
            mesh = Mesh(policy=policy)
            # Inject the simulated provider
            mesh._simulated_provider = provider  # type: ignore[attr-defined]
            r = await _single_run(mesh, task_text)
            results.append(r)

    await asyncio.gather(*[_bounded(f"{task} (run {i})") for i in range(total_runs)])

    total_s = time.perf_counter() - wall_start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    latencies_ms = [r.latency_s * 1000 for r in results]
    latencies_ms.sort()
    successes = [r for r in results if r.status not in ("error", "aborted")]

    def percentile(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    return ScenarioResult(
        name=name,
        concurrency=concurrency,
        n_runs=total_runs,
        total_s=total_s,
        throughput_rps=total_runs / total_s if total_s > 0 else 0.0,
        p50_ms=percentile(latencies_ms, 50),
        p95_ms=percentile(latencies_ms, 95),
        p99_ms=percentile(latencies_ms, 99),
        min_ms=min(latencies_ms) if latencies_ms else 0.0,
        max_ms=max(latencies_ms) if latencies_ms else 0.0,
        success_rate=len(successes) / total_runs if total_runs else 0.0,
        peak_memory_mb=peak / 1_048_576,
        total_tokens=sum(r.tokens for r in results),
        total_cost_usd=sum(r.cost_usd for r in results),
        runs=results,
    )


# ── Provider-level microbenchmarks ────────────────────────────────────────────

async def bench_provider_complete(n: int = 500) -> dict[str, float]:
    provider = _SimulatedProvider(delay_s=0.001)
    messages = [{"role": "user", "content": "ping"}]
    t0 = time.perf_counter()
    await asyncio.gather(*[
        provider.complete("sim", messages, "sys", 16) for _ in range(n)
    ])
    total = time.perf_counter() - t0
    return {"n": n, "total_s": total, "rps": n / total}


async def bench_ledger_write(n: int = 1000) -> dict[str, float]:
    from meshflow.core.ledger import ReplayLedger, StepRecord
    import hashlib, datetime, uuid

    ledger = ReplayLedger(":memory:")
    run_id = str(uuid.uuid4())

    import datetime

    def _record(i: int) -> StepRecord:
        return StepRecord(
            run_id=run_id,
            step_id=str(uuid.uuid4()),
            node_id="bench-node",
            node_kind="python",
            input_task=f"task-{i}",
            output_content=f"output-{i}",
            verdict="commit",
            blocked=False,
            block_reason="",
            uncertainty=0.0,
            cost_usd=0.0001,
            tokens_used=10,
            carbon_gco2=0.0,
            duration_ms=5.0,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            metadata={},
        )

    t0 = time.perf_counter()
    for i in range(n):
        await ledger.write(_record(i))
    total = time.perf_counter() - t0
    return {"n": n, "total_s": total, "writes_per_s": n / total}


async def bench_chain_validation(n: int = 200) -> dict[str, float]:
    from meshflow.core.ledger import ReplayLedger, StepRecord
    import uuid

    import datetime

    ledger = ReplayLedger(":memory:")
    run_id = str(uuid.uuid4())
    for i in range(n):
        await ledger.write(StepRecord(
            run_id=run_id,
            step_id=str(uuid.uuid4()),
            node_id="node",
            node_kind="python",
            input_task="t",
            output_content="o",
            verdict="commit",
            blocked=False,
            block_reason="",
            uncertainty=0.0,
            cost_usd=0.0,
            tokens_used=5,
            carbon_gco2=0.0,
            duration_ms=1.0,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            metadata={},
        ))
    t0 = time.perf_counter()
    result = await ledger.verify_chain(run_id)
    total = time.perf_counter() - t0
    steps = result.get("total_entries", n)
    return {"n_steps": steps, "validation_s": total, "steps_per_ms": steps / (total * 1000)}


# ── ASCII table printer ───────────────────────────────────────────────────────

def _table(headers: list[str], rows: list[list[str]], title: str = "") -> None:
    if title:
        print(f"\n{'─' * 4} {title} {'─' * max(0, 70 - len(title))}")
    widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("─" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


# ── Main ──────────────────────────────────────────────────────────────────────

async def _main(concurrencies: list[int], output_path: str | None) -> None:
    all_results: list[dict[str, Any]] = []

    # ── 1. Concurrency / throughput scenarios ─────────────────────────────────
    scenarios = []
    for c in concurrencies:
        n = min(c * 10, 1000)  # 10× concurrency, max 1000
        print(f"  Running concurrency={c}  total_runs={n} …", flush=True)
        r = await _run_scenario(
            name=f"concurrent-{c}",
            concurrency=c,
            total_runs=n,
            provider_delay_s=0.005,
        )
        scenarios.append(r)
        all_results.append({"type": "scenario", **r.summary_dict()})

    _table(
        ["scenario", "c", "n", "rps", "p50ms", "p95ms", "p99ms", "success%", "mem_mb"],
        [
            [
                r.name,
                str(r.concurrency),
                str(r.n_runs),
                f"{r.throughput_rps:.1f}",
                f"{r.p50_ms:.1f}",
                f"{r.p95_ms:.1f}",
                f"{r.p99_ms:.1f}",
                f"{r.success_rate * 100:.1f}%",
                f"{r.peak_memory_mb:.1f}",
            ]
            for r in scenarios
        ],
        title="Concurrency / throughput",
    )

    # ── 2. Microbenchmarks ────────────────────────────────────────────────────
    print("\n  Running microbenchmarks …", flush=True)

    prov = await bench_provider_complete(500)
    ledger = await bench_ledger_write(1000)
    chain = await bench_chain_validation(200)

    _table(
        ["benchmark", "n", "result"],
        [
            ["provider.complete", str(int(prov["n"])), f"{prov['rps']:.0f} calls/s"],
            ["ledger.write",      str(int(ledger["n"])), f"{ledger['writes_per_s']:.0f} writes/s"],
            ["chain.validate",    str(int(chain["n_steps"])), f"{chain['steps_per_ms']:.1f} steps/ms"],
        ],
        title="Microbenchmarks",
    )
    all_results.append({"type": "micro", **prov, "name": "provider_complete"})
    all_results.append({"type": "micro", **ledger, "name": "ledger_write"})
    all_results.append({"type": "micro", **chain, "name": "chain_validate"})

    # ── 3. Memory profile at peak load ────────────────────────────────────────
    print("\n  Memory profile (concurrency=50, 200 runs) …", flush=True)
    mem_r = await _run_scenario("memory-profile", concurrency=50, total_runs=200)
    _table(
        ["metric", "value"],
        [
            ["peak memory MB",   f"{mem_r.peak_memory_mb:.2f}"],
            ["throughput rps",   f"{mem_r.throughput_rps:.1f}"],
            ["success rate",     f"{mem_r.success_rate * 100:.1f}%"],
        ],
        title="Memory profile",
    )
    all_results.append({"type": "memory", **mem_r.summary_dict()})

    # ── Save output ───────────────────────────────────────────────────────────
    if output_path:
        with open(output_path, "w") as f:
            json.dump({"timestamp": time.time(), "results": all_results}, f, indent=2)
        print(f"\nResults saved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MeshFlow benchmarks")
    parser.add_argument(
        "--concurrency", nargs="+", type=int,
        default=[10, 100, 1000],
        help="Concurrency levels to test (default: 10 100 1000)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save results as JSON to this path",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Smoke-check only: run the first concurrency level with reduced iterations",
    )
    args = parser.parse_args()

    levels = [args.concurrency[0]] if args.quick else args.concurrency

    print("MeshFlow Benchmarks" + (" (quick mode)" if args.quick else ""))
    print(f"  concurrency levels: {levels}")
    print(f"  Python {sys.version.split()[0]}")
    print()

    asyncio.run(_main(levels, args.output))
    print("\nDone.")


if __name__ == "__main__":
    main()
