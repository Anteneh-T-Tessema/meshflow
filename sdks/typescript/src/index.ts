/**
 * MeshFlow TypeScript Client SDK
 *
 * Usage:
 *   import { MeshFlowClient } from "@meshflow/client";
 *
 *   const client = new MeshFlowClient("http://localhost:8000", "my-api-key");
 *
 *   // Run a task
 *   const result = await client.run("Summarize the quarterly report");
 *
 *   // Stream token-by-token
 *   for await (const event of client.stream("Analyse this contract")) {
 *     if (event.kind === "token_delta") process.stdout.write(event.text ?? "");
 *   }
 *
 *   // Trace a past run
 *   const trace = await client.getTrace(result.run_id);
 *
 *   // Approve a paused HITL run
 *   await client.approveHITL(result.run_id, { reviewer_id: "alice", notes: "LGTM" });
 */

// ── Types ─────────────────────────────────────────────────────────────────────

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
  error: string;
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

export interface Trace {
  run_id: string;
  summary: {
    steps: number;
    nodes: string[];
    total_cost_usd: number;
    total_tokens: number;
    total_carbon_gco2: number;
    blocked_steps: number;
    verdicts: string[];
    timestamps: { start: string; end: string };
  };
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
    const h: Record<string, string> = {
      "Content-Type": "application/json",
      ...extra,
    };
    if (this.apiKey) {
      h["Authorization"] = `Bearer ${this.apiKey}`;
    }
    return h;
  }

  private async fetch<T>(
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
      throw new Error(`MeshFlow API error ${response.status}: ${text}`);
    }
    return response.json() as Promise<T>;
  }

  /** Check server health. Does not require authentication. */
  async health(): Promise<HealthResponse> {
    const response = await fetch(`${this.baseUrl}/health`);
    return response.json() as Promise<HealthResponse>;
  }

  /** Execute a task and wait for completion. */
  async run(
    task: string,
    policy?: PolicyConfig,
    context?: Record<string, unknown>,
  ): Promise<RunResult> {
    return this.fetch<RunResult>("POST", "/run", {
      task,
      policy: { ...this.defaultPolicy, ...policy },
      context,
    });
  }

  /** Stream a task execution, yielding events as they arrive.
   *
   * Events with kind === "token_delta" carry per-token text.
   * Events with kind === "step_end" carry the full step result.
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
      throw new Error(`MeshFlow stream error ${response.status}: ${text}`);
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
        try {
          yield JSON.parse(trimmed) as MeshEvent;
        } catch {
          // skip malformed lines
        }
      }
    }
    if (buffer.trim()) {
      try {
        yield JSON.parse(buffer) as MeshEvent;
      } catch {
        // ignore
      }
    }
  }

  /** Get a full run trace including all step records. */
  async getTrace(runId: string): Promise<Trace> {
    return this.fetch<Trace>("GET", `/traces/${runId}`);
  }

  /** List all run IDs in the ledger. */
  async listRuns(): Promise<{ runs: string[] }> {
    return this.fetch<{ runs: string[] }>("GET", "/traces");
  }

  /** List runs currently paused for human approval. */
  async listPendingHITL(): Promise<{ paused_runs: PausedRun[] }> {
    return this.fetch<{ paused_runs: PausedRun[] }>("GET", "/hitl/pending");
  }

  /** Approve a paused run so it can continue. */
  async approveHITL(runId: string, decision: HITLDecision = {}): Promise<void> {
    await this.fetch<unknown>("POST", `/hitl/${runId}/approve`, decision);
  }

  /** Reject a paused run — sets confidence=0.0 on resume. */
  async rejectHITL(runId: string, decision: HITLDecision = {}): Promise<void> {
    await this.fetch<unknown>("POST", `/hitl/${runId}/reject`, decision);
  }
}

export default MeshFlowClient;
