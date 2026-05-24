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
from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.contracts import core_contract_schemas
from meshflow.core.workflow import HumanDecision, WorkflowDefinition, WorkflowResult
from meshflow.core.events import WorkflowEventBus
from meshflow.core.state import StateGraph, END, START, add, last, first, Channel
from meshflow.core.config import MeshFlowConfig, load, loads
from meshflow.core.ledger import (
    LedgerArchiveResult,
    LedgerBackend,
    PostgresLedgerBackend,
    ReplayLedger,
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
    PROVIDER_NAMES,
)
from meshflow.intelligence.memory import AgentMemory, MemoryItem
from meshflow.agents.supervisor import Supervisor, SupervisorResult
from meshflow.agents.adversarial import AdversarialTeam, AdversarialResult
from meshflow.agents.session import AgentSession, SessionResult, Turn
from meshflow.core.compliance import ComplianceProfile, compliance_profile, list_profiles
from meshflow.core.durable import DurableWorkflowExecutor
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
from meshflow.agents.health import (
    ModelHealthTracker,
    ModelHealthSummary,
    get_health_tracker,
)
from meshflow.core.analytics import WorkflowAnalytics, RunSummary
from meshflow.queue import TaskQueue, QueueWorker, TaskItem, TaskStatus

__version__ = "0.23.0"
__all__ = [
    # ── Agent creation ────────────────────────────────────────────────────────
    "Agent",
    "Team",
    "MessageBus",
    "agents",
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
    # ── Kernel ────────────────────────────────────────────────────────────────
    "StepRuntime",
    "RuntimeOutcome",
    # ── Ledger ────────────────────────────────────────────────────────────────
    "ReplayLedger",
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
    "provider_for",           # factory: provider_for("ollama", model="llama3.2")
    "auto_detect_provider",   # auto-pick based on available keys / services
    "PROVIDER_NAMES",         # list of all supported provider names
    # ── 4-tier memory ─────────────────────────────────────────────────────────
    "AgentMemory",
    "MemoryItem",
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
]
