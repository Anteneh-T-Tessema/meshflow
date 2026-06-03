# meshflow — Public API Reference

Complete index of everything exported from `import meshflow`. All symbols listed here are stable under semantic versioning as of v1.13.0.

## Agent Creation

| Symbol | Description |
|--------|-------------|
| `Agent` | Declarative agent builder — the primary entry point |
| `StructuredAgent` | Agent with Pydantic output schema enforcement |
| `token_budget` | Context manager for token-optimized multi-step runs |

## Multi-Agent Patterns

| Symbol | Description |
|--------|-------------|
| `Team` | Multiple agents with a coordination pattern |
| `MessageBus` | Async pub/sub between any agents |
| `GroupChat` | AutoGen-style round-robin/auto multi-agent chat |
| `GroupChatManager` | Orchestrates GroupChat speaker selection |
| `ConversationResult` | Output of a GroupChat session |
| `Supervisor` | One orchestrator → N worker agents |
| `SupervisorResult` | Output of Supervisor.run() |
| `AdversarialTeam` | Proposer → Attacker → Judge debate pattern |
| `AdversarialResult` | Output of AdversarialTeam.run() |
| `AgentSession` | Stateful multi-turn conversation with history compression |
| `SessionResult` | Output of AgentSession.chat() |
| `Turn` | Single turn in an AgentSession |
| `AgentPool` | Async queue with round-robin agent dispatching |
| `PoolStats` | AgentPool utilization statistics |

## Task + Crew (CrewAI-compatible)

| Symbol | Description |
|--------|-------------|
| `Task` | Work unit with role assignment and {placeholder} substitution |
| `TaskOutput` | Output of a completed Task |
| `Crew` | Team of agents running Tasks via a Process |
| `CrewOutput` | Output of Crew.kickoff() |
| `Process` | Execution strategy: sequential / parallel / hierarchical |

## Typed State Graph (LangGraph-compatible)

| Symbol | Description |
|--------|-------------|
| `StateGraph` | Typed workflow graph with conditional edges |
| `END` | Terminal node sentinel |
| `START` | Entry node sentinel |
| `Channel` | State channel descriptor |
| `add` | Reducer: append to list |
| `last` | Reducer: keep last value |
| `first` | Reducer: keep first value |
| `node` | Decorator to define a graph node function |
| `interrupt` | Pause execution for human approval |
| `Command` | Resume command after HITL interrupt |
| `Interrupt` | Raised when execution is paused |
| `Send` | Route to a specific node with payload |
| `MemorySaver` | In-memory graph checkpoint backend |
| `SqliteSaver` | SQLite graph checkpoint backend |

## Flows — Event-Driven Decorator API

| Symbol | Description |
|--------|-------------|
| `Flow` | Event-driven workflow container |
| `FlowState` | Pydantic state model for flows |
| `FlowResult` | Output of flow execution |
| `flow_start` | Decorator: entry point of a flow |
| `flow_listen` | Decorator: react to an event |
| `flow_router` | Decorator: conditional routing |

## Declarative Config

| Symbol | Description |
|--------|-------------|
| `MeshFlowConfig` | Global configuration model |
| `load` | Load config from YAML file path |
| `loads` | Load config from YAML string |

## Tool Ecosystem

| Symbol | Description |
|--------|-------------|
| `Tool` | Callable tool with name, description, risk tier |
| `ToolRegistry` | Registry of all available tools |
| `tool` | Decorator to register a function as a Tool |
| `global_registry` | Module-level default ToolRegistry |
| `GovernedToolRegistry` | Tool registry with permissions and audit trail |
| `ToolPermission` | Permission rule for a governed tool |
| `ToolAuditEntry` | Audit log entry for tool dispatch |
| `PermissionDeniedError` | Raised when tool access is denied |
| `ToolNotFoundError` | Raised when tool name is not registered |

## Governance

| Symbol | Description |
|--------|-------------|
| `Policy` | Governance policy attached to agents/workflows |
| `PolicyMode` | Standard / legal-critical / etc. |
| `policy_for_mode` | Construct a Policy for a named mode |
| `HumanInLoopConfig` | HITL checkpoint configuration |
| `AgentRole` | Enum: planner / researcher / executor / critic / orchestrator / guardian |
| `RiskTier` | Enum: READ_ONLY / INTERNAL / EXTERNAL_IO / DESTRUCTIVE |
| `RunStatus` | Enum: running / completed / failed / paused |
| `RunResult` | Result of a governed workflow run |
| `Evidence` | Evidence block attached to an Intent |
| `Intent` | Declared action before execution |
| `Message` | Inter-agent message |
| `ComplianceProfile` | Named compliance ruleset (HIPAA/SOX/GDPR/PCI/NERC) |
| `compliance_profile` | Look up a ComplianceProfile by name |
| `list_profiles` | List all available compliance profile names |

## Ledger + Audit

| Symbol | Description |
|--------|-------------|
| `ReplayLedger` | Append-only governed step ledger |
| `RunDiff` | Structured diff between two ledger runs |
| `LedgerBackend` | Protocol for custom ledger backends |
| `SQLiteLedgerBackend` | SQLite ledger backend |
| `PostgresLedgerBackend` | PostgreSQL ledger backend |
| `S3LedgerArchiveBackend` | S3 archive backend |
| `LedgerArchiveResult` | Result of archiving a run to S3 |
| `StepRuntime` | 15-step governed execution kernel |
| `RuntimeOutcome` | Outcome of a StepRuntime execution |

## Evaluation

| Symbol | Description |
|--------|-------------|
| `EvalSuite` | YAML-driven evaluation harness |
| `EvalScenario` | Single evaluation scenario |
| `EvalResult` | Aggregate result of a suite run |
| `ScenarioResult` | Result of a single scenario |
| `run_eval` | Run an EvalSuite against an agent |
| `EvalBaseline` | Saved baseline for regression comparison |
| `BaselineDiff` | Diff between current run and baseline |
| `LLMJudge` | LLM-as-judge with structured scoring |
| `JudgeScore` | Score from LLMJudge |
| `JudgeSuiteResult` | Aggregate judge scores |
| `ConversationEval` | Multi-turn conversation evaluator |
| `ConversationCase` | Test case for ConversationEval |
| `EvalTurn` | Turn in a ConversationCase |
| `EvalConversationResult` | Result of ConversationEval |
| `TurnResult` | Result of a single eval turn |
| `ABTest` | A/B test between two agents or prompts |
| `ABVariant` | One variant in an ABTest |
| `ABTestResult` | Aggregate ABTest result |
| `ABTurnResult` | Per-turn result in an ABTest |
| `QualityGate` | Block deploys on quality regression |
| `QualityReport` | QualityGate evaluation report |
| `FeedbackRecord` | Human feedback record |
| `FeedbackStore` | Store for FeedbackRecord entries |
| `ShadowResult` | Result of a shadow run |
| `shadow_run` | Run an agent in shadow mode against production |
| `RegressionAlert` | Alert from regression detection |
| `RegressionDetector` | Detects regressions across shadow runs |

## Memory

| Symbol | Description |
|--------|-------------|
| `AgentMemory` | 4-tier memory: Working → Episodic → Semantic → Procedural |
| `MemoryItem` | Single memory entry |
| `VectorStore` | In-process text embedding store |
| `KnowledgeSource` | Single knowledge source (file / text / VectorStore) |
| `AgentKnowledge` | Multi-source knowledge retriever |
| `MemoryBackend` | Protocol for persistent memory backends |
| `InMemoryBackend` | In-memory (non-persistent) backend |
| `SQLiteMemoryBackend` | SQLite memory backend |
| `PostgresMemoryBackend` | PostgreSQL memory backend |
| `snapshot_from_memory` | Export memory to serializable snapshot |
| `restore_memory` | Restore memory from snapshot |
| `MemoryConsolidator` | Compress and deduplicate memory |
| `ConsolidationReport` | Report from memory consolidation |
| `TeamWorkspace` | Shared memory across a team |
| `WorkspaceSummary` | Summary of team workspace state |
| `SemanticMemoryEntry` | Dense-embedded memory entry |
| `SemanticSearchResult` | Result of semantic memory search |
| `SemanticMemoryStore` | Dense embedding memory store |
| `CrossSessionMemoryStore` | Persist memories across sessions |
| `CrossSessionEntry` | Entry in CrossSessionMemoryStore |

## LLM Providers

| Symbol | Description |
|--------|-------------|
| `LLM` | Universal entry point: `LLM("model-name")` |
| `AnthropicProvider` | Anthropic Claude (with prompt caching) |
| `OpenAICompatibleProvider` | OpenAI or any OpenAI-compatible endpoint |
| `GeminiProvider` | Google Gemini |
| `BedrockProvider` | AWS Bedrock |
| `AzureOpenAIProvider` | Azure OpenAI |
| `OllamaProvider` | Local Ollama (no API key) |
| `LiteLLMProvider` | 100+ models via LiteLLM |
| `EchoProvider` | Offline echo provider (MESHFLOW_MOCK=1) |
| `AzureIdentityProvider` | Azure Managed Identity |
| `BedrockIAMProvider` | AWS IAM role-based Bedrock access |
| `VertexAIProvider` | Google Vertex AI |
| `ProviderRouter` | Route by role × budget × compliance → model |
| `auto_provider` | Auto-pick provider from environment |
| `auto_model` | Auto-pick model from environment |
| `model_to_provider` | Infer provider from model name string |
| `provider_for` | Factory: `provider_for("ollama", model="llama3.2")` |
| `auto_detect_provider` | Auto-pick based on available keys/services |
| `PROVIDER_NAMES` | List of all supported provider name strings |
| `ModelHealthTracker` | Rolling-window per-model health tracking |
| `ModelHealthSummary` | Health summary for a model |
| `get_health_tracker` | Get the global ModelHealthTracker singleton |

## Streaming

| Symbol | Description |
|--------|-------------|
| `StreamChunk` | Token chunk emitted during streaming |
| `BackpressureQueue` | Async queue with configurable backpressure |
| `BackpressureStrategy` | Drop / block / error strategy |
| `StreamMultiplexer` | Fan-out a stream to multiple subscribers |
| `Subscription` | A subscriber in a StreamMultiplexer |
| `PartialStructuredOutput` | Streaming partial Pydantic model output |
| `PartialOutputChunk` | Chunk of a partial structured output |
| `stream_structured` | Stream and parse structured output incrementally |
| `RunStreamHub` | Per-run stream hub for SSE delivery |
| `get_run_hub` | Get or create a RunStreamHub |
| `reset_run_hub` | Reset the global RunStreamHub |

## Guardrails + Security

| Symbol | Description |
|--------|-------------|
| `Guardrail` | Base protocol for all guardrails |
| `GuardrailResult` | Result of a guardrail check |
| `GuardrailStack` | Ordered stack of guardrails |
| `GuardrailViolation` | Exception raised on strict guardrail failure |
| `PIIBlockGuardrail` | Block PII in input or output |
| `ConfidenceGuardrail` | Require minimum confidence score |
| `LengthGuardrail` | Enforce min/max length |
| `ToxicityGuardrail` | Block toxic content |
| `JSONSchemaGuardrail` | Enforce JSON schema on output |
| `RegexGuardrail` | Require or forbid regex pattern |
| `KeywordBlockGuardrail` | Block specific keywords or phrases |
| `CostCapGuardrail` | Block if cost exceeds per-call budget |
| `CustomGuardrail` | User-defined guardrail function |
| `SensitiveDataDetector` | 23 PHI/PII + credential pattern detector |
| `SensitiveMatch` | Match from SensitiveDataDetector |
| `get_sensitive_detector` | Get the global SensitiveDataDetector |
| `PromptInjectionDetector` | Detect prompt injection attempts |
| `InjectionMatch` | Match from PromptInjectionDetector |
| `InjectionResult` | Result of injection scan |
| `PromptInjectionGuardrail` | Guardrail wrapping PromptInjectionDetector |
| `SecretScanner` | Detect exposed secrets and credentials |
| `SecretMatch` | Match from SecretScanner |
| `SecretScanResult` | Result of secret scan |
| `SecretScanGuardrail` | Guardrail wrapping SecretScanner |

## Observability

| Symbol | Description |
|--------|-------------|
| `EventProjector` | Project ledger events into 4 views |
| `AuditTrailProjection` | Audit trail event projection |
| `NodeLatencyProjection` | Per-node latency tracking |
| `NodeLatencyStats` | p50/p95/p99 latency statistics |
| `PolicyViolationProjection` | Policy violation event projection |
| `WorkflowSummaryProjection` | Workflow summary event projection |
| `WorkflowSummary` | Summary of a workflow run |
| `OTELExporter` | OTLP/HTTP span exporter |
| `get_global_exporter` | Get the global OTELExporter |
| `set_global_exporter` | Set the global OTELExporter |
| `reset_global_exporter` | Reset to no-op exporter |
| `MetricsCollector` | Prometheus-compatible metrics |
| `GenAI` | OpenTelemetry GenAI semantic convention constants |
| `MF` | MeshFlow-specific span attribute constants |
| `GenAISpanRecord` | Recorded GenAI span |
| `SpanStore` | In-memory span store |
| `configure_telemetry` | One-call OTEL setup |
| `get_span_store` | Get the global SpanStore |
| `record_agent_step` | Record an agent step span |
| `record_handoff` | Record an agent handoff span |
| `record_tool_call` | Record a tool call span |
| `record_guardrail` | Record a guardrail check span |
| `record_healing_attempt` | Record a self-healing attempt span |
| `otel_span` | Context manager for manual span creation |
| `otel_is_enabled` | Check if OTEL telemetry is active |
| `TraceServer` | Visual trace studio server |
| `WebhookDelivery` | Single webhook delivery record |
| `WebhookRetryQueue` | Durable SQLite-backed retry queue |
| `WebhookReliableDeliverer` | High-level webhook sender with retry |
| `AlertRule` | Metric-threshold alert rule |
| `AlertRecord` | Fired alert record |
| `AlertRuleStore` | Store for AlertRule definitions |
| `AlertStore` | Store for fired AlertRecord entries |
| `AlertEngine` | Evaluates metric points against alert rules |
| `MetricPoint` | Single metric observation |
| `MetricStore` | Time-series metric store |

## Full list of exports

```python
import meshflow
print(meshflow.__all__)  # complete list of 200+ stable symbols
print(meshflow.__version__)  # "1.0.0"
```
