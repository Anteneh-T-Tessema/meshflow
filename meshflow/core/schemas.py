"""Core data models for MeshFlow — all layers share these types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Callable, Literal


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Enumerations ──────────────────────────────────────────────────────────────


class PolicyMode(str, Enum):
    """Progressive governance modes.

    The mode is a product-level contract: developers can start with low-friction
    tracing in ``dev`` and opt into stricter controls as workflow risk rises.
    """

    DEV = "dev"
    STANDARD = "standard"
    REGULATED = "regulated"
    LEGAL_CRITICAL = "legal-critical"
    HIPAA = "hipaa"


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    RESEARCHER = "researcher"
    EXECUTOR = "executor"
    CRITIC = "critic"
    GUARDIAN = "guardian"


class RiskTier(IntEnum):
    """dasc-core compatible risk tiers — auto-classified, never self-declared."""

    READ_ONLY = 1  # pure reads, no side effects
    INTERNAL = 2  # mutates internal mesh state only
    EXTERNAL_IO = 3  # network, filesystem, external API
    IRREVERSIBLE = 4  # deletes, deploys, financial transactions


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"  # human-in-loop pause
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"  # budget / circuit breaker


class EventKind(str, Enum):
    TOKEN_DELTA = "token_delta"  # per-token chunk from the LLM
    STEP_START = "step_start"
    STEP_END = "step_end"
    WORKFLOW_END = "workflow_end"
    HITL_PAUSE = "hitl_pause"
    ERROR = "error"


@dataclass
class TokenChunk:
    """A single token delta from a streaming LLM response."""

    text: str
    agent_id: str
    step_id: str
    run_id: str = ""


class ActionVerdict(str, Enum):
    COMMIT = "commit"
    REJECT = "reject"
    ESCALATE = "escalate"  # requires human approval


class InjectionResult(str, Enum):
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


# ── Primitive building blocks ─────────────────────────────────────────────────


@dataclass
class Evidence:
    """A unit of information with explicit trust provenance.

    Every retrieved chunk (RAG, web, memory) is typed as Evidence so the
    IFC taint check can evaluate source trust before allowing downstream use.
    """

    content: str
    source: str
    trust_level: Literal["trusted", "internal", "untrusted"] = "untrusted"
    retrieved_at: datetime = field(default_factory=_now)
    source_hash: str = ""

    def is_trusted(self) -> bool:
        return self.trust_level == "trusted"


@dataclass
class CompensationPlan:
    """Rollback/compensation actions run if the associated Intent is rejected."""

    steps: list[str]
    rollback_fn: Callable[[], Any] | None = None
    description: str = ""


@dataclass
class Intent:
    """Declared action from an agent — evaluated by dasc-gate before execution."""

    action: str
    payload: dict[str, Any]
    evidence: list[Evidence]
    agent_id: str
    agent_did: str = ""
    risk_tier: RiskTier = RiskTier.READ_ONLY  # overridden by AutoRiskClassifier
    effective_tier: RiskTier = RiskTier.READ_ONLY  # set by classifier, authoritative
    compensation: CompensationPlan | None = None
    intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=_now)
    tainted: bool = False  # IFC taint propagated from evidence


@dataclass
class Message:
    """Typed inter-agent message — all handoffs use this, never raw strings."""

    sender_id: str
    receiver_id: str
    content: str
    role: str = "assistant"
    schema_version: str = "1.0"
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    span_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    injection_scan: InjectionResult = InjectionResult.CLEAN


@dataclass
class UncertaintyScore:
    """Propagated uncertainty from the L2.11 layer."""

    raw: float  # agent's self-reported confidence (0–1)
    calibrated: float  # corrected for historical overconfidence
    propagated: float  # after upstream uncertainty multiplication
    consistency: float  # semantic consistency across rephrased queries
    composite: float  # final score used by the router
    should_escalate: bool = False
    should_abort: bool = False


@dataclass
class AgentState:
    """Complete, serialisable state for one agent at one checkpoint."""

    agent_id: str
    role: AgentRole
    did: str = ""
    messages: list[Message] = field(default_factory=list)
    memory_keys: list[str] = field(default_factory=list)
    uncertainty: UncertaintyScore | None = None
    token_count: int = 0
    cost_usd: float = 0.0
    carbon_g: float = 0.0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    risk_score: float = 0.0
    revoked: bool = False


@dataclass
class CheckpointRecord:
    """Immutable snapshot of full mesh state at a graph transition."""

    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    step: int = 0
    agent_states: dict[str, AgentState] = field(default_factory=dict)
    graph_state: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_now)
    hash: str = ""  # SHA-256 of serialised content for tamper detection


@dataclass
class LedgerEntry:
    """Hash-chained audit record — every dasc-gate decision is logged here."""

    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    intent_id: str = ""
    agent_id: str = ""
    agent_did: str = ""
    action: str = ""
    effective_tier: int = 1
    verdict: ActionVerdict = ActionVerdict.COMMIT
    reason: str = ""
    timestamp: datetime = field(default_factory=_now)
    prev_hash: str = ""
    entry_hash: str = ""  # SHA-256(prev_hash + entry content)


@dataclass
class MCPToolCall:
    """Every MCP tool invocation — traced, validated, rate-limited."""

    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    server_uri: str = ""
    agent_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    trace_id: str = ""
    timestamp: datetime = field(default_factory=_now)
    validated: bool = False
    blocked: bool = False
    block_reason: str = ""


@dataclass
class RAGResult:
    """Result of a retrieval operation with quality metrics."""

    query: str
    chunks: list[Evidence]
    retrieval_score: float = 0.0  # RAGAS faithfulness
    answer_relevance: float = 0.0
    context_precision: float = 0.0
    corrective_applied: bool = False
    latency_ms: float = 0.0


@dataclass
class RunResult:
    """Final output of a Mesh.run() call."""

    run_id: str
    status: RunStatus
    output: Any
    agent_states: dict[str, AgentState]
    total_cost_usd: float
    total_tokens: int
    total_carbon_g: float
    duration_s: float
    checkpoints: list[str]  # checkpoint IDs for replay
    ledger_entries: int
    trace_id: str
    error: str = ""
    human_approvals_required: int = 0
    collusion_alerts: int = 0
    drift_alerts: int = 0


# ── Policy ────────────────────────────────────────────────────────────────────


@dataclass
class CircuitBreakerConfig:
    max_retries: int = 3
    failure_window_s: float = 60.0
    failure_threshold: int = 5
    half_open_after_s: float = 30.0


@dataclass
class HumanInLoopConfig:
    enabled: bool = False
    tier_threshold: RiskTier = RiskTier.IRREVERSIBLE
    timeout_s: float = 86400.0  # 24 hours — durable pause
    approval_webhook: str = ""


@dataclass
class Policy:
    """Single declaration point for all mesh-level constraints."""

    mode: PolicyMode = PolicyMode.STANDARD
    budget_usd: float = 1.0
    budget_tokens: int = 500_000
    timeout_s: float = 300.0
    max_steps: int = 50
    deterministic_gate: bool = True  # enable dasc-gate (L2.5)
    validate_handoffs: bool = True  # enable dual-judge on handoffs
    human_in_loop: HumanInLoopConfig = field(default_factory=HumanInLoopConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    enable_guardian: bool = True
    enable_collusion_audit: bool = True
    enable_uncertainty: bool = True
    enable_environmental: bool = False  # MARLIN eco-optimisation
    enable_cross_run_learning: bool = False  # CORAL cross-run memory
    carbon_budget_g: float = 500.0
    model_tier_map: dict[AgentRole, str] = field(
        default_factory=lambda: {
            AgentRole.ORCHESTRATOR: "claude-opus-4-7",
            AgentRole.PLANNER: "claude-sonnet-4-6",
            AgentRole.RESEARCHER: "claude-sonnet-4-6",
            AgentRole.EXECUTOR: "claude-haiku-4-5-20251001",
            AgentRole.CRITIC: "claude-sonnet-4-6",
            AgentRole.GUARDIAN: "claude-haiku-4-5-20251001",
        }
    )
    require_citations: bool = False
    require_evidence: bool = False
    require_human_review: bool = False
    immutable_audit: bool = False
    max_output_chars: int = 0  # 0 = unlimited; > 0 truncates before ledger write
    scrub_phi: bool = False  # redact PHI patterns before ledger write (HIPAA)
    max_forecast_usd: float = 0.0  # 0 = no pre-run gate; > 0 → abort if forecast exceeds this
    max_replans: int = 3


def policy_for_mode(
    mode: PolicyMode | str = PolicyMode.STANDARD,
    **overrides: Any,
) -> Policy:
    """Build a ``Policy`` from a progressive governance mode.

    ``dev`` keeps the on-ramp light. ``standard`` enables normal production
    audit. ``regulated`` adds durable review gates and stronger audit defaults.
    ``legal-critical`` requires evidence, citations, human review, and immutable
    audit as product-level expectations.
    """
    if isinstance(mode, str):
        mode = PolicyMode(mode)

    if mode == PolicyMode.DEV:
        policy = Policy(
            mode=mode,
            budget_usd=overrides.pop("budget_usd", 0.25),
            deterministic_gate=False,
            validate_handoffs=False,
            human_in_loop=HumanInLoopConfig(enabled=False),
            enable_guardian=False,
            enable_collusion_audit=False,
            enable_uncertainty=False,
        )
    elif mode == PolicyMode.REGULATED:
        policy = Policy(
            mode=mode,
            budget_usd=overrides.pop("budget_usd", 2.0),
            human_in_loop=HumanInLoopConfig(
                enabled=True,
                tier_threshold=RiskTier.IRREVERSIBLE,
            ),
            enable_guardian=True,
            enable_collusion_audit=True,
            enable_uncertainty=True,
            require_evidence=True,
            require_human_review=True,
            immutable_audit=True,
        )
    elif mode == PolicyMode.LEGAL_CRITICAL:
        policy = Policy(
            mode=mode,
            budget_usd=overrides.pop("budget_usd", 5.0),
            human_in_loop=HumanInLoopConfig(
                enabled=True,
                tier_threshold=RiskTier.EXTERNAL_IO,
            ),
            enable_guardian=True,
            enable_collusion_audit=True,
            enable_uncertainty=True,
            require_citations=True,
            require_evidence=True,
            require_human_review=True,
            immutable_audit=True,
        )
    elif mode == PolicyMode.HIPAA:
        policy = Policy(
            mode=mode,
            budget_usd=overrides.pop("budget_usd", 2.0),
            human_in_loop=HumanInLoopConfig(enabled=True, tier_threshold=RiskTier.IRREVERSIBLE),
            enable_guardian=True,
            enable_collusion_audit=True,
            enable_uncertainty=True,
            require_human_review=True,
            immutable_audit=True,
            scrub_phi=True,
            max_output_chars=0,
        )
    else:
        policy = Policy(mode=mode, budget_usd=overrides.pop("budget_usd", 1.0))

    for key, value in overrides.items():
        if hasattr(policy, key):
            setattr(policy, key, value)
    return policy
