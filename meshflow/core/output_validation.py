"""Node-level structured output validation — enforce schemas on workflow step outputs.

Closes the "output_schema declared but never validated" gap.

Every workflow node can declare an ``output_schema`` (Pydantic model or JSON Schema
dict) in YAML or programmatically.  After a node runs, the runtime validates its
output against the schema and retries up to ``max_retries`` times with error
feedback if the output is invalid.

YAML example::

    nodes:
      extractor:
        kind: native
        role: executor
        output_schema:
          type: object
          properties:
            name:  {type: string}
            score: {type: number}
          required: [name, score]
        retry_on_fail: true
        max_retries: 2

Programmatic example::

    from meshflow.core.output_validation import OutputValidator, ValidationResult

    validator = OutputValidator(schema=MyPydanticModel)
    result = validator.validate('{"name": "Alice", "score": 9.5}')
    if not result.valid:
        print(result.error)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ValidationResult:
    valid: bool
    data: Any = None         # parsed/validated data when valid
    error: str = ""          # human-readable error when invalid
    schema_name: str = ""

    def __bool__(self) -> bool:
        return self.valid


class OutputValidator:
    """Validates a node output string against a JSON Schema or Pydantic model.

    Parameters
    ----------
    schema:
        A JSON Schema dict OR a Pydantic ``BaseModel`` class.
        Pass ``None`` to disable validation (always returns ``valid=True``).
    coerce:
        When True (default), attempts to extract a JSON object from the
        output string if it isn't bare JSON (handles markdown code fences,
        text-before-JSON patterns common in LLM outputs).
    """

    def __init__(self, schema: Any, coerce: bool = True) -> None:
        self._schema = schema
        self._coerce = coerce
        self._is_pydantic = _is_pydantic_model(schema)
        self._schema_name = (
            getattr(schema, "__name__", None)
            or (schema.get("title") if isinstance(schema, dict) else None)
            or "schema"
        )

    @property
    def schema_name(self) -> str:
        return self._schema_name

    def validate(self, text: str) -> ValidationResult:
        """Validate *text* (the node's output string) against the schema."""
        if self._schema is None:
            return ValidationResult(valid=True, data=text)

        # Extract JSON from the output
        raw_json = self._extract_json(text) if self._coerce else text
        if raw_json is None:
            return ValidationResult(
                valid=False,
                error=f"Output does not contain valid JSON for schema {self._schema_name!r}.",
                schema_name=self._schema_name,
            )

        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            return ValidationResult(
                valid=False,
                error=f"JSON parse error: {exc}",
                schema_name=self._schema_name,
            )

        # Pydantic validation
        if self._is_pydantic:
            try:
                validated = self._schema.model_validate(parsed)  # type: ignore[attr-defined]
                return ValidationResult(valid=True, data=validated, schema_name=self._schema_name)
            except Exception as exc:
                return ValidationResult(
                    valid=False,
                    error=f"Pydantic validation error: {exc}",
                    schema_name=self._schema_name,
                )

        # JSON Schema validation (stdlib only — no jsonschema required)
        if isinstance(self._schema, dict):
            error = _jsonschema_lite(parsed, self._schema)
            if error:
                return ValidationResult(
                    valid=False, error=error, schema_name=self._schema_name
                )
            return ValidationResult(valid=True, data=parsed, schema_name=self._schema_name)

        return ValidationResult(valid=True, data=parsed, schema_name=self._schema_name)

    def retry_prompt(self, original_output: str, error: str) -> str:
        """Build a retry prompt for the LLM explaining the validation failure."""
        schema_str = (
            json.dumps(self._schema.model_json_schema(), indent=2)  # type: ignore[attr-defined]
            if self._is_pydantic
            else json.dumps(self._schema, indent=2) if isinstance(self._schema, dict)
            else str(self._schema)
        )
        return (
            f"Your previous output failed validation against the required schema.\n\n"
            f"Validation error: {error}\n\n"
            f"Required schema:\n{schema_str}\n\n"
            f"Original output:\n{original_output[:500]}\n\n"
            "Output ONLY valid JSON matching the schema — no prose, no markdown fences."
        )

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """Extract the first JSON object or array from *text*."""
        # Strip markdown fences first
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        text = re.sub(r"```\s*$", "", text).strip()

        # Try to parse as-is
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # Find first { or [ and extract
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = text.find(start_char)
            if start == -1:
                continue
            # Find matching close via depth count
            depth = 0
            in_string = False
            escape_next = False
            for i, ch in enumerate(text[start:], start):
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break
        return None


# ── Lightweight JSON Schema validator (subset) ────────────────────────────────

def _jsonschema_lite(data: Any, schema: dict[str, Any], path: str = "") -> str:
    """Validate *data* against a subset of JSON Schema. Returns error string or empty."""
    schema_type = schema.get("type")

    if schema_type == "object":
        if not isinstance(data, dict):
            return f"{path or 'root'}: expected object, got {type(data).__name__}"
        required = schema.get("required", [])
        for req in required:
            if req not in data:
                return f"{path or 'root'}: missing required field '{req}'"
        props = schema.get("properties", {})
        for key, sub_schema in props.items():
            if key in data:
                err = _jsonschema_lite(data[key], sub_schema, f"{path}.{key}" if path else key)
                if err:
                    return err

    elif schema_type == "array":
        if not isinstance(data, list):
            return f"{path or 'root'}: expected array, got {type(data).__name__}"
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(data):
                err = _jsonschema_lite(item, items_schema, f"{path}[{i}]")
                if err:
                    return err

    elif schema_type == "string":
        if not isinstance(data, str):
            return f"{path or 'root'}: expected string, got {type(data).__name__}"

    elif schema_type == "number":
        if not isinstance(data, (int, float)):
            return f"{path or 'root'}: expected number, got {type(data).__name__}"

    elif schema_type == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            return f"{path or 'root'}: expected integer, got {type(data).__name__}"

    elif schema_type == "boolean":
        if not isinstance(data, bool):
            return f"{path or 'root'}: expected boolean, got {type(data).__name__}"

    # Enum check
    enum = schema.get("enum")
    if enum is not None and data not in enum:
        return f"{path or 'root'}: value {data!r} not in enum {enum}"

    return ""


def _is_pydantic_model(schema: Any) -> bool:
    try:
        return hasattr(schema, "model_validate") and hasattr(schema, "model_json_schema")
    except Exception:
        return False


def validator_from_yaml(node_cfg: dict[str, Any]) -> "OutputValidator | None":
    """Build an OutputValidator from a YAML node config dict, or return None."""
    raw_schema = node_cfg.get("output_schema")
    if not raw_schema:
        return None
    if isinstance(raw_schema, dict):
        return OutputValidator(schema=raw_schema)
    # String reference — try to import the Pydantic model
    if isinstance(raw_schema, str):
        try:
            parts = raw_schema.rsplit(".", 1)
            if len(parts) == 2:
                import importlib
                mod = importlib.import_module(parts[0])
                cls = getattr(mod, parts[1])
                return OutputValidator(schema=cls)
        except Exception:
            pass
    return None


__all__ = ["OutputValidator", "ValidationResult", "validator_from_yaml"]
