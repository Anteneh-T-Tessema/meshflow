"""Unit tests for token and cost optimization features."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meshflow import Agent, token_budget
from meshflow.optimization.planner import ModelSizingAdvisor, TokenBudgetPlanner
from meshflow.optimization.tracker import BudgetExceededError, OptimizationTracker


# ── 1. Static Budget Planner & Model Advisor ──────────────────────────────────

def test_token_budget_planner_estimate():
    planner = TokenBudgetPlanner()
    assert planner.estimate_tokens("") == 0
    assert planner.estimate_tokens("Hello world") == int(2 * 1.35)
    # punctuation counting
    assert planner.estimate_tokens("Hello, world!") > 2 * 1.35


def test_token_budget_planner_plan_budget():
    planner = TokenBudgetPlanner()
    system = "You are a helpful assistant."
    messages = [
        {"role": "user", "content": "How are you today?"},
        {"role": "assistant", "content": "Doing well, thank you!"},
    ]
    t = MagicMock()
    t.name = "search"
    t.description = "Search the web"

    plan = planner.plan_budget(system, messages, tools=[t])
    assert plan["system_tokens"] > 0
    assert plan["message_tokens"] > 0
    assert plan["tool_tokens"] > 0
    assert plan["total_estimated_in"] == plan["system_tokens"] + plan["message_tokens"] + plan["tool_tokens"]


def test_model_sizing_advisor_recommendation():
    advisor = ModelSizingAdvisor()
    
    # Heuristic: simple task -> low tier
    assert advisor.recommend_model("Summarize this text") == advisor.LOW_TIER
    
    # Heuristic: complexity keyword -> high tier
    assert advisor.recommend_model("Optimize and refactor this sorting algorithm") == advisor.HIGH_TIER
    
    # Heuristic: long task -> high tier
    long_task = "Task description: " + "a " * 400
    assert advisor.recommend_model(long_task) == advisor.HIGH_TIER

    # Heuristic: multiple tools -> high tier
    t1 = MagicMock()
    t2 = MagicMock()
    assert advisor.recommend_model("Execute", tools=[t1, t2]) == advisor.HIGH_TIER


# ── 2. Budget Tracker Logic ───────────────────────────────────────────────────

def test_tracker_fails_on_budget_exceeded():
    tracker = OptimizationTracker(max_tokens=100, max_cost_usd=0.01, action="fail")
    tracker.add_usage(50, 0.005)
    assert len(tracker.alerts_triggered) == 0

    # Breach tokens
    with pytest.raises(BudgetExceededError, match="Token budget"):
        tracker.add_usage(60, 0.001)
    assert len(tracker.alerts_triggered) == 1

    # Reset tracker and breach cost
    tracker = OptimizationTracker(max_tokens=1000, max_cost_usd=0.01, action="fail")
    with pytest.raises(BudgetExceededError, match="Cost budget"):
        tracker.add_usage(10, 0.02)


def test_tracker_should_degrade_threshold():
    tracker = OptimizationTracker(max_tokens=100)
    assert tracker.should_degrade() is False

    # Consume 74%
    tracker.add_usage(74, 0.0)
    assert tracker.should_degrade() is False

    # Consume 75%
    tracker.add_usage(1, 0.0)
    assert tracker.should_degrade() is True


def test_tracker_compress_prompt():
    tracker = OptimizationTracker()
    system = "System prompt"
    messages = [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "Q3"},
    ]
    sys, msgs = tracker.compress_prompt(system, messages)
    assert sys == system
    # Should keep first message and the last two turns
    assert len(msgs) == 3
    assert msgs[0]["content"] == "Q1"
    assert msgs[1]["content"] == "A2"
    assert msgs[2]["content"] == "Q3"


def test_tracker_trim_rag_context():
    tracker = OptimizationTracker(max_tokens=500)
    
    class ChunkMock:
        def __init__(self, text: str):
            self.text = text
            
    chunks = [
        ChunkMock("Short chunk"),                 # ~15 tokens
        ChunkMock("Longer text chunk " * 300),       # ~900 tokens
        ChunkMock("Extra chunk" * 20),            # ~400 tokens
    ]
    
    # If limit is 400 tokens
    trimmed = tracker.trim_rag_context(chunks, 400)
    assert len(trimmed) == 1
    assert trimmed[0].text == "Short chunk"


def test_tracker_should_early_exit():
    tracker = OptimizationTracker()
    assert tracker.should_early_exit(0.85, 0.90) is False
    assert tracker.should_early_exit(0.92, 0.90) is True


# ── 3. Budget Decorator & Agent Integration ───────────────────────────────────

@pytest.mark.asyncio
async def test_budget_decorator_injects_tracker():
    @token_budget(max_tokens=1000, action="fail")
    async def run_dummy(task, context=None):
        assert context is not None
        assert "_optimization_tracker" in context
        tracker = context["_optimization_tracker"]
        assert tracker.max_tokens == 1000
        assert tracker.action == "fail"
        return "done"

    await run_dummy("test task")


@pytest.mark.asyncio
async def test_agent_swaps_model_on_budget_degrade(monkeypatch):
    monkeypatch.setenv("MESHFLOW_MOCK", "1")
    
    agent = Agent(name="test_agent", role="researcher")
    
    # Configure a context with a pre-degraded tracker
    tracker = OptimizationTracker(max_tokens=1000, fallback_model="claude-haiku-3-5")
    tracker.add_usage(760, 0.0) # 76% consumed, model should degrade
    
    context = {"_optimization_tracker": tracker}
    
    # Mock complete to assert it uses the fallback model
    provider_mock = MagicMock()
    provider_mock.complete = AsyncMock(return_value=("mocked response", 10, 0.0))
    agent.provider = provider_mock
    
    await agent.run("some task", context=context)
    
    # Check that model parameter sent to provider complete was claud-haiku-3-5
    provider_mock.complete.assert_called_once()
    kwargs = provider_mock.complete.call_args[1]
    assert kwargs["model"] == "claude-haiku-3-5"
    assert tracker.consumed_tokens == 770 # added 10


# ── 4. CLI Cost Regression Gate ───────────────────────────────────────────────

def test_cli_cost_regression_gate_breached():
    from meshflow.cli.main import _async_eval
    
    args = argparse.Namespace(
        eval_file="tests/evals.yaml",
        agent="",
        tags=[],
        concurrency=1,
        fail_under=0.0,
        save_baseline="",
        compare_baseline="baseline.json",
        fail_on_regression=False,
        max_cost_delta=0.10, # fail if token cost increase > 10%
        db=":memory:",
        save_to_ledger=False,
    )
    
    # Mock EvalSuite, EvalBaseline, etc.
    with patch("meshflow.eval.EvalSuite.from_yaml") as mock_suite_load:
        mock_suite = MagicMock()
        mock_result = MagicMock()
        mock_result.scenarios = []
        mock_result.pass_rate = 1.0
        mock_result.weighted_score = 1.0
        mock_result.total_tokens = 1500 # newer consumed 1500
        
        mock_suite.run = AsyncMock(return_value=mock_result)
        mock_suite_load.return_value = mock_suite
        
        with patch("os.path.exists", return_value=True):
            # Baseline loaded with 1000 tokens
            baseline_mock = MagicMock()
            baseline_mock.total_tokens = 1000 # older consumed 1000 (delta = 50% increase > 10% limit)
            baseline_mock.diff = MagicMock(return_value=MagicMock(report=lambda: "diff report", has_regressions=False))
            
            with patch("meshflow.eval.EvalBaseline.load", return_value=baseline_mock):
                with patch("sys.exit") as mock_exit:
                    import asyncio
                    asyncio.run(_async_eval(args))
                    mock_exit.assert_called_with(1)


@pytest.mark.asyncio
async def test_anthropic_prompt_caching_integration():
    from meshflow.agents.base import AnthropicProvider, _CACHE_MIN_CHARS

    with patch("meshflow.agents.base._require_anthropic") as mock_require:
        mock_client = MagicMock()
        mock_messages = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Mock response", type="text")]
        mock_response.usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )

        mock_messages.create = AsyncMock(return_value=mock_response)
        mock_client.messages = mock_messages
        mock_require.return_value.AsyncAnthropic.return_value = mock_client

        provider = AnthropicProvider()

        # Scenario A: short system prompt — no cache headers (below _CACHE_MIN_CHARS)
        await provider.complete(
            model="claude-3-5-sonnet",
            messages=[{"role": "user", "content": "Hi"}],
            system="short",
            max_tokens=100,
        )
        kwargs = mock_messages.create.call_args[1]
        assert kwargs["system"] == "short"
        assert kwargs.get("extra_headers") is None

        # Scenario B: long system prompt — always cached (no tracker needed)
        long_system = "x" * _CACHE_MIN_CHARS
        await provider.complete(
            model="claude-3-5-sonnet",
            messages=[{"role": "user", "content": "Hi"}],
            system=long_system,
            max_tokens=100,
        )
        kwargs = mock_messages.create.call_args[1]
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][0]["text"] == long_system
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "anthropic-beta" in (kwargs.get("extra_headers") or {})

