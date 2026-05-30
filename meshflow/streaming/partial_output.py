"""Partial structured output streaming — emit validated chunks as tokens arrive.

Accumulates tokens, attempts JSON parsing at each step, and emits
:class:`PartialOutputChunk` objects as the JSON becomes more complete.

Works with any async token generator — no LLM API required.

Usage::

    from meshflow.streaming.partial_output import PartialStructuredOutput

    schema = {"type": "object", "properties": {"title": {"type": "string"},
                                                 "score": {"type": "number"}}}

    pso = PartialStructuredOutput(schema)
    async for chunk in pso.stream(token_generator):
        print(chunk.partial)        # dict with fields parsed so far
        if chunk.complete:
            result = chunk.validated  # fully validated final object

    # Convenience: collect everything and return the final validated object
    result = await pso.collect(token_generator)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class PartialOutputChunk:
    """One emission from the partial streaming parser."""

    raw_so_far: str
    partial: dict[str, Any]
    complete: bool
    validated: Any = None
    token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_so_far": self.raw_so_far[:500],
            "partial": self.partial,
            "complete": self.complete,
            "token": self.token,
        }


class PartialStructuredOutput:
    """Stream partial structured output as tokens accumulate.

    Attempts to parse JSON after each new token.  As soon as a valid JSON
    object is detected (even if the schema isn't fully satisfied yet), it
    emits a ``PartialOutputChunk`` with the fields parsed so far.

    When the full valid JSON arrives, the final chunk has ``complete=True``
    and a ``validated`` field with the Pydantic instance or dict.

    Parameters
    ----------
    schema:
        A Pydantic ``BaseModel`` class **or** a plain JSON-schema dict.
    emit_on_every_token:
        If True, emit a chunk on every token regardless of parse state.
        If False (default), only emit when the partial JSON changes.
    """

    def __init__(
        self,
        schema: Any = None,
        emit_on_every_token: bool = False,
    ) -> None:
        self._schema = schema
        self._emit_every = emit_on_every_token

    async def stream(
        self,
        token_gen: AsyncIterator[str],
    ) -> AsyncIterator[PartialOutputChunk]:
        """Consume tokens from *token_gen* and yield :class:`PartialOutputChunk`s."""
        accumulated = ""
        last_partial: dict[str, Any] = {}
        completed = False

        async for token in token_gen:
            accumulated += token
            partial, complete, validated = self._parse(accumulated)

            changed = partial != last_partial
            if changed or self._emit_every:
                last_partial = dict(partial)
                yield PartialOutputChunk(
                    raw_so_far=accumulated,
                    partial=partial,
                    complete=complete,
                    validated=validated,
                    token=token,
                )

            if complete and not completed:
                completed = True
                break  # full object parsed — stop consuming tokens

        # If we never got a complete object, emit one final chunk
        if not completed:
            partial, complete, validated = self._parse(accumulated)
            yield PartialOutputChunk(
                raw_so_far=accumulated,
                partial=partial,
                complete=complete,
                validated=validated,
                token="",
            )

    async def collect(self, token_gen: AsyncIterator[str]) -> Any:
        """Consume *token_gen* and return the final validated object (or raw dict)."""
        last: PartialOutputChunk | None = None
        async for chunk in self.stream(token_gen):
            last = chunk
            if chunk.complete:
                return chunk.validated if chunk.validated is not None else chunk.partial
        if last:
            return last.validated if last.validated is not None else last.partial
        return None

    def _parse(self, text: str) -> tuple[dict[str, Any], bool, Any]:
        """Try to extract and validate JSON from accumulated *text*.

        Returns (partial_dict, is_complete, validated_obj).
        """
        # Try to find the outermost JSON object
        json_str = self._extract_json(text)
        if not json_str:
            # No JSON yet — try to extract partial key-value pairs
            partial = self._extract_partial_pairs(text)
            return partial, False, None

        # Full JSON found
        try:
            data = json.loads(json_str)
            validated = self._validate(data)
            return data if isinstance(data, dict) else {}, True, validated
        except (json.JSONDecodeError, ValueError):
            partial = self._extract_partial_pairs(text)
            return partial, False, None

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """Extract the outermost complete JSON object from *text*."""
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start: i + 1]
        return None

    @staticmethod
    def _extract_partial_pairs(text: str) -> dict[str, Any]:
        """Extract completed key-value pairs from partial JSON."""
        result: dict[str, Any] = {}
        # Match "key": value patterns (value = string, number, bool, null)
        patterns = [
            r'"(\w+)"\s*:\s*"([^"]*)"',          # string values
            r'"(\w+)"\s*:\s*(-?\d+(?:\.\d+)?)',   # numbers
            r'"(\w+)"\s*:\s*(true|false|null)',    # literals
        ]
        for pat in patterns:
            for m in re.finditer(pat, text):
                key = m.group(1)
                val_str = m.group(2)
                try:
                    val = json.loads(val_str)
                except (json.JSONDecodeError, ValueError):
                    val = val_str
                result[key] = val
        return result

    def _validate(self, data: dict[str, Any]) -> Any:
        """Validate *data* against the schema. Returns Pydantic model or dict."""
        if self._schema is None:
            return data
        try:
            # Pydantic model
            if hasattr(self._schema, "model_validate"):
                return self._schema.model_validate(data)
            if hasattr(self._schema, "parse_obj"):
                return self._schema.parse_obj(data)
        except Exception:
            pass
        return data


# ── Convenience: wrap a string generator ──────────────────────────────────────

async def stream_structured(
    token_gen: AsyncIterator[str],
    schema: Any = None,
    *,
    emit_on_every_token: bool = False,
) -> AsyncIterator[PartialOutputChunk]:
    """Convenience function: wrap *token_gen* with :class:`PartialStructuredOutput`.

    Usage::

        async for chunk in stream_structured(provider.stream_complete(...), MySchema):
            if chunk.complete:
                result = chunk.validated
    """
    pso = PartialStructuredOutput(schema=schema, emit_on_every_token=emit_on_every_token)
    async for chunk in pso.stream(token_gen):
        yield chunk
