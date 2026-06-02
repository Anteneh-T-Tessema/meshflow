//! Quickstart example for the MeshFlow Rust SDK.
//!
//! Run with:
//!   cargo run --example quickstart
//!
//! Set MESHFLOW_API_KEY and optionally MESHFLOW_BASE_URL before running.

use futures_util::StreamExt;
use meshflow_sdk::{MeshFlowClient, RunOptions};
use meshflow_sdk::zt_policy::ZTPolicy;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let base_url = std::env::var("MESHFLOW_BASE_URL")
        .unwrap_or_else(|_| "http://localhost:8000".into());
    let api_key = std::env::var("MESHFLOW_API_KEY").unwrap_or_default();

    let client = MeshFlowClient::new(&base_url, &api_key);

    // ── Health check ──────────────────────────────────────────────────────────
    println!("Checking server health...");
    match client.health().await {
        Ok(h) => println!("  ok={} version={} uptime={:.1}s", h.ok, h.version, h.uptime_s),
        Err(e) => println!("  health check failed: {e}"),
    }

    // ── Zero Trust policy ─────────────────────────────────────────────────────
    let policy = ZTPolicy::for_regulation("hipaa");
    println!(
        "\nZT policy: tier={} controls_enabled={}",
        policy.tier,
        policy.controls_enabled().len()
    );

    // ── Run agent ─────────────────────────────────────────────────────────────
    println!("\nRunning agent...");
    let opts = RunOptions::new()
        .policy_mode("standard")
        .cost_cap_usd(0.50)
        .max_steps(10);

    match client
        .run_agent_with_options("Echo: hello from the Rust SDK", &opts)
        .await
    {
        Ok(r) => println!(
            "  run_id={} status={:?} cost=${:.4}",
            r.run_id, r.status, r.total_cost_usd
        ),
        Err(e) => println!("  run failed (server may be offline): {e}"),
    }

    // ── Streaming ─────────────────────────────────────────────────────────────
    println!("\nStreaming agent output (first 5 events):");
    match client.stream_agent("Count to three").await {
        Ok(mut stream) => {
            let mut count = 0;
            while let Some(ev) = stream.next().await {
                match ev {
                    Ok(e) => {
                        println!("  event[{count}] kind={} text={:?}", e.event_type, e.text);
                        count += 1;
                        if count >= 5 {
                            break;
                        }
                    }
                    Err(e) => {
                        println!("  stream error: {e}");
                        break;
                    }
                }
            }
        }
        Err(e) => println!("  stream connect failed (server may be offline): {e}"),
    }

    println!("\nDone.");
    Ok(())
}
