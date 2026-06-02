# MeshFlow Java SDK

Zero-dependency Java 11+ client for the [MeshFlow](https://meshflow.dev) multi-agent orchestration platform.

## Installation

```xml
<dependency>
  <groupId>dev.meshflow</groupId>
  <artifactId>meshflow-sdk</artifactId>
  <version>1.5.0</version>
</dependency>
```

## Quickstart

```java
import dev.meshflow.MeshFlowClient;
import dev.meshflow.ZTPolicy;
import dev.meshflow.types.RunOptions;
import dev.meshflow.types.RunResult;

// 1. Create a client (reuse across requests — it is thread-safe)
MeshFlowClient client = new MeshFlowClient("http://localhost:8000", "mfk_yourkey");

// 2. Check server health
System.out.println(client.health());

// 3. Run a governed task (blocks until complete)
RunResult result = client.runAgent("Summarise the Q3 earnings report");
System.out.printf("run=%s  status=%s  cost=$%.4f%n",
    result.getRunId(), result.getStatus(), result.getTotalCostUsd());

// 4. Run with options — budget cap + HIPAA compliance
RunOptions opts = RunOptions.builder()
    .costCapUsd(1.50)
    .complianceProfile("hipaa")
    .policyMode("regulated")
    .build();
RunResult governed = client.runAgent("Extract patient summary", opts);

// 5. Stream token-by-token events (SSE)
client.streamAgent("Analyse this contract", event -> {
    if ("token_delta".equals(event.getEventType())) {
        System.out.print(event.getText());
    }
});

// 6. Fetch a trace (tamper-evident ledger)
var trace = client.getTrace(result.getRunId());
trace.getSteps().forEach(s -> System.out.println(s.getEntryHash()));

// 7. HITL approve / reject
client.approveHITL(result.getRunId(), "alice@example.com", "Looks good");

// 8. Zero Trust status
System.out.println(client.getZTStatus());

// 9. Zero Trust policy helpers
ZTPolicy hipaa = ZTPolicy.forRegulation("hipaa");
System.out.println(hipaa.getTier());      // ENTERPRISE
System.out.println(hipaa.getDescription());
```

## Zero Trust tiers

| Factory | Tier | Use case |
|---|---|---|
| `ZTPolicy.foundation()` | FOUNDATION | Dev / small deployments |
| `ZTPolicy.enterprise()` | ENTERPRISE | Production default |
| `ZTPolicy.advanced()` | ADVANCED | Regulated / national-security |
| `ZTPolicy.forRegulation("hipaa")` | ENTERPRISE+ | Healthcare |
| `ZTPolicy.forRegulation("fedramp")` | ADVANCED | Federal / critical infra |
