package dev.meshflow.types;

/**
 * Zero Trust posture snapshot returned by {@code GET /api/zt-status}.
 *
 * <p>{@code scorePct} is an integer 0–100 representing the percentage of
 * controls currently enabled relative to the target tier.
 * {@code controlsGap} is the count of controls missing for the target tier.
 */
public final class ZTStatus {

    private final String tier;
    private final String regulation;
    private final int scorePct;
    private final int controlsEnabled;
    private final int controlsGap;
    private final String envTier;
    private final String envRegulation;

    public ZTStatus(
            String tier,
            String regulation,
            int scorePct,
            int controlsEnabled,
            int controlsGap,
            String envTier,
            String envRegulation) {
        this.tier = tier;
        this.regulation = regulation;
        this.scorePct = scorePct;
        this.controlsEnabled = controlsEnabled;
        this.controlsGap = controlsGap;
        this.envTier = envTier;
        this.envRegulation = envRegulation;
    }

    /** Active Zero Trust tier name: {@code "foundation"}, {@code "enterprise"}, or {@code "advanced"}. */
    public String getTier() { return tier; }

    /** Regulatory profile in effect, e.g. {@code "hipaa"}, or null if none. */
    public String getRegulation() { return regulation; }

    /**
     * Posture score as a percentage (0–100). A score of 100 means every
     * control required by the current tier is active.
     */
    public int getScorePct() { return scorePct; }

    /** Number of Zero Trust controls currently active on the server. */
    public int getControlsEnabled() { return controlsEnabled; }

    /** Number of controls missing relative to the target tier. */
    public int getControlsGap() { return controlsGap; }

    /**
     * Tier value read from the server environment variable
     * {@code MESHFLOW_ZT_TIER}. May differ from {@link #getTier()} if
     * the tier was overridden at runtime.
     */
    public String getEnvTier() { return envTier; }

    /** Regulation read from the environment, if set. */
    public String getEnvRegulation() { return envRegulation; }

    @Override
    public String toString() {
        return "ZTStatus{tier='" + tier + "', scorePct=" + scorePct +
               ", controlsEnabled=" + controlsEnabled + ", controlsGap=" + controlsGap + '}';
    }
}
