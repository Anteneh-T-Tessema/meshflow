"""meshflow.zero_trust — Zero Trust security framework for AI agents.

Implements the Anthropic "Zero Trust for AI Agents" framework across three
maturity tiers:

  Foundation  — minimum viable: crypto identity, deny-by-default RBAC,
                action logging, input validation
  Enterprise  — target for production: mTLS, ABAC, sandboxing, immutable logs,
                OTEL tracing, behavior baselines, spotlighting, PII filters,
                signed configs, AI-BOM
  Advanced    — high-risk/regulated: hardware-bound credentials, JIT/JEA,
                hardware isolation, SIEM streaming, ML behavioral analysis,
                continuous authorization, constitutional classifiers

Quick start::

    from meshflow.zero_trust import ZeroTrustOrchestrator, ZeroTrustTier

    zt = ZeroTrustOrchestrator.for_tier(ZeroTrustTier.ENTERPRISE)
    result = await zt.run_agent(my_agent, "Analyse Q1 contracts")
    print(result.trust_score, result.violations)

Policy-first::

    from meshflow.zero_trust import ZeroTrustPolicy, ZeroTrustTier, ENTERPRISE

    # Pre-built tier defaults
    policy = ENTERPRISE                                 # all Enterprise controls on
    policy = ZeroTrustPolicy.for_regulation("hipaa")   # HIPAA-grade

    # Custom
    policy = ZeroTrustPolicy(
        tier=ZeroTrustTier.ENTERPRISE,
        jit_privilege=True,   # promote one control to Advanced
        jit_ttl_seconds=60,
    )

Spotlighting::

    from meshflow.zero_trust import SpotlightingGuardrail, SpotlightContext

    guardrail = SpotlightingGuardrail(strategy="xml_tags")
    ctx = SpotlightContext(strategy="datamark")
    wrapped = ctx.wrap("User uploaded doc: ...")

JIT privileges::

    from meshflow.zero_trust import JITPrivilegeManager

    mgr = JITPrivilegeManager(default_ttl_seconds=120)
    grant = mgr.request("agent-abc", permissions=["read:contracts"])
    mgr.is_allowed(grant.grant_id, "read:contracts")   # True
    mgr.revoke(grant.grant_id)

AI Bill of Materials::

    from meshflow.zero_trust import AIBillOfMaterials
    bom = AIBillOfMaterials.from_meshflow_project()
    print(bom.risk_summary())

Continuous authorization::

    from meshflow.zero_trust import ContinuousAuthorizationEngine
    engine = ContinuousAuthorizationEngine()
    engine.register("agent-xyz", permissions=["read:*", "write:summary"])
    decision = engine.authorize("agent-xyz", "read:contracts")
"""

from meshflow.zero_trust.policy import (
    ZeroTrustPolicy,
    ZeroTrustTier,
    FOUNDATION,
    ENTERPRISE,
    ADVANCED,
)
from meshflow.zero_trust.spotlight import (
    SpotlightingGuardrail,
    SpotlightContext,
    SpotlightStrategy,
)
from meshflow.zero_trust.jit import (
    JITPrivilegeManager,
    PrivilegeGrant,
    PrivilegeExpiredError,
    MaxGrantsExceededError,
    get_default_manager,
)
from meshflow.zero_trust.bom import (
    AIBillOfMaterials,
    ModelComponent,
    ToolComponent,
    DependencyComponent,
)
from meshflow.zero_trust.continuous_auth import (
    ContinuousAuthorizationEngine,
    AuthorizationContext,
    AuthDecision,
)
from meshflow.zero_trust.orchestrator import (
    ZeroTrustOrchestrator,
    ZeroTrustSession,
    ZeroTrustRunResult,
)

__all__ = [
    # Policy
    "ZeroTrustPolicy",
    "ZeroTrustTier",
    "FOUNDATION",
    "ENTERPRISE",
    "ADVANCED",
    # Spotlighting
    "SpotlightingGuardrail",
    "SpotlightContext",
    "SpotlightStrategy",
    # JIT
    "JITPrivilegeManager",
    "PrivilegeGrant",
    "PrivilegeExpiredError",
    "MaxGrantsExceededError",
    "get_default_manager",
    # AI-BOM
    "AIBillOfMaterials",
    "ModelComponent",
    "ToolComponent",
    "DependencyComponent",
    # Continuous auth
    "ContinuousAuthorizationEngine",
    "AuthorizationContext",
    "AuthDecision",
    # Orchestrator
    "ZeroTrustOrchestrator",
    "ZeroTrustSession",
    "ZeroTrustRunResult",
]
