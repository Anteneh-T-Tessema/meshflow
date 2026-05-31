# TypeScript Client SDK

A typed REST + SSE client for the MeshFlow HTTP API, with WebCrypto webhook signature verification.

## Install

```bash
npm install @meshflow/client
# or
yarn add @meshflow/client
```

## Basic usage

```typescript
import { MeshFlowClient } from "@meshflow/client";

const client = new MeshFlowClient({
  baseUrl: "http://localhost:8000",
  apiKey: "mf-your-api-key",
});

// Run an agent
const result = await client.runAgent("assistant", "What is 2 + 2?");
console.log(result.output);        // "4"
console.log(result.cost_usd);     // 0.00012
console.log(result.tokens);       // 48

// Get run history
const runs = await client.listRuns();
const steps = await client.getRun(runs[0]);
```

## SSE streaming

```typescript
for await (const event of client.streamEvents()) {
  console.log(event.event_type, event.run_id, event.data);
  if (event.event_type === "hitl_pending") {
    await client.approveHITL(event.run_id, { decision: "approve" });
  }
}
```

## Typed event types

```typescript
type MeshFlowEvent =
  | { event_type: "step_complete"; run_id: string; node_id: string; cost_usd: number }
  | { event_type: "policy_violation"; run_id: string; rule_name: string; reason: string }
  | { event_type: "hitl_pending"; run_id: string; checkpoint_id: string }
  | { event_type: "budget_exceeded"; run_id: string; cost_usd: number };
```

## Webhook signature verification

```typescript
import { verifyWebhookSignature } from "@meshflow/client";

// In your Express/Next.js webhook handler
app.post("/meshflow-webhook", async (req, res) => {
  const isValid = await verifyWebhookSignature(
    req.body,                              // raw Buffer
    req.headers["x-meshflow-signature"],  // "sha256=..."
    process.env.MESHFLOW_WEBHOOK_SECRET,
  );
  if (!isValid) return res.status(401).end();
  
  const event = JSON.parse(req.body.toString());
  // handle event...
  res.status(200).end();
});
```

## Full client API

```typescript
client.runAgent(name, task, context?)        // → AgentResult
client.listRuns()                            // → string[]
client.getRun(runId)                         // → StepRecord[]
client.getRunSummary(runId)                  // → RunSummary
client.approveHITL(runId, decision)          // → void
client.streamEvents()                        // → AsyncIterableIterator<MeshFlowEvent>
client.getAnalytics(n?)                      // → AnalyticsReport
client.getPlugins()                          // → PluginInfo[]
client.health()                              // → { live: boolean; ready: boolean }
```
