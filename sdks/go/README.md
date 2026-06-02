# MeshFlow Go SDK

Idiomatic Go client for the [MeshFlow](https://meshflow.dev) multi-agent orchestration platform.
Requires Go 1.21+. Zero external dependencies — stdlib only.

## Install

```bash
go get meshflow.dev/go-sdk
```

## Basic usage

```go
package main

import (
    "context"
    "fmt"
    "log"

    meshflow "meshflow.dev/go-sdk"
)

func main() {
    client := meshflow.NewClient("http://localhost:8000", "my-api-key")
    ctx := context.Background()

    result, err := client.RunAgent(ctx, "Summarise the quarterly report",
        meshflow.WithPolicyMode("standard"),
        meshflow.WithCostCap(0.50),
    )
    if err != nil {
        log.Fatal(err)
    }
    fmt.Printf("run_id=%s  cost=$%.4f\n", result.RunID, result.TotalCostUSD)
}
```

## Streaming

```go
events, err := client.Stream(ctx, "Analyse this contract")
if err != nil {
    log.Fatal(err)
}
for ev := range events {
    if ev.EventType == "token_delta" {
        fmt.Print(ev.Text)
    }
}
```

## Zero Trust policy

```go
// Use a named tier
policy := meshflow.EnterprisePolicy()

// Or target a specific regulation
policy = meshflow.ForRegulation("hipaa")
fmt.Println("enabled controls:", policy.ControlsEnabled())
fmt.Println("gap controls:    ", policy.ControlsDisabled())

// Check current server posture
status, err := client.ZTStatus(ctx)
fmt.Printf("tier=%s  score=%d%%  gap=%d controls\n",
    status.Tier, status.ScorePct, status.ControlsGap)
```
