# MeshFlow Governance

MeshFlow is an open-source project governed by its maintainers with community input. This document describes how decisions are made, who makes them, and how to become a maintainer.

---

## Principles

1. **Production safety is non-negotiable.** No change that weakens the audit chain, breaks compliance guarantees, or introduces security regressions will be merged — regardless of other merits.

2. **Infrastructure is stable.** The public API in `meshflow.__all__` follows semantic versioning strictly. Breaking changes require a major version bump and a deprecation cycle.

3. **Community input shapes the roadmap.** RFC Discussions are the mechanism for major decisions. Maintainers commit to responding to every RFC within 10 business days.

4. **Transparency is the default.** Roadmap, decision rationale, and sprint priorities are documented publicly.

---

## Roles

### User
Anyone who installs and uses MeshFlow. No special rights. All feedback is welcome via Issues and Discussions.

### Contributor
Anyone who has merged at least one PR. Listed in `CONTRIBUTORS.md`. Gets `contributor` role in Discord.

### Maintainer
Elected by existing maintainers. Can merge PRs, triage issues, cut releases, and vote on governance decisions. Maintainers are expected to be active — a maintainer inactive for 6 months moves to Emeritus status.

**Current maintainers:** Listed in `CODEOWNERS`.

### Emeritus Maintainer
Former maintainers who are no longer active. Honored in `CONTRIBUTORS.md`. No voting rights but always welcome to return.

---

## Decision-making

### Routine decisions (single maintainer)
- Merge a PR that has passing CI, a reviewer approval, and a filled-in template
- Close a duplicate or out-of-scope issue
- Publish a patch release

### Significant decisions (two maintainer approvals)
- New public API symbols added to `meshflow.__all__`
- Changes to `StepRuntime`, `ReplayLedger`, or any compliance profile
- Adding or removing a provider
- Deprecating a public API

### Major decisions (RFC + community discussion + maintainer vote)
- Breaking changes to the public API
- Major architectural changes (e.g. new execution model)
- New governance structure
- Commercial layer decisions

RFCs stay open for **14 days** before a vote. Votes require a simple majority of active maintainers.

---

## Releases

MeshFlow follows [Semantic Versioning](https://semver.org):

- **Patch** (`1.0.x`) — bug fixes, documentation, non-breaking tweaks. Released as needed.
- **Minor** (`1.x.0`) — new features, new providers, new compliance profiles. Released monthly or when a significant batch is ready.
- **Major** (`x.0.0`) — breaking changes to the public API. At least 6-week deprecation cycle before removal.

Release checklist:
1. All CI checks green
2. `CHANGELOG.md` updated
3. `pyproject.toml` version bumped
4. `meshflow/__init__.py` `__version__` bumped
5. `python -m build && twine upload dist/meshflow-X.Y.Z*`
6. GitHub Release created with changelog extract
7. Discord announcement

---

## RFC process

For significant features, open a Discussion in the **RFCs** category with:

- **Motivation:** what problem this solves, what production scenario it addresses
- **Proposed API:** exact public interface (code example required)
- **Alternatives considered:** what you ruled out and why
- **Compliance impact:** which HIPAA/SOX/GDPR controls this affects, if any
- **Migration path:** how existing users upgrade if this changes behavior

Maintainers will leave structured feedback. After 14 days, the RFC is either:
- **Accepted** — implementation can begin (contributor may self-assign)
- **Revised** — open questions need resolution before acceptance
- **Rejected** — with documented rationale

---

## Security policy

See [SECURITY.md](SECURITY.md). All vulnerabilities must be reported via GitHub Security Advisories — never as public issues.

Response SLAs:
- Critical (CVSS 9.0+): 24-hour acknowledgement, 72-hour patch
- High (CVSS 7.0–8.9): 72-hour acknowledgement, 7-day patch
- Medium/Low: 14-day acknowledgement, best-effort patch

---

## Becoming a maintainer

1. Contribute meaningfully — at least 10 merged PRs, including at least one that touches governance or compliance
2. Be nominated by an existing maintainer via a private message to the maintainer group
3. A vote is held among active maintainers (simple majority, 7-day window)
4. If accepted, you're added to `CODEOWNERS` and given repo write access

---

## Contact

- General: [GitHub Discussions](https://github.com/Anteneh-T-Tessema/meshflow/discussions)
- Security: [GitHub Security Advisories](https://github.com/Anteneh-T-Tessema/meshflow/security/advisories/new)
- Maintainer escalation: open a Discussion tagged `maintainer-attention`
