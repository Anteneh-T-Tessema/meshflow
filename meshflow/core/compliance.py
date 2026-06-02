"""Compliance profiles — auto-configure policy from a regulation name.

A compliance profile bundles together the HITL threshold, required verifier
domains, audit retention policy, PHI scrubbing flag, and cost/token budgets
that a particular regulatory regime requires.

Usage::

    from meshflow.core.compliance import compliance_profile, PROFILES
    from meshflow import Mesh

    # Via Mesh — shorthand
    mesh = Mesh(compliance="hipaa")

    # Direct
    profile = compliance_profile("hipaa")
    print(profile.hitl_threshold)         # 0.70
    print(profile.verifier_domains)       # ["hipaa", "phi_scrubbing", ...]
    print(profile.audit_retention_days)   # 2555 (7 years)
    print(profile.phi_scrubbing)          # True
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from meshflow.core.schemas import HumanInLoopConfig, Policy, PolicyMode, RiskTier


# ── Profile definition ────────────────────────────────────────────────────────

@dataclass
class ComplianceProfile:
    """Full regulatory configuration for a compliance regime.

    Attributes
    ----------
    name:
        Human-readable regime name (e.g. "HIPAA", "SOX", "GDPR").
    hitl_threshold:
        Confidence below which a human must approve.  Lower = stricter.
    verifier_domains:
        SwarmTRM verifier domain keys to activate automatically.
    audit_retention_days:
        How long audit records must be kept per regulation.
    phi_scrubbing:
        Automatically scrub PHI/PII from logs and memory.
    max_cost_usd_per_run:
        Hard cost ceiling per agent run.
    max_tokens_per_step:
        Token cap per LLM step.
    require_evidence:
        Every action must carry Evidence objects.
    policy_mode:
        Base PolicyMode to use.
    extra_policy:
        Any additional fields to merge into the Policy.
    """

    name: str
    hitl_threshold: float
    verifier_domains: list[str]
    audit_retention_days: int
    phi_scrubbing: bool = False
    max_cost_usd_per_run: float = 1.0
    max_tokens_per_step: int = 4096
    require_evidence: bool = False
    policy_mode: PolicyMode = PolicyMode.STANDARD
    extra_policy: dict[str, Any] = field(default_factory=dict)

    def to_policy(self) -> Policy:
        """Return a fully-configured Policy for this compliance regime."""
        hitl = HumanInLoopConfig(
            enabled=self.hitl_threshold < 0.9,
            tier_threshold=RiskTier.EXTERNAL_IO,
        )
        return Policy(
            mode=self.policy_mode,
            require_human_review=self.hitl_threshold < 0.9,
            require_evidence=self.require_evidence,
            human_in_loop=hitl,
            budget_usd=self.max_cost_usd_per_run,
            budget_tokens=self.max_tokens_per_step * 100,
            scrub_phi=self.phi_scrubbing,
        )


# ── Built-in profiles ─────────────────────────────────────────────────────────

PROFILES: dict[str, ComplianceProfile] = {
    "hipaa": ComplianceProfile(
        name="HIPAA",
        hitl_threshold=0.70,
        verifier_domains=["hipaa", "phi_scrubbing", "aml"],
        audit_retention_days=2555,   # 7 years
        phi_scrubbing=True,
        max_cost_usd_per_run=2.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "sox": ComplianceProfile(
        name="SOX",
        hitl_threshold=0.75,
        verifier_domains=["sox", "erp_audit", "aml"],
        audit_retention_days=2555,   # 7 years
        phi_scrubbing=False,
        max_cost_usd_per_run=2.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "gdpr": ComplianceProfile(
        name="GDPR",
        hitl_threshold=0.72,
        verifier_domains=["gdpr", "phi_scrubbing"],
        audit_retention_days=1095,   # 3 years
        phi_scrubbing=True,
        max_cost_usd_per_run=1.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "pci": ComplianceProfile(
        name="PCI-DSS",
        hitl_threshold=0.80,
        verifier_domains=["pci_dss", "aml"],
        audit_retention_days=365,
        phi_scrubbing=True,
        max_cost_usd_per_run=1.5,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "pci-dss": ComplianceProfile(   # alias
        name="PCI-DSS",
        hitl_threshold=0.80,
        verifier_domains=["pci_dss", "aml"],
        audit_retention_days=365,
        phi_scrubbing=True,
        max_cost_usd_per_run=1.5,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "nerc": ComplianceProfile(
        name="NERC CIP",
        hitl_threshold=0.85,
        verifier_domains=["nerc_cip"],
        audit_retention_days=1095,
        phi_scrubbing=False,
        max_cost_usd_per_run=1.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    # ── International / Industry ─────────────────────────────────────────────
    "iso27001": ComplianceProfile(
        name="ISO 27001",
        hitl_threshold=0.75,
        verifier_domains=["phi_scrubbing", "aml"],
        audit_retention_days=1095,   # ISO 27001 Annex A.12.4: 3 years typical
        phi_scrubbing=True,
        max_cost_usd_per_run=2.0,
        max_tokens_per_step=8192,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "iso-27001": ComplianceProfile(  # alias
        name="ISO 27001",
        hitl_threshold=0.75,
        verifier_domains=["phi_scrubbing", "aml"],
        audit_retention_days=1095,
        phi_scrubbing=True,
        max_cost_usd_per_run=2.0,
        max_tokens_per_step=8192,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "ccpa": ComplianceProfile(
        name="CCPA",
        hitl_threshold=0.72,
        verifier_domains=["gdpr", "phi_scrubbing"],   # CCPA mirrors GDPR requirements
        audit_retention_days=365,    # CCPA: 12-month lookback
        phi_scrubbing=True,
        max_cost_usd_per_run=1.5,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "dora": ComplianceProfile(       # EU Digital Operational Resilience Act (financial)
        name="DORA",
        hitl_threshold=0.70,
        verifier_domains=["fedramp", "phi_scrubbing", "aml"],
        audit_retention_days=1825,   # DORA: 5 years
        phi_scrubbing=True,
        max_cost_usd_per_run=1.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "eu-ai-act": ComplianceProfile(  # EU AI Act (high-risk AI systems)
        name="EU AI Act",
        hitl_threshold=0.65,
        verifier_domains=["gdpr", "phi_scrubbing"],
        audit_retention_days=3650,   # EU AI Act: 10 years for high-risk systems
        phi_scrubbing=True,
        max_cost_usd_per_run=0.5,    # tight cap for high-risk AI
        max_tokens_per_step=2048,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "basel-iii": ComplianceProfile(  # Basel III / financial risk management
        name="Basel III",
        hitl_threshold=0.80,
        verifier_domains=["aml", "erp_audit"],
        audit_retention_days=2555,   # Basel III: 7 years
        phi_scrubbing=False,
        max_cost_usd_per_run=1.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    # ── US Federal ────────────────────────────────────────────────────────────
    "fedramp": ComplianceProfile(
        name="FedRAMP",
        hitl_threshold=0.70,
        verifier_domains=["fedramp", "phi_scrubbing", "aml", "nerc_cip"],
        audit_retention_days=2555,   # NIST 800-53: 7 years
        phi_scrubbing=True,
        max_cost_usd_per_run=1.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "fedramp-high": ComplianceProfile(
        name="FedRAMP High",
        hitl_threshold=0.65,         # stricter: human review at lower threshold
        verifier_domains=["fedramp", "phi_scrubbing", "aml", "nerc_cip"],
        audit_retention_days=2555,
        phi_scrubbing=True,
        max_cost_usd_per_run=0.5,    # tighter cost cap for high-impact systems
        max_tokens_per_step=2048,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "fisma": ComplianceProfile(       # alias — FISMA high = FedRAMP High
        name="FISMA",
        hitl_threshold=0.65,
        verifier_domains=["fedramp", "phi_scrubbing", "aml"],
        audit_retention_days=2555,
        phi_scrubbing=True,
        max_cost_usd_per_run=0.5,
        max_tokens_per_step=2048,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "nist-800-53": ComplianceProfile(  # NIST 800-53 moderate baseline
        name="NIST 800-53",
        hitl_threshold=0.72,
        verifier_domains=["fedramp", "phi_scrubbing"],
        audit_retention_days=1095,
        phi_scrubbing=True,
        max_cost_usd_per_run=1.5,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "cmmc": ComplianceProfile(         # CMMC Level 2 (DoD contractors)
        name="CMMC Level 2",
        hitl_threshold=0.75,
        verifier_domains=["fedramp", "aml"],
        audit_retention_days=1095,
        phi_scrubbing=True,
        max_cost_usd_per_run=1.0,
        max_tokens_per_step=4096,
        require_evidence=True,
        policy_mode=PolicyMode.LEGAL_CRITICAL,
    ),
    "standard": ComplianceProfile(
        name="Standard",
        hitl_threshold=0.90,
        verifier_domains=[],
        audit_retention_days=90,
        phi_scrubbing=False,
        max_cost_usd_per_run=5.0,
        max_tokens_per_step=8192,
        require_evidence=False,
        policy_mode=PolicyMode.STANDARD,
    ),
    "research": ComplianceProfile(
        name="Research",
        hitl_threshold=0.95,
        verifier_domains=["logical_validity", "factual_accuracy"],
        audit_retention_days=365,
        phi_scrubbing=False,
        max_cost_usd_per_run=10.0,
        max_tokens_per_step=8192,
        require_evidence=False,
        policy_mode=PolicyMode.STANDARD,
    ),
}


def compliance_profile(name: str) -> ComplianceProfile:
    """Return the ComplianceProfile for *name* (case-insensitive).

    Raises
    ------
    ValueError
        If no profile is registered for *name*.
    """
    key = name.lower().strip()
    profile = PROFILES.get(key)
    if profile is None:
        available = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"Unknown compliance profile '{name}'. Available: {available}"
        )
    return profile


def list_profiles() -> list[str]:
    """Return all registered compliance profile names."""
    return sorted(set(p.name for p in PROFILES.values()))
