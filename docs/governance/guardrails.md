# Guardrails

Every agent input and output passes through a guardrail stack before reaching the LLM or the caller — this is enforced at the MeshFlow kernel level, not bolted on after the fact.

```python
from meshflow import Agent
from meshflow.security.guardrails import PIIBlockGuardrail, ConfidenceGuardrail, LengthGuardrail

agent = Agent(
    name="hipaa_agent",
    role="researcher",
    input_guardrails=[PIIBlockGuardrail(action="block")],
    output_guardrails=[
        ConfidenceGuardrail(min_confidence=0.70),
        LengthGuardrail(max_chars=4000),
    ],
)
```

## Core Types

### `GuardrailResult`

```python
@dataclass
class GuardrailResult:
    passed: bool
    guardrail_name: str
    reason: str = ""
    modified_text: str | None = None   # set when action="modify"
    severity: Literal["block", "warn", "modify"] = "block"
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `GuardrailViolation`

Raised by `GuardrailStack` in `strict` mode when a blocking guardrail fails:

```python
class GuardrailViolation(Exception):
    result: GuardrailResult   # access via .result.guardrail_name, .result.reason
```

### `GuardrailStack`

```python
stack = GuardrailStack(
    guardrails=[PIIBlockGuardrail(), ToxicityGuardrail()],
    mode="strict",   # "strict" | "collect"
)
passed, final_text, results = stack.run("Hello, SSN is 123-45-6789")
# passed=False, results[0].guardrail_name="pii_block"
```

`stack.run()` returns `(all_passed: bool, final_text: str, results: list[GuardrailResult])`.

In `collect` mode all guardrails run and failures are returned in `results` without raising. In `strict` mode the first blocking failure raises `GuardrailViolation`.

---

## Built-in Guardrails

### `PIIBlockGuardrail`

Detects PHI/PII via `SensitiveDataDetector`. Use `action="modify"` to mask instead of block.

```python
PIIBlockGuardrail(
    action="block",          # "block" | "warn" | "modify"
    categories=["ssn", "email"],   # None = all categories
    min_confidence=0.5,
    name="pii_block",
)
```

With `action="modify"`, `GuardrailResult.modified_text` contains the masked text with values replaced by `[REDACTED]`.

### `ConfidenceGuardrail`

Blocks outputs whose `CONFIDENCE:0.XX` marker falls below a threshold.

```python
ConfidenceGuardrail(
    min_confidence=0.70,
    missing_ok=True,   # pass if no CONFIDENCE marker found
    action="block",
)
```

### `LengthGuardrail`

Enforces minimum/maximum text length.

```python
LengthGuardrail(
    min_chars=10,
    max_chars=4000,
    unit="chars",   # "chars" | "words"
    action="block",
)
```

### `ToxicityGuardrail`

Blocks profanity, violence, self-harm, and hate content. Built-in categories: `profanity`, `violence`, `self_harm`, `hate`.

```python
ToxicityGuardrail(
    categories=["violence", "self_harm"],   # None = all
    extra_patterns=[r"\bforbidden_term\b"],
    case_sensitive=False,
    action="block",
)
```

### `JSONSchemaGuardrail`

Validates that the output is parseable JSON, optionally conforming to a schema. Extracts JSON from markdown fences automatically.

```python
JSONSchemaGuardrail(
    schema={
        "required": ["diagnosis", "icd_code"],
        "properties": {"icd_code": {"type": "string"}},
    },
    extract_json=True,   # strip ```json ... ``` fences
    action="block",
)
```

Full JSON Schema draft-7 validation requires `jsonschema`; otherwise falls back to required-key presence checking.

### `RegexGuardrail`

Requires or forbids a regex pattern.

```python
RegexGuardrail(pattern=r"ICD-\d{2}", mode="require", action="block")
RegexGuardrail(pattern=r"\bpassword\b", mode="forbid", action="block")
```

`mode="require"` fails if pattern is absent. `mode="forbid"` fails if pattern is present.

### `KeywordBlockGuardrail`

Blocks any text containing forbidden keywords or phrases.

```python
KeywordBlockGuardrail(
    keywords=["internal only", "confidential", "trade secret"],
    whole_word=True,       # match whole words only
    case_sensitive=False,
    action="block",
)
```

### `CostCapGuardrail`

Rejects input tasks whose estimated token cost exceeds a budget. Apply to `input_guardrails` to catch runaway prompts before they hit the LLM.

```python
CostCapGuardrail(
    max_cost_usd=0.10,
    input_rate_per_1k=0.003,   # USD per 1k tokens (claude-sonnet default)
    chars_per_token=4,
    action="block",
)
```

### `CustomGuardrail`

Wraps any callable as a guardrail.

```python
def check_disclaimer(text: str) -> tuple[bool, str]:
    if "NOT MEDICAL ADVICE" not in text:
        return False, "missing required disclaimer"
    return True, ""

agent = Agent(
    name="medical_agent",
    output_guardrails=[CustomGuardrail(fn=check_disclaimer, name="disclaimer_check")],
)
```

Callable signatures accepted: `bool`, `tuple[bool, str]`, or `tuple[bool, str, str]` (the third element is modified text for `action="modify"`).

---

## Stack Modes

| Mode | Behavior |
|---|---|
| `strict` | Raises `GuardrailViolation` on first blocking failure |
| `collect` | Runs all guardrails; accumulates failures in `results`; never raises |

## `input_guardrails` vs `output_guardrails`

`input_guardrails` run on the task string before the LLM is called. Use them to block PII ingestion and catch cost overruns early.

`output_guardrails` run on the LLM response before it is returned to the caller. Use them to enforce confidence, schema, and content requirements.

Both accept the same list of `Guardrail` instances.
