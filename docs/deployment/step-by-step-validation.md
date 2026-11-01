# Step-by-step deployment validation

Run these gates in order. A prepared command or an offline test does not count as
live cloud or deployed staging evidence.

## 1. Cloud provider validation

Choose a provider and a compatible model that supports chat completion, text
streaming and function tools. The current runner supports OpenAI, Anthropic and
Azure OpenAI. Azure uses an existing deployment name and supports a local API key
or a short-lived Azure CLI token. Bedrock validation still requires its own adapter
and a valid AWS identity.
The OpenAI branch uses `max_completion_tokens`; OpenAI documents the older
`max_tokens` parameter as deprecated and incompatible with o-series models.
Interrupted streams may omit terminal usage, so cancellation retains its bound.
See the [official OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

Set the API key locally as `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, either in the
runner's environment or this repository's ignored `.env` file. `--env-file` selects
another file. The loader reads only the selected provider key as data and never
executes file contents; an existing environment credential takes precedence.
Never put a key
in a command argument, a receipt, a committed file, or chat.

Set `CLOUD_PROVIDER`, `CLOUD_MODEL`, `INPUT_USD_PER_1K` and `OUTPUT_USD_PER_1K`
from the provider's current pricing for that exact model. No price is inferred.

Review preflight without sending a model request:

```sh
MESHFLOW_MOCK=0 .venv/bin/python scripts/validate_cloud_provider.py \
  --provider "$CLOUD_PROVIDER" --model "$CLOUD_MODEL" \
  --input-per-1k "$INPUT_USD_PER_1K" --output-per-1k "$OUTPUT_USD_PER_1K" \
  --max-usd 0.05 --max-tokens 128
```

When preflight reports `ready`, append `--execute` to run the checks. The runner
uses one strict run budget and the configured output limit for completion,
streaming, early close/cancellation, and a read-only addition tool. It verifies
the audit chain and retains `receipt.json` and `audit.db` in the reported local
evidence directory. `--output-dir` can select that directory explicitly.

A `passed` receipt establishes functional API/usage/audit behavior. It does not
prove invoiced dollars. Reconcile the successful requests' token usage and the
UTC receipt window with provider usage/billing records. A cancelled stream retains
a conservative bound, so `accounted_or_reserved_usd` may exceed the final bill.
The receipt deliberately retains `billing_reconciled=false` until that external
check is performed. Failure receipts omit raw provider errors, which can contain
credential fragments; inspect the local audit artifact when diagnosing a failure.

If a model rejects a parameter/tool schema or finishes before cancellation can be
exercised, treat the incomplete check as evidence to investigate, not a passing
cancellation test. Do not repeatedly rerun paid checks without reviewing the result.

### Azure OpenAI

For an existing resource, select its deployment and the subscription explicitly:

```sh
MESHFLOW_MOCK=0 .venv/bin/python scripts/validate_cloud_provider.py \
  --provider azure --model "$AZURE_OPENAI_DEPLOYMENT" \
  --endpoint "$AZURE_OPENAI_ENDPOINT" \
  --azure-subscription "$AZURE_SUBSCRIPTION_ID" --auth-mode azure_cli \
  --input-per-1k "$INPUT_USD_PER_1K" --output-per-1k "$OUTPUT_USD_PER_1K" \
  --max-usd 0.05 --max-tokens 128 --execute
```

The CLI token remains in process memory and is not included in the receipt.
Management-plane access does not imply inference access: Azure may reject the
request unless the principal has an appropriate OpenAI data-plane role. The
validator does not grant roles. If CLI inference is unavailable, an existing
resource key can be configured privately as `AZURE_OPENAI_API_KEY` and used with
`--auth-mode api_key`. The validator does not retrieve or rotate account keys.

The Azure provider uses the unified `/openai/v1/` endpoint for this validation and
inherits governed tool dispatch and terminal stream-usage accounting. See
[Microsoft's endpoint/authentication guide](https://learn.microsoft.com/en-us/azure/developer/ai/how-to/switching-endpoints)
and [keyless RBAC guidance](https://learn.microsoft.com/en-us/azure/developer/ai/keyless-connections).

## 2. Staging environment

Choose an existing staging target, local Docker staging, or a new cloud target.
Verify the hosting environment classification: a user-supplied URL may be a
production alias rather than an isolated staging deployment. Public route checks
on production remain read-only; authenticated tenant tests require a dedicated
test account and cannot be replaced by a successful sign-in redirect.
Prepare the image, persistent audit storage, managed credentials, TLS, and
operator/viewer tenant keys for that target before applying changes.
Validate the deployed URL with the same boundaries covered by local tests:
health/readiness, completion-to-trace round trips, rejected unauthenticated or
viewer execution, cross-tenant reads/checkpoints, revocation, streaming cleanup,
worker restart and realistic traffic limits. Local HTTP concurrency tests are not
a deployment capacity benchmark.

## 3. Multi-host budgets

The SQLite reservation backend supports cooperating processes on one host.
Before distributing workers across hosts, implement and validate an appropriate
shared-service backend with atomic reservation, idempotent settlement, immutable
scope limits, and conservative crash handling. Do not place the SQLite file on a
network volume as a substitute for such a backend.

## 4. Release review

Review the final diff and migration notes, rerun relevant tests, build release
artifacts, and document strict-provider bounds, persistent-ledger defaults,
audit v2 compatibility, tenant enforcement, and streaming cancellation semantics.
Only publish or deploy after the target and concrete artifact are established.
