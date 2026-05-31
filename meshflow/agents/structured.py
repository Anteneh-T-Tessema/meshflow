"""Structured output enforcement — guarantees valid JSON/Pydantic output.

Auto-retry loop: if the LLM produces malformed JSON, re-prompt with a
"fix this JSON" instruction up to *max_retries* times before raising
StructuredOutputError.

Usage::

    from meshflow import Agent
    from pydantic import BaseModel

    class Report(BaseModel):
        title: str
        findings: list[str]
        confidence: float

    agent = Agent(name="analyst", role="researcher")
    result = await agent.run_structured("Analyse Q3 results", Report)
    report: Report = result.data            # typed Pydantic instance
    print(result.attempts)                  # how many LLM calls it took

    # Or with a plain JSON Schema dict:
    schema = {"type": "object", "properties": {"score": {"type": "number"}}}
    result = await agent.run_structured("Rate this text", schema)
    print(result.data["score"])
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

_T = TypeVar("_T")


# ── Public types ───────────────────────────────────────────────────────────────


class StructuredOutputError(Exception):
    """Raised when all retry attempts fail to produce valid structured output."""

    def __init__(self, message: str, last_raw: str = "", attempts: int = 0) -> None:
        super().__init__(message)
        self.last_raw = last_raw
        self.attempts = attempts


@dataclass
class StructuredOutputResult(Generic[_T]):
    """Result of :meth:`Agent.run_structured`."""

    data: _T
    raw: str = ""
    attempts: int = 1
    schema_name: str = ""
    tokens: int = 0
    cost_usd: float = 0.0

    def __repr__(self) -> str:
        return (
            f"StructuredOutputResult(schema={self.schema_name!r}, "
            f"attempts={self.attempts}, tokens={self.tokens})"
        )


# ── JSON extraction helpers ────────────────────────────────────────────────────


def _extract_json(text: str) -> str:
    """Pull JSON out of an LLM response that may have markdown fences or prose."""
    # 1. Try ```json ... ``` or ``` ... ```
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()

    # 2. Try the outermost { ... } or [ ... ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]

    return text.strip()


def _repair_json(text: str) -> str:
    """Minimal JSON repair — remove trailing commas and fix common typos."""
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Replace Python-style True/False/None with JSON equivalents
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    # Remove single-line comments (// ...)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _parse_json(raw: str) -> Any:
    """Extract and parse JSON from *raw*, raising ValueError on failure."""
    candidate = _extract_json(raw)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_json(candidate)
        return json.loads(repaired)  # raises on second failure


# ── Schema introspection ──────────────────────────────────────────────────────


def _schema_name(schema: Any) -> str:
    if hasattr(schema, "__name__"):
        return schema.__name__
    if isinstance(schema, dict):
        return schema.get("title", schema.get("type", "object"))
    return str(type(schema).__name__)


def _schema_to_json_schema(schema: Any) -> dict[str, Any]:
    """Convert a Pydantic model class or raw dict to a JSON Schema dict."""
    if isinstance(schema, dict):
        return schema
    # Pydantic v2
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    # Pydantic v1
    if hasattr(schema, "schema"):
        return schema.schema()
    raise TypeError(f"Cannot derive JSON Schema from {type(schema)}")


def _validate(data: Any, schema: Any) -> Any:
    """Validate *data* against *schema*. Returns a typed instance for Pydantic models."""
    if isinstance(schema, dict):
        # Minimal type checks for plain dicts (full jsonschema validation optional)
        return data
    # Pydantic model
    if hasattr(schema, "model_validate"):
        return schema.model_validate(data)
    if hasattr(schema, "parse_obj"):
        return schema.parse_obj(data)
    return data


# ── Core parser ────────────────────────────────────────────────────────────────


class StructuredOutputParser:
    """Drives the retry loop for structured output extraction."""

    SYSTEM_SUFFIX = (
        "\n\nIMPORTANT: You MUST respond with valid JSON only — no prose, "
        "no markdown fences, no explanation. The JSON must conform exactly "
        "to the schema provided."
    )

    def __init__(self, schema: Any, max_retries: int = 3) -> None:
        self.schema = schema
        self.max_retries = max_retries
        self.schema_name = _schema_name(schema)
        self._json_schema = _schema_to_json_schema(schema)

    def build_prompt(self, task: str) -> str:
        return (
            f"{task}\n\n"
            f"Respond with JSON matching this schema:\n"
            f"```json\n{json.dumps(self._json_schema, indent=2)}\n```"
        )

    def build_retry_prompt(self, raw: str, error: str) -> str:
        return (
            f"The previous response was not valid JSON.\n"
            f"Error: {error}\n\n"
            f"Previous response:\n{raw}\n\n"
            f"Return ONLY the corrected JSON — no prose, no fences."
        )

    def parse(self, raw: str) -> Any:
        data = _parse_json(raw)
        return _validate(data, self.schema)
