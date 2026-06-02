package dev.meshflow;

import dev.meshflow.types.*;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.function.Consumer;

/**
 * Thread-safe HTTP client for the MeshFlow multi-agent orchestration platform.
 *
 * <p>Create once and reuse across requests:
 * <pre>{@code
 * MeshFlowClient client = new MeshFlowClient("http://localhost:8000", "my-api-key");
 *
 * // Blocking run
 * RunResult result = client.runAgent("Summarise the quarterly report");
 * System.out.println(result.getRunId());
 *
 * // Streaming run
 * client.streamAgent("Analyse this contract", event -> {
 *     if ("token_delta".equals(event.getEventType())) {
 *         System.out.print(event.getText());
 *     }
 * });
 * }</pre>
 *
 * <p>All HTTP calls use {@link java.net.http.HttpClient} (Java 11+).
 * JSON serialisation and deserialisation use only the standard library.
 */
public final class MeshFlowClient {

    private static final String SDK_VERSION = "1.5.0";
    private static final Duration DEFAULT_TIMEOUT = Duration.ofSeconds(120);

    private final String baseUrl;
    private final String apiKey;
    private final HttpClient http;

    /**
     * Creates a client that connects to the MeshFlow server at {@code baseUrl},
     * authenticating with {@code apiKey}.
     *
     * @param baseUrl the server root, e.g. {@code "http://localhost:8000"}
     * @param apiKey  the Bearer API key; pass an empty string for unauthenticated servers
     */
    public MeshFlowClient(String baseUrl, String apiKey) {
        this(baseUrl, apiKey, HttpClient.newBuilder()
                .connectTimeout(DEFAULT_TIMEOUT)
                .build());
    }

    /**
     * Creates a client with a custom {@link HttpClient} for advanced TLS,
     * proxy, or timeout configuration.
     */
    public MeshFlowClient(String baseUrl, String apiKey, HttpClient httpClient) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey;
        this.http = httpClient;
    }

    // ── Health ─────────────────────────────────────────────────────────────────

    /**
     * Calls {@code GET /health} and returns server status.
     * Authentication is not required.
     *
     * @return a {@link HealthResponse} describing server health
     * @throws IOException on network or non-2xx response
     */
    public HealthResponse health() throws IOException {
        String body = doGet("/health");
        Map<String, Object> m = JsonParser.parseObject(body);
        return new HealthResponse(
                asBoolean(m.get("ok")),
                asString(m.get("version")),
                asDouble(m.get("uptime_s")),
                asString(m.get("db")));
    }

    // ── Agent execution ────────────────────────────────────────────────────────

    /**
     * Executes {@code task} on the server and blocks until the run completes.
     *
     * @param task the natural-language task description
     * @return the completed {@link RunResult}
     * @throws IOException on network or non-2xx response
     */
    public RunResult runAgent(String task) throws IOException {
        return runAgent(task, RunOptions.builder().build());
    }

    /**
     * Executes {@code task} with the supplied {@link RunOptions} and blocks
     * until the run completes.
     *
     * @param task    the natural-language task description
     * @param options governance, budget, and compliance options
     * @return the completed {@link RunResult}
     * @throws IOException on network or non-2xx response
     */
    public RunResult runAgent(String task, RunOptions options) throws IOException {
        String reqJson = buildRunRequestJson(task, options);
        String respBody = doPost("/run", reqJson);
        return parseRunResult(JsonParser.parseObject(respBody));
    }

    /**
     * Starts a streaming task run over SSE / NDJSON and delivers each event
     * to {@code handler} synchronously on the calling thread.
     *
     * <p>The method returns only after the server closes the connection or
     * a terminal {@code run_complete} / {@code error} event is received.
     *
     * @param task    the natural-language task description
     * @param handler callback invoked for every {@link StreamEvent}
     * @throws IOException on network or non-2xx response
     */
    public void streamAgent(String task, Consumer<StreamEvent> handler) throws IOException {
        streamAgent(task, RunOptions.builder().build(), handler);
    }

    /**
     * Starts a streaming task run with options, delivering each event to
     * {@code handler}.
     *
     * @param task    the natural-language task description
     * @param options governance, budget, and compliance options
     * @param handler callback invoked for every {@link StreamEvent}
     * @throws IOException on network or non-2xx response
     */
    public void streamAgent(String task, RunOptions options, Consumer<StreamEvent> handler)
            throws IOException {
        String reqJson = buildRunRequestJson(task, options);

        HttpRequest req = requestBuilder("/stream")
                .header("Content-Type", "application/json")
                .header("Accept", "application/x-ndjson, text/event-stream")
                .POST(HttpRequest.BodyPublishers.ofString(reqJson, StandardCharsets.UTF_8))
                .build();

        HttpResponse<java.io.InputStream> resp;
        try {
            resp = http.send(req, HttpResponse.BodyHandlers.ofInputStream());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("meshflow: stream interrupted", e);
        }

        if (resp.statusCode() < 200 || resp.statusCode() > 299) {
            String errBody = new String(resp.body().readAllBytes(), StandardCharsets.UTF_8);
            throw new MeshFlowException(resp.statusCode(), "POST", "/stream", errBody.strip());
        }

        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(resp.body(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                line = line.strip();
                if (line.isEmpty()) continue;

                // Strip optional SSE "data: " prefix
                if (line.startsWith("data:")) {
                    line = line.substring(5).strip();
                }
                if (line.isEmpty() || "[DONE]".equals(line)) continue;

                try {
                    Map<String, Object> ev = JsonParser.parseObject(line);
                    handler.accept(parseStreamEvent(ev));
                } catch (Exception ignored) {
                    // Skip malformed lines — mirrors Go SDK behaviour
                }
            }
        }
    }

    // ── Traces ─────────────────────────────────────────────────────────────────

    /**
     * Returns the full execution trace for {@code runId}, including all step
     * records and the tamper-evident hash chain.
     *
     * @param runId the run identifier
     * @return the {@link Trace}
     * @throws IOException on network or non-2xx response
     */
    public Trace getTrace(String runId) throws IOException {
        String body = doGet("/traces/" + urlEncode(runId));
        return parseTrace(JsonParser.parseObject(body));
    }

    /**
     * Returns all run IDs recorded in the ledger.
     *
     * @return list of run identifiers
     * @throws IOException on network or non-2xx response
     */
    public List<String> listRuns() throws IOException {
        String body = doGet("/traces");
        Map<String, Object> m = JsonParser.parseObject(body);
        return asStringList(m.get("runs"));
    }

    // ── HITL ───────────────────────────────────────────────────────────────────

    /**
     * Approves the paused run identified by {@code runId}, allowing it to
     * continue execution.
     *
     * @param runId      the run to approve
     * @param reviewerId reviewer identifier forwarded to the audit log
     * @param notes      optional reviewer notes
     * @return {@code true} if the approval was accepted (HTTP 2xx)
     * @throws IOException on network error or HTTP 4xx/5xx
     */
    public boolean approveHITL(String runId, String reviewerId, String notes)
            throws IOException {
        String payload = buildHitlJson(reviewerId, notes);
        doPost("/hitl/" + urlEncode(runId) + "/approve", payload);
        return true;
    }

    /**
     * Rejects the paused run identified by {@code runId}, aborting execution.
     *
     * @param runId      the run to reject
     * @param reviewerId reviewer identifier forwarded to the audit log
     * @param notes      optional reviewer notes
     * @return {@code true} if the rejection was accepted (HTTP 2xx)
     * @throws IOException on network error or HTTP 4xx/5xx
     */
    public boolean rejectHITL(String runId, String reviewerId, String notes)
            throws IOException {
        String payload = buildHitlJson(reviewerId, notes);
        doPost("/hitl/" + urlEncode(runId) + "/reject", payload);
        return true;
    }

    // ── Zero Trust ─────────────────────────────────────────────────────────────

    /**
     * Returns the current Zero Trust posture snapshot from the server.
     *
     * @return a {@link ZTStatus} describing the active controls
     * @throws IOException on network or non-2xx response
     */
    public ZTStatus getZTStatus() throws IOException {
        String body = doGet("/api/zt-status");
        Map<String, Object> m = JsonParser.parseObject(body);
        return new ZTStatus(
                asString(m.get("tier")),
                asString(m.get("regulation")),
                asInt(m.get("score_pct")),
                asInt(m.get("controls_enabled")),
                asInt(m.get("controls_gap")),
                asString(m.get("env_tier")),
                asString(m.get("env_regulation")));
    }

    // ── private HTTP helpers ───────────────────────────────────────────────────

    private HttpRequest.Builder requestBuilder(String path) {
        HttpRequest.Builder b = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(DEFAULT_TIMEOUT)
                .header("Accept", "application/json")
                .header("User-Agent", "meshflow-java-sdk/" + SDK_VERSION);
        if (apiKey != null && !apiKey.isEmpty()) {
            b.header("Authorization", "Bearer " + apiKey);
        }
        return b;
    }

    private String doGet(String path) throws IOException {
        HttpRequest req = requestBuilder(path).GET().build();
        return sendAndRead(req, "GET", path);
    }

    private String doPost(String path, String jsonBody) throws IOException {
        HttpRequest req = requestBuilder(path)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(jsonBody, StandardCharsets.UTF_8))
                .build();
        return sendAndRead(req, "POST", path);
    }

    private String sendAndRead(HttpRequest req, String method, String path) throws IOException {
        HttpResponse<String> resp;
        try {
            resp = http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("meshflow: request interrupted", e);
        }
        if (resp.statusCode() < 200 || resp.statusCode() > 299) {
            throw new MeshFlowException(resp.statusCode(), method, path,
                    resp.body() == null ? "" : resp.body().strip());
        }
        return resp.body();
    }

    // ── JSON request builders ──────────────────────────────────────────────────

    /** Builds the JSON payload for POST /run and POST /stream. */
    private static String buildRunRequestJson(String task, RunOptions o) {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"task\":").append(JsonSerializer.quoteString(task));

        Map<String, Object> policy = new LinkedHashMap<>();
        if (o.getPolicyMode() != null && !o.getPolicyMode().isEmpty()) {
            policy.put("mode", o.getPolicyMode());
        }
        if (o.getCostCapUsd() > 0) policy.put("budget_usd", o.getCostCapUsd());
        if (o.getBudgetTokens() > 0) policy.put("budget_tokens", o.getBudgetTokens());
        if (o.getTimeoutS() > 0) policy.put("timeout_s", o.getTimeoutS());
        if (o.getMaxSteps() > 0) policy.put("max_steps", o.getMaxSteps());
        if (o.isDeterministicGate()) policy.put("deterministic_gate", true);
        if (o.isEnableGuardian()) policy.put("enable_guardian", true);
        if (o.isEnableCollusionAudit()) policy.put("enable_collusion_audit", true);
        if (o.isEnableUncertainty()) policy.put("enable_uncertainty", true);

        if (!policy.isEmpty()) {
            sb.append(",\"policy\":").append(JsonSerializer.serializeMap(policy));
        }
        if (o.getContext() != null && !o.getContext().isEmpty()) {
            sb.append(",\"context\":").append(JsonSerializer.serializeMap(o.getContext()));
        }
        sb.append('}');
        return sb.toString();
    }

    private static String buildHitlJson(String reviewerId, String notes) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        if (reviewerId != null && !reviewerId.isEmpty()) {
            sb.append("\"reviewer_id\":").append(JsonSerializer.quoteString(reviewerId));
            first = false;
        }
        if (notes != null && !notes.isEmpty()) {
            if (!first) sb.append(',');
            sb.append("\"notes\":").append(JsonSerializer.quoteString(notes));
        }
        sb.append('}');
        return sb.toString();
    }

    // ── Response object parsers ────────────────────────────────────────────────

    private static RunResult parseRunResult(Map<String, Object> m) {
        return new RunResult(
                asString(m.get("run_id")),
                asString(m.get("status")),
                m.get("output"),
                asDouble(m.get("total_cost_usd")),
                asInt(m.get("total_tokens")),
                asDouble(m.get("total_carbon_g")),
                asDouble(m.get("duration_s")),
                asInt(m.get("ledger_entries")),
                asString(m.get("trace_id")),
                asStringList(m.get("checkpoints")),
                asString(m.get("error")),
                asInt(m.get("collusion_alerts")),
                asStringMap(m.get("agent_states")));
    }

    private static StreamEvent parseStreamEvent(Map<String, Object> m) {
        // The server may send output as a plain string or a JSON object.
        // Normalise into Map<String,Object> for structured access.
        Object rawData = m.get("output");
        Map<String, Object> dataMap;
        if (rawData instanceof Map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> cast = (Map<String, Object>) rawData;
            dataMap = cast;
        } else {
            dataMap = rawData != null ? Map.of("value", rawData) : Map.of();
        }

        return new StreamEvent(
                asString(m.get("kind")),
                asString(m.get("agent_id")),
                asString(m.get("role")),
                dataMap,
                asString(m.get("text")),
                asString(m.get("run_id")),
                asInt(m.get("step")),
                asString(m.get("step_id")),
                asString(m.get("node_id")),
                asDouble(m.get("uncertainty")),
                asDouble(m.get("cost_usd")),
                asInt(m.get("tokens")),
                asString(m.get("blocked_by")),
                asString(m.get("error")),
                asDouble(m.get("timestamp")));
    }

    @SuppressWarnings("unchecked")
    private static Trace parseTrace(Map<String, Object> m) {
        Map<String, Object> sumMap = m.get("summary") instanceof Map
                ? (Map<String, Object>) m.get("summary")
                : Map.of();

        Object tsRaw = sumMap.get("timestamps");
        String tsStart = null, tsEnd = null;
        if (tsRaw instanceof Map) {
            Map<String, Object> ts = (Map<String, Object>) tsRaw;
            tsStart = asString(ts.get("start"));
            tsEnd = asString(ts.get("end"));
        }

        Trace.TraceSummary summary = new Trace.TraceSummary(
                asInt(sumMap.get("steps")),
                asStringList(sumMap.get("nodes")),
                asDouble(sumMap.get("total_cost_usd")),
                asInt(sumMap.get("total_tokens")),
                asDouble(sumMap.get("total_carbon_gco2")),
                asInt(sumMap.get("blocked_steps")),
                asStringList(sumMap.get("verdicts")),
                tsStart,
                tsEnd);

        List<TraceStep> steps = new ArrayList<>();
        if (m.get("steps") instanceof List) {
            for (Object stepObj : (List<?>) m.get("steps")) {
                if (stepObj instanceof Map) {
                    steps.add(parseTraceStep((Map<String, Object>) stepObj));
                }
            }
        }

        return new Trace(asString(m.get("run_id")), summary, steps);
    }

    private static TraceStep parseTraceStep(Map<String, Object> m) {
        return new TraceStep(
                asString(m.get("step_id")),
                asString(m.get("run_id")),
                asString(m.get("node_id")),
                asString(m.get("node_kind")),
                asString(m.get("input_task")),
                asString(m.get("output_content")),
                asString(m.get("verdict")),
                asBoolean(m.get("blocked")),
                asString(m.get("block_reason")),
                asDouble(m.get("uncertainty")),
                asDouble(m.get("cost_usd")),
                asInt(m.get("tokens_used")),
                asDouble(m.get("carbon_gco2")),
                asDouble(m.get("duration_ms")),
                asString(m.get("timestamp")),
                asString(m.get("prev_hash")),
                asString(m.get("entry_hash")));
    }

    // ── JSON value coercions ───────────────────────────────────────────────────

    private static String asString(Object v) {
        return v == null ? null : v.toString();
    }

    private static double asDouble(Object v) {
        if (v == null) return 0.0;
        if (v instanceof Number) return ((Number) v).doubleValue();
        try { return Double.parseDouble(v.toString()); }
        catch (NumberFormatException e) { return 0.0; }
    }

    private static int asInt(Object v) {
        if (v == null) return 0;
        if (v instanceof Number) return ((Number) v).intValue();
        try { return Integer.parseInt(v.toString()); }
        catch (NumberFormatException e) { return 0; }
    }

    private static boolean asBoolean(Object v) {
        if (v == null) return false;
        if (v instanceof Boolean) return (Boolean) v;
        return "true".equalsIgnoreCase(v.toString());
    }

    private static List<String> asStringList(Object v) {
        if (!(v instanceof List)) return List.of();
        List<?> raw = (List<?>) v;
        List<String> out = new ArrayList<>(raw.size());
        for (Object item : raw) {
            if (item != null) out.add(item.toString());
        }
        return Collections.unmodifiableList(out);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, String> asStringMap(Object v) {
        if (!(v instanceof Map)) return Map.of();
        Map<?, ?> raw = (Map<?, ?>) v;
        Map<String, String> out = new LinkedHashMap<>();
        for (Map.Entry<?, ?> e : raw.entrySet()) {
            out.put(String.valueOf(e.getKey()),
                    e.getValue() == null ? null : e.getValue().toString());
        }
        return Collections.unmodifiableMap(out);
    }

    /** Percent-encodes a URL path segment (RFC 3986 unreserved chars are left as-is). */
    private static String urlEncode(String s) {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder();
        byte[] bytes = s.getBytes(StandardCharsets.UTF_8);
        for (byte b : bytes) {
            int c = b & 0xFF;
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                    (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
                sb.append((char) c);
            } else {
                sb.append(String.format("%%%02X", c));
            }
        }
        return sb.toString();
    }
}
