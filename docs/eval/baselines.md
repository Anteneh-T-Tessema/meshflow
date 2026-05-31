# Baselines and CI Budget Gate

Save a golden eval snapshot and block merges that regress quality, token usage, or cost.

```python
from meshflow.eval import EvalBaseline, EvalSuite

# After a known-good run, save the baseline
result = await EvalSuite.from_yaml("evals.yaml").run(agent)
baseline = EvalBaseline.from_result(result)
baseline.save("evals/baseline.json")
```

## `EvalBaseline`

Serialisable snapshot of an `EvalResult` used as the golden reference.

| Field | Type | Description |
|---|---|---|
| `suite_name` | str | Name of the `EvalSuite` |
| `timestamp` | str | ISO-8601 capture time |
| `pass_rate` | float | Fraction of scenarios that passed (0–1) |
| `weighted_score` | float | Weight-averaged score (0–1) |
| `total_tokens` | int | Total tokens consumed |
| `scenarios` | dict[str, ScenarioBaseline] | Per-scenario snapshots keyed by name |

### Creating and Saving

```python
baseline = EvalBaseline.from_result(result)
baseline.save("evals/baseline.json")
```

### Loading and Comparing

```python
old = EvalBaseline.load("evals/baseline.json")
new_result = await suite.run(agent)
new = EvalBaseline.from_result(new_result)

diff = old.diff(new)
print(diff.report())
if diff.has_regressions:
    sys.exit(1)
```

## `BaselineDiff`

Returned by `EvalBaseline.diff(other)`.

```python
diff.suite_name          # str
diff.pass_rate_delta     # float — new minus old (negative = regression)
diff.score_delta         # float — weighted_score delta
diff.token_delta         # int   — total_tokens delta
diff.has_regressions     # bool
diff.regressions         # list[str] — scenario names that newly failed
diff.improvements        # list[str] — scenario names that newly passed
diff.report()            # formatted multi-line string
```

## CLI Workflow

```bash
# Save baseline after a good run
meshflow eval run evals.yaml --save-baseline evals/baseline.json

# On every PR, compare against it
meshflow eval run evals.yaml \
    --compare-baseline evals/baseline.json \
    --fail-on-regression
```

## Cost Regression Gate (`CIBudgetGate`)

`CIBudgetGate` extends baseline comparison to token consumption, cost, and pass-rate. It blocks a build when any metric regresses beyond the configured threshold.

```python
import sys
from meshflow.eval.ci_gate import CIBudgetGate

gate = CIBudgetGate(
    baseline_path="eval_baseline.json",
    max_token_regression=0.10,    # fail if tokens increase > 10 %
    max_cost_regression=0.10,     # fail if cost   increases > 10 %
    max_quality_regression=0.05,  # fail if pass_rate drops  > 5 pp
)

exit_code = gate.check(new_result_path="eval_current.json")
sys.exit(exit_code)
```

### `RegressionReport` Fields

| Field | Type | Description |
|---|---|---|
| `baseline_tokens` | int | Tokens in baseline |
| `current_tokens` | int | Tokens in current run |
| `token_delta_pct` | float | `(current − baseline) / baseline` |
| `baseline_cost_usd` | float | Cost in baseline |
| `current_cost_usd` | float | Cost in current run |
| `cost_delta_pct` | float | Relative cost change |
| `baseline_pass_rate` | float | Pass rate in baseline |
| `current_pass_rate` | float | Pass rate in current run |
| `pass_rate_delta_pp` | float | Absolute pp change (negative = regression) |
| `token_regression` | bool | `token_delta_pct > max_token_regression` |
| `cost_regression` | bool | `cost_delta_pct > max_cost_regression` |
| `quality_regression` | bool | `pass_rate_delta_pp < -max_quality_regression` |
| `any_regression` | bool | Any of the above is True |

### `compare_dict(current: dict) -> RegressionReport`

Skip file I/O and compare a metrics dict directly.

```python
report = gate.check_dict({"total_tokens": 5000, "total_cost_usd": 0.02, "pass_rate": 0.90})
```

### CLI

```bash
python -m meshflow.eval.ci_gate \
    --baseline eval_baseline.json \
    --current  eval_current.json  \
    --max-token-regression 0.10   \
    --max-cost-regression  0.10   \
    --max-quality-regression 0.05
```

## GitHub Actions Integration

```yaml
# .github/workflows/cost-regression.yml
name: Cost & Quality Regression Gate
on: [pull_request]

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install meshflow

      - name: Run eval suite and save metrics
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          python - <<'EOF'
          import asyncio, json
          from meshflow.eval import EvalSuite, run_eval

          async def main():
              result = await run_eval(agent, "evals.yaml")
              metrics = {
                  "total_tokens":  result.total_tokens,
                  "total_cost_usd": result.total_cost_usd,
                  "pass_rate":     result.pass_rate,
              }
              json.dump(metrics, open("eval_current.json", "w"), indent=2)

          asyncio.run(main())
          EOF

      - name: Cost & quality regression gate
        run: |
          python -m meshflow.eval.ci_gate \
            --baseline eval_baseline.json \
            --current  eval_current.json  \
            --max-token-regression 0.10   \
            --max-cost-regression  0.10   \
            --max-quality-regression 0.05

      - name: Update baseline on main
        if: github.ref == 'refs/heads/main'
        run: cp eval_current.json eval_baseline.json && git add eval_baseline.json && git commit -m "chore: update eval baseline" && git push
```

The gate skips (exit 0) when no baseline file exists yet, so the first run after adding the workflow always passes.
