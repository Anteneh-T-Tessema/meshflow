// Package meshflow — routing client methods (v1.10.0).
//
// Provides access to the MeshFlow adaptive routing API:
//   - EstimateCost — pre-flight cost estimate for a task across named agent models
//   - RoutingReport — tier distribution, cost savings, and learned threshold state
//   - ListRoutingOutcomes — recent routing decisions from the outcome store
//
// These endpoints require MeshFlow server v1.10.0 or later.
package meshflow

import (
	"context"
	"net/http"
)

// ── Cost estimation ───────────────────────────────────────────────────────────

// costEstimateRequest is the JSON payload for POST /api/cost-estimate.
type costEstimateRequest struct {
	Task   string   `json:"task"`
	Agents []string `json:"agents"`
}

// EstimateCost calls POST /api/cost-estimate and returns a per-agent cost
// breakdown without making any LLM calls. All costs are in USD.
//
// agents is a list of model identifiers in pipeline order, e.g.
// ["llama3.2", "mistral:7b", "meta.llama3-70b-instruct-v1:0"]. Local models
// (Ollama, llama, mistral families) always return $0.00.
//
//	est, err := client.EstimateCost(ctx, "analyse the competitive landscape",
//	    []string{"llama3.2", "mistral:7b", "gpt-4o"})
//	if err != nil { ... }
//	fmt.Printf("Total: $%.4f  Cloud agents: %v\n", est.TotalUSD, est.CloudAgents)
func (c *Client) EstimateCost(ctx context.Context, task string, agents []string) (*CostEstimate, error) {
	req := costEstimateRequest{Task: task, Agents: agents}
	var out CostEstimate
	if err := c.do(ctx, http.MethodPost, "/api/cost-estimate", req, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Routing report ────────────────────────────────────────────────────────────

// RoutingReport calls GET /api/routing-report and returns a summary of routing
// decisions, tier distribution, and estimated cost savings versus always using
// the most expensive tier.
//
//	report, err := client.RoutingReport(ctx)
//	if err != nil { ... }
//	fmt.Printf("Saved $%.4f (%.0f%%) vs always-large\n",
//	    report.CostSavedUSD, report.SavingsPct*100)
func (c *Client) RoutingReport(ctx context.Context) (*RouterReport, error) {
	var out RouterReport
	if err := c.do(ctx, http.MethodGet, "/api/routing-report", nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// RouterStatsForRun calls GET /api/routing-report/{runID} and returns the
// routing stats scoped to a specific run. Useful for per-run cost attribution.
func (c *Client) RouterStatsForRun(ctx context.Context, runID string) (*RouterStats, error) {
	var out RouterStats
	if err := c.do(ctx, http.MethodGet, "/api/routing-report/"+runID, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Routing outcomes ──────────────────────────────────────────────────────────

// listRoutingOutcomesResponse wraps the paginated outcomes list.
type listRoutingOutcomesResponse struct {
	Outcomes []RoutingOutcome `json:"outcomes"`
	Total    int              `json:"total"`
}

// ListRoutingOutcomes calls GET /api/routing-outcomes and returns recent routing
// decisions from the server's outcome store. n controls how many records to
// fetch (0 = server default, typically 200).
//
// Useful for building custom dashboards or feeding outcome data into external
// analytics tools.
func (c *Client) ListRoutingOutcomes(ctx context.Context, n int) ([]RoutingOutcome, error) {
	path := "/api/routing-outcomes"
	if n > 0 {
		path += "?limit=" + itoa(n)
	}
	var out listRoutingOutcomesResponse
	if err := c.do(ctx, http.MethodGet, path, nil, &out); err != nil {
		return nil, err
	}
	return out.Outcomes, nil
}

// ── Router state ──────────────────────────────────────────────────────────────

// RouterStateSnapshot is the JSON representation of a saved router state,
// as produced by AdaptiveModelTierRouter.save() in the Python SDK.
type RouterStateSnapshot struct {
	SmartThreshold  float64           `json:"smart_threshold"`
	LargeThreshold  float64           `json:"large_threshold"`
	RouteCount      int               `json:"route_count"`
	LastAdaptedAt   *float64          `json:"last_adapted_at,omitempty"`
	AdaptEvery      int               `json:"adapt_every"`
	ExplorationRate float64           `json:"exploration_rate"`
	AdaptMode       string            `json:"adapt_mode"`
	Tiers           []ModelTierConfig `json:"tiers"`
}

// GetRouterState calls GET /api/router-state and returns the server's current
// AdaptiveModelTierRouter state snapshot — thresholds, route count, and tier
// definitions. Use this to inspect what the server's router has learned without
// reading the SQLite database directly.
func (c *Client) GetRouterState(ctx context.Context) (*RouterStateSnapshot, error) {
	var out RouterStateSnapshot
	if err := c.do(ctx, http.MethodGet, "/api/router-state", nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// itoa converts an int to a decimal string without importing strconv in this file.
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	buf := [20]byte{}
	pos := len(buf)
	for n > 0 {
		pos--
		buf[pos] = byte('0' + n%10)
		n /= 10
	}
	return string(buf[pos:])
}
