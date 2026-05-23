# MeshFlow Gap Remediation Plan

> Every gap below is grounded in the actual source code.  
> Priority order: **Critical → High → Medium → Low**.  
> Each sprint builds on the one before it — do not start Sprint N+1 until Sprint N tests pass.

---

## CRITICAL — Sprint 1 (Weeks 1–2)
*Blockers that prevent production deployment entirely.*

---

### C-1  Token-Level Streaming

**Problem:** `runtime/server.py` emits one `MeshEvent` JSON line per completed node.  
Users building chat UIs get a blank screen until the entire agent finishes, then a wall of text.  
LangGraph's `astream_events` delivers per-token deltas. This is a hard blocker for consumer products.

**What exists:** `POST /stream` → NDJSON of `MeshEvent` objects (step-level only).  
`AnthropicProvider.complete()` awaits the full response — no streaming SDK call.

**What to build:**

1. **`AnthropicProvider.stream_complete()`** — use `anthropic.AsyncAnthropic.messages.stream()` context manager.  
   Yield `TokenChunk(text, agent_id, step_id)` objects as they arrive from the SDK.

2. **`OpenAICompatibleProvider.stream_complete()`** — use `openai.AsyncOpenAI.chat.completions.create(stream=True)`.  
   Iterate `ChatCompletionChunk` deltas, yield same `TokenChunk` type.

3. **`MeshEvent` — add `TOKEN_DELTA` variant** in `core/schemas.py`:
   ```python
   class EventKind(str, Enum):
       TOKEN_DELTA = "token_delta"   # new
       STEP_START  = "step_start"
       STEP_END    = "step_end"
       ...
   ```

4. **`WorkflowDefinition._run_node_streaming()`** — call `stream_complete()`, emit `TOKEN_DELTA` events  
   through the existing `MeshEvent` async queue that `POST /stream` already reads from.

5. **`LLMProvider` Protocol** — add `stream_complete()` to the protocol in `agents/base.py`  
   so all providers are required to implement it.

6. **Tests:** `tests/test_streaming.py` — mock provider, assert TOKEN_DELTA events arrive before STEP_END.

**Files:** `meshflow/agents/base.py`, `meshflow/core/schemas.py`, `meshflow/core/workflow.py`, `meshflow/runtime/server.py`

---

### C-2  Server Authentication

**Problem:** `runtime/server.py` is a raw `BaseHTTPRequestHandler` with no authentication.  
Any HTTP request to any endpoint succeeds. Deploying as a service exposes full agent execution to the network.

**What exists:** Zero auth. No middleware. No CORS headers. Single-threaded blocking server.

**What to build:**

1. **Replace `BaseHTTPRequestHandler` with `aiohttp`** (or `uvicorn` + `starlette`):  
   Add `aiohttp` (or `starlette>=0.36`) to `pyproject.toml` dependencies.  
   Rewrite `server.py` as an async ASGI app — this also fixes the blocking single-thread problem.

2. **API Key middleware:**
   ```python
   # meshflow/runtime/auth.py
   class APIKeyMiddleware:
       def __init__(self, app, keys: set[str]) -> None: ...
       async def __call__(self, scope, receive, send) -> None:
           # Check Authorization: Bearer <key> or X-API-Key header
           # Return 401 JSON on failure
   ```
   Keys loaded from `MESHFLOW_API_KEYS` env var (comma-separated) or a key file.

3. **CORS middleware** — configurable `MESHFLOW_CORS_ORIGINS` env var.

4. **`meshflow serve --api-key <key>` CLI flag** — generate a random key on first run and print it.

5. **`GET /health` remains unauthenticated** — standard pattern for load-balancer probes.

6. **Tests:** `tests/test_server_auth.py` — assert 401 on missing key, 200 on valid key.

**Files:** `meshflow/runtime/server.py` (rewrite), `meshflow/runtime/auth.py` (new), `pyproject.toml`, `meshflow/cli/main.py`

---

### C-3  Graph Cycles (Reflection and Retry Loops)

**Problem:** `WorkflowDefinition` is a strict DAG — `_topological_sort()` in `workflow.py` raises on any back-edge.  
This means no reflection loops (generate → critique → refine), no retry-until-done, no planning loops.  
These are the most common agentic patterns. LangGraph was built specifically to support cycles.

**What exists:** DAG scheduling with conditional edges and fan-out/fan-in. No cycle detection bypass.

**What to build:**

1. **`WorkflowDefinition.add_loop_edge(src, dst, condition_fn, max_iterations=10)`** — a back-edge that:
   - Is excluded from topological sort (treated as a "loop back" marker, not a DAG edge).
   - After `src` completes, if `condition_fn(output, context)` is True, re-queues `dst` for execution.
   - Tracks iteration count per `(src, dst)` pair; raises `MaxIterationsError` at the limit.

2. **`_LoopEdge` dataclass** in `core/workflow.py`:
   ```python
   @dataclass
   class _LoopEdge:
       src: str
       dst: str
       condition: Callable[[NodeOutput, dict], bool]
       max_iterations: int = 10
       _count: int = field(default=0, init=False)
   ```

3. **Execution engine change** — after each node completes, check `_loop_edges` for `src == node.id`.  
   If condition passes and count < max, insert `dst` back into the ready queue.

4. **Policy integration** — `max_steps` in `Policy` already caps total steps; loop iterations count against it.

5. **`Team` pattern: add `"reflective"` pattern** — sequential with a back-edge from the last node to  
   the first, used for critique-and-refine workflows.

6. **Tests:** `tests/test_loops.py` — generate-critique-refine loop that terminates after 3 iterations.

**Files:** `meshflow/core/workflow.py`, `meshflow/agents/team.py`, `meshflow/core/schemas.py`

---

### C-4  Output Truncation in Ledger

**Problem:** `runtime.py` line 367: `output_content=output.content[:2_000]`.  
For a document review agent processing a 50-page contract, the full LLM output is silently discarded.  
The audit ledger — MeshFlow's core differentiator — is recording incomplete evidence.

**What exists:** Hard-coded 2,000-char slice on `StepRecord.output_content`.

**What to build:**

1. **Policy field `max_output_chars: int = 0`** — `0` means unlimited. Add to `Policy` dataclass and  
   `policy_for_mode()`. Default to `0` (store full output).

2. **Ledger compression option** — store `output_content` as gzip-compressed base64 when > 10 KB:
   ```python
   # meshflow/core/ledger.py
   def _compress(text: str) -> str:
       import gzip, base64
       return base64.b64encode(gzip.compress(text.encode())).decode()
   ```
   Add `output_compressed BOOLEAN DEFAULT FALSE` column to both SQLite and Postgres schemas.

3. **`StepRecord`** — replace fixed slice with `output[:policy.max_output_chars] if policy.max_output_chars else output`.

4. **Schema migration** — add `output_compressed` column via `ALTER TABLE IF NOT EXISTS ... ADD COLUMN` on startup  
   (safe to run on existing databases since it has a default).

5. **`ReplayLedger.get_output(step_id)`** — decompress transparently on read.

6. **Tests:** assert full 5,000-char output is stored and retrievable without truncation.

**Files:** `meshflow/core/runtime.py`, `meshflow/core/ledger.py`, `meshflow/core/schemas.py`

---

## HIGH — Sprint 2 (Weeks 3–4)
*Significant competitive gaps that block enterprise sales and serious production workloads.*

---

### H-1  Vector Memory (Replace Keyword Search)

**Problem:** `mem1.py` line 196: `retrieve_relevant()` does `any(kw in entry["content"] for kw in keywords)`.  
This is substring matching. It fails for paraphrase, synonyms, and any retrieval beyond exact keyword overlap.  
CrewAI ships ChromaDB vector search. LangGraph has `InMemoryStore` with pluggable vector backends.

**What exists:** `ObservationPurifier`, `MemoryConsolidator`, `MEM1Store` — all keyword-based.

**What to build:**

1. **`VectorIndex` abstraction** in `meshflow/intelligence/mem1.py`:
   ```python
   class VectorIndex(Protocol):
       def add(self, key: str, text: str, embedding: list[float]) -> None: ...
       def search(self, embedding: list[float], top_k: int) -> list[tuple[str, float]]: ...
   ```

2. **`NumpyCosineIndex`** — default in-process implementation using `numpy` (already a dep).  
   Stores embeddings as a numpy matrix; cosine similarity via `np.dot`.  
   No external dependency required for the default path.

3. **`ChromaDBIndex`** — optional implementation gated on `chromadb` being installed.  
   Raises `ImportError` with install hint if not available.

4. **Embedding provider** — add `EmbeddingProvider` Protocol:
   ```python
   class EmbeddingProvider(Protocol):
       async def embed(self, texts: list[str]) -> list[list[float]]: ...
   ```
   Implement `AnthropicEmbeddings` (via `voyage-3` model) and `OpenAIEmbeddings` (via `text-embedding-3-small`).  
   Fallback: `TFIDFEmbeddings` (scipy, already a dep) — zero external API cost.

5. **`MEM1Store.add()`** — compute embedding on write, store in `VectorIndex`.

6. **`MEM1Store.retrieve_relevant()`** — embed query, search index, return top-k by cosine similarity.  
   Keep keyword fallback when no embedding provider is configured.

7. **`Mesh.__init__()` / `StepRuntime`** — accept optional `embedding_provider` kwarg and pass through to MEM1.

8. **Tests:** `tests/test_vector_memory.py` — add 20 entries with varied content, assert semantic retrieval  
   returns the correct entry for a paraphrased query (using TFIDFEmbeddings for zero-cost testing).

**Files:** `meshflow/intelligence/mem1.py`, `meshflow/core/mesh.py`, `pyproject.toml` (add `chromadb` optional dep)

---

### H-2  HITL Webhook, Notification, and Timeout

**Problem:** When a run pauses for human approval, MeshFlow sets `paused_for_human=True` and writes a checkpoint.  
There is no webhook, no timeout, no notification system — paused runs stay paused forever.  
A legal buyer's workflow requires: "Notify approver → wait up to 24h → auto-escalate or reject."

**What exists:** `workflow.resume(run_id, approved)` works if you poll the ledger. No push mechanism.

**What to build:**

1. **`HITLWebhook` config in `HumanInLoopConfig`:**
   ```python
   @dataclass
   class HumanInLoopConfig:
       enabled: bool = False
       tier_threshold: RiskTier = RiskTier.HIGH
       webhook_url: str = ""           # new
       timeout_seconds: int = 86400    # new — 24h default
       on_timeout: str = "reject"      # new — "reject" | "approve" | "escalate"
   ```

2. **`HITLNotifier`** in `meshflow/core/hitl.py` (new file):
   - `async notify(run_id, node_id, context) -> None` — POST JSON to `webhook_url` with  
     `{run_id, node_id, approve_url, reject_url, context, expires_at}`.
   - Includes HMAC-SHA256 signature in `X-MeshFlow-Signature` header for webhook verification.

3. **`HITLTimeoutWatcher`** — background asyncio task that polls the ledger for paused runs  
   older than `timeout_seconds` and calls `workflow.resume(run_id, approved=on_timeout=="approve")`.

4. **Approve/reject HTTP endpoints** on the server:
   ```
   POST /hitl/{run_id}/approve   {decision: "approve" | "reject", reviewer_id: str, notes: str}
   POST /hitl/{run_id}/reject
   GET  /hitl/pending            → list of paused run_ids with age and context
   ```

5. **Ledger** — add `reviewer_id TEXT`, `review_notes TEXT`, `review_timestamp TEXT` columns  
   to `workflow_checkpoints` table (via safe `ALTER TABLE ADD COLUMN IF NOT EXISTS`).

6. **Tests:** `tests/test_hitl_webhook.py` — mock httpx, assert webhook fires on pause,  
   timeout watcher calls resume after `timeout_seconds`.

**Files:** `meshflow/core/hitl.py` (new), `meshflow/core/schemas.py`, `meshflow/core/ledger.py`,  
`meshflow/runtime/server.py`, `meshflow/core/workflow.py`

---

### H-3  Rich Tool Schemas (Pydantic + Annotated)

**Problem:** `_build_tool_schema()` in `agents/base.py` uses `inspect.signature` with a basic type map.  
It cannot represent `Optional[List[str]]`, `Annotated[str, "description"]`, nested Pydantic models,  
or union types. Real-world tools break this schema builder.

**What exists:** `_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}`.

**What to build:**

1. **Full `_build_tool_schema(tool: Tool) -> dict`** rewrite:
   - If `tool.input_schema` is set (a `dict`), use it directly — escape hatch for complex tools.
   - If `tool.fn` has a Pydantic `BaseModel` as its first parameter, call `.model_json_schema()`.
   - Otherwise: walk `inspect.signature`, handle `Optional[X]` → `{"anyOf": [X_schema, {"type": "null"}]}`,  
     `list[X]` → `{"type": "array", "items": X_schema}`, `dict[str, X]` → `{"type": "object"}`,  
     `Literal["a","b"]` → `{"enum": ["a","b"]}`, `Annotated[X, desc]` → extract string metadata as description.

2. **`Tool` dataclass update** — add `input_schema: dict | None = None` field so users can  
   pass a raw JSON Schema when automatic inference is insufficient.

3. **Parallel tool calls** — when `AnthropicProvider` receives multiple `tool_use` blocks in one response,  
   dispatch all of them concurrently with `asyncio.gather()` instead of the current sequential loop.  
   Collect all `tool_result` blocks, then send one follow-up message with all results.

4. **Tests:** `tests/test_tool_schema.py` — assert correct JSON Schema for Optional, List, Pydantic model,  
   Annotated, and Literal parameter types.

**Files:** `meshflow/agents/base.py`

---

### H-4  Schema Migrations for the Ledger

**Problem:** `ledger.py` uses `CREATE TABLE IF NOT EXISTS` only. If a new column is added (as happened twice  
in the last sprint with `prev_hash`, `entry_hash`, `output_compressed`), existing databases silently  
lose data for those fields — the column does not exist, inserts fail or use defaults invisibly.

**What exists:** Two schemas (SQLite, Postgres), both in string literals. No migration tracking.

**What to build:**

1. **`schema_migrations` table:**
   ```sql
   CREATE TABLE IF NOT EXISTS schema_migrations (
       version INTEGER PRIMARY KEY,
       applied_at TEXT NOT NULL
   );
   ```

2. **`MIGRATIONS: list[tuple[int, str]]`** — ordered list of `(version, sql)` tuples in `ledger.py`.  
   Each entry is one `ALTER TABLE` statement. Example:
   ```python
   MIGRATIONS = [
       (1, "ALTER TABLE step_records ADD COLUMN IF NOT EXISTS prev_hash TEXT DEFAULT ''"),
       (2, "ALTER TABLE step_records ADD COLUMN IF NOT EXISTS entry_hash TEXT DEFAULT ''"),
       (3, "ALTER TABLE step_records ADD COLUMN IF NOT EXISTS output_compressed BOOLEAN DEFAULT FALSE"),
       (4, "ALTER TABLE workflow_checkpoints ADD COLUMN IF NOT EXISTS reviewer_id TEXT DEFAULT ''"),
       ...
   ]
   ```

3. **`ReplayLedger._run_migrations()`** — on every `connect()`, read max applied version,  
   run any pending migrations in order, write each new version to `schema_migrations`.  
   Idempotent — safe to run on every startup.

4. **Postgres support** — Postgres uses `ADD COLUMN IF NOT EXISTS` (supported since PG9.6).  
   SQLite does not support `IF NOT EXISTS` on `ALTER TABLE` — wrap in try/except `OperationalError`.

5. **Tests:** `tests/test_ledger_migrations.py` — start with schema version 0 (no extra columns),  
   run migrations, assert all columns exist and old rows are readable.

**Files:** `meshflow/core/ledger.py`

---

## MEDIUM — Sprint 3 (Weeks 5–7)
*Important for competitive positioning, enterprise readiness, and developer experience.*

---

### M-1  Semantic RAG Pipeline

**Problem:** `meshflow/intelligence/rag.py` exists but has no embedding index.  
Document retrieval is missing the core component — there is no way to query documents by semantic similarity.

**What to build:**

1. **`DocumentStore`** in `meshflow/intelligence/rag.py`:
   - `async ingest(docs: list[str], metadata: list[dict]) -> None` — chunk, embed, index.
   - `async retrieve(query: str, top_k: int = 5) -> list[RetrievedChunk]`.
   - Uses the same `VectorIndex` abstraction built in H-1.

2. **Chunking strategies** — `fixed_size(chunk_size=512, overlap=64)` and  
   `sentence_boundary()` (split on `.` / `!` / `?` boundaries).

3. **`RAGNode`** — a `MeshNode` subclass that calls `DocumentStore.retrieve()` and  
   prepends results to the node's task prompt. Plugs directly into `WorkflowDefinition`.

4. **`Mesh.add_knowledge_base(docs)`** convenience method — ingests on the fly before running.

5. **Tests:** `tests/test_rag.py` — ingest 10 short documents, assert correct document is  
   retrieved for a paraphrased query.

**Files:** `meshflow/intelligence/rag.py`, `meshflow/core/mesh.py`, `meshflow/core/node.py`

---

### M-2  Multi-Tenancy (Namespace Isolation)

**Problem:** The ledger has no tenant concept. All runs share the same database tables.  
One tenant can read another's audit records if they share a database connection.

**What to build:**

1. **`tenant_id TEXT NOT NULL DEFAULT 'default'`** column added via migration to:
   - `step_records`, `workflow_checkpoints`, `schema_migrations`.

2. **`ReplayLedger(tenant_id: str = "default")`** — all queries filter by `tenant_id`.  
   `verify_chain(run_id)` scoped to tenant.

3. **Server: `X-Tenant-ID` header** — extracted by auth middleware, passed to `Mesh` as `tenant_id`.  
   `MESHFLOW_DEFAULT_TENANT` env var sets the default.

4. **`Mesh(tenant_id=...)` constructor kwarg** — threaded through to `StepRuntime` → `ReplayLedger`.

5. **API key → tenant mapping** — `auth.py` `APIKeyMiddleware` accepts a `key_to_tenant: dict[str, str]`  
   map (loaded from env or config file) so each API key is bound to a tenant namespace.

6. **Tests:** `tests/test_multitenancy.py` — two tenants write runs, assert neither can read the other's records.

**Files:** `meshflow/core/ledger.py`, `meshflow/runtime/server.py`, `meshflow/runtime/auth.py`,  
`meshflow/core/mesh.py`, `meshflow/core/runtime.py`

---

### M-3  Structured Trace Viewer (Local Debug UI)

**Problem:** MeshFlow emits OTEL spans but there is no UI to consume them locally.  
`verify_chain()` proves tamper-evidence but does not help debug why an agent produced bad output.  
LangSmith shows the exact prompt, response, token counts, and latency waterfall.

**What to build:**

1. **`GET /traces/{run_id}`** — return full run trace as JSON: all `StepRecord`s for that `run_id`  
   in order, including `input_task`, `output_content` (decompressed), `verdict`, `uncertainty`,  
   `cost_usd`, `tokens_used`, `duration_ms`, `blocked`, `block_reason`.

2. **`GET /traces/{run_id}/steps/{step_id}/replay`** — return the full input/output pair  
   for a single step. Useful for debugging one node in a 20-step run.

3. **`meshflow trace <run_id>` CLI command** — pretty-print the trace using `rich` (already a dep):
   - Table: step, node, verdict, tokens, cost, duration, uncertainty.
   - Expandable output per step (press Enter to see full output).

4. **`meshflow trace --export <run_id> > trace.json`** — pipe-friendly JSON export.

5. **OTEL exporter config** — `MESHFLOW_OTLP_ENDPOINT` env var wires to the existing  
   `opentelemetry-exporter-otlp` dep (already installed). Add config to `meshflow serve`.

6. **Tests:** `tests/test_trace_api.py` — run a 3-node workflow, fetch trace, assert all steps present.

**Files:** `meshflow/runtime/server.py`, `meshflow/cli/main.py`, `meshflow/core/ledger.py`

---

### M-4  Extended Model Provider Support

**Problem:** MeshFlow only has `AnthropicProvider` and `OpenAICompatibleProvider`.  
Google Gemini, AWS Bedrock, Azure OpenAI, Cohere, and Mistral each have client-specific APIs  
that the OpenAI shim does not fully cover (auth, request format, response shape differ).

**What to build:**

1. **`GeminiProvider`** in `agents/base.py` or `agents/providers.py` (new file):
   - Uses `google-generativeai` (optional dep, gated on import).
   - Implements `LLMProvider.complete()` and `stream_complete()`.

2. **`BedrockProvider`** — uses `boto3` (already an optional dep in `pyproject.toml`).  
   Supports Claude 3/4 on Bedrock via `bedrock-runtime` client.

3. **`AzureOpenAIProvider`** — subclass of `OpenAICompatibleProvider` with Azure endpoint  
   and API version handling. Requires only `openai` (already a dep, supports Azure).

4. **`provider_for(name: str, **kwargs) -> LLMProvider`** factory function:
   ```python
   PROVIDERS = {"anthropic": AnthropicProvider, "openai": OpenAICompatibleProvider,
                "gemini": GeminiProvider, "bedrock": BedrockProvider, "azure": AzureOpenAIProvider}
   ```

5. **`Agent(provider="gemini", model="gemini-2.0-flash")` shorthand** — `Agent` builder resolves  
   provider name string to provider instance via the factory.

6. **Tests:** `tests/test_providers.py` — mock each provider's client, assert `complete()` returns  
   a `NodeOutput` with correct shape.

**Files:** `meshflow/agents/base.py` (or new `meshflow/agents/providers.py`), `meshflow/agents/builder.py`,  
`pyproject.toml` (add optional deps)

---

### M-5  Pre-Built Tool Library

**Problem:** MeshFlow has zero pre-built tools. LangChain has 500+. Every tool is hand-written Python.  
The `meshflow/tools/registry.py` exists but is empty scaffolding.

**What to build (10 essential tools, covers 80% of use cases):**

1. **`web_search(query: str) -> str`** — uses `httpx` + DuckDuckGo Instant Answer API (free, no key).
2. **`web_fetch(url: str) -> str`** — fetches URL, strips HTML via regex, returns plain text.  
   Respects `robots.txt`. Rate-limited to 2 req/s.
3. **`python_repl(code: str) -> str`** — runs code in a `subprocess` sandbox with 5s timeout.  
   Returns stdout + stderr. No `eval()` — subprocess isolation only.
4. **`read_file(path: str) -> str`** — reads a local file; rejects paths outside `MESHFLOW_WORKSPACE_DIR`.
5. **`write_file(path: str, content: str) -> str`** — writes a file; same path restriction.
6. **`shell(command: str) -> str`** — runs shell command in subprocess, 10s timeout.  
   Blocked commands list: `rm -rf`, `sudo`, `curl | sh`, etc. — allowlist by default.
7. **`json_query(data: str, jq_path: str) -> str`** — pure-Python jq-like path query (no `jq` binary).
8. **`http_request(method: str, url: str, body: str = "") -> str`** — generic HTTP tool.
9. **`datetime_now() -> str`** — returns ISO timestamp (solves LLM date blindness).
10. **`calculator(expression: str) -> str`** — uses Python `ast.literal_eval` + operator eval (safe, no `eval()`).

All tools registered in `meshflow/tools/registry.py` via `ToolRegistry.register(name, fn, description)`.  
`Agent(tools=["web_search", "python_repl"])` resolves names from the registry.

**Files:** `meshflow/tools/registry.py`, `meshflow/tools/builtins.py` (new), `meshflow/agents/builder.py`

---

### M-6  Docker and Deployment Artifacts

**Problem:** No `Dockerfile`, no `docker-compose.yml`, no Kubernetes manifests.  
"Deploy to production" currently means "run `python -m meshflow.runtime.server` and hope."

**What to build:**

1. **`Dockerfile`** — multi-stage build:
   ```dockerfile
   FROM python:3.11-slim AS builder
   COPY pyproject.toml .
   RUN pip install --no-cache-dir .
   FROM python:3.11-slim
   COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
   COPY meshflow/ /app/meshflow/
   CMD ["meshflow", "serve", "--port", "8000"]
   ```

2. **`docker-compose.yml`** — meshflow + postgres + optional OTEL collector (Jaeger):
   ```yaml
   services:
     meshflow:
       build: .
       ports: ["8000:8000"]
       environment:
         MESHFLOW_API_KEYS: ${MESHFLOW_API_KEYS}
         MESHFLOW_DB_URL: postgresql://mesh:mesh@postgres/mesh
     postgres:
       image: postgres:16-alpine
   ```

3. **`k8s/`** directory — `Deployment`, `Service`, `ConfigMap`, `Secret` manifests.  
   Horizontal pod autoscaler config for the stateless server layer.

4. **`meshflow/runtime/health.py`** — structured health check that returns:
   ```json
   {"ok": true, "db": "connected", "version": "0.7.0", "uptime_s": 1234}
   ```

5. **`Makefile` targets**: `make docker-build`, `make docker-run`, `make k8s-apply`.

**Files:** `Dockerfile` (new), `docker-compose.yml` (new), `k8s/` (new dir), `meshflow/runtime/health.py` (new)

---

## LOW — Sprint 4 (Weeks 8–10)
*Compliance positioning, developer experience, and ecosystem polish.*

---

### L-1  GDPR Right-to-Erasure in the Ledger

**Problem:** There is no way to delete a specific tenant's data from the ledger.  
A GDPR Subject Access Request or Right-to-Erasure requires deleting all records tied to an identity.

**What to build:**

1. **`ReplayLedger.delete_run(run_id: str, tenant_id: str) -> int`** — deletes all `step_records`  
   and `workflow_checkpoints` for the given run. Returns number of rows deleted.

2. **`ReplayLedger.delete_tenant(tenant_id: str) -> int`** — deletes all records for a tenant.

3. **`ReplayLedger.anonymize_run(run_id: str)`** — replaces `input_task` and `output_content`  
   with `[REDACTED]` while preserving the structural ledger record (for audit purposes).

4. **`POST /admin/runs/{run_id}/delete`** and `POST /admin/tenants/{tenant_id}/delete`** server endpoints  
   — gated behind an `admin` role API key (separate from standard keys).

5. **`meshflow admin delete-run <run_id>` CLI command**.

6. **Tests:** `tests/test_gdpr.py` — write run, delete it, assert `list_runs()` returns empty.

**Files:** `meshflow/core/ledger.py`, `meshflow/runtime/server.py`, `meshflow/cli/main.py`

---

### L-2  TypeScript Client SDK

**Problem:** The server speaks JSON over HTTP, but there is no SDK.  
JS/TS users must write raw `fetch()` calls. LangGraph has official Python and JS SDKs.

**What to build:**

1. **`sdks/typescript/`** directory — minimal TypeScript SDK:
   ```typescript
   export class MeshFlowClient {
     constructor(baseUrl: string, apiKey: string) {}
     async run(task: string, policy?: PolicyConfig): Promise<RunResult> {}
     stream(task: string, policy?: PolicyConfig): AsyncIterable<MeshEvent> {}
     async getTrace(runId: string): Promise<Trace> {}
     async approveHITL(runId: string, notes?: string): Promise<void> {}
     async rejectHITL(runId: string, notes?: string): Promise<void> {}
   }
   ```

2. **Published types** matching `MeshEvent`, `RunResult`, `StepRecord`, `PolicyConfig` Python dataclasses.

3. **`package.json`** with `"name": "@meshflow/client"`, ESM + CJS dual build via `tsup`.

4. **`sdks/typescript/README.md`** — 5-line quickstart example.

5. **CI: `npm run build && npm run test`** — added to GitHub Actions.

**Files:** `sdks/typescript/` (new directory)

---

### L-3  SOC2 and Compliance Documentation

**Problem:** The governance kernel is technically sound (hash chain, DID revocation, policy engine)  
but a legal/compliance buyer's first question after the demo is: "Are you SOC2 certified?"  
The code cannot answer this — it requires an audit program, not a code change.

**What to build:**

1. **`docs/compliance/SOC2_CONTROLS_MAPPING.md`** — map MeshFlow features to SOC2 Trust Service Criteria:
   - CC6.1 (Logical Access) → `AgentIdentityProvider`, `APIKeyMiddleware`
   - CC6.6 (Transmission Encryption) → TLS required on `meshflow serve` (add `--tls-cert` flag)
   - CC7.2 (Monitoring) → `MeshFlowTracer` OTEL spans + `Guardian` behavioral monitoring
   - CC9.2 (Vendor Risk) → DID-based agent identity, capability scoping

2. **`docs/compliance/HIPAA_GUIDE.md`** — deployment guide for healthcare:
   - PHI handling: set `max_output_chars=0` (full logging disabled), use `anonymize_run()` for test data.
   - Network: VPC-only deployment, no external model API calls without customer approval.
   - BAA: MeshFlow is infrastructure; the customer's model provider is the BAA counterparty.

3. **`docs/compliance/GDPR_GUIDE.md`** — data residency, right-to-erasure via `delete_run()`, DPA template.

4. **TLS support** — `meshflow serve --tls-cert cert.pem --tls-key key.pem` using Python's `ssl.SSLContext`.

5. **`docs/compliance/SECURITY.md`** — responsible disclosure policy, CVE process, key rotation guide.

**Files:** `docs/compliance/` (new dir), `meshflow/runtime/server.py` (TLS flag), `meshflow/cli/main.py`

---

### L-4  HIPAA PHI Scrubbing

**Problem:** If MeshFlow processes documents with PHI (patient health information), the ledger stores  
`input_task` and `output_content` containing PHI. This makes the database a PHI store requiring  
HIPAA-compliant infrastructure even in test environments.

**What to build:**

1. **`PHIScrubber`** in `meshflow/security/phi_scrubber.py`:
   - Regex patterns for SSN, DOB, phone, MRN, name (NER-lite using spaCy if available, regex fallback).
   - `scrub(text: str) -> str` — replaces detected PHI with `[PHI-REDACTED]`.

2. **`Policy.scrub_phi: bool = False`** — when True, `StepRuntime` scrubs `input_task` and  
   `output_content` before writing to ledger. The agent still sees original content; only the ledger is scrubbed.

3. **`policy_for_mode("hipaa")` preset** — `scrub_phi=True`, `max_output_chars=0` (store only scrubbed version),  
   `enable_guardian=True`, `enable_collusion_audit=True`.

4. **Tests:** `tests/test_phi_scrubber.py` — assert SSN, phone, and DOB patterns are scrubbed from ledger output.

**Files:** `meshflow/security/phi_scrubber.py` (new), `meshflow/core/schemas.py`, `meshflow/core/runtime.py`

---

### L-5  Observability: Prometheus Metrics Endpoint

**Problem:** OTEL traces require a collector. Most DevOps teams already have Prometheus + Grafana.  
A `/metrics` endpoint would let teams monitor MeshFlow without any additional infrastructure.

**What to build:**

1. **`GET /metrics`** — Prometheus text format endpoint (no `prometheus_client` dep needed;  
   write the text format directly: `# HELP ... \n # TYPE ... \n metric_name{label="v"} value`).

2. **Metrics to expose:**
   - `meshflow_runs_total{status="ok|blocked|paused"}` — counter
   - `meshflow_run_duration_seconds{quantile="0.5|0.95|0.99"}` — summary
   - `meshflow_tokens_total{provider="anthropic|openai"}` — counter
   - `meshflow_cost_usd_total` — counter
   - `meshflow_blocks_total{reason="guardian|dasc|budget|identity|circuit_breaker"}` — counter
   - `meshflow_hitl_pending` — gauge (paused runs awaiting approval)
   - `meshflow_uncertainty_score{agent_id="..."}` — gauge (last value per agent)

3. **`MetricsCollector`** singleton in `meshflow/observability/metrics.py` — in-memory counters/gauges  
   updated by `StepRuntime.run()` at the end of each step.

4. **Tests:** `tests/test_metrics.py` — run a workflow, fetch `/metrics`, assert counters are non-zero.

**Files:** `meshflow/observability/metrics.py` (new), `meshflow/runtime/server.py`, `meshflow/core/runtime.py`

---

### L-6  CLI Developer Experience

**Problem:** `meshflow run` exists but developer experience is minimal. No hot-reload, no interactive  
workflow builder, no trace viewer in the terminal. LangGraph has `langgraph dev` with Studio UI.

**What to build:**

1. **`meshflow dev`** — starts the server with `--reload` (watchdog on `meshflow/` directory;  
   restarts on file change). Prints a colored banner with the API URL and current version.

2. **`meshflow trace <run_id>`** — rich table of steps with color-coded verdicts  
   (green=commit, red=blocked, yellow=escalate). Expandable per-step output.

3. **`meshflow runs`** — list recent runs with status, cost, duration, and step count.  
   `--tenant` flag for multi-tenant filtering.

4. **`meshflow validate <workflow.yaml>`** — parse a YAML workflow definition and report  
   any missing nodes, invalid edges, or policy violations before running.

5. **YAML workflow format:**
   ```yaml
   name: my_workflow
   policy: standard
   nodes:
     - id: planner
       kind: native
       role: planner
     - id: executor
       kind: native
       role: executor
   edges:
     - from: planner
       to: executor
   ```

6. **`meshflow init --template legal|research|coding`** — scaffold a project with example workflow,  
   policy config, and `.env.example`.

**Files:** `meshflow/cli/main.py`, `meshflow/cli/scaffold.py`, `meshflow/core/workflow.py`

---

## Implementation Sequence and Dependency Map

```
Sprint 1 (Critical):
  C-1 Streaming ──────────────────────────── no deps
  C-2 Auth ───────────────────────────────── no deps
  C-3 Cycles ─────────────────────────────── no deps
  C-4 Output truncation ───────────────────── needs C-4 migration → feeds H-4

Sprint 2 (High):
  H-4 Migrations ─────────── must come first (all other ledger changes use it)
  H-1 Vector memory ──────── no deps
  H-2 HITL webhook ───────── needs C-2 (auth) + H-4 (migrations)
  H-3 Tool schemas ────────── no deps

Sprint 3 (Medium):
  M-1 RAG ───────────────── needs H-1 (vector index)
  M-2 Multi-tenancy ──────── needs H-4 (migrations) + C-2 (auth)
  M-3 Trace viewer ───────── needs H-4 (migrations) + C-2 (auth)
  M-4 Model providers ────── needs C-1 (streaming)
  M-5 Tool library ───────── needs H-3 (tool schemas)
  M-6 Docker ─────────────── needs C-2 (auth)

Sprint 4 (Low):
  L-1 GDPR ──────────────── needs M-2 (multi-tenancy) + H-4 (migrations)
  L-2 TypeScript SDK ─────── needs C-1 (streaming) + C-2 (auth)
  L-3 SOC2 docs ──────────── needs C-2 (auth) + M-2 (multi-tenancy)
  L-4 PHI scrubbing ──────── no deps
  L-5 Prometheus metrics ─── needs C-2 (auth) server
  L-6 CLI DX ─────────────── needs M-3 (trace viewer)
```

---

## Definition of Done (per gap)

A gap is closed when:
1. Feature works end-to-end in a manual test or example script.
2. At least 3 automated tests cover the happy path + one failure mode.
3. `mypy --strict` passes on all changed files.
4. `ruff check` passes.
5. The relevant section of `README.md` is updated.

---

## Total Work Estimate

| Sprint | Gaps | Engineer-weeks |
|--------|------|----------------|
| Sprint 1 — Critical | C-1, C-2, C-3, C-4 | 2 weeks |
| Sprint 2 — High | H-1, H-2, H-3, H-4 | 2 weeks |
| Sprint 3 — Medium | M-1, M-2, M-3, M-4, M-5, M-6 | 3 weeks |
| Sprint 4 — Low | L-1, L-2, L-3, L-4, L-5, L-6 | 2 weeks |
| **Total** | **16 gaps** | **~9 engineer-weeks** |

One senior engineer solo: ~10 weeks.  
Two engineers working in parallel on independent gaps: ~5–6 weeks.
