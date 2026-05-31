# A/B Testing

Compare two agent configurations on the same scenario set and identify the winner by LLM-judged scores.

```python
from meshflow.eval.ab_test import ABTest, ABVariant

control = ABVariant("gpt-4o",     provider=prod_provider)
variant = ABVariant("claude-haiku", provider=new_provider)

ab = ABTest(control=control, variant=variant)
result = await ab.run(["What is HIPAA?", "Explain SOC 2 Type II."])

print(result.winner)   # "claude-haiku" | "gpt-4o" | "tie"
print(result.summary())
```

## `ABVariant`

One side of an A/B test. Accepts either a MeshFlow `Agent` or a raw provider.

| Field | Type | Description |
|---|---|---|
| `name` | str | Display name for reports |
| `agent` | Any | MeshFlow `Agent` instance (optional) |
| `provider` | Any | Raw LLM provider (optional; used when `agent` is None) |
| `model` | str | Model name forwarded to the provider (default `claude-haiku-4-5`) |
| `system_prompt` | str | System prompt injected for provider-direct calls |

```python
control = ABVariant(
    name="v1-sonnet",
    agent=production_agent,
)

variant = ABVariant(
    name="v2-haiku",
    provider=anthropic_provider,
    model="claude-haiku-4-5",
    system_prompt="You are a concise compliance assistant.",
)
```

## `ABTest`

```python
ab = ABTest(
    control=control,
    variant=variant,
    judge=None,    # auto-creates LLMJudge; uses EchoProvider when no API key
    rubric="Grade for accuracy and conciseness.",
)
```

### `run(scenarios, *, rubric="") -> ABTestResult`

Run every scenario through both variants concurrently, grade both outputs with `LLMJudge`, and return aggregate results.

```python
scenarios = [
    "What is the HIPAA minimum necessary standard?",
    "Explain SOC 2 Type II in plain English.",
    "List the five GDPR data subject rights.",
]

result = await ab.run(scenarios, rubric="Grade for regulatory accuracy.")
```

Both variants receive the same scenario text. The control and variant outputs are graded concurrently, so the total wall time scales with the number of scenarios rather than doubling.

## `ABTestResult`

```python
result.control_name      # "gpt-4o"
result.variant_name      # "claude-haiku"
result.control_avg       # float — mean judge score for control (0–1)
result.variant_avg       # float — mean judge score for variant
result.delta             # float — variant_avg − control_avg
result.winner            # "control" | "variant" | "tie" (|delta| < 0.02 = tie)
result.effect_size       # float — Cohen's d approximation over per-scenario deltas
result.control_win_rate  # float — fraction of scenarios where control scored higher
result.variant_win_rate  # float — fraction of scenarios where variant scored higher
result.turn_results      # list[ABTurnResult]
result.total_duration_ms # float
result.summary()         # one-line human-readable summary
result.to_dict()         # serialisable dict
```

### `winner` Logic

| Condition | `winner` |
|---|---|
| `abs(delta) < 0.02` | `"tie"` |
| `delta > 0` | `variant_name` |
| `delta < 0` | `control_name` |

### `effect_size`

Cohen's d computed over per-scenario deltas. Values above 0.5 indicate a practically meaningful difference.

## `ABTurnResult`

Per-scenario scores stored in `ABTestResult.turn_results`.

| Field | Type | Description |
|---|---|---|
| `scenario` | str | The scenario text (first 200 chars) |
| `control_output` | str | Control variant's response |
| `variant_output` | str | Variant's response |
| `control_score` | float | Control judge score |
| `variant_score` | float | Variant judge score |
| `control_reasoning` | str | Judge's reasoning for control |
| `variant_reasoning` | str | Judge's reasoning for variant |
| `delta` | float | `variant_score − control_score` |

## Full Example

```python
import asyncio
from meshflow.eval.ab_test import ABTest, ABVariant
from meshflow.agents.providers import AnthropicProvider

async def main():
    control = ABVariant(
        name="sonnet-prod",
        provider=AnthropicProvider(),
        model="claude-sonnet-4-6",
        system_prompt="You are a compliance analyst. Be thorough.",
    )
    variant = ABVariant(
        name="haiku-fast",
        provider=AnthropicProvider(),
        model="claude-haiku-4-5",
        system_prompt="You are a compliance analyst. Be concise.",
    )

    ab = ABTest(control=control, variant=variant)
    result = await ab.run(
        scenarios=[
            "Explain HIPAA's minimum necessary standard.",
            "What are GDPR's data subject rights?",
            "Summarise SOC 2 Type II requirements.",
        ],
        rubric="Grade for regulatory accuracy and clarity.",
    )

    print(result.summary())
    # [HAIKU-FAST WINS] sonnet-prod=0.841 vs haiku-fast=0.879
    # (Δ=+0.038, effect=1.12) over 3 scenarios

    for t in result.turn_results:
        print(f"  Δ={t.delta:+.3f}  {t.scenario[:60]}")

asyncio.run(main())
```

## Statistical Interpretation

| `effect_size` | Interpretation |
|---|---|
| < 0.2 | Negligible difference |
| 0.2–0.5 | Small effect |
| 0.5–0.8 | Medium effect |
| > 0.8 | Large effect; consider shipping the winner |
