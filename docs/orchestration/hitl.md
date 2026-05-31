# Human-in-the-Loop

MeshFlow's HITL system pauses workflow execution at designated nodes, persists a checkpoint, and resumes cleanly after a human provides a decision — across process restarts and arbitrary time delays.

```python
from meshflow.core.state import StateGraph, node, interrupt, Command

@node
def review_node(state: dict) -> dict:
    if state.get("confidence", 1.0) < 0.8:
        interrupt("Please review the draft and approve or reject.")
    return {"approved": True}
```

## `HumanInLoopConfig` in `WorkflowDefinition`

Configure HITL at the policy level so it applies automatically to any node whose risk tier meets the threshold:

```python
from meshflow.core.schemas import HumanInLoopConfig, RiskTier, Policy

policy = Policy(
    human_in_loop=HumanInLoopConfig(
        enabled          = True,
        tier_threshold   = RiskTier.IRREVERSIBLE,  # pause on irreversible nodes
        timeout_s        = 86400.0,                # 24-hour durable pause
        approval_webhook = "https://hooks.example.com/meshflow",
    )
)
```

Or in YAML:

```yaml
policy:
  mode: regulated
  budget_usd: 2.00
  human_approval_tier: irreversible   # none | read_only | internal | external_io | irreversible

nodes:
  approval:
    kind: human   # always pauses; never needs a risk tier
```

## `interrupt()` in `StateGraph`

Call `interrupt(value)` inside any node to pause graph execution. The value is surfaced to the caller as `InterruptedError.value`.

```python
from meshflow.core.state import StateGraph, node, interrupt, Command

@node
async def human_review(state: dict) -> dict:
    if state.get("needs_review"):
        interrupt({
            "prompt": "Review the draft and approve or reject.",
            "draft":  state.get("draft", ""),
        })
    return {"approved": True}
```

### Resuming with `Command`

```python
compiled = graph.compile(checkpointer=saver)

# First run — raises InterruptedError at "human_review"
try:
    result = await compiled.run({"query": "...", "needs_review": True})
except InterruptedError as exc:
    print("Paused at:", exc.node)          # "human_review"
    print("Payload:",   exc.value)         # {"prompt": "...", "draft": "..."}
    saved_state = exc.state

# Human reviews and decides; then resume:
result = await compiled.run(
    saved_state,
    resume=Command(
        resume = "approved",               # replaces the interrupt payload
        goto   = None,                     # None = continue from interrupted node
        update = {"approved": True},       # extra state updates to apply first
    ),
)
```

### `Command` Fields

| Field | Type | Description |
|-------|------|-------------|
| `resume` | `Any` | Value that replaces the interrupt payload |
| `goto` | `str \| None` | Jump to a specific node instead of the interrupted one |
| `update` | `dict` | Extra state updates merged before resuming |

## HITL in `WorkflowDefinition`

For `WorkflowDefinition`-based workflows, use `kind: human` nodes or rely on automatic tier-based pausing:

```yaml
name: contract_review
version: "1"

policy:
  mode: regulated
  budget_usd: 1.00
  human_approval_tier: irreversible

nodes:
  drafter:
    kind: native
    role: executor
    risk: read_only

  legal_approval:
    kind: human           # always pauses here

  publisher:
    kind: native
    role: executor
    risk: irreversible    # pauses automatically due to human_approval_tier

edges:
  - drafter -> legal_approval
  - legal_approval -> publisher
```

#### Resuming a `WorkflowDefinition` Run

```python
from meshflow.core.workflow import WorkflowDefinition, HumanDecision
from meshflow.core.ledger import ReplayLedger

wf     = WorkflowDefinition.from_yaml("contract_review.yaml")
result = await mesh.run_workflow(wf, task="Review NDA v3", ledger_db="runs.db")
# result.paused_nodes == ["legal_approval"]

decision = HumanDecision(approved=True, comment="LGTM", decided_by="jane.doe@example.com")
ledger   = ReplayLedger("runs.db")
runtime  = mesh._make_runtime(run_id=result.run_id)
final    = await wf.resume(run_id=result.run_id, decision=decision, ledger=ledger, runtime=runtime)
assert final.completed is True
```

## Webhook Notifications for Pending Approvals

When `approval_webhook` is set in `HumanInLoopConfig`, MeshFlow POSTs a JSON payload to that URL when any node pauses for human input:

```json
{
  "run_id":     "abc-123",
  "node_id":    "legal_approval",
  "workflow":   "contract_review",
  "payload":    {"prompt": "Review NDA v3", "confidence": 0.72},
  "approve_url": "https://your-app.com/meshflow/approve?run_id=abc-123",
  "reject_url":  "https://your-app.com/meshflow/reject?run_id=abc-123"
}
```

## `meshflow approve` CLI

Approve or reject a paused run without writing code:

```bash
# Approve
meshflow approve --run-id abc-123 --db runs.db --comment "LGTM"

# Reject
meshflow approve --run-id abc-123 --db runs.db --approved false --comment "Needs revision"

# List all paused runs
meshflow approve list --db runs.db
```

## Full HITL Workflow Example

```python
import asyncio
from typing import Annotated, TypedDict
from meshflow.core.state import (
    StateGraph, END, node, interrupt, Command, last, SqliteSaver
)

class ContractState(TypedDict):
    contract_text: str
    summary:       Annotated[str, last]
    approved:      Annotated[bool, last]
    final_text:    Annotated[str, last]

@node
async def summarize(state: dict) -> dict:
    text = state["contract_text"]
    return {"summary": f"Summary of {len(text)}-char contract: ..."}

@node
async def legal_review(state: dict) -> dict:
    # Always require human review for contracts
    interrupt({
        "action":  "Please review and approve or reject this summary.",
        "summary": state.get("summary", ""),
    })
    # Execution resumes here after Command(resume=...) is passed
    return {}

@node
async def finalize(state: dict) -> dict:
    if not state.get("approved", False):
        return {"final_text": "[REJECTED]"}
    return {"final_text": f"APPROVED: {state['summary']}"}

def route_after_review(state: dict) -> str:
    return "finalize"    # always continue; finalize reads state["approved"]

graph = StateGraph(ContractState)
graph.add_node("summarize",    summarize)
graph.add_node("legal_review", legal_review)
graph.add_node("finalize",     finalize)
graph.add_edge("summarize",    "legal_review")
graph.add_conditional_edges("legal_review", route_after_review, {"finalize": "finalize"})
graph.add_edge("finalize",     END)
graph.set_entry_point("summarize")

saver    = SqliteSaver("contracts.db")
compiled = graph.compile(checkpointer=saver)

initial = {
    "contract_text": "This agreement is between Party A and Party B...",
    "summary":       "",
    "approved":      False,
    "final_text":    "",
}

# --- First run: pauses at legal_review ---
try:
    result = await compiled.run(initial, config={"thread_id": "contract-001"})
except InterruptedError as exc:
    print("Paused:", exc.value["action"])
    paused_state = exc.state

    # Simulate human approval
    result = await compiled.run(
        paused_state,
        config  = {"thread_id": "contract-001"},
        resume  = Command(resume="approved", update={"approved": True}),
    )
    print(result["final_text"])   # "APPROVED: Summary of ..."
```
