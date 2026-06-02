"""MeshFlow — build, orchestrate, and govern multi-agent systems.

Build agents:       Agent(name, role, tools, memory=True)
Pre-built agents:   agents.ResearchAgent(), agents.CoderAgent(), agents.CriticAgent(), ...
Form teams:         Team([planner, researcher, executor], pattern="supervised")
Group chat:         GroupChat(agents, max_turns=20, speaker_selection="auto")
Typed state graph:  StateGraph(MyStateDict).add_node(...).compile()
Create tools:       @tool(name="search", risk=RiskTier.EXTERNAL_IO)
Message agents:     MessageBus — async pub/sub between any agents
Govern everything:  policy_for_mode("legal-critical")
Load from YAML:     meshflow.load("meshflow.yaml")
Wrap any framework: govern(my_langgraph_app)
HTTP client:        MeshFlowClient("http://localhost:8000", api_key="...")
Eval agents:        run_eval(agent, "evals.yaml")
"""

from meshflow.client import MeshFlowClient, PolicyConfig as ClientPolicyConfig
from meshflow.core.mesh import Mesh, MeshEvent
from meshflow.core.govern import GovernedApp, govern
from meshflow.optimization import token_budget
from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.contracts import core_contract_schemas
from meshflow.core.workflow import HumanDecision, WorkflowDefinition, WorkflowResult, Workflow, CostCap
from meshflow.core.events import WorkflowEventBus
from meshflow.core.state import StateGraph, END, START, add, last, first, Channel, node, interrupt, Command, Interrupt, Send, MemorySaver, SqliteSaver
from meshflow.core.flows import Flow, FlowState, FlowResult, start as flow_start, listen as flow_listen, router as flow_router
from meshflow.core.prebuilt import MessagesState, ToolNode, create_react_agent, create_tool_calling_agent
from meshflow.core.config import MeshFlowConfig, load, loads
from meshflow.core.ledger import (
    LedgerArchiveResult,
    LedgerBackend,
    PostgresLedgerBackend,
    ReplayLedger,
    RunDiff,
    S3LedgerArchiveBackend,
    SQLiteLedgerBackend,
)
from meshflow.core.runtime import StepRuntime, RuntimeOutcome
from meshflow.core.schemas import (
    AgentRole,
    Evidence,
    HumanInLoopConfig,
    Intent,
    Message,
    Policy,
    PolicyMode,
    RiskTier,
    RunResult,
    RunStatus,
    policy_for_mode,
)
from meshflow.agents.adapters import from_autogen, from_callable, from_crewai, from_langgraph
from meshflow.agents.builder import Agent
from meshflow.agents.team import Team
from meshflow.agents.task import Task, TaskOutput
from meshflow.agents.crew import Crew, Process, CrewOutput
from meshflow.agents.skills import Skill, SKILLS, skill_prompt, list_skills
from meshflow.agents.messaging import MessageBus
from meshflow.agents.conversation import GroupChat, GroupChatManager, ConversationResult
from meshflow.agents.react import ReActAgent, ReActResult, ThoughtStep
from meshflow.agents.router import ProviderRouter, auto_provider, auto_model
from meshflow.agents.base import EchoProvider, AnthropicProvider, OpenAICompatibleProvider
from meshflow.agents.providers import (
    GeminiProvider,
    BedrockProvider,
    AzureOpenAIProvider,
    OllamaProvider,
    LiteLLMProvider,
    provider_for,
    auto_detect_provider,
    model_to_provider,
    LLM,
    PROVIDER_NAMES,
    AzureIdentityProvider,
    BedrockIAMProvider,
    VertexAIProvider,
)
from meshflow.intelligence.memory import AgentMemory, MemoryItem
from meshflow.intelligence.consolidator import MemoryConsolidator, ConsolidationReport
from meshflow.intelligence.team_workspace import TeamWorkspace, WorkspaceSummary
from meshflow.intelligence.knowledge import VectorStore, KnowledgeSource, AgentKnowledge
from meshflow.core.streaming import StreamChunk, tokens, cost_events, filter_stream, task_outputs
from meshflow.streaming.backpressure import BackpressureQueue, BackpressureStrategy
from meshflow.streaming.multiplexer import StreamMultiplexer, Subscription
from meshflow.streaming.partial_output import (
    PartialStructuredOutput, PartialOutputChunk, stream_structured,
)
from meshflow.streaming.run_hub import RunStreamHub, get_run_hub, reset_run_hub
from meshflow.agents.supervisor import Supervisor, SupervisorResult
from meshflow.agents.adversarial import AdversarialTeam, AdversarialResult
from meshflow.agents.session import AgentSession, SessionResult, Turn
from meshflow.core.compliance import ComplianceProfile, compliance_profile, list_profiles
from meshflow.core.durable import DurableWorkflowExecutor
from meshflow.core.worker_pool import WorkerPool, WorkerPoolConfig  # noqa: F401
from meshflow.core.projections import (
    AuditTrailProjection,
    NodeLatencyProjection,
    NodeLatencyStats,
    PolicyViolationProjection,
    WorkflowSummaryProjection,
    WorkflowSummary,
    EventProjector,
)
from meshflow.agents.tool_registry import (
    GovernedToolRegistry,
    ToolPermission,
    AuditEntry as ToolAuditEntry,
    PermissionDeniedError,
    ToolNotFoundError,
)
from meshflow.tools.registry import Tool, ToolRegistry, tool, global_registry
from meshflow.eval import EvalSuite, EvalScenario, EvalResult, ScenarioResult, run_eval, EvalBaseline, BaselineDiff
from meshflow.eval.judge import LLMJudge, JudgeScore, JudgeSuiteResult
from meshflow.eval.conversation_eval import (
    ConversationEval,
    ConversationCase,
    Turn as EvalTurn,
    ConversationResult as EvalConversationResult,
    TurnResult,
)
from meshflow.eval.ab_test import ABTest, ABVariant, ABTestResult, ABTurnResult
from meshflow.eval.quality_gate import QualityGate, QualityReport
from meshflow.agents.pool import AgentPool, PoolStats, register_pool, deregister_pool
from meshflow.plugins import PluginInfo, discover_plugins, load_plugin, verify_plugin
from meshflow.agents import library as agents
from meshflow.mcp.server import MCPServer, MCPToolEntry, from_config as mcp_from_config
from meshflow.swarm import (
    SwarmNode,
    swarm_verifier,
    register_swarm_domain,
    available_domains as swarm_available_domains,
    VerificationResult,
    DeterministicVerifier,
)
from meshflow.security.sensitive_data import (
    SensitiveDataDetector,
    SensitiveMatch,
    get_detector as get_sensitive_detector,
)
from meshflow.security.guardrails import (
    Guardrail,
    GuardrailResult,
    GuardrailStack,
    GuardrailViolation,
    PIIBlockGuardrail,
    ConfidenceGuardrail,
    LengthGuardrail,
    ToxicityGuardrail,
    JSONSchemaGuardrail,
    RegexGuardrail,
    KeywordBlockGuardrail,
    CostCapGuardrail,
    CustomGuardrail,
)
from meshflow.agents.health import (
    ModelHealthTracker,
    ModelHealthSummary,
    get_health_tracker,
)
from meshflow.core.analytics import WorkflowAnalytics, RunSummary
from meshflow.queue import TaskQueue, QueueWorker, TaskItem, TaskStatus
from meshflow.tools.code_interpreter import CodeInterpreter, CodeResult
from meshflow.tools.sandbox_providers import E2BSandboxProvider, ModalSandboxProvider, SandboxRouter
from meshflow.a2a import AgentCard, A2AMessage, A2AResponse, A2AClient, A2AServer
# ── SIEM streaming ────────────────────────────────────────────────────────────
from meshflow.observability.siem import (
    SIEMStreamer,
    SplunkHECBackend,
    DatadogLogsBackend,
    GenericHTTPBackend,
    get_siem_streamer,
)
# ── Red-team testing ──────────────────────────────────────────────────────────
from meshflow.security.red_team import (
    RedTeamSuite,
    RedTeamReport,
    ProbeResult as RedTeamProbeResult,
    Probe as RedTeamProbe,
)
# ── Blue/green deployments ────────────────────────────────────────────────────
from meshflow.deploy.blue_green import (
    BlueGreenRouter,
    AgentDeployment,
    PromotionResult,
    DeploymentStore,
)
# ── Zero Trust framework ──────────────────────────────────────────────────────
from meshflow.zero_trust import (
    ZeroTrustPolicy,
    ZeroTrustTier,
    FOUNDATION as ZT_FOUNDATION,
    ENTERPRISE as ZT_ENTERPRISE,
    ADVANCED as ZT_ADVANCED,
    SpotlightingGuardrail,
    SpotlightContext,
    JITPrivilegeManager,
    PrivilegeGrant,
    PrivilegeExpiredError,
    AIBillOfMaterials,
    ModelComponent as BOMModelComponent,
    ToolComponent as BOMToolComponent,
    ContinuousAuthorizationEngine,
    AuthorizationContext,
    AuthDecision,
    ZeroTrustOrchestrator,
    ZeroTrustSession,
    ZeroTrustRunResult,
)
from meshflow.multimodal import (
    ImageInput,
    DocumentInput,
    AudioInput,
    MultiModalInput,
    build_multimodal_message,
)
from meshflow.agents.structured import (
    StructuredOutputResult,
    StructuredOutputError,
    StructuredOutputParser,
)
from meshflow.agents.builder import StructuredAgent
from meshflow.intelligence.memory_backends import (
    MemoryBackend,
    InMemoryBackend,
    SQLiteMemoryBackend,
    PostgresMemoryBackend,
    snapshot_from_memory,
    restore_memory,
)
from meshflow.cache import CacheEntry, LLMCache, InMemoryCache, SQLiteCache, CachedProvider
from meshflow.agents.healing import HealingPolicy, HealingStrategy, HealingResult, run_with_healing
from meshflow.prompts import PromptVersion, PromptTemplate, PromptRegistry, PromptABTest
from meshflow.export import FinetuneExporter, ExportFormat, TraceRecord, ExportFilter
from meshflow.mcp.client import MCPClientSession, MCPRemoteTool, MCPClient, MCPClientError
from meshflow.agents.handoff import HandoffConfig, HandoffLink, HandoffResult, run_with_handoffs
from meshflow.a2a.tasks import A2ATask, A2ATaskStore, TaskState, TaskEventQueue
from meshflow.observability.genai import (
    GenAI,
    MF,
    GenAISpanRecord,
    SpanStore,
    configure_telemetry,
    get_span_store,
    record_agent_step,
    record_handoff,
    record_tool_call,
    record_guardrail,
    record_healing_attempt,
    span as otel_span,
    is_enabled as otel_is_enabled,
)
from meshflow.registry import AgentManifest, AgentRegistry, get_registry
from meshflow.registry.templates import AgentTemplate, TemplateRegistry, MarketplaceClient, MarketplaceServer
from meshflow.eval.feedback import FeedbackRecord, FeedbackStore
from meshflow.eval.shadow import ShadowResult, shadow_run, RegressionAlert, RegressionDetector
from meshflow.observability.metrics import MetricsCollector
from meshflow.observability.arize_phoenix import PhoenixExporter, auto_instrument
from meshflow.observability.otel_exporter import (
    OTELExporter,
    get_global_exporter,
    set_global_exporter,
    reset_global_exporter,
)
from meshflow.budget import (
    BudgetAccount,
    BudgetSpend,
    BudgetCheckResult,
    BudgetStore,
    BudgetGuardrail,
    get_budget_store,
    reset_budget_store,
    period_key as budget_period_key,
)
from meshflow.scheduler import (
    CronExpression,
    CronScheduler,
    ScheduledTask,
    ScheduleRun,
    ScheduleStore,
)
from meshflow.ratelimit import (
    RateLimitPolicy,
    RateLimitResult,
    RateLimitStore,
    RateLimitPolicyDB,
    RateLimitGuardrail,
    TeamRateLimitGuardrail,
    get_rate_limit_store,
    reset_rate_limit_store,
)
from meshflow.security.injection import (
    InjectionMatch,
    InjectionResult,
    PromptInjectionDetector,
    PromptInjectionGuardrail,
)
from meshflow.security.secrets import (
    SecretMatch,
    SecretScanResult,
    SecretScanner,
    SecretScanGuardrail,
)
# ── OIDC / SSO ────────────────────────────────────────────────────────────────
from meshflow.security.oidc import (
    OIDCConfig,
    OIDCPrincipal,
    OIDCValidator,
    OIDCMiddleware,
    OIDCError,
    TokenExpiredError,
    TokenAudienceMismatchError,
    TokenIssuerMismatchError,
    TokenSignatureError,
    JWKSCache,
    get_oidc_middleware,
    setup_oidc_middleware,
    reset_oidc_middleware,
)
from meshflow.security.sso_providers import (
    OktaConfig,
    Auth0Config,
    AzureADConfig,
    GoogleWorkspaceConfig,
    KeycloakConfig,
)
from meshflow.intelligence.embedding import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    SentenceTransformerProvider,
    cosine_similarity,
    get_embedding_provider,
    reset_embedding_provider,
    embed_text,
)
from meshflow.intelligence.semantic_memory import (
    SemanticMemoryEntry,
    SemanticSearchResult,
    SemanticMemoryStore,
)
from meshflow.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    CircuitBreakerStats,
    CircuitBreakerRegistry,
    CircuitBreakerRecord,
    CircuitBreakerStore,
    get_circuit_registry,
    reset_circuit_registry,
)
from meshflow.observability.webhook_queue import (
    WebhookDelivery,
    WebhookRetryQueue,
    WebhookReliableDeliverer,
)
from meshflow.alerting import (
    MetricPoint,
    MetricStore,
    AlertRule,
    AlertRecord,
    AlertRuleStore,
    AlertStore,
    AlertEngine,
)
from meshflow.locking import (
    LockRecord,
    LockStore,
    DistributedLock,
    LockAcquisitionError,
)
from meshflow.lineage import LineageNode, LineageEdge, LineageGraph
from meshflow.identity import (
    AgentIdentity,
    AgentToken,
    IdentityStore,
    sign_token,
    verify_token,
    decode_token,
)
from meshflow.canary import (
    CanaryConfig,
    CanaryOutcome,
    CanaryStats,
    CanaryStore,
    CanaryRouter,
)
from meshflow.flags import (
    FlagDefinition,
    FlagRule,
    FlagStore,
    FlagEvaluator,
)
from meshflow.vault import VaultSecret, VaultAuditLog, VaultStore, AWSSecretsProvider, HashiCorpVaultProvider, EnvSecretsProvider
from meshflow.tenant import Tenant, TenantContext, TenantStore, TenantGuard, scoped_db_path
from meshflow.tracing import TraceContext, Span, SpanKind, SpanStatus, TraceStore, Tracer
from meshflow.policy import (
    PolicyAction,
    ConditionOp,
    PolicyCondition,
    PolicyRule,
    PolicyDecision,
    PolicyStore,
    PolicyEngine,
    PolicyLoader,
)
from meshflow.sla import SLAContract, LatencyRecord, SLAStats, SLABreach, SLAStore, SLATracker
from meshflow.snapshot import SnapshotManifest, SnapshotBundle, SnapshotExporter
from meshflow.security.dasc_gate import (
    AutoRiskClassifier,
    TaintGraph,
    CompensationExecutor,
    AuditLedger,
    DascGate,
)
from meshflow.studio.trace_server import TraceServer
from meshflow.deploy.doctor import Doctor, DoctorReport, CheckResult, CheckStatus
from meshflow.deploy.env_generator import EnvGenerator, ValidationIssue
from meshflow.deploy.deployer import DockerDeployer, DeployResult
from meshflow.agents.rag_budget import RAGTokenBudget, KnowledgeBudgetResult
from meshflow.core.context_pruner import SlidingWindowPruner, SummaryPruner, PruneResult
from meshflow.intelligence.cross_session import CrossSessionMemoryStore, MemoryEntry as CrossSessionEntry
from meshflow.agents.adaptive import AdaptiveAgent
from meshflow.agents.debate import DebatePanel, DebateNode, DebateResult
from meshflow.agents.early_exit import EarlyExitAgent
from meshflow.agents.context_dedup import ContextDeduplicator
from meshflow.optimization.planner import TokenBudgetPlanner, ModelSizingAdvisor
from meshflow.core.time_travel import RewindEngine, RewindResult, StepSnapshot
from meshflow.eval.pareto import ParetoAnalyzer, ModelBenchmark, BenchmarkRun
from meshflow.agents.model_router import ModelRouter, RouterConfig, RoutingDecision
from meshflow.agents.critic import CriticAgent, CriticResult, CriticTurn
from meshflow.tools.tool_summarizer import ToolOutputSummarizer, CompressionRecord
from meshflow.core.branch_compare import BranchCompare, ForkConfig, ForkResult, CompareResult
from meshflow.agents.role_router import RoleRouter, AgentSpec
from meshflow.intelligence.rag_pipeline import (
    LLMRanker, HybridRetriever, SelfCorrectingRAG, RankedDoc, RAGAnswer,
)
from meshflow.registry.curated_templates import (
    CURATED_TEMPLATES, load_curated_library, template_by_name, templates_by_tag,
)
from meshflow.core.workflow_decorator import workflow, WorkflowProxy
from meshflow.batch.anthropic_batch import (
    AnthropicBatchClient, BatchRequest, BatchResult, BatchJob, batch_agent_tasks,
)
from meshflow.core.tool_intercept import (
    ToolCallEvent,
    ToolCallDecision,
    ToolCallInterceptor,
    AllowListInterceptor,
    PiiScanInterceptor,
    PolicyToolCallInterceptor,
    ChainedInterceptor,
)
from meshflow.integrations.haystack import (
    HaystackStepAdapter,
    HaystackResult,
    governed_haystack_pipeline,
)
from meshflow.proxy.openai_proxy import MeshFlowProxy, ProxyToolCallEvent, ProxyDecision
from meshflow.proxy.http_server import MeshFlowHTTPProxy
from meshflow.integrations.anthropic import (
    meshflow_as_anthropic_tool,
    meshflow_tool_handler,
    meshflow_tool_result_block,
)
from meshflow.integrations.openai import meshflow_as_openai_tool

__version__ = "1.9.1"
__all__ = [
    # ── Agent creation ────────────────────────────────────────────────────────
    "Agent",
    "token_budget",
    "Team",
    "MessageBus",
    "agents",
    # ── Task + Crew (CrewAI-compatible) ───────────────────────────────────────
    "Task",
    "TaskOutput",
    "Crew",
    "CrewOutput",
    "Process",
    # ── Skills library ────────────────────────────────────────────────────────
    "Skill",
    "SKILLS",
    "skill_prompt",
    "list_skills",
    # ── Conversational multi-agent ────────────────────────────────────────────
    "GroupChat",
    "GroupChatManager",
    "ConversationResult",
    # ── Typed state graph (LangGraph-style) ───────────────────────────────────
    "StateGraph",
    "Channel",
    "END",
    "START",
    "add",
    "last",
    "first",
    "node",
    "interrupt",
    "Command",
    "Interrupt",
    "Send",
    "MemorySaver",
    "SqliteSaver",
    # ── Flows — event-driven decorator API (CrewAI Flows parity) ─────────
    "Flow",
    "FlowState",
    "FlowResult",
    "flow_start",
    "flow_listen",
    "flow_router",
    # ── Prebuilt agent graphs (LangGraph-style) ───────────────────────────
    "MessagesState",
    "ToolNode",
    "create_react_agent",
    "create_tool_calling_agent",
    # ── Declarative config ────────────────────────────────────────────────────
    "MeshFlowConfig",
    "load",
    "loads",
    # ── Tool ecosystem ────────────────────────────────────────────────────────
    "Tool",
    "ToolRegistry",
    "tool",
    "global_registry",
    # ── Orchestration ─────────────────────────────────────────────────────────
    "Mesh",
    "MeshEvent",
    "GovernedApp",
    "govern",
    # ── Universal node ────────────────────────────────────────────────────────
    "MeshNode",
    "NodeInput",
    "NodeOutput",
    "NodeKind",
    "core_contract_schemas",
    # ── Workflow ──────────────────────────────────────────────────────────────
    "WorkflowDefinition",
    "WorkflowResult",
    "HumanDecision",
    "WorkflowEventBus",
    "Workflow",
    "CostCap",
    # ── Kernel ────────────────────────────────────────────────────────────────
    "StepRuntime",
    "RuntimeOutcome",
    # ── Ledger ────────────────────────────────────────────────────────────────
    "ReplayLedger",
    "RunDiff",
    "LedgerArchiveResult",
    "LedgerBackend",
    "SQLiteLedgerBackend",
    "PostgresLedgerBackend",
    "S3LedgerArchiveBackend",
    # ── Policy + schemas ──────────────────────────────────────────────────────
    "Policy",
    "PolicyMode",
    "policy_for_mode",
    "HumanInLoopConfig",
    "AgentRole",
    "RiskTier",
    "RunStatus",
    "RunResult",
    "Evidence",
    "Intent",
    "Message",
    # ── Evaluation framework ──────────────────────────────────────────────────
    "EvalSuite",
    "EvalScenario",
    "EvalResult",
    "ScenarioResult",
    "run_eval",
    "EvalBaseline",
    "BaselineDiff",
    # ── Agent pool ────────────────────────────────────────────────────────────
    "AgentPool",
    "PoolStats",
    "register_pool",
    "deregister_pool",
    # ── Plugin system ─────────────────────────────────────────────────────────
    "PluginInfo",
    "discover_plugins",
    "load_plugin",
    "verify_plugin",
    # ── MCP server ────────────────────────────────────────────────────────────
    "MCPServer",
    "MCPToolEntry",
    "mcp_from_config",
    # ── HTTP client SDK ───────────────────────────────────────────────────────
    "MeshFlowClient",
    "ClientPolicyConfig",
    # ── Agentic loops ─────────────────────────────────────────────────────────
    "ReActAgent",
    "ReActResult",
    "ThoughtStep",
    # ── Smart provider routing ─────────────────────────────────────────────────
    "ProviderRouter",
    "auto_provider",
    "auto_model",
    # ── LLM providers — any LLM, zero friction ───────────────────────────────
    "EchoProvider",           # offline / test / MESHFLOW_MOCK=1
    "AnthropicProvider",      # ANTHROPIC_API_KEY
    "OpenAICompatibleProvider",  # OPENAI_API_KEY or any OpenAI-compat endpoint
    "GeminiProvider",         # GOOGLE_API_KEY
    "BedrockProvider",        # AWS credentials
    "AzureOpenAIProvider",    # AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT
    "OllamaProvider",         # local Ollama, no API key
    "LiteLLMProvider",        # 100+ models via LiteLLM
    "LLM",                    # unified entry point: LLM("gpt-4o") / LLM("llama3.2")
    "model_to_provider",      # infer provider from model name string
    "provider_for",           # factory: provider_for("ollama", model="llama3.2")
    "auto_detect_provider",   # auto-pick based on available keys / services
    "PROVIDER_NAMES",         # list of all supported provider names
    # ── 4-tier memory ─────────────────────────────────────────────────────────
    "AgentMemory",
    "MemoryItem",
    # ── Native RAG / Knowledge ────────────────────────────────────────────────
    "VectorStore",
    "KnowledgeSource",
    "AgentKnowledge",
    # ── Streaming ─────────────────────────────────────────────────────────────
    "StreamChunk",
    "tokens",
    "cost_events",
    "filter_stream",
    "task_outputs",
    # ── Multi-agent patterns ──────────────────────────────────────────────────
    "Supervisor",
    "SupervisorResult",
    "AdversarialTeam",
    "AdversarialResult",
    # ── Stateful sessions ─────────────────────────────────────────────────────
    "AgentSession",
    "SessionResult",
    "Turn",
    # ── Compliance profiles ───────────────────────────────────────────────────
    "ComplianceProfile",
    "compliance_profile",
    "list_profiles",
    # ── Event sourcing projections ────────────────────────────────────────────
    "AuditTrailProjection",
    "NodeLatencyProjection",
    "NodeLatencyStats",
    "PolicyViolationProjection",
    "WorkflowSummaryProjection",
    "WorkflowSummary",
    "EventProjector",
    # ── Durable execution (checkpoint/resume) ────────────────────────────────
    "DurableWorkflowExecutor",
    # ── Governed tool registry ────────────────────────────────────────────────
    "GovernedToolRegistry",
    "ToolPermission",
    "ToolAuditEntry",
    "PermissionDeniedError",
    "ToolNotFoundError",
    # ── Framework adapters (low-level — prefer Agent / govern()) ──────────────
    "from_crewai",
    "from_autogen",
    "from_langgraph",
    "from_callable",
    # ── SwarmTRM neural consensus (requires meshflow[swarm]) ──────────────────
    "SwarmNode",
    "swarm_verifier",
    "register_swarm_domain",
    "swarm_available_domains",
    "VerificationResult",
    "DeterministicVerifier",
    # ── Sensitive data detection ───────────────────────────────────────────────
    "SensitiveDataDetector",
    "SensitiveMatch",
    "get_sensitive_detector",
    # ── Guardrails (input/output validation at every agent + node) ────────────
    "Guardrail",
    "GuardrailResult",
    "GuardrailStack",
    "GuardrailViolation",
    "PIIBlockGuardrail",
    "ConfidenceGuardrail",
    "LengthGuardrail",
    "ToxicityGuardrail",
    "JSONSchemaGuardrail",
    "RegexGuardrail",
    "KeywordBlockGuardrail",
    "CostCapGuardrail",
    "CustomGuardrail",
    # ── Model health tracking ─────────────────────────────────────────────────
    "ModelHealthTracker",
    "ModelHealthSummary",
    "get_health_tracker",
    # ── Workflow analytics ────────────────────────────────────────────────────
    "WorkflowAnalytics",
    "RunSummary",
    # ── Background task queue ─────────────────────────────────────────────────
    "TaskQueue",
    "QueueWorker",
    "TaskItem",
    "TaskStatus",
    # ── Code interpreter + cloud sandbox providers ───────────────────────────
    "CodeInterpreter",
    "CodeResult",
    "E2BSandboxProvider",
    "ModalSandboxProvider",
    "SandboxRouter",
    # ── A2A protocol ─────────────────────────────────────────────────────────
    "AgentCard",
    "A2AMessage",
    "A2AResponse",
    "A2AClient",
    "A2AServer",
    # ── Multi-modal inputs ────────────────────────────────────────────────────
    "ImageInput",
    "DocumentInput",
    "AudioInput",
    "MultiModalInput",
    "build_multimodal_message",
    # ── Structured output ─────────────────────────────────────────────────────
    "StructuredOutputResult",
    "StructuredOutputError",
    "StructuredOutputParser",
    "StructuredAgent",
    # ── Persistent memory backends ────────────────────────────────────────────
    "MemoryBackend",
    "InMemoryBackend",
    "SQLiteMemoryBackend",
    "PostgresMemoryBackend",
    "snapshot_from_memory",
    "restore_memory",
    # ── LLM response cache ────────────────────────────────────────────────────
    "CacheEntry",
    "LLMCache",
    "InMemoryCache",
    "SQLiteCache",
    "CachedProvider",
    # ── Self-healing orchestration ────────────────────────────────────────────
    "HealingPolicy",
    "HealingStrategy",
    "HealingResult",
    "run_with_healing",
    # ── Prompt management ─────────────────────────────────────────────────────
    "PromptVersion",
    "PromptTemplate",
    "PromptRegistry",
    "PromptABTest",
    # ── Fine-tuning data export ───────────────────────────────────────────────
    "FinetuneExporter",
    "ExportFormat",
    "TraceRecord",
    "ExportFilter",
    # ── MCP client (consume external MCP servers) ─────────────────────────────
    "MCPClientSession",
    "MCPRemoteTool",
    "MCPClient",
    "MCPClientError",
    # ── Handoff pattern (peer-to-peer agent transfer) ─────────────────────────
    "HandoffConfig",
    "HandoffLink",
    "HandoffResult",
    "run_with_handoffs",
    # ── A2A task lifecycle (full state machine + SSE) ─────────────────────────
    "A2ATask",
    "A2ATaskStore",
    "TaskState",
    "TaskEventQueue",
    # ── Cost budgets + quota enforcement ─────────────────────────────────────
    "BudgetAccount",
    "BudgetSpend",
    "BudgetCheckResult",
    "BudgetStore",
    "BudgetGuardrail",
    "get_budget_store",
    "reset_budget_store",
    "budget_period_key",
    # ── Prometheus metrics + OTLP wire export ────────────────────────────────
    "MetricsCollector",
    "PhoenixExporter",
    "auto_instrument",
    "OTELExporter",
    "get_global_exporter",
    "set_global_exporter",
    "reset_global_exporter",
    # ── OpenTelemetry GenAI semantic conventions ──────────────────────────────
    "GenAI",
    "MF",
    "GenAISpanRecord",
    "SpanStore",
    "configure_telemetry",
    "get_span_store",
    "record_agent_step",
    "record_handoff",
    "record_tool_call",
    "record_guardrail",
    "record_healing_attempt",
    "otel_span",
    "otel_is_enabled",
    # ── Agent registry (publish, discover, govern) ────────────────────────────
    "AgentManifest",
    "AgentRegistry",
    "get_registry",
    # ── Production eval: feedback loop + shadow runner ────────────────────────
    "FeedbackRecord",
    "FeedbackStore",
    "ShadowResult",
    "shadow_run",
    "RegressionAlert",
    "RegressionDetector",
    # ── Streaming v2 — backpressure, multiplexer, partial structured output ───
    "BackpressureQueue",
    "BackpressureStrategy",
    "StreamMultiplexer",
    "Subscription",
    "PartialStructuredOutput",
    "PartialOutputChunk",
    "stream_structured",
    "RunStreamHub",
    "get_run_hub",
    "reset_run_hub",
    # ── Production deployment — doctor, env generator, Docker deployer ────────
    "Doctor",
    "DoctorReport",
    "CheckResult",
    "CheckStatus",
    "EnvGenerator",
    "ValidationIssue",
    "DockerDeployer",
    "DeployResult",
    # ── Memory v2 — consolidation, team workspace ─────────────────────────────
    "MemoryConsolidator",
    "ConsolidationReport",
    "TeamWorkspace",
    "WorkspaceSummary",
    # ── Eval framework v2 — LLM judge, conversation eval, A/B testing ─────────
    "LLMJudge",
    "JudgeScore",
    "JudgeSuiteResult",
    "ConversationEval",
    "ConversationCase",
    "EvalTurn",
    "EvalConversationResult",
    "TurnResult",
    "ABTest",
    "ABVariant",
    "ABTestResult",
    "ABTurnResult",
    "QualityGate",
    "QualityReport",
    # ── Cron scheduler ────────────────────────────────────────────────────────
    "CronExpression",
    "CronScheduler",
    "ScheduledTask",
    "ScheduleRun",
    "ScheduleStore",
    # ── Per-agent / per-team rate limiting ────────────────────────────────────
    "RateLimitPolicy",
    "RateLimitResult",
    "RateLimitStore",
    "RateLimitPolicyDB",
    "RateLimitGuardrail",
    "TeamRateLimitGuardrail",
    "get_rate_limit_store",
    "reset_rate_limit_store",
    # ── Prompt injection detection ────────────────────────────────────────────
    "InjectionMatch",
    "InjectionResult",
    "PromptInjectionDetector",
    "PromptInjectionGuardrail",
    # ── Secret & credential scanner ───────────────────────────────────────────
    "SecretMatch",
    "SecretScanResult",
    "SecretScanner",
    "SecretScanGuardrail",
    # ── OIDC / SSO ────────────────────────────────────────────────────────────
    "OIDCConfig",
    "OIDCPrincipal",
    "OIDCValidator",
    "OIDCMiddleware",
    "OIDCError",
    "TokenExpiredError",
    "TokenAudienceMismatchError",
    "TokenIssuerMismatchError",
    "TokenSignatureError",
    "JWKSCache",
    "get_oidc_middleware",
    "setup_oidc_middleware",
    "reset_oidc_middleware",
    "OktaConfig",
    "Auth0Config",
    "AzureADConfig",
    "GoogleWorkspaceConfig",
    "KeycloakConfig",
    # ── Semantic memory / embedding ───────────────────────────────────────────
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "SentenceTransformerProvider",
    "cosine_similarity",
    "get_embedding_provider",
    "reset_embedding_provider",
    "embed_text",
    "SemanticMemoryEntry",
    "SemanticSearchResult",
    "SemanticMemoryStore",
    # ── Circuit breaker / resilience ──────────────────────────────────────────
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpenError",
    "CircuitBreakerState",
    "CircuitBreakerStats",
    "CircuitBreakerRegistry",
    "CircuitBreakerRecord",
    "CircuitBreakerStore",
    "get_circuit_registry",
    "reset_circuit_registry",
    # ── Durable webhook retry queue ───────────────────────────────────────────
    "WebhookDelivery",
    "WebhookRetryQueue",
    "WebhookReliableDeliverer",
    # ── Alert engine + metric store ───────────────────────────────────────────
    "MetricPoint",
    "MetricStore",
    "AlertRule",
    "AlertRecord",
    "AlertRuleStore",
    "AlertStore",
    "AlertEngine",
    # ── Distributed locking ───────────────────────────────────────────────────
    "LockRecord",
    "LockStore",
    "DistributedLock",
    "LockAcquisitionError",
    # ── Data lineage (GDPR Article 30) ────────────────────────────────────────
    "LineageNode",
    "LineageEdge",
    "LineageGraph",
    # ── Agent identity + zero-trust auth ─────────────────────────────────────
    "AgentIdentity",
    "AgentToken",
    "IdentityStore",
    "sign_token",
    "verify_token",
    "decode_token",
    # ── Canary agent router ───────────────────────────────────────────────────
    "CanaryConfig",
    "CanaryOutcome",
    "CanaryStats",
    "CanaryStore",
    "CanaryRouter",
    # ── Feature flags ─────────────────────────────────────────────────────────
    "FlagDefinition",
    "FlagRule",
    "FlagStore",
    "FlagEvaluator",
    # ── Secret vault ──────────────────────────────────────────────────────────
    "VaultSecret",
    "VaultAuditLog",
    "VaultStore",
    "AWSSecretsProvider",
    "HashiCorpVaultProvider",
    "EnvSecretsProvider",
    # ── Tenant isolation ──────────────────────────────────────────────────────
    "Tenant",
    "TenantContext",
    "TenantStore",
    "TenantGuard",
    "scoped_db_path",
    # ── Distributed tracing ───────────────────────────────────────────────────
    "TraceContext",
    "Span",
    "SpanKind",
    "SpanStatus",
    "TraceStore",
    "Tracer",
    # ── Policy-as-code engine ─────────────────────────────────────────────────
    "PolicyAction",
    "ConditionOp",
    "PolicyCondition",
    "PolicyRule",
    "PolicyDecision",
    "PolicyStore",
    "PolicyEngine",
    "PolicyLoader",
    # ── Agent SLA tracker ─────────────────────────────────────────────────────
    "SLAContract",
    "LatencyRecord",
    "SLAStats",
    "SLABreach",
    "SLAStore",
    "SLATracker",
    # ── Compliance snapshot ───────────────────────────────────────────────────
    "SnapshotManifest",
    "SnapshotBundle",
    "SnapshotExporter",
    # ── DASC-core risk governance ─────────────────────────────────────────────
    "AutoRiskClassifier",
    "TaintGraph",
    "CompensationExecutor",
    "AuditLedger",
    "DascGate",
    # ── Visual trace studio ───────────────────────────────────────────────────
    "TraceServer",
    # ── RAG token budget + context window pruning ─────────────────────────────
    "RAGTokenBudget",
    "KnowledgeBudgetResult",
    "SlidingWindowPruner",
    "SummaryPruner",
    "PruneResult",
    # ── Cross-session memory ───────────────────────────────────────────────────
    "CrossSessionMemoryStore",
    "CrossSessionEntry",
    # ── Cloud managed identity providers ──────────────────────────────────────
    "AzureIdentityProvider",
    "BedrockIAMProvider",
    "VertexAIProvider",
    # ── Agent template marketplace ─────────────────────────────────────────────
    "AgentTemplate",
    "TemplateRegistry",
    "MarketplaceClient",
    "MarketplaceServer",
    # ── @workflow decorator + Anthropic Batch API ─────────────────────────────
    "workflow",
    "WorkflowProxy",
    "AnthropicBatchClient",
    "BatchRequest",
    "BatchResult",
    "BatchJob",
    "batch_agent_tasks",
    # ── Smart routing, critic agent, tool output compression ──────────────────
    "ModelRouter",
    "RouterConfig",
    "RoutingDecision",
    "CriticAgent",
    "CriticResult",
    "CriticTurn",
    "ToolOutputSummarizer",
    "CompressionRecord",
    # ── Branch compare, role router, RAG pipeline depth, curated templates ─────
    "BranchCompare",
    "ForkConfig",
    "ForkResult",
    "CompareResult",
    "RoleRouter",
    "AgentSpec",
    "LLMRanker",
    "HybridRetriever",
    "SelfCorrectingRAG",
    "RankedDoc",
    "RAGAnswer",
    "CURATED_TEMPLATES",
    "load_curated_library",
    "template_by_name",
    "templates_by_tag",
    # ── Adaptive, debate, early-exit, and dedup agent patterns ────────────────
    "AdaptiveAgent",
    "DebatePanel",
    "DebateNode",
    "DebateResult",
    "EarlyExitAgent",
    "ContextDeduplicator",
    "TokenBudgetPlanner",
    "ModelSizingAdvisor",
    "RewindEngine",
    "RewindResult",
    "StepSnapshot",
    "ParetoAnalyzer",
    "ModelBenchmark",
    "BenchmarkRun",
    # ── SIEM streaming ────────────────────────────────────────────────────────
    "SIEMStreamer",
    "SplunkHECBackend",
    "DatadogLogsBackend",
    "GenericHTTPBackend",
    "get_siem_streamer",
    # ── Red-team testing ──────────────────────────────────────────────────────
    "RedTeamSuite",
    "RedTeamReport",
    "RedTeamProbeResult",
    "RedTeamProbe",
    # ── Blue/green deployments ────────────────────────────────────────────────
    "BlueGreenRouter",
    "AgentDeployment",
    "PromotionResult",
    "DeploymentStore",
    # ── Zero Trust framework ──────────────────────────────────────────────────
    "ZeroTrustPolicy",
    "ZeroTrustTier",
    "ZT_FOUNDATION",
    "ZT_ENTERPRISE",
    "ZT_ADVANCED",
    "SpotlightingGuardrail",
    "SpotlightContext",
    "JITPrivilegeManager",
    "PrivilegeGrant",
    "PrivilegeExpiredError",
    "AIBillOfMaterials",
    "BOMModelComponent",
    "BOMToolComponent",
    "ContinuousAuthorizationEngine",
    "AuthorizationContext",
    "AuthDecision",
    "ZeroTrustOrchestrator",
    "ZeroTrustSession",
    "ZeroTrustRunResult",
    # ── Worker pool ───────────────────────────────────────────────────────────
    "WorkerPool",
    "WorkerPoolConfig",
    # ── Tool-call enforcement ─────────────────────────────────────────────────
    "ToolCallEvent",
    "ToolCallDecision",
    "ToolCallInterceptor",
    "AllowListInterceptor",
    "PiiScanInterceptor",
    "PolicyToolCallInterceptor",
    "ChainedInterceptor",
    # ── Haystack integration ──────────────────────────────────────────────────
    "HaystackStepAdapter",
    "HaystackResult",
    "governed_haystack_pipeline",
    # ── OpenAI-compatible proxy layer ─────────────────────────────────────────
    "MeshFlowProxy",
    "ProxyToolCallEvent",
    "ProxyDecision",
    # ── HTTP proxy server (language-agnostic enforcement) ─────────────────────
    "MeshFlowHTTPProxy",
    # ── Anthropic tool-use integration ────────────────────────────────────────
    "meshflow_as_anthropic_tool",
    "meshflow_tool_handler",
    "meshflow_tool_result_block",
    # ── OpenAI tool integration ────────────────────────────────────────────────
    "meshflow_as_openai_tool",
]
