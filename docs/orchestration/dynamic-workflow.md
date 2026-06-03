# DynamicWorkflow

`DynamicWorkflow` spawns additional agents at runtime based on keywords or patterns found in intermediate step output. A `DynamicCoordinator` watches each agent's output; when it matches a configured keyword, a new specialist agent is spawned automatically.

---

## Quick start

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import Agent, DynamicWorkflow
from meshflow.core.dynamic_workflow import DynamicCoordinator

wf = DynamicWorkflow(max_dynamic_nodes=5, mode="sandbox")

# Add the base agent
wf.add(Agent("base", role="researcher"))

# Coordinator spawns an "analyst" agent whenever "sub-topic" appears in output
wf.set_coordinator(DynamicCoordinator(
    spawn_keywords={"sub-topic": "analyst"},
    mode="sandbox",
))

result = wf.run("Research the EU AI Act and identify sub-topics.")
print(result.output)
print(f"Agents spawned: {result.total_spawns}")
print(f"Spawn history:  {result.spawn_history}")
```

---

## How it works

1. `wf.run(task)` passes the task to the base agent(s).
2. After each step, `DynamicCoordinator` scans the output for configured keywords.
3. When a keyword matches, a new `Agent` of the mapped role is instantiated and run on the matched fragment.
4. Spawned agents are also subject to the coordinator — allowing recursive spawning up to `max_dynamic_nodes`.
5. All results are aggregated into a `DynamicWorkflowResult`.

---

## DynamicWorkflow parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_dynamic_nodes` | `int` | `10` | Hard cap on total spawned agents |
| `mode` | `str` | `"production"` | `"sandbox"` for offline testing without LLM calls |

---

## DynamicCoordinator parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `spawn_keywords` | `dict[str, str]` | required | `{keyword: agent_role}` — spawn an agent of `role` when `keyword` appears in output |
| `max_spawns_per_node` | `int` | `3` | Maximum spawns triggered by a single agent's output |
| `mode` | `str` | `"production"` | `"sandbox"` to use `EchoProvider` in spawned agents |

---

## DynamicWorkflowResult

| Field | Type | Description |
|---|---|---|
| `output` | `str` | Aggregated final output |
| `total_spawns` | `int` | Number of agents spawned by the coordinator |
| `spawn_history` | `list` | Records of each spawn event |

---

## Async usage

```python
import asyncio
from meshflow import Agent, DynamicWorkflow
from meshflow.core.dynamic_workflow import DynamicCoordinator

async def main():
    wf = DynamicWorkflow(mode="sandbox")
    wf.add(Agent("base"))
    wf.set_coordinator(DynamicCoordinator(spawn_keywords={"topic": "analyst"}, mode="sandbox"))
    result = await wf.arun("Analyse this topic and each sub-topic.")
    print(result.output)

asyncio.run(main())
```

---

## Governance

Every spawned agent runs through `StepRuntime`. The same `policy=` and cost cap that apply to the base agent apply to all dynamically spawned agents.

---

## Exports

```python
from meshflow import DynamicWorkflow, DynamicWorkflowResult
from meshflow.core.dynamic_workflow import DynamicCoordinator, SpawnDecision
```
