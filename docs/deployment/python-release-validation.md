# Python release validation

Validated locally on 2026-11-01 against the working tree, using Python 3.14
for the full suite and Python 3.11/3.12 for installed-wheel checks.
These results do not establish deployment approval or replace CI on Python
3.11 and 3.12. The working tree includes uncommitted runtime changes.

## Offline checks

- Full offline suite: 5,921 passed, 51 skipped, 76 subtests passed; 89
  deprecation warnings. Command:
  `MESHFLOW_MOCK=1 ANTHROPIC_API_KEY='' .venv/bin/python -m pytest tests/ --ignore=tests/test_integration.py -q --tb=short`.
- HTTP tests required localhost socket access outside the restricted sandbox.
  Initial focused results were 62 passed and four socket-permission failures;
  the subsequent full run with socket access passed.
- Repository lint (`.venv/bin/python scripts/check_lint.py`) passed, as did
  the governance-specific Ruff command in `.github/workflows/ci.yml`.
- All eight type-check targets in that workflow passed with its configured flags.
- `git diff --check` passed.

## Distribution checks

`python -m build --outdir /tmp/meshflow-release-validation` could not fetch
Hatchling because PyPI DNS/network access was unavailable. Building with the
already-installed backend succeeded:

```sh
.venv/bin/python -m build --no-isolation --outdir /tmp/meshflow-release-validation
```

This produced a source archive and a wheel built from that archive. Both include
`core/session.py`, `core/request_budget.py`, `budget/reservations.py`, and
`streaming/sync_bridge.py` under `meshflow/`. The wheel includes `py.typed`.
After extracting the wheel, runtime imports passed from `/tmp` with its extracted
directory on `PYTHONPATH`, using existing environment dependencies. This was an
import smoke check, not a clean dependency installation.

| Artifact | SHA-256 |
| --- | --- |
| `meshflow-1.15.0-py3-none-any.whl` | `8229772963f3138a313e3866152271d06aa59400814be20f27bb2e7e44a32eea` |
| `meshflow-1.15.0.tar.gz` | `eae1fd5ac08a25a2d2e344a533fa46f2136263bd247d1a9df94b7c319e23401f` |

Artifacts are temporary local evidence and must be rebuilt after source changes.
Nothing was published or deployed during these checks.

## Remaining release gates

Confirm hosted Linux CI results. Complete the live billing,
staging, and multi-host budget gates described in
[step-by-step validation](step-by-step-validation.md) before claiming those
deployment guarantees.

## Follow-up isolated build and clean installations

With network access enabled, the isolated build succeeded in
`/tmp/meshflow-isolated-release-validation`. The wheel built from its source
archive was installed into fresh Python 3.11 and 3.12 virtual environments,
including runtime dependencies from PyPI. `pip check` passed in both.

Five regression modules were copied outside the checkout so imports resolved
to the installed wheel: governance guarantees, governed agent modes, shared
reservations, staging boundaries, and worker recovery. All 47 tests passed
on each Python version, including HTTP and worker-subprocess checks. Test tools
were pytest and pytest-asyncio 1.3.0. The optional OpenAI SDK was installed for
the SDK retry test; initial runs without it each reported 46 passing tests
and one missing-dependency failure. The final environments used OpenAI 3.42.0
and Anthropic 2.3.0. No paid inference requests were made.

Both final runs reported one aiohttp `NotAppKeyWarning` for request principal
storage. No runtime source fixes were required in this validation pass.

| Isolated artifact | SHA-256 |
| --- | --- |
| `meshflow-1.15.0-py3-none-any.whl` | `aa6046cf02acf8a53c004282ac79c4e27b535ffb1a3b95145d2c47a6a102b25a` |
| `meshflow-1.15.0.tar.gz` | `a869f1cf9cda74fbaf226bf0bc244f20309a1d00ec8a5743461757a4048eeeba` |

These hashes identify this follow-up build; they supersede the earlier artifacts
for clean-install evidence. Documentation changed between builds. Full-suite
results above still refer to the original Python 3.14 checkout run.

## Full supported-version regression runs

Installed the declared `dev` extra into both temporary environments and ran
the full offline suite against the checkout, with each environment's CLI on
`PATH` and localhost socket access enabled. These are local macOS runs, not
hosted Linux CI results.

The first Python 3.11 run found one test defect: the missing-Redis test removed
`redis` from `sys.modules`, allowing the installed optional dependency to be
imported again. `tests/test_sprint91.py` now uses `monkeypatch.setitem` with a
`None` module entry to simulate absence independently of installed packages.
Its 40 tests passed on both versions after the correction.

Final full-suite results:

| Python | Passed | Skipped | Subtests passed | Warnings | Duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3.11.11 | 5,921 | 51 | 76 | 1 | 87.20 s |
| 3.12.14 | 5,921 | 51 | 76 | 24 | 89.71 s |

Both environments passed the eight CI type-check targets and `pip check`.
Repository lint and `git diff --check` passed after the test fix. Temporary full
logs are `/tmp/meshflow-full-py311.log` and `/tmp/meshflow-full-py312.log`.
No runtime implementation changes were needed. The isolated artifacts above
predate this test-only correction and subsequent documentation updates.

## Automated packaging gate

CI now builds the source archive and its wheel on Python 3.11 and 3.12,
installs the wheel into a fresh virtual environment, checks dependencies, and
runs `scripts/smoke_installed_wheel.py` with isolated Python imports from outside
the checkout. The check verifies the installed package location, typing marker,
new runtime imports, governed sandbox execution, and audit persistence and chain
verification after reopening the database. The release build uses the same check
before uploading the distributions used by publishing jobs.

The smoke script passed locally against installed wheels on Python 3.11 and
3.12. Workflow YAML parsing, repository lint, and diff whitespace checks passed.
Hosted workflow execution remains pending; no release was triggered.

## Hosted status review and offline evaluation repair

Read-only GitHub Actions inspection found failures for CI and Cost Regression
Gate on commit `081a368f238731d829a9f59201e57e02ad048a46`. The corresponding
logs returned HTTP 410, so their failure causes could not be established.
These runs predate the current uncommitted changes.

Source review found that the cost workflow fabricated passing zero-usage JSON
when its eval suite was absent or evaluation failed. The suite was missing,
and the command used an unsupported module invocation and the wrong subcommand.
The workflow now runs the committed three-scenario offline suite through
`meshflow eval`, requires all scenarios to pass, and propagates failure.
Baseline lookup now matches the uploaded filename. The `eval-baseline-v2`
artifact starts a new baseline history without reusing synthetic results.
The offline job no longer receives a cloud API secret.

Local validation: all three scenarios passed on Python 3.11 and 3.12 with 37
mock tokens. Self-comparison passed; a doubled-token result exited 1.
The integration and gap-closure modules passed all 97 tests. Workflow YAML
parsing and diff whitespace checks passed. This is an offline behavior/token
regression check; mock zero cost does not measure provider billing, and
non-empty output checks do not establish model quality. No remote changes
were made or workflows triggered.

## Remaining workflow checks and benchmark repair

The cost workflow's exact test command (`pytest tests/ -q --tb=short
-m "not slow" --no-header -x`) passed: 5,969 passed, 51 skipped, 76 subtests
passed, and one warning on Python 3.11, in 88.34 seconds. This includes
integration tests omitted by the main CI command. This run preceded the new
benchmark regression test.

Local `zt-audit` JSON reports for Foundation, Enterprise, and HIPAA each reported
100 and no control gaps. These are software configuration reports, not independent
compliance certification or deployed-system evidence.

Benchmark review found an unused `_simulated_provider` assignment, failed runs
counted as successes, unclosed microbenchmark ledgers, and a quick flag that did
not reduce iteration counts. The benchmark now passes its simulated provider
through `AgentConfig` on an explicit executor, counts only completed runs,
closes ledgers, and reduces each phase in quick mode. Failed runs cause a nonzero
exit, and CI no longer ignores benchmark command failure.

The corrected quick benchmark completed on Python 3.11 and 3.12 with 100%
completion in both scenario and memory phases. The old stalled benchmark
processes started during diagnosis were terminated. The new regression test
verifies provider dispatch and exact simulated usage; it and the sprint21 module
passed all 59 tests. Changed-file Ruff and diff whitespace checks passed.
Temporary receipts are `/tmp/meshflow-bench-release-validation-final.json`
and `/tmp/meshflow-bench-release-validation-py312.json`. These short local
synthetic runs are smoke checks, not deployment capacity measurements.

## Final local candidate

Rebuilt the working tree with isolated build environments into
`/tmp/meshflow-final-release-candidate`. The source archive includes the new
offline eval suite, installed-wheel smoke script, and benchmark regression test.
The wheel was installed with its runtime dependencies into a fresh Python 3.12
virtual environment without development extras. Dependency consistency and the
isolated-import governance/audit smoke check passed.

| Final artifact | SHA-256 |
| --- | --- |
| `meshflow-1.15.0-py3-none-any.whl` | `aa6046cf02acf8a53c004282ac79c4e27b535ffb1a3b95145d2c47a6a102b25a` |
| `meshflow-1.15.0.tar.gz` | `1ee8e69e45e57a922408211043cbfdac3ad0fa58898bf94dbd81263afe0990c5` |

The wheel hash matches the previous isolated build: subsequent corrections
affected tests, benchmarks, workflows, and documentation rather than packaged
runtime code. All 99 targeted tests across the benchmark smoke, sprint21, and
sprint91 modules passed on Python 3.12. Four workflow YAML files parsed,
repository and changed-file lint passed, and diff whitespace checks passed.
These artifacts precede this final documentation entry.

Local offline validation is complete for this pass. Hosted workflow execution
requires a branch containing these changes. Live staging validation requires an
identified isolated target; no such target was supplied in this session.
