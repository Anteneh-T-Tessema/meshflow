"""Sprint 43 — Production eval: feedback loop, shadow runner, regression detection."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.eval.feedback import FeedbackRecord, FeedbackStore
from meshflow.eval.shadow import (
    RegressionAlert,
    RegressionDetector,
    ShadowResult,
    _text_similarity,
    shadow_run,
)


# ── FeedbackRecord ────────────────────────────────────────────────────────────

class TestFeedbackRecord:
    def _rec(self, **kwargs) -> FeedbackRecord:
        defaults = dict(
            run_id="run-001",
            agent_name="billing-agent",
            task="What is my invoice total?",
            original_output="Your total is $120.",
            score=0.9,
        )
        defaults.update(kwargs)
        return FeedbackRecord(**defaults)

    def test_auto_feedback_id(self):
        r = self._rec()
        assert len(r.feedback_id) > 0

    def test_has_correction_false(self):
        r = self._rec(correction="")
        assert not r.has_correction

    def test_has_correction_true(self):
        r = self._rec(correction="The correct total is $130.")
        assert r.has_correction

    def test_preferred_output_no_correction(self):
        r = self._rec(correction="")
        assert r.preferred_output == r.original_output

    def test_preferred_output_with_correction(self):
        r = self._rec(correction="Fixed answer.")
        assert r.preferred_output == "Fixed answer."

    def test_round_trip(self):
        r = self._rec()
        r2 = FeedbackRecord.from_dict(r.to_dict())
        assert r2.run_id == r.run_id
        assert r2.score == r.score
        assert r2.feedback_id == r.feedback_id


# ── FeedbackStore ─────────────────────────────────────────────────────────────

class TestFeedbackStore:
    @pytest.fixture
    def store(self):
        return FeedbackStore(":memory:")

    def _rec(self, run_id="r1", agent="bot", score=0.9, correction=""):
        return FeedbackRecord(
            run_id=run_id, agent_name=agent,
            task="task", original_output="output",
            score=score, correction=correction,
        )

    def test_save_and_get(self, store):
        r = self._rec()
        store.save(r)
        r2 = store.get(r.feedback_id)
        assert r2 is not None
        assert r2.run_id == "r1"

    def test_get_missing(self, store):
        assert store.get("ghost") is None

    def test_get_by_run(self, store):
        r = self._rec(run_id="run-xyz")
        store.save(r)
        found = store.get_by_run("run-xyz")
        assert found is not None
        assert found.run_id == "run-xyz"

    def test_list_all(self, store):
        store.save(self._rec("r1"))
        store.save(self._rec("r2"))
        assert len(store.list()) == 2

    def test_list_by_agent(self, store):
        store.save(self._rec(agent="billing"))
        store.save(self._rec(agent="support"))
        assert len(store.list(agent_name="billing")) == 1

    def test_list_min_score_filter(self, store):
        store.save(self._rec(score=0.9))
        store.save(self._rec(score=0.3))
        assert len(store.list(min_score=0.7)) == 1

    def test_delete(self, store):
        r = self._rec()
        store.save(r)
        assert store.delete(r.feedback_id) is True
        assert store.get(r.feedback_id) is None

    def test_delete_missing(self, store):
        assert store.delete("ghost") is False

    def test_count(self, store):
        store.save(self._rec("a"))
        store.save(self._rec("b"))
        assert store.count() == 2

    def test_stats_empty(self, store):
        s = store.stats()
        assert s["count"] == 0

    def test_stats_populated(self, store):
        store.save(self._rec(score=0.8))
        store.save(self._rec(score=0.6))
        s = store.stats()
        assert s["count"] == 2
        assert abs(s["avg_score"] - 0.7) < 0.01

    def test_stats_corrections(self, store):
        store.save(self._rec(correction="better answer"))
        store.save(self._rec(correction=""))
        s = store.stats()
        assert s["corrections"] == 1

    def test_export_finetune_openai(self, store):
        store.save(self._rec("r1"))
        jsonl = store.export_finetune()
        lines = [l for l in jsonl.splitlines() if l.strip()]
        assert len(lines) == 1
        d = json.loads(lines[0])
        assert "messages" in d
        roles = [m["role"] for m in d["messages"]]
        assert "user" in roles and "assistant" in roles

    def test_export_finetune_anthropic(self, store):
        store.save(self._rec("r1"))
        jsonl = store.export_finetune(format="anthropic")
        d = json.loads(jsonl.splitlines()[0])
        assert "human" in d and "assistant" in d

    def test_export_finetune_generic(self, store):
        store.save(self._rec("r1"))
        jsonl = store.export_finetune(format="generic")
        d = json.loads(jsonl.splitlines()[0])
        assert "prompt" in d and "completion" in d

    def test_export_corrections_only(self, store):
        store.save(self._rec(correction="fixed"))
        store.save(self._rec(correction=""))
        jsonl = store.export_finetune(corrections_only=True)
        assert len(jsonl.splitlines()) == 1

    def test_export_uses_correction_as_output(self, store):
        store.save(self._rec(correction="corrected answer"))
        jsonl = store.export_finetune()
        d = json.loads(jsonl)
        assistant = next(m for m in d["messages"] if m["role"] == "assistant")
        assert assistant["content"] == "corrected answer"


# ── _text_similarity ──────────────────────────────────────────────────────────

class TestTextSimilarity:
    def test_identical(self):
        assert _text_similarity("hello world", "hello world") == pytest.approx(1.0)

    def test_empty_both(self):
        assert _text_similarity("", "") == pytest.approx(1.0)

    def test_one_empty(self):
        assert _text_similarity("hello", "") == pytest.approx(0.0)

    def test_completely_different(self):
        sim = _text_similarity("aaaaaa", "bbbbbb")
        assert sim < 0.1

    def test_partial_overlap(self):
        sim = _text_similarity("The answer is 42", "The answer is 43")
        assert 0.5 < sim < 1.0


# ── ShadowResult ──────────────────────────────────────────────────────────────

class TestShadowResult:
    def test_to_dict(self):
        r = ShadowResult(
            primary_output="42",
            shadow_output="42",
            primary_agent="v1",
            shadow_agent="v2",
            agreement=True,
            similarity=1.0,
            delta_confidence=0.05,
        )
        d = r.to_dict()
        assert d["agreement"] is True
        assert d["primary_agent"] == "v1"
        assert "delta_confidence" in d


# ── shadow_run ────────────────────────────────────────────────────────────────

class TestShadowRun:
    @pytest.mark.asyncio
    async def test_returns_shadow_result(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        primary = Agent(name="primary-v1", role="executor")
        shadow  = Agent(name="shadow-v2",  role="executor")
        result = await shadow_run(primary, shadow, "hello task")
        assert isinstance(result, ShadowResult)
        assert result.primary_agent == "primary-v1"
        assert result.shadow_agent  == "shadow-v2"

    @pytest.mark.asyncio
    async def test_both_agents_run(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        primary = Agent(name="p", role="executor")
        shadow  = Agent(name="s", role="executor")

        with patch.object(primary, "run", new_callable=AsyncMock) as mp, \
             patch.object(shadow,  "run", new_callable=AsyncMock) as ms:
            mp.return_value = {"result": "answer-A", "tokens": 10, "cost_usd": 0.001,
                               "stated_confidence": 0.9, "blocked": False}
            ms.return_value = {"result": "answer-B", "tokens": 12, "cost_usd": 0.001,
                               "stated_confidence": 0.85, "blocked": False}
            result = await shadow_run(primary, shadow, "question")

        assert result.primary_output == "answer-A"
        assert result.shadow_output  == "answer-B"
        assert result.primary_tokens == 10
        assert result.shadow_tokens  == 12

    @pytest.mark.asyncio
    async def test_agreement_identical_outputs(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        primary = Agent(name="p", role="executor")
        shadow  = Agent(name="s", role="executor")

        same = {"result": "exact same answer", "tokens": 5, "cost_usd": 0.0,
                "stated_confidence": 0.9, "blocked": False}
        with patch.object(primary, "run", new_callable=AsyncMock) as mp, \
             patch.object(shadow,  "run", new_callable=AsyncMock) as ms:
            mp.return_value = same
            ms.return_value = same
            result = await shadow_run(primary, shadow, "q")

        assert result.agreement is True
        assert result.similarity == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_delta_confidence(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        primary = Agent(name="p", role="executor")
        shadow  = Agent(name="s", role="executor")

        with patch.object(primary, "run", new_callable=AsyncMock) as mp, \
             patch.object(shadow,  "run", new_callable=AsyncMock) as ms:
            mp.return_value = {"result": "x", "tokens": 1, "cost_usd": 0.0,
                               "stated_confidence": 0.80, "blocked": False}
            ms.return_value = {"result": "x", "tokens": 1, "cost_usd": 0.0,
                               "stated_confidence": 0.90, "blocked": False}
            result = await shadow_run(primary, shadow, "q")

        assert abs(result.delta_confidence - 0.10) < 0.001


# ── RegressionDetector ────────────────────────────────────────────────────────

class TestRegressionDetector:
    def _results(self, confidence=0.9, cost=0.001, blocked=False, n=10):
        return [
            {"stated_confidence": confidence, "cost_usd": cost,
             "tokens": 100, "blocked": blocked}
        ] * n

    def test_no_baseline_no_alerts(self):
        d = RegressionDetector()
        alerts = d.check("agent", self._results())
        assert alerts == []

    def test_no_regression_no_alerts(self):
        d = RegressionDetector()
        d.set_baseline("agent", "confidence", 0.9)
        alerts = d.check("agent", self._results(confidence=0.9))
        assert alerts == []

    def test_confidence_drop_warning(self):
        d = RegressionDetector()
        d.set_baseline("agent", "confidence", 0.90)
        alerts = d.check("agent", self._results(confidence=0.80))
        assert len(alerts) == 1
        assert alerts[0].metric == "confidence"
        assert alerts[0].severity in ("warning", "critical")

    def test_confidence_drop_critical(self):
        d = RegressionDetector()
        d.set_baseline("agent", "confidence", 0.90)
        alerts = d.check("agent", self._results(confidence=0.50))
        assert any(a.severity == "critical" for a in alerts)

    def test_cost_spike_detected(self):
        d = RegressionDetector()
        d.set_baseline("agent", "cost", 0.001)
        alerts = d.check("agent", self._results(cost=0.005))
        assert len(alerts) == 1
        assert alerts[0].metric == "cost"
        assert alerts[0].delta > 0

    def test_block_rate_regression(self):
        d = RegressionDetector()
        d.set_baseline("agent", "block_rate", 0.0)
        # 50% block rate
        results = [{"stated_confidence": 0.9, "cost_usd": 0.001,
                    "tokens": 100, "blocked": i % 2 == 0} for i in range(10)]
        alerts = d.check("agent", results)
        assert any(a.metric == "block_rate" for a in alerts)

    def test_no_regression_within_threshold(self):
        d = RegressionDetector()
        d.set_baseline("agent", "confidence", 0.90)
        # 2% drop — below 5% warning threshold
        alerts = d.check("agent", self._results(confidence=0.882))
        assert alerts == []

    def test_report_structure(self):
        d = RegressionDetector()
        d.set_baseline("agent", "confidence", 0.9)
        report = d.report("agent", self._results(confidence=0.7))
        assert "agent_name" in report
        assert "alerts" in report
        assert "current" in report
        assert "baselines" in report
        assert report["has_regression"] is True

    def test_empty_results_no_alerts(self):
        d = RegressionDetector()
        d.set_baseline("agent", "confidence", 0.9)
        assert d.check("agent", []) == []

    def test_alert_to_dict(self):
        alert = RegressionAlert("bot", "confidence", 0.9, 0.7, -0.2, "critical")
        d = alert.to_dict()
        assert d["agent_name"] == "bot"
        assert d["severity"] == "critical"
        assert "delta" in d


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_feedback_imports(self):
        from meshflow.eval.feedback import FeedbackRecord, FeedbackStore
        assert FeedbackRecord is not None and FeedbackStore is not None

    def test_shadow_imports(self):
        from meshflow.eval.shadow import (
            ShadowResult, shadow_run, RegressionDetector, RegressionAlert
        )
        assert all(x is not None for x in [
            ShadowResult, shadow_run, RegressionDetector, RegressionAlert
        ])

    def test_eval_package_exports(self):
        from meshflow.eval import (
            FeedbackRecord, FeedbackStore,
            ShadowResult, shadow_run,
            RegressionAlert, RegressionDetector,
        )
        assert all(x is not None for x in [
            FeedbackRecord, FeedbackStore,
            ShadowResult, shadow_run,
            RegressionAlert, RegressionDetector,
        ])
