package dev.meshflow.types;

import java.util.List;

/**
 * Full execution record for a single MeshFlow run, including all
 * {@link TraceStep} records and aggregated {@link TraceSummary} statistics.
 */
public final class Trace {

    private final String runId;
    private final TraceSummary summary;
    private final List<TraceStep> steps;

    public Trace(String runId, TraceSummary summary, List<TraceStep> steps) {
        this.runId = runId;
        this.summary = summary;
        this.steps = steps == null ? List.of() : List.copyOf(steps);
    }

    /** The run identifier this trace belongs to. */
    public String getRunId() { return runId; }

    /** Aggregated statistics across all steps. */
    public TraceSummary getSummary() { return summary; }

    /** Ordered list of ledger steps, oldest first. */
    public List<TraceStep> getSteps() { return steps; }

    @Override
    public String toString() {
        return "Trace{runId='" + runId + "', steps=" + steps.size() + '}';
    }

    // ── Inner summary class ────────────────────────────────────────────────────

    /** Aggregated statistics across all steps in a run. */
    public static final class TraceSummary {

        private final int steps;
        private final List<String> nodes;
        private final double totalCostUsd;
        private final int totalTokens;
        private final double totalCarbonGCO2;
        private final int blockedSteps;
        private final List<String> verdicts;
        private final String timestampStart;
        private final String timestampEnd;

        public TraceSummary(
                int steps,
                List<String> nodes,
                double totalCostUsd,
                int totalTokens,
                double totalCarbonGCO2,
                int blockedSteps,
                List<String> verdicts,
                String timestampStart,
                String timestampEnd) {
            this.steps = steps;
            this.nodes = nodes == null ? List.of() : List.copyOf(nodes);
            this.totalCostUsd = totalCostUsd;
            this.totalTokens = totalTokens;
            this.totalCarbonGCO2 = totalCarbonGCO2;
            this.blockedSteps = blockedSteps;
            this.verdicts = verdicts == null ? List.of() : List.copyOf(verdicts);
            this.timestampStart = timestampStart;
            this.timestampEnd = timestampEnd;
        }

        public int getSteps() { return steps; }
        public List<String> getNodes() { return nodes; }
        public double getTotalCostUsd() { return totalCostUsd; }
        public int getTotalTokens() { return totalTokens; }
        public double getTotalCarbonGCO2() { return totalCarbonGCO2; }
        public int getBlockedSteps() { return blockedSteps; }
        public List<String> getVerdicts() { return verdicts; }
        public String getTimestampStart() { return timestampStart; }
        public String getTimestampEnd() { return timestampEnd; }
    }
}
