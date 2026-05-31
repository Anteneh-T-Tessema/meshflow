# meshflow.core — Core API Reference

Runtime kernel, ledger, workflow, and state graph primitives.

## StepRuntime

The 15-step governed execution kernel. Every agent step passes through:
identity verification → tenant scoping → rate limiting → budget check → policy evaluation → compliance profile → input guardrails → sensitive data scan → risk classification → taint propagation → tool permission → execution → output guardrails → audit ledger → SLA record.

```python
from meshflow import StepRuntime, RuntimeOutcome

# StepRuntime is used internally by Agent; exposed for advanced custom nodes.
runtime = StepRuntime(ledger=ledger, policy=policy)
outcome: RuntimeOutcome = await runtime.run(node_input)
```

## ReplayLedger

```python
from meshflow import ReplayLedger, RunDiff

ledger = ReplayLedger("meshflow_runs.db")            # SQLite
ledger = ReplayLedger("postgresql://user:pass@host/db")  # Postgres

# Write
await ledger.write(step_record)

# Query
steps   = await ledger.get_run("run-id")
run_ids = await ledger.list_runs()
summary = await ledger.run_summary("run-id")

# Time-travel
step  = await ledger.load_state("run-id", step_index=2)
diff  = await ledger.diff("run-a", "run-b")          # → RunDiff
new_id = await ledger.fork("run-id", from_step=3)    # → str (new run ID)

# Audit
chain = await ledger.verify_chain("run-id")    # tamper-evident check
json  = await ledger.export_run("run-id")
csv   = await ledger.export_run_csv("run-id")

# GDPR
n = await ledger.delete_run("run-id")
n = await ledger.anonymize_run("run-id")
```

## RunDiff

```python
from meshflow import RunDiff

diff: RunDiff = await ledger.diff("run-a", "run-b")
diff.only_in_a       # list[str] — node IDs only in run A
diff.only_in_b       # list[str] — node IDs only in run B
diff.common          # list[str] — node IDs in both
diff.changed         # list[dict] — common nodes with different output/verdict
diff.cost_delta_usd  # float — run_b - run_a
diff.token_delta     # int   — run_b - run_a
```

## StateGraph

```python
from meshflow import StateGraph, END, START, add, last, node
from typing import TypedDict

class State(TypedDict):
    messages: list[str]
    count: int

@node
def step_a(state: State) -> State:
    return {"count": state["count"] + 1}

def route(state: State) -> str:
    return "done" if state["count"] >= 3 else "step_a"

graph = (
    StateGraph(State)
    .add_node("step_a", step_a)
    .add_conditional_edges("step_a", route, {"done": END, "step_a": "step_a"})
    .set_entry_point("step_a")
    .compile()
)

result = graph.invoke({"messages": [], "count": 0})
```

## WorkflowDefinition

```python
from meshflow import WorkflowDefinition, WorkflowResult

wf = WorkflowDefinition.from_yaml("workflow.yaml")
result: WorkflowResult = await wf.run(input="summarize AI safety")

yaml_str = wf.to_yaml()            # export back to YAML
wf.to_yaml(path="out.yaml")        # write to file
```

## DurableWorkflowExecutor

```python
from meshflow import DurableWorkflowExecutor

# SQLite (default)
exe = DurableWorkflowExecutor(run_id="my-run", backend="sqlite")

# Redis — survives process restarts
exe = DurableWorkflowExecutor(run_id="my-run", backend="redis", redis_url="redis://localhost")

# Postgres
exe = DurableWorkflowExecutor(run_id="my-run", backend="postgres",
                               postgres_url="postgresql://...")

# S3 — serverless resume
exe = DurableWorkflowExecutor(run_id="my-run", backend="s3",
                               s3_bucket="my-bucket", s3_prefix="runs")

# Fork from checkpoint
forked = exe.fork(parent_run_id="run-1", before_node_id="node-3")
```

## Mesh (Control Plane)

```python
from meshflow import Mesh, MeshEvent

mesh = Mesh()
mesh.register(agent)
await mesh.run_workflow(workflow_def, input="task text")

mesh.on(MeshEvent.STEP_COMPLETE, handler)
mesh.on(MeshEvent.POLICY_VIOLATION, handler)
```

## WorkflowEventBus

```python
from meshflow import WorkflowEventBus

bus = WorkflowEventBus()
bus.subscribe("step_complete", my_handler)
await bus.publish("step_complete", payload)
```
