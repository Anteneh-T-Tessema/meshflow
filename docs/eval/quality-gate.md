# Quality Gate

Block deploys automatically when LLM judge scores drop below the baseline, complementing the cost-based CI budget gate.

```python
import sys
from meshflow.eval.quality_gate import QualityGate

gate = QualityGate(baseline_path="quality_baseline.json", avg_drop_threshold=0.05)

# Save baseline after a known-good run
gate.save_baseline({"avg_score": 0.82, "pass_rate": 0.91, "n": 40})

# On each PR, compare current run against baseline
report = gate.compare({"avg_score": 0.78, "pass_rate": 0.88, "n": 40})
sys.exit(gate.exit_code(report))  # 0 = OK, 1 = regression
```

## `QualityGate`

```python
gate = QualityGate(
    baseline_path="quality_baseline.json",  # JSON file created by save_baseline()
    avg_drop_threshold=0.05,                # flag if avg_score drops > 5 pp
    pass_rate_drop_threshold=0.05,          # flag if pass_rate drops > 5 pp
)
```

### `save_baseline(metrics: dict)`

Write current metrics to the baseline file. Minimum required keys: `avg_score`, `pass_rate`.

```python
gate.save_baseline({
    "avg_score": 0.82,
    "pass_rate": 0.91,
    "n": 40,
})
```

### `load_baseline() -> dict | None`

Read the saved baseline. Returns `None` if the file does not exist yet.

### `compare(current: dict) -> QualityReport`

Compare a current-run metrics dict against the saved baseline.

```python
report = gate.compare({
    "avg_score": 0.78,
    "pass_rate": 0.88,
    "n": 40,
})
```

If no baseline file exists, `compare()` treats the current run as the baseline (no regression flagged).

### `exit_code(report: QualityReport) -> int`

Returns `0` if the report shows no regression, `1` otherwise. Intended for `sys.exit()`.

### `check(current, *, verbose=True, update_baseline_on_pass=False) -> int`

Convenience method: runs `compare()`, prints a summary, and returns the exit code.

```python
code = gate.check(
    {"avg_score": 0.80, "pass_rate": 0.90, "n": 40},
    update_baseline_on_pass=True,  # auto-update baseline when passing
)
sys.exit(code)
```

### `check_suite(suite_result, *, verbose=True, update_baseline_on_pass=False) -> int`

Run the gate directly on a `JudgeSuiteResult` without manual dict construction.

```python
from meshflow.eval.judge import LLMJudge

judge = LLMJudge()
suite_result = await judge.score_suite(eval_results)

gate = QualityGate("quality_baseline.json")
code = await gate.check_suite(suite_result)
sys.exit(code)
```

## `QualityReport`

| Field | Type | Description |
|---|---|---|
| `baseline_avg` | float | Saved baseline `avg_score` |
| `current_avg` | float | Current run `avg_score` |
| `baseline_pass_rate` | float | Saved baseline `pass_rate` |
| `current_pass_rate` | float | Current run `pass_rate` |
| `avg_delta` | float | `current_avg − baseline_avg` (negative = regression) |
| `pass_rate_delta` | float | `current_pass_rate − baseline_pass_rate` |
| `avg_regression` | bool | `avg_delta < −avg_drop_threshold` |
| `pass_rate_regression` | bool | `pass_rate_delta < −pass_rate_drop_threshold` |
| `any_regression` | bool | `avg_regression OR pass_rate_regression` |
| `passed` | bool | `not any_regression` |
| `n_scenarios` | int | Number of scenarios in the current run |

## CLI Usage

```bash
# Save a baseline from a JSON scores file
python -m meshflow.eval.quality_gate \
    --baseline quality_baseline.json \
    --current  current_scores.json \
    --avg-drop 0.05 \
    --pass-rate-drop 0.03
```

## CI/CD Integration

```yaml
# .github/workflows/quality-gate.yml
name: Quality Gate
on: [pull_request]

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run eval suite
        run: |
          python - <<'EOF'
          import asyncio, json, sys
          from meshflow.eval import EvalSuite, run_eval
          from meshflow.eval.judge import LLMJudge

          async def main():
              suite = EvalSuite.from_yaml("evals.yaml")
              result = await suite.run(agent)
              judge = LLMJudge()
              suite_result = await judge.score_suite(
                  [{"task": s.input, "output": r.output}
                   for s, r in zip(suite.scenarios, result.scenarios)]
              )
              json.dump(suite_result.to_dict(), open("current.json", "w"))

          asyncio.run(main())
          EOF

      - name: Quality gate
        run: |
          python -m meshflow.eval.quality_gate \
            --baseline quality_baseline.json \
            --current  current.json \
            --avg-drop 0.05 \
            --pass-rate-drop 0.03
```
