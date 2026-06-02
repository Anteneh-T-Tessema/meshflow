package dev.meshflow.types;

/**
 * A single ledger record within a run trace.
 *
 * <p>The {@code prevHash} / {@code entryHash} pair maintains the tamper-evident
 * cryptographic hash chain across all steps in the run.
 */
public final class TraceStep {

    private final String stepId;
    private final String runId;
    private final String nodeId;
    private final String nodeKind;
    private final String inputTask;
    private final String outputContent;
    private final String verdict;
    private final boolean blocked;
    private final String blockReason;
    private final double uncertainty;
    private final double costUsd;
    private final int tokensUsed;
    private final double carbonGCO2;
    private final double durationMs;
    private final String timestamp;
    private final String prevHash;
    private final String entryHash;

    public TraceStep(
            String stepId,
            String runId,
            String nodeId,
            String nodeKind,
            String inputTask,
            String outputContent,
            String verdict,
            boolean blocked,
            String blockReason,
            double uncertainty,
            double costUsd,
            int tokensUsed,
            double carbonGCO2,
            double durationMs,
            String timestamp,
            String prevHash,
            String entryHash) {
        this.stepId = stepId;
        this.runId = runId;
        this.nodeId = nodeId;
        this.nodeKind = nodeKind;
        this.inputTask = inputTask;
        this.outputContent = outputContent;
        this.verdict = verdict;
        this.blocked = blocked;
        this.blockReason = blockReason;
        this.uncertainty = uncertainty;
        this.costUsd = costUsd;
        this.tokensUsed = tokensUsed;
        this.carbonGCO2 = carbonGCO2;
        this.durationMs = durationMs;
        this.timestamp = timestamp;
        this.prevHash = prevHash;
        this.entryHash = entryHash;
    }

    public String getStepId() { return stepId; }
    public String getRunId() { return runId; }
    public String getNodeId() { return nodeId; }
    public String getNodeKind() { return nodeKind; }
    public String getInputTask() { return inputTask; }
    public String getOutputContent() { return outputContent; }
    public String getVerdict() { return verdict; }
    public boolean isBlocked() { return blocked; }
    public String getBlockReason() { return blockReason; }
    public double getUncertainty() { return uncertainty; }
    public double getCostUsd() { return costUsd; }
    public int getTokensUsed() { return tokensUsed; }
    public double getCarbonGCO2() { return carbonGCO2; }
    public double getDurationMs() { return durationMs; }
    public String getTimestamp() { return timestamp; }

    /** SHA-256 hash of the previous ledger entry — the chain link. */
    public String getPrevHash() { return prevHash; }

    /** SHA-256 hash of this entry's content — the tamper-evident seal. */
    public String getEntryHash() { return entryHash; }

    @Override
    public String toString() {
        return "TraceStep{stepId='" + stepId + "', nodeId='" + nodeId +
               "', blocked=" + blocked + ", costUsd=" + costUsd + '}';
    }
}
