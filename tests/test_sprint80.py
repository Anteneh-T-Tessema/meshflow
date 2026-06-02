"""Sprint 80 — Extended thinking + always-on prompt caching + cache metrics."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_usage(input_tokens=100, output_tokens=50, cache_creation=0, cache_read=0):
    u = MagicMock()
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    u.cache_creation_input_tokens = cache_creation
    u.cache_read_input_tokens = cache_read
    return u


def _make_response(text="hello", usage=None, stop_reason="end_turn"):
    r = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    r.content = [block]
    r.usage = usage or _make_usage()
    r.stop_reason = stop_reason
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Cache stats ContextVar
# ═══════════════════════════════════════════════════════════════════════════════

class TestCacheStats:
    def test_initial_state_is_empty(self):
        from meshflow.agents.base import get_cache_stats, reset_cache_stats
        reset_cache_stats()
        assert get_cache_stats() == {}

    def test_record_cache_usage_accumulates(self):
        from meshflow.agents.base import _record_cache_usage, get_cache_stats, reset_cache_stats
        reset_cache_stats()
        usage = _make_usage(cache_creation=200, cache_read=0)
        _record_cache_usage(usage)
        stats = get_cache_stats()
        assert stats["cache_creation_tokens"] == 200
        assert "cache_read_tokens" not in stats or stats.get("cache_read_tokens", 0) == 0

    def test_record_cache_read_accumulates(self):
        from meshflow.agents.base import _record_cache_usage, get_cache_stats, reset_cache_stats
        reset_cache_stats()
        _record_cache_usage(_make_usage(cache_read=150))
        assert get_cache_stats()["cache_read_tokens"] == 150

    def test_multiple_calls_sum(self):
        from meshflow.agents.base import _record_cache_usage, get_cache_stats, reset_cache_stats
        reset_cache_stats()
        _record_cache_usage(_make_usage(cache_read=100))
        _record_cache_usage(_make_usage(cache_read=50))
        assert get_cache_stats()["cache_read_tokens"] == 150

    def test_reset_clears_stats(self):
        from meshflow.agents.base import _record_cache_usage, get_cache_stats, reset_cache_stats
        _record_cache_usage(_make_usage(cache_read=99))
        reset_cache_stats()
        assert get_cache_stats() == {}

    def test_no_cache_in_usage_is_noop(self):
        from meshflow.agents.base import _record_cache_usage, get_cache_stats, reset_cache_stats
        reset_cache_stats()
        usage = MagicMock(spec=[])  # no cache attributes
        _record_cache_usage(usage)  # must not raise
        assert get_cache_stats() == {}


# ═══════════════════════════════════════════════════════════════════════════════
# AnthropicProvider — always-on caching
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnthropicProviderCaching:
    def _provider(self):
        from meshflow.agents.base import AnthropicProvider
        p = AnthropicProvider.__new__(AnthropicProvider)
        p._client = MagicMock()
        return p

    def test_short_system_not_cached(self):
        from meshflow.agents.base import AnthropicProvider
        system_param, headers = AnthropicProvider._cache_system("short")
        assert system_param == "short"
        assert headers == {}

    def test_long_system_gets_cache_control(self):
        from meshflow.agents.base import AnthropicProvider, _CACHE_MIN_CHARS
        long_sys = "x" * _CACHE_MIN_CHARS
        system_param, headers = AnthropicProvider._cache_system(long_sys)
        assert isinstance(system_param, list)
        assert system_param[0]["cache_control"] == {"type": "ephemeral"}
        assert "anthropic-beta" in headers

    @pytest.mark.asyncio
    async def test_complete_records_cache_tokens(self):
        from meshflow.agents.base import AnthropicProvider, _CACHE_MIN_CHARS, reset_cache_stats, get_cache_stats
        p = self._provider()
        reset_cache_stats()

        response = _make_response(usage=_make_usage(cache_creation=512, cache_read=0))
        p._client.messages.create = AsyncMock(return_value=response)

        long_system = "y" * _CACHE_MIN_CHARS
        await p.complete("claude-haiku-4-5-20251001", [{"role": "user", "content": "hi"}], long_system, 100)
        stats = get_cache_stats()
        assert stats.get("cache_creation_tokens", 0) == 512

    @pytest.mark.asyncio
    async def test_complete_no_cache_on_short_system(self):
        from meshflow.agents.base import AnthropicProvider, reset_cache_stats, get_cache_stats
        p = self._provider()
        reset_cache_stats()

        response = _make_response(usage=_make_usage())  # 0 cache tokens
        p._client.messages.create = AsyncMock(return_value=response)

        await p.complete("claude-haiku-4-5-20251001", [{"role": "user", "content": "hi"}], "short", 100)
        stats = get_cache_stats()
        assert stats.get("cache_creation_tokens", 0) == 0
        assert stats.get("cache_read_tokens", 0) == 0

    @pytest.mark.asyncio
    async def test_complete_passes_no_extra_headers_for_short(self):
        from meshflow.agents.base import AnthropicProvider
        p = self._provider()

        response = _make_response()
        create_mock = AsyncMock(return_value=response)
        p._client.messages.create = create_mock

        await p.complete("claude-haiku-4-5-20251001", [], "short sys", 100)
        call_kwargs = create_mock.call_args.kwargs
        # extra_headers should be None for short system prompts
        assert call_kwargs.get("extra_headers") is None


# ═══════════════════════════════════════════════════════════════════════════════
# AnthropicProvider — extended thinking
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtendedThinking:
    def _provider(self):
        from meshflow.agents.base import AnthropicProvider
        p = AnthropicProvider.__new__(AnthropicProvider)
        p._client = MagicMock()
        return p

    def _thinking_response(self, thinking_text="step by step", answer="42"):
        r = MagicMock()
        think_block = MagicMock()
        think_block.type = "thinking"
        think_block.thinking = thinking_text
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = answer
        r.content = [think_block, text_block]
        r.usage = _make_usage()
        r.stop_reason = "end_turn"
        return r

    @pytest.mark.asyncio
    async def test_complete_with_thinking_returns_5tuple(self):
        from meshflow.agents.base import AnthropicProvider
        p = self._provider()
        p._client.messages.create = AsyncMock(return_value=self._thinking_response())

        result = await p.complete_with_thinking(
            "claude-opus-4-8", [], "system", 2000, thinking_budget=1024
        )
        assert len(result) == 5
        content, tokens, cost, thinking_summary, thinking_tokens = result
        assert content == "42"
        assert isinstance(tokens, int)
        assert thinking_summary  # non-empty

    @pytest.mark.asyncio
    async def test_complete_with_thinking_uses_interleaved_beta(self):
        from meshflow.agents.base import AnthropicProvider
        p = self._provider()
        create_mock = AsyncMock(return_value=self._thinking_response())
        p._client.messages.create = create_mock

        await p.complete_with_thinking("claude-opus-4-8", [], "sys", 2000, 512)
        call_kwargs = create_mock.call_args.kwargs
        assert "interleaved-thinking" in call_kwargs.get("extra_headers", {}).get("anthropic-beta", "")

    @pytest.mark.asyncio
    async def test_complete_with_thinking_fallback_on_error(self):
        from meshflow.agents.base import AnthropicProvider
        p = self._provider()

        # First call (with thinking) raises, second call (regular) succeeds
        regular_response = _make_response("fallback answer")
        p._client.messages.create = AsyncMock(
            side_effect=[Exception("model does not support thinking"), regular_response]
        )

        content, tokens, cost, thinking_summary, thinking_tokens = await p.complete_with_thinking(
            "claude-haiku-4-5-20251001", [], "sys", 200, 512
        )
        assert content == "fallback answer"
        assert thinking_summary == ""
        assert thinking_tokens == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Agent.thinking parameter
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentThinkingParam:
    def test_agent_has_thinking_fields(self):
        from meshflow import Agent
        a = Agent(name="thinker", thinking=True, thinking_budget=4000)
        assert a.thinking is True
        assert a.thinking_budget == 4000

    def test_agent_thinking_defaults_false(self):
        from meshflow import Agent
        a = Agent(name="plain")
        assert a.thinking is False
        assert a.thinking_budget == 2000

    def test_built_agent_has_thinking_attrs(self):
        from meshflow import Agent
        a = Agent(name="t", thinking=True, thinking_budget=3000)
        built = a._build()
        assert built._thinking is True
        assert built._thinking_budget == 3000

    @pytest.mark.asyncio
    async def test_step_uses_think_extended_when_thinking_enabled(self):
        from meshflow import Agent
        a = Agent(name="thinker", thinking=True, thinking_budget=1024)
        built = a._build()

        extended_result = ("deep answer", 200, 0.01, "I thought carefully", 150)
        built._think_extended = AsyncMock(return_value=extended_result)

        result = await built.step("hard question", {})
        assert result["result"] == "deep answer"
        assert result["thinking_summary"] == "I thought carefully"
        assert result["thinking_tokens"] == 150
        built._think_extended.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step_includes_cache_stats_in_result(self):
        from meshflow import Agent
        from meshflow.agents.base import reset_cache_stats, _record_cache_usage
        reset_cache_stats()
        _record_cache_usage(_make_usage(cache_read=300))

        a = Agent(name="cached")
        built = a._build()
        built.think = AsyncMock(return_value=("cached answer", 50, 0.001))

        result = await built.step("task", {})
        assert result["cache_read_tokens"] == 300


# ═══════════════════════════════════════════════════════════════════════════════
# _BuiltAgent._think_extended — non-Anthropic fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestThinkExtendedFallback:
    @pytest.mark.asyncio
    async def test_non_anthropic_provider_falls_back(self):
        from meshflow import Agent
        from meshflow.agents.base import EchoProvider
        a = Agent(name="echo", thinking=True, provider=EchoProvider())
        built = a._build()
        # EchoProvider is not AnthropicProvider → falls back to think()
        built.think = AsyncMock(return_value=("echo reply", 10, 0.0))
        content, tokens, cost, summary, think_tok = await built._think_extended([])
        assert content == "echo reply"
        assert summary == ""
        assert think_tok == 0


# ═══════════════════════════════════════════════════════════════════════════════
# StepOutcome cache fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestStepOutcomeCacheFields:
    def test_default_zero(self):
        from meshflow.core.executor import StepOutcome
        o = StepOutcome(ok=True, data={}, agent_id="a", role="executor")
        assert o.cache_read_tokens == 0
        assert o.cache_creation_tokens == 0

    def test_fields_set(self):
        from meshflow.core.executor import StepOutcome
        o = StepOutcome(
            ok=True, data={}, agent_id="a", role="executor",
            cache_read_tokens=200, cache_creation_tokens=50,
        )
        assert o.cache_read_tokens == 200
        assert o.cache_creation_tokens == 50


# ═══════════════════════════════════════════════════════════════════════════════
# RunResult cache fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunResultCacheFields:
    def test_defaults_zero(self):
        from meshflow.core.schemas import RunResult, RunStatus
        r = RunResult(
            run_id="r", status=RunStatus.COMPLETED, output="",
            agent_states={}, total_cost_usd=0, total_tokens=0,
            total_carbon_g=0, duration_s=0, checkpoints=[],
            ledger_entries=0, trace_id="t",
        )
        assert r.cache_read_tokens == 0
        assert r.cache_creation_tokens == 0

    def test_fields_set(self):
        from meshflow.core.schemas import RunResult, RunStatus
        r = RunResult(
            run_id="r", status=RunStatus.COMPLETED, output="",
            agent_states={}, total_cost_usd=0, total_tokens=0,
            total_carbon_g=0, duration_s=0, checkpoints=[],
            ledger_entries=0, trace_id="t",
            cache_read_tokens=800, cache_creation_tokens=200,
        )
        assert r.cache_read_tokens == 800
        assert r.cache_creation_tokens == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Cloud reporter — cache_hit_rate
# ═══════════════════════════════════════════════════════════════════════════════

class TestCacheHitRate:
    def _make_result(self, cache_read=0, cache_creation=0):
        from meshflow.core.schemas import RunResult, RunStatus
        return RunResult(
            run_id="r", status=RunStatus.COMPLETED, output="",
            agent_states={}, total_cost_usd=0, total_tokens=0,
            total_carbon_g=0, duration_s=0, checkpoints=[],
            ledger_entries=0, trace_id="t",
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

    def test_zero_when_no_cache(self):
        from meshflow.cloud.reporter import _cache_hit_rate
        assert _cache_hit_rate(self._make_result()) == 0.0

    def test_full_hit(self):
        from meshflow.cloud.reporter import _cache_hit_rate
        r = self._make_result(cache_read=1000, cache_creation=0)
        assert _cache_hit_rate(r) == 1.0

    def test_partial_hit(self):
        from meshflow.cloud.reporter import _cache_hit_rate
        r = self._make_result(cache_read=300, cache_creation=700)
        assert _cache_hit_rate(r) == pytest.approx(0.3, abs=1e-4)

    def test_only_creation_is_zero_hit(self):
        from meshflow.cloud.reporter import _cache_hit_rate
        r = self._make_result(cache_read=0, cache_creation=500)
        assert _cache_hit_rate(r) == 0.0

    def test_result_without_cache_attrs(self):
        from meshflow.cloud.reporter import _cache_hit_rate
        # Legacy result object with no cache fields
        r = MagicMock(spec=[])
        assert _cache_hit_rate(r) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# StepRecord metadata — cache tokens
# ═══════════════════════════════════════════════════════════════════════════════

class TestStepRecordCacheMetadata:
    def test_step_record_metadata_stores_cache_tokens(self):
        from meshflow.core.runtime import StepRecord
        record = StepRecord(
            run_id="r", step_id="s", node_id="n", node_kind="python",
            input_task="task", output_content="out", verdict="commit",
            blocked=False, block_reason="", uncertainty=0.0,
            cost_usd=0.001, tokens_used=100, carbon_gco2=0.0,
            duration_ms=10.0, timestamp="2026-01-01T00:00:00Z",
            metadata={"cache_read_tokens": 200, "cache_creation_tokens": 50},
        )
        assert record.metadata["cache_read_tokens"] == 200
        assert record.metadata["cache_creation_tokens"] == 50
