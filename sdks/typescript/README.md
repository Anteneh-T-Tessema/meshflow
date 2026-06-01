# meshflow-sdk

TypeScript / JavaScript client SDK for [MeshFlow](https://meshflow.dev) — the production-safe multi-agent orchestration framework.

[![npm](https://img.shields.io/npm/v/meshflow-sdk)](https://www.npmjs.com/package/meshflow-sdk)
[![license](https://img.shields.io/badge/license-Apache%202.0-blue)](./LICENSE)
[![node](https://img.shields.io/badge/node-%3E%3D18-brightgreen)](https://nodejs.org)

---

## Installation

```bash
npm install meshflow-sdk
# or
pnpm add meshflow-sdk
# or
yarn add meshflow-sdk
```

Node ≥ 18 required (uses native `fetch` and `crypto.subtle`). Works in browsers via the standard Fetch API.

---

## Quick start

```typescript
import { createClient } from "meshflow-sdk";

// Reads MESHFLOW_SERVER and MESHFLOW_API_KEY from environment
const client = createClient();

// Run a governed task and wait for the result
const result = await client.run("Write a competitive analysis of the AI agent market");
console.log(result.output);
console.log(`Cost: $${result.total_cost_usd.toFixed(4)}  Tokens: ${result.total_tokens}`);
```

Or construct the client directly:

```typescript
import { MeshFlowClient } from "meshflow-sdk";

const client = new MeshFlowClient(
  "http://localhost:8000",   // MeshFlow server URL
  "your-api-key",            // optional
  { mode: "standard", budget_usd: 2.00 }  // default policy
);
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MESHFLOW_SERVER` | `http://localhost:8000` | MeshFlow server base URL |
| `MESHFLOW_API_KEY` | `""` | API key (sent as `Authorization: Bearer`) |

---

## API reference

### `client.run(task, policy?, context?)`

Execute a task and wait for the full result.

```typescript
const result = await client.run("Summarise this contract", {
  mode: "regulated",
  budget_usd: 1.00,
});

console.log(result.run_id);         // "run-abc123"
console.log(result.status);         // "completed"
console.log(result.output);         // final agent output
console.log(result.total_cost_usd); // 0.0042
console.log(result.total_tokens);   // 1800
console.log(result.collusion_alerts); // 0
```

### `client.stream(task, policy?, context?)`

Stream token-by-token output and governance events as they arrive.

```typescript
for await (const event of client.stream("Analyse this legal document")) {
  if (event.kind === "token_delta") {
    process.stdout.write(event.text ?? "");
  } else if (event.kind === "step_complete") {
    console.log(`\nStep done: agent=${event.agent_id} cost=$${event.cost_usd}`);
  } else if (event.kind === "run_complete") {
    console.log("\nDone.");
  }
}
```

**Event kinds:** `step_complete`, `step_blocked`, `token_delta`, `paused`, `run_complete`, `error`

### `client.liveEvents(runId?)`

Subscribe to live SSE events from a running workflow. Useful for dashboards.

```typescript
for await (const event of client.liveEvents("run-abc123")) {
  console.log(event.kind, event.agent_id, event.cost_usd);
}
```

### `client.getTrace(runId)`

Retrieve the full step-by-step audit trace for a completed run.

```typescript
const trace = await client.getTrace("run-abc123");
console.log(`Steps: ${trace.summary.steps}`);
console.log(`Blocked: ${trace.summary.blocked_steps}`);
trace.steps.forEach(step => {
  console.log(`  ${step.node_id}: ${step.verdict} (${step.tokens_used} tokens)`);
});
```

### `client.listRuns()`

List all run IDs in the ledger.

```typescript
const runIds = await client.listRuns();
```

### `client.getGraph(runId, format?)`

Export a run's execution graph as Mermaid or DOT.

```typescript
const mermaid = await client.getGraph("run-abc123", "mermaid");
const dot = await client.getGraph("run-abc123", "dot");
```

### `client.exportAudit(runId?, format?)`

Export the audit trail for a run (or all runs) as JSON or CSV.

```typescript
const csv = await client.exportAudit("run-abc123", "csv");
const json = await client.exportAudit(undefined, "json"); // all runs
```

### `client.listPendingHITL()`

List runs currently paused for human approval.

```typescript
const pending = await client.listPendingHITL();
pending.forEach(r => console.log(`Awaiting approval: ${r.run_id} paused at ${r.paused_at}`));
```

### `client.approveHITL(runId, decision?)`

Approve a paused run so it continues execution.

```typescript
await client.approveHITL("run-abc123", {
  reviewer_id: "alice@example.com",
  notes: "Reviewed and approved the summarised PHI output.",
});
```

### `client.rejectHITL(runId, decision?)`

Reject a paused run, stopping it with a `HITL_REJECTED` status.

```typescript
await client.rejectHITL("run-abc123", { notes: "Output contained PII." });
```

### `client.complianceReport(framework, runId?)`

Generate a post-hoc compliance report from ledger data.

```typescript
const report = await client.complianceReport("hipaa");
console.log(`HIPAA: ${report.summary.pass_rate * 100}% pass rate`);
console.log(`Status: ${report.summary.overall_status}`);
report.findings.filter(f => f.status === "fail").forEach(f => {
  console.log(`  FAIL ${f.control_id}: ${f.detail}`);
});
```

**Frameworks:** `"hipaa"` | `"sox"` | `"gdpr"` | `"pci"` | `"nerc"`

### `client.registerWebhook(url, events?, secret?)`

Register an endpoint to receive signed webhook events.

```typescript
const webhook = await client.registerWebhook(
  "https://your-app.com/webhooks/meshflow",
  ["policy_violation", "budget_exceeded", "hitl_pending"],
  process.env.WEBHOOK_SECRET!,
);
console.log(`Registered: ${webhook.id}`);
```

**Events:** `"policy_violation"` | `"budget_exceeded"` | `"hitl_pending"` | `"run_failed"` | `"run_completed"` | `"collusion_alert"` | `"*"`

### `client.getSLA(nodeId?)`

p50 / p95 / p99 latency per agent node.

```typescript
const sla = await client.getSLA();
sla.forEach(s => {
  console.log(`${s.node_id}: p50=${s.p50_ms}ms p99=${s.p99_ms}ms`);
});
```

### `client.health()`

```typescript
const h = await client.health();
console.log(h.ok, h.version, `uptime=${h.uptime_s}s`);
```

---

## Webhook signature verification

Verify incoming webhook payloads using the `WebCrypto` API (Node ≥ 18, all modern browsers).

```typescript
import { verifyWebhookSignature } from "meshflow-sdk";

// Express example
app.post("/webhooks/meshflow", async (req, res) => {
  const rawBody = req.rawBody;  // Buffer or string
  const signature = req.headers["x-meshflow-signature"] as string;

  const valid = await verifyWebhookSignature(rawBody, signature, process.env.WEBHOOK_SECRET!);
  if (!valid) return res.status(403).send("Forbidden");

  const event = req.body;
  console.log("Event:", event.type, event.run_id);
  res.json({ ok: true });
});
```

---

## Policy reference

```typescript
interface PolicyConfig {
  mode?: "dev" | "standard" | "regulated" | "legal-critical" | "hipaa";
  budget_usd?: number;          // max spend per run (default: 1.00)
  budget_tokens?: number;       // max tokens per run
  timeout_s?: number;           // wall-clock timeout
  max_steps?: number;           // max agent steps
  deterministic_gate?: boolean; // SwarmTRM consensus gate
  enable_guardian?: boolean;    // content safety guardian
  enable_collusion_audit?: boolean;
  enable_uncertainty?: boolean;
  enable_environmental?: boolean;
  carbon_budget_g?: number;     // max CO₂ equivalent grams
}
```

| Mode | Governance level |
|---|---|
| `dev` | Fast, minimal gates — for local development |
| `standard` | Audit ledger, policy basics — production default |
| `regulated` | HITL gates, immutable audit — finance / health |
| `legal-critical` | Evidence, citations, human review gates |
| `hipaa` | Full HIPAA §164.312 controls |

---

## RunResult reference

```typescript
interface RunResult {
  run_id: string;
  status: "pending" | "running" | "paused" | "completed" | "failed" | "aborted";
  output: unknown;              // final agent output
  total_cost_usd: number;
  total_tokens: number;
  total_carbon_g: number;
  duration_s: number;
  ledger_entries: number;       // SHA-256 hash chain entries written
  trace_id: string;             // W3C traceparent-compatible trace ID
  checkpoints: string[];        // replay checkpoint IDs
  error: string | null;
  collusion_alerts: number;     // inter-agent collusion detections
}
```

---

## Complete example — governed RAG pipeline

```typescript
import { createClient } from "meshflow-sdk";

const client = createClient({
  baseUrl: process.env.MESHFLOW_SERVER,
  apiKey: process.env.MESHFLOW_API_KEY,
  defaultPolicy: { mode: "regulated", budget_usd: 2.00 },
});

async function analyseContract(contractText: string) {
  // Stream the analysis
  let fullOutput = "";
  console.log("Analysing contract...\n");

  for await (const event of client.stream(
    `Review this contract for liability risks:\n\n${contractText}`,
    { mode: "legal-critical" }
  )) {
    if (event.kind === "token_delta") {
      process.stdout.write(event.text ?? "");
      fullOutput += event.text ?? "";
    }

    if (event.kind === "paused") {
      console.log(`\n\n[HITL] Run ${event.run_id} paused for human approval.`);
      console.log("Approve with: client.approveHITL(runId)");
      return;
    }

    if (event.kind === "run_complete") {
      // Fetch the compliance report
      const report = await client.complianceReport("hipaa", event.run_id);
      console.log(`\n\nHIPAA status: ${report.summary.overall_status}`);
      console.log(`Pass rate: ${(report.summary.pass_rate * 100).toFixed(1)}%`);
    }
  }
}

analyseContract("Section 7.2: Vendor may process Customer Data only to provide the Services...");
```

---

## Links

- [MeshFlow docs](https://meshflow.dev/docs)
- [Python SDK](https://pypi.org/project/meshflow)
- [GitHub](https://github.com/Anteneh-T-Tessema/meshflow)
- [Cloud dashboard](https://meshflow.dev/cloud)

---

## License

Apache 2.0 — see [LICENSE](../../LICENSE).
