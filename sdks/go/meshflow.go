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
	"time"
)

const (
	defaultTimeout = 120 * time.Second
	sdkVersion     = "1.10.0"
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
