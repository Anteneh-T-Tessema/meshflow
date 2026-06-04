//! [`MeshFlowClient`] — the primary entry point for the MeshFlow Rust SDK.
//!
//! All methods are `async` and run on a tokio runtime. The client is cheap to
//! clone because it wraps an `Arc`-backed [`reqwest::Client`].
//!
//! # Example
//! ```rust,no_run
//! # #[tokio::main]
//! # async fn main() -> Result<(), Box<dyn std::error::Error>> {
//! use meshflow_sdk::MeshFlowClient;
//!
//! let client = MeshFlowClient::new("http://localhost:8000", "my-api-key");
//! let result = client.run_agent("Summarise the Q3 earnings report").await?;
//! println!("run_id={} output={:?}", result.run_id, result.output);
//! # Ok(())
//! # }
//! ```

use std::time::Duration;

use futures_util::StreamExt;
use reqwest::{header, Response};

use crate::types::{
    AgentDefinition, DatasetPullResponse, DatasetRow, DatasetSummary, EvalInput, HealthResponse,
    HitlDecisionBody, IngestOk, McpCallInput, MeshFlowError, PausedRun, ProbeResponse,
    PromptRecord, PromptSummary, RunOptions, RunRequestBody, RunResult, SpanInput, StreamEvent,
    Trace, WorkerJobInput, ZTStatus,
};

const SDK_VERSION: &str = env!("CARGO_PKG_VERSION");
const DEFAULT_TIMEOUT_S: u64 = 120;

// ── MeshFlowClient ────────────────────────────────────────────────────────────

/// Async client for the MeshFlow REST/SSE API.
///
/// Create once and reuse across tasks — it is `Clone` and safe for concurrent
/// use from multiple tokio tasks.
#[derive(Debug, Clone)]
pub struct MeshFlowClient {
    pub(crate) base_url: String,
    pub(crate) api_key: String,
    pub(crate) client: reqwest::Client,
}

impl MeshFlowClient {
    /// Create a new client that talks to `base_url` and authenticates with
    /// `api_key`. Pass an empty string for `api_key` when connecting to an
    /// unauthenticated development server.
    pub fn new(base_url: &str, api_key: &str) -> Self {
        let base_url = base_url.trim_end_matches('/').to_owned();
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(DEFAULT_TIMEOUT_S))
            .build()
            .expect("failed to build reqwest client");
        Self {
            base_url,
            api_key: api_key.to_owned(),
            client,
        }
    }

    /// Create a client with a custom [`reqwest::Client`] (e.g. to configure
    /// TLS, proxies, or a different timeout).
    pub fn with_http_client(
        base_url: &str,
        api_key: &str,
        client: reqwest::Client,
    ) -> Self {
        let base_url = base_url.trim_end_matches('/').to_owned();
        Self {
            base_url,
            api_key: api_key.to_owned(),
            client,
        }
    }

    // ── internal helpers ──────────────────────────────────────────────────────

    fn auth_header(&self) -> Option<String> {
        if self.api_key.is_empty() {
            None
        } else {
            Some(format!("Bearer {}", self.api_key))
        }
    }

    fn apply_auth(&self, rb: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        if let Some(auth) = self.auth_header() {
            rb.header(header::AUTHORIZATION, auth)
        } else {
            rb
        }
    }

    /// Apply `x-meshflow-key` auth used by cloud ingest endpoints.
    fn apply_cloud_auth(&self, rb: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        if self.api_key.is_empty() {
            rb
        } else {
            rb.header("x-meshflow-key", &self.api_key)
        }
    }

    /// POST to a cloud ingest path with `x-meshflow-key` auth.
    async fn cloud_post<T>(&self, path: &str, body: &impl serde::Serialize) -> Result<T, MeshFlowError>
    where
        T: serde::de::DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        let rb = self
            .client
            .post(&url)
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::ACCEPT, "application/json")
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"))
            .json(body);
        let rb = self.apply_cloud_auth(rb);
        let resp = rb.send().await?;
        self.decode_response(resp).await
    }

    /// GET a cloud ingest path with `x-meshflow-key` auth.
    async fn cloud_get<T>(&self, path: &str) -> Result<T, MeshFlowError>
    where
        T: serde::de::DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        let rb = self
            .client
            .get(&url)
            .header(header::ACCEPT, "application/json")
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"));
        let rb = self.apply_cloud_auth(rb);
        let resp = rb.send().await?;
        self.decode_response(resp).await
    }

    /// DELETE a cloud ingest path with `x-meshflow-key` auth.
    async fn cloud_delete<T>(&self, path: &str) -> Result<T, MeshFlowError>
    where
        T: serde::de::DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        let rb = self
            .client
            .delete(&url)
            .header(header::ACCEPT, "application/json")
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"));
        let rb = self.apply_cloud_auth(rb);
        let resp = rb.send().await?;
        self.decode_response(resp).await
    }

    /// Build and send a JSON request; decode the 2xx response body into `T`.
    async fn request<T>(&self, method: reqwest::Method, path: &str, body: Option<&impl serde::Serialize>) -> Result<T, MeshFlowError>
    where
        T: serde::de::DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        let rb = self
            .client
            .request(method, &url)
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::ACCEPT, "application/json")
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"));
        let rb = self.apply_auth(rb);
        let rb = if let Some(b) = body {
            rb.json(b)
        } else {
            rb
        };

        let resp = rb.send().await?;
        self.decode_response(resp).await
    }

    /// Interpret a response, converting non-2xx status codes into errors.
    async fn decode_response<T>(&self, resp: Response) -> Result<T, MeshFlowError>
    where
        T: serde::de::DeserializeOwned,
    {
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            if status.as_u16() == 401 || status.as_u16() == 403 {
                return Err(MeshFlowError::Auth {
                    status: status.as_u16(),
                    body,
                });
            }
            return Err(MeshFlowError::Http {
                status: status.as_u16(),
                body,
            });
        }
        let bytes = resp.bytes().await?;
        serde_json::from_slice(&bytes).map_err(MeshFlowError::Json)
    }

    // ── Health ────────────────────────────────────────────────────────────────

    /// `GET /health` — returns server status. Authentication is not required.
    pub async fn health(&self) -> Result<HealthResponse, MeshFlowError> {
        self.request(reqwest::Method::GET, "/health", None::<&()>).await
    }

    /// `GET /health/live` — Kubernetes liveness probe.
    pub async fn health_live(&self) -> Result<ProbeResponse, MeshFlowError> {
        self.request(reqwest::Method::GET, "/health/live", None::<&()>).await
    }

    /// `GET /health/ready` — Kubernetes readiness probe.
    pub async fn health_ready(&self) -> Result<ProbeResponse, MeshFlowError> {
        self.request(reqwest::Method::GET, "/health/ready", None::<&()>).await
    }

    // ── Task execution ────────────────────────────────────────────────────────

    /// Execute `task` on the MeshFlow server and block until the run completes.
    ///
    /// For incremental token-by-token output see [`stream_agent`](Self::stream_agent).
    pub async fn run_agent(&self, task: &str) -> Result<RunResult, MeshFlowError> {
        self.run_agent_with_options(task, &RunOptions::default()).await
    }

    /// Like [`run_agent`](Self::run_agent) but accepts a [`RunOptions`] for
    /// governance, budget, and policy configuration.
    pub async fn run_agent_with_options(
        &self,
        task: &str,
        opts: &RunOptions,
    ) -> Result<RunResult, MeshFlowError> {
        let body = RunRequestBody {
            task,
            policy: opts.to_policy_map(),
            context: opts.context.as_ref(),
        };
        self.request(reqwest::Method::POST, "/run", Some(&body)).await
    }

    /// Start a streaming task run and return a [`futures_util::Stream`] of
    /// [`StreamEvent`]s. The server sends NDJSON (or SSE) lines over a
    /// persistent HTTP connection.
    ///
    /// # Example
    /// ```rust,no_run
    /// # #[tokio::main]
    /// # async fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// use futures_util::StreamExt;
    /// use meshflow_sdk::MeshFlowClient;
    ///
    /// let client = MeshFlowClient::new("http://localhost:8000", "key");
    /// let mut stream = client.stream_agent("Analyse this contract").await?;
    /// while let Some(ev) = stream.next().await {
    ///     let ev = ev?;
    ///     if ev.event_type == "token_delta" {
    ///         print!("{}", ev.text);
    ///     }
    /// }
    /// # Ok(())
    /// # }
    /// ```
    pub async fn stream_agent(
        &self,
        task: &str,
    ) -> Result<
        impl futures_util::Stream<Item = Result<StreamEvent, MeshFlowError>>,
        MeshFlowError,
    > {
        let body = RunRequestBody {
            task,
            policy: None,
            context: None,
        };

        let url = format!("{}/stream", self.base_url);
        let rb = self
            .client
            .post(&url)
            .header(header::CONTENT_TYPE, "application/json")
            .header(
                header::ACCEPT,
                "application/x-ndjson, text/event-stream",
            )
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"))
            .json(&body);
        let rb = self.apply_auth(rb);

        let resp = rb.send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body_text = resp.text().await.unwrap_or_default();
            if status.as_u16() == 401 || status.as_u16() == 403 {
                return Err(MeshFlowError::Auth {
                    status: status.as_u16(),
                    body: body_text,
                });
            }
            return Err(MeshFlowError::Http {
                status: status.as_u16(),
                body: body_text,
            });
        }

        // Convert the byte stream into a stream of parsed StreamEvents.
        let byte_stream = resp.bytes_stream();
        let event_stream = byte_stream.flat_map(|chunk_result| {
            // Each chunk may contain one or more newline-delimited JSON lines.
            let lines: Vec<Result<StreamEvent, MeshFlowError>> = match chunk_result {
                Err(e) => vec![Err(MeshFlowError::Network(e.to_string()))],
                Ok(bytes) => {
                    let text = String::from_utf8_lossy(&bytes);
                    text.lines()
                        .filter_map(|line| {
                            let line = line.trim();
                            if line.is_empty() || line == "[DONE]" {
                                return None;
                            }
                            // Strip optional SSE "data: " prefix.
                            let line = if let Some(stripped) = line.strip_prefix("data:") {
                                stripped.trim()
                            } else {
                                line
                            };
                            if line.is_empty() || line == "[DONE]" {
                                return None;
                            }
                            Some(
                                serde_json::from_str::<StreamEvent>(line)
                                    .map_err(MeshFlowError::Json),
                            )
                        })
                        .collect()
                }
            };
            futures_util::stream::iter(lines)
        });

        Ok(event_stream)
    }

    // ── Traces ────────────────────────────────────────────────────────────────

    /// `GET /traces` — returns all run IDs recorded in the ledger.
    pub async fn list_runs(&self) -> Result<Vec<String>, MeshFlowError> {
        #[derive(serde::Deserialize)]
        struct Wrapper {
            runs: Vec<String>,
        }
        let w: Wrapper = self
            .request(reqwest::Method::GET, "/traces", None::<&()>)
            .await?;
        Ok(w.runs)
    }

    /// `GET /traces/{run_id}` — returns the full execution trace including all
    /// step records and the tamper-evident hash chain.
    pub async fn get_trace(&self, run_id: &str) -> Result<Trace, MeshFlowError> {
        let path = format!("/traces/{}", urlencoding::encode(run_id));
        self.request(reqwest::Method::GET, &path, None::<&()>).await
    }

    // ── HITL ──────────────────────────────────────────────────────────────────

    /// `GET /hitl/pending` — returns all runs currently paused for human
    /// approval.
    pub async fn list_pending_hitl(&self) -> Result<Vec<PausedRun>, MeshFlowError> {
        #[derive(serde::Deserialize)]
        struct Wrapper {
            paused_runs: Vec<PausedRun>,
        }
        let w: Wrapper = self
            .request(reqwest::Method::GET, "/hitl/pending", None::<&()>)
            .await?;
        Ok(w.paused_runs)
    }

    /// `POST /hitl/{run_id}/approve` — approve a paused run so it continues
    /// execution. `reviewer_id` and `notes` are forwarded to the audit log.
    ///
    /// Returns `true` on success.
    pub async fn approve_hitl(
        &self,
        run_id: &str,
        reviewer_id: &str,
        notes: &str,
    ) -> Result<bool, MeshFlowError> {
        let path = format!("/hitl/{}/approve", urlencoding::encode(run_id));
        let body = HitlDecisionBody { reviewer_id, notes };
        // The endpoint returns 200 with no body on success; we treat any 2xx
        // as true and surface errors via the error path.
        let url = format!("{}{}", self.base_url, path);
        let rb = self
            .client
            .post(&url)
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"))
            .json(&body);
        let rb = self.apply_auth(rb);
        let resp = rb.send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body_text = resp.text().await.unwrap_or_default();
            if status.as_u16() == 401 || status.as_u16() == 403 {
                return Err(MeshFlowError::Auth {
                    status: status.as_u16(),
                    body: body_text,
                });
            }
            return Err(MeshFlowError::Http {
                status: status.as_u16(),
                body: body_text,
            });
        }
        Ok(true)
    }

    /// `POST /hitl/{run_id}/reject` — reject a paused run, aborting execution.
    ///
    /// Returns `true` on success.
    pub async fn reject_hitl(
        &self,
        run_id: &str,
        reviewer_id: &str,
        notes: &str,
    ) -> Result<bool, MeshFlowError> {
        let path = format!("/hitl/{}/reject", urlencoding::encode(run_id));
        let body = HitlDecisionBody { reviewer_id, notes };
        let url = format!("{}{}", self.base_url, path);
        let rb = self
            .client
            .post(&url)
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"))
            .json(&body);
        let rb = self.apply_auth(rb);
        let resp = rb.send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body_text = resp.text().await.unwrap_or_default();
            if status.as_u16() == 401 || status.as_u16() == 403 {
                return Err(MeshFlowError::Auth {
                    status: status.as_u16(),
                    body: body_text,
                });
            }
            return Err(MeshFlowError::Http {
                status: status.as_u16(),
                body: body_text,
            });
        }
        Ok(true)
    }

    // ── Zero Trust ────────────────────────────────────────────────────────────

    /// `GET /api/zt-status` — returns the current Zero Trust posture snapshot.
    pub async fn zt_status(&self) -> Result<ZTStatus, MeshFlowError> {
        self.request(reqwest::Method::GET, "/api/zt-status", None::<&()>).await
    }

    // ── Compliance ────────────────────────────────────────────────────────────

    /// `GET /compliance/report?framework=…` — generate a compliance report.
    ///
    /// `framework` is one of `"hipaa"`, `"sox"`, `"gdpr"`, `"pci"`, `"nerc"`.
    /// Pass `None` for `run_id` to aggregate the last 50 runs.
    pub async fn compliance_report(
        &self,
        framework: &str,
        run_id: Option<&str>,
    ) -> Result<serde_json::Value, MeshFlowError> {
        let mut path = format!(
            "/compliance/report?framework={}",
            urlencoding::encode(framework)
        );
        if let Some(id) = run_id {
            path.push_str("&run_id=");
            path.push_str(&urlencoding::encode(id));
        }
        self.request(reqwest::Method::GET, &path, None::<&()>).await
    }

    // ── Metrics ───────────────────────────────────────────────────────────────

    /// `GET /metrics` — returns raw Prometheus metrics text.
    pub async fn metrics(&self) -> Result<String, MeshFlowError> {
        let url = format!("{}/metrics", self.base_url);
        let rb = self
            .client
            .get(&url)
            .header(header::USER_AGENT, format!("meshflow-rust-sdk/{SDK_VERSION}"));
        let rb = self.apply_auth(rb);
        let resp = rb.send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(MeshFlowError::Http {
                status: status.as_u16(),
                body,
            });
        }
        resp.text().await.map_err(|e| MeshFlowError::Network(e.to_string()))
    }

    // ── Cloud ingest — runs, evals, MCP, workers ──────────────────────────────

    /// `POST /api/ingest/run` — report a completed workflow run to the cloud
    /// dashboard. Returns `true` on success.
    pub async fn report_run(&self, payload: &serde_json::Value) -> Result<bool, MeshFlowError> {
        let r: IngestOk = self.cloud_post("/api/ingest/run", payload).await?;
        Ok(r.ok.unwrap_or(true))
    }

    /// `POST /api/ingest/eval` — push one eval result to `/dashboard/evals`.
    pub async fn report_eval(&self, eval: &EvalInput) -> Result<bool, MeshFlowError> {
        let r: IngestOk = self.cloud_post("/api/ingest/eval", eval).await?;
        Ok(r.ok.unwrap_or(true))
    }

    /// `POST /api/ingest/mcp` — record one MCP tool call to `/dashboard/mcp`.
    pub async fn report_mcp_call(&self, call: &McpCallInput) -> Result<bool, MeshFlowError> {
        let r: IngestOk = self.cloud_post("/api/ingest/mcp", call).await?;
        Ok(r.ok.unwrap_or(true))
    }

    /// `POST /api/ingest/worker` — upsert a worker job status event.
    pub async fn report_worker_job(&self, job: &WorkerJobInput) -> Result<bool, MeshFlowError> {
        let r: IngestOk = self.cloud_post("/api/ingest/worker", job).await?;
        Ok(r.ok.unwrap_or(true))
    }

    // ── Cloud ingest — trace spans ────────────────────────────────────────────

    /// `POST /api/ingest/spans` — send a batch of per-step trace spans to
    /// `/dashboard/traces`.
    ///
    /// # Example
    /// ```rust,no_run
    /// # #[tokio::main]
    /// # async fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// use meshflow_sdk::{MeshFlowClient, SpanInput};
    /// let client = MeshFlowClient::new("https://meshflow.dev", "mf_sk_...");
    /// let span = SpanInput::new("run-123", "planner", "plan", "2026-06-04T10:00:00Z", 420)
    ///     .span_type("step")
    ///     .input_tokens(512)
    ///     .output_tokens(128)
    ///     .cost_usd(0.0014);
    /// client.report_spans(vec![span]).await?;
    /// # Ok(())
    /// # }
    /// ```
    pub async fn report_spans(&self, spans: Vec<SpanInput>) -> Result<u32, MeshFlowError> {
        #[derive(serde::Serialize)]
        struct Body { spans: Vec<SpanInput> }
        let r: IngestOk = self.cloud_post("/api/ingest/spans", &Body { spans }).await?;
        Ok(r.ingested.unwrap_or(0))
    }

    // ── Prompt Hub ────────────────────────────────────────────────────────────

    /// `GET /api/ingest/prompts?slug=xxx` — fetch the active (or pinned) version
    /// of a prompt.
    ///
    /// Returns `None` when the slug is not found rather than an error.
    pub async fn prompt_get(
        &self,
        slug: &str,
        version: Option<u32>,
    ) -> Result<Option<PromptRecord>, MeshFlowError> {
        let mut path = format!(
            "/api/ingest/prompts?slug={}",
            urlencoding::encode(slug)
        );
        if let Some(v) = version {
            path.push_str(&format!("&version={v}"));
        }
        match self.cloud_get::<PromptRecord>(&path).await {
            Ok(p) => Ok(Some(p)),
            Err(MeshFlowError::Http { status: 404, .. }) => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// `GET /api/ingest/prompts?list=1` — list all prompt slugs for the org.
    pub async fn prompt_list(&self) -> Result<Vec<PromptSummary>, MeshFlowError> {
        self.cloud_get("/api/ingest/prompts?list=1").await
    }

    /// `POST /api/ingest/prompts` — push a new version of a prompt (creates the
    /// prompt if it doesn't exist yet).
    pub async fn prompt_push(
        &self,
        slug: &str,
        content: &str,
        name: Option<&str>,
        notes: Option<&str>,
    ) -> Result<PromptRecord, MeshFlowError> {
        #[derive(serde::Serialize)]
        struct Body<'a> {
            slug: &'a str,
            content: &'a str,
            #[serde(skip_serializing_if = "Option::is_none")]
            name: Option<&'a str>,
            #[serde(skip_serializing_if = "Option::is_none")]
            notes: Option<&'a str>,
        }
        self.cloud_post("/api/ingest/prompts", &Body { slug, content, name, notes }).await
    }

    // ── Dataset Hub ───────────────────────────────────────────────────────────

    /// `GET /api/ingest/datasets` — list all datasets for the org.
    pub async fn dataset_list(&self) -> Result<Vec<DatasetSummary>, MeshFlowError> {
        self.cloud_get("/api/ingest/datasets").await
    }

    /// `GET /api/ingest/datasets?name=xxx` — pull rows from a named dataset.
    pub async fn dataset_pull(
        &self,
        name: &str,
        limit: Option<u32>,
        offset: Option<u32>,
    ) -> Result<Option<DatasetPullResponse>, MeshFlowError> {
        let mut path = format!("/api/ingest/datasets?name={}", urlencoding::encode(name));
        if let Some(l) = limit { path.push_str(&format!("&limit={l}")); }
        if let Some(o) = offset { path.push_str(&format!("&offset={o}")); }
        match self.cloud_get::<DatasetPullResponse>(&path).await {
            Ok(d) => Ok(Some(d)),
            Err(MeshFlowError::Http { status: 404, .. }) => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// `POST /api/ingest/datasets` — append rows to a named dataset (creates it
    /// if it doesn't exist yet).
    pub async fn dataset_push(
        &self,
        name: &str,
        rows: Vec<DatasetRow>,
        description: Option<&str>,
    ) -> Result<String, MeshFlowError> {
        #[derive(serde::Serialize)]
        struct Body<'a> {
            name: &'a str,
            rows: Vec<DatasetRow>,
            #[serde(skip_serializing_if = "Option::is_none")]
            description: Option<&'a str>,
        }
        #[derive(serde::Deserialize)]
        struct Resp { id: String }
        let r: Resp = self.cloud_post("/api/ingest/datasets", &Body { name, rows, description }).await?;
        Ok(r.id)
    }

    /// `DELETE /api/ingest/datasets?name=xxx` — delete a dataset and all its
    /// rows.
    pub async fn dataset_delete(&self, name: &str) -> Result<bool, MeshFlowError> {
        let path = format!("/api/ingest/datasets?name={}", urlencoding::encode(name));
        let r: IngestOk = self.cloud_delete(&path).await?;
        Ok(r.ok.unwrap_or(true))
    }

    // ── Agent Registry ────────────────────────────────────────────────────────

    /// `GET /api/ingest/agents` — list all registered agent definitions.
    pub async fn list_agents(&self) -> Result<Vec<AgentDefinition>, MeshFlowError> {
        self.cloud_get("/api/ingest/agents").await
    }

    /// `GET /api/ingest/agents?slug=xxx` — fetch one agent definition.
    ///
    /// Returns `None` when the slug is not found.
    pub async fn get_agent(&self, slug: &str) -> Result<Option<AgentDefinition>, MeshFlowError> {
        let path = format!("/api/ingest/agents?slug={}", urlencoding::encode(slug));
        match self.cloud_get::<AgentDefinition>(&path).await {
            Ok(a) => Ok(Some(a)),
            Err(MeshFlowError::Http { status: 404, .. }) => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// `POST /api/ingest/agents` — upsert an agent definition in the cloud
    /// registry. Creates the entry on first call; subsequent calls update it.
    pub async fn register_agent(
        &self,
        name: &str,
        slug: &str,
        role: Option<&str>,
        model: Option<&str>,
        policy: Option<&str>,
    ) -> Result<AgentDefinition, MeshFlowError> {
        #[derive(serde::Serialize)]
        struct Body<'a> {
            name: &'a str,
            slug: &'a str,
            #[serde(skip_serializing_if = "Option::is_none")]
            role: Option<&'a str>,
            #[serde(skip_serializing_if = "Option::is_none")]
            model: Option<&'a str>,
            #[serde(skip_serializing_if = "Option::is_none")]
            policy: Option<&'a str>,
        }
        self.cloud_post("/api/ingest/agents", &Body { name, slug, role, model, policy }).await
    }

    /// Increment the run counter for a registered agent.
    ///
    /// Pass `run_count = 1` after each successful run to keep the
    /// `/dashboard/agents` stats current.
    pub async fn record_agent_run(&self, slug: &str, run_count: u32) -> Result<bool, MeshFlowError> {
        let name = slug;
        #[derive(serde::Serialize)]
        struct Body<'a> { name: &'a str, slug: &'a str, run_count: u32 }
        let r: IngestOk = self
            .cloud_post("/api/ingest/agents", &Body { name, slug, run_count })
            .await?;
        Ok(r.ok.unwrap_or(true))
    }
}

// ── URL encoding helper (no extra dep) ───────────────────────────────────────

mod urlencoding {
    /// Percent-encode a string for use in a URL path or query segment.
    pub fn encode(s: &str) -> String {
        let mut out = String::with_capacity(s.len());
        for byte in s.bytes() {
            match byte {
                b'A'..=b'Z'
                | b'a'..=b'z'
                | b'0'..=b'9'
                | b'-'
                | b'_'
                | b'.'
                | b'~' => out.push(byte as char),
                b => out.push_str(&format!("%{b:02X}")),
            }
        }
        out
    }
}
