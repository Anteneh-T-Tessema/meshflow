# LLM-as-Judge

Use Claude to grade agent outputs with structured 0–1 scoring and per-criterion breakdown.

```python
from meshflow.eval.judge import LLMJudge

judge = LLMJudge()
score = await judge.score(
    task="Summarise the HIPAA privacy rule in three bullet points",
    output=agent_output,
)
print(score.score, score.reasoning)  # 0.87  "Accurate, concise, and well-formatted."
```

## `LLMJudge`

```python
judge = LLMJudge(
    model="claude-haiku-4-5",     # fast default; use claude-sonnet-4-6 for high-stakes
    rubric="Award full marks for accuracy, conciseness, and bullet format.",
    provider=None,                # auto-detected from env; uses EchoProvider in tests
)
```

### `score(task, output, *, reference="", rubric="") -> JudgeScore`

Grade a single output. Pass `reference` to compare against a gold-standard answer.

```python
# Basic scoring
score = await judge.score(task="Explain SOC 2 Type II.", output=agent_output)

# With reference answer
score = await judge.score(task, output, reference=gold_answer)

# Override rubric for this call only
score = await judge.score(task, output, rubric="Focus only on regulatory accuracy.")
```

### `score_batch(items, *, rubric="") -> list[JudgeScore]`

Grade multiple outputs concurrently. Each item is a dict with `task`, `output`, and optionally `reference` and `rubric`.

```python
scores = await judge.score_batch([
    {"task": t1, "output": o1},
    {"task": t2, "output": o2, "reference": r2},
    {"task": t3, "output": o3, "rubric": "Grade only grammar."},
])
```

### `score_suite(results, *, task_key, output_key, rubric) -> JudgeSuiteResult`

Grade an entire list of eval result dicts and return aggregate statistics.

```python
suite_result = await judge.score_suite(
    [{"task": r.input, "output": r.output} for r in eval_results]
)
print(suite_result.avg_score, suite_result.pass_rate)
```

## `JudgeScore` Fields

| Field | Type | Description |
|---|---|---|
| `score` | float | Overall score 0.0–1.0 |
| `reasoning` | str | One-sentence explanation from the judge |
| `criteria` | dict[str, float] | Per-criterion scores: `accuracy`, `completeness`, `clarity`, `relevance` |
| `task` | str | The original task (first 200 chars) |
| `output_preview` | str | Agent output preview (first 200 chars) |
| `model` | str | Judge model used |

```python
score.passed(threshold=0.7)  # bool — True if score >= threshold
score.to_dict()              # serialisable dict
```

## `JudgeSuiteResult`

Returned by `score_suite()`. Aggregates scores across all evaluated scenarios.

```python
suite = await judge.score_suite(results)

suite.avg_score    # float — mean score across all items
suite.pass_rate    # float — fraction with score >= 0.7
suite.min_score    # float — lowest individual score
suite.max_score    # float — highest individual score
suite.scores       # list[JudgeScore] — full per-item results
suite.to_dict()    # serialisable summary dict
```

## Criteria-Based Evaluation Example

The default rubric grades on four criteria. Override it to focus on domain-specific concerns:

```python
compliance_judge = LLMJudge(
    model="claude-sonnet-4-6",
    rubric=(
        "Grade this regulatory summary. "
        "Award accuracy=1.0 only if all cited regulations are correct. "
        "Award completeness=1.0 only if all required sections are covered. "
        "Ignore formatting."
    ),
)

score = await compliance_judge.score(
    task="Summarise GDPR Article 17 (right to erasure).",
    output=agent_output,
    reference=reference_text,
)

print(score.criteria["accuracy"])     # 0.9
print(score.criteria["completeness"]) # 0.75
```

## Notes

- `LLMJudge` falls back to `EchoProvider` (returns score 0.75) when `ANTHROPIC_API_KEY` is absent, so unit tests never stall.
- All `score()` calls are fully async and safe to `asyncio.gather()`.
- Scores are clamped to [0.0, 1.0] and rounded to 4 decimal places.
