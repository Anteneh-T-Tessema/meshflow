# Changelog

All notable changes to MeshFlow are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.10.0] — 2026-06-02

### Self-improving mixed-model routing system

**4,963 tests passing. CI green on Python 3.11 + 3.12.**

This release completes the routing arc started in v1.9.3. Users can now build
mixed local/cloud pipelines that automatically right-size each task, escalate
only when confidence is low, and adapt their own thresholds over time —
all without changing application code.

#### `ModelRegistry` — explicit model catalog (`meshflow.DEFAULT_REGISTRY`)

Register any model once at startup. The registry is consulted by `estimate_cost()`,
`AdaptiveModelTierRouter`, and `routing-report` before falling back to pattern
detection — fixing the long-standing problem of custom Ollama fine-tunes,
LiteLLM proxies, and corporate model names being misclassified:

```python
from meshflow import DEFAULT_REGISTRY, ModelSpec

DEFAULT_REGISTRY.register(ModelSpec(
    model_id="corp-finance-llm",
    is_local=True,
    quality_estimate=0.78,
    tags=["finance", "local"],
))
```

#### `TaskScorer` — 5-factor composite routing score

Replaces raw character count with a multi-dimensional score (0–1):

```
composite = (
    0.35 × length_score          # chars / 2000
  + 0.20 × question_density      # "?" marks per sentence
  + 0.20 × conjunction_density   # adversative conjunctions
  + 0.15 × technical_density     # code / domain keywords
  + 0.10 × tool_pressure         # tool count / 5
) × task_type_multiplier         # code=1.2, analysis=1.1, summary=0.85, chat=0.8
```

#### `AdaptiveModelTierRouter` — self-improving router

Routes by composite score, not character count. Learns from `CONFIDENCE:0.XX`
markers emitted by agents — no user code changes required:

```python
from meshflow import AdaptiveModelTierRouter, ModelTier, CascadeRouter, Agent, Workflow

router = AdaptiveModelTierRouter(
    tiers=[
        ModelTier("fast",  "llama3.2",   max_tokens=512),   # $0 local
        ModelTier("smart", "mistral:7b", max_tokens=2048),  # $0 local
        ModelTier("large", "gpt-4o",     max_tokens=4096),  # cloud, pay only when needed
    ],
    adapt_every=50,          # auto-adapt thresholds every 50 routes
    exploration_rate=0.10,   # epsilon-greedy exploration, decays with experience
)
```

- `router.explain(task)` — human-readable routing rationale
- `router.stats()` → `RouterStats` — per-tier success rate, avg quality, avg latency
- `router.report()` → `RouterReport` — tier distribution + cost savings vs. always-large

#### `CascadeRouter` — FrugalGPT escalation

Start with the cheapest model; escalate automatically on low confidence:

```python
cascade = CascadeRouter(router, escalation_threshold=0.65, max_escalations=2)

wf = Workflow()
wf.add(Agent("analyst", model_router=cascade, cascade_threshold=0.65))
result = wf.run("Summarise the quarterly results.")
# → llama3.2 answers CONFIDENCE:0.90 → done, $0.00
# → if CONFIDENCE:0.40 → retries with mistral → $0.00
# → if still low → retries with gpt-4o → pay only now
```

`result` carries `cascade_escalations` (count of retries) and cumulative `tokens` + `cost_usd`.

#### Router persistence + YAML config

Learned thresholds survive process restarts:

```python
# Save after a run
router.save("router_state.json")

# Restore on next startup
router = AdaptiveModelTierRouter.load("router_state.json", store=RouterOutcomeStore("prod.db"))

# Version-control the config
router.to_yaml("router.yaml")
router = AdaptiveModelTierRouter.from_yaml("router.yaml")

# Export outcome history for the data team
router._store.export_csv("outcomes.csv")
```

#### `meshflow routing-report` CLI

```bash
meshflow routing-report --db meshflow_routing.db
meshflow routing-report --db meshflow_routing.db --state router_state.json
meshflow routing-report --db meshflow_routing.db --export outcomes.csv
meshflow routing-report --db meshflow_routing.db --json
```

#### `ModelTier(is_local=True/False)` explicit override

Custom model names (not in the known-family pattern list) can now declare their
locality explicitly, with correct cost attribution:

```python
ModelTier("fast", "corp-llm",               is_local=True)   # custom Ollama
ModelTier("smart","http://localhost:4000/v1", is_local=True)  # LiteLLM proxy
ModelTier("large","llama3.2",               is_local=False)  # force-cloud billing
```

#### New exports

`AdaptiveModelTierRouter`, `CascadeRouter`, `RouterReport`, `ModelSpec`,
`ModelRegistry`, `DEFAULT_REGISTRY`, `TaskScore`, `TaskScorer`, `score_task`,
`extract_confidence`, `RoutingOutcome`, `RouterOutcomeStore`, `ThresholdOptimizer`,
`ThresholdRecommendation`, `RouterStats`, `TierStats`

---

## [1.9.3] — 2026-06-02

### Extended thinking + always-on prompt caching + cache metrics

**4,720+ tests passing.**

#### Extended thinking — `Agent(thinking=True, thinking_budget=N)`

Claude's extended thinking is now a first-class feature. Enable it per-agent:

```python
agent = Agent(
    name="reasoner",
    model="claude-opus-4-8",
    thinking=True,
    thinking_budget=8000,   # tokens Claude can spend on internal reasoning
)
result = await agent.run("Prove why prompt caching saves 70-85% on token costs")
# result["thinking_summary"] — concise synopsis of Claude's chain of thought
# result["thinking_tokens"] — tokens consumed by the thinking block
```

- Thinking tokens are tracked in `StepRecord.metadata["thinking_tokens"]`
- Governed by the same cost-cap and budget controls as regular tokens
- Falls back gracefully when the model doesn't support thinking

#### Always-on prompt caching

Prompt caching now applies automatically for all Anthropic calls — no
`OptimizationTracker` required. Cache breakpoints are inserted whenever the
system prompt exceeds 1 024 tokens (the minimum cacheable unit).

#### Cache hit-rate metrics

`StepRecord.metadata` now carries `cache_creation_tokens` and
`cache_read_tokens` from every Anthropic response. The cloud reporter's
previously-TODO `cache_hit_rate` field is now computed from actual step data.

```python
from meshflow.cloud.reporter import CloudReporter
report = CloudReporter().report(run_id="run-xyz")
print(report["cache_hit_rate"])   # e.g. 0.83
```

#### CI / runtime fixes (backported to this release)

- `cryptography>=42.0.0` promoted to runtime dependency (was missing from `dependencies`)
- `opentelemetry-api/sdk` added to `[dev]` extras so telemetry tests run without the optional `[otel]` install
- `JWKSCache._fetched_at` sentinel changed from `0.0` to `float("-inf")` — fixes OIDC TTL check on fresh CI runners with low `time.monotonic()` values
- Zero Trust Gate rewired to use local repo code; Cost Regression Gate `.venv/bin/python` replaced with `python`

---

## [1.9.2] — 2026-06-02

### Publishing infrastructure — Smithery, Docker, README, checklist

**4,749 tests passing.**

#### Smithery MCP marketplace (`smithery.yaml`)

MeshFlow is now listable on the Smithery MCP marketplace (smithery.ai).
The manifest defines all three tools, config schema with policy enum and
budget cap, and the `commandFunction` that translates Smithery UI config
to env vars.

```bash
# List via Smithery CLI
npx @smithery/cli publish --config smithery.yaml

# Or connect GitHub repo at smithery.ai → "Add Server" → point to this repo
```

#### Docker image for self-hosted MCP server (`Dockerfile.mcp`)

```bash
# stdio MCP (Claude Desktop, Cursor)
docker run -e ANTHROPIC_API_KEY=sk-ant-... meshflowdev/meshflow-mcp:1.9.2

# HTTP proxy mode (any language, any process)
docker run -p 8080:8080 -e ANTHROPIC_API_KEY=sk-ant-... \
  meshflowdev/meshflow-mcp:1.9.2 \
  meshflow proxy --port 8080 --host 0.0.0.0
```

GitHub Actions workflow `.github/workflows/publish-docker.yml` builds and
pushes to Docker Hub + GitHub Container Registry on every version tag.

#### README — MCP/tools section + updated badges

README now shows:
- MCP Compatible + Claude Tool badges
- One-block Claude Desktop config snippet at the top of the Install section
- uvx usage (no install required)
- `meshflow_as_anthropic_tool()` and `meshflow_as_openai_tool()` one-liners
- Updated version badge (v1.9.2) and test count badge (4,749)

#### Publishing checklist (`docs/publishing_checklist.md`)

Complete step-by-step publish commands for every platform: PyPI (automated),
Claude Code skills PR, Claude Desktop, Smithery, Cursor, OpenAI GPT Actions,
Anthropic Built with Claude, Docker Hub, npm, Rust crate, Go module, Java
Maven, Product Hunt, Show HN, deepset outreach. Includes version bump
procedure and post-publish verification commands.

---

## [1.9.1] — 2026-06-02

### Skills publishing infrastructure — Claude, OpenAI, MCP clients

**4,749 tests passing.**

#### `meshflow-mcp` entry point — zero-install MCP server

```bash
# After pip install meshflow
meshflow-mcp              # starts the stdio MCP server directly

# Without installing (uvx)
uvx meshflow mcp-stdio
```

`meshflow-mcp` is now a standalone console script registered in `pyproject.toml`.  Designed for `claude_desktop_config.json`, Cursor, Zed, and Continue.dev — no subcommand required.

#### Anthropic tool-use integration (`meshflow/integrations/anthropic.py`)

Expose MeshFlow as a first-class Claude tool via the Anthropic API:

```python
from meshflow import meshflow_as_anthropic_tool, meshflow_tool_handler, meshflow_tool_result_block

tool = meshflow_as_anthropic_tool()   # full schema with policy enum
result = await meshflow_tool_handler("meshflow_run", {"task": "...", "policy": "hipaa"})
block = meshflow_tool_result_block(tool_use_id, result)
```

- `meshflow_as_anthropic_tool(tool_name, description, include_policy_param, include_run_id_param)` — Anthropic tool schema with `input_schema`; policy enum includes `hipaa/sox/gdpr/iso27001/ccpa/dora/eu_ai_act/dev/sandbox`
- `meshflow_tool_handler(tool_name, tool_input, agents, ledger_db)` — executes the task through MeshFlow, returns `{status, result, run_id, cost_usd, tokens_used, audit_chain_valid}`
- `meshflow_tool_result_block(tool_use_id, result)` — formats result as an Anthropic `tool_result` content block ready to append to messages

#### OpenAI tool integration — `meshflow_as_openai_tool()`

```python
from meshflow import meshflow_as_openai_tool

tool = meshflow_as_openai_tool()   # OpenAI function-calling schema
# Pass to client.chat.completions.create(tools=[tool])
```

Wraps the entire MeshFlow governance stack as a single OpenAI tool. Returns `{"type": "function", "function": {...}}` with `task` (required) and optional `policy` parameters.

#### MCP manifest + client setup guide

- `mcp.json` — MCP marketplace manifest with tool definitions, install instructions, and quickstart commands
- `docs/integrations/mcp_clients.md` — step-by-step setup for Claude Desktop, Cursor, Zed, Continue.dev, and `uvx` usage; Anthropic API and OpenAI tool usage examples; audit trail verification
- Skills PR docs updated to v1.9 with new trigger keywords (`Zero Trust agents`, `tool call enforcement`, `EU AI Act`, `wire-level proxy`)

#### How to publish

| Platform | Action |
|---|---|
| **Claude Code skill** | PR to `anthropics/claude-code-skills` using `docs/submissions/anthropic_skills_PR.md` |
| **Claude Desktop MCP** | Share `claude_desktop_config.json` snippet from `docs/integrations/mcp_clients.md` |
| **Cursor / Zed / Continue** | Share config snippets from `docs/integrations/mcp_clients.md` |
| **Anthropic API** | `from meshflow import meshflow_as_anthropic_tool` |
| **OpenAI API** | `from meshflow import meshflow_as_openai_tool` |
| **MCP marketplace** | Submit `mcp.json` to mcp.run or equivalent registry |

---

## [1.9.0] — 2026-06-02

### HTTP proxy server — language-agnostic enforcement

**4,749 tests passing (+12 new tests).**

#### `MeshFlowHTTPProxy` — stdlib HTTP reverse proxy (`meshflow/proxy/http_server.py`)

Any process — Python, JavaScript, Ruby, Go, Rust, curl — that routes to
`http://localhost:<port>/v1` gets full tool call enforcement and audit logging.
No SDK integration required.

```bash
# Start the proxy (no API key needed, audit-only by default)
meshflow proxy --port 8080

# With a policy file
meshflow proxy --port 8080 --policy policy.yaml

# Azure OpenAI or any OpenAI-compatible upstream
meshflow proxy --port 8080 --upstream https://my.openai.azure.com

# Point any client to the proxy
export OPENAI_BASE_URL=http://localhost:8080/v1
export OPENAI_API_BASE=http://localhost:8080/v1   # LangChain
python my_langgraph_app.py  # fully governed, zero code changes
```

Implementation:
- `ThreadingHTTPServer` — one thread per connection, concurrent-safe
- `_ProxyHandler` — `BaseHTTPRequestHandler` subclass; forwards all requests to `--upstream`
- POST `/v1/chat/completions` intercepted; non-streaming JSON responses have blocked tool calls stripped; streaming SSE responses have blocked tool call index chunks dropped
- `_assemble_from_sse(chunks)` — assembles `{index: {id, name, arguments}}` from parsed SSE dicts
- Content-Length always recomputed from actual body — never forwarded from upstream (avoids `IncompleteRead` when modified response is shorter)
- `on_block` callback fires per blocked call with `{tool_name, reason, ts, agent_id}`
- Policy YAML loaded via existing `PolicyLoader.from_yaml()` at startup
- `proxy.stats()` returns `{allowed, blocked}` counts

#### Trace viewer — tool call display

`meshflow trace <run_id>` now surfaces tool calls from `StepRecord.metadata["tool_calls"]` under each step:

```
|-- [01] v  researcher    native     conf=0.92   342ms   $0.00031   OK
|        > Quarterly revenue increased 12%...
|        v tool:web_search [llm]     allowed
|        v tool:read_file [llm]      allowed
|        X tool:exec_shell [proxy]   BLOCKED(policy:block-shell:...)
```

---

## [1.8.2] — 2026-06-02

### Async streaming proxy

**4,737 tests passing (+6 new async streaming tests).**

`MeshFlowProxy.acreate(stream=True)` now fully enforced. The async path uses proper `await` on the interceptor — no thread-pool workaround.

- `_AsyncEnforcedStream` — async iterator that collects all chunks, assembles tool call args from deltas, awaits the interceptor per tool call, then re-yields the filtered stream. Blocked tool call index chunks are dropped; content chunks pass through.
- `_SyncToAsyncStream` — wraps any sync iterable so it can be used in `async for`, enabling the sync-client fallback path in async contexts.
- `_raw_completions_astream` — returns the raw async stream from the underlying client, wrapping sync clients via `_SyncToAsyncStream`.

---

## [1.8.1] — 2026-06-02

### Streaming proxy + launch content

**4,731 tests passing (+8 new streaming tests).**

#### MeshFlowProxy streaming support

`MeshFlowProxy` now intercepts `stream=True` completions.

```python
# Streaming works — tool call enforcement applies to streamed responses too
for chunk in client.chat.completions.create(stream=True, model="gpt-4o", messages=[...]):
    print(chunk.choices[0].delta.content or "", end="")
```

Strategy: buffer all chunks, assemble complete tool call args from deltas (partial fragments can't be enforced reliably), run interceptor, re-yield — passing content chunks through immediately and dropping chunks belonging to blocked tool call indices. The `on_block` callback fires for blocked streaming tool calls exactly as it does for non-streaming.

`_assemble_tool_calls_from_chunks` — helper that reconstructs `{index: {id, name, arguments}}` from delta fragments across multiple chunks. Handles parallel tool calls (multiple indices), fragmented argument strings, and chunks without tool call data.

#### Launch content

- `docs/launch/twitter_thread.md` — 9-tweet launch thread, ready to post
- `docs/community/discord_announcement.md` — #announcements pin, #general conversation starter, #showcase seeds, #roadmap-feedback discussion prompts, week-1 daily tip schedule

---

## [1.8.0] — 2026-06-02

### Wire-level proxy, ModelRouter for all frameworks, launch

**4,723 tests passing (+36 new tests).**

#### MeshFlowProxy — OpenAI-compatible wire-level enforcement (`meshflow/proxy/openai_proxy.py`)

The enforcement gap that neither StepRuntime nor ToolCallInterceptor fully closes: frameworks like LangGraph, CrewAI, and AutoGen manage their own LLM calls internally. Any tool call they generate never passes through MeshFlow's governed execution path.

`MeshFlowProxy` sits one level lower — at the HTTP client — and intercepts universally.

```python
from meshflow import MeshFlowProxy, PolicyToolCallInterceptor
from meshflow.policy.engine import PolicyStore, PolicyEngine, PolicyAction
import openai

store = PolicyStore()
store.add_rule("block-shell", PolicyAction.DENY,
               [("tool_name", "eq", "exec_shell")], framework="tool_calls")
interceptor = PolicyToolCallInterceptor(PolicyEngine(store))

# Drop-in replacement for openai.OpenAI()
client = MeshFlowProxy(openai.OpenAI(), tool_call_interceptor=interceptor)

# Every framework using this client gets enforcement — no framework changes needed
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(client=client)
```

- `MeshFlowProxy(client, tool_call_interceptor, agent_id, on_block)` — wraps any OpenAI-compatible client
- Intercepts `chat.completions.create()` synchronously and `acreate()` asynchronously
- Blocked tool calls removed from response; allowed calls pass through unmodified
- `modified_args` support: interceptor can sanitize args (PII scrubbing) before the call reaches the framework
- `on_block` callback fires for every blocked call — wire directly to SIEM/alerting
- `proxy.stats()` / `proxy.blocked_calls()` — audit log of every decision
- `_ProxiedResponse` wraps immutable Pydantic response objects; `response.choices[0].message.tool_calls` returns only allowed calls; `response.blocked_tool_calls` lists what was stopped
- Mixed allow/block: multiple tool calls in one response handled individually

#### ModelRouter for framework adapters (`meshflow/core/node.py`)

`ModelRouter` now works with `from_crewai`, `from_langgraph`, and `from_autogen` — not just native MeshFlow `Agent`.

```python
from meshflow import ModelRouter
from meshflow.core.node import MeshNode

router = ModelRouter()  # cheap tasks → haiku, complex → opus

# CrewAI — patches each crew agent's llm before kickoff, restores after
node = MeshNode.from_crewai("crew", crew, model_router=router)

# AutoGen — patches agent.llm_config["config_list"] before generate_reply
node = MeshNode.from_autogen("agent", autogen_agent, model_router=router)

# LangGraph — two modes:
# 1. Configurable graph: passes model via config={"configurable": {"model": "..."}}
node = MeshNode.from_langgraph("graph", compiled_graph, model_router=router)

# 2. Factory mode: rebuilds graph for selected model (cached per model)
node = MeshNode.from_langgraph(
    "graph", compiled_graph,
    model_router=router,
    graph_factory=lambda model: build_my_graph(model),
)
```

Implementation: `_patch_crewai_agents` / `_restore_crewai_agents` and `_patch_autogen_agent` / `_restore_autogen_agent` use `try/finally` to guarantee the original LLM config is always restored — even if the framework raises. LangGraph factory results are cached per model so the factory is called at most once per unique model selection.

#### Launch materials finalized

- `docs/launch/show_hn.md` — updated to v1.8.0, 4,723 tests, `MeshFlowProxy` in feature list
- `docs/launch/product_hunt.md` — test count updated
- `docs/partnerships/deepset_haystack.md` — outreach email finalized and ready to send

---

## [1.7.0] — 2026-06-02

### Governed tool calls, open audit standard, Haystack integration

**4,687 tests passing (+28 new tests).**

#### Tool-call-level enforcement (`meshflow/core/tool_intercept.py`)

Every tool call an LLM generates mid-node is now evaluated against policy before execution — closing the enforcement gap between node-level governance (StepRuntime) and individual tool invocations within a node.

- `ToolCallEvent` — wire type for a pending tool call: `tool_name`, `args`, `agent_id`, `source` (`"llm"` / `"mcp"` / `"registry"`), `run_id`, `node_id`
- `ToolCallDecision` — enforcement decision: `allowed`, `block_reason`, `modified_args` (for PII-scrubbing args before execution)
- `ToolCallInterceptor` — `@runtime_checkable` Protocol; any object with `async before_call(event) -> decision` satisfies it
- `AllowListInterceptor` — deny-by-default by tool name
- `PiiScanInterceptor` — block or mask args containing PHI/credentials
- `PolicyToolCallInterceptor` — full `PolicyEngine` evaluation + optional PII scan + per-step audit log; the `source` field lets rules distinguish MCP vs LLM-generated calls
- `ChainedInterceptor` — first-DENY-wins composition; propagates `modified_args` between stages

**Zero-config activation:** a `PolicyToolCallInterceptor` backed by an empty `PolicyStore` is now instantiated on every `Mesh.run()`, `run_workflow()`, and `resume_workflow()` call. No rules = default allow; every tool call attempt is audit-logged. Users add rules via policy-as-code to enforce deny/allow semantics.

**MCP path:** `MCPGateway` accepts `tool_call_interceptor=` and fires it as step 5 in the call pipeline (after manifest validation and rate limiting, before dispatch). MCP calls blocked by the interceptor never reach the handler.

**StepRuntime integration:** tool calls made during each step are collected from the interceptor's audit log and written into `StepRecord.metadata["tool_calls"]`, so the ledger captures both the node-level outcome and every individual tool call attempt within that step.

#### Open audit chain specification (`docs/audit_chain_spec.md`)

The tamper-evident hash chain format is now published as a versioned open spec so SIEM integrations, compliance verifiers, and independent auditors can verify chain integrity without importing MeshFlow.

- Canonical field set, encoding rules, hash algorithm (`SHA-256(json.dumps(fields, sort_keys=True))`)
- Chain verification rules (prev_hash linkage + entry_hash recomputation)
- `fork()` semantics — branched runs produce independent chains from the branch point
- Export format (flat JSON array, one object per step)
- Self-contained stdlib reference verifier in ≤ 50 lines — exit code 0 = valid, 1 = tampered
- Compatibility table (v1.0–v1.6 migration `0001`/`0002`)

**`ReplayLedger.export_run()` updated** to emit a flat JSON array matching the spec (previously `{"run_id": ..., "steps": [...]}`). The reference verifier and `meshflow audit export --format json` now produce identical output.

#### Haystack integration (`meshflow/integrations/haystack.py`)

Wraps any Haystack pipeline as a governed `MeshNode` running through the full StepRuntime kernel.

- `HaystackStepAdapter(pipeline, node_id, compliance_profile, pii_scan, mask_pii, block_on_pii)` — MeshNode subclass; works with any object that has `.run(inputs: dict) -> dict`
- `governed_haystack_pipeline(pipeline, ...)` — factory; returns an adapter you add directly to a `Workflow` or `WorkflowDefinition`
- GDPR/PHI protection: `SensitiveDataDetector` scans retrieved documents before they reach downstream agents; detected PII is masked (or the step is blocked if `block_on_pii=True`)
- Haystack v1 and v2 result formats both supported
- Zero external dependencies — works offline without Haystack installed (pass any mock pipeline)
- Compliance profile metadata stored in `node.metadata["compliance_profile"]` for audit trail

```python
from meshflow.integrations.haystack import governed_haystack_pipeline
from meshflow import Workflow

adapter = governed_haystack_pipeline(
    haystack_pipeline=my_pipeline,
    compliance_profile="gdpr",
    pii_scan=True,
)
result = Workflow().add(adapter, Agent("summariser")).run("Retrieve patient notes for Q2")
```

---

## [1.4.0] — 2026-06-01

### Multi-language ecosystem + Enterprise auth

**4,583 tests passing (+43 new OIDC tests).**

#### Go SDK (`sdks/go/`)
- `meshflow.NewClient(baseURL, apiKey)` — stdlib-only, no external deps
- `RunAgent(ctx, task, opts...)` — POST /run with functional options
- `Stream(ctx, task, opts...)` — SSE streaming via channel (`<-chan StreamEvent`)
- `GetTrace(ctx, runID)`, `ApproveHITL`, `RejectHITL`, `ZTStatus`, `Health`
- `ZTPolicy`, `FoundationPolicy()`, `EnterprisePolicy()`, `AdvancedPolicy()`, `ForRegulation()`
- Full type coverage: `RunResult`, `StreamEvent`, `Trace`, `TraceStep`, `ZTStatus`, `HealthResponse`

#### OIDC/SSO (`meshflow/security/oidc.py`)
- `OIDCConfig` — config dataclass with `from_env()`, `MESHFLOW_OIDC_*` env vars
- `JWKSCache` — thread-safe JWKS fetching with configurable TTL (default 1 hr)
- `OIDCValidator` — stdlib JWT validation (RS256/ES256), signature + expiry + audience + issuer
- `OIDCPrincipal` — sub, email, role (admin/operator/viewer), raw claims
- `OIDCMiddleware` — wraps existing HTTP handler, falls back to API key auth when no Bearer

#### SSO Provider helpers (`meshflow/security/sso_providers.py`)
Pre-configured `OIDCConfig` for five platforms:
- `OktaConfig(domain, audience)` → `https://{domain}/oauth2/default`
- `Auth0Config(domain, audience)` → `https://{domain}/`
- `AzureADConfig(tenant_id, client_id)` → Microsoft Entra ID v2.0 endpoint
- `GoogleWorkspaceConfig(client_id)` → `https://accounts.google.com`
- `KeycloakConfig(base_url, realm, client_id=...)` → `{base_url}/realms/{realm}`

#### CLI: `meshflow serve` OIDC flags
```bash
meshflow serve --oidc-issuer https://dev-abc.okta.com \
               --oidc-audience meshflow-api \
               --oidc-role-claim groups
```

#### GitHub Releases
- v1.1.0, v1.2.0, v1.3.0 — full changelogs at github.com/Anteneh-T-Tessema/meshflow/releases

---

## [1.3.0] — 2026-06-01

### Enterprise security operations — SIEM streaming, red-team testing, blue/green deployments

**4,540 tests passing.**

#### SIEM Streaming (`meshflow/observability/siem.py`)
- `SIEMStreamer` — fan-out streamer to all configured backends (fire-and-forget daemon threads)
- `SplunkHECBackend` — Splunk HTTP Event Collector (`MESHFLOW_SIEM_SPLUNK_URL` + `_TOKEN`)
- `DatadogLogsBackend` — Datadog Log Management (`MESHFLOW_SIEM_DATADOG_API_KEY`)
- `GenericHTTPBackend` — any SIEM webhook (`MESHFLOW_SIEM_HTTP_URL`)
- `SIEMStreamer.from_env()` — auto-detects all configured backends
- Wired into `StepRuntime` — when ZT Advanced `siem_streaming=True`, every step emits
  `step_complete`, `step_blocked`, and `policy_violation` events automatically
- Closes ZT Advanced tier `siem_streaming` control gap

#### Red-Team Testing (`meshflow/security/red_team.py`)
- `RedTeamSuite` — 22 adversarial probes across 6 OWASP-aligned categories:
  prompt injection (6), indirect injection (3), privilege escalation (3),
  data exfiltration (4), tool poisoning (3), context manipulation (3)
- `RedTeamReport` — pass rate, risk level (low/medium/high), per-category breakdown
- CLI: `meshflow red-team [--config agent.yaml] [--categories ...] [--fail-on-risk high]`
- Probes run through full guardrail + ZT stack — reports which attacks were blocked
  and which reached the agent

#### Blue/Green Deployments (`meshflow/deploy/blue_green.py`)
- `BlueGreenRouter` — traffic splitting between blue/green slots with configurable
  promotion steps (default: 10% → 50% → 100%)
- `AgentDeployment` — versioned descriptor with health tracking (error rate, request count)
- `PromotionResult` — success/rollback outcome with per-step health log
- `DeploymentStore` — persists state to `.meshflow_deploy.json`
- CLI: `meshflow blue-green register|promote|rollback|status`
- Automatic rollback when error rate exceeds threshold during promotion

---

## [1.2.0] — 2026-06-01

### Zero Trust ops layer — env-driven tier, GitHub Actions gate, Cloud GA

**4,540 tests passing.**

#### `MESHFLOW_ZT_TIER` / `MESHFLOW_ZT_REGULATION` env vars
- Set ZT tier from the environment with zero code changes:
  `MESHFLOW_ZT_TIER=enterprise` or `MESHFLOW_ZT_REGULATION=hipaa`
- Both `Mesh.stream()` and `Mesh.run_workflow_definition()` now resolve tier
  via `_zt_from_env()` — regulation takes precedence over tier

#### GitHub Actions ZT gate (`.github/actions/zt-audit/`)
- Reusable composite action: `uses: ./.github/actions/zt-audit`
- Inputs: `tier`, `regulation`, `fail-on-gaps`, `meshflow-version`
- Outputs: `score`, `tier`, `gaps`, `passed` — posted to PR step summary
- `.github/workflows/zt-gate.yml` — example matrix (Foundation blocks, Enterprise reports)

#### MeshFlow Cloud GA
- `report_run()` now includes `zero_trust` payload — tier, score, controls active/gap
- `GET /api/zt-status` on TraceServer — live ZT posture snapshot
- Dashboard **Cloud** page — ZT posture KPIs, token optimization savings, cost
  regression baseline status, GitHub Actions ZT gate snippet

---

## [1.1.0] — 2026-06-01

### Zero Trust for AI Agents — first framework to implement the Anthropic ZT guide

**4,540 tests passing (19 skipped = live API + optional deps).**

#### Zero Trust framework (`meshflow/zero_trust/`)

MeshFlow v1.1 ships the first agentic framework implementation of the
[Anthropic Zero Trust for AI Agents](https://www.anthropic.com/security/zero-trust-ai-agents)
framework. **Foundation tier is active by default on every `Mesh.run()` and
`Workflow.run()` call** — zero configuration required.

- **`ZeroTrustPolicy`** — Foundation / Enterprise / Advanced tier presets +
  regulation presets (`hipaa`, `sox`, `gdpr`, `pci`, `nerc`).
  `controls_enabled()` / `controls_disabled()` for gap analysis.
- **`SpotlightingGuardrail`** — three strategies: `xml_tags`, `json_envelope`,
  HMAC-signed `datamark`. Blocks envelope-escape attempts. Countermeasure for
  indirect prompt injection (ZT Advanced tier input control).
- **`JITPrivilegeManager`** — time-limited privilege grants with automatic
  expiry, wildcard permissions (`write:*`), `revoke_all()` for instant
  containment, background reaper thread.
- **`AIBillOfMaterials`** — model provenance, tool hashes, dependency CVEs,
  OpenSSF scores. CycloneDX 1.5 JSON export (OWASP AI-BOM standard).
- **`ContinuousAuthorizationEngine`** — re-evaluates authorization on every
  action (not just session start). ABAC with anomaly score threshold and
  time-of-day window. `suspend()` / `unsuspend()` for instant containment.
- **`ZeroTrustOrchestrator`** — single entry point. `for_tier()` /
  `for_regulation()` factories. `session()` async context manager with JIT
  grant lifecycle, `run_agent()` convenience wrapper.

#### Default-on wiring

- `Mesh.stream()` and `Mesh.run_workflow_definition()` now instantiate a
  Foundation-tier `ZeroTrustOrchestrator` on every run, passing it to
  `GovernedStepExecutor` and `StepRuntime`.
- `GovernedStepExecutor` and `StepRuntime` accept `zero_trust=` parameter
  for continuous auth checks and input spotlighting per-step.

#### New CLI command

- **`meshflow zt-audit`** — scores a deployment against the ZT framework.
  Prints a pillar-by-pillar report with ✅ / ⚠️ / `(higher tier)` annotations.
  `--fail-on-gaps` exits non-zero for CI gates. `--json` for machine-readable output.

#### Other additions (since v1.0.0)

- **ACP bridge** (`meshflow/acp/`) — BeeAI / ACP protocol interop
- **WorkerPool** (`meshflow/core/worker_pool.py`) — horizontal scaling with
  in-memory and Redis backends
- **55 pre-built tool connectors** (`meshflow/tools/connectors.py`) — Slack,
  GitHub, web search, CRM, calendar, finance, DevOps and more
- **Cloud sandbox providers** — E2B and Modal backends via `SandboxRouter`
- **External secrets backends** — `AWSSecretsProvider`, `HashiCorpVaultProvider`,
  `EnvSecretsProvider`
- **Typed streaming helpers** — `tokens()`, `filter_stream()`, `cost_events()`
- **ModelRouter analytics** — `GET /api/analytics/model-router` + dashboard page
- **Per-step timeouts** — `Policy.step_timeout_s` with fail/skip/retry actions
- **Grafana dashboard** — `dashboards/grafana-meshflow.json` (10 panels)
- **Arize Phoenix connector** — `PhoenixExporter`, `auto_instrument()`
- **AutoGen migration guide** — `docs/migration/autogen-to-meshflow.md`

---

## [1.0.0] — 2026-05-30

### First stable release — Production/Stable

**4,349 tests passing (19 skipped = live API + optional deps).**

MeshFlow 1.0 is the first production-stable release. The public API is now
locked under semantic versioning. Breaking changes will require a major version bump.

#### Stable API surface

All symbols exported from `meshflow.__all__` are now part of the stable public API.
Internal modules (prefixed with `_`) remain subject to change.

#### What's included in 1.0

**Agents**
- `Agent` — role, tools, memory, guardrails, streaming, structured output, healing, handoffs
- `Team`, `GroupChat`, `GroupChatManager` — multi-agent coordination patterns
- `Supervisor`, `AdversarialTeam` — orchestrator and debate patterns
- `ReActAgent` — Plan → Act → Observe → Reflect loop
- `AgentSession` — stateful multi-turn with compression
- `AgentPool` — async queue, round-robin, global registry
- `CriticAgent`, `AdaptiveAgent`, `DebatePanel`, `EarlyExitAgent` — specialized patterns
- Pre-built agents library: `agents.ResearchAgent()`, `agents.CoderAgent()`, etc.

**Orchestration**
- `StateGraph` — typed LangGraph-compatible state graph with `interrupt()` / `Command` HITL
- `Flow` — event-driven decorator API (CrewAI Flows parity)
- `Crew`, `Task`, `Process` — CrewAI-compatible team primitives
- `DurableWorkflowExecutor` — SQLite / Redis / Postgres / S3 checkpoint and resume
- `WorkflowDefinition.from_yaml()` — full YAML-driven pipeline execution
- `@workflow` — decorator API for defining typed workflows
- `BranchCompare` — parallel fork comparison (LangGraph Branch & Compare parity)

**Governance (the kernel)**
- `StepRuntime` — 15-step governed execution kernel
- Compliance profiles: `hipaa`, `sox`, `gdpr`, `pci`, `nerc`
- `ComplianceGuard` — real-time mid-run enforcement
- `ComplianceReporter` + `SnapshotExporter` — post-hoc audit artifacts
- `PolicyEngine` / `PolicyLoader` — YAML policy-as-code (DENY wins, 10 operators)
- `DascGate` + `AutoRiskClassifier` + `TaintGraph` — 4-tier risk governance
- `VaultStore` — Fernet AES secret vault with PBKDF2 key derivation
- `TenantStore` / `TenantContext` — full tenant isolation with scoped DB paths
- `SLATracker` — p50/p95/p99 latency, breach detection, CLI reporting
- `AuditLedger` — SHA-256 hash chain for tamper-evident audit trails
- `KeyStore` — PBKDF2 API key management, roles (admin/operator/viewer)

**Security**
- `GuardrailStack` — 9 built-in guardrails (PII, toxicity, cost cap, JSON schema, regex, keyword)
- `SensitiveDataDetector` — 23 PHI/PII + credential patterns, mask/audit
- `PromptInjectionDetector` + `SecretScanner` — supply chain and injection defenses
- `AgentIdentity` / `sign_token` / `verify_token` — zero-trust agent authentication
- `CircuitBreaker` — per-model circuit breakers with rolling-window stats

**Memory & RAG**
- `AgentMemory` — 4-tier: Working → Episodic → Semantic (BM25) → Procedural
- `VectorStore`, `KnowledgeSource`, `AgentKnowledge` — native RAG pipeline
- `HybridRetriever` (BM25 + dense RRF), `LLMRanker`, `SelfCorrectingRAG`
- `SemanticMemoryStore` — dense embedding search
- `CrossSessionMemoryStore` — persist memories across sessions
- `MemoryConsolidator`, `TeamWorkspace` — shared team memory

**Evaluation**
- `EvalSuite` — YAML-driven evals, `--save-baseline` / `--compare-baseline` / `--fail-on-regression`
- `LLMJudge` — LLM-as-judge with structured scoring
- `ConversationEval`, `ABTest`, `QualityGate` — multi-turn and A/B eval primitives
- `ShadowResult` / `shadow_run` — production shadow mode with regression detection
- `FeedbackStore` — collect human feedback in production

**Observability**
- `EventProjector` — AuditTrail, NodeLatency, PolicyViolation, WorkflowSummary projections
- `OTELExporter` — OTLP/HTTP span export (zero external deps in core)
- `TraceServer` — visual trace studio (Sprint 69+)
- `MetricsCollector` — Prometheus-compatible metrics
- `WebhookManager` + `WebhookRetryQueue` — HMAC-signed durable webhook delivery
- `AlertEngine` — metric-threshold alert rules

**Providers**
- `AnthropicProvider` (with prompt caching), `OpenAICompatibleProvider`, `GeminiProvider`
- `BedrockProvider`, `AzureOpenAIProvider`, `OllamaProvider`, `LiteLLMProvider`
- `AzureIdentityProvider`, `BedrockIAMProvider`, `VertexAIProvider` — cloud managed identity
- `LLM("model-name")` — universal entry point
- `ProviderRouter` — role × budget × compliance → model selection
- `ModelHealthTracker` — rolling-window health, fallback chain
- `AnthropicBatchClient` — Anthropic Batch API for high-throughput eval and inference
- `CachedProvider` — SQLite LLM response cache

**Deployment**
- `Doctor` — pre-deploy environment health check
- `EnvGenerator` — generate production `.env` from schema
- `DockerDeployer` — programmatic Docker build + run
- Helm chart at `k8s/helm/`
- `meshflow serve` — FastAPI REST + SSE + WebSocket server
- `/health/live` + `/health/ready` — Kubernetes probes
- Graceful SIGTERM/SIGINT shutdown

**Protocols**
- A2A (Agent-to-Agent) protocol — `AgentCard`, `A2AClient`, `A2AServer`, `A2ATaskStore`
- MCP gateway — `MCPServer`, `MCPClient` (consume and expose MCP tools)
- TypeScript client SDK — typed REST + SSE, WebCrypto signature verification
- Go SDK — generated from OpenAPI spec

**CLI**
`meshflow serve`, `eval`, `run`, `graph`, `audit`, `compliance`, `vault`, `tenant`,
`tracing`, `policy`, `sla`, `snapshot`, `dasc`, `keys`, `webhooks`, `analytics`,
`queue`, `doctor`, `bench`

#### Migration from 0.x

- No breaking changes in the stable `__all__` surface between 0.77 and 1.0.
- `pyproject.toml` version classifier updated from `4 - Beta` to `5 - Production/Stable`.
- Sprint-numbered section headers removed from `__init__.py` (cosmetic only — no symbol changes).

---

## [0.77.0] — 2026-05-30

### Sprint 77 — Integration, CLI completeness, Studio navigation

**4231 tests passing (19 skipped).**

Wires Sprint 74-76 capabilities into the execution path, adds missing CLI
commands, integrates HybridRetriever/SelfCorrectingRAG as Agent knowledge
backends, adds RoleRouter to Crew, adds `model_router:` YAML section, and
connects the three studio pages with a shared navigation bar.

---

## [0.76.0] — 2026-05-30

### Sprint 76 — Strict competitive gap closure (all 6 frameworks)

**4231 tests passing (19 skipped).**

Closed every remaining gap from the May 2026 Competitive Intelligence document.

#### BranchCompare — LangGraph Branch & Compare mode
- `BranchCompare` runs N workflow forks in parallel from a checkpoint, diffs
  outputs, picks winner by confidence score (`core/branch_compare.py`)
- `ForkConfig(label, model_override, prompt_override, context_patch)` — each fork
  independently configured; `context_patch` implements LangGraph's State Injection mode
- `CompareResult.cost_comparison()` / `quality_comparison()` — structured analytics
- `_word_diff()` — unified diff between fork outputs

#### S3 backend for DurableWorkflowExecutor
- `_S3Store` — checkpoints stored as S3 objects under `<prefix>/<run_id>/<node_id>.json`
- `DurableWorkflowExecutor(backend="s3", s3_bucket=..., s3_prefix=...)` — serverless resume
- Fork method dispatches all four backends: memory / sqlite / redis / postgres / s3

#### RoleRouter — first-mover dynamic role assignment
- `RoleRouter` — LLM-driven role classification with 13-role catalogue
- `AgentSpec(role, goal, tools, model_tier)` → `spec.to_agent()` instantiates live agents
- Keyword heuristic fallback (no LLM required for offline use)

#### RAG depth parity with Haystack
- `LLMRanker` — LLM relevance scoring with heuristic fallback; `score_threshold` filtering
- `HybridRetriever` — BM25 + dense Reciprocal Rank Fusion; `add_texts()`, `query()`
- `SelfCorrectingRAG` — retrieve → grade → refine loop; `grade_threshold`, `max_correction_rounds`
- `RAGAnswer(text, correction_rounds, grade, context_used)`

#### Curated template library — 20 specialist templates
- 20 pre-built templates: HIPAA analyst, SOC2 auditor, GDPR DPO, security CVE researcher,
  contract legal analyst, financial risk, market researcher, Python code reviewer,
  data pipeline analyst, clinical literature, API planner, incident response, prompt engineer,
  PCI DSS checker, technical writer, A/B test analyst, cloud cost optimiser, accessibility
  auditor, agent workflow designer, competitive intelligence analyst
- `load_curated_library(registry_dir)` — loads all 20 into a local registry
- `template_by_name(name)` / `templates_by_tag(*tags)` — lookup helpers

#### Interactive studio pages
- `graph.html` — browser-based interactive Mermaid graph with clickable nodes
  (cost/latency/token detail panel), runs via `meshflow studio` on `/graph`
- `rag_builder.html` — no-code RAG pipeline configurator (7 stages: data → chunk →
  embed → retrieve → rank → generate → guard), YAML export, Dify feature parity
- `TraceServer.get_mermaid(run_id)` — generates Mermaid TD syntax from ledger steps
- `/graph` and `/rag` routes added to TraceServer

#### ModelRouter → analytics integration
- `ModelRouter(analytics_ledger=ledger)` — routing decisions emitted as async
  fire-and-forget events to the ReplayLedger, closing the cost-analytics feedback loop

---

## [0.75.0] — 2026-05-30

### Sprint 75 — Token optimization layer + CriticAgent + Haystack pipeline parity

**4141 tests passing (19 skipped).**

#### ModelRouter — pre-dispatch model tier routing (first mover)
- `ModelRouter` with `RouterConfig` — classifies task → nano/small/medium/large
- YAML-configurable tiers, keyword catalogue, token-count thresholds
- `record_decisions=True` + `savings_vs_default()` for cost analytics
- Zero competitors (LangGraph/CrewAI/AutoGen/Dify/Flowise/Haystack) have this

#### CriticAgent — propose / challenge / refine loop
- `CriticAgent(proposer, critic, max_refinements, stop_on_confidence)` closes AutoGen
  multi-agent critique gap; lighter than DebatePanel (no arbiter required)
- `CriticResult.improvement_delta` — confidence gain across refinement turns

#### ToolOutputSummarizer — token Tier 1 gap closure
- `ToolOutputSummarizer(max_tokens=500)` — nano-model summarization pass when tool
  output exceeds threshold; `passthrough_tools` set; `summary_report()` analytics

#### WorkflowDefinition.to_yaml() — Haystack pipeline portability
- Round-trip YAML export: nodes, edges, loop edges, policy, terminal
- `to_yaml(path=...)` writes to file; closes Haystack pipeline-serialization gap

#### DurableWorkflowExecutor cloud backends
- `backend="redis"` — `_RedisStore` with TTL, run index (`pip install redis`)
- `backend="postgres"` — `_PostgresStore` with UPSERT (`pip install psycopg2-binary`)

#### Subprocess sandbox hardening
- `CodeInterpreter(max_memory_mb=N)` — `resource.setrlimit(RLIMIT_AS)` on Unix
- `CodeInterpreter(block_network=True)` — strips proxy env vars from subprocess env

---

## [0.74.0] — 2026-05-30

### Sprint 74 — Scorecard gap closures: public API, managed identity, marketplace

**4080 tests passing (19 skipped).**

#### Public API promotion (7 modules → __all__)
- `AdaptiveAgent`, `DebatePanel`/`DebateNode`/`DebateResult`, `EarlyExitAgent`,
  `ContextDeduplicator`, `TokenBudgetPlanner`/`ModelSizingAdvisor`, `RewindEngine`/
  `RewindResult`/`StepSnapshot`, `ParetoAnalyzer`/`ModelBenchmark`/`BenchmarkRun`

#### Cloud managed identity providers
- `AzureIdentityProvider` — `DefaultAzureCredential` (CLI, managed identity, workload identity)
- `BedrockIAMProvider` — IAM role assumption via `sts:AssumeRole` + named AWS profiles
- `VertexAIProvider` — GCP Application Default Credentials, Vertex AI Gemini

#### Marketplace HTTP registry
- `MarketplaceClient` — `push(tmpl)`, `pull(name)`, `list_all()`, `search(query)`
- `MarketplaceServer` — self-hostable HTTP server wrapping `TemplateRegistry`
- CLI: `meshflow templates share <name> --url http://marketplace.example.com`

#### Docker isolation CI test
- `test_sprint74.py::TestDockerCodeInterpreter` — proves `docker=True` flag wiring
  and graceful-fail path without Docker daemon

---

## [0.69.0] — 2026-05-29

### Sprint 68 — Structured Output on Agent/LLM

**3747 tests passing (18 skipped).**

#### `Agent.with_structured_output(schema)`

- **`Agent.with_structured_output(schema, *, max_retries=3)`** (`meshflow/agents/builder.py`):
  Returns a `StructuredAgent` bound to the given schema.  Calling `.run(task)`
  returns the validated Pydantic instance or dict directly — no
  `StructuredOutputResult` wrapper.  `.ainvoke(task)` is a LangChain-compatible
  alias.
- **`StructuredAgent`** (`meshflow/agents/builder.py`):
  Thin wrapper around `Agent.run_structured` that unwraps `.data` automatically.
  Exported as `meshflow.StructuredAgent`.

#### Provider `response_format` parameter

- **`LLMProvider.complete(…, response_format=None)`** (`meshflow/agents/base.py`):
  Protocol updated — all providers accept an optional `response_format` string.
- **`AnthropicProvider`**: `response_format="json"` prepends a JSON-only directive
  to the system prompt.
- **`OpenAICompatibleProvider`**: `response_format="json"` passes
  `response_format={"type": "json_object"}` natively to the API.
- **`EchoProvider`**: `response_format="json"` returns `{"echo": <input>}` JSON,
  enabling deterministic structured-output tests without an API key.

#### Tests

- `tests/test_structured_output.py` — 27 tests covering `StructuredOutputParser`,
  `with_structured_output`, `ainvoke`, Pydantic schema validation, and
  `response_format` on `EchoProvider`.

---

## [0.68.0] — 2026-05-29

### Sprint 67 — Flows Decorator API

**3699+ tests passing.**

#### Event-driven workflow decorators (CrewAI Flows parity)

- **`@start()`** (`meshflow/core/flows.py`):
  Marks one or more Flow methods as entry points.  All `@start` methods
  fire concurrently when `Flow.kickoff()` is called.
- **`@listen(trigger)`** (`meshflow/core/flows.py`):
  Fires after `trigger` completes.  Trigger may be a method name string,
  a method reference, or a `(method, route)` tuple for router branches.
- **`@router(trigger)`** (`meshflow/core/flows.py`):
  Conditional branching — return a route string; `@listen((trigger, route))`
  handlers fire only when the route matches.
- **`Flow[S]`** (`meshflow/core/flows.py`):
  Generic base class.  `S` must subclass `FlowState`.  Handles BFS execution,
  state propagation, and result collection.
- **`Flow.kickoff(inputs=None)`** — async execution entry point.
- **`Flow.kickoff_sync(inputs=None)`** — synchronous wrapper.
- **`Flow.plot()`** — returns a Mermaid diagram string of the flow graph.
- **`FlowState`** — typed shared state base class (subclass to add fields).
- **`FlowResult`** — `final_output`, `state`, `steps_executed`, `total_tokens`,
  `total_cost_usd`, `duration_s`.

All six symbols exported as `meshflow.Flow`, `meshflow.FlowState`,
`meshflow.FlowResult`, `meshflow.flow_start`, `meshflow.flow_listen`,
`meshflow.flow_router`.

#### Bug fix

- Fixed `Flow` router routing key: routed listeners (`@listen((trigger, route))`)
  now correctly use the trigger method name as the key, matching the documented
  `@listen((fn, route))` convention.

#### Tests

- `tests/test_flows_api.py` — 28 tests covering all decorators, `kickoff`,
  `kickoff_sync`, `plot`, chaining, branching, and public API exports.

---

## [0.67.0] — 2026-05-29

### Sprint 66 — Prebuilt Agent Graphs + StateGraph enhancements + Scorecard gap closure

**3658+ tests passing.**

#### Prebuilt Agent Graphs (LangGraph parity)

- **`MessagesState`** (`meshflow/core/prebuilt.py`):
  Built-in `TypedDict` with a `messages` channel using the `add` reducer.
- **`ToolNode`** (`meshflow/core/prebuilt.py`):
  Graph node that dispatches tool calls from the last AI message.
  Supports Anthropic content blocks, OpenAI `tool_calls`, and ReAct
  inline `Action: / Action Input:` format.  `handle_errors=True` by default.
- **`create_react_agent(model, tools, *, state_schema, system_message, max_iterations)`**:
  One-liner factory for a full ReAct loop as a `CompiledGraph`.
- **`create_tool_calling_agent(model, tools, *, system_message)`**:
  Single-shot tool-calling graph (agent → tools → end, no loop).

#### StateGraph enhancements (Sprint 68)

- **`Send(node, state={})`** — dynamic fan-out: return `Send` or `list[Send]`
  from a conditional edge to dispatch parallel branches with per-branch state.
- **`add_sequence([(name, fn), ...])`** — chain nodes in one call.
- **Subgraph nesting** — pass a `CompiledGraph` directly to `add_node()`.
- **`MemorySaver`** — in-memory checkpoint store keyed by `thread_id`.
- **`SqliteSaver`** — SQLite-backed checkpoint store, survives restarts.
- **`compile(checkpointer=...)`** — attach a checkpointer at compile time.
- **`CompiledGraph.get_state(config)`** / **`update_state(config, values)`** —
  inspect and patch saved thread state between runs.
- **`add_conditional_edges(..., mapping=None)`** — mapping is now optional for
  `Send`-based routing.

#### Scorecard gap closure (Sprints 67 RAG/context/memory)

- **`RAGTokenBudget`** (`meshflow/agents/rag_budget.py`):
  Enforce `max_chars` / `max_tokens` per knowledge injection.
  Strategies: `"truncate"`, `"drop"`, `"tail"`.
- **`SlidingWindowPruner`** (`meshflow/core/context_pruner.py`):
  Keep the N most recent messages; always preserves system prompt.
- **`SummaryPruner`** (`meshflow/core/context_pruner.py`):
  Compress old messages into a rolling summary when token count exceeds limit.
  Supports custom sync/async summarise functions.
- **`CrossSessionMemoryStore`** (`meshflow/intelligence/cross_session.py`):
  SQLite-backed persistent episodic memory across sessions.
  Features: bigram-similarity deduplication, LRU eviction, multi-agent
  isolation, tag/session filtering, keyword search.

---

## [0.26.0] — 2026-05-24

### Sprint 26 — Streaming at all layers + Sprint 27 — Native RAG / Knowledge

**1521+ tests passing (18 skipped).**

#### Sprint 26 — Streaming

- **`StreamChunk`** (`meshflow/core/streaming.py`):
  Unified streaming event type across all MeshFlow layers.
  `kind`: `token | node_start | node_end | task_start | task_end | done | error`.
  Fields: `content`, `node_name`, `task_index`, `metadata`.
  Properties: `is_token`, `is_done`. Exported as `meshflow.StreamChunk`.

- **`Team.stream(task, context)`**:
  Async generator yielding `StreamChunk` objects. Sequential/hierarchical/supervised
  patterns stream each agent in order, passing accumulated output forward.
  Parallel pattern interleaves token chunks across agents via `asyncio.Queue`.
  Each agent produces: `node_start → token… → node_end`. Ends with `done`.

- **`Crew.kickoff_stream(inputs)`**:
  Stream token-by-token from each Task in the crew — one LLM call per task
  (no double-calling). Collects streamed tokens, sets `task.output` for
  downstream context injection, then yields `task_end` with full content.
  Supports sequential, hierarchical, and parallel process modes.
  Events: `task_start → token… → task_end → done`.

- **`Agent.stream()`** already existed — regression tested (Sprint 9).

- 34 new deterministic tests in `tests/test_sprint26.py`.

#### Sprint 27 — Native RAG / Knowledge

- **`VectorStore`** (`meshflow/intelligence/knowledge.py`):
  In-memory semantic search with zero required dependencies. Embedding chain:
  sentence-transformers → numpy BoW → pure-Python char n-gram (always works
  offline). `from_texts(texts)`, `from_file(path)` (txt/md/py/json/yaml/csv/pdf),
  `from_directory(dir, extensions)`. `query(text, top_k) → list[str]`.
  Sentence-boundary-aware chunking with configurable `chunk_size` and `overlap`.

- **`KnowledgeSource`**:
  A single retrievable source — file path, directory, raw text snippet, URL,
  or `VectorStore`. Lazy-loaded on first `retrieve()`. Configurable `top_k`.

- **`AgentKnowledge`**:
  Aggregates multiple `KnowledgeSource` / `VectorStore` / string sources.
  `retrieve(query)` deduplicates across sources. `context_string(query,
  max_chars)` returns a prompt-ready `[Knowledge]` block with `---` separators.

- **`Agent(knowledge=[...])`**:
  Accepts file paths, text snippets, `VectorStore`, or `KnowledgeSource` objects.
  Before each LLM call, the agent queries its knowledge and injects retrieved
  chunks as `[Knowledge]\n...` context. Zero cost when no knowledge is provided.

- **`Task(knowledge=[...])`**:
  Per-task knowledge override; injected as `[Task Knowledge]\n...` in the
  task prompt. Independent from (and additive to) the agent's own knowledge.

- 48 new deterministic tests in `tests/test_sprint27.py`.

---

## [0.25.0] — 2026-05-24

### Sprint 25 — Guardrails: input/output validation at every agent and node

**1357+ tests passing (18 skipped).**

#### Added

- **`Guardrail` base class** (`meshflow/security/guardrails.py`):
  Abstract base with `check(text) -> GuardrailResult` and `action` field
  (`"block"` / `"warn"` / `"modify"`). `GuardrailResult` carries `passed`,
  `guardrail_name`, `reason`, `modified_text`, `severity`, and `metadata`.
  `GuardrailViolation` exception carries the failing `GuardrailResult`.

- **`GuardrailStack`**:
  Compose multiple guardrails in sequence. `mode="strict"` raises
  `GuardrailViolation` on first blocking failure; `mode="collect"` runs all.
  "modify" action guardrails rewrite the text in-place for downstream checks.
  "warn" action guardrails record the failure but do not block the stack.
  `stack.run(text) -> (all_passed, final_text, results)`.

- **8 built-in guardrails**:
  - `PIIBlockGuardrail` — detect & block/mask/warn PHI/PII via `SensitiveDataDetector`
  - `ConfidenceGuardrail` — block outputs below stated CONFIDENCE:0.XX threshold
  - `LengthGuardrail` — enforce min/max chars or word count
  - `ToxicityGuardrail` — block violence / self_harm / hate / profanity patterns
  - `JSONSchemaGuardrail` — validate JSON output; extracts from markdown fences
  - `RegexGuardrail` — require or forbid a regex pattern
  - `KeywordBlockGuardrail` — block forbidden keywords/phrases (whole-word or substring)
  - `CostCapGuardrail` — reject tasks whose estimated input cost exceeds a budget
  - `CustomGuardrail` — wrap any callable; supports `bool`, `(bool, str)`,
    `(bool, str, str)` and `(bool, modified_text)` for modify-mode

- **`Agent(input_guardrails=[], output_guardrails=[])` parameters**:
  Input guardrails run on the task text *before* the LLM call; output guardrails
  run on the LLM response *before* returning to the caller. A blocking violation
  returns `{"blocked": True, "guardrail": name, "guardrail_reason": reason}` instead
  of calling the LLM (zero cost on input block). Non-blocking runs include
  `{"blocked": False, "guardrail_results": [...]}` in the result dict.

- Exported from `meshflow`: all 9 guardrail classes + `GuardrailResult`,
  `GuardrailStack`, `GuardrailViolation`.

#### Tests

83 new deterministic tests in `tests/test_sprint25.py` across 14 test classes.

---

## [0.24.0] — 2026-05-24

### Sprint 24 — CrewAI/LangGraph/AutoGen feature parity

**1274+ tests passing (18 skipped).**

#### Added

- **Task class** (`meshflow/agents/task.py`):
  CrewAI-compatible first-class task abstraction. `Task(description, expected_output,
  agent, human_input=False, context=[], tools=[])`. Supports `{placeholder}` substitution
  in `description` via `kickoff(inputs={...})`. Auto-injects prior task outputs as context
  when `context` is set. Extra `tools` are merged for the duration of the task, then
  restored. `TaskOutput` holds `raw`, `agent_name`, `tokens`, `cost_usd`. The `output`
  field is `None` before run and filled after. Exported as `meshflow.Task`.

- **Crew + Process + CrewOutput** (`meshflow/agents/crew.py`):
  `Crew(agents, tasks, process=Process.sequential, verbose=False)` — governed crew with
  three execution modes: `sequential` (chain with auto context injection), `parallel`
  (concurrent asyncio.gather), `hierarchical` (first task is manager, rest are workers
  that receive manager output as context). `CrewOutput` aggregates per-task outputs,
  total tokens, and total cost. `Crew.kickoff(inputs={})` is the entry point.
  `Process` is a `str` enum so string literals work interchangeably. Exported as
  `meshflow.Crew`, `meshflow.Process`, `meshflow.CrewOutput`.

- **Built-in skills library** (`meshflow/agents/skills.py`):
  15 built-in skills: `python`, `javascript`, `data_analysis`, `sql`, `web_search`,
  `code_review`, `writing`, `legal`, `medical`, `security`, `api_design`, `devops`,
  `machine_learning`, `finance`, `product`. Each `Skill` is a frozen dataclass with
  `name`, `description`, and `tags`. `skill_prompt(["python", "security"])` returns
  a combined system-prompt snippet. Unknown skill names are silently ignored.
  `list_skills()` returns sorted names. Exported as `meshflow.SKILLS`, `meshflow.Skill`,
  `meshflow.skill_prompt`, `meshflow.list_skills`.

- **`Agent(skills=[], mcps=[])` parameters** (`meshflow/agents/builder.py`):
  `skills`: list of built-in skill names that augment the agent's system prompt.
  Skills are appended after the role prompt (or custom `system_prompt`).
  `mcps`: list of MCP server URLs (strings); each is registered in `MCPGateway` and
  exposed as a `Tool` in the agent's tool list. Stdio params objects are accepted too.

- **`@node` decorator** (`meshflow/core/state.py`):
  LangGraph-style decorator to mark functions as StateGraph nodes.
  `@node` bare sets `_is_meshflow_node=True` and `_node_name=fn.__name__`.
  `@node("custom_name")` sets a custom node name. Decorated functions are still
  callable directly. Exported as `meshflow.node`.

- **`interrupt()` + `Command` HITL** (`meshflow/core/state.py`):
  `interrupt(value)` raises `Interrupt` from inside a node to pause graph execution.
  `CompiledGraph.run()` catches the `Interrupt`, attaches `.node`, `.value`, `.state`
  to the raised `InterruptedError`, and stores `_interrupted_node` for resume.
  `Command(resume=..., goto=None, update={})` resumes execution: `update` is merged
  into `initial` state, `goto` redirects to a different node, `resume` carries the
  human decision back into the graph. Exported as `meshflow.interrupt`, `meshflow.Command`,
  `meshflow.Interrupt`.

#### Tests

74 new deterministic tests in `tests/test_sprint24.py` across 14 test classes:
Task, TaskOutput, Crew (sequential/parallel/hierarchical), Process, CrewOutput,
Skills library, Agent skills integration, @node decorator, interrupt/Command,
StateGraph reducers (regression), and public API surface.

---

## [0.23.0] — 2026-05-23

### Sprint 23 — Sensitive data detection, model health tracking, workflow analytics, background task queue

**1190+ tests passing (18 skipped).**

#### Added

- **Sprint 23A — SensitiveDataDetector** (`meshflow/security/sensitive_data.py`):
  Rich PHI + PII + credential detection over arbitrary text. Returns structured
  `SensitiveMatch` objects with `kind`, `category`, `value_preview`, `start`,
  `end`, `confidence`. 11 PHI/PII patterns (SSN, EMAIL, PHONE, DATE, ZIP, IP,
  URL, MRN, NPI, CREDIT_CARD, NAME) and 12 credential patterns (Anthropic/
  OpenAI/AWS/GCP/GitHub API keys, JWT, RSA private key, DB connection strings,
  high-entropy hex, Bearer tokens). `mask()` replaces PHI with `[REDACTED]`
  and credentials with `[CREDENTIAL-REDACTED]` in a single non-shifting pass.
  `audit_report()` returns a compliance-ready summary. Module singleton via
  `get_detector()` / `reset_detector()`. Exported as `meshflow.SensitiveDataDetector`.

- **Sprint 23B — ModelHealthTracker + ProviderRouter auto-fallback**
  (`meshflow/agents/health.py`, `meshflow/agents/router.py`):
  `ModelHealthTracker` records per-model success/failure outcomes in a
  rolling window (default 50; configurable via `MESHFLOW_HEALTH_WINDOW`).
  Health score = success fraction; models below threshold (default 0.7;
  `MESHFLOW_HEALTH_DEGRADED_THRESHOLD`) are marked degraded. `summary(model)`
  returns a `ModelHealthSummary` with p50/p95 latency percentiles and last
  error. Global singleton via `get_health_tracker()`. `ProviderRouter` gains
  `set_fallback_chain(*models)` and `route_with_health()` which skips degraded
  models and returns the best healthy candidate (or `best_model()` if all degraded).

- **Sprint 23C — WorkflowAnalytics** (`meshflow/core/analytics.py`):
  Async post-hoc analytics over `ReplayLedger`. `WorkflowAnalytics` exposes:
  `cost_trend(n)`, `latency_percentiles(n)` (p50/p95/p99), `blocked_rate(n)`,
  `quality_drift(n)` (uncertainty trend → "degrading"/"stable"/"improving"),
  `carbon_trend(n)`, `top_costly_nodes(n_runs, top_n)`, and `full_report(n)`.
  All methods are async. New server endpoint `GET /analytics?n=N`. New CLI
  subcommand `meshflow analytics [--metric cost|latency|blocked|quality|carbon|nodes|full]
  [--runs N] [--format text|json]`. New dashboard "Analytics" page with KPI tiles,
  cost bar chart, latency metrics, blocked-rate progress bar, quality drift delta,
  and top-costly-nodes table.

- **Sprint 23D — Background task queue** (`meshflow/queue/`):
  SQLite-backed durable async task queue. `TaskItem` (task_id, payload, status,
  priority, timestamps, result, error) persists across restarts. `TaskQueue`
  is crash-safe: tasks stuck in "running" are automatically re-queued on startup.
  `QueueWorker` provides a bounded async concurrency pool with pluggable
  `handler(TaskItem) → dict`. New server endpoints: `GET /queue/status`,
  `POST /queue/push`, `DELETE /queue/{task_id}/cancel`, `GET /queue/{task_id}`.
  New CLI subcommands: `meshflow queue push <yaml>`, `meshflow queue status`,
  `meshflow queue list [--status ...]`, `meshflow queue cancel <id>`,
  `meshflow queue worker [--concurrency N]`.

#### Changed

- `meshflow/__init__.py` version bumped to **0.23.0**.
- New top-level exports: `SensitiveDataDetector`, `SensitiveMatch`,
  `get_sensitive_detector`, `ModelHealthTracker`, `ModelHealthSummary`,
  `get_health_tracker`, `WorkflowAnalytics`, `RunSummary`, `TaskQueue`,
  `QueueWorker`, `TaskItem`, `TaskStatus`.

---

## [0.22.0] — 2026-05-23

### Sprint 22 — Dashboard v2, per-tenant rate limiting, scheduled compliance reports, declarative YAML workflows

**1131 tests passing (18 skipped).**

#### Added

- **Sprint 22A — Dashboard v2**:
  New `API Keys` page in the Streamlit dashboard: list, generate, and revoke
  keys via the `/keys` REST endpoints (admin-only). Sidebar now shows the
  authenticated user's name, role, and tenant from `GET /keys/whoami`.
  OTEL page updated to show live `OTELExporter` stats: `exported_count`,
  `error_count`, endpoint, and service name with a Refresh button.
  (`dashboard/app.py`)

- **Sprint 22D — Per-tenant rate limiting**:
  `RateLimiter` now uses `tenant_id` as the bucket key instead of the raw
  API key string. `status()` returns `tenant_id` field. Per-tenant limits
  are configurable via env vars: `MESHFLOW_RATE_LIMIT_TENANT_<ID>_RPS` and
  `MESHFLOW_RATE_LIMIT_TENANT_<ID>_BURST`. Server `_require_auth()` passes
  principal's `tenant_id` to the limiter.
  (`meshflow/observability/sla.py`, `meshflow/runtime/server.py`)

- **Sprint 22B — Scheduled compliance reports**:
  New `meshflow/compliance/scheduler.py` with `ReportSchedule` (dataclass),
  `ScheduleStore` (JSON-backed persistence at `~/.meshflow/schedules.json`),
  `ScheduledReporter.run_now()` (generates `ComplianceReporter` artifact,
  delivers to file/webhook/stdout sink). `create_schedule()` factory.
  HMAC-SHA256 signatures on webhook delivery. Three sinks: `file` (write/append
  with separator), `webhook` (HTTP POST + signature), `stdout`.
  CLI: `meshflow compliance schedule add|list|run|remove`.

- **Sprint 22C — Declarative YAML workflow extensions**:
  `WorkflowDefinition.from_yaml()` now parses:
  — `loop_edges:` list → `add_loop_edge()` (back-edges with condition + max_iterations)
  — `compliance:` section → live `ComplianceGuard` wired into `StepRuntime`
  — `metadata:` section → stored as `wf.metadata` dict
  `WorkflowDefinition.__init__` gains `compliance_guard` and `metadata` attrs.
  `describe()` includes `loop_edges`, `compliance_guard`, `metadata` fields.
  CLI `meshflow run` passes `wf.compliance_guard` to `StepRuntime`.

#### Changed

- Rate limiter bucket key changed from raw API key string to `tenant_id`
  (`"anonymous"` for open-mode / unauthenticated requests).
- `RateLimiter.status()` now returns `tenant_id` key instead of `key`.

---

## [0.21.0] — 2026-05-23

### Sprint 21 — Tenant isolation, CI, benchmarks, docs

**1082 tests passing (18 skipped).**

#### Added

- **Sprint 21A — Tenant isolation**:
  `WebhookRegistration` gains `tenant_id` field; `WebhookManager.list()`,
  `get()`, `unregister()`, `deliver()`, `delivery_history()` all accept
  optional `tenant_id` parameter for scoped filtering. Tenant-scoped
  webhooks are only visible/deletable by the owning tenant; global hooks
  (empty tenant_id) are visible to all. Server: all data endpoints
  (`/traces`, `/hitl`, `/webhooks`, `/eval-results`) use `_ledger_for(principal)`
  helper to scope ledger reads/writes to the authenticated key's tenant.
  Per-tenant ledger cache avoids per-request `ReplayLedger` construction.

- **Sprint 21B — GitHub Actions CI** (`.github/workflows/ci.yml`):
  Matrix test job on Python 3.11 + 3.12; mypy type-check job;
  ruff lint job; benchmark smoke-test job (`--quick`); artifact upload
  for test results and benchmark output.

- **Sprint 21C — Benchmark integration**:
  `bench_core.py` tracked; `--quick` flag added (first concurrency level
  only — used in CI); `benchmarks/README.md` updated with `--quick`
  documentation and latency regression comparison script.

- **Sprint 21D — Docs**:
  `docs/QUICKSTART.md` — 9-section developer quickstart covering install,
  first run, team API, policy-as-code, server, keys, endpoints, Kubernetes,
  and OTEL tracing. `SECURITY.md` at repo root (GitHub's standard location).
  All compliance guides (`HIPAA_GUIDE.md`, `GDPR_GUIDE.md`,
  `SOC2_CONTROLS_MAPPING.md`) tracked in `docs/compliance/`.

- `tests/test_sprint21.py` — 58 deterministic tests across all Sprint 21 features

---

## [0.20.0] — 2026-05-23

### Sprint 20 — API auth, Helm chart, policy-as-code, OTEL export

**1024 tests passing (18 skipped).**

#### Added

- **Sprint 20A — API key management** (`meshflow/security/api_keys.py`):
  SQLite-backed `KeyStore` with PBKDF2-SHA256 hashed secrets; three roles
  (`admin`, `operator`, `viewer`); per-tenant scoping; `create()`, `verify()`,
  `revoke()`, `list()` API; co-exists with legacy `MESHFLOW_API_KEYS` env var.
  Server: `GET /keys`, `POST /keys`, `DELETE /keys/{key_id}`, `GET /keys/whoami`;
  key management endpoints restricted to `admin` role via `_require_role()`.
  CLI: `meshflow keys generate|list|revoke --db --role --tenant`

- **Sprint 20B — Deployment artifacts**:
  Helm chart (`k8s/helm/`) — `Chart.yaml`, `values.yaml`, templates for
  Deployment, Service, Secret, PVC, HPA, `_helpers.tpl`; Deployment uses
  `/health/live` + `/health/ready` probes; autoscaling and ingress configurable
  via values. `Dockerfile` updated: non-root user (uid 1000), healthcheck uses
  `/health/live`. `docker-compose.yml` adds Redis service profile.
  `k8s/deployment.yaml` updated to use `/health/live`+`/health/ready`.

- **Sprint 20C — Policy-as-code** (`meshflow/core/policy_loader.py`):
  `load_policy_yaml(path)` → `Policy`; `load_guard_yaml(path)` → `ComplianceGuard | None`;
  `load_yaml(path)` → `(Policy, ComplianceGuard | None)` convenience;
  `validate_policy_yaml(path)` → `list[str]` of issues.
  Minimal built-in YAML parser (no PyYAML dep; uses PyYAML when installed).
  `meshflow serve --policy-file meshflow.policy.yaml` validates on startup.
  Example `meshflow.policy.yaml` in project root.

- **Sprint 20D — OTEL export pipeline** (`meshflow/observability/otel_exporter.py`):
  `OTELExporter` ships spans as OTLP/HTTP JSON to any OTEL collector
  (Jaeger, Grafana Tempo, Honeycomb, etc.) using zero external dependencies.
  `from_env()` factory reads `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_HEADERS`. Global singleton via
  `get_global_exporter()`. `StepRuntime` exports a span per step via
  `run_in_executor` (never blocks). `GET /otel/config` now reports live
  exporter state including `exported_count` and `error_count`.

- `tests/test_sprint20.py` — 75 deterministic tests across all Sprint 20 features

---

## [0.19.0] — 2026-05-23

### Sprint 19 — Webhook wiring, production hardening, TypeScript SDK, ComplianceGuard

**949 tests passing (18 skipped).**

#### Added

- **Sprint 19A — Webhook wiring into StepRuntime**:
  `meshflow/core/runtime.py` now fires `policy_violation`, `budget_exceeded`,
  `hitl_pending`, and `collusion_alert` webhooks directly from step execution
  via fire-and-forget `asyncio.create_task()` — step execution is never blocked

- **Sprint 19B — TypeScript client SDK** (`sdks/typescript/`):
  Full typed REST+SSE client for all MeshFlow API endpoints;
  `verifyWebhookSignature()` using WebCrypto (constant-time HMAC comparison);
  `liveEvents(runId?)` AsyncIterable for Server-Sent Events streaming;
  `createClient()` factory reading `MESHFLOW_SERVER` / `MESHFLOW_API_KEY` env vars;
  complete TypeScript interfaces for all MeshFlow types

- **Sprint 19C — Production hardening**:
  - `RedisBusBackend` in `meshflow/agents/messaging.py` — asyncio pub/sub
    backed by `redis[asyncio]`; drop-in for the in-memory bus
  - PostgreSQL connection pool config via `MESHFLOW_PG_POOL_MIN/MAX/TIMEOUT`
    env vars (or constructor kwargs); `statement_cache_size=100` enabled
  - Kubernetes probes: `GET /health/live` (always 200),
    `GET /health/ready` (503 during shutdown or ledger unreachable)
  - Graceful SIGTERM/SIGINT shutdown with 2 s drain window in `server.py`

- **Sprint 19D — ComplianceGuard** (`meshflow/compliance/guard.py`):
  Real-time mid-run enforcement that runs before each step executes;
  8 built-in rules across 5 frameworks — HIPAA `§164.502(b)` / `§164.312(e)`,
  SOX `§302` / `§404`, GDPR Art. `5(1)(b)` / `5(1)(c)`, PCI DSS Req 3,
  NERC CIP-007; `block_on_violation=True` raises `ComplianceViolation`
  and halts the step; `False` records violations without blocking;
  integrates with `StepRuntime` via optional `compliance_guard` parameter

- `tests/test_sprint19.py` — 47 deterministic tests across all Sprint 19 features

---

## [0.18.0] — 2026-05-23

### Sprint 18 — Compliance reporting, webhook alerting

**902 tests passing (18 skipped).**

#### Added

- `meshflow/compliance/reporter.py` — `ComplianceReporter` that generates
  structured audit artifacts from ledger data for five regulated frameworks:
  HIPAA (§164.308/312), SOX (§302/§404/§409), GDPR (Art. 5/6/30/32),
  PCI DSS v4 (Req 6/7/8/10/12), NERC CIP v6 (CIP-007/008/012/014)
- `ComplianceReport`, `ComplianceFinding`, `ComplianceSummary` data model
  with `to_dict()`, `to_json()`, `to_text()` serialisation
- `meshflow/observability/webhooks.py` — `WebhookManager`:
  in-memory webhook registry, HMAC-SHA256 signed payloads, async delivery
  with 3-attempt exponential-backoff retry, per-webhook delivery history
- Supported event types: `policy_violation`, `budget_exceeded`,
  `hitl_pending`, `run_failed`, `run_completed`, `collusion_alert`, `*`
- `GET /compliance/report?framework=hipaa&run_id=X[&format=text|json]` —
  on-demand compliance report from the ledger
- `POST /webhooks` — register a webhook endpoint
- `GET /webhooks` — list webhooks + delivery stats
- `DELETE /webhooks/{id}` — remove a webhook
- `GET /webhooks/{id}/deliveries` — per-webhook delivery history
- Dashboard pages: **Compliance** (generate/download reports per framework)
  and **Alerts** (manage webhooks, view delivery stats, register new hooks)
- `meshflow compliance report --framework <fw> [--run-id] [--format] [--out]`
- `meshflow webhooks list|add <url>|remove <id>` CLI subcommands
- `tests/test_sprint18.py` — 64 deterministic tests across all new features

---

## [0.17.0] — 2026-05-23

### Sprint 17 — OTEL traces, graph export, audit CSV, SLA monitoring, rate limiting

**838 tests passing (18 skipped).**

#### Added

- `meshflow/observability/trace_context.py` — W3C Trace Context RFC;
  `TraceContext`, `extract_trace_context()`, `inject_trace_headers()`
- `StepRuntime.run()` propagates `traceparent` header through `context`
  dict so every step carries its trace lineage
- `meshflow/core/graph_export.py` — `steps_to_mermaid()`,
  `steps_to_dot()`, `graph_to_mermaid()`, `graph_to_dot()`
- `ReplayLedger.export_run_csv()` — tamper-evident CSV audit artifact
- `meshflow/observability/sla.py` — `NodeLatencyTracker` (p50/p95/p99
  per node, thread-safe sorted reservoir) + `RateLimiter` (token-bucket
  per API key, env-configurable)
- `GET /otel/config` — OTEL setup introspection
- `GET /graph/{run_id}[?format=mermaid|dot]` — execution graph export
- `GET /audit/export[?run_id=&format=csv|json]` — compliance download
- `GET /sla[?node_id=]` — p50/p95/p99 latency per node
- `GET /rate-limit/status` — token-bucket stats per API key
- Rate limiting wired into `_require_auth` (global singleton, 60 RPS
  default, override via `MESHFLOW_RATE_LIMIT_RPS` / `_BURST`)
- SLA recording wired into `StepRuntime` (best-effort, never raises)
- `meshflow graph [--run-id] [--format] [--db] [--out]` CLI subcommand
- `meshflow audit export [--run-id] [--format] [--db] [--out]` CLI
- Dashboard pages: **Graph**, **Audit**, **SLA**, **OTEL**
- `test_sprint17.py` — 47 deterministic tests across all four features

---

## [0.16.0] — 2026-05-23

### Sprint 16 — Dashboard integration, eval history CLI, plugins endpoint

**791 tests passing (18 skipped = live API tests + streamlit missing).**

#### Added

- `GET /plugins[?group=]` — REST endpoint exposing installed plugin
  entry-points as JSON
- `meshflow eval-history [--db] [--suite] [--json]` — list stored eval
  results from the ledger in a formatted table or raw JSON
- Dashboard **Evals** page — browse stored eval baselines, select two
  to diff, display `BaselineDiff.report()`
- Dashboard **Plugins** page — list installed plugins, filter by group,
  one-click load-verify
- Dashboard **Overview** — two new KPI tiles: Last Eval Pass Rate and
  Installed Plugins count
- `fetch_eval_results()` / `fetch_plugins()` cached fetch helpers in
  `dashboard/app.py`
- `test_sprint16.py` — 15 new deterministic tests (4 skipped when
  streamlit absent)

#### Fixed

- `weighted_score` field name used consistently in CLI table and
  dashboard (was `mean_score`)

---

## [0.15.0] — 2026-05-23

### Sprint 14 — SSE events, WebSocket message bus, OpenAI adapter parity

**733 tests passing (14 skipped = live API tests).**

#### Added

- **`GET /events` SSE endpoint** — streams all `WorkflowEvent` lifecycle events
  (`STEP_START`, `STEP_COMPLETE`, `STEP_BLOCKED`, `HITL_REQUIRED`,
  `WORKFLOW_START/COMPLETE`, etc.) to any SSE client. Pass `?run_id=<id>` to
  filter to a single run. Past events are replayed on connect; then live events
  follow in real time.

- **Dashboard "Live" page** — new Streamlit page that connects to `GET /events`,
  renders a live event table (kind / run_id / node / timestamp), and accepts an
  optional run_id filter. Updates the table with each arriving SSE event.

- **`WebSocketBusBackend`** (`meshflow/agents/messaging.py`) — cross-process
  agent messaging over WebSocket. Connects to the server's `GET /ws/bus` hub;
  serialises/deserialises `Message` as JSON; fans out to remote peers and
  delivers incoming messages to local subscribers via a background drain task.

- **`InMemoryBusBackend`** — explicit in-process backend (replaces the implicit
  defaultdict behaviour). `MessageBus()` uses it by default; pass
  `MessageBus(backend=WebSocketBusBackend(url))` for cross-process.

- **`BusBackend` protocol** (`runtime_checkable`) — `publish()`, `incoming()`,
  `connect()`, `disconnect()`. Both built-in backends satisfy it.

- **`GET /ws/bus`** — WebSocket hub in the aiohttp server. Receives any JSON
  message from a connected client and fans it out to every other live
  connection, enabling agents in separate processes to communicate through one
  shared hub.

- **`team_from_openai_agents(agents, name, policy, pattern)`** — wraps a list of
  OpenAI Agents SDK agents as a governed MeshFlow `Team` (any pattern). Mirrors
  `team_from_autogen` / `team_from_crewai` to give full adapter parity.

- **`mesh_tool_to_openai_function(tool)`** — converts a MeshFlow `Tool` to an
  OpenAI function-calling schema dict compatible with Chat Completions and
  Assistants API `tools` parameters. Handles `str/int/float/bool/list/dict`
  annotations and marks only required params as `required`.

#### Fixed

- `team_from_autogen` returned `GroupChatManager` instead of `Team`; corrected
  to return `Team` and updated stale `test_integration_fixes.py` assertions.

- `mesh_tool_to_openai_function` now uses `typing.get_type_hints()` (not just
  `param.annotation`) to resolve annotations correctly under Python 3.14
  deferred evaluation (PEP 649).

---

---

## [0.15.1] — 2026-05-23

### Sprint 15 — Eval CI regression, AgentPool, Plugin system

**776 tests passing (14 skipped = live API tests).**

#### Added

- **`EvalBaseline`** (`meshflow/eval/baseline.py`) — golden-set snapshot of an
  `EvalResult`. `from_result(result)`, `save(path)`, `load(path)`, `to_dict()`.
  Serialised as plain JSON for version control and artefact storage.

- **`BaselineDiff`** — regression diff between two baselines. Tracks regressions
  (PASS→FAIL), improvements (FAIL→PASS), per-scenario score deltas, new/removed
  scenarios, and pass-rate delta. `diff.has_regressions` is the CI gate.
  `diff.report()` produces a human-readable table.

- **`EvalBaseline` + `BaselineDiff` exported** from `meshflow` top-level.

- **`meshflow eval --save-baseline <path>`** — save the current run result as a
  golden baseline JSON after evaluation.

- **`meshflow eval --compare-baseline <path> --fail-on-regression`** — compare
  the current run against a saved baseline and exit 1 on any regression. Enables
  golden-set regression testing in CI without an LLM.

- **`meshflow eval-diff <baseline_a> <baseline_b>`** — standalone diff command
  comparing two baseline JSON files. Supports `--fail-on-regression`.

- **`meshflow eval --save-to-ledger`** — persist an `EvalResult` in the ledger
  as a checkpoint entry (`eval:<suite>:<timestamp>`).

- **`ReplayLedger.save_eval_result(result)`** — stores a result in the ledger;
  returns the storage key.

- **`ReplayLedger.list_eval_results(suite_name=None)`** — retrieves stored eval
  results, optionally filtered by suite name.

- **`AgentPool`** (`meshflow/agents/pool.py`) — a governed, bounded pool of
  agents driven by an `asyncio.Queue`. `submit(task)` dispatches to the next
  free agent; `map(tasks)` fans out and collects results in order. Accumulates
  `total_cost_usd` and `total_tokens` across the pool. Context-manager
  (`async with pool`) handles start/stop. Raises on empty agents or zero
  concurrency.

- **`PoolStats`** — snapshot of pool counters: active workers, queue depth,
  submitted/completed/failed, cost, tokens, uptime. `to_dict()` for JSON.

- **`register_pool` / `deregister_pool`** — global pool registry so the server
  can expose stats without tight coupling.

- **`GET /pool/status`** — returns `{"pools": [PoolStats.to_dict(), ...]}` for
  all registered pools.

- **Dashboard "Pool" page** — live view of registered pools: metrics cards per
  pool (active workers, queued, completed, failed, cost, tokens, concurrency,
  uptime).

- **`AgentPool`, `PoolStats`, `register_pool`, `deregister_pool`** exported from
  `meshflow` top-level.

- **`meshflow/plugins.py`** — entry-point–based plugin registry.
  `discover_plugins(group=None)` discovers installed packages that declare
  `meshflow.agents`, `meshflow.tools`, `meshflow.compliance`, or
  `meshflow.ledger` entry points. `load_plugin(name, group)` loads the object.
  `verify_plugin(name, group)` returns `(ok, message)` without raising.
  `list_plugins_table()` for tabular display.

- **`PluginInfo`** dataclass — name, group, ep_group, module, dist_name,
  version, description, loaded, error. `to_dict()`.

- **`meshflow plugins list [--group]`** — lists all installed plugins in a
  table.

- **`meshflow plugins verify <name> [--group]`** — loads and validates a plugin;
  exits 1 on failure.

- **`meshflow plugins info <name>`** — shows full metadata + load-check result.

- **`PluginInfo`, `discover_plugins`, `load_plugin`, `verify_plugin`** exported
  from `meshflow` top-level.

#### Fixed

- Bumped `__version__` to `0.15.0`; updated three stale `== "0.14.0"` version
  assertions in test files.

- Added `[[tool.mypy.overrides]] module = ["dashboard.*"]` to suppress
  `disallow_untyped_decorators` errors from `@st.cache_data` (Streamlit has no
  type stubs — pre-existing in all dashboard functions).

---

## [0.14.0] — 2026-05-22

### Release readiness — Sprint 13

**644 tests passing (14 skipped = live API tests).** All gap-plan items resolved.

#### Added

- **`meshflow bench` CLI command** — runs the full performance benchmark suite without
  an API key. Concurrency sweep (10/100/1000), provider microbench, ledger write
  throughput, and hash-chain validation speed. `--quick` flag for CI smoke-check.
  `--output results.json` for machine-readable results.

- **Real multi-tenant isolation** (`ReplayLedger`) — `write()` now injects the
  ledger's `tenant_id` into the SQLite row for non-default tenants. `list_runs()`
  filters by `tenant_id` so each tenant sees only their own runs. Previously
  `tenant_id='default'` was written for every row regardless of `ReplayLedger(tenant_id=...)`.

#### Fixed

- **`ReplayLedger.write()` tenant isolation** — non-default tenant ledger instances
  now correctly scope their rows so `delete_tenant()` and `list_runs()` work as
  documented.

---

## [0.13.0] — 2026-05-22

### Sprint 12 — Comprehensive test coverage

**88 new tests across 5 subsystems** — previously implemented but untested.

#### Added

- **Built-in tool library tests** (`tests/test_builtin_tools.py`, 28 tests) — calculator,
  datetime_now, json_query, shell blocklist, web_search, web_fetch, python_repl,
  http_request, read_file/write_file, global_registry coverage.

- **Provider extension tests** (`tests/test_providers.py`, 16 tests) — GeminiProvider,
  BedrockProvider, AzureOpenAIProvider complete() paths and provider_for() factory.

- **HITL notification tests** (`tests/test_hitl_notifications.py`, 11 tests) —
  HITLNotifier webhook dispatch, HMAC-SHA256 signatures, approve/reject URL injection,
  network error handling; HITLTimeoutWatcher auto-reject/approve/escalate paths.

- **RAG pipeline tests** (`tests/test_rag_pipeline.py`, 14 tests) — NumpyCosineIndex
  add/search/top_k, TFIDFEmbeddings async embed/determinism/semantic quality,
  DocumentStore ingest+retrieve, fixed/sentence chunking, metadata, RAGNode MeshNode wrapping.

- **GDPR + multi-tenancy tests** (`tests/test_gdpr_multitenancy.py`, 19 tests) —
  delete_run, anonymize_run, delete_tenant, tenant isolation (shared SQLite DB), schema
  migration ordering.

#### Fixed

- `NumpyCosineIndex` and `TFIDFEmbeddings` test constructors corrected (zero-arg, async embed API).
- HITL sync test converted to `@pytest.mark.asyncio`.
- GDPR tests use `write(StepRecord(...))` — the correct ledger API.

---

## [0.11.0–0.12.0] — 2026-05-22

### Sprints 11–12 — SwarmTRM embeddings + EventProjector

#### Added

- **Real SwarmTRM embeddings** (`meshflow/swarm/embeddings.py`) — three-tier fallback:
  `SentenceTransformerEmbedder` (`all-MiniLM-L6-v2`) → `NumpyBowEmbedder` (random
  projection, seeded) → `CharNgramEmbedder` (zero-dep, hash-based). `get_embedder(dim)`
  factory is `lru_cache`'d. `embed_text(text, dim)` convenience function.
  `SwarmTRM._input_embedding()` and `_role_vector()` now use real embeddings; falls
  back to hash-seeded noise only on exception.

- **EventProjector** (`meshflow/core/projections.py`) — four projections over the
  `MeshEvent` stream:
  - `AuditTrailProjection` — per-run ordered timeline + `to_dict(run_id)`.
  - `NodeLatencyProjection` — STEP_START/STEP_COMPLETE pairs; `query()`, `slowest(n)`.
  - `PolicyViolationProjection` — captures BLOCKED/PAUSED/HITL_REQUIRED; `violation_count()`.
  - `WorkflowSummaryProjection` — per-run rollup (`WorkflowSummary` dataclass).
  - `EventProjector` — coordinates all four; `report(run_id)` → full dict.

- **GroupChat + GroupChatManager** (`meshflow/agents/conversation.py`) — AutoGen-style
  multi-agent conversations. `round_robin`, `random`, `auto`, `custom` speaker strategies.
  Keyword and callable termination conditions. `GroupChatManager.stream()` yields
  `ChatMessage` objects. 18 tests in `tests/test_agentic_platform.py`.

- **DurableWorkflowExecutor** (`meshflow/core/durable.py`) — SQLite + in-memory
  checkpoint/resume. `_wrap_node()` skips completed nodes on replay.

- **GovernedToolRegistry** (`meshflow/agents/tool_registry.py`) — `ToolPermission`
  tiers (READ_ONLY → DATABASE_WRITE → CODE_EXEC → EXTERNAL_API), async/sync dispatch,
  full `AuditEntry` log.

---

## [0.10.0] — 2026-05-22

### Added — MeshFlow as an MCP Server

- **`MCPServer`** (`meshflow/mcp/server.py`) — MeshFlow now speaks MCP as a server,
  not just a client. Claude Desktop, Cursor, VS Code Copilot, and any MCP-capable host
  can connect and invoke governed workflows as tools.
  - Full JSON-RPC 2.0 dispatch: `initialize`, `tools/list`, `tools/call`, `resources/list`,
    `prompts/list`, `ping`.
  - Built-in tools: `meshflow_run`, `meshflow_approve_hitl`, `meshflow_reject_hitl`,
    `meshflow_get_trace`, `meshflow_list_runs`.
  - `register_agent(agent)` — any `Agent` becomes an MCP tool automatically.
  - `register_team(team)` — any `Team` becomes an MCP tool automatically.
  - `register_workflow(wf)` — any `WorkflowDefinition` becomes an MCP tool.
  - Every tool call returns a **governance receipt**: run_id, cost, tokens, HITL status.
  - `mcp_from_config("meshflow.yaml")` — builds a fully configured MCP server from YAML.

- **HTTP+SSE transport** (`/mcp` endpoint on the aiohttp server):
  - `GET /mcp` — discovery endpoint (server info, capabilities, full tool list).
  - `POST /mcp` — JSON-RPC 2.0 endpoint (Claude Desktop remote connection).
  - `GET /mcp/sse` — SSE stream for server→client notifications.
  - Full auth (`Authorization: Bearer` / `X-API-Key`) and CORS support.
  - `204 No Content` for MCP notifications (no `id` field).

- **stdio transport** (`meshflow mcp-stdio` CLI command):
  - `meshflow mcp-stdio` — starts a governed MCP stdio server for Claude Desktop local mode.
  - `meshflow mcp-stdio --config meshflow.yaml` — loads agents/teams from YAML.
  - `meshflow mcp-stdio --print-config` — prints the exact `claude_desktop_config.json`
    snippet to add to Claude Desktop, including the correct executable path.

- **`meshflow.MCPServer`**, **`MCPToolEntry`**, **`mcp_from_config`** exported from
  the top-level `meshflow` package.

### Claude Desktop integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "meshflow",
      "args": ["mcp-stdio", "--policy", "standard"],
      "env": {"ANTHROPIC_API_KEY": "sk-ant-..."}
    }
  }
}
```

Or generate this snippet automatically: `meshflow mcp-stdio --print-config`

### Updated in 0.10.0

- Server version string updated to `0.10.0` in `/health` and MCP `serverInfo`.
- `MCPServer`, `MCPToolEntry`, `mcp_from_config` added to `meshflow.__all__`.
- Version bumped `0.9.0 → 0.10.0`.

---

## [0.9.0] — 2026-05-22

### Added — Golden Standard Sprint

- **Typed state channels** — `StateGraph` with reducer-aware `Channel` descriptors.
  `Annotated[list[str], add]` accumulates across parallel branches; `last`, `first`,
  `max_reducer`, `min_reducer` built-in reducers. `compile()` returns a `CompiledGraph`
  with streaming (`stream()` async generator) and parallel fan-out execution.
  Parity with LangGraph `StateGraph` + MeshFlow governance layer on top.

- **Pre-built agent library** — 21 drop-in specialist agents in `meshflow.agents`:
  `ResearchAgent`, `CoderAgent`, `ReviewerAgent`, `AnalystAgent`, `WriterAgent`,
  `CriticAgent`, `PlannerAgent`, `SummarizerAgent`, `ExtractorAgent`, `ClassifierAgent`,
  `ValidatorAgent`, `TranslatorAgent`, `SQLAgent`, `APIAgent`, `AuditorAgent`,
  `ReporterAgent`, `DebugAgent`, `TeacherAgent`, `NegotiatorAgent`, `OrchestratorAgent`,
  `GuardianAgent`. All accept `policy=`, `model=`, `tools=`.

- **GroupChat** — AutoGen-style multi-agent conversational orchestration.
  `GroupChat(agents, max_turns, speaker_selection)` with `round_robin`, `random`,
  `auto` (LLM-driven), `custom` (callback) speaker strategies.
  `GroupChatManager.stream()` yields `ChatMessage` objects in real time.
  `ConversationResult.transcript()` returns full formatted dialogue.
  Callable or keyword-string termination conditions.

- **Declarative YAML config** — `meshflow.load("meshflow.yaml")` builds a complete
  governed multi-agent system from a single file. Supports `agents`, `team`,
  `workflow` (graph), and `groupchat` sections. Environment variable expansion with
  `${VAR}`. `meshflow.loads(yaml_string)` for in-process use.

- **Agent evaluation framework** — `EvalSuite`, `EvalScenario`, `run_eval()`.
  Scenarios support `expected_contains`, `expected_not_contains`, `min_confidence`,
  `max_tokens`, `eval_fn` (built-in: `valid_json`, `check_runnable_python`, `non_empty`,
  `no_hallucination_markers`; or inline Python expression). `--fail-under` threshold for
  CI gating. `meshflow eval evals.yaml --tags smoke --fail-under 0.9`.

- **LangChain tool bridge** — `meshflow.integrations.langchain`:
  `lc_tool(lc_tool_obj)` wraps any LangChain `BaseTool` as a MeshFlow `Tool`.
  `lc_tools([...])` wraps a list. `mesh_tool_to_lc(tool)` converts the other way.
  `agent_from_lc(lc_agent)` wraps an `AgentExecutor` or LCEL chain as a `MeshFlow Agent`.

- **`meshflow eval` CLI command** — `meshflow eval evals.yaml [--agent path.py]
  [--tags smoke] [--concurrency 4] [--fail-under 0.9]`. Auto-loads a `ResearchAgent`
  if `--agent` is omitted.

### Updated

- **`meshflow/__init__.py`** — exports `StateGraph`, `END`, `START`, `add`, `last`,
  `first`, `Channel`, `GroupChat`, `GroupChatManager`, `ConversationResult`,
  `MeshFlowConfig`, `load`, `loads`, `EvalSuite`, `EvalScenario`, `EvalResult`,
  `ScenarioResult`, `run_eval`, and the `agents` namespace module.
- **Version** — bumped `0.8.0 → 0.9.0`.
- **Description** — updated to "the golden standard of multi-agent orchestration."
- **Deprecation fix** — replaced `asyncio.iscoroutinefunction()` with
  `inspect.iscoroutinefunction()` throughout (deprecated in Python 3.16).

### Test Coverage

- 36 new tests in `tests/test_golden_standard.py` covering all six new feature areas.
- Full suite: **265/265 passing**.

---

## [0.8.0] — 2026-05-22

### Added — Critical gaps closed

- **Token-level streaming** — `AnthropicProvider` and `OpenAICompatibleProvider` now
  implement `stream_complete()` yielding `TokenChunk` objects. The HTTP server streams
  NDJSON over `aiohttp.StreamResponse`.
- **API key authentication** — `Authorization: Bearer` and `X-API-Key` header support.
  Keys loaded from `MESHFLOW_API_KEYS` env var (comma-separated). Server rewritten from
  `BaseHTTPRequestHandler` to fully async `aiohttp`.
- **Graph cycles / loop edges** — `WorkflowDefinition.add_loop_edge(src, dst, condition,
  max_iterations)`. `MaxIterationsError` raised as safety cap. Powers the new
  `"reflective"` team pattern.
- **Output compression + schema migrations** — ledger entries >10 KB are gzip+base64
  compressed transparently. `_MIGRATIONS` registry applied on startup for both SQLite
  and PostgreSQL.

### Added — High priority

- **Vector memory** — `TFIDFEmbeddings` (zero-dep, in-process TF-IDF) and
  `NumpyCosineIndex` (cosine similarity). `MEM1Store` gains semantic `retrieve_relevant()`.
  Vocabulary frozen after ingestion to guarantee consistent vector dimensions.
- **HITL webhooks + timeout** — `HITLNotifier` POSTs HMAC-SHA256 signed payloads.
  `HITLTimeoutWatcher` auto-approves/rejects/escalates after configurable timeout.
- **Rich tool schemas** — `_ann_to_json_schema()` handles `Annotated`, `Optional`,
  `Literal`, `list[X]`, Pydantic `BaseModel`. Parallel tool dispatch via `asyncio.gather`.
- **Schema migrations** — versioned migration registry; SQLite wraps in `try/except`,
  Postgres uses `ADD COLUMN IF NOT EXISTS`.

### Added — Medium priority

- **RAG pipeline** — `DocumentStore` (chunk → embed → index), `RAGNode(MeshNode)`,
  `RAGPipeline` (synchronous façade for scripts/tests), `Evidence` + `RAGResult` types.
- **Multi-tenancy** — `ReplayLedger(tenant_id=...)` scopes all queries. `delete_run()`,
  `delete_tenant()`, `anonymize_run()` for GDPR right-to-erasure.
- **Trace viewer + Prometheus metrics** — `meshflow trace <run-id>` rich terminal table
  with chain validation. `MetricsCollector` singleton; `/metrics` endpoint emits
  Prometheus text format.
- **Additional providers** — `GeminiProvider`, `BedrockProvider`, `AzureOpenAIProvider`.
  `provider_for(name, **kwargs)` factory.
- **Pre-built tool library** — 10 tools: `web_search`, `web_fetch`, `python_repl`,
  `read_file`, `write_file`, `shell` (with blocklist), `json_query`, `http_request`,
  `datetime_now`, `calculator` (AST-based safe eval).
- **Deployment** — `Dockerfile` (multi-stage, `python:3.11-slim`), `docker-compose.yml`
  (SQLite + PostgreSQL profiles), `k8s/deployment.yaml` (Deployment + Service + PVC + HPA).

### Added — Low priority / DX

- **TypeScript SDK** — `@meshflow/client`: `MeshFlowClient` with `run()`, `stream()`
  (async generator), `getTrace()`, `listRuns()`, HITL approve/reject. `package.json` +
  `tsconfig.json` with `tsup` dual CJS/ESM build.
- **Python client SDK** — `meshflow.client.MeshFlowClient` (async) + `_SyncClient`
  wrapper. Exported from `meshflow` top-level.
- **SOC 2 / HIPAA / GDPR compliance docs** — `docs/compliance/`: `SOC2_CONTROLS_MAPPING.md`
  (CC1–CC9 + A1/C1/P), `HIPAA_GUIDE.md`, `GDPR_GUIDE.md`, `SECURITY.md`.
- **PHI scrubber** — `PHIScrubber` covers all 18 HIPAA Safe Harbor categories. Activated
  via `Policy.scrub_phi=True` or `mode="hipaa"`.
- **CLI improvements** — `meshflow trace`, `meshflow runs`, `meshflow dev`, `meshflow serve`
  with `--api-key`, `--ledger`, `--tls-cert`, `--tls-key`.
- **Streamlit dashboard** — `dashboard/app.py`: Overview, Runs (trace inspector + hash
  viewer), HITL Queue, Metrics, Submit Task. `make dashboard` to launch.
- **Benchmarks** — `benchmarks/bench_core.py`: concurrency sweep (10/100/1000), provider
  microbench (155k calls/s), ledger writes (69k/s), chain validation (116 steps/ms).
- **Live integration tests** — `tests/test_live.py` (14 tests, gated behind
  `ANTHROPIC_API_KEY`). `make test-live`.
- **conftest.py** — `in_memory_ledger`, `shared_ledger`, `dev_policy`, `regulated_policy`,
  `make_step_record` fixtures. Session-scoped `live_server_url` + `live_client`.
- **Policy-mode examples** — `examples/hipaa_phi_pipeline.py`,
  `examples/regulated_financial_review.py`, `examples/legal_critical_nda_review.py`.

### Changed

- **`pyproject.toml`** — mandatory deps trimmed to 6 (`anthropic`, `aiohttp`, `httpx`,
  `aiosqlite`, `pyyaml`, `rich`). Heavy deps moved to named extras: `meshflow[openai]`,
  `meshflow[gemini]`, `meshflow[bedrock]`, `meshflow[rag]`, `meshflow[postgres]`,
  `meshflow[s3]`, `meshflow[dashboard]`, `meshflow[otel]`, `meshflow[full]`.
- **Ledger** — `StepRecord` gains `timestamp` (required), `prev_hash` (default `""`),
  `metadata` (default `{}`). Output stored compressed when >10 KB.
- **Server** — replaced `BaseHTTPRequestHandler` + `HTTPServer` with `aiohttp` app.
  Added `/metrics`, `/hitl/pending`, `/hitl/{id}/approve`, `/hitl/{id}/reject` routes.
- **`pytest` markers** — `live` and `slow` markers registered in `pyproject.toml` and
  `conftest.py`. No more marker warnings.
- **Version** — bumped `0.7.0 → 0.8.0`.

### Fixed

- TF-IDF embedding vocabulary frozen after corpus ingestion — prevents dimension mismatch
  between stored document vectors and query vectors.
- `RAGPipeline` now batch-ingests all documents on first `retrieve()` call (lazy build)
  rather than per-`add_document()`, ensuring consistent vocabulary.
- `PostgresLedgerBackend` schema-migrations query no longer fails when the fake test
  connection returns step rows for unrecognised SQL.

---

## [0.7.0] — 2026-05-01

- Universal `MeshNode` + `StepRuntime` kernel
- `WorkflowDefinition` with fan-out/fan-in parallel execution
- Conditional edge routing with transitive skip propagation
- Durable human approval checkpoints
- Pluggable ledger backends (SQLite, PostgreSQL, S3 archive)
- 33 integration tests

---

## [0.1.0–0.6.x] — 2025

Initial development: cross-framework execution, governance layers, DID identity,
SHA-256 audit chain, DascGate policy engine, HITL, collusion detection,
uncertainty scoring, environmental optimizer, cross-run learner, MCP gateway.
