package dev.meshflow;

/**
 * Zero Trust policy helpers — tier constants and per-regulation presets.
 *
 * <p>Usage:
 * <pre>{@code
 * // Pick a tier for a run
 * ZTPolicy.ZTTier tier = ZTPolicy.enterprise().getTier();
 *
 * // Or derive the recommended tier from a regulatory label
 * ZTPolicy policy = ZTPolicy.forRegulation("hipaa");
 * System.out.println(policy.getDescription());
 * }</pre>
 */
public final class ZTPolicy {

    // ── Tier enum ──────────────────────────────────────────────────────────────

    /**
     * Zero Trust security maturity tier.
     *
     * <ul>
     *   <li>{@link #FOUNDATION} — minimum viable; enables crypto identity,
     *       least-privilege RBAC, action logging, and input validation.</li>
     *   <li>{@link #ENTERPRISE} — recommended production default; adds mTLS,
     *       ABAC, sandboxed execution, immutable logs, OTEL, anomaly
     *       detection, injection guards, and AI-BOM.</li>
     *   <li>{@link #ADVANCED} — high-risk / regulated / national-security
     *       grade; adds hardware-bound credentials, JIT privilege, hardware
     *       isolation, SIEM streaming, ML-based behavioural analysis,
     *       continuous authorisation, and supply-chain attestation.</li>
     * </ul>
     */
    public enum ZTTier {
        FOUNDATION,
        ENTERPRISE,
        ADVANCED;

        /** Returns the lowercase wire name expected by the MeshFlow server. */
        public String wireName() {
            return name().toLowerCase();
        }
    }

    // ── Policy fields ──────────────────────────────────────────────────────────

    private final ZTTier tier;

    // Identity & Authentication
    private final boolean cryptoIdentity;
    private final boolean shortLivedTokens;
    private final int tokenTtlSeconds;
    private final boolean requireMtls;
    private final boolean hardwareBound;

    // Privilege Management
    private final boolean denyByDefault;
    private final boolean abacContext;
    private final boolean jitPrivilege;
    private final int jitTtlSeconds;
    private final int jitMaxGrants;

    // Resource Isolation
    private final boolean identityIsolation;
    private final boolean sandboxedExecution;
    private final boolean hardwareIsolation;

    // Observability
    private final boolean actionLogging;
    private final boolean immutableLogs;
    private final boolean otelTracing;
    private final boolean siemStreaming;
    private final boolean fullProvenance;

    // Behavioral Monitoring
    private final boolean behaviorBaseline;
    private final boolean anomalyDetection;
    private final boolean autoContainment;
    private final boolean mlBehavioral;
    private final boolean continuousBaseline;

    // Input / Output Controls
    private final boolean inputValidation;
    private final boolean injectionDetection;
    private final boolean spotlighting;
    private final boolean outputPiiFilter;
    private final boolean outputSemanticFilter;
    private final boolean hitlHighRisk;

    // Configuration Integrity
    private final boolean configVersionControl;
    private final boolean configSigning;
    private final boolean immutableInfra;

    // Supply Chain
    private final boolean aiBom;
    private final boolean dependencyAudit;
    private final boolean supplyChainVerify;

    // Governance
    private final boolean policyDocumentation;
    private final boolean formalGovernance;
    private final boolean automatedCompliance;

    // Continuous Authorization
    private final boolean continuousAuth;

    // Metadata
    private final String description;
    private final String regulation;

    private ZTPolicy(Builder b) {
        this.tier = b.tier;
        this.cryptoIdentity = b.cryptoIdentity;
        this.shortLivedTokens = b.shortLivedTokens;
        this.tokenTtlSeconds = b.tokenTtlSeconds;
        this.requireMtls = b.requireMtls;
        this.hardwareBound = b.hardwareBound;
        this.denyByDefault = b.denyByDefault;
        this.abacContext = b.abacContext;
        this.jitPrivilege = b.jitPrivilege;
        this.jitTtlSeconds = b.jitTtlSeconds;
        this.jitMaxGrants = b.jitMaxGrants;
        this.identityIsolation = b.identityIsolation;
        this.sandboxedExecution = b.sandboxedExecution;
        this.hardwareIsolation = b.hardwareIsolation;
        this.actionLogging = b.actionLogging;
        this.immutableLogs = b.immutableLogs;
        this.otelTracing = b.otelTracing;
        this.siemStreaming = b.siemStreaming;
        this.fullProvenance = b.fullProvenance;
        this.behaviorBaseline = b.behaviorBaseline;
        this.anomalyDetection = b.anomalyDetection;
        this.autoContainment = b.autoContainment;
        this.mlBehavioral = b.mlBehavioral;
        this.continuousBaseline = b.continuousBaseline;
        this.inputValidation = b.inputValidation;
        this.injectionDetection = b.injectionDetection;
        this.spotlighting = b.spotlighting;
        this.outputPiiFilter = b.outputPiiFilter;
        this.outputSemanticFilter = b.outputSemanticFilter;
        this.hitlHighRisk = b.hitlHighRisk;
        this.configVersionControl = b.configVersionControl;
        this.configSigning = b.configSigning;
        this.immutableInfra = b.immutableInfra;
        this.aiBom = b.aiBom;
        this.dependencyAudit = b.dependencyAudit;
        this.supplyChainVerify = b.supplyChainVerify;
        this.policyDocumentation = b.policyDocumentation;
        this.formalGovernance = b.formalGovernance;
        this.automatedCompliance = b.automatedCompliance;
        this.continuousAuth = b.continuousAuth;
        this.description = b.description;
        this.regulation = b.regulation;
    }

    // ── Factory methods ────────────────────────────────────────────────────────

    /**
     * Returns a policy pre-configured to the {@link ZTTier#FOUNDATION} tier:
     * minimum viable Zero Trust for small deployments and initial rollouts.
     */
    public static ZTPolicy foundation() {
        return new Builder()
                .tier(ZTTier.FOUNDATION)
                .cryptoIdentity(true)
                .shortLivedTokens(true)
                .tokenTtlSeconds(900)
                .denyByDefault(true)
                .identityIsolation(true)
                .actionLogging(true)
                .inputValidation(true)
                .configVersionControl(true)
                .policyDocumentation(true)
                .description("Foundation: minimum viable Zero Trust for small deployments")
                .build();
    }

    /**
     * Returns a policy pre-configured to the {@link ZTTier#ENTERPRISE} tier:
     * the recommended default for most production deployments.
     */
    public static ZTPolicy enterprise() {
        return new Builder()
                .tier(ZTTier.ENTERPRISE)
                .cryptoIdentity(true)
                .shortLivedTokens(true)
                .tokenTtlSeconds(600)
                .requireMtls(true)
                .denyByDefault(true)
                .abacContext(true)
                .identityIsolation(true)
                .sandboxedExecution(true)
                .actionLogging(true)
                .immutableLogs(true)
                .otelTracing(true)
                .behaviorBaseline(true)
                .anomalyDetection(true)
                .autoContainment(true)
                .inputValidation(true)
                .injectionDetection(true)
                .spotlighting(true)
                .outputPiiFilter(true)
                .configVersionControl(true)
                .configSigning(true)
                .aiBom(true)
                .dependencyAudit(true)
                .formalGovernance(true)
                .policyDocumentation(true)
                .description("Enterprise: target maturity for most production deployments")
                .build();
    }

    /**
     * Returns a policy pre-configured to the {@link ZTTier#ADVANCED} tier:
     * aspirational controls for high-risk, regulated, or national-security
     * grade deployments.
     */
    public static ZTPolicy advanced() {
        return new Builder()
                .tier(ZTTier.ADVANCED)
                .cryptoIdentity(true)
                .shortLivedTokens(true)
                .tokenTtlSeconds(300)
                .requireMtls(true)
                .hardwareBound(true)
                .denyByDefault(true)
                .abacContext(true)
                .jitPrivilege(true)
                .jitTtlSeconds(120)
                .jitMaxGrants(10)
                .identityIsolation(true)
                .sandboxedExecution(true)
                .hardwareIsolation(true)
                .actionLogging(true)
                .immutableLogs(true)
                .otelTracing(true)
                .siemStreaming(true)
                .fullProvenance(true)
                .behaviorBaseline(true)
                .anomalyDetection(true)
                .autoContainment(true)
                .mlBehavioral(true)
                .continuousBaseline(true)
                .inputValidation(true)
                .injectionDetection(true)
                .spotlighting(true)
                .outputPiiFilter(true)
                .outputSemanticFilter(true)
                .hitlHighRisk(true)
                .configVersionControl(true)
                .configSigning(true)
                .immutableInfra(true)
                .aiBom(true)
                .dependencyAudit(true)
                .supplyChainVerify(true)
                .policyDocumentation(true)
                .formalGovernance(true)
                .automatedCompliance(true)
                .continuousAuth(true)
                .description("Advanced: aspirational / regulated / national-security grade")
                .build();
    }

    /**
     * Returns a {@link ZTPolicy} tuned for a specific regulated industry.
     *
     * <p>Recognised values (case-insensitive):
     * {@code "hipaa"}, {@code "sox"}, {@code "gdpr"}, {@code "fedramp"},
     * {@code "pci"}, {@code "nerc"}.
     * Any unrecognised value returns {@link #enterprise()}.
     *
     * @param reg the regulatory label
     * @return a pre-configured policy for the regulation
     */
    public static ZTPolicy forRegulation(String reg) {
        if (reg == null) {
            return enterprise();
        }
        String key = reg.trim().toLowerCase();
        if ("hipaa".equals(key)) {
            return enterprise().toBuilder()
                    .regulation("hipaa")
                    .outputPiiFilter(true)
                    .hitlHighRisk(true)
                    .fullProvenance(true)
                    .description("HIPAA-grade Zero Trust for healthcare AI agents")
                    .build();
        } else if ("sox".equals(key)) {
            return enterprise().toBuilder()
                    .regulation("sox")
                    .immutableLogs(true)
                    .fullProvenance(true)
                    .configSigning(true)
                    .description("SOX-grade Zero Trust for financial AI agents")
                    .build();
        } else if ("gdpr".equals(key)) {
            return enterprise().toBuilder()
                    .regulation("gdpr")
                    .outputPiiFilter(true)
                    .description("GDPR-grade Zero Trust")
                    .build();
        } else if ("pci".equals(key)) {
            return enterprise().toBuilder()
                    .regulation("pci")
                    .outputPiiFilter(true)
                    .description("PCI-grade Zero Trust")
                    .build();
        } else if ("fedramp".equals(key) || "fedramp-high".equals(key)
                || "nerc".equals(key) || "fisma".equals(key)) {
            return advanced().toBuilder()
                    .regulation(key)
                    .description("NERC/FedRAMP-grade Zero Trust for critical infrastructure AI agents")
                    .build();
        } else {
            return enterprise();
        }
    }

    // ── Accessors ──────────────────────────────────────────────────────────────

    public ZTTier getTier() { return tier; }
    public boolean isCryptoIdentity() { return cryptoIdentity; }
    public boolean isShortLivedTokens() { return shortLivedTokens; }
    public int getTokenTtlSeconds() { return tokenTtlSeconds; }
    public boolean isRequireMtls() { return requireMtls; }
    public boolean isHardwareBound() { return hardwareBound; }
    public boolean isDenyByDefault() { return denyByDefault; }
    public boolean isAbacContext() { return abacContext; }
    public boolean isJitPrivilege() { return jitPrivilege; }
    public int getJitTtlSeconds() { return jitTtlSeconds; }
    public int getJitMaxGrants() { return jitMaxGrants; }
    public boolean isIdentityIsolation() { return identityIsolation; }
    public boolean isSandboxedExecution() { return sandboxedExecution; }
    public boolean isHardwareIsolation() { return hardwareIsolation; }
    public boolean isActionLogging() { return actionLogging; }
    public boolean isImmutableLogs() { return immutableLogs; }
    public boolean isOtelTracing() { return otelTracing; }
    public boolean isSiemStreaming() { return siemStreaming; }
    public boolean isFullProvenance() { return fullProvenance; }
    public boolean isBehaviorBaseline() { return behaviorBaseline; }
    public boolean isAnomalyDetection() { return anomalyDetection; }
    public boolean isAutoContainment() { return autoContainment; }
    public boolean isMlBehavioral() { return mlBehavioral; }
    public boolean isContinuousBaseline() { return continuousBaseline; }
    public boolean isInputValidation() { return inputValidation; }
    public boolean isInjectionDetection() { return injectionDetection; }
    public boolean isSpotlighting() { return spotlighting; }
    public boolean isOutputPiiFilter() { return outputPiiFilter; }
    public boolean isOutputSemanticFilter() { return outputSemanticFilter; }
    public boolean isHitlHighRisk() { return hitlHighRisk; }
    public boolean isConfigVersionControl() { return configVersionControl; }
    public boolean isConfigSigning() { return configSigning; }
    public boolean isImmutableInfra() { return immutableInfra; }
    public boolean isAiBom() { return aiBom; }
    public boolean isDependencyAudit() { return dependencyAudit; }
    public boolean isSupplyChainVerify() { return supplyChainVerify; }
    public boolean isPolicyDocumentation() { return policyDocumentation; }
    public boolean isFormalGovernance() { return formalGovernance; }
    public boolean isAutomatedCompliance() { return automatedCompliance; }
    public boolean isContinuousAuth() { return continuousAuth; }
    public String getDescription() { return description; }
    public String getRegulation() { return regulation; }

    /** Returns a new {@link Builder} pre-populated with all fields of this policy. */
    public Builder toBuilder() {
        Builder b = new Builder();
        b.tier = this.tier;
        b.cryptoIdentity = this.cryptoIdentity;
        b.shortLivedTokens = this.shortLivedTokens;
        b.tokenTtlSeconds = this.tokenTtlSeconds;
        b.requireMtls = this.requireMtls;
        b.hardwareBound = this.hardwareBound;
        b.denyByDefault = this.denyByDefault;
        b.abacContext = this.abacContext;
        b.jitPrivilege = this.jitPrivilege;
        b.jitTtlSeconds = this.jitTtlSeconds;
        b.jitMaxGrants = this.jitMaxGrants;
        b.identityIsolation = this.identityIsolation;
        b.sandboxedExecution = this.sandboxedExecution;
        b.hardwareIsolation = this.hardwareIsolation;
        b.actionLogging = this.actionLogging;
        b.immutableLogs = this.immutableLogs;
        b.otelTracing = this.otelTracing;
        b.siemStreaming = this.siemStreaming;
        b.fullProvenance = this.fullProvenance;
        b.behaviorBaseline = this.behaviorBaseline;
        b.anomalyDetection = this.anomalyDetection;
        b.autoContainment = this.autoContainment;
        b.mlBehavioral = this.mlBehavioral;
        b.continuousBaseline = this.continuousBaseline;
        b.inputValidation = this.inputValidation;
        b.injectionDetection = this.injectionDetection;
        b.spotlighting = this.spotlighting;
        b.outputPiiFilter = this.outputPiiFilter;
        b.outputSemanticFilter = this.outputSemanticFilter;
        b.hitlHighRisk = this.hitlHighRisk;
        b.configVersionControl = this.configVersionControl;
        b.configSigning = this.configSigning;
        b.immutableInfra = this.immutableInfra;
        b.aiBom = this.aiBom;
        b.dependencyAudit = this.dependencyAudit;
        b.supplyChainVerify = this.supplyChainVerify;
        b.policyDocumentation = this.policyDocumentation;
        b.formalGovernance = this.formalGovernance;
        b.automatedCompliance = this.automatedCompliance;
        b.continuousAuth = this.continuousAuth;
        b.description = this.description;
        b.regulation = this.regulation;
        return b;
    }

    @Override
    public String toString() {
        return "ZTPolicy{tier=" + tier + ", regulation='" + regulation +
               "', description='" + description + "'}";
    }

    // ── Builder ────────────────────────────────────────────────────────────────

    /** Fluent builder for {@link ZTPolicy}. */
    public static final class Builder {
        private ZTTier tier = ZTTier.FOUNDATION;
        private boolean cryptoIdentity;
        private boolean shortLivedTokens;
        private int tokenTtlSeconds;
        private boolean requireMtls;
        private boolean hardwareBound;
        private boolean denyByDefault;
        private boolean abacContext;
        private boolean jitPrivilege;
        private int jitTtlSeconds;
        private int jitMaxGrants;
        private boolean identityIsolation;
        private boolean sandboxedExecution;
        private boolean hardwareIsolation;
        private boolean actionLogging;
        private boolean immutableLogs;
        private boolean otelTracing;
        private boolean siemStreaming;
        private boolean fullProvenance;
        private boolean behaviorBaseline;
        private boolean anomalyDetection;
        private boolean autoContainment;
        private boolean mlBehavioral;
        private boolean continuousBaseline;
        private boolean inputValidation;
        private boolean injectionDetection;
        private boolean spotlighting;
        private boolean outputPiiFilter;
        private boolean outputSemanticFilter;
        private boolean hitlHighRisk;
        private boolean configVersionControl;
        private boolean configSigning;
        private boolean immutableInfra;
        private boolean aiBom;
        private boolean dependencyAudit;
        private boolean supplyChainVerify;
        private boolean policyDocumentation;
        private boolean formalGovernance;
        private boolean automatedCompliance;
        private boolean continuousAuth;
        private String description;
        private String regulation;

        private Builder() {}

        public Builder tier(ZTTier v) { this.tier = v; return this; }
        public Builder cryptoIdentity(boolean v) { this.cryptoIdentity = v; return this; }
        public Builder shortLivedTokens(boolean v) { this.shortLivedTokens = v; return this; }
        public Builder tokenTtlSeconds(int v) { this.tokenTtlSeconds = v; return this; }
        public Builder requireMtls(boolean v) { this.requireMtls = v; return this; }
        public Builder hardwareBound(boolean v) { this.hardwareBound = v; return this; }
        public Builder denyByDefault(boolean v) { this.denyByDefault = v; return this; }
        public Builder abacContext(boolean v) { this.abacContext = v; return this; }
        public Builder jitPrivilege(boolean v) { this.jitPrivilege = v; return this; }
        public Builder jitTtlSeconds(int v) { this.jitTtlSeconds = v; return this; }
        public Builder jitMaxGrants(int v) { this.jitMaxGrants = v; return this; }
        public Builder identityIsolation(boolean v) { this.identityIsolation = v; return this; }
        public Builder sandboxedExecution(boolean v) { this.sandboxedExecution = v; return this; }
        public Builder hardwareIsolation(boolean v) { this.hardwareIsolation = v; return this; }
        public Builder actionLogging(boolean v) { this.actionLogging = v; return this; }
        public Builder immutableLogs(boolean v) { this.immutableLogs = v; return this; }
        public Builder otelTracing(boolean v) { this.otelTracing = v; return this; }
        public Builder siemStreaming(boolean v) { this.siemStreaming = v; return this; }
        public Builder fullProvenance(boolean v) { this.fullProvenance = v; return this; }
        public Builder behaviorBaseline(boolean v) { this.behaviorBaseline = v; return this; }
        public Builder anomalyDetection(boolean v) { this.anomalyDetection = v; return this; }
        public Builder autoContainment(boolean v) { this.autoContainment = v; return this; }
        public Builder mlBehavioral(boolean v) { this.mlBehavioral = v; return this; }
        public Builder continuousBaseline(boolean v) { this.continuousBaseline = v; return this; }
        public Builder inputValidation(boolean v) { this.inputValidation = v; return this; }
        public Builder injectionDetection(boolean v) { this.injectionDetection = v; return this; }
        public Builder spotlighting(boolean v) { this.spotlighting = v; return this; }
        public Builder outputPiiFilter(boolean v) { this.outputPiiFilter = v; return this; }
        public Builder outputSemanticFilter(boolean v) { this.outputSemanticFilter = v; return this; }
        public Builder hitlHighRisk(boolean v) { this.hitlHighRisk = v; return this; }
        public Builder configVersionControl(boolean v) { this.configVersionControl = v; return this; }
        public Builder configSigning(boolean v) { this.configSigning = v; return this; }
        public Builder immutableInfra(boolean v) { this.immutableInfra = v; return this; }
        public Builder aiBom(boolean v) { this.aiBom = v; return this; }
        public Builder dependencyAudit(boolean v) { this.dependencyAudit = v; return this; }
        public Builder supplyChainVerify(boolean v) { this.supplyChainVerify = v; return this; }
        public Builder policyDocumentation(boolean v) { this.policyDocumentation = v; return this; }
        public Builder formalGovernance(boolean v) { this.formalGovernance = v; return this; }
        public Builder automatedCompliance(boolean v) { this.automatedCompliance = v; return this; }
        public Builder continuousAuth(boolean v) { this.continuousAuth = v; return this; }
        public Builder description(String v) { this.description = v; return this; }
        public Builder regulation(String v) { this.regulation = v; return this; }

        public ZTPolicy build() { return new ZTPolicy(this); }
    }
}
