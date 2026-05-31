"""Sprint 25 — Guardrails tests.

All tests are deterministic (no live API calls, MESHFLOW_MOCK=1).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.security.guardrails import (
    ConfidenceGuardrail,
    CostCapGuardrail,
    CustomGuardrail,
    Guardrail,
    GuardrailResult,
    GuardrailStack,
    GuardrailViolation,
    JSONSchemaGuardrail,
    KeywordBlockGuardrail,
    LengthGuardrail,
    PIIBlockGuardrail,
    RegexGuardrail,
    ToxicityGuardrail,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. GuardrailResult
# ═══════════════════════════════════════════════════════════════════════════════

class TestGuardrailResult:
    def test_bool_passed(self):
        r = GuardrailResult(passed=True, guardrail_name="test")
        assert bool(r) is True

    def test_bool_failed(self):
        r = GuardrailResult(passed=False, guardrail_name="test", reason="x")
        assert bool(r) is False

    def test_defaults(self):
        r = GuardrailResult(passed=True, guardrail_name="g")
        assert r.reason == ""
        assert r.modified_text is None
        assert r.severity == "block"
        assert r.metadata == {}

    def test_metadata_stored(self):
        r = GuardrailResult(passed=False, guardrail_name="g", metadata={"k": 1})
        assert r.metadata["k"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GuardrailViolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestGuardrailViolation:
    def test_is_exception(self):
        assert issubclass(GuardrailViolation, Exception)

    def test_carries_result(self):
        r = GuardrailResult(passed=False, guardrail_name="pii", reason="ssn found")
        exc = GuardrailViolation(r)
        assert exc.result is r

    def test_message_contains_reason(self):
        r = GuardrailResult(passed=False, guardrail_name="pii", reason="ssn found")
        exc = GuardrailViolation(r)
        assert "ssn found" in str(exc)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CustomGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestCustomGuardrail:
    def test_bool_return_pass(self):
        g = CustomGuardrail(fn=lambda t: True, name="ok")
        r = g.check("anything")
        assert r.passed

    def test_bool_return_fail(self):
        g = CustomGuardrail(fn=lambda t: False, name="nope")
        r = g.check("anything")
        assert not r.passed

    def test_tuple_return(self):
        g = CustomGuardrail(fn=lambda t: (False, "bad content"), name="c")
        r = g.check("x")
        assert not r.passed
        assert r.reason == "bad content"

    def test_name_default(self):
        g = CustomGuardrail(fn=lambda t: True)
        assert g.name == "custom"

    def test_action_stored(self):
        g = CustomGuardrail(fn=lambda t: True, action="warn")
        assert g.action == "warn"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LengthGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestLengthGuardrail:
    def test_passes_within_range(self):
        g = LengthGuardrail(min_chars=5, max_chars=100)
        assert g.check("hello world").passed

    def test_fails_too_short(self):
        g = LengthGuardrail(min_chars=50)
        r = g.check("short")
        assert not r.passed
        assert "too short" in r.reason

    def test_fails_too_long(self):
        g = LengthGuardrail(max_chars=5)
        r = g.check("this is definitely too long")
        assert not r.passed
        assert "too long" in r.reason

    def test_no_min_passes_empty(self):
        g = LengthGuardrail(max_chars=100)
        assert g.check("").passed

    def test_word_unit(self):
        g = LengthGuardrail(min_chars=3, unit="words")
        assert g.check("one two three").passed
        assert not g.check("one two").passed

    def test_metadata_has_size(self):
        g = LengthGuardrail(max_chars=100)
        r = g.check("hello")
        assert r.metadata["size"] == 5

    def test_no_max_no_fail_on_long(self):
        g = LengthGuardrail(min_chars=1)
        assert g.check("x" * 10000).passed


# ═══════════════════════════════════════════════════════════════════════════════
# 5. RegexGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegexGuardrail:
    def test_require_found(self):
        g = RegexGuardrail(r"\d{4}", mode="require")
        assert g.check("code is 1234").passed

    def test_require_not_found(self):
        g = RegexGuardrail(r"\d{4}", mode="require")
        r = g.check("no digits here")
        assert not r.passed
        assert "not found" in r.reason

    def test_forbid_not_found(self):
        g = RegexGuardrail(r"DROP TABLE", mode="forbid")
        assert g.check("SELECT * FROM users").passed

    def test_forbid_found(self):
        g = RegexGuardrail(r"DROP TABLE", mode="forbid")
        r = g.check("DROP TABLE users;")
        assert not r.passed
        assert "forbidden" in r.reason

    def test_case_insensitive_default(self):
        g = RegexGuardrail(r"drop table", mode="forbid")
        assert not g.check("DROP TABLE users").passed

    def test_case_sensitive_no_match(self):
        g = RegexGuardrail(r"drop table", mode="forbid", flags=0)
        assert g.check("DROP TABLE users").passed  # case-sensitive: no match


# ═══════════════════════════════════════════════════════════════════════════════
# 6. KeywordBlockGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeywordBlockGuardrail:
    def test_no_match_passes(self):
        g = KeywordBlockGuardrail(["secret", "confidential"])
        assert g.check("This is public information").passed

    def test_match_fails(self):
        g = KeywordBlockGuardrail(["secret"])
        r = g.check("This is secret information")
        assert not r.passed
        assert "secret" in r.metadata["keywords"]

    def test_multiple_keywords(self):
        g = KeywordBlockGuardrail(["alpha", "beta", "gamma"])
        r = g.check("beta version released")
        assert not r.passed

    def test_case_insensitive_default(self):
        g = KeywordBlockGuardrail(["SECRET"])
        assert not g.check("this is secret").passed

    def test_whole_word_no_partial_match(self):
        g = KeywordBlockGuardrail(["ass"], whole_word=True)
        assert g.check("assessment tool").passed  # "ass" not as whole word

    def test_phrase_match(self):
        g = KeywordBlockGuardrail(["credit card number"], whole_word=False)
        r = g.check("Please provide your credit card number")
        assert not r.passed

    def test_metadata_lists_keywords(self):
        g = KeywordBlockGuardrail(["secret", "private"])
        r = g.check("secret and private data")
        assert "secret" in r.metadata["keywords"]
        assert "private" in r.metadata["keywords"]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ConfidenceGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfidenceGuardrail:
    def test_passes_above_threshold(self):
        g = ConfidenceGuardrail(min_confidence=0.7)
        assert g.check("Good answer.\nCONFIDENCE:0.85").passed

    def test_fails_below_threshold(self):
        g = ConfidenceGuardrail(min_confidence=0.7)
        r = g.check("Not sure.\nCONFIDENCE:0.50")
        assert not r.passed
        assert "0.50" in r.reason

    def test_no_marker_missing_ok_true(self):
        g = ConfidenceGuardrail(min_confidence=0.7, missing_ok=True)
        assert g.check("Answer with no confidence marker").passed

    def test_no_marker_missing_ok_false(self):
        g = ConfidenceGuardrail(min_confidence=0.7, missing_ok=False)
        r = g.check("Answer with no confidence marker")
        assert not r.passed

    def test_exactly_at_threshold_passes(self):
        g = ConfidenceGuardrail(min_confidence=0.7)
        assert g.check("CONFIDENCE:0.70").passed

    def test_metadata_has_confidence(self):
        g = ConfidenceGuardrail(min_confidence=0.5)
        r = g.check("CONFIDENCE:0.80")
        assert pytest.approx(r.metadata["confidence"], abs=0.01) == 0.80

    def test_case_insensitive_marker(self):
        g = ConfidenceGuardrail(min_confidence=0.5)
        assert g.check("confidence:0.75").passed


# ═══════════════════════════════════════════════════════════════════════════════
# 8. ToxicityGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestToxicityGuardrail:
    def test_clean_text_passes(self):
        g = ToxicityGuardrail()
        assert g.check("The weather today is sunny and warm.").passed

    def test_violence_detected(self):
        g = ToxicityGuardrail(categories=["violence"])
        r = g.check("I want to kill the process")
        assert not r.passed
        assert "violence" in r.metadata["categories"]

    def test_self_harm_detected(self):
        g = ToxicityGuardrail(categories=["self_harm"])
        r = g.check("How to commit suicide")
        assert not r.passed

    def test_extra_patterns(self):
        g = ToxicityGuardrail(extra_patterns=[r"banana"])
        r = g.check("I love banana smoothies")
        assert not r.passed
        assert "custom" in r.metadata["categories"]

    def test_category_filter(self):
        g = ToxicityGuardrail(categories=["hate"])
        # violence not checked when only "hate" category selected
        r = g.check("I want to kill the process")
        assert r.passed

    def test_metadata_has_categories(self):
        g = ToxicityGuardrail()
        r = g.check("I want to kill and destroy everything")
        assert not r.passed
        assert isinstance(r.metadata["categories"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. JSONSchemaGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestJSONSchemaGuardrail:
    def test_valid_json_no_schema(self):
        g = JSONSchemaGuardrail(schema=None)
        assert g.check('{"key": "value"}').passed

    def test_invalid_json_fails(self):
        g = JSONSchemaGuardrail()
        r = g.check("not json at all")
        assert not r.passed
        assert "invalid JSON" in r.reason

    def test_schema_required_keys_pass(self):
        g = JSONSchemaGuardrail(schema={"required": ["score", "issues"]})
        assert g.check('{"score": 8, "issues": []}').passed

    def test_schema_missing_key_fails(self):
        g = JSONSchemaGuardrail(schema={"required": ["score", "issues"]})
        r = g.check('{"score": 8}')
        assert not r.passed
        assert "issues" in r.reason

    def test_extracts_json_from_markdown(self):
        g = JSONSchemaGuardrail()
        text = "Here is the result:\n```json\n{\"a\": 1}\n```"
        assert g.check(text).passed

    def test_no_extraction_mode(self):
        g = JSONSchemaGuardrail(extract_json=False)
        text = "```json\n{\"a\": 1}\n```"
        r = g.check(text)
        assert not r.passed  # backticks make it invalid JSON

    def test_empty_required_passes(self):
        g = JSONSchemaGuardrail(schema={"required": []})
        assert g.check("{}").passed


# ═══════════════════════════════════════════════════════════════════════════════
# 10. PIIBlockGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestPIIBlockGuardrail:
    def test_clean_text_passes(self):
        g = PIIBlockGuardrail()
        assert g.check("The capital of France is Paris.").passed

    def test_ssn_detected_and_blocked(self):
        g = PIIBlockGuardrail(action="block")
        r = g.check("My SSN is 123-45-6789")
        assert not r.passed
        assert "SSN" in r.reason or "ssn" in r.reason.lower()

    def test_email_detected_and_blocked(self):
        g = PIIBlockGuardrail(action="block")
        r = g.check("Email me at user@example.com for details")
        assert not r.passed

    def test_modify_action_masks_pii(self):
        g = PIIBlockGuardrail(action="modify")
        r = g.check("SSN: 123-45-6789 is sensitive")
        assert r.passed
        assert r.modified_text is not None
        assert "123-45-6789" not in r.modified_text
        assert "REDACTED" in r.modified_text

    def test_warn_action_check_still_fails(self):
        # check() returns passed=False when PII found; action="warn" only
        # affects the GuardrailStack (which keeps all_passed=True for warns)
        g = PIIBlockGuardrail(action="warn")
        r = g.check("SSN: 123-45-6789")
        assert not r.passed   # condition failed (PII detected)
        assert g.action == "warn"

    def test_warn_action_stack_does_not_block(self):
        stack = GuardrailStack([PIIBlockGuardrail(action="warn")])
        passed, _, results = stack.run("SSN: 123-45-6789")
        assert passed          # stack-level: warn doesn't block
        assert not results[0].passed  # but individual result is still failed

    def test_metadata_has_count(self):
        g = PIIBlockGuardrail(action="block")
        r = g.check("SSN: 123-45-6789. Email: x@y.com")
        assert "match_count" in r.metadata
        assert r.metadata["match_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 11. CostCapGuardrail
# ═══════════════════════════════════════════════════════════════════════════════

class TestCostCapGuardrail:
    def test_short_text_passes(self):
        g = CostCapGuardrail(max_cost_usd=1.0)
        assert g.check("Hello").passed

    def test_long_text_fails(self):
        g = CostCapGuardrail(max_cost_usd=0.000001)  # tiny cap
        r = g.check("word " * 1000)
        assert not r.passed
        assert "exceeds cap" in r.reason

    def test_metadata_has_estimate(self):
        g = CostCapGuardrail(max_cost_usd=1.0)
        r = g.check("hello world")
        assert "estimated_usd" in r.metadata

    def test_custom_rate(self):
        g = CostCapGuardrail(max_cost_usd=0.01, input_rate_per_1k=0.001)
        # 10 chars / 4 chars_per_token = 2.5 tokens; 2.5 * 0.000001 = tiny
        assert g.check("hi").passed


# ═══════════════════════════════════════════════════════════════════════════════
# 12. GuardrailStack
# ═══════════════════════════════════════════════════════════════════════════════

class TestGuardrailStack:
    def test_empty_stack_passes(self):
        stack = GuardrailStack([])
        passed, text, results = stack.run("hello")
        assert passed
        assert text == "hello"
        assert results == []

    def test_all_pass(self):
        stack = GuardrailStack([
            LengthGuardrail(min_chars=1),
            RegexGuardrail(r"\w+", mode="require"),
        ])
        passed, text, results = stack.run("hello world")
        assert passed
        assert len(results) == 2

    def test_first_block_raises_in_strict(self):
        stack = GuardrailStack([
            LengthGuardrail(max_chars=3),  # fails
            RegexGuardrail(r"\w+", mode="require"),
        ], mode="strict")
        with pytest.raises(GuardrailViolation) as exc_info:
            stack.run("hello world")
        assert "length" in exc_info.value.result.guardrail_name

    def test_collect_mode_runs_all(self):
        stack = GuardrailStack([
            LengthGuardrail(max_chars=3),  # fails
            LengthGuardrail(max_chars=2),  # also fails
        ], mode="collect")
        passed, _, results = stack.run("hello")
        assert not passed
        assert len(results) == 2

    def test_modify_action_rewrites_text(self):
        def masker(text):
            return True, text.replace("secret", "[REDACTED]")

        stack = GuardrailStack([
            CustomGuardrail(fn=masker, name="mask", action="modify"),
        ])
        passed, text, _ = stack.run("this is secret data")
        assert passed
        assert text == "this is [REDACTED] data"

    def test_warn_action_does_not_block(self):
        stack = GuardrailStack([
            CustomGuardrail(fn=lambda t: (False, "advisory only"), action="warn"),
        ], mode="strict")
        passed, text, results = stack.run("hello")
        assert passed  # warn doesn't block
        assert not results[0].passed

    def test_add_method(self):
        stack = GuardrailStack()
        stack.add(LengthGuardrail(min_chars=1))
        assert len(stack) == 1

    def test_len(self):
        stack = GuardrailStack([LengthGuardrail(), LengthGuardrail()])
        assert len(stack) == 2

    def test_modify_then_block_uses_modified_text(self):
        """Modify guardrail changes text; subsequent guardrail sees the modified version."""
        def add_flag(text):
            return True, text + " [FLAG]"

        stack = GuardrailStack([
            CustomGuardrail(fn=add_flag, name="add_flag", action="modify"),
            RegexGuardrail(r"\[FLAG\]", mode="require"),  # passes because modified text has [FLAG]
        ])
        passed, text, _ = stack.run("original")
        assert passed
        assert "[FLAG]" in text


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Agent integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentGuardrailIntegration:
    @pytest.mark.asyncio
    async def test_input_block_returns_blocked_dict(self):
        from meshflow import Agent

        agent = Agent(
            name="guarded",
            role="executor",
            input_guardrails=[KeywordBlockGuardrail(["FORBIDDEN_WORD"])],
        )
        result = await agent.run("This contains FORBIDDEN_WORD in the task")
        assert result.get("blocked") is True
        assert "FORBIDDEN_WORD" in result["result"] or "keyword_block" in result.get("guardrail", "")

    @pytest.mark.asyncio
    async def test_clean_input_passes_through(self):
        from meshflow import Agent

        agent = Agent(
            name="guarded",
            role="executor",
            input_guardrails=[KeywordBlockGuardrail(["FORBIDDEN_WORD"])],
        )
        result = await agent.run("This is a clean task with no forbidden words")
        assert result.get("blocked") is False or "blocked" not in result or result.get("blocked") is False

    @pytest.mark.asyncio
    async def test_output_block_when_output_fails_guardrail(self):
        from meshflow import Agent

        agent = Agent(
            name="guarded",
            role="executor",
            output_guardrails=[LengthGuardrail(min_chars=100000)],  # impossible to satisfy
        )
        result = await agent.run("Write a short summary")
        assert result.get("blocked") is True

    @pytest.mark.asyncio
    async def test_no_guardrails_runs_normally(self):
        from meshflow import Agent

        agent = Agent(name="plain", role="executor")
        result = await agent.run("Hello")
        assert "result" in result
        assert result.get("blocked") is False

    @pytest.mark.asyncio
    async def test_guardrail_results_in_output(self):
        from meshflow import Agent

        agent = Agent(
            name="g",
            role="executor",
            output_guardrails=[LengthGuardrail(min_chars=1)],  # always passes
        )
        result = await agent.run("Hello")
        assert isinstance(result.get("guardrail_results"), list)

    def test_agent_stores_guardrails(self):
        from meshflow import Agent

        g1 = LengthGuardrail(min_chars=1)
        g2 = ConfidenceGuardrail()
        agent = Agent(
            name="a",
            role="executor",
            input_guardrails=[g1],
            output_guardrails=[g2],
        )
        assert len(agent.input_guardrails) == 1
        assert len(agent.output_guardrails) == 1

    def test_built_agent_has_stacks(self):
        from meshflow import Agent

        agent = Agent(
            name="a",
            role="executor",
            input_guardrails=[LengthGuardrail()],
            output_guardrails=[ConfidenceGuardrail()],
        )
        built = agent._build()
        assert len(built._input_stack.guardrails) == 1
        assert len(built._output_stack.guardrails) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Public API surface
# ═══════════════════════════════════════════════════════════════════════════════

class TestGuardrailPublicAPI:
    def test_all_importable_from_root(self):
        import meshflow
        for name in [
            "Guardrail", "GuardrailResult", "GuardrailStack", "GuardrailViolation",
            "PIIBlockGuardrail", "ConfidenceGuardrail", "LengthGuardrail",
            "ToxicityGuardrail", "JSONSchemaGuardrail", "RegexGuardrail",
            "KeywordBlockGuardrail", "CostCapGuardrail", "CustomGuardrail",
        ]:
            assert hasattr(meshflow, name), f"meshflow.{name} not exported"

    def test_guardrail_is_abstract(self):
        from abc import ABC
        assert issubclass(Guardrail, ABC)

    def test_version_bumped(self):
        import meshflow
        major, minor, _ = meshflow.__version__.split(".")
        assert int(major) >= 1 or int(minor) >= 25  # Sprint 25+

    def test_all_guardrails_have_repr(self):
        guards = [
            LengthGuardrail(), RegexGuardrail(r"\w+"),
            KeywordBlockGuardrail(["x"]), ConfidenceGuardrail(),
            ToxicityGuardrail(), JSONSchemaGuardrail(),
            CostCapGuardrail(), PIIBlockGuardrail(),
            CustomGuardrail(fn=lambda t: True),
        ]
        for g in guards:
            r = repr(g)
            assert "Guardrail" in r or "action" in r.lower()
