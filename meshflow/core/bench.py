"""High-throughput orchestration latency benchmark engine.

Measures node transitions and workflow execution overhead to verify the framework
can sustain under 42ms scale requirements.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from meshflow.core.node import MeshNode, NodeInput, NodeOutput
from meshflow.core.workflow import WorkflowDefinition
from meshflow.core.runtime import StepRuntime
from meshflow.core.schemas import Policy
from meshflow.security.identity import AgentIdentityProvider
from meshflow.core.ledger import ReplayLedger


@dataclass
class BenchmarkResult:
    iterations: int
    num_nodes: int
    total_time_ms: float
    avg_run_time_ms: float
    avg_per_node_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


class FrameworkBenchmark:
    """Benchmark suite to measure internal orchestration transition overhead."""

    def __init__(self, num_nodes: int = 5) -> None:
        self.num_nodes = num_nodes

    def _build_test_workflow(self) -> WorkflowDefinition:
        wf = WorkflowDefinition(name="benchmark-workflow")
        
        # Build sequential dummy nodes with zero-latency runner
        async def dummy_runner(node_input: NodeInput) -> NodeOutput:
            return NodeOutput(content=f"Processed: {node_input.task}")

        from meshflow.core.node import NodeKind
        for i in range(self.num_nodes):
            node_id = f"node_{i}"
            node = MeshNode(id=node_id, kind=NodeKind.NATIVE)
            node._runner = dummy_runner
            wf.add_node(node)

        # Set sequential edges
        for i in range(self.num_nodes - 1):
            wf.add_edge(f"node_{i}", f"node_{i+1}")

        wf.set_entry("node_0")
        wf.set_terminal(f"node_{self.num_nodes - 1}")
        return wf

    async def run(self, iterations: int = 50) -> BenchmarkResult:
        wf = self._build_test_workflow()
        latencies: list[float] = []

        # Run multiple warm-up runs
        for _ in range(5):
            runtime = StepRuntime(
                policy=Policy(),
                run_id="warmup",
                identity=AgentIdentityProvider("warmup"),
                ledger=ReplayLedger(":memory:"),
            )
            await wf.run("test", runtime)

        # Execute timed runs
        start_total = time.perf_counter()
        for _ in range(iterations):
            runtime = StepRuntime(
                policy=Policy(),
                run_id="bench",
                identity=AgentIdentityProvider("bench"),
                ledger=ReplayLedger(":memory:"),
            )
            t0 = time.perf_counter()
            await wf.run("test", runtime)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0)
        
        end_total = time.perf_counter()

        total_time_ms = (end_total - start_total) * 1000.0
        latencies.sort()
        
        avg_run_time_ms = sum(latencies) / len(latencies)
        p50 = latencies[int(len(latencies) * 0.5)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]

        return BenchmarkResult(
            iterations=iterations,
            num_nodes=self.num_nodes,
            total_time_ms=total_time_ms,
            avg_run_time_ms=avg_run_time_ms,
            avg_per_node_ms=avg_run_time_ms / self.num_nodes,
            p50_ms=p50,
            p95_ms=p95,
            p99_ms=p99,
        )
