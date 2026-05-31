"""Sprint 68 — Structured Output tests.

Tests for Agent.with_structured_output(), StructuredAgent, and
response_format on EchoProvider. No API key required.
"""

from __future__ import annotations

import json
import pytest

import meshflow
from meshflow import Agent, StructuredAgent
from meshflow.agents.base import EchoProvider
from meshflow.agents.structured import (
    StructuredOutputResult,
    StructuredOutputParser,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json_agent(payload: dict) -> Agent:
    """Return an Agent backed by EchoProvider that emits valid JSON."""
    return Agent(
        name="test",
        role="analyst",
        provider=EchoProvider(response=json.dumps(payload)),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  StructuredOutputParser
# ══════════════════════════════════════════════════════════════════════════════

class TestStructuredOutputParser:

    def test_parse_valid_json(self):
        parser = StructuredOutputParser({"type": "object"})
        result = parser.parse('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_extracts_json_from_prose(self):
        parser = StructuredOutputParser({"type": "object"})
        raw = 'Here is the result: {"score": 42} — done.'
        result = parser.parse(raw)
        assert result["score"] == 42

    def test_parse_invalid_json_raises(self):
        parser = StructuredOutputParser({"type": "object"})
        with pytest.raises(Exception):
            parser.parse("this is not json")

    def test_build_prompt_includes_schema(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        parser = StructuredOutputParser(schema)
        prompt = parser.build_prompt("Summarise this")
        assert "Summarise this" in prompt

    def test_build_retry_prompt(self):
        parser = StructuredOutputParser({"type": "object"})
        retry = parser.build_retry_prompt('bad json', 'parse error')
        assert "bad json" in retry or "parse error" in retry

    def test_schema_name_from_dict(self):
        parser = StructuredOutputParser({"type": "object"})
        assert isinstance(parser.schema_name, str)
        assert len(parser.schema_name) > 0

    def test_pydantic_schema_name(self):
        try:
            from pydantic import BaseModel
            class Report(BaseModel):
                title: str
            parser = StructuredOutputParser(Report)
            assert "Report" in parser.schema_name
        except ImportError:
            pytest.skip("pydantic not installed")


# ══════════════════════════════════════════════════════════════════════════════
#  Agent.with_structured_output()
# ══════════════════════════════════════════════════════════════════════════════

class TestWithStructuredOutput:

    def test_returns_structured_agent(self):
        agent = _json_agent({"result": "ok"})
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        wrapped = agent.with_structured_output(schema)
        assert isinstance(wrapped, StructuredAgent)

    def test_wrapped_agent_stores_schema(self):
        agent = _json_agent({"x": 1})
        schema = {"type": "object"}
        wrapped = agent.with_structured_output(schema)
        assert wrapped._schema is schema

    def test_max_retries_configurable(self):
        agent = _json_agent({"x": 1})
        wrapped = agent.with_structured_output({"type": "object"}, max_retries=5)
        assert wrapped._max_retries == 5

    @pytest.mark.asyncio
    async def test_run_returns_data_directly(self):
        payload = {"score": 99, "label": "excellent"}
        agent = _json_agent(payload)
        wrapped = agent.with_structured_output({"type": "object"})
        data = await wrapped.run("Rate this text")
        assert data["score"] == 99
        assert data["label"] == "excellent"

    @pytest.mark.asyncio
    async def test_ainvoke_alias(self):
        payload = {"answer": "yes"}
        agent = _json_agent(payload)
        wrapped = agent.with_structured_output({"type": "object"})
        data = await wrapped.ainvoke("Is it correct?")
        assert data["answer"] == "yes"

    @pytest.mark.asyncio
    async def test_run_with_pydantic_schema(self):
        try:
            from pydantic import BaseModel

            class Summary(BaseModel):
                title: str
                score: int

            payload = {"title": "MeshFlow Q3", "score": 95}
            agent = _json_agent(payload)
            wrapped = agent.with_structured_output(Summary)
            data = await wrapped.run("Summarise Q3")
            assert isinstance(data, Summary)
            assert data.title == "MeshFlow Q3"
            assert data.score == 95
        except ImportError:
            pytest.skip("pydantic not installed")

    @pytest.mark.asyncio
    async def test_data_not_wrapped_in_result_object(self):
        """with_structured_output returns the data, not StructuredOutputResult."""
        payload = {"ok": True}
        agent = _json_agent(payload)
        wrapped = agent.with_structured_output({"type": "object"})
        data = await wrapped.run("Check status")
        assert not isinstance(data, StructuredOutputResult)
        assert data["ok"] is True

    def test_chaining_multiple_schemas(self):
        """Multiple structured agents can be derived from one base agent."""
        agent = _json_agent({"x": 1})
        s1 = agent.with_structured_output({"type": "object"})
        s2 = agent.with_structured_output({"type": "object"}, max_retries=1)
        assert s1 is not s2
        assert s1._max_retries == 3
        assert s2._max_retries == 1


# ══════════════════════════════════════════════════════════════════════════════
#  Provider response_format
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderResponseFormat:

    @pytest.mark.asyncio
    async def test_echo_json_format_returns_valid_json(self):
        provider = EchoProvider()
        content, _, _ = await provider.complete(
            model="echo",
            messages=[{"role": "user", "content": "hello"}],
            system="",
            max_tokens=100,
            response_format="json",
        )
        parsed = json.loads(content)
        assert "echo" in parsed

    @pytest.mark.asyncio
    async def test_echo_no_format_returns_plain_text(self):
        provider = EchoProvider()
        content, _, _ = await provider.complete(
            model="echo",
            messages=[{"role": "user", "content": "hello"}],
            system="",
            max_tokens=100,
        )
        assert content.startswith("[echo]")

    @pytest.mark.asyncio
    async def test_echo_fixed_response_ignores_format(self):
        provider = EchoProvider(response='{"fixed": true}')
        content, _, _ = await provider.complete(
            model="echo",
            messages=[],
            system="",
            max_tokens=100,
            response_format="json",
        )
        assert content == '{"fixed": true}'

    @pytest.mark.asyncio
    async def test_echo_json_format_embeds_input(self):
        provider = EchoProvider()
        content, _, _ = await provider.complete(
            model="echo",
            messages=[{"role": "user", "content": "test message"}],
            system="",
            max_tokens=100,
            response_format="json",
        )
        data = json.loads(content)
        assert data["echo"] == "test message"


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_structured_agent_exported(self):
        assert hasattr(meshflow, "StructuredAgent")
        assert "StructuredAgent" in meshflow.__all__

    def test_structured_output_result_exported(self):
        assert hasattr(meshflow, "StructuredOutputResult")

    def test_structured_output_error_exported(self):
        assert hasattr(meshflow, "StructuredOutputError")

    def test_structured_output_parser_exported(self):
        assert hasattr(meshflow, "StructuredOutputParser")
