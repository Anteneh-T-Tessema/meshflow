# State Graphs

`StateGraph` is MeshFlow's typed, reducer-aware workflow graph — a parallel execution engine with built-in governance, HITL checkpointing, and SHA-256 audit trails.

```python
from typing import Annotated
from meshflow.core.state import StateGraph, END, node, add, last, MemorySaver

class ResearchState(dict):
    query:   str
    sources: Annotated[list[str], add]   # branches append, reducer merges
    draft:   Annotated[str, last]        # last writer wins

@node
def search(state: dict) -> dict:
    return {"sources": ["https://arxiv.org/abs/2401.00001"]}

@node("generate")          # custom node name
def draft_fn(state: dict) -> dict:
    return {"draft": f"Draft based on {state.get('sources', [])}"}

def route(state: dict) -> str:
    return "done" if len(state.get("draft", "")) > 50 else "revise"

graph = StateGraph(ResearchState)
graph.add_node("search",   search)
graph.add_node("generate", draft_fn)
graph.add_edge("search", "generate")
graph.add_conditional_edges("generate", route, {"revise": "generate", "done": END})
graph.set_entry_point("search")

result = await graph.run({"query": "What is RAG?"})
```

## Graph Construction

### `StateGraph(state_schema)`

Pass a `TypedDict` or plain `dict` subclass. Fields annotated with `Annotated[T, reducer]` get typed channels; plain fields default to `last` (last-writer-wins).

```python
import operator
from typing import Annotated, TypedDict
from meshflow.core.state import add, last, first

class PipelineState(TypedDict):
    query:    str                            # last-writer-wins (default)
    results:  Annotated[list[str], add]      # list accumulator across branches
    winner:   Annotated[str, first]          # first writer wins; ignores later updates
    tokens:   Annotated[int, operator.add]  # sum across all nodes
```

### Built-in Reducers

| Reducer | Behaviour |
|---------|-----------|
| `add` | Appends lists; coerces scalars to single-element list |
| `last` | Last writer wins (default) |
| `first` | First writer wins; ignores subsequent updates |
| `operator.add` | Numeric accumulation |

### `add_node(name, fn)`

`fn` may be an async function, a sync function (auto-wrapped), or a `CompiledGraph` subgraph.

```python
graph.add_node("search",  search_fn)
graph.add_node("subflow", compiled_subgraph)   # subgraph nesting
```

### `add_edge(src, dst)` / `add_sequence`

```python
graph.add_edge("search", "draft")             # unconditional
graph.add_edge("draft", END)                   # marks draft as terminal

# Convenience: register + chain multiple nodes at once
graph.add_sequence([
    ("fetch",     fetch_fn),
    ("parse",     parse_fn),
    ("summarize", summarize_fn),
])
```

### `add_conditional_edges(src, condition_fn, mapping)`

`condition_fn(state_dict) -> str`; the returned string is looked up in `mapping`. Use `Send` for fan-out.

```python
from meshflow.core.state import Send

async def fan_out(state: dict) -> list[Send]:
    return [Send("process_item", {"item": x}) for x in state["items"]]

graph.add_conditional_edges("split", fan_out)   # no mapping needed
```

## `@node` Decorator

```python
from meshflow.core.state import node

@node
def plain(state: dict) -> dict:          # name = "plain"
    return {"result": "..."}

@node("my_name")                          # explicit node name
def impl(state: dict) -> dict:
    return {"result": "..."}
```

## Compile and Execute

```python
saver    = MemorySaver()
compiled = graph.compile(checkpointer=saver)

# Single run with thread ID (enables checkpointing)
result = await compiled.run(
    {"query": "AI governance"},
    config={"thread_id": "session-42"},
)

# Inspect saved state
state = compiled.get_state({"thread_id": "session-42"})

# Merge external updates into saved state
compiled.update_state({"thread_id": "session-42"}, {"approved": True})
```

### `stream(initial)` — async generator

Yields `(node_name, state_snapshot)` after each step.

```python
async for node_name, snapshot in compiled.stream({"query": "RAG"}):
    print(f"{node_name}: {list(snapshot.keys())}")
```

## Checkpointing

### `MemorySaver`

In-process only — useful for testing and short-lived sessions.

```python
from meshflow.core.state import MemorySaver

saver = MemorySaver()
graph = my_graph.compile(checkpointer=saver)
saver.list_threads()        # ["session-1", "session-2"]
saver.delete("session-1")
```

### `SqliteSaver`

Persists across process restarts. State values must be JSON-serialisable.

```python
from meshflow.core.state import SqliteSaver

saver = SqliteSaver("checkpoints.db")       # file-backed
saver = SqliteSaver(":memory:")             # in-memory SQLite (tests)
graph = my_graph.compile(checkpointer=saver)
```

## Full Multi-Step Example

```python
import asyncio
import operator
from typing import Annotated, TypedDict
from meshflow.core.state import StateGraph, END, node, add, last, SqliteSaver

class ReportState(TypedDict):
    topic:    str
    sources:  Annotated[list[str], add]
    analysis: Annotated[str, last]
    approved: Annotated[bool, last]

@node
async def gather(state: dict) -> dict:
    return {"sources": [f"src-{i}" for i in range(3)]}

@node
async def analyze(state: dict) -> dict:
    return {"analysis": f"Analysis of {state['topic']} using {len(state['sources'])} sources"}

@node
async def review(state: dict) -> dict:
    # Automatically approved when confidence is high
    return {"approved": len(state["analysis"]) > 20}

def should_publish(state: dict) -> str:
    return "publish" if state.get("approved") else "revise"

@node
async def publish(state: dict) -> dict:
    print("Published:", state["analysis"][:60])
    return {}

graph = StateGraph(ReportState)
graph.add_node("gather",  gather)
graph.add_node("analyze", analyze)
graph.add_node("review",  review)
graph.add_node("publish", publish)
graph.add_edge("gather",  "analyze")
graph.add_edge("analyze", "review")
graph.add_conditional_edges("review", should_publish, {"publish": "publish", "revise": "analyze"})
graph.add_edge("publish", END)
graph.set_entry_point("gather")

saver  = SqliteSaver("reports.db")
app    = graph.compile(checkpointer=saver)
result = asyncio.run(app.run(
    {"topic": "AI governance", "sources": [], "analysis": "", "approved": False},
    config={"thread_id": "report-001"},
))
print(result["analysis"])
```
