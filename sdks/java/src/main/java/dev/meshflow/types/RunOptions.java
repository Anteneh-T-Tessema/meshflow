package dev.meshflow.types;

import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/**
 * Optional parameters for {@code MeshFlowClient.runAgent()} and
 * {@code MeshFlowClient.streamAgent()}.
 *
 * <p>Obtain a fully-configured instance via the nested {@link Builder}:
 * <pre>{@code
 * RunOptions opts = RunOptions.builder()
 *     .policyMode("regulated")
 *     .costCapUsd(2.50)
 *     .complianceProfile("hipaa")
 *     .build();
 * }</pre>
 */
public final class RunOptions {

    /**
     * Governance strictness mode.
     * Values: {@code "dev"}, {@code "standard"}, {@code "regulated"},
     * {@code "legal-critical"}, {@code "hipaa"}.
     */
    private final String policyMode;

    /** Hard per-run spend ceiling in US dollars. Zero means no cap. */
    private final double costCapUsd;

    /** Maximum token budget for the run. Zero means no cap. */
    private final int budgetTokens;

    /** Maximum wall-clock seconds allowed. Zero means no cap. */
    private final double timeoutS;

    /** Maximum number of agent execution steps. Zero means no cap. */
    private final int maxSteps;

    /**
     * Compliance framework hint forwarded to governance policies.
     * Examples: {@code "hipaa"}, {@code "sox"}, {@code "gdpr"}.
     */
    private final String complianceProfile;

    /** Logical tenant identifier for multi-tenant deployments. */
    private final String tenantId;

    /** When true, enables the DASC determinism gate. */
    private final boolean deterministicGate;

    /** When true, activates the guardian agent for this run. */
    private final boolean enableGuardian;

    /** When true, enables inter-agent collusion monitoring. */
    private final boolean enableCollusionAudit;

    /** When true, enables uncertainty-awareness scoring. */
    private final boolean enableUncertainty;

    /** Arbitrary key/value context forwarded to the agents. */
    private final Map<String, Object> context;

    private RunOptions(Builder b) {
        this.policyMode = b.policyMode;
        this.costCapUsd = b.costCapUsd;
        this.budgetTokens = b.budgetTokens;
        this.timeoutS = b.timeoutS;
        this.maxSteps = b.maxSteps;
        this.complianceProfile = b.complianceProfile;
        this.tenantId = b.tenantId;
        this.deterministicGate = b.deterministicGate;
        this.enableGuardian = b.enableGuardian;
        this.enableCollusionAudit = b.enableCollusionAudit;
        this.enableUncertainty = b.enableUncertainty;
        this.context = b.context == null
                ? Collections.emptyMap()
                : Collections.unmodifiableMap(new HashMap<>(b.context));
    }

    /** Returns a new {@link Builder} with all defaults. */
    public static Builder builder() { return new Builder(); }

    public String getPolicyMode() { return policyMode; }
    public double getCostCapUsd() { return costCapUsd; }
    public int getBudgetTokens() { return budgetTokens; }
    public double getTimeoutS() { return timeoutS; }
    public int getMaxSteps() { return maxSteps; }
    public String getComplianceProfile() { return complianceProfile; }
    public String getTenantId() { return tenantId; }
    public boolean isDeterministicGate() { return deterministicGate; }
    public boolean isEnableGuardian() { return enableGuardian; }
    public boolean isEnableCollusionAudit() { return enableCollusionAudit; }
    public boolean isEnableUncertainty() { return enableUncertainty; }
    public Map<String, Object> getContext() { return context; }

    // ── Builder ────────────────────────────────────────────────────────────────

    /** Fluent builder for {@link RunOptions}. */
    public static final class Builder {

        private String policyMode;
        private double costCapUsd;
        private int budgetTokens;
        private double timeoutS;
        private int maxSteps;
        private String complianceProfile;
        private String tenantId;
        private boolean deterministicGate;
        private boolean enableGuardian;
        private boolean enableCollusionAudit;
        private boolean enableUncertainty;
        private Map<String, Object> context;

        private Builder() {}

        /** Sets the governance policy mode (e.g. {@code "regulated"}). */
        public Builder policyMode(String mode) {
            this.policyMode = mode;
            return this;
        }

        /** Sets a hard USD spend ceiling for this run. */
        public Builder costCapUsd(double usd) {
            this.costCapUsd = usd;
            return this;
        }

        /** Sets a maximum token budget for this run. */
        public Builder budgetTokens(int tokens) {
            this.budgetTokens = tokens;
            return this;
        }

        /** Sets the maximum run duration in seconds. */
        public Builder timeoutS(double seconds) {
            this.timeoutS = seconds;
            return this;
        }

        /** Caps the number of agent execution steps. */
        public Builder maxSteps(int steps) {
            this.maxSteps = steps;
            return this;
        }

        /** Sets the compliance framework hint (e.g. {@code "hipaa"}). */
        public Builder complianceProfile(String profile) {
            this.complianceProfile = profile;
            return this;
        }

        /** Scopes this run to a logical tenant. */
        public Builder tenantId(String tenantId) {
            this.tenantId = tenantId;
            return this;
        }

        /** Enables the DASC determinism gate. */
        public Builder deterministicGate(boolean enabled) {
            this.deterministicGate = enabled;
            return this;
        }

        /** Activates the guardian agent for this run. */
        public Builder enableGuardian(boolean enabled) {
            this.enableGuardian = enabled;
            return this;
        }

        /** Enables inter-agent collusion monitoring. */
        public Builder enableCollusionAudit(boolean enabled) {
            this.enableCollusionAudit = enabled;
            return this;
        }

        /** Enables uncertainty-awareness scoring. */
        public Builder enableUncertainty(boolean enabled) {
            this.enableUncertainty = enabled;
            return this;
        }

        /** Attaches an arbitrary key/value context map to the run. */
        public Builder context(Map<String, Object> ctx) {
            this.context = ctx;
            return this;
        }

        /** Constructs the immutable {@link RunOptions}. */
        public RunOptions build() { return new RunOptions(this); }
    }
}
