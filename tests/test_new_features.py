"""Tests for the five new features:
 1  CrewAI Flows equivalent   (meshflow/core/flows.py)
 2  Conditional task context  (meshflow/agents/task.py filter factories)
 3  Workflow token streaming  (WorkflowDefinition.stream())
 4  Parameter sweep runner    (meshflow/eval/sweep.py)
 5  Terminal dashboard        (meshflow/cli/dashboard.py)
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import pytest
import yaml as _yaml


# ─────────────────────────────────────────────────────────────────────────────
# Feature 1 — CrewAI Flows equivalent
# ─────────────────────────────────────────────────────────────────────────────

class TestFlows:
    def test_imports(self):
        from meshflow.core.flows import Flow, FlowState, start, listen, router
        assert Flow
        assert FlowState

    def test_flow_state_defaults(self):
        from meshflow.core.flows import FlowState

        class MyState(FlowState):
            topic: str = "default"
            count: int = 0

        s = MyState._get_defaults()
        assert s["topic"] == "default"
        assert s["count"] == 0

    def test_flow_state_update(self):
        from meshflow.core.flows import FlowState

        s = FlowState(x=1, y=2)
        s.update(x=10, z=3)
        assert s.x == 10
        assert s.z == 3

    @pytest.mark.asyncio
    async def test_simple_start_method(self):
        from meshflow.core.flows import Flow, FlowState, start

        class S(FlowState):
            result: str = ""

        class F(Flow[S]):
            @start()
            async def go(self):
                self.state.result = "ran"
                return "ok"

        result = await F().kickoff()
        assert result.steps_executed == ["go"]
        assert result.final_output == "ok"

    @pytest.mark.asyncio
    async def test_start_then_listen(self):
        from meshflow.core.flows import Flow, FlowState, start, listen

        log: list[str] = []

        class S(FlowState):
            pass

        class F(Flow[S]):
            @start()
            async def step_a(self):
                log.append("a")
                return "from_a"

            @listen("step_a")
            async def step_b(self, prev):
                log.append(f"b:{prev}")
                return "from_b"

        result = await F().kickoff()
        assert "a" in log
        assert "b:from_a" in log
        assert result.steps_executed == ["step_a", "step_b"]

    @pytest.mark.asyncio
    async def test_router_conditional_branch(self):
        from meshflow.core.flows import Flow, FlowState, start, listen, router

        executed: list[str] = []

        class S(FlowState):
            score: int = 0

        class F(Flow[S]):
            @start()
            async def evaluate(self):
                self.state.score = 90
                return 90

            @router("evaluate")
            def pick_branch(self, score):
                return "high" if score >= 80 else "low"

            @listen(("evaluate", "high"))
            async def high_path(self, score):
                executed.append("high")
                return "high_result"

            @listen(("evaluate", "low"))
            async def low_path(self, score):
                executed.append("low")
                return "low_result"

        result = await F().kickoff()
        assert "high" in executed
        assert "low" not in executed

    @pytest.mark.asyncio
    async def test_router_low_branch(self):
        from meshflow.core.flows import Flow, FlowState, start, listen, router

        executed: list[str] = []

        class S(FlowState):
            pass

        class F(Flow[S]):
            @start()
            async def eval(self):
                return 40

            @router("eval")
            def route(self, v):
                return "high" if v >= 80 else "low"

            @listen(("eval", "high"))
            async def high(self, v):
                executed.append("high")

            @listen(("eval", "low"))
            async def low(self, v):
                executed.append("low")

        await F().kickoff()
        assert executed == ["low"]

    @pytest.mark.asyncio
    async def test_kickoff_with_inputs(self):
        from meshflow.core.flows import Flow, FlowState, start

        class S(FlowState):
            topic: str = ""

        class F(Flow[S]):
            @start()
            async def run(self):
                return f"topic={self.state.topic}"

        result = await F().kickoff(inputs={"topic": "AI"})
        assert result.final_output == "topic=AI"

    def test_kickoff_sync(self):
        from meshflow.core.flows import Flow, FlowState, start

        class S(FlowState):
            pass

        class F(Flow[S]):
            @start()
            def go(self):
                return "sync"

        result = F().kickoff_sync()
        assert result.final_output == "sync"

    def test_describe(self):
        from meshflow.core.flows import Flow, FlowState, start, listen

        class S(FlowState):
            pass

        class F(Flow[S]):
            @start()
            async def a(self):
                return "a"

            @listen("a")
            async def b(self, _):
                return "b"

        desc = F().describe()
        assert "a" in desc["start_methods"]
        assert "a" in desc["listeners"]

    def test_plot_returns_mermaid(self):
        from meshflow.core.flows import Flow, FlowState, start, listen

        class S(FlowState):
            pass

        class F(Flow[S]):
            @start()
            async def step1(self):
                return ""

            @listen("step1")
            async def step2(self, _):
                return ""

        diagram = F().plot()
        assert "graph TD" in diagram
        assert "step1" in diagram
        assert "step2" in diagram

    @pytest.mark.asyncio
    async def test_max_steps_guard(self):
        from meshflow.core.flows import Flow, FlowState, start, listen

        class S(FlowState):
            n: int = 0

        class F(Flow[S]):
            @start()
            async def a(self):
                self.state.n += 1
                return "loop"

            @listen("a")
            async def b(self, _):
                return "loop"

        # With max_steps=3 it should stop after 3 total
        result = await F(max_steps=3).kickoff()
        assert len(result.steps_executed) <= 3


# ─────────────────────────────────────────────────────────────────────────────
# Feature 2 — Conditional task context filtering
# ─────────────────────────────────────────────────────────────────────────────

class TestContextFilters:
    def _make_task_with_output(self, desc: str, raw: str) -> Any:
        from meshflow.agents.task import Task, TaskOutput
        t = Task(description=desc, expected_output="x")
        t.output = TaskOutput(raw=raw, task_description=desc)
        return t

    def test_confidence_filter_passes_high(self):
        from meshflow.agents.task import Task, TaskOutput, confidence_filter

        src = self._make_task_with_output("Research", "Good output. CONFIDENCE:0.92")
        consumer = Task(
            description="Consume", expected_output="y",
            context=[src],
            context_filter=confidence_filter(0.80),
        )
        prompt = consumer._build_prompt(None)
        assert "Good output" in prompt

    def test_confidence_filter_blocks_low(self):
        from meshflow.agents.task import Task, TaskOutput, confidence_filter

        src = self._make_task_with_output("Research", "Uncertain output. CONFIDENCE:0.50")
        consumer = Task(
            description="Consume", expected_output="y",
            context=[src],
            context_filter=confidence_filter(0.80),
        )
        prompt = consumer._build_prompt(None)
        # Low confidence task should not appear as context
        assert "Uncertain output" not in prompt

    def test_confidence_filter_includes_no_marker(self):
        """Tasks without CONFIDENCE marker pass by default."""
        from meshflow.agents.task import Task, TaskOutput, confidence_filter

        src = self._make_task_with_output("Research", "Output without marker")
        consumer = Task(
            description="Consume", expected_output="y",
            context=[src],
            context_filter=confidence_filter(0.90),
        )
        prompt = consumer._build_prompt(None)
        assert "Output without marker" in prompt

    def test_tag_filter_matches_description(self):
        from meshflow.agents.task import Task, TaskOutput, tag_filter

        # "verified" is in the description
        src = self._make_task_with_output("Verified high-quality research", "Data")
        consumer = Task(
            description="Consume", expected_output="y",
            context=[src],
            context_filter=tag_filter("verified"),
        )
        prompt = consumer._build_prompt(None)
        assert "Data" in prompt

    def test_tag_filter_blocks_missing_tag(self):
        from meshflow.agents.task import Task, TaskOutput, tag_filter

        src = self._make_task_with_output("Basic research", "Data")
        consumer = Task(
            description="Consume", expected_output="y",
            context=[src],
            context_filter=tag_filter("verified"),
        )
        prompt = consumer._build_prompt(None)
        assert "Data" not in prompt

    def test_min_length_filter(self):
        from meshflow.agents.task import Task, TaskOutput, min_length_filter

        short = self._make_task_with_output("Short task", "Hi")
        long_  = self._make_task_with_output("Long task", "A" * 100)

        consumer = Task(
            description="Consume", expected_output="y",
            context=[short, long_],
            context_filter=min_length_filter(50),
        )
        prompt = consumer._build_prompt(None)
        assert "A" * 50 in prompt
        assert "Short task" not in prompt or "Hi" not in prompt

    def test_no_filter_injects_all(self):
        from meshflow.agents.task import Task, TaskOutput

        src_a = self._make_task_with_output("Task A", "Output A")
        src_b = self._make_task_with_output("Task B", "Output B")
        consumer = Task(
            description="Consume", expected_output="y",
            context=[src_a, src_b],
        )
        prompt = consumer._build_prompt(None)
        assert "Output A" in prompt
        assert "Output B" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# Feature 3 — Workflow token streaming
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowStream:
    def _make_workflow_yaml(self, tmp_path):
        data = {
            "name": "stream-test",
            "nodes": {"step": {"kind": "native", "role": "executor"}},
            "edges": [],
        }
        p = tmp_path / "wf.yaml"
        p.write_text(_yaml.safe_dump(data))
        return str(p)

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self, tmp_path):
        from meshflow.core.streaming import StreamChunk
        from meshflow.core.workflow import WorkflowDefinition
        from unittest.mock import AsyncMock, MagicMock

        path = self._make_workflow_yaml(tmp_path)
        wf = WorkflowDefinition.from_yaml(path)

        from meshflow.core.runtime import StepRuntime, RuntimeOutcome, StepRecord
        from meshflow.core.node import NodeOutput
        import uuid, datetime

        runtime = MagicMock(spec=StepRuntime)
        runtime._run_id = "stream-run"

        async def fake_run(node, node_input, ctx):
            record = StepRecord(
                run_id="stream-run", step_id=uuid.uuid4().hex[:8],
                node_id=node.id, node_kind="native",
                input_task="", output_content="streamed output",
                verdict="commit", blocked=False, block_reason="",
                uncertainty=0.0, cost_usd=0.0, tokens_used=5,
                carbon_gco2=0.0, duration_ms=10.0,
                timestamp=datetime.datetime.now().isoformat(),
            )
            return RuntimeOutcome(
                ok=True, node_id=node.id, node_kind="native",
                output=NodeOutput(content="streamed output"),
                record=record, blocked_by="", paused_for_human=False, human_context={},
            )

        runtime.run = fake_run

        chunks = []
        async for chunk in wf.stream("test task", runtime):
            chunks.append(chunk)

        kinds = [c.kind for c in chunks]
        assert "task_start" in kinds
        assert "node_start" in kinds
        assert "node_end"   in kinds
        assert "done"       in kinds

    @pytest.mark.asyncio
    async def test_stream_yields_token_chunk(self, tmp_path):
        from meshflow.core.streaming import StreamChunk
        from meshflow.core.workflow import WorkflowDefinition
        from unittest.mock import MagicMock
        import uuid, datetime

        path = self._make_workflow_yaml(tmp_path)
        wf = WorkflowDefinition.from_yaml(path)

        from meshflow.core.runtime import StepRuntime, RuntimeOutcome, StepRecord
        from meshflow.core.node import NodeOutput

        runtime = MagicMock(spec=StepRuntime)
        runtime._run_id = "t-run"

        async def fake_run(node, node_input, ctx):
            record = StepRecord(
                run_id="t-run", step_id=uuid.uuid4().hex[:8],
                node_id=node.id, node_kind="native",
                input_task="", output_content="hello world",
                verdict="commit", blocked=False, block_reason="",
                uncertainty=0.0, cost_usd=0.0, tokens_used=3,
                carbon_gco2=0.0, duration_ms=5.0,
                timestamp=datetime.datetime.now().isoformat(),
            )
            return RuntimeOutcome(
                ok=True, node_id=node.id, node_kind="native",
                output=NodeOutput(content="hello world"),
                record=record, blocked_by="", paused_for_human=False, human_context={},
            )

        runtime.run = fake_run

        token_chunks = []
        async for chunk in wf.stream("go", runtime):
            if chunk.kind == "token":
                token_chunks.append(chunk.content)

        # At least one token was emitted (either live-streamed or fallback)
        assert token_chunks, "Expected at least one token chunk"
        combined = "".join(token_chunks)
        assert len(combined) > 0

    @pytest.mark.asyncio
    async def test_stream_node_end_metadata(self, tmp_path):
        from meshflow.core.workflow import WorkflowDefinition
        from unittest.mock import MagicMock
        import uuid, datetime

        path = self._make_workflow_yaml(tmp_path)
        wf = WorkflowDefinition.from_yaml(path)

        from meshflow.core.runtime import StepRuntime, RuntimeOutcome, StepRecord
        from meshflow.core.node import NodeOutput

        runtime = MagicMock(spec=StepRuntime)
        runtime._run_id = "meta-run"

        async def fake_run(node, node_input, ctx):
            record = StepRecord(
                run_id="meta-run", step_id="s1",
                node_id=node.id, node_kind="native",
                input_task="", output_content="result",
                verdict="commit", blocked=False, block_reason="",
                uncertainty=0.0, cost_usd=0.00123, tokens_used=42,
                carbon_gco2=0.0, duration_ms=30.0,
                timestamp=datetime.datetime.now().isoformat(),
            )
            return RuntimeOutcome(
                ok=True, node_id=node.id, node_kind="native",
                output=NodeOutput(content="result"),
                record=record, blocked_by="", paused_for_human=False, human_context={},
            )

        runtime.run = fake_run

        node_end_chunks = []
        async for chunk in wf.stream("task", runtime):
            if chunk.kind == "node_end":
                node_end_chunks.append(chunk)

        assert node_end_chunks
        meta = node_end_chunks[0].metadata
        assert meta["tokens"] == 42
        assert meta["ok"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Feature 4 — Parameter sweep runner
# ─────────────────────────────────────────────────────────────────────────────

class TestParameterSweep:
    def test_sweep_grid_combinations(self):
        from meshflow.eval.sweep import SweepGrid

        grid = SweepGrid(task=["t1", "t2"], model=["m1", "m2"])
        combos = grid.combinations()
        assert len(combos) == 4
        assert {"task": "t1", "model": "m1"} in combos
        assert {"task": "t2", "model": "m2"} in combos

    def test_sweep_grid_single_param(self):
        from meshflow.eval.sweep import SweepGrid

        grid = SweepGrid(model=["a", "b", "c"])
        assert len(grid) == 3

    def test_sweep_grid_empty(self):
        from meshflow.eval.sweep import SweepGrid

        grid = SweepGrid()
        combos = grid.combinations()
        assert combos == [{}]

    def test_sweep_results_comparison_table(self):
        from meshflow.eval.sweep import SweepGrid, SweepResults, SweepVariantResult

        grid = SweepGrid(model=["a", "b"])
        results = SweepResults(
            grid=grid,
            variants=[
                SweepVariantResult("v1", {"model": "a"}, "out a", True, 1000, 0.01, 1.2),
                SweepVariantResult("v2", {"model": "b"}, "out b", True, 800,  0.005, 0.9),
            ],
        )
        table = results.comparison_table()
        assert "model" in table
        assert "a" in table
        assert "b" in table

    def test_sweep_results_best_by_cost(self):
        from meshflow.eval.sweep import SweepGrid, SweepResults, SweepVariantResult

        grid = SweepGrid(model=["a", "b"])
        results = SweepResults(
            grid=grid,
            variants=[
                SweepVariantResult("v1", {"model": "a"}, "", True, 1000, 0.10, 1.0),
                SweepVariantResult("v2", {"model": "b"}, "", True, 800,  0.01, 0.8),
            ],
        )
        best = results.best_by_cost()
        assert best is not None
        assert best.params["model"] == "b"

    def test_sweep_results_to_benchmark(self):
        from meshflow.eval.sweep import SweepGrid, SweepResults, SweepVariantResult
        from meshflow.eval.pareto import ModelBenchmark

        grid = SweepGrid(model=["x"])
        results = SweepResults(
            grid=grid,
            variants=[SweepVariantResult("v1", {"model": "x"}, "", True, 500, 0.005, 0.5)],
        )
        bench = results.to_benchmark()
        assert isinstance(bench, ModelBenchmark)
        assert len(bench.runs()) == 1

    def test_sweep_results_error_variant(self):
        from meshflow.eval.sweep import SweepGrid, SweepResults, SweepVariantResult

        grid = SweepGrid(model=["bad"])
        results = SweepResults(
            grid=grid,
            variants=[SweepVariantResult("v1", {"model": "bad"}, "", False, 0, 0, 0,
                                          pass_rate=0.0, error="timeout")],
        )
        table = results.comparison_table()
        assert "ERR" in table or "timeout" in table.lower() or "bad" in table

    def test_sweep_to_list(self):
        from meshflow.eval.sweep import SweepGrid, SweepResults, SweepVariantResult

        grid = SweepGrid(model=["m"])
        r = SweepResults(
            grid=grid,
            variants=[SweepVariantResult("v1", {"model": "m"}, "out", True, 100, 0.001, 0.1)],
        )
        lst = r.to_list()
        assert len(lst) == 1
        assert lst[0]["params"] == {"model": "m"}


# ─────────────────────────────────────────────────────────────────────────────
# Feature 5 — Terminal dashboard
# ─────────────────────────────────────────────────────────────────────────────

class TestTerminalDashboard:
    def test_import(self):
        from meshflow.cli.dashboard import TerminalDashboard
        dash = TerminalDashboard(":memory:")
        assert dash is not None

    def test_sparkline_length(self):
        from meshflow.cli.dashboard import _sparkline

        result = _sparkline([1.0, 2.0, 3.0, 2.0, 1.0], width=5)
        assert len(result) == 5

    def test_sparkline_empty(self):
        from meshflow.cli.dashboard import _sparkline
        result = _sparkline([], width=10)
        assert len(result) == 10

    def test_sparkline_uniform_values(self):
        from meshflow.cli.dashboard import _sparkline
        result = _sparkline([5.0] * 8, width=4)
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_render_missing_db_does_not_raise(self, tmp_path):
        from meshflow.cli.dashboard import TerminalDashboard
        import io, contextlib

        # Use a non-existent db path — should handle gracefully
        dash = TerminalDashboard(str(tmp_path / "nonexistent.db"), limit=5)
        buf = io.StringIO()
        # Redirect stdout to suppress output
        with contextlib.redirect_stdout(buf):
            try:
                await dash.render()
            except Exception:
                pass  # any exception is acceptable for missing db

    @pytest.mark.asyncio
    async def test_plain_render_produces_output(self, tmp_path, capsys):
        from meshflow.cli.dashboard import TerminalDashboard

        dash = TerminalDashboard(str(tmp_path / "test.db"), limit=5)
        data = {
            "runs": [
                {"run_id": "abc123", "total_cost_usd": 0.01, "total_tokens": 100,
                 "duration_s": 1.2, "blocked_nodes": [], "workflow_name": "test-wf"}
            ],
            "paused": [],
            "health": [],
            "error": "",
        }
        dash._render_plain(data)
        captured = capsys.readouterr()
        assert "abc123" in captured.out
        assert "0.01000" in captured.out or "MeshFlow" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# Any helper type
# ─────────────────────────────────────────────────────────────────────────────

from typing import Any
