package dev.meshflow.types;

/**
 * Returned by {@code GET /health}.
 *
 * <p>{@code ok} is {@code true} when the server is healthy and all
 * subsystems (database, ledger, etc.) are operational.
 */
public final class HealthResponse {

    private final boolean ok;
    private final String version;
    private final double uptimeS;
    private final String db;

    public HealthResponse(boolean ok, String version, double uptimeS, String db) {
        this.ok = ok;
        this.version = version;
        this.uptimeS = uptimeS;
        this.db = db;
    }

    /** {@code true} if the server is healthy. */
    public boolean isOk() { return ok; }

    /** Server version string (e.g. {@code "1.5.0"}). */
    public String getVersion() { return version; }

    /** Server uptime in seconds since the last restart. */
    public double getUptimeS() { return uptimeS; }

    /**
     * Database status string. Typically {@code "ok"} or an error
     * description if the database is unavailable.
     */
    public String getDb() { return db; }

    @Override
    public String toString() {
        return "HealthResponse{ok=" + ok + ", version='" + version +
               "', uptimeS=" + uptimeS + ", db='" + db + "'}";
    }
}
