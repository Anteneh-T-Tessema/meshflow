# Changelog

All notable changes to `meshflow-sdk` are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-06-01

First stable release. Tracks MeshFlow Python framework v1.0.0.

### Added

**Core execution**
- `MeshFlowClient.run()` — execute a governed task, wait for `RunResult`
- `MeshFlowClient.stream()` — stream NDJSON events token-by-token (`token_delta`, `step_complete`, `step_blocked`, `paused`, `run_complete`, `error`)
- `MeshFlowClient.liveEvents()` — subscribe to SSE event bus (dashboard / monitoring use case)

**Audit & traces**
- `MeshFlowClient.listRuns()` — list all run IDs in the ledger
- `MeshFlowClient.getTrace()` — full step-by-step `StepRecord` array with SHA-256 hashes
- `MeshFlowClient.getGraph()` — Mermaid or DOT execution graph export
- `MeshFlowClient.exportAudit()` — CSV or JSON audit trail export

**Human-in-the-loop**
- `MeshFlowClient.listPendingHITL()` — runs paused for human approval
- `MeshFlowClient.approveHITL()` — approve and resume a paused run
- `MeshFlowClient.rejectHITL()` — reject a paused run

**Compliance**
- `MeshFlowClient.complianceReport()` — post-hoc HIPAA / SOX / GDPR / PCI / NERC report generation

**Webhooks**
- `MeshFlowClient.listWebhooks()` — list registrations and delivery stats
- `MeshFlowClient.registerWebhook()` — register an endpoint with event filter and signing secret
- `MeshFlowClient.deleteWebhook()` — deregister a webhook
- `MeshFlowClient.getWebhookDeliveries()` — per-webhook delivery history
- `verifyWebhookSignature()` — HMAC-SHA256 signature verification via `WebCrypto` (Node ≥ 18 + browsers)

**Observability**
- `MeshFlowClient.getSLA()` — p50 / p95 / p99 latency per agent node
- `MeshFlowClient.getRateLimiterStatus()` — token-bucket status per API key
- `MeshFlowClient.getPoolStatus()` — AgentPool stats
- `MeshFlowClient.getOTELConfig()` — OpenTelemetry / trace-context configuration
- `MeshFlowClient.getMetrics()` — raw Prometheus metrics

**Evals & plugins**
- `MeshFlowClient.listEvalResults()` — stored eval baseline results
- `MeshFlowClient.listPlugins()` — installed MeshFlow plugin list

**Health**
- `MeshFlowClient.health()` — server health check
- `MeshFlowClient.healthLive()` — Kubernetes liveness probe
- `MeshFlowClient.healthReady()` — Kubernetes readiness probe

**Convenience**
- `createClient()` factory — reads `MESHFLOW_SERVER` and `MESHFLOW_API_KEY` from environment

**Types exported**
- `PolicyConfig`, `RunResult`, `MeshEvent`, `StepRecord`, `Trace`, `TraceSummary`
- `HITLDecision`, `PausedRun`, `HealthResponse`, `ProbeResponse`
- `ComplianceFramework`, `ComplianceFinding`, `ComplianceSummary`, `ComplianceReport`
- `WebhookEvent`, `WebhookRegistration`, `WebhookStats`, `DeliveryRecord`
- `SLASummary`, `RateLimiterBucket`, `PoolStats`
- `EvalResult`, `Plugin`, `OTELConfig`
- `MeshFlowError`

### Technical

- Zero runtime dependencies — uses native `fetch`, `crypto.subtle`, and `TextDecoder`
- Dual CJS + ESM output via `tsup`
- Full TypeScript declarations (`.d.ts` + declaration maps)
- Constant-time HMAC comparison in `verifyWebhookSignature` to prevent timing attacks
- Node ≥ 18 required; browser-compatible

---

## [0.19.0] — 2026-05-23

Pre-release development build. Internal use only.

### Added
- Initial TypeScript SDK implementation covering REST and SSE endpoints
- `MeshFlowClient` base class with typed request helper
- Webhook signature verification with WebCrypto
- Full type definitions for all MeshFlow API response shapes

---

[1.0.0]: https://github.com/Anteneh-T-Tessema/meshflow/releases/tag/sdk-ts-v1.0.0
[0.19.0]: https://github.com/Anteneh-T-Tessema/meshflow/releases/tag/sdk-ts-v0.19.0
