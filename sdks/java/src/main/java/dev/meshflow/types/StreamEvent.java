package dev.meshflow.types;

import java.util.Map;

/**
 * A single event emitted over the SSE {@code /stream} or {@code /events}
 * endpoint.
 *
 * <p>Common {@code eventType} values:
 * <ul>
 *   <li>{@code token_delta} — incremental token text; read {@link #getData()}</li>
 *   <li>{@code step_start} / {@code step_end} — agent step lifecycle</li>
 *   <li>{@code run_complete} — terminal event containing final cost/tokens</li>
 *   <li>{@code error} — server-side error</li>
 * </ul>
 */
public final class StreamEvent {

    /** Kind/type of this event (maps to JSON field {@code "kind"}). */
    private final String eventType;

    /** Originating agent identifier. */
    private final String agentId;

    /** Agent role within the pipeline (e.g. {@code "planner"}, {@code "executor"}). */
    private final String role;

    /**
     * Structured event payload parsed from the JSON {@code "output"} field.
     * For {@code token_delta} events, prefer {@link #getText()}.
     */
    private final Map<String, Object> data;

    /** Raw text content for {@code token_delta} events. */
    private final String text;

    /** Run identifier this event belongs to. */
    private final String runId;

    /** Step index within the run. */
    private final int step;

    /** Step identifier (UUID). */
    private final String stepId;

    /** Node identifier within the agent graph. */
    private final String nodeId;

    /** Uncertainty score (0.0–1.0) for this step. */
    private final double uncertainty;

    /** Incremental LLM cost in USD for this event. */
    private final double costUsd;

    /** Tokens consumed by this event. */
    private final int tokens;

    /** Control that blocked this step, if any. */
    private final String blockedBy;

    /** Error message, populated for {@code error} events. */
    private final String errorMsg;

    /** Unix epoch timestamp in seconds. */
    private final double timestamp;

    public StreamEvent(
            String eventType,
            String agentId,
            String role,
            Map<String, Object> data,
            String text,
            String runId,
            int step,
            String stepId,
            String nodeId,
            double uncertainty,
            double costUsd,
            int tokens,
            String blockedBy,
            String errorMsg,
            double timestamp) {
        this.eventType = eventType;
        this.agentId = agentId;
        this.role = role;
        this.data = data == null ? Map.of() : Map.copyOf(data);
        this.text = text;
        this.runId = runId;
        this.step = step;
        this.stepId = stepId;
        this.nodeId = nodeId;
        this.uncertainty = uncertainty;
        this.costUsd = costUsd;
        this.tokens = tokens;
        this.blockedBy = blockedBy;
        this.errorMsg = errorMsg;
        this.timestamp = timestamp;
    }

    public String getEventType() { return eventType; }
    public String getAgentId() { return agentId; }
    public String getRole() { return role; }
    public Map<String, Object> getData() { return data; }
    public String getText() { return text; }
    public String getRunId() { return runId; }
    public int getStep() { return step; }
    public String getStepId() { return stepId; }
    public String getNodeId() { return nodeId; }
    public double getUncertainty() { return uncertainty; }
    public double getCostUsd() { return costUsd; }
    public int getTokens() { return tokens; }
    public String getBlockedBy() { return blockedBy; }
    public String getErrorMsg() { return errorMsg; }
    public double getTimestamp() { return timestamp; }

    @Override
    public String toString() {
        return "StreamEvent{kind='" + eventType + "', runId='" + runId +
               "', step=" + step + ", agentId='" + agentId + "'}";
    }
}
