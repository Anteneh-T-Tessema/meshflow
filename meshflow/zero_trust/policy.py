"""Zero Trust policy configuration — Foundation / Enterprise / Advanced tiers.

Maps the Anthropic "Zero Trust for AI Agents" framework to MeshFlow controls.

Usage::

    from meshflow.zero_trust.policy import ZeroTrustPolicy, ZeroTrustTier

    # Pick a tier and get a fully configured policy
    policy = ZeroTrustPolicy.for_tier(ZeroTrustTier.ENTERPRISE)

    # Or build custom
    policy = ZeroTrustPolicy(
        tier=ZeroTrustTier.ENTERPRISE,
        require_mtls=True,
        jit_privilege=True,
        jit_ttl_seconds=300,
        spotlighting=True,
        behavior_baseline=True,
        continuous_auth=True,
        ai_bom=True,
    )

    # Apply to an Agent
    agent = Agent(name="analyst", role="executor", zero_trust=policy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ZeroTrustTier(str, Enum):
    """Security maturity tiers from the Anthropic Zero Trust framework."""

    FOUNDATION = "foundation"   # minimum viable — small deployments, initial rollout
    ENTERPRISE = "enterprise"   # target for most production deployments
    ADVANCED   = "advanced"     # high-risk / regulated / national-security grade


@dataclass
class ZeroTrustPolicy:
    """Unified Zero Trust configuration for an agent or workflow.

    Each field maps to a specific control in the ZT framework document.
    ``for_tier()`` pre-configures all fields to the recommended defaults for
    that tier.
    """

    tier: ZeroTrustTier = ZeroTrustTier.ENTERPRISE

    # ── Identity & Authentication ─────────────────────────────────────────────
    # Foundation: cryptographic agent IDs (DID)
    # Enterprise: X.509-style short-lived tokens with cert lifecycle mgmt
    # Advanced:   hardware-bound credentials (HSM/TPM)
    crypto_identity: bool = True          # always-on: DID minted per agent
    short_lived_tokens: bool = True       # Foundation+: token TTL in minutes
    token_ttl_seconds: int = 600          # 10 min default; reduce for higher tiers
    require_mtls: bool = False            # Enterprise+: mutual TLS on all calls
    hardware_bound: bool = False          # Advanced: HSM/TPM credential binding

    # ── Privilege Management ──────────────────────────────────────────────────
    # Foundation: static least-privilege RBAC (deny-by-default)
    # Enterprise: ABAC with context-aware dynamic privilege adjustment
    # Advanced:   JIT/JEA with automatic expiration
    deny_by_default: bool = True
    abac_context: bool = False            # Enterprise+
    jit_privilege: bool = False           # Advanced / high-risk Enterprise
    jit_ttl_seconds: int = 120            # JIT grant duration (2 min default)
    jit_max_grants: int = 10             # max concurrent JIT grants per agent

    # ── Resource Isolation ────────────────────────────────────────────────────
    # Foundation: identity-based isolation + network segmentation backstop
    # Enterprise: sandboxed execution with gVisor-style syscall filtering
    # Advanced:   hardware isolation (AMD SEV / Intel TDX / microVM)
    identity_isolation: bool = True       # always-on
    sandboxed_execution: bool = False     # Enterprise+
    hardware_isolation: bool = False      # Advanced

    # ── Observability ─────────────────────────────────────────────────────────
    # Foundation: comprehensive action logs with request IDs
    # Enterprise: immutable append-only logs + distributed OTEL tracing
    # Advanced:   real-time SIEM streaming + full provenance chains
    action_logging: bool = True
    immutable_logs: bool = False          # Enterprise+: hash-chain verification
    otel_tracing: bool = False            # Enterprise+
    siem_streaming: bool = False          # Advanced
    full_provenance: bool = False         # Advanced: replay of every decision step

    # ── Behavioral Monitoring ─────────────────────────────────────────────────
    # Foundation: threshold alerts + automated first-pass triage
    # Enterprise: statistical anomaly detection + auto-containment
    # Advanced:   ML-based behavioral analysis + continuous baseline refinement
    behavior_baseline: bool = False       # Enterprise+
    anomaly_detection: bool = False       # Enterprise+
    auto_containment: bool = False        # Enterprise+: session termination on alert
    ml_behavioral: bool = False           # Advanced
    continuous_baseline: bool = False     # Advanced

    # ── Input / Output Controls ───────────────────────────────────────────────
    # Foundation: schema validation + length limits
    # Enterprise: pattern matching for known injection attacks
    # Advanced:   multi-layer validation + spotlighting + constitutional classifiers
    input_validation: bool = True
    injection_detection: bool = False     # Enterprise+
    spotlighting: bool = False            # Advanced (but recommended Enterprise+)
    output_pii_filter: bool = False       # Enterprise+
    output_semantic_filter: bool = False  # Advanced
    hitl_high_risk: bool = False          # Advanced: human approval for risky actions

    # ── Configuration Integrity ───────────────────────────────────────────────
    # Foundation: version-controlled configs
    # Enterprise: signed configs with deployment verification
    # Advanced:   immutable infrastructure + attestation
    config_version_control: bool = True
    config_signing: bool = False          # Enterprise+
    immutable_infra: bool = False         # Advanced

    # ── Supply Chain ─────────────────────────────────────────────────────────
    # All tiers: AI-BOM tracking
    ai_bom: bool = False                  # Enterprise+: full AI-BOM generation
    dependency_audit: bool = False        # Enterprise+: OpenSSF score tracking
    supply_chain_verify: bool = False     # Advanced: runtime integrity attestation

    # ── Governance ───────────────────────────────────────────────────────────
    policy_documentation: bool = True
    formal_governance: bool = False       # Enterprise+
    automated_compliance: bool = False    # Advanced: policy-as-code enforcement

    # ── Continuous Authorization ──────────────────────────────────────────────
    continuous_auth: bool = False         # Advanced: re-evaluate at each action

    # ── Metadata ─────────────────────────────────────────────────────────────
    description: str = ""
    regulation: str = ""                  # e.g. "hipaa", "sox", "gdpr"
    custom: dict = field(default_factory=dict)

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def for_tier(cls, tier: ZeroTrustTier) -> "ZeroTrustPolicy":
        """Return a policy pre-configured to the given tier's recommended defaults."""
        if tier == ZeroTrustTier.FOUNDATION:
            return cls(
                tier=tier,
                crypto_identity=True,
                short_lived_tokens=True,
                token_ttl_seconds=900,
                deny_by_default=True,
                identity_isolation=True,
                action_logging=True,
                input_validation=True,
                config_version_control=True,
                policy_documentation=True,
                description="Foundation: minimum viable Zero Trust for small deployments",
            )
        if tier == ZeroTrustTier.ENTERPRISE:
            return cls(
                tier=tier,
                crypto_identity=True,
                short_lived_tokens=True,
                token_ttl_seconds=600,
                require_mtls=True,
                deny_by_default=True,
                abac_context=True,
                identity_isolation=True,
                sandboxed_execution=True,
                action_logging=True,
                immutable_logs=True,
                otel_tracing=True,
                behavior_baseline=True,
                anomaly_detection=True,
                auto_containment=True,
                input_validation=True,
                injection_detection=True,
                spotlighting=True,
                output_pii_filter=True,
                config_version_control=True,
                config_signing=True,
                ai_bom=True,
                dependency_audit=True,
                formal_governance=True,
                description="Enterprise: target maturity for most production deployments",
            )
        # ADVANCED
        return cls(
            tier=tier,
            crypto_identity=True,
            short_lived_tokens=True,
            token_ttl_seconds=300,
            require_mtls=True,
            hardware_bound=True,
            deny_by_default=True,
            abac_context=True,
            jit_privilege=True,
            jit_ttl_seconds=120,
            identity_isolation=True,
            sandboxed_execution=True,
            hardware_isolation=True,
            action_logging=True,
            immutable_logs=True,
            otel_tracing=True,
            siem_streaming=True,
            full_provenance=True,
            behavior_baseline=True,
            anomaly_detection=True,
            auto_containment=True,
            ml_behavioral=True,
            continuous_baseline=True,
            input_validation=True,
            injection_detection=True,
            spotlighting=True,
            output_pii_filter=True,
            output_semantic_filter=True,
            hitl_high_risk=True,
            config_version_control=True,
            config_signing=True,
            immutable_infra=True,
            ai_bom=True,
            dependency_audit=True,
            supply_chain_verify=True,
            formal_governance=True,
            automated_compliance=True,
            continuous_auth=True,
            description="Advanced: aspirational / regulated / national-security grade",
        )

    @classmethod
    def for_regulation(cls, regulation: str) -> "ZeroTrustPolicy":
        """Return a policy appropriate for a specific regulated industry."""
        reg = regulation.lower()
        if reg == "hipaa":
            p = cls.for_tier(ZeroTrustTier.ENTERPRISE)
            p.regulation = "hipaa"
            p.output_pii_filter = True
            p.hitl_high_risk = True
            p.full_provenance = True
            p.description = "HIPAA-grade Zero Trust for healthcare AI agents"
            return p
        if reg == "sox":
            p = cls.for_tier(ZeroTrustTier.ENTERPRISE)
            p.regulation = "sox"
            p.immutable_logs = True
            p.full_provenance = True
            p.config_signing = True
            p.description = "SOX-grade Zero Trust for financial AI agents"
            return p
        if reg in ("gdpr", "pci"):
            p = cls.for_tier(ZeroTrustTier.ENTERPRISE)
            p.regulation = reg
            p.output_pii_filter = True
            p.description = f"{reg.upper()}-grade Zero Trust"
            return p
        if reg == "nerc":
            return cls.for_tier(ZeroTrustTier.ADVANCED)
        if reg in ("fedramp", "fedramp-high", "fisma", "cmmc"):
            p = cls.for_tier(ZeroTrustTier.ADVANCED)
            p.regulation = reg
            p.output_pii_filter = True
            p.full_provenance = True
            p.supply_chain_verify = True
            p.automated_compliance = True
            p.siem_streaming = True        # FedRAMP requires continuous monitoring
            p.description = f"{reg.upper()}-grade Zero Trust for US federal AI agents"
            return p
        if reg in ("nist-800-53", "nist"):
            p = cls.for_tier(ZeroTrustTier.ENTERPRISE)
            p.regulation = reg
            p.output_pii_filter = True
            p.full_provenance = True
            p.description = "NIST 800-53 Zero Trust baseline"
            return p
        if reg in ("iso27001", "iso-27001"):
            p = cls.for_tier(ZeroTrustTier.ENTERPRISE)
            p.regulation = reg
            p.output_pii_filter = True
            p.immutable_logs = True
            p.config_signing = True
            p.description = "ISO 27001 Zero Trust for ISMS-certified deployments"
            return p
        if reg == "ccpa":
            p = cls.for_tier(ZeroTrustTier.ENTERPRISE)
            p.regulation = reg
            p.output_pii_filter = True
            p.description = "CCPA Zero Trust for California consumer privacy"
            return p
        if reg in ("dora", "eu-ai-act"):
            p = cls.for_tier(ZeroTrustTier.ADVANCED)
            p.regulation = reg
            p.output_pii_filter = True
            p.full_provenance = True
            p.siem_streaming = True
            p.description = f"{reg.upper()} Zero Trust for EU regulatory compliance"
            return p
        if reg == "basel-iii":
            p = cls.for_tier(ZeroTrustTier.ENTERPRISE)
            p.regulation = reg
            p.immutable_logs = True
            p.full_provenance = True
            p.description = "Basel III Zero Trust for financial risk management"
            return p
        # Default to Enterprise
        return cls.for_tier(ZeroTrustTier.ENTERPRISE)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    def controls_enabled(self) -> list[str]:
        """Return the names of all boolean controls that are enabled."""
        enabled = []
        for f_name, f_val in self.__dict__.items():
            if isinstance(f_val, bool) and f_val and f_name not in ("deny_by_default",):
                enabled.append(f_name)
        return sorted(enabled)

    def controls_disabled(self) -> list[str]:
        """Return the names of boolean controls that are off (gaps vs the current tier)."""
        disabled = []
        target = self.for_tier(self.tier)
        for f_name, f_val in target.__dict__.items():
            if isinstance(f_val, bool) and f_val and not getattr(self, f_name, False):
                disabled.append(f_name)
        return sorted(disabled)


# ── Convenience aliases ────────────────────────────────────────────────────────

FOUNDATION = ZeroTrustPolicy.for_tier(ZeroTrustTier.FOUNDATION)
ENTERPRISE  = ZeroTrustPolicy.for_tier(ZeroTrustTier.ENTERPRISE)
ADVANCED    = ZeroTrustPolicy.for_tier(ZeroTrustTier.ADVANCED)

__all__ = [
    "ZeroTrustTier",
    "ZeroTrustPolicy",
    "FOUNDATION",
    "ENTERPRISE",
    "ADVANCED",
]
