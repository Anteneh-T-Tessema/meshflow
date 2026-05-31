## What does this PR do?

<!-- One sentence. What changes and why. -->

## Type

- [ ] Bug fix
- [ ] New feature
- [ ] Performance / token optimization
- [ ] Governance / compliance
- [ ] Documentation
- [ ] Refactor
- [ ] CI / tooling

## Related issue

Closes #

## Checklist

- [ ] Tests added for every new behavior (run `pytest` — must be 100% green)
- [ ] No new test skips without a documented reason
- [ ] `meshflow doctor` still passes after this change
- [ ] Docs updated if this changes public API or behavior
- [ ] `CHANGELOG.md` entry added under `## [Unreleased]`

## For governance / compliance changes

- [ ] Compliance profile tests updated
- [ ] Audit chain integrity preserved (no ledger schema changes without migration)
- [ ] `meshflow snapshot export` output still valid

## For token optimization changes

- [ ] Cost regression gate passes: `pytest tests/ -k cost` or `meshflow eval run evals.yaml --compare-baseline baseline.json`
- [ ] Before/after token count documented in PR description

## Testing

<!-- How did you verify this? Include the minimal command someone can run to confirm. -->

```bash
MESHFLOW_MOCK=1 pytest tests/test_your_test.py -v
```

## Screenshots / traces (optional)

<!-- For UI changes or trace output differences, include before/after. -->
