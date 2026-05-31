# Flows

`Flow` is MeshFlow's event-driven, state-machine workflow model — describe your pipeline as a class whose methods fire in response to each other, rather than as a YAML DAG.

```python
from meshflow.core.flows import Flow, FlowState, start, listen, router

class ReportState(FlowState):
    topic:    str  = ""
    research: str  = ""
    approved: bool = False

class ReportFlow(Flow[ReportState]):
    @start()
    async def plan(self):
        self.state.topic = "AI governance"
        return "planned"

    @listen("plan")
    async def research(self, _):
        self.state.research = f"Research on {self.state.topic}"
        return self.state.research

    @listen("research")
    async def finalize(self, text: str):
        self.state.approved = True
        return f"Report: {text}"

flow   = ReportFlow()
result = await flow.kickoff()
print(flow.state.approved)   # True
```

## `FlowState`

Shared mutable state for a `Flow`. Subclass to add typed fields with defaults:

```python
from meshflow.core.flows import FlowState

class AnalysisState(FlowState):
    query:    str       = ""
    sources:  list[str] = []
    summary:  str       = ""
    score:    float     = 0.0

# Access inside any handler via self.state
class MyFlow(Flow[AnalysisState]):
    @start()
    def init(self):
        self.state.query = "climate risk"
        return self.state.query
```

`FlowState` methods:

| Method | Description |
|--------|-------------|
| `state.update(**kwargs)` | Merge keyword args into state in-place |
| `state.to_dict()` | Snapshot all public fields as a dict |

## Decorators

### `@start()`

Marks one or more entry-point methods. All `@start()` methods run when `kickoff()` is called.

```python
@start()
async def initialize(self):
    self.state.query = "..."
    return "init done"
```

### `@listen(trigger)`

Fires after `trigger` completes. The return value of `trigger` is passed as the first argument.

```python
# By name string
@listen("initialize")
async def fetch(self, prev_result: str): ...

# By method reference (resolved at class scan time)
@listen(initialize)
async def fetch(self, prev_result: str): ...

# Routed: only fires when the router returned "approve"
@listen(("validate", "approve"))
async def publish(self, text: str): ...
```

### `@router(trigger)`

Reads the output of `trigger` and returns a route string. Downstream `@listen((trigger, route))` handlers whose route matches are then enqueued.

```python
@router("validate")
def route_after_validate(self, result: str) -> str:
    return "approve" if len(result) > 20 else "skip"

@listen(("validate", "approve"))
async def publish(self, text: str):
    self.state.summary = text

@listen(("validate", "skip"))
async def log_skip(self, text: str):
    print("Skipped:", text[:40])
```

## Execution Model

`kickoff()` runs a BFS queue:

1. All `@start()` methods are enqueued simultaneously.
2. When a method completes, its return value is passed to all registered `@listen` handlers for that method name.
3. If a `@router` is registered for the completed method, it runs first and filters which `@listen((method, route))` handlers are enqueued.
4. Execution ends when the queue is empty or `max_steps` is reached.

## `kickoff(inputs=None)` and `kickoff_sync`

```python
# Async (preferred)
result = await flow.kickoff(inputs={"topic": "GDPR"})

# Sync wrapper
result = flow.kickoff_sync(inputs={"topic": "HIPAA"})

print(result.final_output)       # return value of the last method executed
print(result.state.to_dict())    # full final state
print(result.steps_executed)     # ["plan", "research", "finalize"]
print(result.duration_s)         # wall-clock seconds
```

### `FlowResult` Fields

| Field | Type | Description |
|-------|------|-------------|
| `final_output` | `Any` | Return value of the last handler executed |
| `state` | `FlowState` | Final flow state |
| `steps_executed` | `list[str]` | Ordered list of method names that ran |
| `total_tokens` | `int` | Aggregated tokens (when governance is attached) |
| `total_cost_usd` | `float` | Aggregated spend |
| `duration_s` | `float` | Wall-clock seconds |
| `error` | `str` | Non-empty if execution terminated with an error |

## Introspection

```python
print(flow.describe())   # topology as dict
print(flow.plot())       # Mermaid diagram string
```

## Full Example — Multi-Step Flow with Routing

```python
import asyncio
from meshflow.core.flows import Flow, FlowState, start, listen, router

class PipelineState(FlowState):
    topic:     str       = ""
    sources:   list[str] = []
    draft:     str       = ""
    published: bool      = False

class ContentFlow(Flow[PipelineState]):

    @start()
    async def gather_sources(self):
        self.state.sources = [
            f"https://example.com/article-{i}" for i in range(5)
        ]
        return f"Gathered {len(self.state.sources)} sources"

    @listen("gather_sources")
    async def write_draft(self, _):
        self.state.draft = (
            f"Draft about '{self.state.topic}' "
            f"citing {len(self.state.sources)} sources."
        )
        return self.state.draft

    @router("write_draft")
    def quality_gate(self, draft: str) -> str:
        # Route to "publish" if draft is long enough, else "revise"
        return "publish" if len(draft) > 30 else "revise"

    @listen(("write_draft", "revise"))
    async def revise(self, draft: str):
        self.state.draft = draft + " [REVISED]"
        return self.state.draft

    @listen(("write_draft", "publish"))
    async def publish(self, draft: str):
        self.state.published = True
        return f"PUBLISHED: {draft}"

    # Fires regardless of route — always runs after write_draft
    @listen("write_draft")
    async def log_draft(self, draft: str):
        print(f"[log] Draft length: {len(draft)}")


flow   = ContentFlow(state=PipelineState(topic="climate risk"))
result = asyncio.run(flow.kickoff())

print("Steps:", result.steps_executed)
print("Published:", flow.state.published)
print("Output:", result.final_output)
```

## Initialising with Pre-Set State

```python
flow   = ContentFlow(state=PipelineState(topic="HIPAA"))      # pre-set at construction
result = await flow.kickoff(inputs={"topic": "GDPR"})         # or override at kickoff
```
