// Package meshflow provides an idiomatic Go client for the MeshFlow
// multi-agent orchestration platform.
//
// Quick start:
//
//	client := meshflow.NewClient("http://localhost:8000", "my-api-key")
//
//	// Run a governed task and wait for completion
//	result, err := client.RunAgent(ctx, "Summarise the quarterly report")
//
//	// Stream token-by-token events
//	events, err := client.Stream(ctx, "Analyse this contract")
//	for ev := range events {
//	    if ev.EventType == "token_delta" {
//	        fmt.Print(ev.Text)
//	    }
//	}
package meshflow

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const (
	defaultTimeout = 120 * time.Second
	sdkVersion     = "1.14.0"
)

// Client is the MeshFlow API client. Create one with NewClient and reuse it
// across requests — it is safe for concurrent use.
type Client struct {
	// BaseURL is the MeshFlow server root, e.g. "http://localhost:8000".
	BaseURL string

	// APIKey is sent as a Bearer token. May be empty for unauthenticated servers.
	APIKey string

	// HTTPClient is the underlying transport. Defaults to a client with a
	// 120-second timeout. Replace to customise TLS, proxies, or timeouts.
	HTTPClient *http.Client

	// Timeout is the per-request deadline applied when the caller does not
	// provide a context with a deadline. Defaults to 120 seconds.
	Timeout time.Duration
}

// NewClient creates a Client that talks to the MeshFlow server at baseURL,
// authenticating with the supplied apiKey. Pass an empty string for apiKey
// when connecting to an unauthenticated development server.
func NewClient(baseURL, apiKey string) *Client {
	baseURL = strings.TrimRight(baseURL, "/")
	return &Client{
		BaseURL: baseURL,
		APIKey:  apiKey,
		HTTPClient: &http.Client{
			Timeout: defaultTimeout,
		},
		Timeout: defaultTimeout,
	}
}

// ── internal helpers ──────────────────────────────────────────────────────────

// headers returns the standard request headers, injecting Authorization when
// an API key is configured.
func (c *Client) headers() map[string]string {
	h := map[string]string{
		"Content-Type": "application/json",
		"Accept":       "application/json",
		"User-Agent":   "meshflow-go-sdk/" + sdkVersion,
	}
	if c.APIKey != "" {
		h["Authorization"] = "Bearer " + c.APIKey
	}
	return h
}

// do executes an HTTP request, decodes a JSON response into dst, and returns
// a structured *Error for non-2xx responses.
func (c *Client) do(ctx context.Context, method, path string, body interface{}, dst interface{}) error {
	var bodyReader io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("meshflow: marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(b)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, bodyReader)
	if err != nil {
		return fmt.Errorf("meshflow: build request: %w", err)
	}
	for k, v := range c.headers() {
		req.Header.Set(k, v)
	}

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("meshflow: %s %s: %w", method, path, err)
	}
	defer resp.Body.Close()

	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &Error{
			StatusCode: resp.StatusCode,
			Method:     method,
			Path:       path,
			Body:       strings.TrimSpace(string(raw)),
		}
	}

	if dst != nil {
		if err := json.Unmarshal(raw, dst); err != nil {
			return fmt.Errorf("meshflow: decode response from %s %s: %w", method, path, err)
		}
	}
	return nil
}

// buildPolicy converts a RunOptions into the policy map expected by the server.
func buildPolicy(o RunOptions) map[string]interface{} {
	p := make(map[string]interface{})
	if o.PolicyMode != "" {
		p["mode"] = o.PolicyMode
	}
	if o.CostCapUSD > 0 {
		p["budget_usd"] = o.CostCapUSD
	}
	if o.BudgetTokens > 0 {
		p["budget_tokens"] = o.BudgetTokens
	}
	if o.TimeoutS > 0 {
		p["timeout_s"] = o.TimeoutS
	}
	if o.MaxSteps > 0 {
		p["max_steps"] = o.MaxSteps
	}
	if o.DeterministicGate {
		p["deterministic_gate"] = true
	}
	if o.EnableGuardian {
		p["enable_guardian"] = true
	}
	if o.EnableCollusionAudit {
		p["enable_collusion_audit"] = true
	}
	if o.EnableUncertainty {
		p["enable_uncertainty"] = true
	}
	// v1.10.0 — routing
	if len(o.ModelTiers) > 0 {
		p["model_tiers"] = o.ModelTiers
	}
	if o.SmartThreshold > 0 {
		p["smart_threshold"] = o.SmartThreshold
	}
	if o.LargeThreshold > 0 {
		p["large_threshold"] = o.LargeThreshold
	}
	if o.CascadeThreshold > 0 {
		p["cascade_threshold"] = o.CascadeThreshold
	}
	if o.MaxEscalations > 0 {
		p["max_escalations"] = o.MaxEscalations
	}
	// v1.10.0 — extended thinking
	if o.ThinkingBudget > 0 {
		p["thinking_budget"] = o.ThinkingBudget
	}
	if len(p) == 0 {
		return nil
	}
	return p
}

// ── Health ────────────────────────────────────────────────────────────────────

// Health calls GET /health and returns server status. Authentication is not
// required.
func (c *Client) Health(ctx context.Context) (*HealthResponse, error) {
	var out HealthResponse
	if err := c.do(ctx, http.MethodGet, "/health", nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// HealthLive calls GET /health/live (Kubernetes liveness probe).
func (c *Client) HealthLive(ctx context.Context) (*ProbeResponse, error) {
	var out ProbeResponse
	if err := c.do(ctx, http.MethodGet, "/health/live", nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// HealthReady calls GET /health/ready (Kubernetes readiness probe).
// Returns an *Error with StatusCode 503 during graceful shutdown.
func (c *Client) HealthReady(ctx context.Context) (*ProbeResponse, error) {
	var out ProbeResponse
	if err := c.do(ctx, http.MethodGet, "/health/ready", nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Task execution ────────────────────────────────────────────────────────────

// RunAgent executes task on the MeshFlow server and blocks until the run
// completes, returning the full RunResult. Use Stream for incremental output.
func (c *Client) RunAgent(ctx context.Context, task string, opts ...RunOption) (*RunResult, error) {
	o := applyOptions(opts)
	reqBody := runRequestBody{
		Task:    task,
		Policy:  buildPolicy(o),
		Context: o.Context,
	}
	var out RunResult
	if err := c.do(ctx, http.MethodPost, "/run", reqBody, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Stream starts a streaming task run and returns a read-only channel of
// StreamEvents. The server sends NDJSON lines over a persistent HTTP
// connection; the channel is closed when the connection ends or ctx is
// cancelled.
//
// The caller must drain the channel completely or cancel ctx to avoid
// goroutine leaks.
func (c *Client) Stream(ctx context.Context, task string, opts ...RunOption) (<-chan StreamEvent, error) {
	o := applyOptions(opts)
	reqBody := runRequestBody{
		Task:    task,
		Policy:  buildPolicy(o),
		Context: o.Context,
	}

	b, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("meshflow: marshal stream request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/stream", bytes.NewReader(b))
	if err != nil {
		return nil, fmt.Errorf("meshflow: build stream request: %w", err)
	}
	for k, v := range c.headers() {
		req.Header.Set(k, v)
	}
	// Accept both NDJSON and SSE; server may send either.
	req.Header.Set("Accept", "application/x-ndjson, text/event-stream")

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("meshflow: stream connect: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return nil, &Error{
			StatusCode: resp.StatusCode,
			Method:     http.MethodPost,
			Path:       "/stream",
			Body:       strings.TrimSpace(string(raw)),
		}
	}

	ch := make(chan StreamEvent, 64)
	go func() {
		defer close(ch)
		defer resp.Body.Close()
		scanner := bufio.NewScanner(resp.Body)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" {
				continue
			}
			// Strip optional SSE "data: " prefix
			if strings.HasPrefix(line, "data:") {
				line = strings.TrimSpace(line[len("data:"):])
			}
			if line == "" || line == "[DONE]" {
				continue
			}
			var ev StreamEvent
			if err := json.Unmarshal([]byte(line), &ev); err != nil {
				continue // skip malformed lines
			}
			select {
			case ch <- ev:
			case <-ctx.Done():
				return
			}
		}
	}()

	return ch, nil
}

// LiveEvents subscribes to the SSE /events endpoint and returns a channel of
// StreamEvents. Optionally pass a runID to filter events to a specific run.
// The channel is closed when the connection ends or ctx is cancelled.
func (c *Client) LiveEvents(ctx context.Context, runID string) (<-chan StreamEvent, error) {
	path := "/events"
	if runID != "" {
		path += "?run_id=" + url.QueryEscape(runID)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return nil, fmt.Errorf("meshflow: build live-events request: %w", err)
	}
	for k, v := range c.headers() {
		req.Header.Set(k, v)
	}
	req.Header.Set("Accept", "text/event-stream")

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("meshflow: live-events connect: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return nil, &Error{
			StatusCode: resp.StatusCode,
			Method:     http.MethodGet,
			Path:       path,
			Body:       strings.TrimSpace(string(raw)),
		}
	}

	ch := make(chan StreamEvent, 64)
	go func() {
		defer close(ch)
		defer resp.Body.Close()

		// SSE frames are separated by blank lines; each frame may contain
		// one or more "field: value" lines. We accumulate data lines.
		scanner := bufio.NewScanner(resp.Body)
		var dataLines []string

		flush := func() {
			if len(dataLines) == 0 {
				return
			}
			payload := strings.Join(dataLines, "\n")
			dataLines = dataLines[:0]
			var ev StreamEvent
			if err := json.Unmarshal([]byte(payload), &ev); err != nil {
				return
			}
			// Skip the initial handshake {"ok": true}
			if ev.EventType == "" && ev.RunID == "" {
				return
			}
			select {
			case ch <- ev:
			case <-ctx.Done():
			}
		}

		for scanner.Scan() {
			line := scanner.Text()
			if line == "" {
				flush()
				continue
			}
			if strings.HasPrefix(line, "data:") {
				data := strings.TrimSpace(line[len("data:"):])
				dataLines = append(dataLines, data)
			}
		}
		flush() // emit any final buffered frame
	}()

	return ch, nil
}

// ── Traces ────────────────────────────────────────────────────────────────────

// ListRuns returns all run IDs recorded in the ledger.
func (c *Client) ListRuns(ctx context.Context) ([]string, error) {
	var out struct {
		Runs []string `json:"runs"`
	}
	if err := c.do(ctx, http.MethodGet, "/traces", nil, &out); err != nil {
		return nil, err
	}
	return out.Runs, nil
}

// GetTrace returns the full execution trace for runID, including all step
// records and the tamper-evident hash chain.
func (c *Client) GetTrace(ctx context.Context, runID string) (*Trace, error) {
	var out Trace
	if err := c.do(ctx, http.MethodGet, "/traces/"+url.PathEscape(runID), nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetGraph returns the execution graph for runID serialised as Mermaid or DOT.
func (c *Client) GetGraph(ctx context.Context, runID, format string) (string, error) {
	if format == "" {
		format = "mermaid"
	}
	path := "/graph/" + url.PathEscape(runID) + "?format=" + url.QueryEscape(format)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return "", fmt.Errorf("meshflow: build graph request: %w", err)
	}
	for k, v := range c.headers() {
		req.Header.Set(k, v)
	}
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("meshflow: get graph: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return "", &Error{StatusCode: resp.StatusCode, Method: http.MethodGet, Path: path, Body: string(raw)}
	}
	return string(raw), nil
}

// ExportAudit exports the audit trail as JSON or CSV. Pass an empty runID to
// export all runs.
func (c *Client) ExportAudit(ctx context.Context, runID, format string) (string, error) {
	if format == "" {
		format = "json"
	}
	q := url.Values{"format": {format}}
	if runID != "" {
		q.Set("run_id", runID)
	}
	path := "/audit/export?" + q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return "", fmt.Errorf("meshflow: build audit-export request: %w", err)
	}
	for k, v := range c.headers() {
		req.Header.Set(k, v)
	}
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("meshflow: export audit: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return "", &Error{StatusCode: resp.StatusCode, Method: http.MethodGet, Path: path, Body: string(raw)}
	}
	return string(raw), nil
}

// ── HITL ──────────────────────────────────────────────────────────────────────

// ListPendingHITL returns all runs currently paused for human approval.
func (c *Client) ListPendingHITL(ctx context.Context) ([]PausedRun, error) {
	var out struct {
		PausedRuns []PausedRun `json:"paused_runs"`
	}
	if err := c.do(ctx, http.MethodGet, "/hitl/pending", nil, &out); err != nil {
		return nil, err
	}
	return out.PausedRuns, nil
}

// ApproveHITL approves the paused run identified by runID, allowing it to
// continue execution. reviewerID and notes are forwarded to the audit log.
func (c *Client) ApproveHITL(ctx context.Context, runID, reviewerID, notes string) error {
	body := hitlDecisionBody{ReviewerID: reviewerID, Notes: notes}
	return c.do(ctx, http.MethodPost, "/hitl/"+url.PathEscape(runID)+"/approve", body, nil)
}

// RejectHITL rejects the paused run identified by runID, aborting execution.
func (c *Client) RejectHITL(ctx context.Context, runID, reviewerID, notes string) error {
	body := hitlDecisionBody{ReviewerID: reviewerID, Notes: notes}
	return c.do(ctx, http.MethodPost, "/hitl/"+url.PathEscape(runID)+"/reject", body, nil)
}

// ── Compliance ────────────────────────────────────────────────────────────────

// ComplianceReport generates a compliance report for the given framework
// ("hipaa", "sox", "gdpr", "pci", "nerc"). Pass an empty runID to aggregate
// the last 50 runs.
func (c *Client) ComplianceReport(ctx context.Context, framework, runID string) (map[string]interface{}, error) {
	q := url.Values{"framework": {framework}}
	if runID != "" {
		q.Set("run_id", runID)
	}
	var out map[string]interface{}
	if err := c.do(ctx, http.MethodGet, "/compliance/report?"+q.Encode(), nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// ── Zero Trust ────────────────────────────────────────────────────────────────

// ZTStatus returns the current Zero Trust posture snapshot from the server.
func (c *Client) ZTStatus(ctx context.Context) (*ZTStatus, error) {
	var out ZTStatus
	if err := c.do(ctx, http.MethodGet, "/api/zt-status", nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Metrics ───────────────────────────────────────────────────────────────────

// Metrics fetches raw Prometheus metrics text from GET /metrics.
func (c *Client) Metrics(ctx context.Context) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/metrics", nil)
	if err != nil {
		return "", fmt.Errorf("meshflow: build metrics request: %w", err)
	}
	for k, v := range c.headers() {
		req.Header.Set(k, v)
	}
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("meshflow: get metrics: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return "", &Error{StatusCode: resp.StatusCode, Method: http.MethodGet, Path: "/metrics", Body: string(raw)}
	}
	return string(raw), nil
}

// ── Multi-modal ───────────────────────────────────────────────────────────────

// multimodalRequestBody extends runRequestBody with content blocks.
type multimodalRequestBody struct {
	Task             string                   `json:"task"`
	Policy           map[string]interface{}   `json:"policy,omitempty"`
	Context          map[string]interface{}   `json:"context,omitempty"`
	MultimodalInputs []map[string]interface{} `json:"multimodal_inputs,omitempty"`
}

// RunAgentMultimodal runs a task that includes multi-modal content (images,
// documents, audio) alongside the text prompt.  The server passes the content
// blocks to the first agent in the pipeline; subsequent agents receive the
// text output.
//
//	img := meshflow.NewImageFromBytes(pngBytes, "image/png")
//	doc := meshflow.NewDocumentFromString(jsonText, "data.json")
//	result, err := client.RunAgentMultimodal(ctx,
//	    "Extract all line items and totals from this invoice.",
//	    []meshflow.MultimodalInput{img, doc},
//	)
func (c *Client) RunAgentMultimodal(
	ctx context.Context,
	task string,
	inputs []MultimodalInput,
	opts ...RunOption,
) (*RunResult, error) {
	o := applyOptions(opts)
	blocks := BuildContentBlocks(inputs, "anthropic")
	reqBody := multimodalRequestBody{
		Task:             task,
		Policy:           buildPolicy(o),
		Context:          o.Context,
		MultimodalInputs: blocks,
	}
	var out RunResult
	if err := c.do(ctx, http.MethodPost, "/run", reqBody, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// StreamMultimodal starts a streaming task run with multi-modal inputs.
// Returns a channel of StreamEvents; see [Client.Stream] for usage details.
//
//	img, _ := meshflow.NewImageFromFile("chart.png")
//	ch, err := client.StreamMultimodal(ctx,
//	    "Describe this chart in detail.",
//	    []meshflow.MultimodalInput{img},
//	)
//	text := meshflow.CollectTokens(ch)
func (c *Client) StreamMultimodal(
	ctx context.Context,
	task string,
	inputs []MultimodalInput,
	opts ...RunOption,
) (<-chan StreamEvent, error) {
	o := applyOptions(opts)
	blocks := BuildContentBlocks(inputs, "anthropic")
	reqBody := multimodalRequestBody{
		Task:             task,
		Policy:           buildPolicy(o),
		Context:          o.Context,
		MultimodalInputs: blocks,
	}

	b, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("meshflow: marshal stream request: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/stream", bytes.NewReader(b))
	if err != nil {
		return nil, fmt.Errorf("meshflow: build stream request: %w", err)
	}
	for k, v := range c.headers() {
		req.Header.Set(k, v)
	}
	req.Header.Set("Accept", "application/x-ndjson, text/event-stream")

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("meshflow: stream connect: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return nil, &Error{
			StatusCode: resp.StatusCode,
			Method:     http.MethodPost,
			Path:       "/stream",
			Body:       strings.TrimSpace(string(raw)),
		}
	}

	ch := make(chan StreamEvent, 64)
	go func() {
		defer close(ch)
		defer resp.Body.Close()
		scanner := bufio.NewScanner(resp.Body)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" {
				continue
			}
			if strings.HasPrefix(line, "data:") {
				line = strings.TrimSpace(line[len("data:"):])
			}
			if line == "" || line == "[DONE]" {
				continue
			}
			var ev StreamEvent
			if err := json.Unmarshal([]byte(line), &ev); err == nil {
				select {
				case ch <- ev:
				case <-ctx.Done():
					return
				}
			}
		}
	}()
	return ch, nil
}

// ── Batch execution ───────────────────────────────────────────────────────────

// BatchRun executes multiple tasks concurrently, up to maxConcurrency at a
// time, and returns results in the same order as tasks.
//
// Failed tasks do not abort the batch; they return a RunResult with
// Status="failed" and Error set to the error message.  Check each result's
// Error field for per-task failure details.
//
//	results := client.BatchRun(ctx, []string{
//	    "Summarise Q1 results",
//	    "Summarise Q2 results",
//	    "Summarise Q3 results",
//	    "Summarise Q4 results",
//	}, 4)
//	for i, r := range results {
//	    fmt.Printf("Q%d: %s (cost=$%.4f)\n", i+1, r.Status, r.TotalCostUSD)
//	}
func (c *Client) BatchRun(
	ctx context.Context,
	tasks []string,
	maxConcurrency int,
	opts ...RunOption,
) []*RunResult {
	if maxConcurrency <= 0 {
		maxConcurrency = 4
	}
	results := make([]*RunResult, len(tasks))
	sem := make(chan struct{}, maxConcurrency)
	var wg sync.WaitGroup

	for i, task := range tasks {
		wg.Add(1)
		go func(idx int, t string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			r, err := c.RunAgent(ctx, t, opts...)
			if err != nil {
				results[idx] = &RunResult{
					Status: "failed",
					Error:  err.Error(),
				}
			} else {
				results[idx] = r
			}
		}(i, task)
	}

	wg.Wait()
	return results
}

// ── Structured output ─────────────────────────────────────────────────────────

// structuredRequestBody adds a JSON schema hint to the run request.
type structuredRequestBody struct {
	Task       string                 `json:"task"`
	Policy     map[string]interface{} `json:"policy,omitempty"`
	Context    map[string]interface{} `json:"context,omitempty"`
	OutputMode string                 `json:"output_mode,omitempty"` // "json"
	Schema     string                 `json:"schema,omitempty"`       // JSON Schema string
}

// StructuredRunResult extends RunResult with the parsed JSON output.
type StructuredRunResult struct {
	RunResult
	// ParsedOutput holds the server's structured JSON response as a map.
	// Unmarshal into your own type:
	//   data, _ := json.Marshal(r.ParsedOutput)
	//   json.Unmarshal(data, &myStruct)
	ParsedOutput map[string]interface{} `json:"parsed_output,omitempty"`
}

// RunAgentStructured asks the server to return structured JSON output.
//
// schema is a JSON Schema string describing the expected output format.
// Pass an empty string to request JSON output without a strict schema.
//
// Usage:
//
//	result, err := client.RunAgentStructured(ctx,
//	    "Extract all line items from this invoice.",
//	    `{"type":"object","properties":{"items":{"type":"array"},"total":{"type":"number"}}}`,
//	)
//	if err != nil { ... }
//	data, _ := json.Marshal(result.ParsedOutput)
//	json.Unmarshal(data, &myInvoice)
func (c *Client) RunAgentStructured(
	ctx context.Context,
	task string,
	schema string,
	opts ...RunOption,
) (*StructuredRunResult, error) {
	o := applyOptions(opts)
	reqBody := structuredRequestBody{
		Task:       task,
		Policy:     buildPolicy(o),
		Context:    o.Context,
		OutputMode: "json",
		Schema:     schema,
	}
	var out StructuredRunResult
	if err := c.do(ctx, http.MethodPost, "/run", reqBody, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Cloud ingest ──────────────────────────────────────────────────────────────
//
// These methods POST telemetry to the meshflow.dev cloud platform using the
// x-meshflow-key header rather than Bearer auth.

func (c *Client) cloudDo(ctx context.Context, method, path string, body interface{}, out interface{}) error {
	var buf bytes.Buffer
	if body != nil {
		if err := json.NewEncoder(&buf).Encode(body); err != nil {
			return fmt.Errorf("meshflow cloud encode: %w", err)
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, &buf)
	if err != nil {
		return fmt.Errorf("meshflow cloud request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "meshflow-go-sdk/"+sdkVersion)
	if c.APIKey != "" {
		req.Header.Set("x-meshflow-key", c.APIKey)
	}
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("meshflow cloud network: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return nil // caller checks nil out
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("meshflow cloud HTTP %d: %s", resp.StatusCode, string(raw))
	}
	if out != nil {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return nil
}

// CloudSpanInput is a single trace span sent to POST /api/ingest/spans.
type CloudSpanInput struct {
	RunID        string                 `json:"run_id"`
	AgentName    string                 `json:"agent_name"`
	SpanType     string                 `json:"span_type"` // "llm_call","tool_call","step",...
	Name         string                 `json:"name"`
	StartedAt    string                 `json:"started_at"` // ISO-8601
	DurationMs   int64                  `json:"duration_ms"`
	InputText    string                 `json:"input_text,omitempty"`
	OutputText   string                 `json:"output_text,omitempty"`
	InputTokens  int                    `json:"input_tokens,omitempty"`
	OutputTokens int                    `json:"output_tokens,omitempty"`
	CostUSD      float64                `json:"cost_usd,omitempty"`
	Status       string                 `json:"status,omitempty"`
	ErrorMsg     string                 `json:"error_msg,omitempty"`
	Metadata     map[string]interface{} `json:"metadata,omitempty"`
}

// CloudEvalInput is the payload for POST /api/ingest/eval.
type CloudEvalInput struct {
	RunID     string  `json:"run_id"`
	Suite     string  `json:"suite,omitempty"`
	Scenario  string  `json:"scenario"`
	Metric    string  `json:"metric,omitempty"`
	Score     float64 `json:"score"`
	Passed    bool    `json:"passed"`
	Reasoning string  `json:"reasoning,omitempty"`
	CostUSD   float64 `json:"cost_usd,omitempty"`
	LatencyMs int64   `json:"latency_ms,omitempty"`
}

// CloudMcpCallInput is the payload for POST /api/ingest/mcp.
type CloudMcpCallInput struct {
	ServerName string  `json:"server_name"`
	ToolName   string  `json:"tool_name"`
	Transport  string  `json:"transport,omitempty"`
	Endpoint   string  `json:"endpoint,omitempty"`
	LatencyMs  int64   `json:"latency_ms,omitempty"`
	Success    bool    `json:"success"`
	CostUSD    float64 `json:"cost_usd,omitempty"`
	ToolCount  int     `json:"tool_count,omitempty"`
}

// CloudWorkerJobInput is the payload for POST /api/ingest/worker.
type CloudWorkerJobInput struct {
	JobID        string `json:"job_id"`
	WorkflowName string `json:"workflow_name"`
	Status       string `json:"status"`
	Retries      int    `json:"retries,omitempty"`
	MaxRetries   int    `json:"max_retries,omitempty"`
	DurationMs   int64  `json:"duration_ms,omitempty"`
	ErrorMsg     string `json:"error_msg,omitempty"`
	ScheduledFor string `json:"scheduled_for,omitempty"`
}

// CloudPromptRecord is returned by GET /api/ingest/prompts?slug=xxx.
type CloudPromptRecord struct {
	Slug        string  `json:"slug"`
	Name        string  `json:"name"`
	Description string  `json:"description"`
	Version     int     `json:"version"`
	Content     string  `json:"content"`
	Model       string  `json:"model"`
	Temperature float64 `json:"temperature"`
}

// CloudPromptSummary is an item in the list returned by GET /api/ingest/prompts?list=1.
type CloudPromptSummary struct {
	Slug        string `json:"slug"`
	Name        string `json:"name"`
	Description string `json:"description"`
	UpdatedAt   string `json:"updatedAt"`
}

// CloudDatasetRow is a single row in a dataset.
type CloudDatasetRow struct {
	Input          string                 `json:"input"`
	ExpectedOutput string                 `json:"expected_output,omitempty"`
	Metadata       map[string]interface{} `json:"metadata,omitempty"`
}

// CloudDatasetSummary is an item in the list returned by GET /api/ingest/datasets.
type CloudDatasetSummary struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	RowCount    int    `json:"rowCount"`
	UpdatedAt   string `json:"updatedAt"`
}

// CloudDatasetPullResponse is the body returned by GET /api/ingest/datasets?name=xxx.
type CloudDatasetPullResponse struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Description string            `json:"description"`
	RowCount    int               `json:"row_count"`
	Rows        []CloudDatasetRow `json:"rows"`
}

// CloudAgentDefinition is an agent entry in the cloud Agent Registry.
type CloudAgentDefinition struct {
	ID           string `json:"id"`
	Slug         string `json:"slug"`
	Name         string `json:"name"`
	Description  string `json:"description"`
	Role         string `json:"role"`
	Model        string `json:"model"`
	Policy       string `json:"policy"`
	SystemPrompt string `json:"systemPrompt"`
	Tags         string `json:"tags"`
	DeployTarget string `json:"deployTarget"`
	Version      string `json:"version"`
	Status       string `json:"status"`
	TotalRuns    int64  `json:"totalRuns"`
}

type cloudIngestOK struct {
	OK       bool `json:"ok"`
	Ingested int  `json:"ingested"`
}

// ReportRun posts a completed run summary to /dashboard/runs.
func (c *Client) ReportRun(ctx context.Context, payload map[string]interface{}) error {
	var out cloudIngestOK
	return c.cloudDo(ctx, http.MethodPost, "/api/ingest/run", payload, &out)
}

// ReportEval pushes one eval result to /dashboard/evals.
func (c *Client) ReportEval(ctx context.Context, eval CloudEvalInput) error {
	var out cloudIngestOK
	return c.cloudDo(ctx, http.MethodPost, "/api/ingest/eval", eval, &out)
}

// ReportMcpCall records one MCP tool call to /dashboard/mcp.
func (c *Client) ReportMcpCall(ctx context.Context, call CloudMcpCallInput) error {
	var out cloudIngestOK
	return c.cloudDo(ctx, http.MethodPost, "/api/ingest/mcp", call, &out)
}

// ReportWorkerJob upserts a worker job status event.
func (c *Client) ReportWorkerJob(ctx context.Context, job CloudWorkerJobInput) error {
	var out cloudIngestOK
	return c.cloudDo(ctx, http.MethodPost, "/api/ingest/worker", job, &out)
}

// ReportSpans sends a batch of per-step trace spans to /dashboard/traces.
func (c *Client) ReportSpans(ctx context.Context, spans []CloudSpanInput) (int, error) {
	if len(spans) == 0 {
		return 0, nil
	}
	body := map[string]interface{}{"spans": spans}
	var out cloudIngestOK
	if err := c.cloudDo(ctx, http.MethodPost, "/api/ingest/spans", body, &out); err != nil {
		return 0, err
	}
	if out.Ingested > 0 {
		return out.Ingested, nil
	}
	return len(spans), nil
}

// PromptGet fetches the active (or pinned) version of a prompt by slug.
// Returns nil when the prompt is not found.
func (c *Client) PromptGet(ctx context.Context, slug string, version int) (*CloudPromptRecord, error) {
	path := "/api/ingest/prompts?slug=" + url.QueryEscape(slug)
	if version > 0 {
		path += fmt.Sprintf("&version=%d", version)
	}
	var out CloudPromptRecord
	if err := c.cloudDo(ctx, http.MethodGet, path, nil, &out); err != nil {
		return nil, err
	}
	if out.Slug == "" {
		return nil, nil
	}
	return &out, nil
}

// PromptList lists all prompt slugs for the org.
func (c *Client) PromptList(ctx context.Context) ([]CloudPromptSummary, error) {
	var out []CloudPromptSummary
	if err := c.cloudDo(ctx, http.MethodGet, "/api/ingest/prompts?list=1", nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// PromptPush pushes a new version of a prompt (creates it if new).
func (c *Client) PromptPush(ctx context.Context, slug, content, notes string) (*CloudPromptRecord, error) {
	body := map[string]interface{}{"slug": slug, "content": content}
	if notes != "" {
		body["notes"] = notes
	}
	var out CloudPromptRecord
	if err := c.cloudDo(ctx, http.MethodPost, "/api/ingest/prompts", body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// DatasetList lists all datasets for the org.
func (c *Client) DatasetList(ctx context.Context) ([]CloudDatasetSummary, error) {
	var out []CloudDatasetSummary
	if err := c.cloudDo(ctx, http.MethodGet, "/api/ingest/datasets", nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// DatasetPull fetches rows from a named dataset. Returns nil when not found.
func (c *Client) DatasetPull(ctx context.Context, name string, limit, offset int) (*CloudDatasetPullResponse, error) {
	qs := url.Values{"name": {name}}
	if limit > 0 {
		qs.Set("limit", fmt.Sprintf("%d", limit))
	}
	if offset > 0 {
		qs.Set("offset", fmt.Sprintf("%d", offset))
	}
	var out CloudDatasetPullResponse
	if err := c.cloudDo(ctx, http.MethodGet, "/api/ingest/datasets?"+qs.Encode(), nil, &out); err != nil {
		return nil, err
	}
	if out.ID == "" {
		return nil, nil
	}
	return &out, nil
}

// DatasetPush appends rows to a named dataset (creates it if new). Returns the dataset ID.
func (c *Client) DatasetPush(ctx context.Context, name string, rows []CloudDatasetRow, description string) (string, error) {
	body := map[string]interface{}{"name": name, "rows": rows}
	if description != "" {
		body["description"] = description
	}
	var out struct {
		ID string `json:"id"`
	}
	if err := c.cloudDo(ctx, http.MethodPost, "/api/ingest/datasets", body, &out); err != nil {
		return "", err
	}
	return out.ID, nil
}

// DatasetDelete deletes a dataset and all its rows.
func (c *Client) DatasetDelete(ctx context.Context, name string) error {
	return c.cloudDo(ctx, http.MethodDelete, "/api/ingest/datasets?name="+url.QueryEscape(name), nil, nil)
}

// ListAgents lists all registered agent definitions.
func (c *Client) ListAgents(ctx context.Context) ([]CloudAgentDefinition, error) {
	var out []CloudAgentDefinition
	if err := c.cloudDo(ctx, http.MethodGet, "/api/ingest/agents", nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// GetAgent fetches one agent definition by slug. Returns nil when not found.
func (c *Client) GetAgent(ctx context.Context, slug string) (*CloudAgentDefinition, error) {
	var out CloudAgentDefinition
	if err := c.cloudDo(ctx, http.MethodGet, "/api/ingest/agents?slug="+url.QueryEscape(slug), nil, &out); err != nil {
		return nil, err
	}
	if out.ID == "" {
		return nil, nil
	}
	return &out, nil
}

// RegisterAgent upserts an agent definition in the cloud Agent Registry.
func (c *Client) RegisterAgent(ctx context.Context, name, slug, role, model, policy string) (*CloudAgentDefinition, error) {
	body := map[string]interface{}{"name": name, "slug": slug}
	if role   != "" { body["role"]   = role }
	if model  != "" { body["model"]  = model }
	if policy != "" { body["policy"] = policy }
	var out CloudAgentDefinition
	if err := c.cloudDo(ctx, http.MethodPost, "/api/ingest/agents", body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// RecordAgentRun increments the run counter for a registered agent.
func (c *Client) RecordAgentRun(ctx context.Context, slug string, runCount int) error {
	body := map[string]interface{}{"name": slug, "slug": slug, "run_count": runCount}
	var out cloudIngestOK
	return c.cloudDo(ctx, http.MethodPost, "/api/ingest/agents", body, &out)
}
