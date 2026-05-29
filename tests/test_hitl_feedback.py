"""Sprint 69 — HITL feedback collection tests."""

from __future__ import annotations

import pytest
from meshflow.eval.feedback import FeedbackCollector, FeedbackRecord, FeedbackStore
from meshflow.core.workflow import HumanDecision


# ── HumanDecision extended fields ─────────────────────────────────────────────


def test_human_decision_default_values():
    d = HumanDecision(approved=True)
    assert d.rating == 0
    assert d.feedback == ""
    assert d.corrections == {}


def test_human_decision_with_rating_and_feedback():
    d = HumanDecision(
        approved=True,
        rating=4,
        feedback="Good but verbose.",
        corrections={"section": "should be shorter"},
    )
    assert d.rating == 4
    assert d.feedback == "Good but verbose."
    assert d.corrections["section"] == "should be shorter"


# ── FeedbackCollector.summary ─────────────────────────────────────────────────


def _populated_store() -> FeedbackStore:
    store = FeedbackStore(":memory:")
    for i, score in enumerate([0.9, 0.7, 0.5]):
        store.save(FeedbackRecord(
            run_id="run-001",
            agent_name="agent-x",
            task=f"task {i}",
            original_output=f"output {i}",
            score=score,
            correction="fixed" if i == 2 else "",
        ))
    return store


def test_summary_run_id():
    store = _populated_store()
    collector = FeedbackCollector(store)
    summary = collector.summary("run-001")
    assert summary["count"] == 3
    assert summary["corrections"] == 1
    assert abs(summary["avg_score"] - (0.9 + 0.7 + 0.5) / 3) < 0.001


def test_summary_missing_run():
    store = FeedbackStore(":memory:")
    collector = FeedbackCollector(store)
    summary = collector.summary("run-nonexistent")
    assert summary["count"] == 0
    assert summary["avg_score"] == 0.0


# ── FeedbackCollector.export_training_pairs ───────────────────────────────────


def test_export_training_pairs_all():
    store = _populated_store()
    collector = FeedbackCollector(store)
    pairs = collector.export_training_pairs()
    assert len(pairs) == 3
    for p in pairs:
        assert "prompt" in p
        assert "output" in p
        assert "correction" in p


def test_export_training_pairs_corrections_only():
    store = _populated_store()
    collector = FeedbackCollector(store)
    pairs = collector.export_training_pairs(corrections_only=True)
    assert len(pairs) == 1
    assert pairs[0]["correction"] == "fixed"


def test_export_training_pairs_agent_filter():
    store = FeedbackStore(":memory:")
    for agent in ["agent-a", "agent-b"]:
        store.save(FeedbackRecord(
            run_id="r1", agent_name=agent, task="t", original_output="o", score=1.0
        ))
    collector = FeedbackCollector(store)
    pairs = collector.export_training_pairs(agent_name="agent-a")
    assert len(pairs) == 1
    assert pairs[0]["agent_name"] == "agent-a"


# ── global_summary ────────────────────────────────────────────────────────────


def test_global_summary():
    store = _populated_store()
    collector = FeedbackCollector(store)
    s = collector.global_summary()
    assert s["count"] == 3
    assert "avg_score" in s


# ── FeedbackStore stats ───────────────────────────────────────────────────────


def test_feedback_store_stats():
    store = _populated_store()
    s = store.stats()
    assert s["count"] == 3
    assert s["corrections"] == 1
