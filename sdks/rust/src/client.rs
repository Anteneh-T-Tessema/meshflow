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
    HealthResponse, HitlDecisionBody, MeshFlowError, PausedRun, ProbeResponse, RunOptions,
    RunRequestBody, RunResult, StreamEvent, Trace, ZTStatus,
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
