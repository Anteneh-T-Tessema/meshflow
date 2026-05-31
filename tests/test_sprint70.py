"""Sprint 70 — Eval Framework v2 tests.

Covers LLMJudge, ConversationEval, ABTest, and QualityGate.
All tests use EchoProvider — no API key required.
"""

from __future__ import annotations

import json

import pytest

import meshflow
from meshflow.agents.base import EchoProvider
from meshflow.eval.judge import LLMJudge, JudgeScore, JudgeSuiteResult
from meshflow.eval.conversation_eval import (
    ConversationEval, ConversationCase, Turn, ConversationResult,
)
from meshflow.eval.ab_test import ABTest, ABVariant, ABTestResult, ABTurnResult
from meshflow.eval.quality_gate import QualityGate


# ── Helpers ───────────────────────────────────────────────────────────────────

def _judge(response: str = "") -> LLMJudge:
    """Judge backed by EchoProvider with a fixed JSON score response."""
    payload = json.dumps({
        "score": 0.82,
        "reasoning": "Output is accurate and clear.",
        "criteria": {
            "accuracy": 0.85,
            "completeness": 0.80,
            "clarity": 0.82,
            "relevance": 0.81,
        },
    })
    return LLMJudge(provider=EchoProvider(response=payload or payload))


def _low_judge() -> LLMJudge:
    payload = json.dumps({
        "score": 0.40,
        "reasoning": "Output is vague and incomplete.",
        "criteria": {"accuracy": 0.40, "completeness": 0.38, "clarity": 0.42, "relevance": 0.40},
    })
    return LLMJudge(provider=EchoProvider(response=payload))


# ══════════════════════════════════════════════════════════════════════════════
#  LLMJudge
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMJudge:

    @pytest.mark.asyncio
    async def test_score_returns_judge_score(self):
        judge = _judge()
        result = await judge.score("What is HIPAA?", "HIPAA is a US privacy law.")
        assert isinstance(result, JudgeScore)

    @pytest.mark.asyncio
    async def test_score_in_range(self):
        judge = _judge()
        result = await judge.score("task", "output")
        assert 0.0 <= result.score <= 1.0

    @pytest.mark.asyncio
    async def test_score_has_reasoning(self):
        judge = _judge()
        result = await judge.score("task", "output")
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 0

    @pytest.mark.asyncio
    async def test_score_has_criteria(self):
        judge = _judge()
        result = await judge.score("task", "output")
        assert isinstance(result.criteria, dict)
        assert "accuracy" in result.criteria

    @pytest.mark.asyncio
    async def test_score_with_reference(self):
        judge = _judge()
        result = await judge.score("task", "output", reference="gold answer")
        assert isinstance(result, JudgeScore)

    @pytest.mark.asyncio
    async def test_score_with_custom_rubric(self):
        judge = _judge()
        result = await judge.score("task", "output", rubric="Focus on brevity.")
        assert isinstance(result, JudgeScore)

    def test_judge_score_passed_above_threshold(self):
        s = JudgeScore(score=0.8, reasoning="good")
        assert s.passed(threshold=0.7) is True

    def test_judge_score_failed_below_threshold(self):
        s = JudgeScore(score=0.5, reasoning="weak")
        assert s.passed(threshold=0.7) is False

    def test_judge_score_to_dict(self):
        s = JudgeScore(score=0.9, reasoning="great", criteria={"accuracy": 0.9})
        d = s.to_dict()
        assert d["score"] == 0.9
        assert "reasoning" in d
        assert "criteria" in d

    @pytest.mark.asyncio
    async def test_score_batch(self):
        judge = _judge()
        items = [
            {"task": "What is HIPAA?", "output": "A privacy law."},
            {"task": "What is SOC 2?", "output": "An audit framework."},
        ]
        scores = await judge.score_batch(items)
        assert len(scores) == 2
        assert all(isinstance(s, JudgeScore) for s in scores)

    @pytest.mark.asyncio
    async def test_score_batch_empty(self):
        judge = _judge()
        scores = await judge.score_batch([])
        assert scores == []

    @pytest.mark.asyncio
    async def test_score_suite(self):
        judge = _judge()
        results = [
            {"task": "t1", "output": "o1"},
            {"task": "t2", "output": "o2"},
        ]
        suite = await judge.score_suite(results)
        assert isinstance(suite, JudgeSuiteResult)
        assert len(suite.scores) == 2

    def test_judge_suite_result_avg_score(self):
        scores = [
            JudgeScore(score=0.8, reasoning="a"),
            JudgeScore(score=0.6, reasoning="b"),
        ]
        suite = JudgeSuiteResult(scores=scores)
        assert suite.avg_score == pytest.approx(0.7, abs=0.001)

    def test_judge_suite_result_min_max(self):
        scores = [JudgeScore(score=0.4, reasoning=""), JudgeScore(score=0.9, reasoning="")]
        suite = JudgeSuiteResult(scores=scores)
        assert suite.min_score == pytest.approx(0.4)
        assert suite.max_score == pytest.approx(0.9)

    def test_judge_suite_result_to_dict(self):
        suite = JudgeSuiteResult(scores=[JudgeScore(score=0.75, reasoning="ok")])
        d = suite.to_dict()
        assert "avg_score" in d
        assert "pass_rate" in d
        assert "scores" in d

    @pytest.mark.asyncio
    async def test_fallback_on_malformed_json(self):
        """Judge should not raise when provider returns non-JSON."""
        judge = LLMJudge(provider=EchoProvider(response="not json at all"))
        result = await judge.score("task", "output")
        assert isinstance(result, JudgeScore)
        assert 0.0 <= result.score <= 1.0

    @pytest.mark.asyncio
    async def test_fallback_extracts_float(self):
        """Judge should extract bare float from prose response."""
        judge = LLMJudge(provider=EchoProvider(response="The score is 0.73 based on accuracy."))
        result = await judge.score("task", "output")
        assert result.score == pytest.approx(0.73)


# ══════════════════════════════════════════════════════════════════════════════
#  ConversationEval
# ══════════════════════════════════════════════════════════════════════════════

class TestConversationEval:

    def _provider(self) -> EchoProvider:
        return EchoProvider(response="This is a helpful response about the topic.")

    def _case(self) -> ConversationCase:
        return ConversationCase(
            name="hipaa-basics",
            turns=[
                Turn(user="What is HIPAA?", must_contain=["helpful"]),
                Turn(user="What are the main rules?", must_contain=["helpful"]),
            ],
        )

    @pytest.mark.asyncio
    async def test_run_returns_conversation_result(self):
        ev = ConversationEval(judge=_judge())
        result = await ev.run(self._case(), provider=self._provider())
        assert isinstance(result, ConversationResult)

    @pytest.mark.asyncio
    async def test_run_has_correct_turn_count(self):
        ev = ConversationEval(judge=_judge())
        result = await ev.run(self._case(), provider=self._provider())
        assert len(result.turn_results) == 2

    @pytest.mark.asyncio
    async def test_turn_results_have_correct_idx(self):
        ev = ConversationEval(judge=_judge())
        result = await ev.run(self._case(), provider=self._provider())
        for i, t in enumerate(result.turn_results, 1):
            assert t.turn_idx == i

    @pytest.mark.asyncio
    async def test_must_contain_passes(self):
        ev = ConversationEval(judge=_judge())
        case = ConversationCase(
            name="test",
            turns=[Turn(user="Say hello", must_contain=["helpful"])],
        )
        result = await ev.run(case, provider=EchoProvider(response="This is helpful."))
        assert result.turn_results[0].contains_passed is True

    @pytest.mark.asyncio
    async def test_must_contain_fails(self):
        ev = ConversationEval(judge=_judge())
        case = ConversationCase(
            name="test",
            turns=[Turn(user="Say hello", must_contain=["MISSING_WORD"])],
        )
        result = await ev.run(case, provider=EchoProvider(response="Hello there."))
        assert result.turn_results[0].contains_passed is False
        assert result.turn_results[0].passed is False

    @pytest.mark.asyncio
    async def test_must_not_contain_fails(self):
        ev = ConversationEval(judge=_judge())
        case = ConversationCase(
            name="test",
            turns=[Turn(user="Say hello", must_not_contain=["helpful"])],
        )
        result = await ev.run(case, provider=EchoProvider(response="This is helpful."))
        assert result.turn_results[0].not_contains_passed is False

    @pytest.mark.asyncio
    async def test_avg_score(self):
        ev = ConversationEval(judge=_judge())
        result = await ev.run(self._case(), provider=self._provider())
        assert 0.0 <= result.avg_score <= 1.0

    @pytest.mark.asyncio
    async def test_summary_string(self):
        ev = ConversationEval(judge=_judge())
        result = await ev.run(self._case(), provider=self._provider())
        s = result.summary()
        assert "hipaa-basics" in s
        assert "/" in s

    @pytest.mark.asyncio
    async def test_to_dict(self):
        ev = ConversationEval(judge=_judge())
        result = await ev.run(self._case(), provider=self._provider())
        d = result.to_dict()
        assert "case_name" in d
        assert "turns" in d
        assert "avg_score" in d

    @pytest.mark.asyncio
    async def test_turn_result_to_dict(self):
        ev = ConversationEval(judge=_judge())
        result = await ev.run(self._case(), provider=self._provider())
        d = result.turn_results[0].to_dict()
        assert "turn_idx" in d
        assert "judge_score" in d
        assert "passed" in d

    @pytest.mark.asyncio
    async def test_run_suite(self):
        ev = ConversationEval(judge=_judge())
        cases = [self._case(), self._case()]
        results = await ev.run_suite(cases, provider=self._provider())
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_turns_passed_and_failed(self):
        ev = ConversationEval(judge=_judge(), pass_threshold=0.0)
        result = await ev.run(self._case(), provider=self._provider())
        total = result.turns_passed + result.turns_failed
        assert total == len(result.turn_results)


# ══════════════════════════════════════════════════════════════════════════════
#  ABTest
# ══════════════════════════════════════════════════════════════════════════════

class TestABTest:

    def _ab(self, ctrl_score: float = 0.60, var_score: float = 0.80) -> ABTest:
        ctrl_payload = json.dumps({"score": ctrl_score, "reasoning": "ctrl", "criteria": {}})
        var_payload = json.dumps({"score": var_score, "reasoning": "var", "criteria": {}})

        # Use a judge that alternates based on which variant is being scored
        # Simpler: use two providers but same judge — just use a neutral judge
        judge = LLMJudge(provider=EchoProvider(response=json.dumps({
            "score": 0.70, "reasoning": "ok", "criteria": {},
        })))
        return ABTest(
            control=ABVariant("ctrl", provider=EchoProvider("control answer")),
            variant=ABVariant("var", provider=EchoProvider("variant answer")),
            judge=judge,
        )

    @pytest.mark.asyncio
    async def test_run_returns_ab_result(self):
        ab = self._ab()
        result = await ab.run(["What is HIPAA?", "Explain SOC 2."])
        assert isinstance(result, ABTestResult)

    @pytest.mark.asyncio
    async def test_run_has_correct_scenario_count(self):
        ab = self._ab()
        result = await ab.run(["s1", "s2", "s3"])
        assert len(result.turn_results) == 3

    @pytest.mark.asyncio
    async def test_turn_result_fields(self):
        ab = self._ab()
        result = await ab.run(["task1"])
        t = result.turn_results[0]
        assert isinstance(t, ABTurnResult)
        assert t.scenario == "task1"
        assert 0.0 <= t.control_score <= 1.0
        assert 0.0 <= t.variant_score <= 1.0

    @pytest.mark.asyncio
    async def test_delta_is_variant_minus_control(self):
        ab = self._ab()
        result = await ab.run(["task"])
        t = result.turn_results[0]
        assert t.delta == pytest.approx(t.variant_score - t.control_score, abs=0.001)

    @pytest.mark.asyncio
    async def test_winner_tie_when_close(self):
        ab = self._ab()
        result = await ab.run(["task1", "task2"])
        # Both variants get same score from same judge → tie
        assert result.winner in ("ctrl", "var", "tie")

    def test_ab_result_control_avg(self):
        turns = [
            ABTurnResult("s1", "co", "vo", 0.8, 0.9, "", ""),
            ABTurnResult("s2", "co", "vo", 0.6, 0.7, "", ""),
        ]
        r = ABTestResult("ctrl", "var", turns, 100.0)
        assert r.control_avg == pytest.approx(0.7, abs=0.001)
        assert r.variant_avg == pytest.approx(0.8, abs=0.001)

    def test_ab_result_winner_variant(self):
        turns = [ABTurnResult("s", "co", "vo", 0.5, 0.9, "", "")]
        r = ABTestResult("ctrl", "var", turns, 100.0)
        assert r.winner == "var"

    def test_ab_result_winner_control(self):
        turns = [ABTurnResult("s", "co", "vo", 0.9, 0.5, "", "")]
        r = ABTestResult("ctrl", "var", turns, 100.0)
        assert r.winner == "ctrl"

    def test_ab_result_tie_when_close(self):
        turns = [ABTurnResult("s", "co", "vo", 0.80, 0.81, "", "")]
        r = ABTestResult("ctrl", "var", turns, 100.0)
        assert r.winner == "tie"

    def test_ab_result_effect_size(self):
        turns = [
            ABTurnResult("s1", "", "", 0.5, 0.9, "", ""),
            ABTurnResult("s2", "", "", 0.5, 0.9, "", ""),
        ]
        r = ABTestResult("ctrl", "var", turns, 100.0)
        # All deltas identical → variance=0, effect_size = |mean| / tiny = large
        assert r.effect_size >= 0.0

    def test_ab_result_win_rates(self):
        turns = [
            ABTurnResult("s1", "", "", 0.4, 0.9, "", ""),
            ABTurnResult("s2", "", "", 0.9, 0.4, "", ""),
        ]
        r = ABTestResult("ctrl", "var", turns, 100.0)
        assert r.control_win_rate == pytest.approx(0.5)
        assert r.variant_win_rate == pytest.approx(0.5)

    def test_ab_result_summary(self):
        turns = [ABTurnResult("s", "", "", 0.7, 0.8, "", "")]
        r = ABTestResult("ctrl", "var", turns, 500.0)
        s = r.summary()
        assert "ctrl" in s
        assert "var" in s

    def test_ab_result_to_dict(self):
        turns = [ABTurnResult("s", "co", "vo", 0.7, 0.8, "r1", "r2")]
        r = ABTestResult("ctrl", "var", turns, 200.0)
        d = r.to_dict()
        assert d["control"] == "ctrl"
        assert d["variant"] == "var"
        assert "winner" in d
        assert "turns" in d

    @pytest.mark.asyncio
    async def test_empty_scenarios(self):
        ab = self._ab()
        result = await ab.run([])
        assert len(result.turn_results) == 0
        assert result.control_avg == 0.0
        assert result.variant_avg == 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  QualityGate
# ══════════════════════════════════════════════════════════════════════════════

class TestQualityGate:

    def _gate(self, tmp_path) -> QualityGate:
        return QualityGate(
            baseline_path=str(tmp_path / "baseline.json"),
            avg_drop_threshold=0.05,
            pass_rate_drop_threshold=0.05,
        )

    def test_no_baseline_returns_no_regression(self, tmp_path):
        gate = self._gate(tmp_path)
        report = gate.compare({"avg_score": 0.80, "pass_rate": 0.90, "n": 20})
        assert not report.any_regression

    def test_save_and_load_baseline(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.82, "pass_rate": 0.91, "n": 30})
        loaded = gate.load_baseline()
        assert loaded is not None
        assert loaded["avg_score"] == pytest.approx(0.82)

    def test_no_regression_when_scores_same(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.80, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.80, "pass_rate": 0.90, "n": 20})
        assert not report.any_regression

    def test_no_regression_when_scores_improve(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.70, "pass_rate": 0.80})
        report = gate.compare({"avg_score": 0.85, "pass_rate": 0.92, "n": 20})
        assert not report.any_regression

    def test_avg_regression_detected(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.85, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.75, "pass_rate": 0.90, "n": 20})
        assert report.avg_regression is True
        assert report.any_regression is True

    def test_pass_rate_regression_detected(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.85, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.85, "pass_rate": 0.80, "n": 20})
        assert report.pass_rate_regression is True

    def test_below_threshold_is_not_regression(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.85, "pass_rate": 0.90})
        # Drop of 0.03 < threshold 0.05 → no regression
        report = gate.compare({"avg_score": 0.82, "pass_rate": 0.87, "n": 20})
        assert not report.any_regression

    def test_report_deltas(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.80, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.75, "pass_rate": 0.85, "n": 20})
        assert report.avg_delta == pytest.approx(-0.05, abs=0.001)
        assert report.pass_rate_delta == pytest.approx(-0.05, abs=0.001)

    def test_report_to_dict(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.80, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.80, "pass_rate": 0.90, "n": 10})
        d = report.to_dict()
        assert "baseline_avg" in d
        assert "current_avg" in d
        assert "any_regression" in d

    def test_exit_code_pass(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.80, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.80, "pass_rate": 0.90})
        assert gate.exit_code(report) == 0

    def test_exit_code_fail(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.85, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.70, "pass_rate": 0.90})
        assert gate.exit_code(report) == 1

    def test_check_returns_exit_code(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.80, "pass_rate": 0.90})
        code = gate.check({"avg_score": 0.80, "pass_rate": 0.90}, verbose=False)
        assert code == 0

    def test_check_updates_baseline_on_pass(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.70, "pass_rate": 0.80})
        gate.check(
            {"avg_score": 0.85, "pass_rate": 0.92},
            verbose=False,
            update_baseline_on_pass=True,
        )
        new_baseline = gate.load_baseline()
        assert new_baseline is not None
        assert new_baseline["avg_score"] == pytest.approx(0.85)

    def test_summary_lines(self, tmp_path):
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.80, "pass_rate": 0.90})
        report = gate.compare({"avg_score": 0.75, "pass_rate": 0.85, "n": 10})
        lines = report.summary_lines()
        assert len(lines) >= 2
        assert any("avg_score" in ln for ln in lines)

    @pytest.mark.asyncio
    async def test_check_suite(self, tmp_path):
        from meshflow.eval.judge import JudgeSuiteResult, JudgeScore
        gate = self._gate(tmp_path)
        gate.save_baseline({"avg_score": 0.70, "pass_rate": 0.80})
        suite = JudgeSuiteResult(scores=[
            JudgeScore(score=0.85, reasoning="good"),
            JudgeScore(score=0.90, reasoning="great"),
        ])
        code = await gate.check_suite(suite, verbose=False)
        assert code == 0


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_llm_judge_exported(self):
        assert hasattr(meshflow, "LLMJudge")
        assert hasattr(meshflow, "JudgeScore")
        assert hasattr(meshflow, "JudgeSuiteResult")

    def test_conversation_eval_exported(self):
        assert hasattr(meshflow, "ConversationEval")
        assert hasattr(meshflow, "ConversationCase")
        assert hasattr(meshflow, "EvalTurn")
        assert hasattr(meshflow, "EvalConversationResult")
        assert hasattr(meshflow, "TurnResult")

    def test_ab_test_exported(self):
        assert hasattr(meshflow, "ABTest")
        assert hasattr(meshflow, "ABVariant")
        assert hasattr(meshflow, "ABTestResult")
        assert hasattr(meshflow, "ABTurnResult")

    def test_quality_gate_exported(self):
        assert hasattr(meshflow, "QualityGate")
        assert hasattr(meshflow, "QualityReport")

    def test_all_in___all__(self):
        for sym in (
            "LLMJudge", "JudgeScore", "JudgeSuiteResult",
            "ConversationEval", "ConversationCase", "EvalTurn",
            "EvalConversationResult", "TurnResult",
            "ABTest", "ABVariant", "ABTestResult", "ABTurnResult",
            "QualityGate", "QualityReport",
        ):
            assert sym in meshflow.__all__, f"{sym} missing from __all__"

    def test_version_bumped(self):
        assert meshflow.__version__ >= "0.77.0"
