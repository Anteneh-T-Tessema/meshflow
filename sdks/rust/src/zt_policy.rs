//! Zero Trust policy helpers for the MeshFlow Rust SDK.
//!
//! Mirror of the Python `ZeroTrustPolicy` dataclass and the Go `ZTPolicy`
//! struct. Use the factory methods to obtain a pre-configured policy and
//! optionally customise individual fields.
//!
//! # Example
//! ```rust
//! use meshflow_sdk::zt_policy::{ZTPolicy, ZTTier};
//!
//! // Fastest way to get a production-ready policy
//! let policy = ZTPolicy::enterprise();
//!
//! // Regulation-specific preset
//! let hipaa = ZTPolicy::for_regulation("hipaa");
//! assert_eq!(hipaa.regulation.as_deref(), Some("hipaa"));
//! assert!(hipaa.output_pii_filter);
//! ```

use serde::{Deserialize, Serialize};

// ── ZTTier ────────────────────────────────────────────────────────────────────

/// Zero Trust security maturity tier.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ZTTier {
    /// Minimum viable Zero Trust for small deployments or initial rollouts.
    Foundation,
    /// Recommended production tier for most deployments.
    Enterprise,
    /// Aspirational tier for high-risk, regulated, or national-security grade
    /// deployments.
    Advanced,
}

impl ZTTier {
    /// Returns the canonical environment-variable string value.
    pub fn env_var_value(&self) -> &'static str {
        match self {
            ZTTier::Foundation => "foundation",
            ZTTier::Enterprise => "enterprise",
            ZTTier::Advanced => "advanced",
        }
    }
}

impl std::fmt::Display for ZTTier {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.env_var_value())
    }
}

// ── ZTPolicy ──────────────────────────────────────────────────────────────────

/// Complete set of Zero Trust controls for an agent or workflow.
///
/// Obtain a pre-configured policy via [`ZTPolicy::foundation`],
/// [`ZTPolicy::enterprise`], [`ZTPolicy::advanced`], or
/// [`ZTPolicy::for_regulation`], then mutate fields as needed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ZTPolicy {
    pub tier: ZTTier,

    // Identity & Authentication
    pub crypto_identity: bool,
    pub short_lived_tokens: bool,
    pub token_ttl_seconds: u32,
    pub require_mtls: bool,
    pub hardware_bound: bool,

    // Privilege Management
    pub deny_by_default: bool,
    pub abac_context: bool,
    pub jit_privilege: bool,
    pub jit_ttl_seconds: u32,
    pub jit_max_grants: u32,

    // Resource Isolation
    pub identity_isolation: bool,
    pub sandboxed_execution: bool,
    pub hardware_isolation: bool,

    // Observability
    pub action_logging: bool,
    pub immutable_logs: bool,
    pub otel_tracing: bool,
    pub siem_streaming: bool,
    pub full_provenance: bool,

    // Behavioral Monitoring
    pub behavior_baseline: bool,
    pub anomaly_detection: bool,
    pub auto_containment: bool,
    pub ml_behavioral: bool,
    pub continuous_baseline: bool,

    // Input / Output Controls
    pub input_validation: bool,
    pub injection_detection: bool,
    pub spotlighting: bool,
    pub output_pii_filter: bool,
    pub output_semantic_filter: bool,
    pub hitl_high_risk: bool,

    // Configuration Integrity
    pub config_version_control: bool,
    pub config_signing: bool,
    pub immutable_infra: bool,

    // Supply Chain
    pub ai_bom: bool,
    pub dependency_audit: bool,
    pub supply_chain_verify: bool,

    // Governance
    pub policy_documentation: bool,
    pub formal_governance: bool,
    pub automated_compliance: bool,

    // Continuous Authorization
    pub continuous_auth: bool,

    // Metadata
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub regulation: Option<String>,
}

impl ZTPolicy {
    /// Foundation tier — minimum viable Zero Trust for small deployments and
    /// initial rollouts.
    pub fn foundation() -> Self {
        Self {
            tier: ZTTier::Foundation,
            crypto_identity: true,
            short_lived_tokens: true,
            token_ttl_seconds: 900,
            require_mtls: false,
            hardware_bound: false,
            deny_by_default: true,
            abac_context: false,
            jit_privilege: false,
            jit_ttl_seconds: 0,
            jit_max_grants: 0,
            identity_isolation: true,
            sandboxed_execution: false,
            hardware_isolation: false,
            action_logging: true,
            immutable_logs: false,
            otel_tracing: false,
            siem_streaming: false,
            full_provenance: false,
            behavior_baseline: false,
            anomaly_detection: false,
            auto_containment: false,
            ml_behavioral: false,
            continuous_baseline: false,
            input_validation: true,
            injection_detection: false,
            spotlighting: false,
            output_pii_filter: false,
            output_semantic_filter: false,
            hitl_high_risk: false,
            config_version_control: true,
            config_signing: false,
            immutable_infra: false,
            ai_bom: false,
            dependency_audit: false,
            supply_chain_verify: false,
            policy_documentation: true,
            formal_governance: false,
            automated_compliance: false,
            continuous_auth: false,
            description: Some(
                "Foundation: minimum viable Zero Trust for small deployments".into(),
            ),
            regulation: None,
        }
    }

    /// Enterprise tier — recommended production default for most deployments.
    pub fn enterprise() -> Self {
        Self {
            tier: ZTTier::Enterprise,
            crypto_identity: true,
            short_lived_tokens: true,
            token_ttl_seconds: 600,
            require_mtls: true,
            hardware_bound: false,
            deny_by_default: true,
            abac_context: true,
            jit_privilege: false,
            jit_ttl_seconds: 0,
            jit_max_grants: 0,
            identity_isolation: true,
            sandboxed_execution: true,
            hardware_isolation: false,
            action_logging: true,
            immutable_logs: true,
            otel_tracing: true,
            siem_streaming: false,
            full_provenance: false,
            behavior_baseline: true,
            anomaly_detection: true,
            auto_containment: true,
            ml_behavioral: false,
            continuous_baseline: false,
            input_validation: true,
            injection_detection: true,
            spotlighting: true,
            output_pii_filter: true,
            output_semantic_filter: false,
            hitl_high_risk: false,
            config_version_control: true,
            config_signing: true,
            immutable_infra: false,
            ai_bom: true,
            dependency_audit: true,
            supply_chain_verify: false,
            policy_documentation: true,
            formal_governance: true,
            automated_compliance: false,
            continuous_auth: false,
            description: Some(
                "Enterprise: target maturity for most production deployments".into(),
            ),
            regulation: None,
        }
    }

    /// Advanced tier — aspirational controls for high-risk, regulated, or
    /// national-security grade deployments.
    pub fn advanced() -> Self {
        Self {
            tier: ZTTier::Advanced,
            crypto_identity: true,
            short_lived_tokens: true,
            token_ttl_seconds: 300,
            require_mtls: true,
            hardware_bound: true,
            deny_by_default: true,
            abac_context: true,
            jit_privilege: true,
            jit_ttl_seconds: 120,
            jit_max_grants: 10,
            identity_isolation: true,
            sandboxed_execution: true,
            hardware_isolation: true,
            action_logging: true,
            immutable_logs: true,
            otel_tracing: true,
            siem_streaming: true,
            full_provenance: true,
            behavior_baseline: true,
            anomaly_detection: true,
            auto_containment: true,
            ml_behavioral: true,
            continuous_baseline: true,
            input_validation: true,
            injection_detection: true,
            spotlighting: true,
            output_pii_filter: true,
            output_semantic_filter: true,
            hitl_high_risk: true,
            config_version_control: true,
            config_signing: true,
            immutable_infra: true,
            ai_bom: true,
            dependency_audit: true,
            supply_chain_verify: true,
            policy_documentation: true,
            formal_governance: true,
            automated_compliance: true,
            continuous_auth: true,
            description: Some(
                "Advanced: aspirational / regulated / national-security grade".into(),
            ),
            regulation: None,
        }
    }

    /// Returns a [`ZTPolicy`] tuned for a specific regulated industry.
    ///
    /// Recognised `reg` values (case-insensitive): `"hipaa"`, `"sox"`,
    /// `"gdpr"`, `"pci"`, `"nerc"`. Any unrecognised value falls back to
    /// [`ZTPolicy::enterprise`].
    pub fn for_regulation(reg: &str) -> Self {
        match reg.to_lowercase().as_str() {
            "hipaa" => {
                let mut p = Self::enterprise();
                p.regulation = Some("hipaa".into());
                p.output_pii_filter = true;
                p.hitl_high_risk = true;
                p.full_provenance = true;
                p.description =
                    Some("HIPAA-grade Zero Trust for healthcare AI agents".into());
                p
            }
            "sox" => {
                let mut p = Self::enterprise();
                p.regulation = Some("sox".into());
                p.immutable_logs = true;
                p.full_provenance = true;
                p.config_signing = true;
                p.description =
                    Some("SOX-grade Zero Trust for financial AI agents".into());
                p
            }
            "gdpr" => {
                let mut p = Self::enterprise();
                p.regulation = Some("gdpr".into());
                p.output_pii_filter = true;
                p.description = Some("GDPR-grade Zero Trust".into());
                p
            }
            "pci" => {
                let mut p = Self::enterprise();
                p.regulation = Some("pci".into());
                p.output_pii_filter = true;
                p.description = Some("PCI-grade Zero Trust".into());
                p
            }
            "nerc" => {
                let mut p = Self::advanced();
                p.regulation = Some("nerc".into());
                p.description = Some(
                    "NERC CIP-grade Zero Trust for critical infrastructure AI agents".into(),
                );
                p
            }
            _ => Self::enterprise(),
        }
    }

    /// Returns the `MESHFLOW_ZT_TIER` environment-variable string for this
    /// policy's tier.
    pub fn env_var_value(&self) -> &'static str {
        self.tier.env_var_value()
    }

    /// Returns the names of all boolean controls that are currently enabled.
    pub fn controls_enabled(&self) -> Vec<&'static str> {
        self.bool_fields(true)
    }

    /// Returns boolean control names that are `false` in `self` but `true` in
    /// the canonical policy for `self.tier` — i.e. the current gap.
    pub fn controls_gap(&self) -> Vec<&'static str> {
        let target = match self.tier {
            ZTTier::Foundation => Self::foundation(),
            ZTTier::Enterprise => Self::enterprise(),
            ZTTier::Advanced => Self::advanced(),
        };
        let enabled: std::collections::HashSet<&'static str> =
            self.bool_fields(true).into_iter().collect();
        target
            .bool_fields(true)
            .into_iter()
            .filter(|name| !enabled.contains(name))
            .collect()
    }

    fn bool_fields(&self, want: bool) -> Vec<&'static str> {
        let fields: &[(&'static str, bool)] = &[
            ("crypto_identity", self.crypto_identity),
            ("short_lived_tokens", self.short_lived_tokens),
            ("require_mtls", self.require_mtls),
            ("hardware_bound", self.hardware_bound),
            ("deny_by_default", self.deny_by_default),
            ("abac_context", self.abac_context),
            ("jit_privilege", self.jit_privilege),
            ("identity_isolation", self.identity_isolation),
            ("sandboxed_execution", self.sandboxed_execution),
            ("hardware_isolation", self.hardware_isolation),
            ("action_logging", self.action_logging),
            ("immutable_logs", self.immutable_logs),
            ("otel_tracing", self.otel_tracing),
            ("siem_streaming", self.siem_streaming),
            ("full_provenance", self.full_provenance),
            ("behavior_baseline", self.behavior_baseline),
            ("anomaly_detection", self.anomaly_detection),
            ("auto_containment", self.auto_containment),
            ("ml_behavioral", self.ml_behavioral),
            ("continuous_baseline", self.continuous_baseline),
            ("input_validation", self.input_validation),
            ("injection_detection", self.injection_detection),
            ("spotlighting", self.spotlighting),
            ("output_pii_filter", self.output_pii_filter),
            ("output_semantic_filter", self.output_semantic_filter),
            ("hitl_high_risk", self.hitl_high_risk),
            ("config_version_control", self.config_version_control),
            ("config_signing", self.config_signing),
            ("immutable_infra", self.immutable_infra),
            ("ai_bom", self.ai_bom),
            ("dependency_audit", self.dependency_audit),
            ("supply_chain_verify", self.supply_chain_verify),
            ("policy_documentation", self.policy_documentation),
            ("formal_governance", self.formal_governance),
            ("automated_compliance", self.automated_compliance),
            ("continuous_auth", self.continuous_auth),
        ];
        fields
            .iter()
            .filter(|(_, v)| *v == want)
            .map(|(name, _)| *name)
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn foundation_has_fewer_controls_than_enterprise() {
        let f = ZTPolicy::foundation().controls_enabled().len();
        let e = ZTPolicy::enterprise().controls_enabled().len();
        assert!(f < e, "foundation ({f}) should enable fewer controls than enterprise ({e})");
    }

    #[test]
    fn hipaa_sets_pii_filter() {
        let p = ZTPolicy::for_regulation("HIPAA");
        assert!(p.output_pii_filter);
        assert_eq!(p.regulation.as_deref(), Some("hipaa"));
    }

    #[test]
    fn nerc_is_advanced_tier() {
        let p = ZTPolicy::for_regulation("nerc");
        assert_eq!(p.tier, ZTTier::Advanced);
    }

    #[test]
    fn env_var_values() {
        assert_eq!(ZTPolicy::foundation().env_var_value(), "foundation");
        assert_eq!(ZTPolicy::enterprise().env_var_value(), "enterprise");
        assert_eq!(ZTPolicy::advanced().env_var_value(), "advanced");
    }
}
