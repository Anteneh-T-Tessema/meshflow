# meshflow — Eval API Reference

Evaluation harness, baselines, regression gating, and production feedback.

## EvalSuite

```python
from meshflow import EvalSuite, EvalScenario, EvalResult, ScenarioResult, run_eval

# From YAML
suite = EvalSuite.from_yaml("evals.yaml")
result: EvalResult = await suite.run(agent)

# Shorthand
result = await run_eval(agent, "evals.yaml")

# EvalResult fields
result.suite_name
result.passed          # int
result.failed          # int
result.pass_rate       # float 0–1
result.scenarios       # list[ScenarioResult]
result.total_cost_usd
result.total_tokens
```

YAML format:
```yaml
suite: my-agent-eval
scenarios:
  - name: math
    input: "What is 2 + 2?"
    expected: "4"
    judge: exact_match

  - name: summarization
    input: "Summarize: The sky is blue."
    judge: llm
    criteria: "Response is a concise accurate summary"

  - name: contains-check
    input: "List 3 European capitals"
    expected: "Paris"
    judge: contains
```

## Baselines + Regression

```python
from meshflow import EvalBaseline, BaselineDiff

# Save baseline
result = await suite.run(agent)
baseline = EvalBaseline.from_result(result)
baseline.save("baseline.json")

# Compare
current = await suite.run(agent)
diff: BaselineDiff = BaselineDiff.compare(baseline, current)
print(diff.pass_rate_delta)
print(diff.cost_delta_usd)
if diff.is_regression:
    sys.exit(1)
```

CLI:
```bash
meshflow eval run evals.yaml --save-baseline baseline.json
meshflow eval run evals.yaml --compare-baseline baseline.json --fail-on-regression
```

## Cost Regression CI Gate

```python
from meshflow.eval.ci_gate import CIBudgetGate, GateResult

gate = CIBudgetGate(
    max_cost_regression=0.10,    # fail if cost increases > 10%
    max_token_regression=0.15,   # fail if tokens increase > 15%
    min_quality_score=0.80,      # fail if quality drops below 80%
)

result: GateResult = gate.evaluate(baseline, current_result)
if result.failed:
    print(result.summary())
    sys.exit(1)
```

GitHub Actions workflow (`.github/workflows/cost-regression.yml` is included in the repo).

## LLMJudge

```python
from meshflow import LLMJudge, JudgeScore, JudgeSuiteResult

judge = LLMJudge(
    criteria="Is the response accurate, helpful, and concise?",
    scale=10,
)

score: JudgeScore = await judge.score(
    input="What is Python?",
    output="Python is a high-level programming language.",
)
print(score.score)      # 0–10
print(score.rationale)
```

## ConversationEval

```python
from meshflow import ConversationEval, ConversationCase, EvalTurn, EvalConversationResult

eval = ConversationEval(agent=agent)
case = ConversationCase(
    name="multi-turn-support",
    turns=[
        EvalTurn(input="Hi, I need help.", expected_contains="help"),
        EvalTurn(input="What are your hours?", judge="llm",
                 criteria="Provides business hours or asks for clarification"),
    ]
)

result: EvalConversationResult = await eval.run(case)
for turn in result.turn_results:
    print(turn.passed, turn.score)
```

## ABTest

```python
from meshflow import ABTest, ABVariant, ABTestResult

test = ABTest(
    variants=[
        ABVariant(name="gpt4o",  agent=Agent(name="a", model="gpt-4o")),
        ABVariant(name="sonnet", agent=Agent(name="b", model="claude-sonnet-4-6")),
    ],
    inputs=["Summarize AI safety", "What is RAG?"],
    judge=LLMJudge(criteria="accuracy and conciseness"),
)

result: ABTestResult = await test.run()
print(result.winner)           # variant name
print(result.cost_comparison)  # dict of variant → total cost
```

## QualityGate

```python
from meshflow import QualityGate, QualityReport

gate = QualityGate(
    min_pass_rate=0.90,
    min_avg_score=7.5,
    judge=LLMJudge(criteria="correctness"),
)

report: QualityReport = await gate.evaluate(agent, eval_suite)
if report.failed:
    print(report.reason)
    sys.exit(1)
```

## Production Feedback + Shadow

```python
from meshflow import FeedbackStore, FeedbackRecord, shadow_run, ShadowResult

# Collect feedback
store = FeedbackStore("feedback.db")
store.record(FeedbackRecord(
    run_id="run-123",
    step_id="node-2",
    rating=4,
    comment="Good but could be more concise",
))

# Shadow run — run new agent in parallel, compare without affecting prod
result: ShadowResult = await shadow_run(
    production_agent=prod_agent,
    shadow_agent=candidate_agent,
    task="Summarize the quarterly report",
)
print(result.production_output)
print(result.shadow_output)
print(result.regression_detected)
```
