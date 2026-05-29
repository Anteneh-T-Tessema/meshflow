"""Sprint 68 — TeachableAgent tests."""

from __future__ import annotations

import pytest
from meshflow.agents.teachable import TeachableAgent, _detect_correction


# ── Correction detection ──────────────────────────────────────────────────────


def test_detect_correction_triggers():
    assert _detect_correction("Actually, the answer is 42.")
    assert _detect_correction("No, that's wrong.")
    assert _detect_correction("Correction: it should be Paris.")
    assert _detect_correction("That's not correct.")


def test_no_correction_in_normal_text():
    assert not _detect_correction("Yes, please continue.")
    assert not _detect_correction("The answer is correct.")
    assert not _detect_correction("I agree with your analysis.")


# ── teach / teachings / forget_teaching ──────────────────────────────────────


def _make_teachable() -> TeachableAgent:
    class _FakeAgent:
        name = "test-agent"
        system_prompt = "You are helpful."
        async def run(self, task, context=None):
            return {"result": "42", "agent_name": "test-agent"}

    return TeachableAgent(_FakeAgent(), storage_path=":memory:")


def test_teach_stores_correction():
    agent = _make_teachable()
    agent.teach("Sydney is the capital of Australia.", "Canberra is the capital.")
    teachings = agent.teachings()
    assert len(teachings) == 1
    assert "Sydney" in teachings[0]["original"]
    assert "Canberra" in teachings[0]["correction"]


def test_forget_teaching():
    agent = _make_teachable()
    agent.teach("old mistake", "correct answer")
    key = agent.teachings()[0]["key"]
    agent.forget_teaching(key)
    assert agent.teachings() == []


def test_forget_all_teachings():
    agent = _make_teachable()
    agent.teach("mistake A", "fix A")
    agent.teach("mistake B", "fix B")
    agent.forget_all_teachings()
    assert agent.teachings() == []


def test_teachings_block_format():
    agent = _make_teachable()
    agent.teach("wrong", "right")
    block = agent._build_teachings_block()
    assert "[Learned Corrections]" in block
    assert "wrong" in block
    assert "right" in block


def test_empty_teachings_block():
    agent = _make_teachable()
    assert agent._build_teachings_block() == ""


# ── run with correction injection ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_detects_correction_in_task():
    class TrackingAgent:
        name = "tracker"
        system_prompt = ""
        async def run(self, task, context=None):
            return {"result": "Sydney is the capital.", "agent_name": "tracker"}

    agent = TeachableAgent(TrackingAgent(), storage_path=":memory:")
    # First call — response sets _last_response internally
    await agent.run("What is the capital of Australia?")
    # Second call — correction trigger detected; _last_response is "Sydney is the capital."
    await agent.run("Actually, it's Canberra, not Sydney.")
    # A teaching should be stored
    assert len(agent.teachings()) >= 1


@pytest.mark.asyncio
async def test_run_prepends_teachings():
    prompts_seen = []

    class PromptCapturingAgent:
        name = "capper"
        system_prompt = "Original prompt."
        async def run(self, task, context=None):
            prompts_seen.append(self.system_prompt)
            return {"result": "ok", "agent_name": "capper"}

    agent = TeachableAgent(PromptCapturingAgent(), storage_path=":memory:")
    agent.teach("mistake", "correction")
    await agent.run("Do something.")
    # System prompt should have been temporarily prepended
    assert any("[Learned Corrections]" in p for p in prompts_seen)


# ── Agent builder integration ─────────────────────────────────────────────────


def test_agent_builder_teachable_param():
    from meshflow.agents.builder import Agent
    agent = Agent(name="teach-me", teachable=True)
    assert agent.teachable is True


def test_agent_builder_teachable_default():
    from meshflow.agents.builder import Agent
    agent = Agent(name="normal")
    assert agent.teachable is False
