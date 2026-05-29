"""Tests for 6 developer-experience features.

1  Anthropic Batches API (BatchCompletion + batch_complete)
2  Workflow YAML linter  (lint_workflow_yaml)
3  Workflow diff         (workflow_diff + workflow_diff_objects)
4  Agent composition     (pipe, parallel, AgentPipeline)
5  Test utilities        (MockNode, assertions, helpers)
6  Memory CLI export/import (snapshot round-trip)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import pytest
import yaml as _yaml


# ─────────────────────────────────────────────────────────────────────────────
# 1. Anthropic Batches API
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchCompletion:
    def test_request_to_anthropic_params(self):
        from meshflow.agents.batch_completions import BatchCompletionRequest
        req = BatchCompletionRequest(
            messages=[{"role": "user", "content": "Hi"}],
            model="claude-haiku-4-5-20251001",
            system="Be helpful",
            custom_id="test-001",
        )
        params = req.to_anthropic_params()
        assert params["custom_id"] == "test-001"
        assert params["params"]["model"] == "claude-haiku-4-5-20251001"
        assert params["params"]["system"] == "Be helpful"

    def test_request_custom_id_auto_generated(self):
        from meshflow.agents.batch_completions import BatchCompletionRequest
        r = BatchCompletionRequest(messages=[{"role": "user", "content": "x"}])
        assert r.custom_id  # non-empty
        assert len(r.custom_id) == 12

    def test_batch_result_fields(self):
        from meshflow.agents.batch_completions import BatchCompletionResult
        r = BatchCompletionResult(custom_id="abc", success=True, content="hello",
                                  input_tokens=10, output_tokens=5, cost_usd=0.001)
        assert r.success
        assert r.content == "hello"

    def test_batch_result_failure(self):
        from meshflow.agents.batch_completions import BatchCompletionResult
        r = BatchCompletionResult(custom_id="x", success=False, error="timeout")
        assert not r.success
        assert "timeout" in r.error

    @pytest.mark.asyncio
    async def test_sequential_fallback_no_api_key(self):
        """Without an API key the sequential fallback should handle ImportError or auth error."""
        from meshflow.agents.batch_completions import BatchCompletion, BatchCompletionRequest

        requests = [
            BatchCompletionRequest(
                messages=[{"role": "user", "content": "ping"}],
                model="claude-haiku-4-5-20251001",
                custom_id="ping-01",
            )
        ]
        client = BatchCompletion(api_key="invalid-key-for-test", fallback_sequential=True)
        # Should return error results, not raise
        results = await client._run_sequential(requests)
        assert len(results) == 1
        # Either success (unlikely) or failure with an error
        assert results[0].custom_id == "ping-01"

    @pytest.mark.asyncio
    async def test_empty_requests_returns_empty(self):
        from meshflow.agents.batch_completions import BatchCompletion
        client = BatchCompletion()
        results = await client.run([])
        assert results == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. Workflow YAML linter
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowLinter:
    def _write_yaml(self, tmp_path, data):
        p = tmp_path / "wf.yaml"
        p.write_text(_yaml.safe_dump(data))
        return str(p)

    def test_valid_workflow_no_issues(self, tmp_path):
        from meshflow.core.lint import lint_workflow_yaml
        data = {
            "name": "test-wf",
            "nodes": {
                "step_a": {"kind": "native", "role": "executor"},
                "step_b": {"kind": "native", "role": "executor"},
            },
            "edges": ["step_a -> step_b"],
            "terminal": ["step_b"],
        }
        path = self._write_yaml(tmp_path, data)
        issues = lint_workflow_yaml(path)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_undefined_edge_target_is_error(self, tmp_path):
        from meshflow.core.lint import lint_workflow_yaml
        data = {
            "name": "bad",
            "nodes": {"a": {"kind": "native"}},
            "edges": ["a -> nonexistent"],
        }
        path = self._write_yaml(tmp_path, data)
        issues = lint_workflow_yaml(path)
        errors = [i for i in issues if i.severity == "error"]
        assert any("nonexistent" in e.message for e in errors)

    def test_unknown_node_kind_is_error(self, tmp_path):
        from meshflow.core.lint import lint_workflow_yaml
        data = {
            "name": "bad",
            "nodes": {"a": {"kind": "invalid_kind"}},
            "edges": [],
        }
        path = self._write_yaml(tmp_path, data)
        issues = lint_workflow_yaml(path)
        assert any("invalid_kind" in i.message for i in issues if i.severity == "error")

    def test_http_node_missing_url_is_error(self, tmp_path):
        from meshflow.core.lint import lint_workflow_yaml
        data = {
            "name": "bad",
            "nodes": {"http_step": {"kind": "http"}},
            "edges": [],
        }
        path = self._write_yaml(tmp_path, data)
        issues = lint_workflow_yaml(path)
        assert any("url" in i.message.lower() for i in issues if i.severity == "error")

    def test_missing_name_is_warning(self, tmp_path):
        from meshflow.core.lint import lint_workflow_yaml
        data = {"nodes": {"a": {"kind": "native"}}, "edges": []}
        path = self._write_yaml(tmp_path, data)
        issues = lint_workflow_yaml(path)
        assert any(i.severity == "warning" and "name" in i.path for i in issues)

    def test_nonexistent_file_returns_error(self):
        from meshflow.core.lint import lint_workflow_yaml
        issues = lint_workflow_yaml("/no/such/file.yaml")
        assert issues and issues[0].severity == "error"

    def test_lint_result_summary(self, tmp_path):
        from meshflow.core.lint import lint_workflow_yaml, LintResult
        data = {"name": "ok", "nodes": {"a": {"kind": "native"}}, "edges": []}
        path = self._write_yaml(tmp_path, data)
        issues = lint_workflow_yaml(path)
        result = LintResult(yaml_path=path, issues=issues)
        assert result.ok
        summary = result.summary()
        assert "PASS" in summary

    def test_condition_syntax_error_is_warning(self, tmp_path):
        from meshflow.core.lint import lint_workflow_yaml
        data = {
            "name": "cond-test",
            "nodes": {
                "a": {"kind": "native"},
                "b": {"kind": "native"},
            },
            # Unbalanced parens → definite syntax error
            "edges": [{"from": "a", "to": "b", "condition": "confidence > (0.8"}],
        }
        path = self._write_yaml(tmp_path, data)
        issues = lint_workflow_yaml(path)
        warns = [i for i in issues if i.severity == "warning" and "condition" in i.path]
        assert warns  # syntax error detected

    def test_lint_workflow_data_api(self):
        from meshflow.core.lint import lint_workflow_data
        data = {
            "name": "inline",
            "nodes": {"x": {"kind": "native"}},
            "edges": [],
        }
        issues = lint_workflow_data(data)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Workflow diff
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowDiff:
    def _write_yaml(self, tmp_path, data, name="wf.yaml"):
        p = tmp_path / name
        p.write_text(_yaml.safe_dump(data))
        return str(p)

    def test_no_changes_same_file(self, tmp_path):
        from meshflow.core.diff import workflow_diff
        data = {"name": "wf", "nodes": {"a": {"kind": "native"}}, "edges": []}
        path = self._write_yaml(tmp_path, data)
        result = workflow_diff(path, path)
        assert not result.has_changes

    def test_node_added(self, tmp_path):
        from meshflow.core.diff import workflow_diff
        data_a = {"name": "wf", "nodes": {"a": {"kind": "native"}}, "edges": []}
        data_b = {"name": "wf", "nodes": {"a": {"kind": "native"}, "b": {"kind": "native"}}, "edges": []}
        path_a = self._write_yaml(tmp_path, data_a, "a.yaml")
        path_b = self._write_yaml(tmp_path, data_b, "b.yaml")
        result = workflow_diff(path_a, path_b)
        assert "b" in result.nodes_added

    def test_node_removed(self, tmp_path):
        from meshflow.core.diff import workflow_diff
        data_a = {"name": "wf", "nodes": {"a": {"kind": "native"}, "b": {"kind": "native"}}, "edges": []}
        data_b = {"name": "wf", "nodes": {"a": {"kind": "native"}}, "edges": []}
        path_a = self._write_yaml(tmp_path, data_a, "a.yaml")
        path_b = self._write_yaml(tmp_path, data_b, "b.yaml")
        result = workflow_diff(path_a, path_b)
        assert "b" in result.nodes_removed

    def test_edge_added(self, tmp_path):
        from meshflow.core.diff import workflow_diff
        data_a = {"name": "wf", "nodes": {"a": {"kind": "native"}, "b": {"kind": "native"}}, "edges": []}
        data_b = {"name": "wf", "nodes": {"a": {"kind": "native"}, "b": {"kind": "native"}}, "edges": ["a -> b"]}
        path_a = self._write_yaml(tmp_path, data_a, "a.yaml")
        path_b = self._write_yaml(tmp_path, data_b, "b.yaml")
        result = workflow_diff(path_a, path_b)
        edge_changes = [c for c in result.changes if c.kind == "edge_added"]
        assert edge_changes

    def test_policy_changed(self, tmp_path):
        from meshflow.core.diff import workflow_diff
        data_a = {"name": "wf", "nodes": {"a": {"kind": "native"}}, "edges": [], "policy": {"budget_usd": 1.0}}
        data_b = {"name": "wf", "nodes": {"a": {"kind": "native"}}, "edges": [], "policy": {"budget_usd": 5.0}}
        path_a = self._write_yaml(tmp_path, data_a, "a.yaml")
        path_b = self._write_yaml(tmp_path, data_b, "b.yaml")
        result = workflow_diff(path_a, path_b)
        pol_changes = [c for c in result.changes if c.kind == "policy_changed"]
        assert pol_changes

    def test_diff_objects(self):
        from meshflow.core.diff import workflow_diff_objects
        from meshflow.core.workflow import WorkflowDefinition
        from meshflow.testing import MockNode

        wf_a = WorkflowDefinition(name="wf")
        wf_a.add_node(MockNode("step_a"))

        wf_b = WorkflowDefinition(name="wf")
        wf_b.add_node(MockNode("step_a"))
        wf_b.add_node(MockNode("step_b"))

        result = workflow_diff_objects(wf_a, wf_b)
        assert "step_b" in result.nodes_added

    def test_diff_result_to_dict(self, tmp_path):
        from meshflow.core.diff import workflow_diff
        data = {"name": "wf", "nodes": {"a": {"kind": "native"}}, "edges": []}
        path = self._write_yaml(tmp_path, data)
        result = workflow_diff(path, path)
        d = result.to_dict()
        assert "has_changes" in d
        assert "changes" in d


# ─────────────────────────────────────────────────────────────────────────────
# 4. Agent composition
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentComposition:
    def _make_agent(self, name, response):
        from meshflow.testing import fake_agent
        return fake_agent(name, response=response)

    @pytest.mark.asyncio
    async def test_pipe_sequential(self):
        from meshflow.agents.compose import pipe
        a = self._make_agent("a", "output_a")
        b = self._make_agent("b", "output_b")
        chain = pipe(a, b)
        result = await chain("start task")
        assert result["result"] == "output_b"
        assert a.call_count == 1
        assert b.call_count == 1

    @pytest.mark.asyncio
    async def test_pipe_passes_output_as_next_task(self):
        from meshflow.agents.compose import pipe
        received_tasks = []

        class TrackAgent:
            name = "tracker"
            async def run(self, task, ctx=None):
                received_tasks.append(task)
                return {"result": f"processed:{task}"}

        source = self._make_agent("source", "intermediate")
        tracker = TrackAgent()
        chain = pipe(source, tracker)
        await chain("initial")
        # Second agent should receive "intermediate" (source's output)
        assert received_tasks[0] == "intermediate"

    @pytest.mark.asyncio
    async def test_parallel_runs_all_agents(self):
        from meshflow.agents.compose import parallel
        a = self._make_agent("alpha", "alpha result")
        b = self._make_agent("beta",  "beta result")
        results = await parallel(a, b)("same task")
        assert "alpha" in results
        assert "beta" in results
        assert results["alpha"]["result"] == "alpha result"
        assert results["beta"]["result"]  == "beta result"
        assert a.call_count == 1
        assert b.call_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_fluent_builder(self):
        from meshflow.agents.compose import AgentPipeline
        step1 = self._make_agent("s1", "from_step1")
        step2 = self._make_agent("s2", "from_step2")
        pipeline = AgentPipeline().pipe(step1).pipe(step2)
        result = await pipeline.run("go")
        assert result["result"] == "from_step2"

    @pytest.mark.asyncio
    async def test_pipeline_branch(self):
        from meshflow.agents.compose import AgentPipeline
        a = self._make_agent("a", "a_out")
        b = self._make_agent("b", "b_out")
        pipeline = AgentPipeline().branch(a, b)
        result = await pipeline.run("task")
        assert "branch_results" in result

    @pytest.mark.asyncio
    async def test_pipeline_when_condition_true(self):
        from meshflow.agents.compose import AgentPipeline
        primary  = self._make_agent("primary", "primary_out")
        on_true  = self._make_agent("true_branch", "true_out")
        on_false = self._make_agent("false_branch", "false_out")

        pipeline = AgentPipeline().pipe(primary).when(
            lambda text: "primary" in text, on_true, on_false
        )
        result = await pipeline.run("task")
        assert on_true.call_count == 1
        assert on_false.call_count == 0

    @pytest.mark.asyncio
    async def test_pipeline_when_condition_false(self):
        from meshflow.agents.compose import AgentPipeline
        primary  = self._make_agent("primary", "other output")
        on_true  = self._make_agent("t", "t")
        on_false = self._make_agent("f", "f")
        pipeline = AgentPipeline().pipe(primary).when(
            lambda text: "impossible_keyword" in text, on_true, on_false
        )
        await pipeline.run("task")
        assert on_true.call_count == 0
        assert on_false.call_count == 1

    def test_pipe_returns_piped_agents_object(self):
        from meshflow.agents.compose import pipe, _PipedAgents
        a = self._make_agent("a", "x")
        b = self._make_agent("b", "y")
        chain = pipe(a, b)
        assert isinstance(chain, _PipedAgents)

    def test_pipe_chaining_extends_pipeline(self):
        from meshflow.agents.compose import pipe
        a = self._make_agent("a", "x")
        b = self._make_agent("b", "y")
        c = self._make_agent("c", "z")
        chain = pipe(a, b).pipe(c)
        assert len(chain._agents) == 3


# ─────────────────────────────────────────────────────────────────────────────
# 5. Test utilities module
# ─────────────────────────────────────────────────────────────────────────────

class TestTestUtilities:
    @pytest.mark.asyncio
    async def test_mock_node_returns_fixed_response(self):
        from meshflow.testing import MockNode
        from meshflow.core.node import NodeInput
        node = MockNode("step", response="fixed!", tokens=42, cost=0.005)
        inp = NodeInput(task="anything")
        result = await node.run(inp)
        assert result.content == "fixed!"
        assert result.tokens_used == 42
        assert node.call_count == 1

    @pytest.mark.asyncio
    async def test_echo_node_returns_task(self):
        from meshflow.testing import EchoNode
        from meshflow.core.node import NodeInput
        node = EchoNode("echo")
        result = await node.run(NodeInput(task="hello world"))
        assert result.content == "hello world"

    @pytest.mark.asyncio
    async def test_fail_node_returns_blocked_output(self):
        from meshflow.testing import FailNode
        from meshflow.core.node import NodeInput
        node = FailNode("bad", reason="policy violation")
        result = await node.run(NodeInput(task="task"))
        assert "BLOCKED" in result.content
        assert result.metadata["blocked"] is True

    @pytest.mark.asyncio
    async def test_counter_node_accumulates(self):
        from meshflow.testing.mock_nodes import CounterNode
        from meshflow.core.node import NodeInput
        node = CounterNode("counter", tokens_per_call=5, cost_per_call=0.01)
        for _ in range(3):
            await node.run(NodeInput(task="x"))
        assert node.call_count == 3
        assert node.total_tokens == 15
        assert abs(node.total_cost - 0.03) < 1e-9

    def test_mock_node_reset(self):
        from meshflow.testing import MockNode
        from meshflow.core.node import NodeInput
        node = MockNode("n", response="r")
        asyncio.run(node.run(NodeInput(task="x")))
        node.reset()
        assert node.call_count == 0
        assert node.call_history == []

    def test_assert_completed_passes(self):
        from meshflow.testing import assert_completed
        from meshflow.core.workflow import WorkflowResult
        from meshflow.core.runtime import RuntimeOutcome, StepRecord
        from meshflow.core.node import NodeOutput
        import datetime

        dummy_record = StepRecord(
            run_id="r", step_id="s", node_id="n", node_kind="native",
            input_task="", output_content="", verdict="commit", blocked=False,
            block_reason="", uncertainty=0.0, cost_usd=0.0, tokens_used=0,
            carbon_gco2=0.0, duration_ms=0.0, timestamp=datetime.datetime.now().isoformat(),
        )
        result = WorkflowResult(
            run_id="r", workflow_name="wf", completed=True, output="done",
            steps=[RuntimeOutcome(ok=True, node_id="n", node_kind="native",
                                  output=NodeOutput(content="done"),
                                  record=dummy_record, blocked_by="",
                                  paused_for_human=False, human_context={})],
            total_cost_usd=0.001, total_tokens=10, total_carbon_gco2=0.0,
            duration_s=0.1, blocked_nodes=[], paused_nodes=[], skipped_nodes=[],
            ledger_db=":memory:",
        )
        assert_completed(result)  # should not raise

    def test_assert_completed_fails_when_blocked(self):
        from meshflow.testing import assert_completed
        from meshflow.core.workflow import WorkflowResult

        class FakeResult:
            completed = False
            blocked_nodes = ["step"]
            paused_nodes = []

        with pytest.raises(AssertionError, match="did not complete"):
            assert_completed(FakeResult())

    def test_workflow_assertion_fluent(self):
        from meshflow.testing import WorkflowAssertion

        class FakeResult:
            completed = True
            output = "Hello world"
            blocked_nodes = []
            paused_nodes = []
            total_cost_usd = 0.001
            total_tokens = 50
            steps = []

        WorkflowAssertion(FakeResult()).completed().output_contains("Hello").cost_within(1.0)

    def test_assert_cost_within_raises(self):
        from meshflow.testing import assert_cost_within

        class FakeResult:
            total_cost_usd = 5.0

        with pytest.raises(AssertionError, match="exceeds limit"):
            assert_cost_within(FakeResult(), max_usd=0.01)

    @pytest.mark.asyncio
    async def test_fake_agent_returns_fixed_response(self):
        from meshflow.testing import fake_agent
        agent = fake_agent("researcher", response="Research done")
        result = await agent.run("some task")
        assert result["result"] == "Research done"
        assert result["agent_name"] == "researcher"
        assert agent.call_count == 1

    def test_make_workflow_builds_correctly(self):
        from meshflow.testing import make_workflow, MockNode
        wf = make_workflow(
            nodes={"a": MockNode("a"), "b": MockNode("b")},
            edges=[("a", "b")],
            name="test-wf",
        )
        assert "a" in wf._nodes
        assert "b" in wf._nodes
        assert len(wf._edges) == 1

    def test_make_runtime_returns_step_runtime(self):
        from meshflow.testing import make_runtime
        from meshflow.core.runtime import StepRuntime
        runtime = make_runtime(run_id="rt-001")
        assert isinstance(runtime, StepRuntime)
        assert runtime._run_id == "rt-001"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Memory export/import round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryExportImport:
    def test_snapshot_round_trip(self):
        from meshflow.intelligence.memory import AgentMemory
        from meshflow.intelligence.memory_backends import (
            SQLiteMemoryBackend,
            snapshot_from_memory,
            restore_memory,
        )

        mem = AgentMemory("export-agent")
        mem.add("Fact 1: revenue is $12M")
        mem.add("Fact 2: growth is 18%")

        snapshot = snapshot_from_memory(mem)

        # Restore into a new memory instance
        mem2 = AgentMemory("export-agent")
        restore_memory(mem2, snapshot)

        assert mem2.working_count == 2
        recent = mem2.recent(2)
        assert any("revenue" in r for r in recent) or any("growth" in r for r in recent)

    def test_to_snapshot_from_snapshot_methods(self):
        from meshflow.intelligence.memory import AgentMemory

        mem = AgentMemory("snap-agent")
        mem.add("Data point A")
        snap = mem.to_snapshot()

        mem2 = AgentMemory("snap-agent")
        mem2.from_snapshot(snap)
        assert mem2.working_count == 1

    def test_backend_save_and_load(self, tmp_path):
        from meshflow.intelligence.memory import AgentMemory
        from meshflow.intelligence.memory_backends import (
            SQLiteMemoryBackend, snapshot_from_memory, restore_memory
        )

        db_path = str(tmp_path / "mem.db")
        backend = SQLiteMemoryBackend(db_path)
        mem = AgentMemory("agent-x")
        mem.add("Important context")
        backend.save("agent-x", snapshot_from_memory(mem))

        # Simulate new process: load from DB
        backend2 = SQLiteMemoryBackend(db_path)
        snap = backend2.load("agent-x")
        assert snap is not None
        mem2 = AgentMemory("agent-x")
        restore_memory(mem2, snap)
        assert mem2.working_count == 1

    def test_snapshot_json_serialisable(self):
        from meshflow.intelligence.memory import AgentMemory
        from meshflow.intelligence.memory_backends import snapshot_from_memory

        mem = AgentMemory("json-test")
        mem.add("Hello JSON")
        snap = snapshot_from_memory(mem)
        # Should be serialisable to JSON without errors
        json_str = json.dumps(snap)
        loaded = json.loads(json_str)
        assert loaded["agent_id"] == "json-test"
        assert len(loaded["working"]) == 1
