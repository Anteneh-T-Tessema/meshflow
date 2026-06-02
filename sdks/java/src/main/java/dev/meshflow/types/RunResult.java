package dev.meshflow.types;

import java.util.List;
import java.util.Map;

/**
 * Returned by {@code MeshFlowClient.runAgent()} once the task has completed.
 *
 * <p>All monetary values are in US dollars. Duration is in seconds.
 * {@code status} will be one of: {@code pending}, {@code running},
 * {@code paused}, {@code completed}, {@code failed}, {@code aborted}.
 */
public final class RunResult {

    private final String runId;
    private final String status;
    private final Object output;
    private final double totalCostUsd;
    private final int totalTokens;
    private final double totalCarbonG;
    private final double durationS;
    private final int ledgerEntries;
    private final String traceId;
    private final List<String> checkpoints;
    private final String error;
    private final int collusionAlerts;
    private final Map<String, String> agentStates;

    public RunResult(
            String runId,
            String status,
            Object output,
            double totalCostUsd,
            int totalTokens,
            double totalCarbonG,
            double durationS,
            int ledgerEntries,
            String traceId,
            List<String> checkpoints,
            String error,
            int collusionAlerts,
            Map<String, String> agentStates) {
        this.runId = runId;
        this.status = status;
        this.output = output;
        this.totalCostUsd = totalCostUsd;
        this.totalTokens = totalTokens;
        this.totalCarbonG = totalCarbonG;
        this.durationS = durationS;
        this.ledgerEntries = ledgerEntries;
        this.traceId = traceId;
        this.checkpoints = checkpoints == null ? List.of() : List.copyOf(checkpoints);
        this.error = error;
        this.collusionAlerts = collusionAlerts;
        this.agentStates = agentStates == null ? Map.of() : Map.copyOf(agentStates);
    }

    /** The unique run identifier assigned by the server. */
    public String getRunId() { return runId; }

    /** Lifecycle status of this run. */
    public String getStatus() { return status; }

    /** Raw output produced by the agent pipeline. May be a String, Map, or null. */
    public Object getOutput() { return output; }

    /** Total LLM spend for this run in USD. */
    public double getTotalCostUsd() { return totalCostUsd; }

    /** Total tokens consumed across all agents in this run. */
    public int getTotalTokens() { return totalTokens; }

    /** Estimated carbon footprint in grams of CO₂. */
    public double getTotalCarbonG() { return totalCarbonG; }

    /** Wall-clock time for the run in seconds. */
    public double getDurationS() { return durationS; }

    /** Number of ledger entries written for this run. */
    public int getLedgerEntries() { return ledgerEntries; }

    /** Trace/correlation identifier. */
    public String getTraceId() { return traceId; }

    /** Checkpoint identifiers created during the run. */
    public List<String> getCheckpoints() { return checkpoints; }

    /** Error message if the run failed; otherwise null or empty. */
    public String getError() { return error; }

    /** Number of inter-agent collusion alerts raised during this run. */
    public int getCollusionAlerts() { return collusionAlerts; }

    /** Per-agent state map at run completion. */
    public Map<String, String> getAgentStates() { return agentStates; }

    @Override
    public String toString() {
        return "RunResult{runId='" + runId + "', status='" + status +
               "', totalCostUsd=" + totalCostUsd + ", totalTokens=" + totalTokens +
               ", durationS=" + durationS + '}';
    }
}
