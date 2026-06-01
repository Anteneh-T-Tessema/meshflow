# MeshFlow SOC 2 Type II Controls Mapping

**Framework:** AICPA 2017 Trust Services Criteria (TSC)
**Audit program start:** June 2026
**MeshFlow version at program start:** v1.2
**Prepared by:** Engineering / Compliance — MeshFlow v1.2

This document maps every MeshFlow subsystem to the SOC 2 TSC criterion it
satisfies.  For each criterion the table lists the **MeshFlow control**, the
**primary code path** containing the implementation, and the **CLI command or
HTTP endpoint** that produces auditable evidence.

---

## CC6 — Logical and Physical Access Controls

### CC6.1 — Logical access security measures

**Requirement:** Restrict logical access to information assets to authorised
users.

| Control | Implementation | Evidence |
|---|---|---|
| Agent identity registry | `meshflow/identity/core.py` — `IdentityStore` (SQLite), `AgentIdentity` dataclass | `meshflow identity list --json` |
| HMAC-HS256 token issuance | `meshflow/identity/core.py` — `sign_token()`, token format `header.payload.sig` | `meshflow identity sign <name> --secret $SECRET` |
| Token verification with expiry | `meshflow/identity/core.py` — `verify_token()` — uses `hmac.compare_digest()` to prevent timing attacks | `meshflow identity verify <token> --secret $SECRET` |
| Identity revocation | `IdentityStore.revoke(agent_id)` — sets `revoked=1` in SQLite; subsequent `verify_token` calls check revocation | `meshflow identity revoke <agent_id>` |
| Capability scoping | `AgentIdentity.capabilities: list[str]` — each token encodes the capability list; agents cannot self-elevate | JSON output of `meshflow identity list` |
| API key store — PBKDF2 hashing | `meshflow/security/api_keys.py` — `_hash_key()` calls `hashlib.pbkdf2_hmac("sha256", key, salt, 100_000)` | `meshflow keys list --db meshflow_runs.db` |
| API key roles | `KeyStore` roles: `admin`, `operator`, `viewer` — enforced in `KeyStore.create()` | `meshflow keys generate <name> --role operator` |
| API key revocation | `KeyStore.revoke(key_id)` — soft-delete, `last_used_at` preserved for audit | `meshflow keys revoke <key_id>` |
| HTTP middleware | `meshflow/runtime/server.py` — `APIKeyMiddleware` reads `Authorization: Bearer` or `X-API-Key`; rejects 401 on invalid key | GET `/health` with and without key |
| Static key fallback | `MESHFLOW_API_KEYS` env var loaded by `_load_static_keys()` | `meshflow env --validate .env` |

**Key code location:** `meshflow/security/api_keys.py` lines 259–270 (PBKDF2 hash function), `meshflow/identity/core.py` lines 92–126 (token sign/verify).

---

### CC6.6 — Data transmission security

**Requirement:** Protect data in transit against unauthorised interception.

| Control | Implementation | Evidence |
|---|---|---|
| TLS termination | `meshflow serve --tls-cert <cert.pem> --tls-key <key.pem>` — flags parsed in `meshflow/cli/main.py` lines 138–139; passed to `ssl.SSLContext` in `meshflow/runtime/server.py` | `openssl s_client -connect host:8765` — verify TLS handshake |
| HMAC-SHA256 webhook signatures | `meshflow/core/hitl.py` — outbound HITL notifications include `X-MeshFlow-Signature: sha256=<hmac>` header; computed with `hmac.new(secret, body, hashlib.sha256)` | `meshflow webhooks add <url> --secret $WEBHOOK_SECRET` |
| Webhook delivery queue | `meshflow/observability/webhook_queue.py` — SQLite-backed delivery queue with retry/dead-letter | `meshflow webhooks queue --db meshflow_webhooks.db` |
| A2A bearer tokens | Agent-to-agent calls use HMAC-signed tokens from `IdentityStore`; see CC6.1 above | Inter-agent span attributes in `meshflow tracing show <trace_id>` |

---

## CC7 — System Operations

### CC7.2 — System monitoring

**Requirement:** Monitor the operational effectiveness of controls through continuous monitoring.

| Control | Implementation | Evidence |
|---|---|---|
| OTEL span emission | `meshflow/observability/telemetry.py` — `MeshFlowTracer` emits OpenTelemetry spans for every agent step, tool call, LLM call, and guardrail check | `meshflow tracing show <trace_id> --db meshflow_traces.db` |
| W3C traceparent propagation | `meshflow/tracing/context.py` — `TraceContext` carries `trace_id` + `span_id` across agent boundaries; `traceparent()` generates conformant header | Span `parent_id` linkage in trace output |
| SQLite span storage | `meshflow/tracing/context.py` — `TraceStore` persists spans in `trace_spans` table; indexed by `trace_id`, `parent_id`, `run_id` | `meshflow tracing count --db meshflow_traces.db` |
| Per-agent latency tracking | `meshflow/observability/sla.py` — `NodeLatencyTracker` accumulates wall-clock durations per node_id | `meshflow sla stats <agent_name>` |
| Prometheus metrics | `meshflow/observability/metrics.py` — `MetricsCollector` (singleton) exposes `meshflow_runs_total`, `meshflow_agent_latency_ms{agent,quantile}`, `meshflow_blocks_total{reason}`, `meshflow_tokens_total`, `meshflow_cost_usd_total` in Prometheus text format | GET `/metrics` on the running server |
| OTEL export pipeline | `meshflow/observability/otel_exporter.py` — configures OTLP HTTP/gRPC exporters; `meshflow/observability/genai.py` — GenAI semantic conventions | `OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318 meshflow serve` |
| Arize Phoenix integration | `meshflow/observability/arize_phoenix.py` — sends spans to Arize Phoenix for LLM observability | Phoenix UI traces view |

**Prometheus metrics relevant to CC7.2 monitoring:**
- `meshflow_agent_calls_total{agent, role}` — call volume per agent
- `meshflow_agent_latency_ms{agent, quantile="0.5|0.95|0.99"}` — p50/p95/p99
- `meshflow_blocks_total{reason}` — count of blocked actions by reason
- `meshflow_hitl_pending` — number of runs awaiting human approval

---

### CC7.3 — Security event detection

**Requirement:** Detect and respond to security events in a timely manner.

| Control | Implementation | Evidence |
|---|---|---|
| SHA-256 tamper-evident ledger | `meshflow/core/runtime.py` — `StepRecord` computes `entry_hash = sha256(run_id + node_id + status + token_count + prev_hash + ...)` at construction; `prev_hash` chains to previous record | `meshflow dasc verify --db meshflow_dasc.db` — outputs "CHAIN VALID" or lists broken links |
| `ReplayLedger` | `meshflow/core/ledger.py` — `ReplayLedger` (line 544) persists every `StepRecord` in SQLite `step_records` table with `prev_hash` and `entry_hash` columns | `meshflow audit export --format json --out audit_$(date +%Y%m).json` |
| `AuditLedger` | `meshflow/security/dasc_gate.py` — `AuditLedger` (line 188) records every intent that passed or was blocked by DascGate; separately queryable | `meshflow dasc ledger --db meshflow_dasc.db --limit 1000` |
| Guardian — injection scanning | `meshflow/security/guardian.py` — `InjectionScanner` scans every inter-agent message against 13 regex patterns; `BLOCKED` alerts written to `Guardian._alerts` | `meshflow security scan "<text>"` |
| Guardian — tool chain DoS detection | `meshflow/security/guardian.py` — `ToolChainAnalyzer` computes amplification factor; `is_dangerous=True` blocks execution and fires alert | Guardian `alerts()` list in application logs |
| Guardian — behavioural anomaly | `meshflow/security/guardian.py` — `BehaviouralMonitor` computes z-score of token rate per agent; z > 3.0 flags alert, z > 4.5 triggers Guardian block | Per-agent z-score in structured logs |
| AutoRiskClassifier | `meshflow/security/dasc_gate.py` — `AutoRiskClassifier` overrides agent-declared risk tiers using keyword analysis; prevents self-declaration inflation | DascGate ledger `risk_tier` column |
| DascGate policy enforcement | `meshflow/security/dasc_gate.py` — all intents pass through before execution; Tier 4 (delete/deploy/transfer_funds) requires explicit policy approval | `meshflow dasc classify "<action>"` |
| ComplianceGuard | `meshflow/compliance/guard.py` — `ComplianceGuard.pre_check()` blocks steps in real-time for HIPAA/SOX/GDPR/PCI/NERC violations; raises `ComplianceViolation` | `ComplianceGuard.summary()` in application startup logs |
| Alert engine | `meshflow/alerting/` — metric-threshold rules with webhook fanout; fired alerts stored in `meshflow_alerts.db` | `meshflow alerts list --status firing` |

---

## CC9 — Risk Mitigation

### CC9.2 — Vendor and business partner risk management

**Requirement:** Manage risks associated with vendors and business partners.

| Control | Implementation | Evidence |
|---|---|---|
| DID-based agent identity | `meshflow/identity/core.py` — each agent receives a UUID `agent_id` and signed token scoped to declared capabilities; external/vendor agents must register before use | `meshflow identity list --active-only --json` |
| Capability scoping | `AgentIdentity.capabilities` — tokens encode capability list; `verify_token()` returns capabilities that callers must check | Token payload decoded by `meshflow identity verify` |
| Third-party tool policy | `meshflow/security/dasc_gate.py` — `AutoRiskClassifier` classifies tool calls by keyword before execution | DascGate ledger entries per vendor tool |
| ComplianceGuard framework check | `meshflow/compliance/guard.py` — `SUPPORTED_FRAMEWORKS` = `["hipaa", "sox", "gdpr", "pci", "nerc"]`; non-framework calls raise `ValueError` at startup | `meshflow compliance report --framework sox` |
| MCP server isolation | `meshflow/mcp/` — MeshFlow exposes governed MCP server; tool calls from external MCP servers pass through DascGate | `meshflow mcp-stdio --policy regulated` |
| Plugin entry-point validation | `meshflow/plugins.py` — plugins loaded via `importlib.metadata` entry points; `meshflow plugins verify <name>` validates schema | `meshflow plugins list --group compliance` |

---

## A1 — Availability

### A1.1 — Performance monitoring

**Requirement:** Current and historical processing capacity and utilisation.

| Control | Implementation | Evidence |
|---|---|---|
| `SLATracker` | `meshflow/sla/tracker.py` — `SLATracker` records observations in `sla_observations` table; computes p50/p95/p99 using linear interpolation | `meshflow sla stats <agent_name> --window 86400` |
| SLA breach detection | `meshflow/sla/tracker.py` — `_check_breaches()` runs after every observation; writes `SLABreach` records to `sla_breaches` table | `meshflow sla breaches --limit 100` |
| SLA contracts | `meshflow/sla/tracker.py` — `SLAStore.define_contract()` enforces `p50 ≤ p95 ≤ p99`; `error_rate` and `window_s` configurable | `meshflow sla list` |
| `NodeLatencyTracker` | `meshflow/observability/sla.py` — `NodeLatencyTracker` (line 46) tracks wall-clock p50/p95/p99 per node_id; global singleton via `get_sla_tracker()` | Exposed in GET `/metrics` as `meshflow_agent_latency_ms` |
| `WorkflowAnalytics` | `meshflow/core/analytics.py` — `WorkflowAnalytics` reads `ReplayLedger` and produces time-series cost, latency, and quality summaries across runs | `meshflow analytics --db meshflow_runs.db --format json` |
| Budget tracking | `meshflow/budget/` — per-agent cost and token budgets; daily/weekly/monthly/total windows | `meshflow budget status <account_id>` |
| Prometheus histogram | `MetricsCollector._agent_latencies` feeds `meshflow_run_duration_seconds{q}` summary metric | GET `/metrics` — quantile labels `0.5`, `0.95`, `0.99` |

---

## PI1 — Processing Integrity

### PI1.4 — Complete and accurate processing

**Requirement:** Inputs, processing, and outputs are complete and accurate.

| Control | Implementation | Evidence |
|---|---|---|
| `StepRecord` hash chain | `meshflow/core/runtime.py` — every `StepRecord` is constructed with `prev_hash` (hash of prior record) and computes its own `entry_hash = sha256(run_id + node_id + status + token_count + prev_hash + ...)` in `__post_init__` (line 108) | `meshflow dasc verify` — chain integrity check |
| Ledger migration registry | `meshflow/core/ledger.py` — `_MIGRATIONS` list tracks schema evolution with version numbers; applied on every startup | `meshflow doctor --db meshflow_runs.db` |
| Schema-validated node I/O | `meshflow/core/schemas.py` — `NodeInput` and `NodeOutput` are typed dataclasses; `meshflow schema NodeInput` prints JSON Schema contract | `meshflow schema NodeInput` |
| Replay-based verification | `meshflow/core/ledger.py` — `ReplayLedger` supports `fork_at(step)` and `rewind` to reproduce past computation; any output change detects tampering | `meshflow replay <run_id> --json` |
| Audit export | `meshflow/cli/main.py` — `meshflow audit export` serialises all `StepRecord` fields including `prev_hash` and `entry_hash` to JSON or CSV | `meshflow audit export --format json --out evidence.json` |
| Compliance snapshot ZIP | `meshflow/snapshot/bundle.py` — `SnapshotExporter` bundles identities, lineage, policy rules, SLA contracts, vault audit, canary experiments into a single ZIP with `manifest.json` | `meshflow snapshot export --output q2_2026_snapshot.zip` |

---

## Additional Criteria Cross-Reference

| TSC | MeshFlow Feature | Module |
|---|---|---|
| CC1.5 — Accountability | SHA-256 chain; `dasc verify` | `meshflow/core/runtime.py` |
| CC2.2 — Internal communication | Trace Studio UI; `/traces` endpoint | `meshflow/studio/` |
| CC2.3 — External communication | HMAC webhook notifications | `meshflow/core/hitl.py` |
| CC3.2 — Risk analysis | DascGate Tier 1–4 classification | `meshflow/security/dasc_gate.py` |
| CC5.2 — Technology controls | Bearer token auth middleware | `meshflow/runtime/server.py` |
| CC5.3 — Deployment controls | Docker multi-stage; K8s HPA 2–10 replicas | `Dockerfile`, `k8s/deployment.yaml` |
| CC7.4 — Incident response | HITL approve/reject; `anonymize_run()` | `meshflow/core/ledger.py` |
| C1.1 — Confidential information | PHI scrubber; tenant isolation | `meshflow/security/phi_scrubber.py` |
| P4.2 — Retention | `delete_run()`, `anonymize_run()` | `meshflow/core/ledger.py` |

---

*This mapping is reviewed quarterly.  Last substantive update: June 2026.*
