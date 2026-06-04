/**
 * MeshFlow TypeScript Client SDK  v0.19.0
 *
 * Covers all MeshFlow REST + SSE endpoints.
 *
 * Node ≥ 18 (native fetch).  Browser-compatible via the Fetch API.
 *
 * Quick start:
 *   import { MeshFlowClient } from "meshflow-sdk";
 *
 *   const client = new MeshFlowClient("http://localhost:8000", "my-api-key");
 *
 *   // Run a governed task
 *   const result = await client.run("Summarise the quarterly report");
 *
 *   // Stream token-by-token
 *   for await (const event of client.stream("Analyse this contract")) {
 *     if (event.kind === "token_delta") process.stdout.write(event.text ?? "");
 *   }
 *
 *   // Subscribe to live workflow events (SSE)
 *   for await (const event of client.liveEvents()) {
 *     console.log(event);
 *   }
 *
 *   // Verify an incoming webhook signature
 *   import { verifyWebhookSignature } from "meshflow-sdk";
 *   const ok = await verifyWebhookSignature(rawBody, signature, secret);
 */

// ── Error type ────────────────────────────────────────────────────────────────

export class MeshFlowError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: string,
  ) {
    super(message);
    this.name = "MeshFlowError";
  }
}

// ── Shared types ──────────────────────────────────────────────────────────────

export interface PolicyConfig {
  mode?: "dev" | "standard" | "regulated" | "legal-critical" | "hipaa";
  budget_usd?: number;
  budget_tokens?: number;
  timeout_s?: number;
  max_steps?: number;
  deterministic_gate?: boolean;
  enable_guardian?: boolean;
  enable_collusion_audit?: boolean;
  enable_uncertainty?: boolean;
  enable_environmental?: boolean;
  carbon_budget_g?: number;
}

export interface RunResult {
  run_id: string;
  status: "pending" | "running" | "paused" | "completed" | "failed" | "aborted";
  output: unknown;
  total_cost_usd: number;
  total_tokens: number;
  total_carbon_g: number;
  duration_s: number;
  ledger_entries: number;
  trace_id: string;
  checkpoints: string[];
  error: string | null;
  collusion_alerts: number;
}

export interface MeshEvent {
  kind: string;
  agent_id?: string;
  role?: string;
  step?: number;
  uncertainty?: number;
  cost_usd?: number;
  tokens?: number;
  blocked_by?: string;
  output?: string;
  text?: string;       // present when kind === "token_delta"
  step_id?: string;
  run_id?: string;
  error?: string;
  timestamp?: number;
  node_id?: string;
}

export interface StepRecord {
  step_id: string;
  run_id: string;
  node_id: string;
  node_kind: string;
  input_task: string;
  output_content: string;
  verdict: string;
  blocked: boolean;
  block_reason: string;
  uncertainty: number;
  cost_usd: number;
  tokens_used: number;
  carbon_gco2: number;
  duration_ms: number;
  timestamp: string;
  prev_hash: string;
  entry_hash: string;
}

export interface TraceSummary {
  steps: number;
  nodes: string[];
  total_cost_usd: number;
  total_tokens: number;
  total_carbon_gco2: number;
  blocked_steps: number;
  verdicts: string[];
  timestamps: { start: string; end: string };
}

export interface Trace {
  run_id: string;
  summary: TraceSummary;
  steps: StepRecord[];
}

export interface HITLDecision {
  reviewer_id?: string;
  notes?: string;
}

export interface PausedRun {
  run_id: string;
  paused_at: string;
}

export interface HealthResponse {
  ok: boolean;
  version: string;
  uptime_s: number;
  db: string;
}

export interface ProbeResponse {
  live?: boolean;
  ready?: boolean;
  uptime_s?: number;
  version?: string;
  reason?: string;
}

// ── Compliance types ──────────────────────────────────────────────────────────

export type ComplianceFramework = "hipaa" | "sox" | "gdpr" | "pci" | "nerc";

export interface ComplianceFinding {
  category: string;
  control_id: string;
  status: "pass" | "fail" | "warning" | "na";
  detail: string;
  evidence: string[];
}

export interface ComplianceSummary {
  total: number;
  passed: number;
  failed: number;
  warnings: number;
  na: number;
  pass_rate: number;
  overall_status: "compliant" | "non_compliant" | "partial";
}

export interface ComplianceReport {
  framework: string;
  framework_version: string;
  run_ids: string[];
  generated_at: string;
  total_steps: number;
  summary: ComplianceSummary;
  findings: ComplianceFinding[];
  metadata: Record<string, unknown>;
}

// ── Webhook types ─────────────────────────────────────────────────────────────

export type WebhookEvent =
  | "policy_violation"
  | "budget_exceeded"
  | "hitl_pending"
  | "run_failed"
  | "run_completed"
  | "collusion_alert"
  | "*";

export interface WebhookRegistration {
  id: string;
  url: string;
  events: WebhookEvent[];
  created_at: string;
  delivery_count: number;
  failure_count: number;
  last_delivery_at: string | null;
  last_error: string | null;
}

export interface WebhookStats {
  registered: number;
  total_deliveries: number;
  total_failures: number;
  history_size: number;
}

export interface DeliveryRecord {
  webhook_id: string;
  event_type: string;
  timestamp: string;
  success: boolean;
  status_code: number | null;
  error: string | null;
  attempt: number;
}

// ── SLA / Pool types ──────────────────────────────────────────────────────────

export interface SLASummary {
  node_id: string;
  count: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  min_ms: number;
  max_ms: number;
  mean_ms: number;
}

export interface RateLimiterBucket {
  key: string;
  tokens: number;
  capacity: number;
}

export interface PoolStats {
  pool_name: string;
  active_workers: number;
  queued: number;
  total_completed: number;
  total_failed: number;
  agent_count: number;
  concurrency: number;
  total_cost_usd: number;
  total_tokens: number;
  uptime_s: number;
  total_submitted: number;
}

// ── Eval / Plugin types ───────────────────────────────────────────────────────

export interface EvalResult {
  suite_name: string;
  pass_rate: number;
  weighted_score?: number;
  score?: number;
  total_scenarios: number;
  timestamp: string;
  scenarios: unknown[];
}

export interface Plugin {
  name: string;
  group: string;
  version: string;
  dist_name: string;
  description: string | null;
  ep_group?: string;
}

// ── OTEL config ───────────────────────────────────────────────────────────────

export interface OTELConfig {
  otlp_enabled: boolean;
  otlp_endpoint: string;
  otlp_protocol: string;
  otlp_error: string | null;
  w3c_traceparent: boolean;
  env_vars: Record<string, string>;
}

// ── Client ────────────────────────────────────────────────────────────────────

export class MeshFlowClient {
  private baseUrl: string;
  private apiKey: string;
  private defaultPolicy: PolicyConfig;

  constructor(
    baseUrl: string,
    apiKey: string = "",
    defaultPolicy: PolicyConfig = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
    this.defaultPolicy = defaultPolicy;
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
    if (this.apiKey) h["Authorization"] = `Bearer ${this.apiKey}`;
    return h;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: this.headers(),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new MeshFlowError(
        response.status,
        `MeshFlow API error ${response.status} at ${method} ${path}: ${text}`,
        text,
      );
    }
    return response.json() as Promise<T>;
  }

  // ── Health ─────────────────────────────────────────────────────────────────

  /** Server health. No auth required. */
  async health(): Promise<HealthResponse> {
    const r = await fetch(`${this.baseUrl}/health`);
    return r.json() as Promise<HealthResponse>;
  }

  /** Kubernetes liveness probe. Returns 200 while the process is alive. */
  async healthLive(): Promise<ProbeResponse> {
    const r = await fetch(`${this.baseUrl}/health/live`);
    return r.json() as Promise<ProbeResponse>;
  }

  /**
   * Kubernetes readiness probe.
   * Resolves normally (200) when ready, rejects with MeshFlowError (503)
   * during graceful shutdown or when the ledger is unreachable.
   */
  async healthReady(): Promise<ProbeResponse> {
    const r = await fetch(`${this.baseUrl}/health/ready`);
    if (!r.ok) {
      const text = await r.text();
      throw new MeshFlowError(r.status, `Not ready: ${text}`, text);
    }
    return r.json() as Promise<ProbeResponse>;
  }

  // ── Task execution ─────────────────────────────────────────────────────────

  /** Execute a task and wait for completion. */
  async run(
    task: string,
    policy?: PolicyConfig,
    context?: Record<string, unknown>,
  ): Promise<RunResult> {
    return this.request<RunResult>("POST", "/run", {
      task,
      policy: { ...this.defaultPolicy, ...policy },
      context,
    });
  }

  /**
   * Stream a task execution, yielding NDJSON events as they arrive.
   * Events with kind === "token_delta" carry per-token text.
   */
  async *stream(
    task: string,
    policy?: PolicyConfig,
    context?: Record<string, unknown>,
  ): AsyncIterable<MeshEvent> {
    const response = await fetch(`${this.baseUrl}/stream`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({
        task,
        policy: { ...this.defaultPolicy, ...policy },
        context,
      }),
    });

    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new MeshFlowError(response.status, `Stream error: ${text}`, text);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try { yield JSON.parse(trimmed) as MeshEvent; } catch { /* skip */ }
      }
    }
    if (buffer.trim()) {
      try { yield JSON.parse(buffer) as MeshEvent; } catch { /* ignore */ }
    }
  }

  /**
   * Subscribe to live workflow events via Server-Sent Events.
   * Optionally filter to a single run with runId.
   */
  async *liveEvents(runId?: string): AsyncIterable<MeshEvent> {
    const qs = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    const response = await fetch(`${this.baseUrl}/events${qs}`, {
      headers: this.headers({ Accept: "text/event-stream" }),
    });

    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new MeshFlowError(response.status, `SSE error: ${text}`, text);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const block of events) {
        const dataLine = block.split("\n").find((l) => l.startsWith("data:"));
        if (!dataLine) continue;
        const payload = dataLine.slice(5).trim();
        try {
          const parsed = JSON.parse(payload) as MeshEvent;
          if (!("ok" in parsed)) yield parsed; // skip handshake
        } catch { /* skip */ }
      }
    }
  }

  // ── Traces ─────────────────────────────────────────────────────────────────

  /** List all run IDs in the ledger. */
  async listRuns(): Promise<string[]> {
    const r = await this.request<{ runs: string[] }>("GET", "/traces");
    return r.runs;
  }

  /** Get a full run trace including all step records. */
  async getTrace(runId: string): Promise<Trace> {
    return this.request<Trace>("GET", `/traces/${encodeURIComponent(runId)}`);
  }

  /** Export a run's execution graph as Mermaid or DOT. */
  async getGraph(runId: string, format: "mermaid" | "dot" = "mermaid"): Promise<string> {
    const r = await fetch(
      `${this.baseUrl}/graph/${encodeURIComponent(runId)}?format=${format}`,
      { headers: this.headers() },
    );
    if (!r.ok) throw new MeshFlowError(r.status, `Graph error: ${await r.text()}`);
    return r.text();
  }

  /** Export the audit trail as CSV or JSON. Pass undefined runId for all runs. */
  async exportAudit(runId?: string, format: "json" | "csv" = "json"): Promise<string> {
    const qs = new URLSearchParams({ format });
    if (runId) qs.set("run_id", runId);
    const r = await fetch(`${this.baseUrl}/audit/export?${qs}`, { headers: this.headers() });
    if (!r.ok) throw new MeshFlowError(r.status, `Audit export error: ${await r.text()}`);
    return r.text();
  }

  // ── HITL ───────────────────────────────────────────────────────────────────

  /** List runs currently paused for human approval. */
  async listPendingHITL(): Promise<PausedRun[]> {
    const r = await this.request<{ paused_runs: PausedRun[] }>("GET", "/hitl/pending");
    return r.paused_runs;
  }

  /** Approve a paused run so it can continue. */
  async approveHITL(runId: string, decision: HITLDecision = {}): Promise<void> {
    await this.request<unknown>("POST", `/hitl/${encodeURIComponent(runId)}/approve`, decision);
  }

  /** Reject a paused run. */
  async rejectHITL(runId: string, decision: HITLDecision = {}): Promise<void> {
    await this.request<unknown>("POST", `/hitl/${encodeURIComponent(runId)}/reject`, decision);
  }

  // ── Compliance ─────────────────────────────────────────────────────────────

  /**
   * Generate a compliance report from ledger data.
   * @param framework - "hipaa" | "sox" | "gdpr" | "pci" | "nerc"
   * @param runId - scope to a specific run (omit for last 50 runs)
   */
  async complianceReport(
    framework: ComplianceFramework,
    runId?: string,
  ): Promise<ComplianceReport> {
    const qs = new URLSearchParams({ framework });
    if (runId) qs.set("run_id", runId);
    return this.request<ComplianceReport>("GET", `/compliance/report?${qs}`);
  }

  // ── Webhooks ───────────────────────────────────────────────────────────────

  /** List all registered webhooks and delivery stats. */
  async listWebhooks(): Promise<{ webhooks: WebhookRegistration[]; stats: WebhookStats }> {
    return this.request<{ webhooks: WebhookRegistration[]; stats: WebhookStats }>(
      "GET",
      "/webhooks",
    );
  }

  /** Register a new webhook endpoint. */
  async registerWebhook(
    url: string,
    events: WebhookEvent[] = ["*"],
    secret: string = "",
  ): Promise<WebhookRegistration> {
    return this.request<WebhookRegistration>("POST", "/webhooks", { url, events, secret });
  }

  /** Remove a registered webhook by ID. */
  async deleteWebhook(webhookId: string): Promise<{ deleted: string }> {
    return this.request<{ deleted: string }>(
      "DELETE",
      `/webhooks/${encodeURIComponent(webhookId)}`,
    );
  }

  /** Get delivery history for a specific webhook. */
  async getWebhookDeliveries(webhookId: string): Promise<DeliveryRecord[]> {
    const r = await this.request<{ deliveries: DeliveryRecord[] }>(
      "GET",
      `/webhooks/${encodeURIComponent(webhookId)}/deliveries`,
    );
    return r.deliveries;
  }

  // ── SLA & Rate limiting ────────────────────────────────────────────────────

  /** p50/p95/p99 latency per node. Pass nodeId to filter to one node. */
  async getSLA(nodeId?: string): Promise<SLASummary[]> {
    const qs = nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : "";
    const r = await this.request<{ sla: SLASummary | SLASummary[] }>("GET", `/sla${qs}`);
    const raw = r.sla;
    return Array.isArray(raw) ? raw : [raw];
  }

  /** Token-bucket rate limiter status per API key. */
  async getRateLimiterStatus(): Promise<RateLimiterBucket[]> {
    const r = await this.request<{ buckets: RateLimiterBucket[] }>(
      "GET",
      "/rate-limit/status",
    );
    return r.buckets;
  }

  // ── Agent pool ─────────────────────────────────────────────────────────────

  /** Stats for all registered AgentPool instances. */
  async getPoolStatus(): Promise<PoolStats[]> {
    const r = await this.request<{ pools: PoolStats[] }>("GET", "/pool/status");
    return r.pools;
  }

  // ── Evals ──────────────────────────────────────────────────────────────────

  /** List stored eval baseline results, optionally filtered by suite name. */
  async listEvalResults(suite?: string): Promise<EvalResult[]> {
    const qs = suite ? `?suite=${encodeURIComponent(suite)}` : "";
    const r = await this.request<{ eval_results: EvalResult[] }>("GET", `/eval-results${qs}`);
    return r.eval_results;
  }

  // ── Plugins ────────────────────────────────────────────────────────────────

  /** List installed MeshFlow plugins, optionally filtered by group. */
  async listPlugins(group?: string): Promise<Plugin[]> {
    const qs = group ? `?group=${encodeURIComponent(group)}` : "";
    const r = await this.request<{ plugins: Plugin[] }>("GET", `/plugins${qs}`);
    return r.plugins;
  }

  // ── OTEL ───────────────────────────────────────────────────────────────────

  /** Current OpenTelemetry / trace-context configuration. */
  async getOTELConfig(): Promise<OTELConfig> {
    return this.request<OTELConfig>("GET", "/otel/config");
  }

  // ── Metrics ────────────────────────────────────────────────────────────────

  /** Raw Prometheus metrics text. */
  async getMetrics(): Promise<string> {
    const r = await fetch(`${this.baseUrl}/metrics`, { headers: this.headers() });
    if (!r.ok) throw new MeshFlowError(r.status, "Metrics fetch failed");
    return r.text();
  }

  // ── Cloud ingest — internal helper ────────────────────────────────────────

  /** Headers for cloud ingest endpoints (x-meshflow-key, not Bearer). */
  private cloudHeaders(): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json", "Accept": "application/json" };
    if (this.apiKey) h["x-meshflow-key"] = this.apiKey;
    return h;
  }

  private async cloudPost<T>(path: string, body: unknown): Promise<T> {
    const r = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: this.cloudHeaders(),
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const text = await r.text();
      throw new MeshFlowError(r.status, `Cloud ingest error at POST ${path}: ${text}`, text);
    }
    return r.json() as Promise<T>;
  }

  private async cloudGet<T>(path: string): Promise<T> {
    const r = await fetch(`${this.baseUrl}${path}`, {
      method: "GET",
      headers: this.cloudHeaders(),
    });
    if (!r.ok) {
      if (r.status === 404) return null as T;
      const text = await r.text();
      throw new MeshFlowError(r.status, `Cloud ingest error at GET ${path}: ${text}`, text);
    }
    return r.json() as Promise<T>;
  }

  private async cloudDelete(path: string): Promise<void> {
    const r = await fetch(`${this.baseUrl}${path}`, {
      method: "DELETE",
      headers: this.cloudHeaders(),
    });
    if (!r.ok) {
      const text = await r.text();
      throw new MeshFlowError(r.status, `Cloud ingest error at DELETE ${path}: ${text}`, text);
    }
  }

  // ── Cloud ingest — runs, evals, MCP, workers ──────────────────────────────

  /** Send a completed run summary to /dashboard/runs. */
  async reportRun(payload: Record<string, unknown>): Promise<boolean> {
    const r = await this.cloudPost<{ ok?: boolean }>("/api/ingest/run", payload);
    return r.ok !== false;
  }

  /** Push one eval result to /dashboard/evals. */
  async reportEval(eval_: CloudEvalInput): Promise<boolean> {
    const r = await this.cloudPost<{ ok?: boolean }>("/api/ingest/eval", eval_);
    return r.ok !== false;
  }

  /** Record one MCP tool call to /dashboard/mcp. */
  async reportMcpCall(call: CloudMcpCallInput): Promise<boolean> {
    const r = await this.cloudPost<{ ok?: boolean }>("/api/ingest/mcp", call);
    return r.ok !== false;
  }

  /** Upsert a worker job status event to /dashboard/workers. */
  async reportWorkerJob(job: CloudWorkerJobInput): Promise<boolean> {
    const r = await this.cloudPost<{ ok?: boolean }>("/api/ingest/worker", job);
    return r.ok !== false;
  }

  // ── Cloud ingest — trace spans ─────────────────────────────────────────────

  /**
   * Send a batch of per-step trace spans to /dashboard/traces.
   *
   * @example
   * ```ts
   * await client.reportSpans([{
   *   run_id: "run-123", agent_name: "planner", span_type: "step",
   *   name: "plan", started_at: new Date().toISOString(), duration_ms: 420,
   *   input_tokens: 512, cost_usd: 0.0014,
   * }]);
   * ```
   */
  async reportSpans(spans: CloudSpanInput[]): Promise<number> {
    if (!spans.length) return 0;
    const r = await this.cloudPost<{ ok?: boolean; ingested?: number }>("/api/ingest/spans", { spans });
    return r.ingested ?? spans.length;
  }

  // ── Prompt Hub ─────────────────────────────────────────────────────────────

  /**
   * Fetch the active (or pinned) version of a prompt by slug.
   * Returns `null` when not found.
   *
   * @example
   * ```ts
   * const p = await client.promptGet("hipaa-intake-processor");
   * if (p) console.log(p.content);
   * ```
   */
  async promptGet(slug: string, version?: number): Promise<CloudPromptRecord | null> {
    let path = `/api/ingest/prompts?slug=${encodeURIComponent(slug)}`;
    if (version !== undefined) path += `&version=${version}`;
    return this.cloudGet<CloudPromptRecord | null>(path);
  }

  /** List all prompt slugs for the org. */
  async promptList(): Promise<CloudPromptSummary[]> {
    return this.cloudGet<CloudPromptSummary[]>("/api/ingest/prompts?list=1") ?? [];
  }

  /**
   * Push a new version of a prompt (creates the prompt if new).
   *
   * @example
   * ```ts
   * await client.promptPush("hipaa-intake-processor", "You are a HIPAA specialist…");
   * ```
   */
  async promptPush(
    slug: string,
    content: string,
    options: { name?: string; notes?: string; model?: string; temperature?: number } = {},
  ): Promise<CloudPromptRecord> {
    return this.cloudPost<CloudPromptRecord>("/api/ingest/prompts", { slug, content, ...options });
  }

  // ── Dataset Hub ────────────────────────────────────────────────────────────

  /** List all datasets for the org. */
  async datasetList(): Promise<CloudDatasetSummary[]> {
    return this.cloudGet<CloudDatasetSummary[]>("/api/ingest/datasets") ?? [];
  }

  /**
   * Fetch rows from a named dataset.
   * Returns `null` when the dataset is not found.
   */
  async datasetPull(
    name: string,
    options: { limit?: number; offset?: number } = {},
  ): Promise<CloudDatasetPullResponse | null> {
    const qs = new URLSearchParams({ name });
    if (options.limit  !== undefined) qs.set("limit",  String(options.limit));
    if (options.offset !== undefined) qs.set("offset", String(options.offset));
    return this.cloudGet<CloudDatasetPullResponse | null>(`/api/ingest/datasets?${qs}`);
  }

  /**
   * Append rows to a named dataset (creates it if new).
   *
   * @example
   * ```ts
   * await client.datasetPush("hipaa-qa-v1", [
   *   { input: "What is PHI?", expected_output: "Protected Health Information." },
   * ]);
   * ```
   */
  async datasetPush(
    name: string,
    rows: CloudDatasetRow[],
    description?: string,
  ): Promise<{ id: string; rows_added: number }> {
    return this.cloudPost("/api/ingest/datasets", { name, rows, description });
  }

  /** Delete a dataset and all its rows. */
  async datasetDelete(name: string): Promise<void> {
    await this.cloudDelete(`/api/ingest/datasets?name=${encodeURIComponent(name)}`);
  }

  // ── Agent Registry ─────────────────────────────────────────────────────────

  /** List all registered agent definitions. */
  async listAgents(): Promise<CloudAgentDefinition[]> {
    return this.cloudGet<CloudAgentDefinition[]>("/api/ingest/agents") ?? [];
  }

  /** Fetch one agent definition by slug. Returns `null` when not found. */
  async getAgent(slug: string): Promise<CloudAgentDefinition | null> {
    return this.cloudGet<CloudAgentDefinition | null>(
      `/api/ingest/agents?slug=${encodeURIComponent(slug)}`,
    );
  }

  /**
   * Upsert an agent definition in the cloud registry.
   *
   * @example
   * ```ts
   * await client.registerAgent("HIPAA Intake", "hipaa-intake", {
   *   role: "executor", model: "claude-sonnet-4-6", policy: "hipaa",
   * });
   * ```
   */
  async registerAgent(
    name: string,
    slug: string,
    options: {
      role?: string; model?: string; policy?: string;
      systemPrompt?: string; tags?: string; deployTarget?: string;
      version?: string; status?: string;
    } = {},
  ): Promise<CloudAgentDefinition> {
    return this.cloudPost("/api/ingest/agents", {
      name, slug,
      system_prompt: options.systemPrompt,
      deploy_target: options.deployTarget,
      ...options,
    });
  }

  /** Increment the run counter for a registered agent. */
  async recordAgentRun(slug: string, runCount = 1): Promise<boolean> {
    const r = await this.cloudPost<{ ok?: boolean }>(
      "/api/ingest/agents",
      { name: slug, slug, run_count: runCount },
    );
    return r.ok !== false;
  }
}

// ── Cloud ingest types ────────────────────────────────────────────────────────

export interface CloudSpanInput {
  run_id: string;
  agent_name: string;
  /** "llm_call" | "tool_call" | "guardrail" | "policy_check" | "step" */
  span_type: string;
  name: string;
  /** ISO-8601 timestamp */
  started_at: string;
  duration_ms: number;
  input_text?: string;
  output_text?: string;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  status?: string;
  error_msg?: string;
  metadata?: Record<string, unknown>;
}

export interface CloudEvalInput {
  run_id: string;
  suite?: string;
  scenario: string;
  metric?: string;
  score: number;
  passed: boolean;
  reasoning?: string;
  cost_usd?: number;
  latency_ms?: number;
}

export interface CloudMcpCallInput {
  server_name: string;
  tool_name: string;
  transport?: string;
  endpoint?: string;
  latency_ms?: number;
  success?: boolean;
  cost_usd?: number;
  tool_count?: number;
}

export interface CloudWorkerJobInput {
  job_id: string;
  workflow_name: string;
  status: string;
  retries?: number;
  max_retries?: number;
  duration_ms?: number;
  error_msg?: string;
  scheduled_for?: string;
}

export interface CloudPromptRecord {
  slug: string;
  name: string;
  description: string;
  version: number;
  content: string;
  model: string;
  temperature: number;
}

export interface CloudPromptSummary {
  slug: string;
  name: string;
  description: string;
  updatedAt: string;
}

export interface CloudDatasetRow {
  input: string;
  expected_output?: string;
  metadata?: Record<string, unknown>;
}

export interface CloudDatasetPullResponse {
  id: string;
  name: string;
  description: string;
  row_count: number;
  rows: Array<{ id: string; input: string; expected_output: string; metadata: Record<string, unknown> }>;
}

export interface CloudDatasetSummary {
  id: string;
  name: string;
  description: string;
  rowCount: number;
  updatedAt: string;
}

export interface CloudAgentDefinition {
  id: string;
  slug: string;
  name: string;
  description: string;
  role: string;
  model: string;
  policy: string;
  systemPrompt: string;
  tags: string;
  deployTarget: string;
  version: string;
  status: string;
  totalRuns: number;
}

// ── Webhook signature verification ───────────────────────────────────────────

/**
 * Verify an incoming MeshFlow webhook signature.
 *
 * The server signs the raw request body with HMAC-SHA256 using the webhook
 * secret.  The signature is sent in the X-MeshFlow-Signature header.
 *
 * Usage (Node.js):
 *   import { verifyWebhookSignature } from "meshflow-sdk";
 *   const rawBody = await request.text();
 *   const sig = request.headers.get("X-MeshFlow-Signature") ?? "";
 *   const valid = await verifyWebhookSignature(rawBody, sig, process.env.WEBHOOK_SECRET!);
 *   if (!valid) return new Response("Forbidden", { status: 403 });
 */
export async function verifyWebhookSignature(
  rawBody: string | Uint8Array | ArrayBuffer,
  signature: string,
  secret: string,
): Promise<boolean> {
  const enc = new TextEncoder();

  // Normalise to ArrayBuffer — required by crypto.subtle in strict TS 5.x
  const toArrayBuffer = (v: string | Uint8Array | ArrayBuffer): ArrayBuffer => {
    if (typeof v === "string") {
      const u = enc.encode(v);
      return u.buffer.slice(u.byteOffset, u.byteOffset + u.byteLength) as ArrayBuffer;
    }
    if (v instanceof ArrayBuffer) return v;
    return v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength) as ArrayBuffer;
  };

  const keyData = toArrayBuffer(enc.encode(secret));
  const bodyData = toArrayBuffer(rawBody);

  const key = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sigBuffer = await crypto.subtle.sign("HMAC", key, bodyData);
  const expected = Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  // Constant-time comparison
  if (expected.length !== signature.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return diff === 0;
}

// ── Convenience factory ───────────────────────────────────────────────────────

/**
 * Create a MeshFlowClient from environment variables.
 *
 *   MESHFLOW_SERVER   — server base URL (default: http://localhost:8000)
 *   MESHFLOW_API_KEY  — API key
 */
export function createClient(overrides: {
  baseUrl?: string;
  apiKey?: string;
  defaultPolicy?: PolicyConfig;
} = {}): MeshFlowClient {
  const baseUrl =
    overrides.baseUrl ??
    (typeof process !== "undefined"
      ? process.env["MESHFLOW_SERVER"] ?? "http://localhost:8000"
      : "http://localhost:8000");
  const apiKey =
    overrides.apiKey ??
    (typeof process !== "undefined" ? process.env["MESHFLOW_API_KEY"] ?? "" : "");
  return new MeshFlowClient(baseUrl, apiKey, overrides.defaultPolicy ?? {});
}

export default MeshFlowClient;

// ── Native Agent / Team (no MeshFlow server required) ─────────────────────────

export interface AgentConfig {
  name: string;
  role?: "planner" | "researcher" | "executor" | "critic" | "guardian";
  model?: string;
  systemPrompt?: string;
  /** Anthropic API key — falls back to ANTHROPIC_API_KEY env var */
  apiKey?: string;
  maxTokens?: number;
}

export interface AgentRunResult {
  output: string;
  inputTokens: number;
  outputTokens: number;
  model: string;
  stopReason: string;
}

// ── Internal shapes for the Anthropic /v1/messages response ──────────────────

interface AnthropicContentBlock {
  type: string;
  text?: string;
}

interface AnthropicUsage {
  input_tokens: number;
  output_tokens: number;
}

interface AnthropicMessagesResponse {
  content: AnthropicContentBlock[];
  usage: AnthropicUsage;
  stop_reason: string;
  model: string;
}

// SSE streaming shapes
interface AnthropicSSEDelta {
  type: string;
  text?: string;
}

interface AnthropicSSEEvent {
  type: string;
  delta?: AnthropicSSEDelta;
}

const ANTHROPIC_API_BASE = "https://api.anthropic.com/v1";
const ANTHROPIC_VERSION = "2023-06-01";
const DEFAULT_MODEL = "claude-3-5-sonnet-20241022";
const DEFAULT_MAX_TOKENS = 4096;

/**
 * MeshFlowAgent — A TypeScript-native agent that calls Claude directly via the
 * Anthropic API.  No running MeshFlow server is required.
 *
 * Quick start:
 *   const agent = new MeshFlowAgent({ name: "Analyst", role: "researcher" });
 *   const result = await agent.run("Summarise the quarterly report");
 *   console.log(result.output);
 *
 * The API key is resolved from (in order):
 *   1. `config.apiKey`
 *   2. `process.env.ANTHROPIC_API_KEY`
 */
export class MeshFlowAgent {
  readonly name: string;
  readonly role: string;
  readonly model: string;
  private readonly systemPrompt: string;
  private readonly apiKey: string;
  private readonly maxTokens: number;

  constructor(config: AgentConfig) {
    this.name = config.name;
    this.role = config.role ?? "executor";
    this.model = config.model ?? DEFAULT_MODEL;
    this.maxTokens = config.maxTokens ?? DEFAULT_MAX_TOKENS;
    this.systemPrompt =
      config.systemPrompt ??
      `You are ${this.name}, a ${this.role} agent. Complete the given task accurately and concisely.`;

    const key =
      config.apiKey ??
      (typeof process !== "undefined" ? process.env["ANTHROPIC_API_KEY"] ?? "" : "");

    if (!key) {
      throw new Error(
        "MeshFlowAgent: no Anthropic API key supplied. " +
          "Pass apiKey in AgentConfig or set ANTHROPIC_API_KEY env var.",
      );
    }
    this.apiKey = key;
  }

  /** Build the user message content, optionally prepending serialised context. */
  private buildUserContent(
    task: string,
    context?: Record<string, unknown>,
  ): string {
    if (!context || Object.keys(context).length === 0) return task;
    return `Context:\n${JSON.stringify(context, null, 2)}\n\nTask:\n${task}`;
  }

  /** Execute a task and return the full result. */
  async run(
    task: string,
    context?: Record<string, unknown>,
  ): Promise<AgentRunResult> {
    const response = await fetch(`${ANTHROPIC_API_BASE}/messages`, {
      method: "POST",
      headers: {
        "x-api-key": this.apiKey,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: this.model,
        max_tokens: this.maxTokens,
        system: this.systemPrompt,
        messages: [{ role: "user", content: this.buildUserContent(task, context) }],
      }),
    });

    if (!response.ok) {
      const text = await response.text();
      throw new MeshFlowError(
        response.status,
        `Anthropic API error ${response.status} for agent "${this.name}": ${text}`,
        text,
      );
    }

    const data = (await response.json()) as AnthropicMessagesResponse;

    const output = data.content
      .filter((b) => b.type === "text" && typeof b.text === "string")
      .map((b) => b.text as string)
      .join("");

    return {
      output,
      inputTokens: data.usage.input_tokens,
      outputTokens: data.usage.output_tokens,
      model: data.model,
      stopReason: data.stop_reason,
    };
  }

  /**
   * Stream a task execution, yielding text deltas as they arrive.
   *
   *   for await (const chunk of agent.stream("Write a poem")) {
   *     process.stdout.write(chunk);
   *   }
   */
  async *stream(
    task: string,
    context?: Record<string, unknown>,
  ): AsyncIterable<string> {
    const response = await fetch(`${ANTHROPIC_API_BASE}/messages`, {
      method: "POST",
      headers: {
        "x-api-key": this.apiKey,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: this.model,
        max_tokens: this.maxTokens,
        system: this.systemPrompt,
        stream: true,
        messages: [{ role: "user", content: this.buildUserContent(task, context) }],
      }),
    });

    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new MeshFlowError(
        response.status,
        `Anthropic streaming error for agent "${this.name}": ${text}`,
        text,
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by "\n\n"
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        // Each frame may contain multiple lines; find the "data:" line
        const dataLine = frame
          .split("\n")
          .find((l) => l.startsWith("data:"));
        if (!dataLine) continue;
        const payload = dataLine.slice(5).trim();
        if (payload === "[DONE]") return;
        try {
          const event = JSON.parse(payload) as AnthropicSSEEvent;
          if (
            event.type === "content_block_delta" &&
            event.delta?.type === "text_delta" &&
            typeof event.delta.text === "string"
          ) {
            yield event.delta.text;
          }
        } catch {
          // Malformed SSE line — skip
        }
      }
    }

    // Flush any remaining buffer
    if (buffer.trim() && !buffer.trim().startsWith("event:")) {
      const dataLine = buffer
        .split("\n")
        .find((l) => l.startsWith("data:"));
      if (dataLine) {
        const payload = dataLine.slice(5).trim();
        if (payload && payload !== "[DONE]") {
          try {
            const event = JSON.parse(payload) as AnthropicSSEEvent;
            if (
              event.type === "content_block_delta" &&
              event.delta?.type === "text_delta" &&
              typeof event.delta.text === "string"
            ) {
              yield event.delta.text;
            }
          } catch {
            // ignore
          }
        }
      }
    }
  }
}

// ── Team ──────────────────────────────────────────────────────────────────────

export type TeamPattern = "sequential" | "parallel" | "supervised";

export interface TeamConfig {
  name: string;
  agents: MeshFlowAgent[];
  pattern?: TeamPattern;
  /**
   * 0.0–1.0.  In sequential mode, after each agent step the runner parses
   * `"confidence": <number>` from the output.  If the value is >= this
   * threshold the team exits early without running remaining agents.
   */
  stopOnConfidence?: number;
}

export interface TeamRunResult {
  output: string;
  agentResults: AgentRunResult[];
  stoppedEarly: boolean;
}

const CONFIDENCE_RE = /"confidence"\s*:\s*([\d.]+)/;

/**
 * MeshFlowTeam — Runs multiple MeshFlowAgents together.
 *
 * Patterns:
 *   - `sequential` (default) — agents run one after another; each agent
 *     receives the previous agent's output as additional context.
 *   - `parallel` — all agents run concurrently against the same task; the
 *     final output is their results concatenated.
 *   - `supervised` — sequential with the first agent acting as supervisor;
 *     its output is used as final result without further processing.
 *
 * Quick start:
 *   const team = new MeshFlowTeam({
 *     name: "Research Team",
 *     agents: [researcher, analyst, writer],
 *     pattern: "sequential",
 *     stopOnConfidence: 0.9,
 *   });
 *   const result = await team.run("Produce a market analysis");
 */
export class MeshFlowTeam {
  private readonly name: string;
  private readonly agents: MeshFlowAgent[];
  private readonly pattern: TeamPattern;
  private readonly stopOnConfidence: number | undefined;

  constructor(config: TeamConfig) {
    if (config.agents.length === 0) {
      throw new Error("MeshFlowTeam: agents array must not be empty.");
    }
    this.name = config.name;
    this.agents = config.agents;
    this.pattern = config.pattern ?? "sequential";
    this.stopOnConfidence = config.stopOnConfidence;
  }

  async run(task: string): Promise<TeamRunResult> {
    switch (this.pattern) {
      case "parallel":
        return this._runParallel(task);
      case "supervised":
        return this._runSupervised(task);
      default:
        return this._runSequential(task);
    }
  }

  private async _runSequential(task: string): Promise<TeamRunResult> {
    const agentResults: AgentRunResult[] = [];
    let currentTask = task;
    let stoppedEarly = false;

    for (const agent of this.agents) {
      const result = await agent.run(currentTask);
      agentResults.push(result);

      // Check confidence-based early exit
      if (this.stopOnConfidence !== undefined) {
        const match = CONFIDENCE_RE.exec(result.output);
        if (match) {
          const confidence = parseFloat(match[1]);
          if (confidence >= this.stopOnConfidence) {
            stoppedEarly = true;
            break;
          }
        }
      }

      // Pass this agent's output as context to the next agent
      currentTask = result.output;
    }

    const lastResult = agentResults[agentResults.length - 1];
    return {
      output: lastResult?.output ?? "",
      agentResults,
      stoppedEarly,
    };
  }

  private async _runParallel(task: string): Promise<TeamRunResult> {
    const agentResults = await Promise.all(
      this.agents.map((agent) => agent.run(task)),
    );

    const output = agentResults
      .map((r, i) => `[${this.agents[i]?.name ?? `Agent ${i}`}]\n${r.output}`)
      .join("\n\n---\n\n");

    return { output, agentResults, stoppedEarly: false };
  }

  /** Supervised: only the first agent runs; remaining agents are skipped. */
  private async _runSupervised(task: string): Promise<TeamRunResult> {
    const supervisor = this.agents[0];
    if (!supervisor) {
      throw new Error("MeshFlowTeam (supervised): no supervisor agent defined.");
    }
    const result = await supervisor.run(task);
    return {
      output: result.output,
      agentResults: [result],
      stoppedEarly: false,
    };
  }
}

// ── Convenience factories ─────────────────────────────────────────────────────

/** Create a MeshFlowAgent from a config object. */
export function createAgent(config: AgentConfig): MeshFlowAgent {
  return new MeshFlowAgent(config);
}

/** Create a MeshFlowTeam from a config object. */
export function createTeam(config: TeamConfig): MeshFlowTeam {
  return new MeshFlowTeam(config);
}

// ── Wire-level tool call enforcement ─────────────────────────────────────────

/** A single tool call event intercepted before execution. */
export interface ToolCallEvent {
  toolName: string;
  args: Record<string, unknown>;
  callId: string;
  source: "llm" | "mcp" | "proxy";
  runId?: string;
}

/** Enforcement decision for a tool call. */
export interface ToolCallDecision {
  allowed: boolean;
  blockReason?: string;
  modifiedArgs?: Record<string, unknown>;
}

/** Implement this interface to enforce policy on tool calls. */
export interface ToolCallInterceptor {
  beforeCall(event: ToolCallEvent): Promise<ToolCallDecision>;
}

/** Allow-list interceptor — blocks any tool not in the list. */
export class AllowListInterceptor implements ToolCallInterceptor {
  private allowed: Set<string>;

  constructor(allowedTools: string[]) {
    this.allowed = new Set(allowedTools);
  }

  async beforeCall(event: ToolCallEvent): Promise<ToolCallDecision> {
    if (this.allowed.has(event.toolName)) {
      return { allowed: true };
    }
    return { allowed: false, blockReason: `tool '${event.toolName}' not in allow-list` };
  }
}

// OpenAI response types (minimal — works with full openai SDK response objects)
interface OAIToolCall {
  id?: string;
  function?: { name?: string; arguments?: string };
  [key: string]: unknown;
}
interface OAIChoice {
  message?: { tool_calls?: OAIToolCall[] | null; [key: string]: unknown };
  [key: string]: unknown;
}
interface OAIResponse {
  choices?: OAIChoice[];
  [key: string]: unknown;
}

interface BlockedCall {
  toolName: string;
  blockReason: string;
}

/**
 * MeshFlowProxy — drop-in wrapper for any OpenAI-compatible client.
 *
 * Intercepts tool calls in chat completions responses, evaluates them
 * against the interceptor, and removes blocked calls from the response.
 *
 * Works with the official `openai` npm package and any OpenAI-compatible client.
 *
 * ```typescript
 * import OpenAI from "openai";
 * import { MeshFlowProxy, AllowListInterceptor } from "meshflow-sdk";
 *
 * const client = new OpenAI();
 * const proxy = new MeshFlowProxy(client, {
 *   interceptor: new AllowListInterceptor(["search", "read_file"]),
 *   onBlock: (event) => console.log("Blocked:", event.toolName),
 * });
 *
 * const response = await proxy.chat.completions.create({
 *   model: "gpt-4o",
 *   messages: [{ role: "user", content: "Search for AI safety papers" }],
 *   tools: [...],
 * });
 * ```
 */
export class MeshFlowProxy {
  private _client: unknown;
  private _interceptor: ToolCallInterceptor | null;
  private _agentId: string;
  private _onBlock: ((event: ToolCallEvent) => void) | null;
  private _allowedCount = 0;
  private _blockedCalls: BlockedCall[] = [];

  readonly chat: {
    completions: {
      create: (params: Record<string, unknown>) => Promise<OAIResponse>;
    };
  };

  constructor(
    client: unknown,
    options: {
      interceptor?: ToolCallInterceptor;
      agentId?: string;
      onBlock?: (event: ToolCallEvent) => void;
    } = {}
  ) {
    this._client = client;
    this._interceptor = options.interceptor ?? null;
    this._agentId = options.agentId ?? "meshflow-proxy";
    this._onBlock = options.onBlock ?? null;

    this.chat = {
      completions: {
        create: async (params: Record<string, unknown>): Promise<OAIResponse> => {
          const rawClient = this._client as {
            chat: { completions: { create: (p: unknown) => Promise<OAIResponse> } };
          };
          const response = await rawClient.chat.completions.create(params);
          return this._enforce(response);
        },
      },
    };
  }

  private async _enforce(response: OAIResponse): Promise<OAIResponse> {
    if (!this._interceptor) return response;

    const choices = response.choices ?? [];
    for (const choice of choices) {
      const toolCalls = choice.message?.tool_calls;
      if (!toolCalls?.length) continue;

      const allowed: OAIToolCall[] = [];
      for (const tc of toolCalls) {
        const name = tc.function?.name ?? "unknown";
        let args: Record<string, unknown> = {};
        try {
          args = JSON.parse(tc.function?.arguments ?? "{}");
        } catch {
          args = { _raw: tc.function?.arguments };
        }

        const event: ToolCallEvent = {
          toolName: name,
          args,
          callId: String(tc.id ?? Math.random().toString(36).slice(2)),
          source: "proxy",
        };

        const decision = await this._interceptor.beforeCall(event);
        if (decision.allowed) {
          this._allowedCount++;
          allowed.push(tc);
        } else {
          this._blockedCalls.push({ toolName: name, blockReason: decision.blockReason ?? "" });
          this._onBlock?.(event);
        }
      }

      if (choice.message) {
        choice.message.tool_calls = allowed.length > 0 ? allowed : null;
      }
    }

    return response;
  }

  /** Returns allow/block counts. */
  stats(): { allowedToolCalls: number; blockedToolCalls: number } {
    return {
      allowedToolCalls: this._allowedCount,
      blockedToolCalls: this._blockedCalls.length,
    };
  }

  /** Returns all blocked tool call events. */
  blockedCalls(): BlockedCall[] {
    return [...this._blockedCalls];
  }

  /** Forward any other property to the underlying client. */
  [key: string]: unknown;
}

/** Return an OpenAI tool schema for calling MeshFlow. */
export function meshflowAsOpenAITool(options: {
  toolName?: string;
  description?: string;
  includePolicyParam?: boolean;
} = {}): Record<string, unknown> {
  const name = options.toolName ?? "meshflow_run";
  const desc = options.description ??
    "Run a governed multi-agent workflow through MeshFlow. " +
    "Every run gets: SHA-256 tamper-evident audit chain, hard cost cap, " +
    "HIPAA/SOX/GDPR/ISO 27001 compliance, Zero Trust agent identity, and crash recovery.";

  const properties: Record<string, unknown> = {
    task: { type: "string", description: "The task to execute through the governed agent pipeline." },
  };
  if (options.includePolicyParam !== false) {
    properties["policy"] = {
      type: "string",
      enum: ["standard", "strict", "hipaa", "sox", "gdpr", "iso27001", "dev", "sandbox"],
      description: "Governance policy mode. Default: standard.",
    };
  }

  return {
    type: "function",
    function: {
      name,
      description: desc,
      parameters: { type: "object", properties, required: ["task"] },
    },
  };
}
