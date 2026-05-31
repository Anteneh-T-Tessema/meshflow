# Branch Protection Settings

This file documents the branch protection rules for `main`. Apply these in
**GitHub → Settings → Branches → Branch protection rules → Add rule** for `main`.

## Required settings for `main`

| Setting | Value |
|---------|-------|
| Require a pull request before merging | ✅ |
| Required approvals | 1 (2 for governance/kernel changes — see CODEOWNERS) |
| Dismiss stale reviews on new commits | ✅ |
| Require review from Code Owners | ✅ |
| Require status checks to pass | ✅ |
| Required status checks | `test (3.11)`, `test (3.12)`, `test (3.13)`, `lint`, `type-check` |
| Require branches to be up to date | ✅ |
| Require conversation resolution | ✅ |
| Restrict force pushes | ✅ |
| Restrict deletions | ✅ |

## Rationale

MeshFlow is governance infrastructure. A broken `main` has compliance implications for anyone who installs from PyPI. The branch protection rules reflect that — no direct pushes, no force pushes, always green CI.

The CODEOWNERS file ensures that changes to the governance kernel (`StepRuntime`, `ReplayLedger`, compliance profiles) always require the lead maintainer's review.
