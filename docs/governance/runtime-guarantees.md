# Runtime guarantees

`Agent.run()`, `Agent.run_typed()`, `Agent.run_structured()`, `Agent.stream()`,
`Team.run()` (including confidence early exit), complex `Workflow.run()` paths,
text `Workflow.stream()` / `Workflow.astream()` and `Flow.kickoff()` execute
through `StepRuntime`.
Simple workflows compile into governed teams. These APIs record blocked and
paused attempts as well as completed steps. Enforced Zero Trust authorization
and spotlighting failures block execution; telemetry remains best effort.

## Durable storage and recovery

The default ledger is `meshflow_runs.db`. Set `MESHFLOW_LEDGER_DB` to choose a
location, or explicitly pass `ledger_db=":memory:"` to supported async APIs for
an ephemeral run. Production execution writes synchronously to the ledger before
returning a step outcome. Graph workflows retain their existing checkpoint APIs.

Stateful, parallel, conditional and breakpoint workflows save checkpoints at step
boundaries. To recover in another process, reconstruct the same workflow and state
schema, then call:

```python
result = workflow.resume_from_checkpoint(run_id, ledger_db="meshflow_runs.db")
```

The run ID, audit chain and accumulated budget are retained. Completed step
boundaries are skipped. Interrupted steps, partially completed parallel groups,
and side effects between execution and checkpoint commit may replay. Tools must
use idempotency keys for external writes. This is not an exactly-once guarantee.
Workflow reconstruction must preserve the original callbacks and configuration;
the checkpoint checks agent topology, not executable code equivalence.

## Cost enforcement

`CostCap(usd=5)` enables strict request bounds. Native Anthropic and OpenAI-compatible
SDK requests reserve a conservative input bound and the configured output limit
before dispatch, including each tool-loop round. Parallel requests share the same
reservation tracker. An ambiguous request failure retains the full reservation
rather than assuming that the provider did not bill it.

Other providers must declare a bound for the complete operation:

```python
def request_cost_bound(self, **kwargs):
    return maximum_usd, maximum_total_tokens
```

The bound must include retries and tool rounds. External graph nodes declare
`max_cost_usd` and `max_tokens` in their node metadata. Unbounded operations are
blocked under strict caps. Local Echo/Sandbox/Ollama providers are treated as
free. `Policy(strict_budget=True)` enables this requirement for lower-level APIs;
`CostCap(usd=5, strict=False)` explicitly opts into usage-only accounting for
custom operations.

Reservations depend on configured prices and trustworthy provider usage/bounds.
Unknown native model pricing and media requests without a token bound are
blocked. Currency changes, inaccurate rates, and a provider violating its declared
bound cannot be undone after dispatch. Native SDK automatic retries are disabled;
explicit application retries obtain their own reservation.

### Shared worker budgets

Opt into a durable account shared by cooperating processes:

```python
from meshflow import Policy, Team

policy = Policy(
    strict_budget=True,
    budget_usd=1.0,                      # per-run ceiling
    shared_budget_db="shared-budgets.db",
    shared_budget_id="tenant-a:daily:2026-10-31",
    shared_budget_usd=10.0,               # account ceiling across runs/workers
    shared_budget_tokens=500_000,
)
team = Team("research", agents=[researcher, writer], policy=policy)
```

Workers use the same database, scope and limits. Account limits are immutable;
attaching with different limits raises an error rather than increasing the cap.
Money is reserved conservatively in integer nanodollars. SQLite transactions
atomically check spent amounts plus pending holds before allowing dispatch.
A zero limit is a hard zero, not an unlimited allowance.

Holds survive worker death and never expire automatically. Use
`ReservationStore.pending()` to inspect uncertain holds. `settle(id)` charges the
whole bound when actual usage is unknown; retrying settlement is idempotent.
`release(id)` is only appropriate after confirming no billable request occurred.
Account IDs represent explicit windows; rotating a window is an application choice.
These settings belong in trusted application policy, not client HTTP payloads.

This backend coordinates processes on one host with a local SQLite file. It is
not a multi-host/HA billing authority; use an appropriate shared service for that
deployment model.

## Streaming lifecycle

Agent text streams emit chunks while the provider is running. Text workflow
streams share one run ID and budget across sequential and parallel agents.
The final `done` event includes the run ID, ledger location and usage totals.
Closing an async stream cancels its producer and records partial output;
closing a synchronous workflow stream also stops and joins its background worker.
Post-step output checks run at stream completion, so text already emitted cannot
be recalled if a later check rejects the result.

Supported native streams settle terminal provider usage when it is available.
If the consumer closes early or a provider omits terminal usage, accounting retains
the conservative request bound. Custom streaming providers under strict caps must
declare `request_cost_bound`; their bound must cover the complete stream.

Nested agent calls produce their own records. Enclosing Flow/operation records
exclude child usage from their own accounting so run totals count each call once.

## Audit integrity

New records use hash format v2, covering full stored input/output, accounting,
verdicts, node identity, timestamps and metadata. Tenant scope is included in
metadata. Batched records retain independent per-run chains and Merkle proofs.
Legacy v1 records remain verifiable using their original, narrower field set.
See [the audit specification](../audit_chain_spec.md).

Hash chains expose modifications to retained records. A trusted external root or
write-once archive is needed to detect a privileged attacker rewriting the entire
chain or deleting a run. Redaction and output limits apply before hashing.

## HTTP boundaries

HTTP audit writes inherit the configured server ledger and authenticated tenant.
Client context and `X-Tenant-ID` headers cannot change ownership. Tenant-scoped
reads, checkpoints, redaction and deletion cannot target another namespace.
Run/stream execution requires an operator or admin; viewer keys are read-only.
OIDC validation errors do not fall back to static Bearer-key authentication.
Server cleanup closes default and tenant ledger handles.

## Validation and CI

Governance regression tests cover API paths, parallel reservations, fail-closed
authorization, full-record tampering, legacy verification and restart/resume.
The recorded lint backlog has been cleared. CI directly enforces the repository
E/W/F rule set, with explicit exclusions for line length, exports and redefinitions;
there is no legacy baseline. Blocking type checks cover core contracts and the
session, request-budget, shared-reservation and synchronous-stream bridge modules. This is not a claim
that every module is fully type-checked.

Offline mock tests do not validate live provider credentials, billing accuracy,
external services, or compliance certification.

Local validation can be repeated with
`MESHFLOW_MOCK=0 python scripts/validate_local_provider.py --model mistral:7b`.
It exercises real local completion, streaming usage and audit verification without
selecting a cloud provider. Cloud credentials and deployment-specific load/capacity
must be validated in the target environment.
