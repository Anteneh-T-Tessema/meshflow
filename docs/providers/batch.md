# Batch Processing

`AnthropicBatchClient` submits workloads to the Anthropic Message Batches API, which applies a **50% cost discount** for tasks that can tolerate up to 24-hour processing time.

```python
from meshflow.batch.anthropic_batch import batch_agent_tasks

# High-level: submit a list of tasks and wait for results
results = await batch_agent_tasks(
    tasks=["Summarise doc1.pdf", "Summarise doc2.pdf", "Audit contract.txt"],
    agent_model="claude-haiku-4-5-20251001",
    system_prompt="You are a thorough analyst.",
)
for r in results:
    print(r.custom_id, r.succeeded, r.output[:80])
```

## When to Use Batch

| Use Case | Suitable? |
|----------|-----------|
| Nightly eval runs (1000s of agent outputs) | Yes |
| Cost regression CI gate (run baseline at half price) | Yes |
| Bulk document processing (500 contracts) | Yes |
| Prompt A/B testing against large datasets | Yes |
| Real-time user-facing responses | No — use standard `Agent.step()` |

## `BatchRequest`

```python
from meshflow.batch.anthropic_batch import BatchRequest

req = BatchRequest(
    custom_id="task-001",                         # stable ID for result correlation
    prompt="Analyse this contract for GDPR risks",
    model="claude-haiku-4-5-20251001",            # default model
    system="You are a legal analyst.",
    max_tokens=1024,
)

# Convert to Anthropic API format
api_dict = req.to_api_request()
```

## `BatchResult`

```python
from meshflow.batch.anthropic_batch import BatchResult

result: BatchResult
result.custom_id   # matches the submitted BatchRequest.custom_id
result.output      # model response text (empty string if errored)
result.tokens      # total tokens used (input + output)
result.cost_usd    # estimated cost (Anthropic applies 50% discount automatically)
result.error       # error message if this individual request failed
result.succeeded   # bool convenience property
result.to_dict()   # serialise to dict
```

## `BatchJob`

```python
from meshflow.batch.anthropic_batch import BatchJob

job: BatchJob
job.batch_id        # Anthropic-assigned batch ID
job.status          # "in_progress" | "ended" | "canceling" | "canceled"
job.request_counts  # {"processing": 10, "succeeded": 5, "errored": 0, ...}
job.is_complete     # True when status is "ended" or "canceled"
job.to_dict()       # serialise to dict
```

## `AnthropicBatchClient`

### Submit

```python
from meshflow.batch.anthropic_batch import AnthropicBatchClient, BatchRequest

client = AnthropicBatchClient(api_key="")   # defaults to ANTHROPIC_API_KEY env var

requests = [
    BatchRequest(custom_id=f"task-{i}", prompt=task, model="claude-haiku-4-5-20251001")
    for i, task in enumerate(my_tasks)
]
batch_id = await client.submit(requests)
print(f"Submitted batch {batch_id}")
```

### Poll Status

```python
job = await client.status(batch_id)
print(job.status, job.request_counts)
# "in_progress" {"processing": 50, "succeeded": 0, "errored": 0}
```

### Retrieve Results

```python
# Only call after job.is_complete is True
results = await client.results(batch_id)
```

### Wait for Completion

```python
results = await client.wait(
    batch_id,
    poll_interval=30.0,   # seconds between status checks
    timeout=86400.0,      # max wait (default 24 hours)
)
```

### One-Shot: Submit + Wait

```python
results = await client.run(
    requests,
    poll_interval=30.0,
    timeout=86400.0,
)
```

### Cancel

```python
accepted = await client.cancel(batch_id)   # returns True if accepted
```

## `batch_agent_tasks()` Convenience Wrapper

```python
from meshflow.batch.anthropic_batch import batch_agent_tasks

results = await batch_agent_tasks(
    tasks=["task 1", "task 2", "task 3"],
    agent_model="claude-haiku-4-5-20251001",
    system_prompt="You are a helpful assistant.",
    max_tokens=1024,
    api_key="",          # defaults to ANTHROPIC_API_KEY
    poll_interval=30.0,
)
# Returns list[BatchResult] in the same order as tasks
```

Each task is assigned a unique `custom_id` of the form `task-{i}-{random_hex}`.

## Cost Comparison

```python
# Standard: 1000 tasks × claude-haiku-4-5 at $0.0008/1k input tokens
standard_cost = 1000 * (500 / 1000) * 0.0008  # ≈ $0.40

# Batch API: same workload at 50% discount
batch_cost = standard_cost * 0.50              # ≈ $0.20
```

The discount is applied automatically by Anthropic — `BatchResult.cost_usd` reflects the discounted rate.
