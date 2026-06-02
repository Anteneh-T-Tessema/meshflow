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
	RunID          string            `json:"run_id"`
	Status         RunStatus         `json:"status"`
	Output         interface{}       `json:"output"`
	TotalCostUSD   float64           `json:"total_cost_usd"`
	TotalTokens    int               `json:"total_tokens"`
	TotalCarbonG   float64           `json:"total_carbon_g"`
	DurationS      float64           `json:"duration_s"`
	LedgerEntries  int               `json:"ledger_entries"`
	TraceID        string            `json:"trace_id"`
	Checkpoints    []string          `json:"checkpoints"`
	Error          string            `json:"error"`
	CollusionAlerts int              `json:"collusion_alerts"`
	AgentStates    map[string]string `json:"agent_states,omitempty"`
}

// StreamEvent is a single event emitted by the SSE /stream endpoint.
type StreamEvent struct {
	// EventType (Kind) identifies what happened: "token_delta", "step_start",
	// "step_end", "run_complete", "error", etc.
	EventType string  `json:"kind"`
	AgentID   string  `json:"agent_id,omitempty"`
	Role      string  `json:"role,omitempty"`
	Data      string  `json:"output,omitempty"`
	Text      string  `json:"text,omitempty"` // populated for kind == "token_delta"
	RunID     string  `json:"run_id,omitempty"`
	Step      int     `json:"step,omitempty"`
	StepID    string  `json:"step_id,omitempty"`
	NodeID    string  `json:"node_id,omitempty"`
	Uncertainty float64 `json:"uncertainty,omitempty"`
	CostUSD   float64 `json:"cost_usd,omitempty"`
	Tokens    int     `json:"tokens,omitempty"`
	BlockedBy string  `json:"blocked_by,omitempty"`
	ErrMsg    string  `json:"error,omitempty"`
	Timestamp float64 `json:"timestamp,omitempty"`
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
