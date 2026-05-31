# Durable Workflows

`DurableWorkflowExecutor` wraps any `WorkflowDefinition` so every node's output is persisted to a checkpoint store — re-running with the same `run_id` replays completed nodes from the store instead of calling the LLM again.

```python
from meshflow.core.durable import DurableWorkflowExecutor
from meshflow.core.workflow import WorkflowDefinition

wf       = WorkflowDefinition.from_yaml("pipeline.yaml")
executor = DurableWorkflowExecutor(run_id="report-42", backend="sqlite", db_path="runs.db")

# First run — executes all nodes and persists their outputs
result = await executor.run(wf, task="Audit this contract")

# Interrupted mid-run? Resume with the exact same call:
result = await executor.run(wf, task="Audit this contract")
# Completed nodes are replayed from SQLite — no LLM calls.
```

## Constructor

```python
DurableWorkflowExecutor(
    run_id       = "my-run-001",    # stable ID; auto-generated if omitted
    backend      = "sqlite",        # see Backend Reference below
    db_path      = "runs.db",       # SQLite path (backend="sqlite" only)
    redis_url    = "",              # Redis DSN (backend="redis" only)
    postgres_url = "",              # Postgres DSN (backend="postgres" only)
    s3_bucket    = "",              # S3 bucket name (backend="s3" only)
    s3_prefix    = "meshflow/checkpoints",
)
```

## Backend Reference

| Backend | `backend=` | Extra params | Notes |
|---------|-----------|--------------|-------|
| In-process dict | `"memory"` | — | Tests / local dev; not durable |
| SQLite (default) | `"sqlite"` | `db_path` | Single machine; survives restarts |
| Redis | `"redis"` | `redis_url` or `MESHFLOW_REDIS_URL` | Distributed; requires `pip install redis` |
| PostgreSQL | `"postgres"` | `postgres_url` or `MESHFLOW_POSTGRES_URL` | Enterprise cloud; requires `pip install psycopg2-binary` |
| AWS S3 | `"s3"` | `s3_bucket` or `MESHFLOW_S3_BUCKET` | Serverless / cross-region; requires `pip install boto3` |

## Checkpoint / Resume Pattern

```python
executor = DurableWorkflowExecutor(run_id="run-99", backend="sqlite", db_path="state.db")

# Check which nodes have already completed
print(executor.status())
# {"gather": "completed", "analyze": "completed"}

# Check a specific node
if executor.is_completed("gather"):
    print("gather already done — will replay from cache")

# Wipe checkpoints and start fresh
executor.clear()
```

## `.fork()` — Branch from a Checkpoint

Create a new executor by copying all checkpoints from `parent_run_id` that were completed *before* `before_node_id`:

```python
# parent run completed: gather → analyze → review → publish
parent = DurableWorkflowExecutor(run_id="run-99", backend="sqlite", db_path="state.db")

# Fork before "review" — copies gather and analyze checkpoints into a new run
forked = parent.fork(
    parent_run_id  = "run-99",
    before_node_id = "review",
    new_run_id     = "run-99-variant-a",
)

# Run the forked executor — gather and analyze are replayed; review onward re-executes
result = await forked.run(wf, task="Audit this contract with stricter policy")
```

## S3 Serverless Backend Example

```python
import os
from meshflow.core.durable import DurableWorkflowExecutor

os.environ["MESHFLOW_S3_BUCKET"] = "my-company-meshflow"

executor = DurableWorkflowExecutor(
    run_id    = "lambda-run-001",
    backend   = "s3",
    s3_prefix = "prod/checkpoints",
    # region defaults to AWS_DEFAULT_REGION env var or "us-east-1"
)

result = await executor.run(wf, task="Process invoice batch")
```

Each node's output is stored under `prod/checkpoints/<run_id>/<node_id>.json`. A lightweight index at `prod/checkpoints/<run_id>/_index.json` tracks completion times. On Lambda cold start, the executor reads the index and skips already-completed nodes.

## Redis Backend Example

```python
from meshflow.core.durable import DurableWorkflowExecutor

executor = DurableWorkflowExecutor(
    run_id    = "worker-1234",
    backend   = "redis",
    redis_url = "rediss://user:pass@redis.example.com:6380/0",  # TLS
)
result = await executor.run(wf, task="Summarise regulatory updates")
```

Keys are stored as `meshflow:checkpoint:<run_id>:<node_id>` with a default 7-day TTL.

## PostgreSQL Backend Example

```python
from meshflow.core.durable import DurableWorkflowExecutor

executor = DurableWorkflowExecutor(
    run_id       = "prod-run-9001",
    backend      = "postgres",
    postgres_url = "postgresql://meshflow:secret@db.internal:5432/workflows",
)
result = await executor.run(wf, task="Monthly compliance audit")
```

## `meshflow replay` CLI

Inspect checkpointed runs without re-executing:

```bash
# List completed nodes for a run
meshflow replay status --run-id run-99 --db runs.db

# Show the output of a specific completed node
meshflow replay show --run-id run-99 --node analyze --db runs.db

# Delete all checkpoints for a run (start fresh)
meshflow replay clear --run-id run-99 --db runs.db
```

## Passing an Existing `Mesh`

```python
from meshflow.core.mesh import Mesh

mesh     = Mesh(mode="production")
executor = DurableWorkflowExecutor(run_id="r1", backend="sqlite", db_path="r.db")
result   = await executor.run(wf, task="...", mesh=mesh)
```

## How Checkpointing Works

`DurableWorkflowExecutor.run()` wraps every `MeshNode` in the workflow with a transparent checkpoint-checking runner:

1. Before calling the node's original runner, it checks the store for `(run_id, node_id)`.
2. If a cached `NodeOutput` is found, it is returned immediately with `metadata["_from_checkpoint"] = True`.
3. If not found, the original runner is called and the result is persisted before returning.

The original `MeshNode` runners are restored after execution, so the `WorkflowDefinition` object is unmodified and can be re-used.
