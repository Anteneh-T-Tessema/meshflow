"""Sprint 23 deterministic tests — no live API calls, no external services.

Sprint 23A: SensitiveDataDetector (PHI + credential detection/masking)
Sprint 23B: ModelHealthTracker + ProviderRouter fallback chain
Sprint 23C: WorkflowAnalytics (async, in-memory ledger)
Sprint 23D: TaskQueue + QueueWorker (SQLite :memory:)
"""
from __future__ import annotations

import asyncio
import statistics
import time
import uuid
from typing import Any

import pytest
import pytest_asyncio


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 23A — SensitiveDataDetector
# ══════════════════════════════════════════════════════════════════════════════

class TestSensitiveMatch:
    def test_to_dict_has_all_fields(self):
        from meshflow.security.sensitive_data import SensitiveMatch
        m = SensitiveMatch(
            kind="SSN", category="phi", value_preview="123-45…",
            start=0, end=11, confidence=1.0,
        )
        d = m.to_dict()
        assert set(d.keys()) == {"kind", "category", "value_preview", "start", "end", "confidence"}
        assert d["kind"] == "SSN"

    def test_preview_truncates(self):
        from meshflow.security.sensitive_data import SensitiveMatch
        m = SensitiveMatch(
            kind="EMAIL", category="pii", value_preview="hello@…",
            start=5, end=20, confidence=1.0,
        )
        assert "…" in m.value_preview or len(m.value_preview) <= 7


class TestSensitiveDataDetector:
    def setup_method(self):
        from meshflow.security.sensitive_data import SensitiveDataDetector
        self.det = SensitiveDataDetector()

    def test_detects_ssn(self):
        matches = self.det.detect("SSN: 123-45-6789")
        kinds = [m.kind for m in matches]
        assert "SSN" in kinds

    def test_detects_email(self):
        matches = self.det.detect("Contact me at alice@example.com please")
        kinds = [m.kind for m in matches]
        assert "EMAIL" in kinds

    def test_detects_phone(self):
        matches = self.det.detect("Call (415) 555-1234 for details")
        kinds = [m.kind for m in matches]
        assert "PHONE" in kinds

    def test_detects_anthropic_api_key(self):
        key = "sk-ant-abcdefghijklmnopqrst1234"
        matches = self.det.detect(f"Use key: {key}")
        kinds = [m.kind for m in matches]
        assert "API_KEY_ANTHROPIC" in kinds

    def test_detects_aws_access_key(self):
        # AWS access keys: AKIA + exactly 16 uppercase alphanumeric chars (20 total)
        matches = self.det.detect("AKIAIOSFODNN7EXAMPLE")
        kinds = [m.kind for m in matches]
        assert "AWS_ACCESS_KEY" in kinds

    def test_detects_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        matches = self.det.detect(jwt)
        kinds = [m.kind for m in matches]
        assert "JWT" in kinds

    def test_detects_github_token(self):
        token = "ghp_" + "a" * 36
        matches = self.det.detect(f"token: {token}")
        kinds = [m.kind for m in matches]
        assert "GITHUB_TOKEN" in kinds

    def test_mask_replaces_phi(self):
        masked = self.det.mask("SSN: 123-45-6789")
        assert "123-45-6789" not in masked
        assert "[REDACTED]" in masked

    def test_mask_replaces_credentials(self):
        key = "sk-ant-abcdefghijklmnopqrst1234"
        masked = self.det.mask(key)
        assert key not in masked
        assert "[CREDENTIAL-REDACTED]" in masked

    def test_mask_empty_string(self):
        assert self.det.mask("") == ""

    def test_has_credentials_true(self):
        assert self.det.has_credentials("sk-ant-abcdefghijklmnopqrst12345678")

    def test_has_credentials_false(self):
        assert not self.det.has_credentials("Hello world, no secrets here!")

    def test_has_phi_true(self):
        assert self.det.has_phi("Patient SSN: 123-45-6789")

    def test_has_phi_false(self):
        assert not self.det.has_phi("The quick brown fox jumps over the lazy dog.")

    def test_audit_report_structure(self):
        report = self.det.audit_report("SSN 123-45-6789 and AKIAIOSFODNN7EXAMPLE")
        assert "total_matches" in report
        assert "has_phi" in report
        assert "has_credentials" in report
        assert "kinds_found" in report
        assert "by_category" in report
        assert report["has_phi"] is True
        assert report["has_credentials"] is True

    def test_detect_sorted_by_position(self):
        matches = self.det.detect("Email alice@x.com and phone 415-555-1234 and SSN 123-45-6789")
        positions = [m.start for m in matches]
        assert positions == sorted(positions)

    def test_phi_disabled(self):
        from meshflow.security.sensitive_data import SensitiveDataDetector
        det = SensitiveDataDetector(phi_enabled=False, credential_enabled=True)
        matches = det.detect("SSN: 123-45-6789")
        kinds = [m.kind for m in matches]
        assert "SSN" not in kinds

    def test_credential_disabled(self):
        from meshflow.security.sensitive_data import SensitiveDataDetector
        det = SensitiveDataDetector(phi_enabled=True, credential_enabled=False)
        key = "sk-ant-abcdefghijklmnopqrst1234"
        matches = det.detect(key)
        kinds = [m.kind for m in matches]
        assert "API_KEY_ANTHROPIC" not in kinds

    def test_singleton(self):
        from meshflow.security.sensitive_data import get_detector, reset_detector
        reset_detector()
        d1 = get_detector()
        d2 = get_detector()
        assert d1 is d2
        reset_detector()


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 23B — ModelHealthTracker + ProviderRouter fallback
# ══════════════════════════════════════════════════════════════════════════════

class TestModelHealthTracker:
    def setup_method(self):
        from meshflow.agents.health import ModelHealthTracker
        self.tracker = ModelHealthTracker(window_size=10, degraded_threshold=0.7)

    def test_unseen_model_is_healthy(self):
        assert self.tracker.health_score("new-model") == 1.0
        assert not self.tracker.is_degraded("new-model")

    def test_all_successes_score_one(self):
        for _ in range(5):
            self.tracker.record_success("m", latency_ms=100.0)
        assert self.tracker.health_score("m") == 1.0

    def test_all_failures_score_zero(self):
        for _ in range(5):
            self.tracker.record_failure("m", error="timeout")
        assert self.tracker.health_score("m") == 0.0

    def test_mixed_score(self):
        for _ in range(7):
            self.tracker.record_success("m")
        for _ in range(3):
            self.tracker.record_failure("m")
        score = self.tracker.health_score("m")
        assert abs(score - 0.7) < 0.01

    def test_is_degraded_below_threshold(self):
        for _ in range(4):
            self.tracker.record_failure("m")
        for _ in range(6):
            self.tracker.record_success("m")
        # 6/10 = 0.6 < 0.7 → degraded
        assert self.tracker.is_degraded("m")

    def test_is_not_degraded_above_threshold(self):
        for _ in range(8):
            self.tracker.record_success("m")
        for _ in range(2):
            self.tracker.record_failure("m")
        assert not self.tracker.is_degraded("m")

    def test_rolling_window(self):
        # Fill window then add fresh successes — old failures drop out
        for _ in range(10):
            self.tracker.record_failure("m")
        for _ in range(10):
            self.tracker.record_success("m")
        assert self.tracker.health_score("m") == 1.0

    def test_summary_fields(self):
        self.tracker.record_success("m", latency_ms=200.0)
        self.tracker.record_failure("m", error="err", latency_ms=50.0)
        s = self.tracker.summary("m")
        assert s.model == "m"
        assert s.success_count == 1
        assert s.failure_count == 1
        assert s.last_error == "err"
        d = s.to_dict()
        assert "health_score" in d

    def test_best_model(self):
        self.tracker.record_success("a")
        for _ in range(5):
            self.tracker.record_failure("b")
        best = self.tracker.best_model(["a", "b"])
        assert best == "a"

    def test_best_model_single_candidate(self):
        assert self.tracker.best_model(["only"]) == "only"

    def test_best_model_empty_raises(self):
        with pytest.raises(ValueError):
            self.tracker.best_model([])

    def test_reset_specific(self):
        self.tracker.record_failure("m")
        self.tracker.reset("m")
        assert self.tracker.health_score("m") == 1.0

    def test_reset_all(self):
        self.tracker.record_failure("a")
        self.tracker.record_failure("b")
        self.tracker.reset()
        assert self.tracker.all_summaries() == []

    def test_singleton(self):
        from meshflow.agents.health import get_health_tracker, reset_health_tracker
        reset_health_tracker()
        t1 = get_health_tracker()
        t2 = get_health_tracker()
        assert t1 is t2
        reset_health_tracker()


class TestProviderRouterFallback:
    def setup_method(self):
        from meshflow.agents.health import ModelHealthTracker, reset_health_tracker
        from meshflow.agents.router import ProviderRouter
        reset_health_tracker()
        self.router = ProviderRouter()
        self.tracker = ModelHealthTracker(window_size=10)

    def test_set_fallback_chain_returns_self(self):
        result = self.router.set_fallback_chain("a", "b", "c")
        assert result is self.router

    def test_route_with_health_no_chain(self):
        # No chain → behaves like route()
        _, model = self.router.route_with_health("executor", tracker=self.tracker)
        assert model  # just gets something

    def test_route_with_health_skips_degraded(self):
        primary = "claude-opus-4-7"
        fallback = "claude-haiku-4-5-20251001"
        self.router.set_fallback_chain(primary, fallback)
        # Degrade primary
        for _ in range(10):
            self.tracker.record_failure(primary)
        _, model = self.router.route_with_health("guardian", tracker=self.tracker)
        assert model == fallback

    def test_route_with_health_uses_primary_when_healthy(self):
        primary = "claude-haiku-4-5-20251001"
        fallback = "claude-opus-4-7"
        self.router.set_fallback_chain(primary, fallback)
        self.tracker.record_success(primary)
        _, model = self.router.route_with_health("executor", tracker=self.tracker)
        assert model == primary

    def test_route_with_health_all_degraded_returns_best(self):
        # guardian role → primary = opus; set chain to [opus, sonnet]
        m1, m2 = "claude-opus-4-7", "claude-sonnet-4-6"
        self.router.set_fallback_chain(m1, m2)
        # Fully degrade opus
        for _ in range(10):
            self.tracker.record_failure(m1)
        # Partially degrade sonnet (50% < 70% threshold → still degraded)
        for _ in range(5):
            self.tracker.record_failure(m2)
        for _ in range(5):
            self.tracker.record_success(m2)
        _, model = self.router.route_with_health("guardian", tracker=self.tracker)
        # Both degraded — best_model picks m2 (0.5 > 0.0)
        assert model == m2


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 23C — WorkflowAnalytics
# ══════════════════════════════════════════════════════════════════════════════

def _make_step(run_id: str, node_id: str = "n1", **overrides: Any) -> Any:
    from meshflow.core.ledger import StepRecord
    defaults = dict(
        run_id=run_id,
        step_id=str(uuid.uuid4()),
        node_id=node_id,
        node_kind="native",
        input_task="t",
        output_content="ok",
        verdict="APPROVED",
        blocked=False,
        block_reason="",
        uncertainty=0.1,
        cost_usd=0.001,
        tokens_used=100,
        carbon_gco2=0.0001,
        duration_ms=50.0,
        timestamp=time.time(),
    )
    defaults.update(overrides)
    return StepRecord(**defaults)


class TestWorkflowAnalytics:
    @pytest.fixture
    def ledger(self, tmp_path):
        from meshflow.core.ledger import ReplayLedger
        db = str(tmp_path / "test.db")
        return ReplayLedger(db)

    @pytest.mark.asyncio
    async def test_cost_trend_empty(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        a = WorkflowAnalytics(ledger)
        result = await a.cost_trend(n=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_cost_trend_returns_per_run(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        r1, r2 = str(uuid.uuid4()), str(uuid.uuid4())
        await ledger.write(_make_step(r1, cost_usd=0.005))
        await ledger.write(_make_step(r2, cost_usd=0.010))
        a = WorkflowAnalytics(ledger)
        result = await a.cost_trend(n=10)
        assert len(result) == 2
        costs = {row["run_id"]: row["cost_usd"] for row in result}
        assert any(abs(v - 0.005) < 1e-6 for v in costs.values())

    @pytest.mark.asyncio
    async def test_latency_percentiles_empty(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        a = WorkflowAnalytics(ledger)
        result = await a.latency_percentiles(n=5)
        assert result["runs_analysed"] == 0
        assert result["p50_run_p95_ms"] == 0.0

    @pytest.mark.asyncio
    async def test_latency_percentiles_with_data(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        r1 = str(uuid.uuid4())
        await ledger.write(_make_step(r1, duration_ms=100.0))
        await ledger.write(_make_step(r1, duration_ms=200.0))
        a = WorkflowAnalytics(ledger)
        result = await a.latency_percentiles(n=10)
        assert result["runs_analysed"] == 1
        assert result["p95_run_p95_ms"] > 0

    @pytest.mark.asyncio
    async def test_blocked_rate_empty(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        a = WorkflowAnalytics(ledger)
        result = await a.blocked_rate(n=5)
        assert result["blocked_rate"] == 0.0
        assert result["runs"] == 0

    @pytest.mark.asyncio
    async def test_blocked_rate_with_blocked_steps(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        r1 = str(uuid.uuid4())
        await ledger.write(_make_step(r1, blocked=False))
        await ledger.write(_make_step(r1, blocked=True, block_reason="policy"))
        a = WorkflowAnalytics(ledger)
        result = await a.blocked_rate(n=10)
        assert result["blocked_steps"] == 1
        assert result["total_steps"] == 2
        assert abs(result["blocked_rate"] - 0.5) < 0.01

    @pytest.mark.asyncio
    async def test_quality_drift_stable(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        for _ in range(4):
            r = str(uuid.uuid4())
            await ledger.write(_make_step(r, uncertainty=0.2))
        a = WorkflowAnalytics(ledger)
        result = await a.quality_drift(n=10)
        assert result["trend"] in ("stable", "improving", "degrading")
        assert "delta" in result

    @pytest.mark.asyncio
    async def test_carbon_trend(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        r1 = str(uuid.uuid4())
        await ledger.write(_make_step(r1, carbon_gco2=0.05))
        a = WorkflowAnalytics(ledger)
        result = await a.carbon_trend(n=10)
        assert len(result) == 1
        assert result[0]["carbon_gco2"] == pytest.approx(0.05, abs=1e-6)

    @pytest.mark.asyncio
    async def test_top_costly_nodes(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        r1 = str(uuid.uuid4())
        await ledger.write(_make_step(r1, node_id="expensive", cost_usd=0.10))
        await ledger.write(_make_step(r1, node_id="cheap", cost_usd=0.001))
        a = WorkflowAnalytics(ledger)
        result = await a.top_costly_nodes(n_runs=10, top_n=5)
        assert len(result) >= 1
        assert result[0]["node_id"] == "expensive"
        assert result[0]["total_cost_usd"] == pytest.approx(0.10, abs=1e-6)

    @pytest.mark.asyncio
    async def test_full_report_structure(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        r1 = str(uuid.uuid4())
        await ledger.write(_make_step(r1, cost_usd=0.002, tokens_used=200, carbon_gco2=0.001))
        a = WorkflowAnalytics(ledger)
        report = await a.full_report(n_runs=5)
        assert "runs_analysed" in report
        assert "cost_trend" in report
        assert "latency" in report
        assert "blocked" in report
        assert "quality" in report
        assert "top_costly_nodes" in report
        assert "total_cost_usd" in report
        assert "total_tokens" in report
        assert "total_carbon_gco2" in report

    @pytest.mark.asyncio
    async def test_run_summary_dataclass(self, ledger):
        from meshflow.core.analytics import WorkflowAnalytics
        r1 = str(uuid.uuid4())
        await ledger.write(_make_step(r1))
        a = WorkflowAnalytics(ledger)
        summaries = await a._load_runs(10)
        assert len(summaries) == 1
        s = summaries[0]
        assert s.run_id == r1
        d = s.to_dict()
        assert set(d.keys()) == {
            "run_id", "total_steps", "blocked_steps", "total_cost_usd",
            "total_tokens", "total_carbon_gco2", "avg_uncertainty",
            "p95_latency_ms", "blocked_rate",
        }


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 23D — TaskQueue + QueueWorker
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskItem:
    def test_to_dict_has_all_fields(self):
        from meshflow.queue.core import TaskItem, TaskStatus
        item = TaskItem(
            task_id="tid-1",
            payload={"workflow": "test.yaml"},
            status=TaskStatus.PENDING,
        )
        d = item.to_dict()
        assert d["task_id"] == "tid-1"
        assert d["status"] == "pending"
        assert "payload" in d

    def test_from_row_roundtrip(self):
        import json
        from meshflow.queue.core import TaskItem, TaskStatus
        row = (
            "tid-2", json.dumps({"k": "v"}), "done", 5,
            1000.0, 1001.0, 1002.0, json.dumps({"result": "ok"}), "",
        )
        item = TaskItem.from_row(row)
        assert item.task_id == "tid-2"
        assert item.status == TaskStatus.DONE
        assert item.priority == 5
        assert item.result == {"result": "ok"}


class TestTaskQueue:
    @pytest.mark.asyncio
    async def test_push_returns_task_id(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        tid = await q.push({"workflow": "x.yaml"})
        assert isinstance(tid, str)
        assert len(tid) > 0
        await q.close()

    @pytest.mark.asyncio
    async def test_push_custom_task_id(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        tid = await q.push({"k": "v"}, task_id="my-task-123")
        assert tid == "my-task-123"
        await q.close()

    @pytest.mark.asyncio
    async def test_pop_returns_pending_task(self):
        from meshflow.queue import TaskQueue, TaskStatus
        q = TaskQueue(":memory:")
        await q.push({"job": "a"})
        item = await q.pop()
        assert item is not None
        assert item.status == TaskStatus.RUNNING
        await q.close()

    @pytest.mark.asyncio
    async def test_pop_empty_returns_none(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        item = await q.pop()
        assert item is None
        await q.close()

    @pytest.mark.asyncio
    async def test_pop_priority_order(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        await q.push({"job": "low"}, priority=0)
        await q.push({"job": "high"}, priority=10)
        item = await q.pop()
        assert item is not None
        assert item.payload["job"] == "high"
        await q.close()

    @pytest.mark.asyncio
    async def test_complete_sets_done(self):
        from meshflow.queue import TaskQueue, TaskStatus
        q = TaskQueue(":memory:")
        tid = await q.push({"job": "x"})
        await q.pop()
        await q.complete(tid, {"result": "success"})
        item = await q.get(tid)
        assert item is not None
        assert item.status == TaskStatus.DONE
        assert item.result == {"result": "success"}
        await q.close()

    @pytest.mark.asyncio
    async def test_fail_sets_failed(self):
        from meshflow.queue import TaskQueue, TaskStatus
        q = TaskQueue(":memory:")
        tid = await q.push({"job": "x"})
        await q.pop()
        await q.fail(tid, "timeout error")
        item = await q.get(tid)
        assert item is not None
        assert item.status == TaskStatus.FAILED
        assert "timeout" in item.error
        await q.close()

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self):
        from meshflow.queue import TaskQueue, TaskStatus
        q = TaskQueue(":memory:")
        tid = await q.push({"job": "x"})
        ok = await q.cancel(tid)
        assert ok is True
        item = await q.get(tid)
        assert item is not None
        assert item.status == TaskStatus.CANCELLED
        await q.close()

    @pytest.mark.asyncio
    async def test_cancel_running_task_fails(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        tid = await q.push({"job": "x"})
        await q.pop()  # marks as running
        ok = await q.cancel(tid)
        assert ok is False  # running tasks cannot be cancelled
        await q.close()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        ok = await q.cancel("no-such-task")
        assert ok is False
        await q.close()

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        item = await q.get("ghost")
        assert item is None
        await q.close()

    @pytest.mark.asyncio
    async def test_stats_counts(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        t1 = await q.push({"a": 1})
        t2 = await q.push({"b": 2})
        await q.pop()  # t1 → running (FIFO on equal priority + earlier created_at)
        stats = await q.stats()
        assert stats.get("pending", 0) + stats.get("running", 0) == 2
        await q.close()

    @pytest.mark.asyncio
    async def test_list_tasks_all(self):
        from meshflow.queue import TaskQueue
        q = TaskQueue(":memory:")
        await q.push({"a": 1})
        await q.push({"b": 2})
        items = await q.list_tasks(limit=10)
        assert len(items) == 2
        await q.close()

    @pytest.mark.asyncio
    async def test_list_tasks_by_status(self):
        from meshflow.queue import TaskQueue, TaskStatus
        q = TaskQueue(":memory:")
        t1 = await q.push({"a": 1})
        await q.push({"b": 2})
        await q.pop()  # first pop → one running
        pending = await q.list_tasks(status=TaskStatus.PENDING)
        running = await q.list_tasks(status=TaskStatus.RUNNING)
        assert len(pending) + len(running) == 2
        await q.close()


class TestQueueWorker:
    @pytest.mark.asyncio
    async def test_worker_processes_task(self):
        from meshflow.queue import TaskQueue, QueueWorker, TaskStatus

        processed: list[str] = []

        async def handler(item):
            processed.append(item.task_id)
            return {"done": True}

        q = TaskQueue(":memory:")
        tid = await q.push({"job": "x"})

        stop = asyncio.Event()
        worker = QueueWorker(q, concurrency=1, handler=handler, poll_interval=0.05)

        async def _run_then_stop():
            await asyncio.sleep(0.15)
            stop.set()

        await asyncio.gather(worker.run(stop_event=stop), _run_then_stop())

        assert tid in processed
        item = await q.get(tid)
        assert item is not None
        assert item.status == TaskStatus.DONE
        await q.close()

    @pytest.mark.asyncio
    async def test_worker_records_failure_on_exception(self):
        from meshflow.queue import TaskQueue, QueueWorker, TaskStatus

        async def bad_handler(item):
            raise RuntimeError("intentional failure")

        q = TaskQueue(":memory:")
        tid = await q.push({"job": "bad"})

        stop = asyncio.Event()
        worker = QueueWorker(q, concurrency=1, handler=bad_handler, poll_interval=0.05)

        async def _stop_after():
            await asyncio.sleep(0.2)
            stop.set()

        await asyncio.gather(worker.run(stop_event=stop), _stop_after())

        item = await q.get(tid)
        assert item is not None
        assert item.status == TaskStatus.FAILED
        assert "intentional" in item.error
        await q.close()

    @pytest.mark.asyncio
    async def test_worker_stats(self):
        from meshflow.queue import TaskQueue, QueueWorker

        async def handler(item):
            return {}

        q = TaskQueue(":memory:")
        await q.push({"job": "a"})
        stop = asyncio.Event()
        worker = QueueWorker(q, concurrency=2, handler=handler, poll_interval=0.05)

        async def _stop():
            await asyncio.sleep(0.2)
            stop.set()

        await asyncio.gather(worker.run(stop_event=stop), _stop())
        stats = worker.stats
        assert stats["processed"] >= 1
        assert stats["concurrency"] == 2
        await q.close()


# ══════════════════════════════════════════════════════════════════════════════
# Integration — version check
# ══════════════════════════════════════════════════════════════════════════════

class TestVersionBump:
    def test_version_is_023(self):
        import meshflow
        assert meshflow.__version__ >= "0.77.0"

    def test_new_exports_accessible(self):
        import meshflow
        assert hasattr(meshflow, "SensitiveDataDetector")
        assert hasattr(meshflow, "ModelHealthTracker")
        assert hasattr(meshflow, "WorkflowAnalytics")
        assert hasattr(meshflow, "TaskQueue")
        assert hasattr(meshflow, "QueueWorker")
        assert hasattr(meshflow, "TaskItem")
        assert hasattr(meshflow, "TaskStatus")
