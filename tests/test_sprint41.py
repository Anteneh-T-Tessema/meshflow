"""Sprint 41 — GenAI semantic conventions + live agent observability."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.observability.genai import (
    GenAI, MF,
    GenAISpanRecord, SpanStore,
    configure_telemetry, get_span_store, is_enabled,
    record_agent_step, record_handoff, record_tool_call,
    record_guardrail, record_healing_attempt,
    span,
    _infer_system,
)


# ── Setup: enable telemetry for all tests ─────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_store():
    configure_telemetry(enabled=True)
    get_span_store().clear()
    yield
    get_span_store().clear()


# ── GenAISpanRecord ───────────────────────────────────────────────────────────

class TestGenAISpanRecord:
    def test_auto_ids(self):
        rec = GenAISpanRecord(name="test")
        assert len(rec.trace_id) > 0
        assert len(rec.span_id) > 0

    def test_finish_sets_end_ns(self):
        rec = GenAISpanRecord(name="test")
        rec.finish("ok")
        assert rec.end_ns > 0
        assert rec.status == "ok"

    def test_finish_error(self):
        rec = GenAISpanRecord(name="test")
        rec.finish("error", "oops")
        assert rec.status == "error"
        assert rec.error_message == "oops"

    def test_duration_ms_positive(self):
        import time
        rec = GenAISpanRecord(name="test")
        time.sleep(0.01)
        rec.finish()
        assert rec.duration_ms > 0

    def test_to_dict(self):
        rec = GenAISpanRecord(name="meshflow.agent.step",
                              attributes={GenAI.REQUEST_MODEL: "claude-sonnet-4-6"})
        rec.finish()
        d = rec.to_dict()
        assert d["name"] == "meshflow.agent.step"
        assert d["attributes"][GenAI.REQUEST_MODEL] == "claude-sonnet-4-6"
        assert "duration_ms" in d
        assert "status" in d


# ── SpanStore ─────────────────────────────────────────────────────────────────

class TestSpanStore:
    def test_record_and_all(self):
        store = SpanStore()
        rec = GenAISpanRecord(name="test")
        rec.finish()
        store.record(rec)
        assert len(store.all()) == 1

    def test_by_name_filter(self):
        store = SpanStore()
        for name in ["a", "b", "a"]:
            r = GenAISpanRecord(name=name)
            r.finish()
            store.record(r)
        assert len(store.by_name("a")) == 2
        assert len(store.by_name("b")) == 1

    def test_by_trace_filter(self):
        store = SpanStore()
        r1 = GenAISpanRecord(name="x", trace_id="tid1")
        r2 = GenAISpanRecord(name="y", trace_id="tid2")
        r1.finish(); r2.finish()
        store.record(r1); store.record(r2)
        assert len(store.by_trace("tid1")) == 1

    def test_clear(self):
        store = SpanStore()
        store.record(GenAISpanRecord(name="x"))
        store.clear()
        assert store.count() == 0

    def test_max_spans_eviction(self):
        store = SpanStore(max_spans=3)
        for i in range(5):
            store.record(GenAISpanRecord(name=f"s{i}"))
        assert store.count() == 3

    def test_summary(self):
        store = SpanStore()
        r = GenAISpanRecord(name="meshflow.agent.step")
        r.finish("error")
        store.record(r)
        s = store.summary()
        assert s["total"] == 1
        assert s["errors"] == 1
        assert "meshflow.agent.step" in s["by_name"]


# ── span() context manager ────────────────────────────────────────────────────

class TestSpanContextManager:
    def test_records_on_success(self):
        store = get_span_store()
        with span("test.op", {GenAI.REQUEST_MODEL: "x"}) as s:
            s.attributes["custom"] = "val"
        spans = store.by_name("test.op")
        assert len(spans) == 1
        assert spans[0].attributes["custom"] == "val"
        assert spans[0].status == "ok"

    def test_records_error_on_exception(self):
        store = get_span_store()
        with pytest.raises(ValueError):
            with span("failing.op") as s:
                raise ValueError("boom")
        spans = store.by_name("failing.op")
        assert len(spans) == 1
        assert spans[0].status == "error"

    def test_disabled_does_not_record(self):
        configure_telemetry(enabled=False)
        store = get_span_store()
        with span("disabled.op"):
            pass
        assert len(store.by_name("disabled.op")) == 0
        configure_telemetry(enabled=True)


# ── Convenience emitters ──────────────────────────────────────────────────────

class TestConvenienceEmitters:
    def test_record_agent_step(self):
        rec = record_agent_step(
            agent_name="analyst",
            role="executor",
            model="claude-sonnet-4-6",
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.002,
            confidence=0.9,
            blocked=False,
        )
        assert rec.attributes[GenAI.SYSTEM] == "anthropic"
        assert rec.attributes[GenAI.INPUT_TOKENS] == 100
        assert rec.attributes[MF.AGENT_NAME] == "analyst"
        assert rec.status == "ok"
        spans = get_span_store().by_name("meshflow.agent.step")
        assert len(spans) >= 1

    def test_record_agent_step_blocked(self):
        rec = record_agent_step(
            agent_name="bot", role="executor", model="gpt-4o",
            tokens_in=10, tokens_out=0, cost_usd=0.0, confidence=0.0, blocked=True,
        )
        assert rec.status == "error"
        assert rec.attributes[GenAI.SYSTEM] == "openai"

    def test_record_handoff(self):
        rec = record_handoff("triage", "billing", reason="needs refund")
        assert rec.attributes[MF.HANDOFF_FROM] == "triage"
        assert rec.attributes[MF.HANDOFF_TO] == "billing"
        spans = get_span_store().by_name("meshflow.handoff")
        assert len(spans) >= 1

    def test_record_tool_call(self):
        rec = record_tool_call("search", "researcher", risk_tier="external_io", success=True)
        assert rec.attributes[MF.TOOL_NAME] == "search"
        assert rec.status == "ok"

    def test_record_guardrail(self):
        rec = record_guardrail("PIIBlockGuardrail", "agent-a", blocked=True)
        assert rec.attributes[MF.GUARDRAIL_BLOCK] is True
        assert rec.status == "error"

    def test_record_healing_attempt(self):
        rec = record_healing_attempt("healer", attempt=2, strategy="retry_same", success=False)
        assert rec.attributes[MF.HEALING_ATTEMPT] == 2
        assert rec.status == "error"


# ── _infer_system ─────────────────────────────────────────────────────────────

class TestInferSystem:
    def test_claude(self):      assert _infer_system("claude-sonnet-4-6") == "anthropic"
    def test_gpt(self):         assert _infer_system("gpt-4o") == "openai"
    def test_gemini(self):      assert _infer_system("gemini-pro") == "google"
    def test_llama(self):       assert _infer_system("llama3.2") == "meta"
    def test_unknown(self):     assert _infer_system("custom-model") == "unknown"


# ── configure_telemetry ───────────────────────────────────────────────────────

class TestConfigureTelemetry:
    def test_enable_disable(self):
        configure_telemetry(enabled=False)
        assert not is_enabled()
        configure_telemetry(enabled=True)
        assert is_enabled()

    def test_service_name(self):
        configure_telemetry(service_name="my-app")
        from meshflow.observability import genai as g
        assert g._service_name == "my-app"
        configure_telemetry(service_name="meshflow")


# ── Live Agent integration ────────────────────────────────────────────────────

class TestLiveAgentInstrumentation:
    @pytest.mark.asyncio
    async def test_agent_run_emits_span(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        get_span_store().clear()
        agent = Agent(name="instrumented", role="executor")
        await agent.run("hello world")
        spans = get_span_store().by_name("meshflow.agent.step")
        assert len(spans) >= 1
        s = spans[0]
        assert s.attributes[MF.AGENT_NAME] == "instrumented"
        assert GenAI.INPUT_TOKENS in s.attributes

    @pytest.mark.asyncio
    async def test_span_has_genai_attributes(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        get_span_store().clear()
        agent = Agent(name="genai-check", role="executor")
        await agent.run("test task")
        spans = get_span_store().by_name("meshflow.agent.step")
        assert len(spans) >= 1
        attrs = spans[0].attributes
        assert GenAI.SYSTEM in attrs
        assert GenAI.OPERATION in attrs
        assert GenAI.REQUEST_MODEL in attrs


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.observability.genai import (
            GenAI, MF, SpanStore, GenAISpanRecord,
            configure_telemetry, get_span_store, record_agent_step,
        )
        assert all(x is not None for x in [
            GenAI, MF, SpanStore, GenAISpanRecord,
            configure_telemetry, get_span_store, record_agent_step,
        ])
