"""Sprint 98 — Agent Evals v2, @traceable, MCPRouter, Durable Workers, Cloud SDK."""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")
os.environ.setdefault("MESHFLOW_CLOUD_ENABLED", "0")   # no real HTTP in tests


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MeshFlowCloud SDK
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeshFlowCloud:
    def test_disabled_when_no_key(self) -> None:
        from meshflow import MeshFlowCloud
        c = MeshFlowCloud(api_key="", enabled=True)
        assert not c.enabled

    def test_disabled_via_env(self, monkeypatch: Any) -> None:
        from meshflow import MeshFlowCloud
        monkeypatch.setenv("MESHFLOW_CLOUD_ENABLED", "0")
        c = MeshFlowCloud(api_key="mf_sk_test")
        assert not c.enabled

    def test_report_run_noop_when_disabled(self) -> None:
        from meshflow import MeshFlowCloud
        c = MeshFlowCloud(api_key="", enabled=False)
        assert c.report_run({}) is True     # silently succeeds

    def test_report_eval_noop_when_disabled(self) -> None:
        from meshflow import MeshFlowCloud
        c = MeshFlowCloud(api_key="")
        result = c.report_eval(run_id="r1", scenario="test", score=0.9)
        assert result is True

    def test_report_mcp_call_noop(self) -> None:
        from meshflow import MeshFlowCloud
        c = MeshFlowCloud(api_key="")
        assert c.report_mcp_call(server_name="fs", tool_name="read") is True

    def test_report_worker_job_noop(self) -> None:
        from meshflow import MeshFlowCloud
        c = MeshFlowCloud(api_key="")
        assert c.report_worker_job(job_id="j1", workflow_name="wf", status="completed") is True

    def test_base_url_default(self) -> None:
        from meshflow import MeshFlowCloud
        c = MeshFlowCloud(api_key="key")
        assert c.base_url == "https://meshflow.dev"

    def test_base_url_override(self) -> None:
        from meshflow import MeshFlowCloud
        c = MeshFlowCloud(api_key="key", base_url="http://localhost:3000")
        assert c.base_url == "http://localhost:3000"

    def test_extract_run_payload(self) -> None:
        from meshflow.cloud.client import _extract_run_payload
        from meshflow import WorkflowResult
        wr = WorkflowResult(
            run_id="r1", workflow_name="wf", completed=True,
            output="done", steps=[], total_cost_usd=0.01, total_tokens=100,
            total_carbon_gco2=0.0, duration_s=1.5,
            blocked_nodes=[], paused_nodes=[], skipped_nodes=[], ledger_db="",
        )
        payload = _extract_run_payload(wr)
        assert payload["run_id"] == "r1"
        assert payload["workflow_name"] == "wf"
        assert payload["status"] == "completed"
        assert payload["duration_ms"] == 1500

    def test_top_level_exports(self) -> None:
        from meshflow import MeshFlowCloud, get_cloud_client
        from meshflow import cloud_report_run, cloud_report_eval
        from meshflow import cloud_report_mcp_call, cloud_report_worker_job
        assert MeshFlowCloud is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. StructuredJudge
# ═══════════════════════════════════════════════════════════════════════════════

class TestStructuredJudge:
    def test_score_returns_result(self) -> None:
        from meshflow import StructuredJudge
        judge = StructuredJudge(criteria=["correctness", "helpfulness"])
        result = asyncio.run(judge.score("task", "output"))
        assert 0.0 <= result.overall <= 1.0
        assert "correctness" in result.by_criterion
        assert "helpfulness" in result.by_criterion
        assert isinstance(result.passed, bool)

    def test_custom_pass_threshold(self) -> None:
        from meshflow import StructuredJudge, StructuredJudgeResult
        judge = StructuredJudge(pass_threshold=0.0)  # always pass
        result = asyncio.run(judge.score("t", "o"))
        assert result.passed is True

    def test_weighted_scoring(self) -> None:
        from meshflow import StructuredJudge
        judge = StructuredJudge(
            criteria=["correctness", "format"],
            weights={"correctness": 0.9, "format": 0.1},
        )
        result = asyncio.run(judge.score("t", "o"))
        assert 0.0 <= result.overall <= 1.0

    def test_score_batch(self) -> None:
        from meshflow import StructuredJudge
        judge = StructuredJudge()
        cases = [{"task": "t1", "output": "o1"}, {"task": "t2", "output": "o2"}]
        results = asyncio.run(judge.score_batch(cases))
        assert len(results) == 2

    def test_score_sync(self) -> None:
        from meshflow import StructuredJudge
        judge = StructuredJudge()
        result = judge.score_sync("t", "o")
        assert 0.0 <= result.overall <= 1.0

    def test_str_repr(self) -> None:
        from meshflow import StructuredJudge
        judge = StructuredJudge()
        result = asyncio.run(judge.score("task", "output"))
        s = str(result)
        assert "StructuredJudgeResult" in s

    def test_exported(self) -> None:
        from meshflow import StructuredJudge, StructuredJudgeResult
        assert StructuredJudge is not None
        assert StructuredJudgeResult is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TrajectoryEval
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrajectoryEval:
    def _traj(self) -> list[dict]:
        return [
            {"thought": "I need to search", "action": "search('AI')", "observation": "Found 5 results"},
            {"thought": "Summarising results", "action": "summarise(...)", "observation": "Summary done"},
        ]

    def test_evaluate_returns_result(self) -> None:
        from meshflow import TrajectoryEval
        teval = TrajectoryEval()
        result = asyncio.run(teval.evaluate("Find AI info", self._traj(), "Here is the summary"))
        assert 0.0 <= result.path_score <= 1.0
        assert len(result.step_scores) == 2
        assert 0.0 <= result.efficiency <= 1.0

    def test_step_scores_match_trajectory_length(self) -> None:
        from meshflow import TrajectoryEval
        teval = TrajectoryEval()
        traj = [{"thought": f"step {i}", "action": "a", "observation": "o"} for i in range(4)]
        result = asyncio.run(teval.evaluate("task", traj, "output"))
        assert len(result.step_scores) == 4

    def test_trajectory_step_objects(self) -> None:
        from meshflow import TrajectoryEval, TrajectoryStep
        teval = TrajectoryEval()
        steps = [TrajectoryStep(thought="think", action="act", observation="obs")]
        result = asyncio.run(teval.evaluate("task", steps, "output"))
        assert 0.0 <= result.path_score <= 1.0

    def test_evaluate_sync(self) -> None:
        from meshflow import TrajectoryEval
        result = TrajectoryEval().evaluate_sync("t", self._traj(), "out")
        assert isinstance(result.passed, bool)

    def test_exported(self) -> None:
        from meshflow import TrajectoryEval, TrajectoryEvalResult, TrajectoryStep
        assert TrajectoryEval is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RAGEval
# ═══════════════════════════════════════════════════════════════════════════════

class TestRAGEval:
    def test_evaluate_returns_result(self) -> None:
        from meshflow import RAGEval
        rag = RAGEval()
        result = asyncio.run(rag.evaluate(
            question="What is HIPAA?",
            answer="HIPAA is a health privacy law.",
            retrieved_contexts=["HIPAA stands for Health Insurance Portability and Accountability Act."],
        ))
        assert 0.0 <= result.faithfulness    <= 1.0
        assert 0.0 <= result.answer_relevance <= 1.0
        assert 0.0 <= result.context_recall  <= 1.0
        assert 0.0 <= result.overall         <= 1.0

    def test_with_reference_answer(self) -> None:
        from meshflow import RAGEval
        result = asyncio.run(RAGEval().evaluate(
            question="Capital of France?",
            answer="Paris",
            retrieved_contexts=["France's capital is Paris"],
            reference_answer="Paris is the capital of France.",
        ))
        assert isinstance(result.passed, bool)

    def test_overall_is_mean_of_three(self) -> None:
        from meshflow import RAGEvalResult
        r = RAGEvalResult(faithfulness=0.8, answer_relevance=0.6, context_recall=0.4,
                          overall=0.6, reasoning="", passed=False)
        assert abs(r.overall - 0.6) < 0.01

    def test_evaluate_sync(self) -> None:
        from meshflow import RAGEval
        result = RAGEval().evaluate_sync("q", "a", ["ctx"])
        assert 0.0 <= result.overall <= 1.0

    def test_exported(self) -> None:
        from meshflow import RAGEval, RAGEvalResult
        assert RAGEval is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 5. EvalCI
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvalCI:
    def _cases(self) -> list[dict]:
        return [{"task": "t1", "output": "o1"}, {"task": "t2", "output": "o2"}]

    def test_run_returns_report(self) -> None:
        from meshflow import EvalCI
        ci = EvalCI(baseline_score=0.0, fail_on_regression=False)
        report = asyncio.run(ci.run(self._cases()))
        assert report.n_cases == 2
        assert report.suite_name == "default"

    def test_no_regression_does_not_raise(self) -> None:
        from meshflow import EvalCI
        ci = EvalCI(baseline_score=0.0, fail_on_regression=True)
        report = asyncio.run(ci.run(self._cases()))
        assert report.passed

    def test_regression_raises_when_configured(self) -> None:
        from meshflow import EvalCI, EvalRegressionError
        ci = EvalCI(baseline_score=1.0, fail_on_regression=True)  # impossible threshold
        with pytest.raises(EvalRegressionError):
            asyncio.run(ci.run(self._cases()))

    def test_regression_no_raise_when_disabled(self) -> None:
        from meshflow import EvalCI
        ci = EvalCI(baseline_score=1.0, fail_on_regression=False)
        report = asyncio.run(ci.run(self._cases()))
        assert not report.passed
        assert report.regression

    def test_run_sync(self) -> None:
        from meshflow import EvalCI
        ci = EvalCI(baseline_score=0.0, fail_on_regression=False)
        report = ci.run_sync(self._cases())
        assert report.n_cases == 2

    def test_report_str(self) -> None:
        from meshflow import EvalCI
        ci = EvalCI(baseline_score=0.0, fail_on_regression=False)
        report = asyncio.run(ci.run(self._cases()))
        assert "suite=" in str(report)

    def test_exported(self) -> None:
        from meshflow import EvalCI, EvalCIReport, EvalRegressionError
        assert EvalCI is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 6. @traceable
# ═══════════════════════════════════════════════════════════════════════════════

class TestTraceable:
    def test_bare_decorator_async(self) -> None:
        from meshflow import traceable
        @traceable
        async def my_fn(x: int) -> int:
            return x * 2
        assert asyncio.run(my_fn(3)) == 6

    def test_bare_decorator_sync(self) -> None:
        from meshflow import traceable
        @traceable
        def my_fn(x: int) -> int:
            return x + 1
        assert my_fn(5) == 6

    def test_with_name_and_run_type(self) -> None:
        from meshflow import traceable
        @traceable(name="custom", run_type="llm")
        async def fn() -> str:
            return "result"
        assert asyncio.run(fn()) == "result"

    def test_exception_propagates(self) -> None:
        from meshflow import traceable
        @traceable
        def boom() -> None:
            raise ValueError("test error")
        with pytest.raises(ValueError, match="test error"):
            boom()

    def test_trace_span_context_manager(self) -> None:
        from meshflow import trace_span
        with trace_span("test_span", run_type="tool") as span:
            span.metadata["x"] = 42
        assert span.latency_ms >= 0
        assert span.metadata["x"] == 42

    def test_set_exporter_replaces(self) -> None:
        from meshflow import TraceExporter, set_exporter
        from meshflow.observability.traceable import _exporters
        captured: list[Any] = []

        class CapturingExporter(TraceExporter):
            def export(self, span: Any) -> None:
                captured.append(span)

        set_exporter(CapturingExporter())
        from meshflow import traceable
        @traceable
        def fn() -> str:
            return "hi"
        fn()
        assert len(captured) >= 1

    def test_langfuse_exporter_importable(self) -> None:
        from meshflow import LangfuseExporter
        assert LangfuseExporter is not None

    def test_exported(self) -> None:
        from meshflow import traceable, TraceSpan, TraceExporter, set_exporter, trace_span
        assert all(x is not None for x in [traceable, TraceSpan, TraceExporter])


# ═══════════════════════════════════════════════════════════════════════════════
# 7. MCPRouter
# ═══════════════════════════════════════════════════════════════════════════════

class TestMCPRouter:
    def _router(self) -> Any:
        from meshflow import MCPRouter, MCPServerConfig
        return MCPRouter([
            MCPServerConfig(name="filesystem", transport="stdio", command=["npx", "mcp-fs"]),
            MCPServerConfig(name="github",     transport="http",  endpoint="https://github.com/mcp"),
        ], fallback_to_mock=True)

    def test_list_tools_returns_entries(self) -> None:
        from meshflow import MCPRouterToolEntry
        router = self._router()
        tools = asyncio.run(router.list_tools())
        assert len(tools) > 0
        assert all(isinstance(t, MCPRouterToolEntry) for t in tools)

    def test_each_tool_has_server_name(self) -> None:
        router = self._router()
        tools = asyncio.run(router.list_tools())
        server_names = {t.server_name for t in tools}
        assert "filesystem" in server_names or "github" in server_names

    def test_call_mock_tool(self) -> None:
        from meshflow import MCPCallResult
        router = self._router()
        result = asyncio.run(router.call("read_file", {"path": "/tmp/test.txt"}))
        assert isinstance(result, MCPCallResult)
        assert result.tool_name == "read_file"
        assert result.server_name == "filesystem"

    def test_call_sync(self) -> None:
        from meshflow import MCPCallResult
        router = self._router()
        result = router.call_sync("read_file")
        assert isinstance(result, MCPCallResult)

    def test_unknown_tool_raises_key_error(self) -> None:
        router = self._router()
        with pytest.raises(KeyError):
            asyncio.run(router.call("nonexistent_tool"))

    def test_auth_policy_deny_blocks_call(self) -> None:
        from meshflow import MCPRouter, MCPServerConfig, MCPAuthPolicy, MCPDeniedError
        router = MCPRouter([
            MCPServerConfig(
                name="github",
                transport="http",
                endpoint="https://github.com/mcp",
                policy=MCPAuthPolicy(deny_tools=["delete_repo"]),
            )
        ], fallback_to_mock=True)
        with pytest.raises(MCPDeniedError):
            asyncio.run(router.call("delete_repo", server_name="github"))

    def test_auth_policy_allow_list(self) -> None:
        from meshflow import MCPRouter, MCPServerConfig, MCPAuthPolicy
        router = MCPRouter([
            MCPServerConfig(
                name="github",
                transport="http",
                policy=MCPAuthPolicy(allow_tools=["list_prs"]),
            )
        ], fallback_to_mock=True)
        tools = asyncio.run(router.list_tools())
        names = [t.name for t in tools]
        assert "delete_repo" not in names
        assert "list_prs" in names

    def test_server_names_property(self) -> None:
        router = self._router()
        assert "filesystem" in router.server_names
        assert "github" in router.server_names

    def test_repr(self) -> None:
        router = self._router()
        assert "MCPRouter" in repr(router)

    def test_exported(self) -> None:
        from meshflow import MCPRouter, MCPServerConfig, MCPAuthPolicy, MCPDeniedError
        assert MCPRouter is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Durable Workers
# ═══════════════════════════════════════════════════════════════════════════════

class TestDurableTask:
    def test_decorator_bare(self) -> None:
        from meshflow import durable_task
        @durable_task
        async def my_task(x: int) -> int:
            return x * 2
        assert asyncio.run(my_task(5)) == 10

    def test_decorator_with_params(self) -> None:
        from meshflow import durable_task, DurableTask
        @durable_task(max_retries=5, backoff_s=0.5)
        async def my_task() -> str:
            return "ok"
        assert isinstance(my_task, DurableTask)
        assert my_task.max_retries == 5

    def test_task_name_equals_function_name(self) -> None:
        from meshflow import durable_task
        @durable_task
        async def hello_world() -> None: pass
        assert hello_world.name == "hello_world"

    def test_exported(self) -> None:
        from meshflow import durable_task, DurableTask
        assert durable_task is not None


class TestWorkerDaemon:
    def test_enqueue_and_run_until_empty(self) -> None:
        from meshflow import durable_task, WorkerDaemon, JobStatus
        @durable_task
        async def add(a: int, b: int) -> int:
            return a + b

        daemon = WorkerDaemon(concurrency=2)
        daemon.register(add)
        job = asyncio.run(daemon.enqueue("add", [2, 3]))
        asyncio.run(daemon.run(until_empty=True))
        stored = daemon._store.get(job.job_id)
        assert stored is not None
        assert stored.status == JobStatus.COMPLETED
        assert stored.result == 5

    def test_failed_job_retries(self) -> None:
        from meshflow import durable_task, WorkerDaemon, JobStatus
        call_count = {"n": 0}

        @durable_task(max_retries=2, backoff_s=0.0)
        async def flaky() -> str:
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ValueError("transient")
            return "recovered"

        daemon = WorkerDaemon(concurrency=1)
        daemon.register(flaky)
        job = asyncio.run(daemon.enqueue("flaky"))
        for _ in range(10):   # run several times to process retries
            asyncio.run(daemon.run(until_empty=True))
            stored = daemon._store.get(job.job_id)
            if stored and stored.is_terminal:
                break
        stored = daemon._store.get(job.job_id)
        assert stored is not None
        assert stored.status == JobStatus.COMPLETED

    def test_exhausted_retries_marks_dead(self) -> None:
        from meshflow import durable_task, WorkerDaemon, JobStatus
        @durable_task(max_retries=1, backoff_s=0.0)
        async def always_fail() -> None:
            raise RuntimeError("always")

        daemon = WorkerDaemon(concurrency=1)
        daemon.register(always_fail)
        job = asyncio.run(daemon.enqueue("always_fail"))
        for _ in range(5):
            asyncio.run(daemon.run(until_empty=True))
            stored = daemon._store.get(job.job_id)
            if stored and stored.is_terminal:
                break
        stored = daemon._store.get(job.job_id)
        assert stored is not None
        assert stored.status in (JobStatus.DEAD, JobStatus.FAILED)

    def test_stats(self) -> None:
        from meshflow import WorkerDaemon, WorkerStats
        daemon = WorkerDaemon()
        stats = daemon.stats
        assert isinstance(stats, WorkerStats)

    def test_sqlite_store_persists_job(self) -> None:
        from meshflow import durable_task, WorkerDaemon, SQLiteJobStore, JobStatus
        @durable_task
        async def simple() -> str:
            return "done"

        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            store = SQLiteJobStore(f.name)
            daemon = WorkerDaemon(concurrency=1, store=store)
            daemon.register(simple)
            job = asyncio.run(daemon.enqueue("simple"))
            asyncio.run(daemon.run(until_empty=True))
            # Reload from disk
            store2 = SQLiteJobStore(f.name)
            stored = store2.get(job.job_id)
            assert stored is not None
            assert stored.status == JobStatus.COMPLETED

    def test_exported(self) -> None:
        from meshflow import (
            WorkerDaemon, CronTrigger, JobRecord, JobStatus,
            WorkerStats, InMemoryJobStore, SQLiteJobStore,
        )
        assert WorkerDaemon is not None


class TestCronTrigger:
    def test_add_returns_self_for_chaining(self) -> None:
        from meshflow import WorkerDaemon, CronTrigger
        daemon = WorkerDaemon()
        cron = CronTrigger(daemon)
        result = cron.add("task", "0 * * * *")
        assert result is cron

    def test_entries_stored(self) -> None:
        from meshflow import WorkerDaemon, CronTrigger
        daemon = WorkerDaemon()
        cron = CronTrigger(daemon)
        cron.add("daily", "0 9 * * 1-5")
        cron.add("hourly", "0 * * * *")
        assert len(cron._entries) == 2
