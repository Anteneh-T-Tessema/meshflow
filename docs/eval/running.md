# Running Evals

Run a YAML eval suite against any MeshFlow agent in one line.

```python
from meshflow.eval import run_eval

result = await run_eval(agent, "evals.yaml")
print(result.report())
```

## YAML Format

```yaml
version: "1.0"
name: "Research Agent Evals"
policy: dev  # governance mode applied during eval runs

scenarios:
  - name: basic_qa
    input: "What is the capital of France?"
    expected_contains: ["Paris"]
    expected_not_contains: ["Berlin", "London"]
    min_confidence: 0.8
    max_tokens: 200
    tags: [smoke, factual]
    weight: 1.0

  - name: code_generation
    input: "Write a Python function that reverses a string"
    expected_contains: ["def", "return"]
    eval_fn: check_runnable_python
    max_tokens: 500
    tags: [code]

  - name: json_output
    input: "Return JSON with keys: name, age, city"
    eval_fn: valid_json
    tags: [structured]

  - name: custom_eval
    input: "Summarize the French Revolution in one sentence"
    eval_fn: |
      words = output.split()
      return 5 <= len(words) <= 50
```

### Scenario Fields

| Field | Type | Description |
|---|---|---|
| `name` | str | Unique scenario identifier |
| `input` | str | Prompt sent to the agent |
| `expected_contains` | list[str] | Substrings the output must include (case-insensitive) |
| `expected_not_contains` | list[str] | Substrings the output must not include |
| `min_confidence` | float | Minimum confidence score from the agent (0–1) |
| `max_tokens` | int | Hard token budget; 0 = unlimited |
| `eval_fn` | str or inline Python | Custom pass/fail function receiving `output: str` |
| `tags` | list[str] | Used to filter scenarios with `EvalSuite.filter()` |
| `weight` | float | Contribution to weighted_score (default 1.0) |
| `context` | dict | Extra context dict forwarded to `agent.run()` |

### Built-in `eval_fn` Values

- `valid_json` — output parses as valid JSON
- `check_runnable_python` — output contains syntactically valid Python
- `non_empty` — output has more than 10 non-whitespace characters
- `no_hallucination_markers` — output does not contain hedging phrases like "as an AI"

## Programmatic API

```python
from meshflow.eval import EvalSuite, run_eval

# Load from YAML
suite = EvalSuite.from_yaml("evals.yaml")

# Filter to a tag subset before running
smoke = suite.filter(["smoke"])
result = await smoke.run(agent, concurrency=4)

# Or use the shortcut
result = await run_eval(agent, "evals.yaml")
```

## CLI

```bash
# Run an eval suite
meshflow eval run evals.yaml --agent my_agent.py

# Run only tagged scenarios
meshflow eval run evals.yaml --tag smoke

# Save output for baseline comparisons
meshflow eval run evals.yaml --save-baseline baseline.json
```

## Result Structure

`EvalResult` is returned by `suite.run()` and `run_eval()`.

```python
result.suite_name      # str  — name from YAML
result.total           # int  — number of scenarios
result.passed          # int  — scenarios that passed all checks
result.failed          # int  — scenarios that failed at least one check
result.errors          # int  — scenarios that raised an exception
result.pass_rate       # float — passed / total (0–1)
result.weighted_score  # float — weight-averaged score (0–1)
result.total_tokens    # int  — total tokens consumed
result.total_cost_usd  # float — total LLM cost
result.duration_s      # float — wall-clock seconds
result.scenarios       # list[ScenarioResult]
```

Each `ScenarioResult` contains:

```python
sr.scenario_name  # str
sr.passed         # bool — True only when all checks pass
sr.score          # float — fraction of checks that passed
sr.checks         # dict[str, bool] — per-check results
sr.output         # str — raw agent response
sr.tokens         # int — tokens used for this scenario
sr.confidence     # float — agent confidence score
sr.duration_ms    # float — scenario wall-clock time
sr.error          # str — exception message if the run errored
```

```python
# Print a formatted report
print(result.report())          # summary only
print(result.report(verbose=True))  # includes per-check breakdown
```
