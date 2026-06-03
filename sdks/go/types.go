// Package meshflow provides a Go client for the MeshFlow multi-agent
// orchestration platform REST and SSE API.
package meshflow

import "net/http"

// RunStatus represents the lifecycle state of a MeshFlow run.
type RunStatus string

const (
	RunStatusPending   RunStatus = "pending"
	RunStatusRunning   RunStatus = "running"
	RunStatusPaused    RunStatus = "paused"
	RunStatusCompleted RunStatus = "completed"
	RunStatusFailed    RunStatus = "failed"
	RunStatusAborted   RunStatus = "aborted"
)

// RunResult is returned by RunAgent once the task has completed.
type RunResult struct {
	RunID               string             `json:"run_id"`
	Status              RunStatus          `json:"status"`
	Output              interface{}        `json:"output"`
	TotalCostUSD        float64            `json:"total_cost_usd"`
	TotalTokens         int                `json:"total_tokens"`
	TotalCarbonG        float64            `json:"total_carbon_g"`
	DurationS           float64            `json:"duration_s"`
	LedgerEntries       int                `json:"ledger_entries"`
	TraceID             string             `json:"trace_id"`
	Checkpoints         []string           `json:"checkpoints"`
	Error               string             `json:"error"`
	CollusionAlerts     int                `json:"collusion_alerts"`
	AgentStates         map[string]string  `json:"agent_states,omitempty"`

	// v1.10.0 — mixed-model cost attribution
	AgentCosts          map[string]float64 `json:"agent_costs,omitempty"`
	CloudAgents         []string           `json:"cloud_agents,omitempty"`

	// v1.10.0 — prompt cache metrics
	CacheReadTokens     int                `json:"cache_read_tokens,omitempty"`
	CacheCreationTokens int                `json:"cache_creation_tokens,omitempty"`

	// v1.10.0 — cascade escalation count (non-zero when CascadeRouter retried)
	CascadeEscalations  int                `json:"cascade_escalations,omitempty"`
}

// StreamEvent is a single event emitted by the SSE /stream endpoint.
type StreamEvent struct {
	// EventType (Kind) identifies what happened: "token_delta", "step_start",
	// "step_end", "run_complete", "error", etc.
	EventType          string  `json:"kind"`
	AgentID            string  `json:"agent_id,omitempty"`
	Role               string  `json:"role,omitempty"`
	Data               string  `json:"output,omitempty"`
	Text               string  `json:"text,omitempty"` // populated for kind == "token_delta"
	RunID              string  `json:"run_id,omitempty"`
	Step               int     `json:"step,omitempty"`
	StepID             string  `json:"step_id,omitempty"`
	NodeID             string  `json:"node_id,omitempty"`
	Uncertainty        float64 `json:"uncertainty,omitempty"`
	CostUSD            float64 `json:"cost_usd,omitempty"`
	Tokens             int     `json:"tokens,omitempty"`
	BlockedBy          string  `json:"blocked_by,omitempty"`
	ErrMsg             string  `json:"error,omitempty"`
	Timestamp          float64 `json:"timestamp,omitempty"`

	// v1.10.0 — model routing context
	ModelUsed          string                 `json:"model_used,omitempty"`
	TierUsed           string                 `json:"tier_used,omitempty"`
	IsLocalModel       bool                   `json:"is_local_model,omitempty"`
	CascadeEscalations int                    `json:"cascade_escalations,omitempty"`
	StatedConfidence   float64                `json:"stated_confidence,omitempty"`
	// Metadata carries arbitrary key-value data from routing events and node_end events.
	// For routing events: model, tier, is_local, cascade_escalation, reason.
	// For node_end events: tokens, cost_usd.
	Metadata           map[string]interface{} `json:"metadata,omitempty"`
}

// TraceStep is a single ledger record within a run trace.
type TraceStep struct {
	StepID        string  `json:"step_id"`
	RunID         string  `json:"run_id"`
	NodeID        string  `json:"node_id"`
	NodeKind      string  `json:"node_kind"`
	InputTask     string  `json:"input_task"`
	OutputContent string  `json:"output_content"`
	Verdict       string  `json:"verdict"`
	Blocked       bool    `json:"blocked"`
	BlockReason   string  `json:"block_reason"`
	Uncertainty   float64 `json:"uncertainty"`
	CostUSD       float64 `json:"cost_usd"`
	TokensUsed    int     `json:"tokens_used"`
	CarbonGCO2    float64 `json:"carbon_gco2"`
	DurationMS    float64 `json:"duration_ms"`
	Timestamp     string  `json:"timestamp"`
	PrevHash      string  `json:"prev_hash"`
	EntryHash     string  `json:"entry_hash"`
}

// TraceSummary aggregates statistics across all steps in a run.
type TraceSummary struct {
	Steps          int      `json:"steps"`
	Nodes          []string `json:"nodes"`
	TotalCostUSD   float64  `json:"total_cost_usd"`
	TotalTokens    int      `json:"total_tokens"`
	TotalCarbonGCO2 float64 `json:"total_carbon_gco2"`
	BlockedSteps   int      `json:"blocked_steps"`
	Verdicts       []string `json:"verdicts"`
	Timestamps     struct {
		Start string `json:"start"`
		End   string `json:"end"`
	} `json:"timestamps"`
}

// Trace is the full execution record for a single run.
type Trace struct {
	RunID   string       `json:"run_id"`
	Summary TraceSummary `json:"summary"`
	Steps   []TraceStep  `json:"steps"`
}

// ZTStatus is the Zero Trust posture snapshot returned by GET /api/zt-status.
type ZTStatus struct {
	Tier            string `json:"tier"`
	Regulation      string `json:"regulation"`
	ScorePct        int    `json:"score_pct"`
	ControlsEnabled int    `json:"controls_enabled"`
	ControlsGap     int    `json:"controls_gap"`
	EnvTier         string `json:"env_tier"`
	EnvRegulation   string `json:"env_regulation,omitempty"`
}

// HealthResponse is returned by GET /health.
type HealthResponse struct {
	OK       bool    `json:"ok"`
	Version  string  `json:"version"`
	UptimeS  float64 `json:"uptime_s"`
	DB       string  `json:"db"`
}

// ProbeResponse is returned by GET /health/live and GET /health/ready.
type ProbeResponse struct {
	Live    *bool   `json:"live,omitempty"`
	Ready   *bool   `json:"ready,omitempty"`
	UptimeS float64 `json:"uptime_s,omitempty"`
	Version string  `json:"version,omitempty"`
	Reason  string  `json:"reason,omitempty"`
}

// PausedRun identifies a run that is currently awaiting human approval.
type PausedRun struct {
	RunID    string `json:"run_id"`
	PausedAt string `json:"paused_at"`
}

// RunOptions holds optional parameters for RunAgent and Stream.
type RunOptions struct {
	// PolicyMode controls governance strictness.
	// Values: "dev", "standard", "regulated", "legal-critical", "hipaa"
	PolicyMode string `json:"mode,omitempty"`

	// CostCapUSD is a hard per-run spend ceiling in US dollars.
	CostCapUSD float64 `json:"budget_usd,omitempty"`

	// BudgetTokens caps total token consumption for the run.
	BudgetTokens int `json:"budget_tokens,omitempty"`

	// TimeoutS is the maximum wall-clock seconds allowed for the run.
	TimeoutS float64 `json:"timeout_s,omitempty"`

	// MaxSteps limits the number of agent execution steps.
	MaxSteps int `json:"max_steps,omitempty"`

	// ComplianceProfile is forwarded as the policy compliance hint
	// (e.g. "hipaa", "sox", "gdpr").
	ComplianceProfile string `json:"-"`

	// Tenant scopes the run to a logical tenant for multi-tenant deployments.
	Tenant string `json:"-"`

	// DeterministicGate enables the DASC determinism gate.
	DeterministicGate bool `json:"deterministic_gate,omitempty"`

	// EnableGuardian activates the guardian agent for this run.
	EnableGuardian bool `json:"enable_guardian,omitempty"`

	// EnableCollusionAudit turns on inter-agent collusion monitoring.
	EnableCollusionAudit bool `json:"enable_collusion_audit,omitempty"`

	// EnableUncertainty enables uncertainty-awareness scoring.
	EnableUncertainty bool `json:"enable_uncertainty,omitempty"`

	// Context is an arbitrary key/value map forwarded to the agents.
	Context map[string]interface{} `json:"-"`

	// ── v1.10.0 — routing options ─────────────────────────────────────────────

	// ModelTiers configures a multi-tier model routing policy on the server.
	// The server selects the tier based on task complexity and the adaptive
	// router's learned thresholds. Omit to use the server's default.
	ModelTiers []ModelTierConfig `json:"model_tiers,omitempty"`

	// SmartThreshold is the composite score (0–1) above which the server
	// routes to the "smart" tier. Default: 0.33.
	SmartThreshold float64 `json:"smart_threshold,omitempty"`

	// LargeThreshold is the composite score (0–1) above which the server
	// routes to the "large" tier. Default: 0.67.
	LargeThreshold float64 `json:"large_threshold,omitempty"`

	// CascadeThreshold is the minimum CONFIDENCE score (0–1) required to
	// accept the first-tier response without escalating. Default: disabled.
	// When set, the server retries with the next model tier if the agent's
	// CONFIDENCE marker falls below this value.
	CascadeThreshold float64 `json:"cascade_threshold,omitempty"`

	// MaxEscalations caps the number of tier upgrades per cascade. Default: 2.
	MaxEscalations int `json:"max_escalations,omitempty"`

	// ThinkingBudget enables Claude extended thinking with a token budget.
	// Set to 0 to disable (default). Requires a Claude model.
	ThinkingBudget int `json:"thinking_budget,omitempty"`
}

// RunOption is a functional option that mutates a RunOptions value.
type RunOption func(*RunOptions)

// WithPolicyMode sets the governance policy mode.
func WithPolicyMode(mode string) RunOption {
	return func(o *RunOptions) { o.PolicyMode = mode }
}

// WithCostCap sets a hard USD spend ceiling for the run.
func WithCostCap(usd float64) RunOption {
	return func(o *RunOptions) { o.CostCapUSD = usd }
}

// WithBudgetTokens sets a maximum token budget for the run.
func WithBudgetTokens(n int) RunOption {
	return func(o *RunOptions) { o.BudgetTokens = n }
}

// WithTimeoutS sets the run timeout in seconds.
func WithTimeoutS(s float64) RunOption {
	return func(o *RunOptions) { o.TimeoutS = s }
}

// WithMaxSteps caps the number of agent execution steps.
func WithMaxSteps(n int) RunOption {
	return func(o *RunOptions) { o.MaxSteps = n }
}

// WithComplianceProfile sets the compliance framework hint (e.g. "hipaa").
func WithComplianceProfile(profile string) RunOption {
	return func(o *RunOptions) { o.ComplianceProfile = profile }
}

// WithTenant scopes the run to a logical tenant.
func WithTenant(tenant string) RunOption {
	return func(o *RunOptions) { o.Tenant = tenant }
}

// WithContext attaches an arbitrary key/value context to the run request.
func WithContext(ctx map[string]interface{}) RunOption {
	return func(o *RunOptions) { o.Context = ctx }
}

// WithGuardian enables the guardian agent.
func WithGuardian() RunOption {
	return func(o *RunOptions) { o.EnableGuardian = true }
}

// WithCollusionAudit enables inter-agent collusion monitoring.
func WithCollusionAudit() RunOption {
	return func(o *RunOptions) { o.EnableCollusionAudit = true }
}

// ── v1.10.0 functional options ────────────────────────────────────────────────

// WithModelTiers sets a multi-tier routing policy. The server routes each task
// to the cheapest tier whose composite score is within the configured thresholds.
//
//	client.RunAgent(ctx, task,
//	    meshflow.WithModelTiers([]meshflow.ModelTierConfig{
//	        {Name: "fast",  Model: "llama3.2", MaxTokens: 512},
//	        {Name: "smart", Model: "mistral",  MaxTokens: 2048},
//	        {Name: "large", Model: "gpt-4o",   MaxTokens: 4096},
//	    }),
//	    meshflow.WithCascadeThreshold(0.65),
//	)
func WithModelTiers(tiers []ModelTierConfig) RunOption {
	return func(o *RunOptions) { o.ModelTiers = tiers }
}

// WithSmartThreshold sets the composite score threshold above which the router
// selects the "smart" (second) tier. Default on the server: 0.33.
func WithSmartThreshold(threshold float64) RunOption {
	return func(o *RunOptions) { o.SmartThreshold = threshold }
}

// WithLargeThreshold sets the composite score threshold above which the router
// selects the "large" (third) tier. Default on the server: 0.67.
func WithLargeThreshold(threshold float64) RunOption {
	return func(o *RunOptions) { o.LargeThreshold = threshold }
}

// WithCascadeThreshold enables cascade escalation. If the agent's
// CONFIDENCE marker falls below threshold, the server retries with the next
// tier automatically. Set to 0 to disable (the default).
//
// Typical value: 0.65 — accept the fast-tier response unless confidence is poor.
func WithCascadeThreshold(threshold float64) RunOption {
	return func(o *RunOptions) { o.CascadeThreshold = threshold }
}

// WithMaxEscalations caps the number of cascade tier upgrades per task.
// Default: 2 (fast → smart → large). Lower to limit cloud spend.
func WithMaxEscalations(n int) RunOption {
	return func(o *RunOptions) { o.MaxEscalations = n }
}

// WithThinking enables Claude extended thinking with the given token budget.
// Only takes effect when the model is a Claude model that supports thinking.
//
//	client.RunAgent(ctx, "Prove why prompt caching saves 70-85%",
//	    meshflow.WithThinking(8000),
//	)
func WithThinking(budgetTokens int) RunOption {
	return func(o *RunOptions) { o.ThinkingBudget = budgetTokens }
}

// applyOptions applies all provided RunOption functions and returns the result.
func applyOptions(opts []RunOption) RunOptions {
	var o RunOptions
	for _, fn := range opts {
		fn(&o)
	}
	return o
}

// runRequestBody is the JSON payload sent to POST /run and POST /stream.
type runRequestBody struct {
	Task    string                 `json:"task"`
	Policy  map[string]interface{} `json:"policy,omitempty"`
	Context map[string]interface{} `json:"context,omitempty"`
}

// hitlDecisionBody is the JSON payload sent to the HITL approve/reject endpoints.
type hitlDecisionBody struct {
	ReviewerID string `json:"reviewer_id,omitempty"`
	Notes      string `json:"notes,omitempty"`
}

// ── v1.10.0 routing types ─────────────────────────────────────────────────────

// ModelTierConfig describes one tier in a multi-model routing configuration.
// Pass a slice of these via WithModelTiers to configure the server-side router.
type ModelTierConfig struct {
	// Name is a human-readable label, e.g. "fast", "smart", "large".
	Name      string  `json:"name"`
	// Model is the model identifier — Ollama-style for local, provider ID for cloud.
	Model     string  `json:"model"`
	// MaxTokens is a soft cap on output tokens for this tier (advisory).
	MaxTokens int     `json:"max_tokens,omitempty"`
	// IsLocal explicitly marks the model as local (zero cost) when the name is
	// not in the auto-detected local-family list (e.g. custom Ollama fine-tunes).
	// Omit or set to nil for auto-detection.
	IsLocal   *bool   `json:"is_local,omitempty"`
}

// TierStats holds aggregated quality and cost metrics for a single routing tier.
type TierStats struct {
	Tier          string  `json:"tier"`
	N             int     `json:"n"`
	SuccessRate   float64 `json:"success_rate"`
	AvgQuality    float64 `json:"avg_quality"`
	AvgLatencyMS  float64 `json:"avg_latency_ms"`
	AvgCostUSD    float64 `json:"avg_cost_usd"`
}

// RouterStats is a full snapshot of an AdaptiveModelTierRouter's runtime state.
type RouterStats struct {
	Tiers                map[string]TierStats `json:"tiers"`
	TotalRuns            int                  `json:"total_runs"`
	ExplorationRateActual float64             `json:"exploration_rate_actual"`
	LastAdaptedAt        *float64             `json:"last_adapted_at,omitempty"`
}

// RouterReport is the structured response from GET /api/routing-report.
// It mirrors the Python RouterReport dataclass (v1.10.0).
type RouterReport struct {
	SmartThreshold    float64              `json:"smart_threshold"`
	LargeThreshold    float64              `json:"large_threshold"`
	RouteCount        int                  `json:"route_count"`
	LastAdaptedAt     *float64             `json:"last_adapted_at,omitempty"`
	TierDistribution  map[string]int       `json:"tier_distribution"`
	TierStats         map[string]TierStats `json:"tier_stats"`
	OutcomesAnalyzed  int                  `json:"outcomes_analyzed"`
	ActualCostUSD     float64              `json:"actual_cost_usd"`
	AlwaysLargeCostUSD float64            `json:"always_large_cost_usd"`
	CostSavedUSD      float64             `json:"cost_saved_usd"`
	SavingsPct        float64             `json:"savings_pct"`
}

// CostEstimateLine is the per-agent breakdown within a CostEstimate.
type CostEstimateLine struct {
	Agent    string  `json:"agent"`
	Model    string  `json:"model"`
	CostUSD  float64 `json:"cost_usd"`
	IsLocal  bool    `json:"is_local"`
	Tag      string  `json:"tag"` // "local" or "cloud"
}

// CostEstimate is the response from POST /api/cost-estimate.
// It mirrors the Python CostEstimate dataclass (v1.10.0).
type CostEstimate struct {
	Lines       []CostEstimateLine `json:"estimate"`
	TotalUSD    float64            `json:"total_usd"`
	TaskPreview string             `json:"task_preview,omitempty"`
	CloudAgents []string           `json:"cloud_agents,omitempty"`
	LocalAgents []string           `json:"local_agents,omitempty"`
}

// RoutingOutcome is a single routing event record from the outcome store.
type RoutingOutcome struct {
	OutcomeID      string   `json:"outcome_id"`
	RunID          string   `json:"run_id"`
	TaskHash       string   `json:"task_hash"`
	TaskLength     int      `json:"task_length"`
	CompositeScore float64  `json:"composite_score"`
	Model          string   `json:"model"`
	Tier           string   `json:"tier"`
	WasExploration bool     `json:"was_exploration"`
	Success        bool     `json:"success"`
	QualityScore   *float64 `json:"quality_score,omitempty"`
	LatencyMS      float64  `json:"latency_ms"`
	ActualCostUSD  float64  `json:"actual_cost_usd"`
	Timestamp      float64  `json:"timestamp"`
}

// Error is a structured API error returned when the server responds with a
// non-2xx status code.
type Error struct {
	// StatusCode is the HTTP response status code.
	StatusCode int
	// Method is the HTTP verb used for the request.
	Method string
	// Path is the request path.
	Path string
	// Body is the raw response body text.
	Body string
}

func (e *Error) Error() string {
	return "meshflow: " + e.Method + " " + e.Path +
		" returned HTTP " + http.StatusText(e.StatusCode) +
		" (" + e.Body + ")"
}
