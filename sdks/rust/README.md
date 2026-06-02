# meshflow-sdk (Rust)

Async Rust SDK for [MeshFlow](https://meshflow.ai) — governed multi-agent orchestration.

## Installation

Add to your `Cargo.toml`:

```toml
[dependencies]
meshflow-sdk = "1.6.0"
tokio = { version = "1", features = ["full"] }
```

## Quick start

```rust
use meshflow_sdk::{MeshFlowClient, RunOptions};
use futures_util::StreamExt;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = MeshFlowClient::new("http://localhost:8000", "my-api-key");

    // --- Basic run ---
    let result = client.run_agent("Summarise the Q3 earnings report").await?;
    println!("run_id={} status={:?}", result.run_id, result.status);

    // --- With governance options ---
    let opts = RunOptions::new()
        .policy_mode("hipaa")
        .cost_cap_usd(1.50)
        .budget_tokens(50_000)
        .max_steps(20);
    let result = client.run_agent_with_options("Analyse patient data", &opts).await?;
    println!("cost=${:.4}  tokens={}", result.total_cost_usd, result.total_tokens);

    // --- Streaming ---
    let mut stream = client.stream_agent("Draft a release note").await?;
    while let Some(ev) = stream.next().await {
        let ev = ev?;
        if ev.event_type == "token_delta" { print!("{}", ev.text); }
    }

    // --- Zero Trust policy ---
    use meshflow_sdk::zt_policy::ZTPolicy;
    let policy = ZTPolicy::for_regulation("hipaa");
    println!("tier={} controls={}", policy.tier, policy.controls_enabled().len());

    // --- HITL (human-in-the-loop) ---
    let _ = client.approve_hitl("run-abc", "alice@co.com", "Looks good").await?;

    Ok(())
}
```

## Environment variables

| Variable | Description |
|---|---|
| `MESHFLOW_ZT_TIER` | `foundation` / `enterprise` / `advanced` |
| `MESHFLOW_ZT_REGULATION` | `hipaa` / `sox` / `gdpr` / `pci` / `nerc` |
