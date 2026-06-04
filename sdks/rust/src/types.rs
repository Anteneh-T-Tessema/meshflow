//! Public types for the MeshFlow Rust SDK.
//!
//! All types implement [`serde::Serialize`] and [`serde::Deserialize`] so they
//! can be sent to and received from the MeshFlow REST API without ceremony.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use thiserror::Error;

// ── Error ─────────────────────────────────────────────────────────────────────

/// Errors that can be returned by [`MeshFlowClient`](crate::MeshFlowClient).
#[derive(Debug, Error)]
pub enum MeshFlowError {
    /// The server returned a non-2xx HTTP status.
    #[error("HTTP {status}: {body}")]
    Http { status: u16, body: String },

    /// The server response could not be decoded as the expected JSON type.
    #[error("JSON decode error: {0}")]
    Json(#[from] serde_json::Error),

    /// A network-level failure (connect timeout, TLS error, etc.).
    #[error("Network error: {0}")]
    Network(String),

    /// Authentication failed (401 / 403).
    #[error("Auth error (HTTP {status}): {body}")]
    Auth { status: u16, body: String },
}

impl From<reqwest::Error> for MeshFlowError {
    fn from(e: reqwest::Error) -> Self {
        if e.is_decode() {
            // reqwest decode errors wrap a serde error but expose only the
            // reqwest::Error type, so we surface them as Network.
            MeshFlowError::Network(e.to_string())
        } else {
            MeshFlowError::Network(e.to_string())
        }
    }
}

// ── Run status ────────────────────────────────────────────────────────────────

/// Lifecycle state of a MeshFlow run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Pending,
    Running,
    Paused,
    Completed,
    Failed,
    Aborted,
    #[serde(other)]
    Unknown,
}

// ── RunResult ─────────────────────────────────────────────────────────────────

/// Returned by [`run_agent`](crate::MeshFlowClient::run_agent) once the task
/// has completed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunResult {
    pub run_id: String,
    pub status: RunStatus,
    /// The agent's final output. Kept as a raw JSON value to stay flexible.
    pub output: Option<serde_json::Value>,
    pub total_cost_usd: f64,
    pub total_tokens: i64,
    pub total_carbon_g: f64,
    pub duration_s: f64,
    pub ledger_entries: i64,
    pub trace_id: Option<String>,
    #[serde(default)]
    pub checkpoints: Vec<String>,
    pub error: Option<String>,
    pub collusion_alerts: Option<i64>,
    #[serde(default)]
    pub agent_states: HashMap<String, String>,
}

// ── RunOptions & builder ──────────────────────────────────────────────────────

/// Optional parameters for [`run_agent_with_options`](crate::MeshFlowClient::run_agent_with_options)
/// and [`stream_agent`](crate::MeshFlowClient::stream_agent).
///
/// # Example
/// ```rust,no_run
/// use meshflow_sdk::RunOptions;
///
/// let opts = RunOptions::new()
///     .policy_mode("hipaa")
///     .cost_cap_usd(1.50)
///     .budget_tokens(50_000)
///     .max_steps(20)
///     .deterministic_gate(true);
/// ```
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RunOptions {
    /// Governance policy mode: "dev", "standard", "regulated",
    /// "legal-critical", "hipaa".
    #[serde(skip_serializing_if = "Option::is_none")]
    pub policy_mode: Option<String>,

    /// Hard per-run spend ceiling in USD.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cost_cap_usd: Option<f64>,

    /// Maximum total token consumption for the run.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub budget_tokens: Option<u64>,

    /// Maximum wall-clock seconds allowed for the run.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeout_s: Option<f64>,

    /// Maximum number of agent execution steps.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_steps: Option<u32>,

    /// Enable the DASC determinism gate.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub deterministic_gate: Option<bool>,

    /// Activate the guardian agent for this run.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enable_guardian: Option<bool>,

    /// Enable inter-agent collusion monitoring.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enable_collusion_audit: Option<bool>,

    /// Enable uncertainty-awareness scoring.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enable_uncertainty: Option<bool>,

    /// Arbitrary key/value context forwarded to the agents.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<HashMap<String, serde_json::Value>>,

    /// Compliance framework hint ("hipaa", "sox", "gdpr", "pci", "nerc").
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compliance_profile: Option<String>,

    /// Logical tenant for multi-tenant deployments.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tenant: Option<String>,
}

impl RunOptions {
    /// Create a blank [`RunOptions`].
    pub fn new() -> Self {
        Self::default()
    }

    /// Set the governance policy mode.
    pub fn policy_mode(mut self, mode: impl Into<String>) -> Self {
        self.policy_mode = Some(mode.into());
        self
    }

    /// Set a hard USD spend ceiling.
    pub fn cost_cap_usd(mut self, usd: f64) -> Self {
        self.cost_cap_usd = Some(usd);
        self
    }

    /// Set a maximum token budget.
    pub fn budget_tokens(mut self, n: u64) -> Self {
        self.budget_tokens = Some(n);
        self
    }

    /// Set the run timeout in seconds.
    pub fn timeout_s(mut self, s: f64) -> Self {
        self.timeout_s = Some(s);
        self
    }

    /// Cap the number of agent execution steps.
    pub fn max_steps(mut self, n: u32) -> Self {
        self.max_steps = Some(n);
        self
    }

    /// Enable or disable the DASC determinism gate.
    pub fn deterministic_gate(mut self, v: bool) -> Self {
        self.deterministic_gate = Some(v);
        self
    }

    /// Activate the guardian agent.
    pub fn enable_guardian(mut self, v: bool) -> Self {
        self.enable_guardian = Some(v);
        self
    }

    /// Enable inter-agent collusion monitoring.
    pub fn enable_collusion_audit(mut self, v: bool) -> Self {
        self.enable_collusion_audit = Some(v);
        self
    }

    /// Enable uncertainty-awareness scoring.
    pub fn enable_uncertainty(mut self, v: bool) -> Self {
        self.enable_uncertainty = Some(v);
        self
    }

    /// Set the compliance framework hint.
    pub fn compliance_profile(mut self, profile: impl Into<String>) -> Self {
        self.compliance_profile = Some(profile.into());
        self
    }

    /// Scope this run to a logical tenant.
    pub fn tenant(mut self, t: impl Into<String>) -> Self {
        self.tenant = Some(t.into());
        self
    }

    /// Attach an arbitrary context map to the run.
    pub fn context(mut self, ctx: HashMap<String, serde_json::Value>) -> Self {
        self.context = Some(ctx);
        self
    }

    /// Convert to the `policy` sub-object that the REST API expects.
    pub(crate) fn to_policy_map(&self) -> Option<serde_json::Value> {
        let mut m = serde_json::Map::new();
        if let Some(ref v) = self.policy_mode {
            m.insert("mode".into(), serde_json::Value::String(v.clone()));
        }
        if let Some(v) = self.cost_cap_usd {
            m.insert(
                "budget_usd".into(),
                serde_json::Value::Number(
                    serde_json::Number::from_f64(v).unwrap_or_else(|| 0.into()),
                ),
            );
        }
        if let Some(v) = self.budget_tokens {
            m.insert("budget_tokens".into(), v.into());
        }
        if let Some(v) = self.timeout_s {
            m.insert(
                "timeout_s".into(),
                serde_json::Value::Number(
                    serde_json::Number::from_f64(v).unwrap_or_else(|| 0.into()),
                ),
            );
        }
        if let Some(v) = self.max_steps {
            m.insert("max_steps".into(), v.into());
        }
        if self.deterministic_gate == Some(true) {
            m.insert("deterministic_gate".into(), true.into());
        }
        if self.enable_guardian == Some(true) {
            m.insert("enable_guardian".into(), true.into());
        }
        if self.enable_collusion_audit == Some(true) {
            m.insert("enable_collusion_audit".into(), true.into());
        }
        if self.enable_uncertainty == Some(true) {
            m.insert("enable_uncertainty".into(), true.into());
        }
        if m.is_empty() {
            None
        } else {
            Some(serde_json::Value::Object(m))
        }
    }
}

// ── StreamEvent ───────────────────────────────────────────────────────────────

/// A single event emitted by the SSE `/stream` endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamEvent {
    /// Event kind: "token_delta", "step_start", "step_end", "run_complete",
    /// "error", etc.
    #[serde(rename = "kind", default)]
    pub event_type: String,

    #[serde(default)]
    pub agent_id: String,

    #[serde(default)]
    pub role: String,

    /// Raw output payload (populated for most non-delta events).
    #[serde(rename = "output", default)]
    pub data: String,

    /// Token text — populated when `event_type == "token_delta"`.
    #[serde(default)]
    pub text: String,

    #[serde(default)]
    pub run_id: String,

    #[serde(default)]
    pub step: u32,

    #[serde(default)]
    pub step_id: String,

    #[serde(default)]
    pub node_id: String,

    #[serde(default)]
    pub uncertainty: f64,

    #[serde(default)]
    pub cost_usd: f64,

    #[serde(default)]
    pub tokens: u64,

    #[serde(default)]
    pub blocked_by: String,

    #[serde(rename = "error", default)]
    pub err_msg: String,

    #[serde(default)]
    pub timestamp: f64,
}

// ── Trace ─────────────────────────────────────────────────────────────────────

/// A single ledger record within a run trace.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceStep {
    pub step_id: String,
    pub run_id: String,
    pub node_id: String,
    pub node_kind: String,
    pub input_task: String,
    pub output_content: String,
    pub verdict: String,
    pub blocked: bool,
    pub block_reason: String,
    pub uncertainty: f64,
    pub cost_usd: f64,
    pub tokens_used: u64,
    pub carbon_gco2: f64,
    pub duration_ms: f64,
    pub timestamp: String,
    /// SHA-256 hash of the previous ledger entry (tamper-evident chain).
    pub prev_hash: String,
    /// SHA-256 hash of this entry.
    pub entry_hash: String,
}

/// Aggregated statistics across all steps in a run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceSummary {
    pub steps: u32,
    #[serde(default)]
    pub nodes: Vec<String>,
    pub total_cost_usd: f64,
    pub total_tokens: u64,
    pub total_carbon_gco2: f64,
    pub blocked_steps: u32,
    #[serde(default)]
    pub verdicts: Vec<String>,
    pub timestamps: TraceSummaryTimestamps,
}

/// Start / end timestamps from a [`TraceSummary`].
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceSummaryTimestamps {
    pub start: String,
    pub end: String,
}

/// Full execution record for a single run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trace {
    pub run_id: String,
    pub summary: TraceSummary,
    #[serde(default)]
    pub steps: Vec<TraceStep>,
}

// ── ZTStatus ──────────────────────────────────────────────────────────────────

/// Zero Trust posture snapshot returned by `GET /api/zt-status`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ZTStatus {
    pub tier: String,
    pub regulation: String,
    pub score_pct: u32,
    pub controls_enabled: u32,
    pub controls_gap: u32,
    pub env_tier: String,
    pub env_regulation: Option<String>,
}

// ── HealthResponse ────────────────────────────────────────────────────────────

/// Returned by `GET /health`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthResponse {
    pub ok: bool,
    pub version: String,
    pub uptime_s: f64,
    pub db: String,
}

/// Returned by `GET /health/live` and `GET /health/ready`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProbeResponse {
    pub live: Option<bool>,
    pub ready: Option<bool>,
    pub uptime_s: Option<f64>,
    pub version: Option<String>,
    pub reason: Option<String>,
}

/// A run currently paused for human approval.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PausedRun {
    pub run_id: String,
    pub paused_at: String,
}

// ── Internal request bodies ───────────────────────────────────────────────────

/// JSON body sent to `POST /run` and `POST /stream`.
#[derive(Debug, Serialize)]
pub(crate) struct RunRequestBody<'a> {
    pub task: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub policy: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<&'a HashMap<String, serde_json::Value>>,
}

/// JSON body sent to HITL approve/reject endpoints.
#[derive(Debug, Serialize)]
pub(crate) struct HitlDecisionBody<'a> {
    #[serde(skip_serializing_if = "str::is_empty")]
    pub reviewer_id: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    pub notes: &'a str,
}

// ── Cloud ingest — response types ─────────────────────────────────────────────

/// Generic `{ ok: true }` response from cloud ingest endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestOk {
    pub ok: Option<bool>,
    #[serde(default)]
    pub ingested: Option<u32>,
}

// ── Cloud ingest — span ───────────────────────────────────────────────────────

/// A single trace span sent to `POST /api/ingest/spans`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpanInput {
    pub run_id: String,
    pub agent_name: String,
    /// `"llm_call"`, `"tool_call"`, `"guardrail"`, `"policy_check"`, `"step"`.
    pub span_type: String,
    pub name: String,
    /// ISO-8601 timestamp when the span started.
    pub started_at: String,
    pub duration_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cost_usd: Option<f64>,
    /// `"ok"` or `"error"`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_msg: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<serde_json::Value>,
}

/// Builder for [`SpanInput`].
impl SpanInput {
    pub fn new(
        run_id: impl Into<String>,
        agent_name: impl Into<String>,
        name: impl Into<String>,
        started_at: impl Into<String>,
        duration_ms: u64,
    ) -> Self {
        Self {
            run_id: run_id.into(),
            agent_name: agent_name.into(),
            span_type: "step".into(),
            name: name.into(),
            started_at: started_at.into(),
            duration_ms,
            input_text: None,
            output_text: None,
            input_tokens: None,
            output_tokens: None,
            cost_usd: None,
            status: None,
            error_msg: None,
            metadata: None,
        }
    }

    pub fn span_type(mut self, t: impl Into<String>) -> Self {
        self.span_type = t.into();
        self
    }
    pub fn input_tokens(mut self, n: u32) -> Self { self.input_tokens = Some(n); self }
    pub fn output_tokens(mut self, n: u32) -> Self { self.output_tokens = Some(n); self }
    pub fn cost_usd(mut self, c: f64) -> Self { self.cost_usd = Some(c); self }
    pub fn output_text(mut self, t: impl Into<String>) -> Self { self.output_text = Some(t.into()); self }
    pub fn input_text(mut self, t: impl Into<String>) -> Self { self.input_text = Some(t.into()); self }
}

// ── Cloud ingest — eval ───────────────────────────────────────────────────────

/// Payload for `POST /api/ingest/eval`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalInput {
    pub run_id: String,
    #[serde(default)]
    pub suite: String,
    pub scenario: String,
    #[serde(default)]
    pub metric: String,
    pub score: f64,
    pub passed: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning: Option<String>,
    #[serde(default)]
    pub cost_usd: f64,
    #[serde(default)]
    pub latency_ms: u64,
}

// ── Cloud ingest — MCP ────────────────────────────────────────────────────────

/// Payload for `POST /api/ingest/mcp`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpCallInput {
    pub server_name: String,
    pub tool_name: String,
    #[serde(default)]
    pub transport: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub endpoint: Option<String>,
    #[serde(default)]
    pub latency_ms: u64,
    #[serde(default = "default_true")]
    pub success: bool,
    #[serde(default)]
    pub cost_usd: f64,
    #[serde(default)]
    pub tool_count: u32,
}

fn default_true() -> bool { true }

// ── Cloud ingest — worker job ─────────────────────────────────────────────────

/// Payload for `POST /api/ingest/worker`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkerJobInput {
    pub job_id: String,
    pub workflow_name: String,
    /// `"queued"`, `"running"`, `"completed"`, `"failed"`, `"retrying"`.
    pub status: String,
    #[serde(default)]
    pub retries: u32,
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    #[serde(default)]
    pub duration_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_msg: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scheduled_for: Option<String>,
}

fn default_max_retries() -> u32 { 3 }

// ── Prompt Hub ────────────────────────────────────────────────────────────────

/// Prompt record returned by `GET /api/ingest/prompts?slug=xxx`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptRecord {
    pub slug: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub version: u32,
    pub content: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub temperature: f64,
}

/// Summary item returned by `GET /api/ingest/prompts?list=1`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptSummary {
    pub slug: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(rename = "updatedAt", default)]
    pub updated_at: String,
}

// ── Dataset Hub ───────────────────────────────────────────────────────────────

/// A single dataset row.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatasetRow {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    pub input: String,
    #[serde(default, rename = "expected_output")]
    pub expected_output: String,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

impl DatasetRow {
    pub fn new(input: impl Into<String>) -> Self {
        Self {
            id: None,
            input: input.into(),
            expected_output: String::new(),
            metadata: serde_json::Value::Object(Default::default()),
        }
    }
    pub fn expected_output(mut self, v: impl Into<String>) -> Self {
        self.expected_output = v.into();
        self
    }
}

/// Dataset pull response from `GET /api/ingest/datasets?name=xxx`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatasetPullResponse {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default, rename = "row_count")]
    pub row_count: u32,
    #[serde(default)]
    pub rows: Vec<DatasetRow>,
}

/// Summary item from `GET /api/ingest/datasets`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatasetSummary {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default, rename = "rowCount")]
    pub row_count: u32,
    #[serde(rename = "updatedAt", default)]
    pub updated_at: String,
}

// ── Agent Registry ────────────────────────────────────────────────────────────

/// Agent definition returned by the cloud Agent Registry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentDefinition {
    pub id: String,
    pub slug: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub policy: String,
    #[serde(default, rename = "systemPrompt")]
    pub system_prompt: String,
    #[serde(default)]
    pub tags: String,
    #[serde(default, rename = "deployTarget")]
    pub deploy_target: String,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub status: String,
    #[serde(default, rename = "totalRuns")]
    pub total_runs: u64,
}
