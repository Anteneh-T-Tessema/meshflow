# DASC Governance Gate

`DascGate` is MeshFlow's deterministic governance kernel — every agent action passes through it before execution, producing the same verdict for the same input every time with no LLM involved.

```python
from meshflow.security.dasc_gate import DascGate
from meshflow.core.schemas import Policy, Intent

gate = DascGate(policy=Policy(), run_id="run-abc123")
verdict = await gate.evaluate(Intent(
    agent_id="billing-agent",
    action="send_payment",
    payload={"amount": 5000},
))
# ActionVerdict.ESCALATE — irreversible action with human-in-the-loop enabled
```

## Processing pipeline

```
Intent → AutoRiskClassifier → TaintGraph → PolicyEval → AuditLedger → CompensationExecutor
```

1. `AutoRiskClassifier` computes `effective_tier` (overrides agent self-declaration)
2. `TaintGraph` propagates IFC taint across agents
3. Policy evaluation produces `COMMIT | REJECT | ESCALATE`
4. Hash-chained `AuditLedger` entry is appended
5. `CompensationExecutor` runs rollback on `REJECT` if a plan was declared

## AutoRiskClassifier

Overrides any risk tier the agent declares — agents cannot lie about their own risk.

```python
from meshflow.security.dasc_gate import AutoRiskClassifier

classifier = AutoRiskClassifier()
classifier.record_outcome("billing-agent", success=False)  # EMA failure tracking
tier = classifier.classify(intent)
```

**Classification priority** (highest wins):

| Tier               | Value | Triggers                                                        |
|--------------------|-------|-----------------------------------------------------------------|
| `IRREVERSIBLE` (4) | 4     | `delete`, `drop`, `deploy`, `send_payment`, `rm -rf`, `purge`  |
| `EXTERNAL_IO` (3)  | 3     | `write`, `update`, `send`, `email`; or sensitive payload keys; or failure rate > 50% |
| `INTERNAL` (2)     | 2     | `compute`, `transform`, `aggregate`, `cache`                   |
| `READ_ONLY` (1)    | 1     | Everything else                                                 |

Failure rate is tracked per agent with EMA (α = 0.3). Agents above 50% failure rate are escalated to `EXTERNAL_IO`.

## TaintGraph

Tracks information-flow-control (IFC) taint across agents. If Agent A uses untrusted data, Agent B's intent derived from A's output is automatically tainted.

```python
from meshflow.security.dasc_gate import TaintGraph

graph = TaintGraph()
graph.mark_tainted("agent-a")
graph.propagate("agent-a", "agent-b")  # returns True — taint spread
graph.is_tainted("agent-b")            # True
graph.clear("agent-b")                 # removes taint
```

Tainted `EXTERNAL_IO` intents are **rejected**. Tainted `IRREVERSIBLE` intents are **rejected** unless `human_in_loop` is enabled (which escalates).

## CompensationExecutor

Runs compensation steps declared on an `Intent` when the gate rejects it. Steps execute in reverse order (stack unwind), then the optional `rollback_fn` is called.

```python
from meshflow.security.dasc_gate import CompensationExecutor
from meshflow.core.schemas import CompensationPlan

executor = CompensationExecutor()
ok = await executor.execute(plan, reason="tier=4, tainted=true, verdict=REJECT")
```

## AuditLedger

An append-only SQLite ledger with SHA-256 hash chaining. Every gate decision is recorded; the chain detects any post-hoc tampering.

```python
from meshflow.security.dasc_gate import AuditLedger

ledger = AuditLedger(db_path="meshflow_dasc.db")
ledger.count()           # number of entries
ledger.verify_chain()    # True if no tampering detected
```

The `entry_hash` of each row is `SHA-256(json(entry_id, run_id, intent_id, action, verdict, timestamp, prev_hash))`.

## DascGate

```python
DascGate(
    policy:  Policy,
    run_id:  str,
    db_path: str = ":memory:",  # swap to Postgres URI in production
)

gate.evaluate(intent)               # async — returns ActionVerdict
gate.record_outcome(agent_id, ok)  # feed back success/failure for EMA
gate.propagate_taint(src, tgt)     # manually propagate taint
gate.ledger_count()                 # entries in ledger
gate.verify_ledger()               # True if hash chain intact
```

## Policy evaluation rules

| Tier           | Tainted | human_in_loop | Verdict      |
|----------------|---------|---------------|--------------|
| `READ_ONLY`    | any     | any           | `COMMIT`     |
| `INTERNAL`     | any     | any           | `COMMIT`     |
| `EXTERNAL_IO`  | False   | any           | `COMMIT`     |
| `EXTERNAL_IO`  | True    | any           | `REJECT`     |
| `IRREVERSIBLE` | any     | True          | `ESCALATE`   |
| `IRREVERSIBLE` | False   | False         | `COMMIT`     |
| `IRREVERSIBLE` | True    | False         | `REJECT`     |

## CLI

```bash
# Classify an action's risk tier
meshflow dasc classify --action send_payment --payload '{"amount": 5000}'

# Show recent ledger entries
meshflow dasc ledger --db meshflow_runs.db --limit 20

# Verify chain integrity
meshflow dasc verify --db meshflow_runs.db

# Show taint graph state
meshflow dasc taint --run-id <run_id>
```
