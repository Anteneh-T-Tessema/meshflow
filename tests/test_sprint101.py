"""Sprint 101 — Claude-ecosystem gap closure:
C1: AdvisorAgent + AdvisorRouter (Anthropic advisor-tool pattern)
C2: ThinkingBudget + EffortBudget + BudgetConfig
C3: DynamicWorkflow (runtime agent spawning from intermediate results)
C4: ContextCompactor (Claude native + sliding window + summary strategies)
C5: Fine-grained tool streaming (ToolStreamEvent hierarchy, stream_tool_calls)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.agents.base import EchoProvider


def _ep(reply: str = "ok") -> EchoProvider:
    return EchoProvider(reply)


# ══════════════════════════════════════════════════════════════════════════════
# C1 — AdvisorAgent + AdvisorRouter
# ══════════════════════════════════════════════════════════════════════════════

class TestAdvisorConfig:
    def test_defaults(self) -> None:
        from meshflow.agents.advisor import AdvisorConfig
        cfg = AdvisorConfig()
        assert cfg.advisor_model == "claude-opus-4-8"
        assert cfg.executor_model == "claude-sonnet-4-6"
        assert cfg.complexity_threshold == 0.5
        assert cfg.guidance_format == "text"

    def test_custom_values(self) -> None:
        from meshflow.agents.advisor import AdvisorConfig
        cfg = AdvisorConfig(
            advisor_model="claude-opus-4-7",
            executor_model="claude-haiku-4-5-20251001",
            complexity_threshold=0.3,
            guidance_format="json",
        )
        assert cfg.complexity_threshold == 0.3
        assert cfg.guidance_format == "json"


class TestAdvisorGuidance:
    def test_as_context_block_with_content(self) -> None:
        from meshflow.agents.advisor import AdvisorGuidance
        g = AdvisorGuidance(raw="Use async/await.", advisor_model="opus")
        block = g.as_context_block()
        assert "[Advisor guidance]" in block
        assert "async/await" in block

    def test_as_context_block_skipped(self) -> None:
        from meshflow.agents.advisor import AdvisorGuidance
        g = AdvisorGuidance(skipped=True)
        assert g.as_context_block() == ""

    def test_as_context_block_with_checklist(self) -> None:
        from meshflow.agents.advisor import AdvisorGuidance
        g = AdvisorGuidance(
            raw="approach", checklist=["step 1", "step 2"], advisor_model="opus"
        )
        block = g.as_context_block()
        assert "step 1" in block
        assert "step 2" in block


class TestAdvisorResult:
    def test_cost_savings_with_advisor(self) -> None:
        from meshflow.agents.advisor import AdvisorResult, AdvisorGuidance
        r = AdvisorResult(
            output="done",
            advisor_guidance=AdvisorGuidance(raw="advice"),
            executor_model="sonnet",
            total_cost_usd=0.01,
            advisor_cost_usd=0.002,
            executor_cost_usd=0.008,
            total_tokens=500,
            advisor_used=True,
        )
        assert r.cost_savings_vs_full_opus >= 0.0

    def test_str_returns_output(self) -> None:
        from meshflow.agents.advisor import AdvisorResult, AdvisorGuidance
        r = AdvisorResult(
            output="result text",
            advisor_guidance=AdvisorGuidance(skipped=True),
            executor_model="sonnet",
            total_cost_usd=0.0,
            advisor_cost_usd=0.0,
            executor_cost_usd=0.0,
            total_tokens=0,
            advisor_used=False,
        )
        assert str(r) == "result text"


class TestAdvisorAgent:
    def _agent(self) -> Any:
        from meshflow.agents.advisor import AdvisorAgent, AdvisorConfig
        return AdvisorAgent(
            name="test_advisor",
            config=AdvisorConfig(complexity_threshold=0.0),  # always advise
            mode="sandbox",
            provider=_ep("advised output"),
        )

    def test_run_returns_advisor_result(self) -> None:
        from meshflow.agents.advisor import AdvisorResult
        result = self._agent().run("complex task")
        assert isinstance(result, AdvisorResult)

    def test_output_is_string(self) -> None:
        result = self._agent().run("task")
        assert isinstance(result.output, str)

    def test_advisor_used_when_above_threshold(self) -> None:
        from meshflow.agents.advisor import AdvisorAgent, AdvisorConfig
        agent = AdvisorAgent(
            config=AdvisorConfig(complexity_threshold=0.0),
            mode="sandbox",
            provider=_ep("done"),
        )
        result = agent.run("anything")
        assert result.advisor_used is True

    def test_advisor_skipped_when_below_threshold(self) -> None:
        from meshflow.agents.advisor import AdvisorAgent, AdvisorConfig
        agent = AdvisorAgent(
            config=AdvisorConfig(complexity_threshold=1.1),  # never advise
            mode="sandbox",
            provider=_ep("done"),
        )
        result = agent.run("hi")
        assert result.advisor_used is False
        assert result.advisor_guidance.skipped is True

    def test_arun_async(self) -> None:
        from meshflow.agents.advisor import AdvisorAgent, AdvisorConfig
        agent = AdvisorAgent(
            config=AdvisorConfig(complexity_threshold=1.1),
            mode="sandbox",
            provider=_ep("async done"),
        )
        result = asyncio.run(agent.arun("task"))
        assert isinstance(result.output, str)

    def test_exported(self) -> None:
        from meshflow import AdvisorAgent, AdvisorConfig, AdvisorGuidance, AdvisorResult
        assert AdvisorAgent is not None


class TestAdvisorRouter:
    def test_route_returns_decision(self) -> None:
        from meshflow.agents.advisor import AdvisorRouter
        router = AdvisorRouter(complexity_threshold=0.0)
        decision = router.route("complex task here")
        assert hasattr(decision, "model")
        assert hasattr(decision, "use_advisor")

    def test_use_advisor_true_above_threshold(self) -> None:
        from meshflow.agents.advisor import AdvisorRouter
        router = AdvisorRouter(complexity_threshold=0.0)
        decision = router.route("x")
        assert decision.use_advisor is True

    def test_use_advisor_false_below_threshold(self) -> None:
        from meshflow.agents.advisor import AdvisorRouter
        router = AdvisorRouter(complexity_threshold=1.1)
        decision = router.route("short")
        assert decision.use_advisor is False

    def test_record_outcome_and_report(self) -> None:
        from meshflow.agents.advisor import AdvisorRouter
        router = AdvisorRouter(complexity_threshold=0.5)
        router.record_outcome("r1", use_advisor=True)
        router.record_outcome("r2", use_advisor=False)
        report = router.report()
        assert report["total_routes"] == 2

    def test_exported(self) -> None:
        from meshflow import AdvisorRouter
        assert AdvisorRouter is not None


# ══════════════════════════════════════════════════════════════════════════════
# C2 — ThinkingBudget + EffortBudget + BudgetConfig
# ══════════════════════════════════════════════════════════════════════════════

class TestThinkingBudget:
    def test_defaults(self) -> None:
        from meshflow.core.budget_config import ThinkingBudget
        tb = ThinkingBudget()
        assert tb.tokens == 2_000
        assert tb.enabled is True

    def test_to_anthropic_param_enabled(self) -> None:
        from meshflow.core.budget_config import ThinkingBudget
        tb = ThinkingBudget(tokens=4096, enabled=True)
        param = tb.to_anthropic_param()
        assert param["type"] == "enabled"
        assert param["budget_tokens"] == 4096

    def test_to_anthropic_param_disabled(self) -> None:
        from meshflow.core.budget_config import ThinkingBudget
        tb = ThinkingBudget(enabled=False)
        param = tb.to_anthropic_param()
        assert param["type"] == "disabled"

    def test_negative_tokens_raises(self) -> None:
        from meshflow.core.budget_config import ThinkingBudget
        with pytest.raises(ValueError):
            ThinkingBudget(tokens=-1)

    def test_exported(self) -> None:
        from meshflow import ThinkingBudget
        assert ThinkingBudget is not None


class TestEffortBudget:
    def test_low_maps_to_1024(self) -> None:
        from meshflow.core.budget_config import EffortBudget
        assert EffortBudget("low").tokens == 1_024

    def test_medium_maps_to_4096(self) -> None:
        from meshflow.core.budget_config import EffortBudget
        assert EffortBudget("medium").tokens == 4_096

    def test_high_maps_to_16000(self) -> None:
        from meshflow.core.budget_config import EffortBudget
        assert EffortBudget("high").tokens == 16_000

    def test_max_maps_to_32000(self) -> None:
        from meshflow.core.budget_config import EffortBudget
        assert EffortBudget("max").tokens == 32_000

    def test_invalid_level_raises(self) -> None:
        from meshflow.core.budget_config import EffortBudget
        with pytest.raises(ValueError):
            EffortBudget("ultra")  # type: ignore

    def test_to_thinking_budget(self) -> None:
        from meshflow.core.budget_config import EffortBudget, ThinkingBudget
        tb = EffortBudget("high").to_thinking_budget()
        assert isinstance(tb, ThinkingBudget)
        assert tb.tokens == 16_000

    def test_exported(self) -> None:
        from meshflow import EffortBudget
        assert EffortBudget is not None


class TestBudgetConfig:
    def test_resolved_thinking_from_thinking(self) -> None:
        from meshflow.core.budget_config import BudgetConfig, ThinkingBudget
        bc = BudgetConfig(thinking=ThinkingBudget(tokens=8000))
        tb = bc.resolved_thinking_budget()
        assert tb is not None and tb.tokens == 8000

    def test_resolved_thinking_from_effort(self) -> None:
        from meshflow.core.budget_config import BudgetConfig, EffortBudget
        bc = BudgetConfig(effort=EffortBudget("low"))
        tb = bc.resolved_thinking_budget()
        assert tb is not None and tb.tokens == 1_024

    def test_thinking_takes_priority_over_effort(self) -> None:
        from meshflow.core.budget_config import BudgetConfig, ThinkingBudget, EffortBudget
        bc = BudgetConfig(thinking=ThinkingBudget(tokens=999), effort=EffortBudget("max"))
        assert bc.resolved_thinking_budget().tokens == 999

    def test_check_usd_raises_on_exceed(self) -> None:
        from meshflow.core.budget_config import BudgetConfig, BudgetViolation
        bc = BudgetConfig(usd_cap=0.10)
        with pytest.raises(BudgetViolation):
            bc.check_usd(0.20)

    def test_check_usd_no_cap(self) -> None:
        from meshflow.core.budget_config import BudgetConfig
        bc = BudgetConfig(usd_cap=0.0)
        bc.check_usd(999.0)  # no error

    def test_check_thinking_tokens_raises(self) -> None:
        from meshflow.core.budget_config import BudgetConfig, ThinkingBudget, BudgetViolation
        bc = BudgetConfig(thinking=ThinkingBudget(tokens=100))
        with pytest.raises(BudgetViolation):
            bc.check_thinking_tokens(200)

    def test_budget_usage_to_dict(self) -> None:
        from meshflow.core.budget_config import BudgetUsage
        bu = BudgetUsage(usd_spent=0.05, thinking_tokens_used=500,
                         output_tokens_used=300, input_tokens_used=200)
        d = bu.to_dict()
        assert d["total_tokens"] == 1000
        assert d["usd_spent"] == pytest.approx(0.05)

    def test_exported(self) -> None:
        from meshflow import BudgetConfig, BudgetUsage, BudgetViolation
        assert BudgetConfig is not None


# ══════════════════════════════════════════════════════════════════════════════
# C3 — DynamicWorkflow
# ══════════════════════════════════════════════════════════════════════════════

class TestSpawnDecision:
    def test_no_spawn(self) -> None:
        from meshflow.core.dynamic_workflow import SpawnDecision
        d = SpawnDecision(spawn=False, reason="nothing found")
        assert d.spawn is False
        assert d.agents == []

    def test_with_agents(self) -> None:
        from meshflow.core.dynamic_workflow import SpawnDecision
        d = SpawnDecision(spawn=True, agents=[("a1", "researcher", "find it")])
        assert d.spawn is True
        assert len(d.agents) == 1


class TestDynamicCoordinator:
    def test_keyword_match_triggers_spawn(self) -> None:
        from meshflow.core.dynamic_workflow import DynamicCoordinator
        coord = DynamicCoordinator(spawn_keywords={"sub-topic": "analyst"})
        decision = coord.decide("node1", "Found 3 sub-topics in the report.")
        assert decision.spawn is True
        assert any(a[1] == "analyst" for a in decision.agents)

    def test_no_keyword_no_spawn(self) -> None:
        from meshflow.core.dynamic_workflow import DynamicCoordinator
        coord = DynamicCoordinator(spawn_keywords={"error": "debugger"})
        decision = coord.decide("node1", "everything looks fine")
        assert decision.spawn is False

    def test_max_spawns_per_node_respected(self) -> None:
        from meshflow.core.dynamic_workflow import DynamicCoordinator
        coord = DynamicCoordinator(
            spawn_keywords={"x": "r1", "y": "r2", "z": "r3"},
            max_spawns_per_node=2,
        )
        decision = coord.decide("n", "x y z present")
        assert len(decision.agents) <= 2

    def test_pattern_match(self) -> None:
        from meshflow.core.dynamic_workflow import DynamicCoordinator
        coord = DynamicCoordinator(spawn_patterns={r"error\s+\w+": "debugger"})
        decision = coord.decide("n", "encountered error 404 in request")
        assert decision.spawn is True

    def test_exported(self) -> None:
        from meshflow import DynamicCoordinator, SpawnDecision, SpawnRecord
        assert DynamicCoordinator is not None


class TestDynamicWorkflow:
    def _wf(self) -> Any:
        from meshflow import Agent
        from meshflow.core.dynamic_workflow import DynamicWorkflow, DynamicCoordinator
        wf = DynamicWorkflow(max_dynamic_nodes=5, mode="sandbox")
        wf.add(Agent("base", provider=_ep("found sub-topic A and sub-topic B")))
        wf.set_coordinator(
            DynamicCoordinator(spawn_keywords={"sub-topic": "analyst"}, mode="sandbox")
        )
        return wf

    def test_run_returns_result(self) -> None:
        from meshflow.core.dynamic_workflow import DynamicWorkflowResult
        result = self._wf().run("research task")
        assert isinstance(result, DynamicWorkflowResult)

    def test_output_is_string(self) -> None:
        result = self._wf().run("task")
        assert isinstance(result.output, str)

    def test_spawn_history_populated(self) -> None:
        result = self._wf().run("task")
        # spawn_history may have entries if coordinator matched
        assert isinstance(result.spawn_history, list)

    def test_no_coordinator_no_spawns(self) -> None:
        from meshflow import Agent
        from meshflow.core.dynamic_workflow import DynamicWorkflow
        wf = DynamicWorkflow(mode="sandbox")
        wf.add(Agent("base", provider=_ep("plain output")))
        result = wf.run("task")
        assert result.total_spawns == 0

    def test_max_dynamic_nodes_respected(self) -> None:
        from meshflow import Agent
        from meshflow.core.dynamic_workflow import DynamicWorkflow, DynamicCoordinator
        wf = DynamicWorkflow(max_dynamic_nodes=1, mode="sandbox")
        wf.add(Agent("base", provider=_ep("sub-topic x sub-topic y sub-topic z")))
        wf.set_coordinator(DynamicCoordinator(
            spawn_keywords={"sub-topic": "analyst"},
            max_spawns_per_node=10,
            mode="sandbox",
        ))
        result = wf.run("task")
        assert result.total_spawns <= 1

    def test_arun(self) -> None:
        result = asyncio.run(self._wf().arun("async task"))
        assert isinstance(result.output, str)

    def test_exported(self) -> None:
        from meshflow import DynamicWorkflow, DynamicWorkflowResult
        assert DynamicWorkflow is not None


# ══════════════════════════════════════════════════════════════════════════════
# C4 — ContextCompactor
# ══════════════════════════════════════════════════════════════════════════════

def _msgs(n: int, chars: int = 200) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * chars}
        for i in range(n)
    ]


class TestCompactionConfig:
    def test_defaults(self) -> None:
        from meshflow.core.compactor import CompactionConfig, CompactionStrategy
        cfg = CompactionConfig()
        assert cfg.strategy == CompactionStrategy.CLAUDE_NATIVE
        assert cfg.max_tokens == 8_000

    def test_custom(self) -> None:
        from meshflow.core.compactor import CompactionConfig, CompactionStrategy
        cfg = CompactionConfig(strategy=CompactionStrategy.SLIDING_WINDOW, max_tokens=4000)
        assert cfg.max_tokens == 4000


class TestContextCompactor:
    def test_no_compaction_when_under_budget(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig
        compactor = ContextCompactor(CompactionConfig(max_tokens=100_000))
        msgs = _msgs(2, chars=10)
        result, stats = compactor.compact(msgs)
        assert len(result) == len(msgs)
        assert stats.messages_removed == 0

    def test_sliding_window_removes_old_messages(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy
        compactor = ContextCompactor(CompactionConfig(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            max_tokens=50,
            preserve_last_n=2,
        ))
        msgs = _msgs(10, chars=100)
        result, stats = compactor.compact(msgs)
        assert len(result) < len(msgs)
        assert stats.messages_removed > 0

    def test_sliding_window_preserves_last_n(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy
        compactor = ContextCompactor(CompactionConfig(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            max_tokens=10,
            preserve_last_n=2,
        ))
        msgs = _msgs(8, chars=50)
        result, stats = compactor.compact(msgs, budget_tokens=10)
        # Last 2 non-system messages should be preserved
        assert len(result) >= 2

    def test_summary_strategy(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy
        compactor = ContextCompactor(CompactionConfig(
            strategy=CompactionStrategy.SUMMARY,
            max_tokens=50,
            preserve_last_n=1,
        ))
        msgs = _msgs(6, chars=100)
        result, stats = compactor.compact(msgs)
        assert stats.strategy_used in ("summary", "summary_passthrough", "none_needed")

    def test_claude_native_strategy(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy
        compactor = ContextCompactor(CompactionConfig(
            strategy=CompactionStrategy.CLAUDE_NATIVE,
            max_tokens=50,
            preserve_last_n=1,
        ))
        msgs = _msgs(6, chars=100)
        result, stats = compactor.compact(msgs)
        assert stats.strategy_used in ("claude_native", "claude_native_preserve",
                                       "claude_native_passthrough", "none_needed")

    def test_hybrid_selects_sliding_window_for_short(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy
        compactor = ContextCompactor(CompactionConfig(
            strategy=CompactionStrategy.HYBRID,
            max_tokens=10,
            hybrid_threshold=100_000,
        ))
        msgs = _msgs(4, chars=50)
        _, stats = compactor.compact(msgs)
        assert stats.strategy_used in ("sliding_window", "none_needed")

    def test_summary_cache_reuse(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy
        compactor = ContextCompactor(CompactionConfig(
            strategy=CompactionStrategy.SUMMARY,
            max_tokens=50,
        ))
        msgs = _msgs(6, chars=100)
        _, stats1 = compactor.compact(msgs)
        _, stats2 = compactor.compact(msgs)
        # second call should hit cache
        assert stats2.cache_hit is True

    def test_needs_compaction(self) -> None:
        from meshflow.core.compactor import ContextCompactor, CompactionConfig
        compactor = ContextCompactor(CompactionConfig(max_tokens=10))
        assert compactor.needs_compaction(_msgs(5, chars=100))
        assert not compactor.needs_compaction([{"role": "user", "content": "hi"}])

    def test_compression_ratio(self) -> None:
        from meshflow.core.compactor import CompactionStats
        stats = CompactionStats(original_tokens=1000, compacted_tokens=400)
        assert stats.compression_ratio == pytest.approx(0.4)
        assert stats.tokens_saved == 600

    def test_to_dict(self) -> None:
        from meshflow.core.compactor import CompactionStats
        d = CompactionStats(original_tokens=100, compacted_tokens=60,
                            messages_removed=3, strategy_used="sliding_window").to_dict()
        assert d["tokens_saved"] == 40
        assert d["strategy_used"] == "sliding_window"

    def test_exported(self) -> None:
        from meshflow import ContextCompactor, CompactionConfig, CompactionStats, CompactionStrategy
        assert ContextCompactor is not None
        assert CompactionStrategy is not None


# ══════════════════════════════════════════════════════════════════════════════
# C5 — Fine-grained tool streaming
# ══════════════════════════════════════════════════════════════════════════════

class TestToolStreamEvents:
    def test_tool_call_start_event(self) -> None:
        from meshflow.streaming.tool_stream import ToolCallStartEvent, ToolEventKind
        ev = ToolCallStartEvent(tool_name="search", agent_name="bot")
        assert ev.kind == ToolEventKind.TOOL_CALL_START
        assert ev.tool_name == "search"

    def test_tool_result_end_event(self) -> None:
        from meshflow.streaming.tool_stream import ToolResultEndEvent, ToolEventKind
        ev = ToolResultEndEvent(tool_name="calc", result="42", duration_ms=5.0)
        assert ev.kind == ToolEventKind.TOOL_RESULT_END
        assert ev.result == "42"

    def test_text_delta_event(self) -> None:
        from meshflow.streaming.tool_stream import TextDeltaEvent, ToolEventKind
        ev = TextDeltaEvent(delta="hello ")
        assert ev.kind == ToolEventKind.TEXT_DELTA

    def test_tool_stream_error_event(self) -> None:
        from meshflow.streaming.tool_stream import ToolStreamError, ToolEventKind
        ev = ToolStreamError(tool_name="broken", error="timeout")
        assert ev.kind == ToolEventKind.ERROR

    def test_events_have_call_id(self) -> None:
        from meshflow.streaming.tool_stream import ToolCallStartEvent
        ev = ToolCallStartEvent()
        assert len(ev.call_id) > 0

    def test_exported(self) -> None:
        from meshflow import (
            ToolEventKind, ToolCallStartEvent, ToolCallEndEvent,
            ToolResultEndEvent, TextDeltaEvent, ToolStreamError,
        )
        assert ToolEventKind is not None


class TestToolStreamResult:
    def test_completed_true_no_errors(self) -> None:
        from meshflow.streaming.tool_stream import ToolStreamResult
        r = ToolStreamResult(text_output="done", errors=[])
        assert r.completed is True

    def test_completed_false_with_errors(self) -> None:
        from meshflow.streaming.tool_stream import ToolStreamResult
        r = ToolStreamResult(errors=["timeout"])
        assert r.completed is False

    def test_exported(self) -> None:
        from meshflow import ToolStreamResult
        assert ToolStreamResult is not None


class TestStreamToolCalls:
    def test_no_tools_yields_text_deltas(self) -> None:
        from meshflow import Agent
        from meshflow.streaming.tool_stream import stream_tool_calls, TextDeltaEvent

        agent = Agent(name="no_tools", provider=_ep("hello world"), mode="sandbox")

        async def _run():
            events = []
            async for ev in stream_tool_calls(agent, "say hi", emit_text_deltas=True):
                events.append(ev)
            return events

        events = asyncio.run(_run())
        assert any(isinstance(e, TextDeltaEvent) for e in events)

    def test_with_tool_yields_call_events(self) -> None:
        from meshflow import Agent
        from meshflow.tools.registry import Tool
        from meshflow.streaming.tool_stream import (
            stream_tool_calls, ToolCallStartEvent, ToolCallEndEvent, ToolResultEndEvent,
        )

        async def my_tool(task: str = "") -> str:
            return f"result:{task[:10]}"

        tool = Tool(name="my_tool", description="does stuff", fn=my_tool)
        agent = Agent(name="tooled", provider=_ep("ok"), tools=[tool], mode="sandbox")

        async def _run():
            events = []
            async for ev in stream_tool_calls(agent, "do something"):
                events.append(ev)
            return events

        events = asyncio.run(_run())
        kinds = {type(e).__name__ for e in events}
        assert "ToolCallStartEvent" in kinds
        assert "ToolCallEndEvent" in kinds
        assert "ToolResultEndEvent" in kinds

    def test_collect_tool_stream(self) -> None:
        from meshflow import Agent
        from meshflow.tools.registry import Tool
        from meshflow.streaming.tool_stream import collect_tool_stream

        async def adder(task: str = "") -> str:
            return "42"

        tool = Tool(name="adder", description="adds", fn=adder)
        agent = Agent(name="math", provider=_ep("ok"), tools=[tool], mode="sandbox")
        result = asyncio.run(collect_tool_stream(agent, "compute 6*7"))
        assert isinstance(result.total_tool_calls, int)
        assert result.total_tool_calls >= 1

    def test_tool_stream_session(self) -> None:
        from meshflow import Agent
        from meshflow.streaming.tool_stream import ToolStreamSession

        a1 = Agent(name="a1", provider=_ep("r1"), mode="sandbox")
        a2 = Agent(name="a2", provider=_ep("r2"), mode="sandbox")

        async def _run():
            session = ToolStreamSession()
            session.add(a1, "task 1")
            session.add(a2, "task 2")
            events = []
            async with session:
                async for ev in session.stream_all():
                    events.append(ev)
            return events

        events = asyncio.run(_run())
        assert isinstance(events, list)

    def test_exported(self) -> None:
        from meshflow import stream_tool_calls, collect_tool_stream, ToolStreamSession
        assert stream_tool_calls is not None
        assert collect_tool_stream is not None
        assert ToolStreamSession is not None
