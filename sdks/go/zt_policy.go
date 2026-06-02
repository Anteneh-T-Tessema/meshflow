package meshflow

// ZTTier represents the Zero Trust security maturity level for an agent or
// workflow. It maps directly to the ZeroTrustTier enum in the Python SDK.
type ZTTier string

const (
	// ZTTierFoundation is the minimum viable tier for small deployments or
	// initial rollouts. Enables cryptographic identity, least-privilege RBAC,
	// comprehensive action logging, and input validation.
	ZTTierFoundation ZTTier = "foundation"

	// ZTTierEnterprise is the recommended production tier. Adds mTLS, ABAC,
	// sandboxed execution, immutable logs, OTEL tracing, anomaly detection,
	// injection guards, and AI-BOM generation.
	ZTTierEnterprise ZTTier = "enterprise"

	// ZTTierAdvanced is the aspirational tier for high-risk, regulated, or
	// national-security grade deployments. Adds hardware-bound credentials,
	// JIT privilege, hardware isolation, SIEM streaming, ML-based behavioural
	// analysis, continuous authorisation, and supply-chain attestation.
	ZTTierAdvanced ZTTier = "advanced"
)

// ZTPolicy is the Go representation of the Python ZeroTrustPolicy dataclass.
// It carries the complete set of Zero Trust controls for an agent or workflow.
// Use FoundationPolicy, EnterprisePolicy, AdvancedPolicy, or ForRegulation to
// obtain a pre-configured policy, then customise individual fields as needed.
type ZTPolicy struct {
	Tier ZTTier `json:"tier"`

	// Identity & Authentication
	CryptoIdentity   bool `json:"crypto_identity"`
	ShortLivedTokens bool `json:"short_lived_tokens"`
	TokenTTLSeconds  int  `json:"token_ttl_seconds"`
	RequireMTLS      bool `json:"require_mtls"`
	HardwareBound    bool `json:"hardware_bound"`

	// Privilege Management
	DenyByDefault bool `json:"deny_by_default"`
	ABACContext   bool `json:"abac_context"`
	JITPrivilege  bool `json:"jit_privilege"`
	JITTTLSeconds int  `json:"jit_ttl_seconds"`
	JITMaxGrants  int  `json:"jit_max_grants"`

	// Resource Isolation
	IdentityIsolation  bool `json:"identity_isolation"`
	SandboxedExecution bool `json:"sandboxed_execution"`
	HardwareIsolation  bool `json:"hardware_isolation"`

	// Observability
	ActionLogging  bool `json:"action_logging"`
	ImmutableLogs  bool `json:"immutable_logs"`
	OTELTracing    bool `json:"otel_tracing"`
	SIEMStreaming  bool `json:"siem_streaming"`
	FullProvenance bool `json:"full_provenance"`

	// Behavioral Monitoring
	BehaviorBaseline   bool `json:"behavior_baseline"`
	AnomalyDetection   bool `json:"anomaly_detection"`
	AutoContainment    bool `json:"auto_containment"`
	MLBehavioral       bool `json:"ml_behavioral"`
	ContinuousBaseline bool `json:"continuous_baseline"`

	// Input / Output Controls
	InputValidation     bool `json:"input_validation"`
	InjectionDetection  bool `json:"injection_detection"`
	Spotlighting        bool `json:"spotlighting"`
	OutputPIIFilter     bool `json:"output_pii_filter"`
	OutputSemanticFilter bool `json:"output_semantic_filter"`
	HITLHighRisk        bool `json:"hitl_high_risk"`

	// Configuration Integrity
	ConfigVersionControl bool `json:"config_version_control"`
	ConfigSigning        bool `json:"config_signing"`
	ImmutableInfra       bool `json:"immutable_infra"`

	// Supply Chain
	AIBOM             bool `json:"ai_bom"`
	DependencyAudit   bool `json:"dependency_audit"`
	SupplyChainVerify bool `json:"supply_chain_verify"`

	// Governance
	PolicyDocumentation  bool `json:"policy_documentation"`
	FormalGovernance     bool `json:"formal_governance"`
	AutomatedCompliance  bool `json:"automated_compliance"`

	// Continuous Authorization
	ContinuousAuth bool `json:"continuous_auth"`

	// Metadata
	Description string `json:"description,omitempty"`
	Regulation  string `json:"regulation,omitempty"`
}

// FoundationPolicy returns a ZTPolicy pre-configured to the Foundation tier:
// minimum viable Zero Trust for small deployments and initial rollouts.
func FoundationPolicy() ZTPolicy {
	return ZTPolicy{
		Tier:                 ZTTierFoundation,
		CryptoIdentity:       true,
		ShortLivedTokens:     true,
		TokenTTLSeconds:      900,
		DenyByDefault:        true,
		IdentityIsolation:    true,
		ActionLogging:        true,
		InputValidation:      true,
		ConfigVersionControl: true,
		PolicyDocumentation:  true,
		Description:          "Foundation: minimum viable Zero Trust for small deployments",
	}
}

// EnterprisePolicy returns a ZTPolicy pre-configured to the Enterprise tier:
// the recommended default for most production deployments.
func EnterprisePolicy() ZTPolicy {
	return ZTPolicy{
		Tier:                 ZTTierEnterprise,
		CryptoIdentity:       true,
		ShortLivedTokens:     true,
		TokenTTLSeconds:      600,
		RequireMTLS:          true,
		DenyByDefault:        true,
		ABACContext:          true,
		IdentityIsolation:    true,
		SandboxedExecution:   true,
		ActionLogging:        true,
		ImmutableLogs:        true,
		OTELTracing:          true,
		BehaviorBaseline:     true,
		AnomalyDetection:     true,
		AutoContainment:      true,
		InputValidation:      true,
		InjectionDetection:   true,
		Spotlighting:         true,
		OutputPIIFilter:      true,
		ConfigVersionControl: true,
		ConfigSigning:        true,
		AIBOM:                true,
		DependencyAudit:      true,
		FormalGovernance:     true,
		PolicyDocumentation:  true,
		Description:          "Enterprise: target maturity for most production deployments",
	}
}

// AdvancedPolicy returns a ZTPolicy pre-configured to the Advanced tier:
// aspirational controls for high-risk, regulated, or national-security grade
// deployments.
func AdvancedPolicy() ZTPolicy {
	return ZTPolicy{
		Tier:                 ZTTierAdvanced,
		CryptoIdentity:       true,
		ShortLivedTokens:     true,
		TokenTTLSeconds:      300,
		RequireMTLS:          true,
		HardwareBound:        true,
		DenyByDefault:        true,
		ABACContext:          true,
		JITPrivilege:         true,
		JITTTLSeconds:        120,
		JITMaxGrants:         10,
		IdentityIsolation:    true,
		SandboxedExecution:   true,
		HardwareIsolation:    true,
		ActionLogging:        true,
		ImmutableLogs:        true,
		OTELTracing:          true,
		SIEMStreaming:        true,
		FullProvenance:       true,
		BehaviorBaseline:     true,
		AnomalyDetection:     true,
		AutoContainment:      true,
		MLBehavioral:         true,
		ContinuousBaseline:   true,
		InputValidation:      true,
		InjectionDetection:   true,
		Spotlighting:         true,
		OutputPIIFilter:      true,
		OutputSemanticFilter: true,
		HITLHighRisk:         true,
		ConfigVersionControl: true,
		ConfigSigning:        true,
		ImmutableInfra:       true,
		AIBOM:                true,
		DependencyAudit:      true,
		SupplyChainVerify:    true,
		PolicyDocumentation:  true,
		FormalGovernance:     true,
		AutomatedCompliance:  true,
		ContinuousAuth:       true,
		Description:          "Advanced: aspirational / regulated / national-security grade",
	}
}

// ForRegulation returns a ZTPolicy tuned for a specific regulated industry.
// Recognised values for reg: "hipaa", "sox", "gdpr", "pci", "nerc"
// (case-insensitive). Any unrecognised value returns EnterprisePolicy.
func ForRegulation(reg string) ZTPolicy {
	switch normalize(reg) {
	case "hipaa":
		p := EnterprisePolicy()
		p.Regulation = "hipaa"
		p.OutputPIIFilter = true
		p.HITLHighRisk = true
		p.FullProvenance = true
		p.Description = "HIPAA-grade Zero Trust for healthcare AI agents"
		return p
	case "sox":
		p := EnterprisePolicy()
		p.Regulation = "sox"
		p.ImmutableLogs = true
		p.FullProvenance = true
		p.ConfigSigning = true
		p.Description = "SOX-grade Zero Trust for financial AI agents"
		return p
	case "gdpr":
		p := EnterprisePolicy()
		p.Regulation = "gdpr"
		p.OutputPIIFilter = true
		p.Description = "GDPR-grade Zero Trust"
		return p
	case "pci":
		p := EnterprisePolicy()
		p.Regulation = "pci"
		p.OutputPIIFilter = true
		p.Description = "PCI-grade Zero Trust"
		return p
	case "nerc":
		p := AdvancedPolicy()
		p.Regulation = "nerc"
		p.Description = "NERC CIP-grade Zero Trust for critical infrastructure AI agents"
		return p
	default:
		return EnterprisePolicy()
	}
}

// ControlsEnabled returns the names of all boolean fields that are set to true.
func (p ZTPolicy) ControlsEnabled() []string {
	return boolFields(p, true)
}

// ControlsDisabled returns boolean field names that are false in p but true in
// the canonical policy for p.Tier — these represent the current gap.
func (p ZTPolicy) ControlsDisabled() []string {
	var target ZTPolicy
	switch p.Tier {
	case ZTTierFoundation:
		target = FoundationPolicy()
	case ZTTierAdvanced:
		target = AdvancedPolicy()
	default:
		target = EnterprisePolicy()
	}
	return controlsGap(p, target)
}

// ── private helpers ───────────────────────────────────────────────────────────

func normalize(s string) string {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c >= 'A' && c <= 'Z' {
			c += 32
		}
		out = append(out, c)
	}
	return string(out)
}

// boolFields uses a simple struct-walk via JSON marshaling to find true fields.
// We use explicit field inspection for zero-dependency compliance.
func boolFields(p ZTPolicy, want bool) []string {
	type namedBool struct {
		name string
		val  bool
	}
	fields := []namedBool{
		{"crypto_identity", p.CryptoIdentity},
		{"short_lived_tokens", p.ShortLivedTokens},
		{"require_mtls", p.RequireMTLS},
		{"hardware_bound", p.HardwareBound},
		{"deny_by_default", p.DenyByDefault},
		{"abac_context", p.ABACContext},
		{"jit_privilege", p.JITPrivilege},
		{"identity_isolation", p.IdentityIsolation},
		{"sandboxed_execution", p.SandboxedExecution},
		{"hardware_isolation", p.HardwareIsolation},
		{"action_logging", p.ActionLogging},
		{"immutable_logs", p.ImmutableLogs},
		{"otel_tracing", p.OTELTracing},
		{"siem_streaming", p.SIEMStreaming},
		{"full_provenance", p.FullProvenance},
		{"behavior_baseline", p.BehaviorBaseline},
		{"anomaly_detection", p.AnomalyDetection},
		{"auto_containment", p.AutoContainment},
		{"ml_behavioral", p.MLBehavioral},
		{"continuous_baseline", p.ContinuousBaseline},
		{"input_validation", p.InputValidation},
		{"injection_detection", p.InjectionDetection},
		{"spotlighting", p.Spotlighting},
		{"output_pii_filter", p.OutputPIIFilter},
		{"output_semantic_filter", p.OutputSemanticFilter},
		{"hitl_high_risk", p.HITLHighRisk},
		{"config_version_control", p.ConfigVersionControl},
		{"config_signing", p.ConfigSigning},
		{"immutable_infra", p.ImmutableInfra},
		{"ai_bom", p.AIBOM},
		{"dependency_audit", p.DependencyAudit},
		{"supply_chain_verify", p.SupplyChainVerify},
		{"policy_documentation", p.PolicyDocumentation},
		{"formal_governance", p.FormalGovernance},
		{"automated_compliance", p.AutomatedCompliance},
		{"continuous_auth", p.ContinuousAuth},
	}
	var result []string
	for _, f := range fields {
		if f.val == want {
			result = append(result, f.name)
		}
	}
	return result
}

// controlsGap returns field names that are true in target but false in current.
func controlsGap(current, target ZTPolicy) []string {
	targetEnabled := boolFields(target, true)
	currentEnabled := make(map[string]struct{}, len(targetEnabled))
	for _, name := range boolFields(current, true) {
		currentEnabled[name] = struct{}{}
	}
	var gap []string
	for _, name := range targetEnabled {
		if _, ok := currentEnabled[name]; !ok {
			gap = append(gap, name)
		}
	}
	return gap
}
