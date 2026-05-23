# Changelog

All notable changes to MeshFlow are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
