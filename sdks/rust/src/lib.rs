//! # meshflow-sdk
//!
//! Async Rust SDK for the [MeshFlow](https://meshflow.ai) multi-agent
//! orchestration platform.
//!
//! ## Quick start
//!
//! ```rust,no_run
//! #[tokio::main]
//! async fn main() -> Result<(), Box<dyn std::error::Error>> {
//!     use meshflow_sdk::{MeshFlowClient, RunOptions};
//!
//!     let client = MeshFlowClient::new("http://localhost:8000", "my-api-key");
//!
//!     // Simple run
//!     let result = client.run_agent("Summarise the Q3 earnings report").await?;
//!     println!("Status: {:?}", result.status);
//!
//!     // Run with governance options
//!     let opts = RunOptions::new()
//!         .policy_mode("hipaa")
//!         .cost_cap_usd(1.50)
//!         .budget_tokens(50_000);
//!     let result = client.run_agent_with_options("Analyse patient data", &opts).await?;
//!     println!("Cost: ${:.4}", result.total_cost_usd);
//!
//!     Ok(())
//! }
//! ```
//!
//! ## Modules
//!
//! | Module | Contents |
//! |--------|----------|
//! | *(root)* | [`MeshFlowClient`], [`RunOptions`], error and response types |
//! | [`zt_policy`] | [`ZTPolicy`](zt_policy::ZTPolicy), [`ZTTier`](zt_policy::ZTTier) |

pub mod client;
pub mod types;
pub mod zt_policy;

// Re-export the most commonly used items at the crate root for ergonomics.
pub use client::MeshFlowClient;
pub use types::{
    AgentDefinition, DatasetPullResponse, DatasetRow, DatasetSummary, EvalInput,
    HealthResponse, IngestOk, McpCallInput, MeshFlowError, PausedRun, ProbeResponse,
    PromptRecord, PromptSummary, RunOptions, RunResult, RunStatus, SpanInput,
    StreamEvent, Trace, TraceSummary, TraceStep, WorkerJobInput, ZTStatus,
};
