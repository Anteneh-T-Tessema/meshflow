# Contributing to MeshFlow

MeshFlow's goal is to be the gold standard of production-safe agent infrastructure — the layer that every serious agent deployment runs on. Every contribution should move toward that goal.

Thank you for being here.

---

## Before you start

- Check [open issues](https://github.com/Anteneh-T-Tessema/meshflow/issues) and [Discussions](https://github.com/Anteneh-T-Tessema/meshflow/discussions) — your idea may already be in flight.
- For major changes, open a [Discussion RFC](https://github.com/Anteneh-T-Tessema/meshflow/discussions/categories/rfcs) first. Large PRs that appear without prior discussion are hard to review and often need fundamental rework.
- For bugs, a minimal reproduction is mandatory. Set `MESHFLOW_MOCK=1` and strip the code to the smallest failing case.

---

## Three pillars — does your contribution strengthen one?

Every MeshFlow feature should strengthen at least one of:

1. **Infrastructure** — makes agents safer, more durable, or more compliant in production
2. **Distribution** — makes it easier for developers to discover and adopt MeshFlow
3. **Community** — makes it easier for developers to share and build on each other's work

If your contribution doesn't clearly strengthen one of these, it's probably out of scope.

---

## Development setup

```bash
git clone https://github.com/Anteneh-T-Tessema/meshflow
cd meshflow
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Run the full suite:

```bash
MESHFLOW_MOCK=1 pytest          # ~60 seconds, 4,379 tests
```

Run a specific test file:

```bash
MESHFLOW_MOCK=1 pytest tests/test_your_area.py -v
```

Run with a real LLM (optional — requires `ANTHROPIC_API_KEY`):

```bash
pytest -m live
```

---

## The test bar

**Every PR must have tests.** We don't merge untested code — not because we're pedantic, but because MeshFlow is governance infrastructure. If a guardrail or audit chain has a bug, it has a compliance implication for someone in production.

Rules:
- New behavior → new test
- Bug fix → regression test that would have caught it
- No new `pytest.mark.skip` without a `# TODO: remove after X` comment explaining why
- The full suite must be green: `pytest` with no arguments, no flags
- Test files live in `tests/` and follow the `test_<area>.py` naming convention

The 4,379 tests are not a badge — they're the trust mechanism. Treat them accordingly.

---

## Code style

```bash
ruff check .          # linting
ruff format .         # formatting
mypy meshflow/        # type checking (strict)
```

All three must pass. The CI pipeline enforces them.

Key conventions:
- No `print()` in library code — use the audit ledger or raise
- No `time.sleep()` in tests — mock or use async properly
- No bare `except:` — catch specific exceptions
- Public API types must be fully annotated
- Comments explain *why*, not *what*

---

## Governance changes — higher bar

Changes to the `StepRuntime`, `ReplayLedger`, `ComplianceProfile`, or audit chain carry a higher review bar because they affect every production deployment. For these:

1. Open an RFC in Discussions first
2. Include a compliance impact analysis: which HIPAA/SOX/GDPR controls are affected?
3. The audit chain hash format is immutable — no changes to `entry_hash` or `prev_hash` calculation without a deprecation cycle
4. Every compliance profile change needs a test against the affected framework's specific requirements

---

## Submitting a PR

1. Fork the repo, create a branch from `main`: `git checkout -b feat/your-feature`
2. Write code and tests
3. Run `ruff check . && mypy meshflow/ && pytest`
4. Fill in the PR template completely — incomplete PRs are closed
5. Link the issue: `Closes #123`
6. Add a `CHANGELOG.md` entry under `## [Unreleased]`

**PR title format:** `<type>(<area>): <description>`

Examples:
- `feat(agents): add model_router parameter to Agent`
- `fix(ledger): fork() generates unique step_ids to avoid UNIQUE constraint`
- `docs(governance): add compliance profile parameter table`
- `perf(cache): apply cache_control to tool definitions`

Types: `feat`, `fix`, `docs`, `perf`, `refactor`, `test`, `ci`, `chore`

---

## What gets reviewed

Every PR gets a review within **5 business days**. Reviewers check:

1. Does it strengthen a pillar?
2. Is the test coverage real (not just lines covered)?
3. Does the public API feel right? Would a new developer understand it without docs?
4. Are there compliance implications?
5. Does it introduce any regressions in cost, latency, or correctness?

We give direct feedback. We won't accept PRs that aren't ready — but we will tell you exactly what needs to change.

---

## Good first issues

Look for [`good-first-issue`](https://github.com/Anteneh-T-Tessema/meshflow/labels/good-first-issue) labeled issues. These are scoped to a single file or feature, have clear acceptance criteria, and won't require deep framework knowledge.

---

## Recognition

Contributors who merge 3+ PRs become **MeshFlow Contributors** and get:
- Listed in `CONTRIBUTORS.md`
- `contributor` role in Discord
- Early access to MeshFlow Cloud beta

Contributors who merge 10+ significant PRs can be nominated for **Maintainer** status via a governance vote.

---

## Code of Conduct

We follow the [Contributor Covenant](CODE_OF_CONDUCT.md). Be direct, be kind, be professional. We have no tolerance for harassment or gatekeeping.

---

## Questions

- **General questions:** [GitHub Discussions → Q&A](https://github.com/Anteneh-T-Tessema/meshflow/discussions/categories/q-a)
- **Security issues:** [GitHub Security Advisories](https://github.com/Anteneh-T-Tessema/meshflow/security/advisories/new) — never open a public issue for a security vulnerability
- **Discord:** https://discord.gg/meshflow
