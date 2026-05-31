# Streaming

Every MeshFlow streaming surface — `Agent`, `Team`, `GroupChat` — yields `StreamChunk` objects so callers handle tokens uniformly regardless of which layer produces them.

---

## Agent streaming

```python
async for token in agent.stream("Summarise the key findings"):
    print(token, end="", flush=True)
print()  # newline after stream ends
```

`Agent.stream()` is an async generator that yields raw token strings — not `StreamChunk` objects. Use it when you only need the text and don't care about agent lifecycle events.

---

## Team streaming

`Team.stream()` yields `StreamChunk` objects with lifecycle events for each agent:

```python
async for chunk in await team.stream("Draft a product roadmap"):
    if chunk.kind == "node_start":
        print(f"\n[{chunk.node_name}] starting...")
    elif chunk.is_token:
        print(chunk.content, end="", flush=True)
    elif chunk.kind == "node_end":
        print(f"\n  ({chunk.node_name} done)")
    elif chunk.kind == "done":
        print("\n\nAll agents finished.")
```

For `parallel` teams, token chunks from different agents are interleaved in arrival order.

---

## StreamChunk fields

| Field | Type | Description |
|---|---|---|
| `kind` | `str` | Event type — see table below |
| `content` | `str` | Token text (`kind="token"`) or full output (`kind="node_end"`, `"task_end"`) |
| `node_name` | `str` | Name of the agent or node producing this chunk |
| `task_index` | `int` | Zero-based task index (Crew streaming only) |
| `metadata` | `dict` | Extra data — e.g. `tokens`, `cost_usd`, state snapshots |

### Kind values

| `kind` | When it fires |
|---|---|
| `"token"` | A piece of generated text is available |
| `"node_start"` | An agent/node has started processing |
| `"node_end"` | An agent/node finished; `content` has its full output |
| `"task_start"` | A Crew task has started |
| `"task_end"` | A Crew task finished; `content` has the full output |
| `"done"` | Stream complete — no more chunks will arrive |
| `"error"` | An error occurred; `content` contains the error message |

### Convenience properties

```python
chunk.is_token   # bool — True when kind == "token"
chunk.is_done    # bool — True when kind == "done"
```

---

## Collecting a full response from a Team stream

```python
outputs: dict[str, str] = {}
current_agent = ""

async for chunk in await team.stream("Analyse Q3 performance"):
    if chunk.kind == "node_start":
        current_agent = chunk.node_name
        outputs[current_agent] = ""
    elif chunk.is_token:
        outputs[current_agent] += chunk.content
    elif chunk.kind == "done":
        break

# outputs = {"planner": "...", "researcher": "...", "writer": "..."}
```

---

## GroupChat streaming

`GroupChatManager.stream()` yields `ChatMessage` objects (not `StreamChunk`):

```python
async for msg in manager.stream("Design a caching layer"):
    print(f"[{msg.sender}]: {msg.content[:80]}...")
```

Each `ChatMessage` arrives after its agent finishes — GroupChat streaming is message-level, not token-level.

---

## Parallel team streams

For `pattern="parallel"` teams, all agents stream concurrently. Chunks from different agents interleave in arrival order:

```python
team = Team(
    agents=[entry, analyst_a, analyst_b, synthesizer],
    pattern="parallel",
)

async for chunk in await team.stream("Evaluate three investment strategies"):
    if chunk.kind == "node_start":
        print(f"\n>>> {chunk.node_name} started")
    elif chunk.is_token:
        print(chunk.content, end="", flush=True)
```

The internal implementation uses `asyncio.Queue` — each agent runs as a concurrent task and posts chunks into the queue, which the caller drains in order.

---

## Error handling during streaming

```python
async for chunk in await team.stream("..."):
    if chunk.kind == "error":
        print(f"Error from {chunk.node_name}: {chunk.content}")
        break
    elif chunk.is_token:
        print(chunk.content, end="", flush=True)
```

---

## Streaming with async context managers

For production use, wrap the stream in a try/finally to ensure cleanup:

```python
async def run_with_stream(agent, task):
    tokens = []
    try:
        async for token in agent.stream(task):
            tokens.append(token)
            yield token
    except Exception as e:
        yield f"\n[stream error: {e}]"
    finally:
        full_output = "".join(tokens)
        # log or save full_output
```

---

## Crew streaming (task-level)

When using `Crew`, stream task completions one at a time:

```python
async for chunk in crew.kickoff_stream(inputs={"topic": "AI safety"}):
    if chunk.kind == "task_start":
        print(f"\n--- Task {chunk.task_index + 1}: {chunk.node_name} ---")
    elif chunk.is_token:
        print(chunk.content, end="", flush=True)
    elif chunk.kind == "task_end":
        tokens = chunk.metadata.get("tokens", "?")
        print(f"\n  [{tokens} tokens]")
```

!!! tip
    Use `chunk.is_token` and `chunk.is_done` as shorthand checks instead of comparing `chunk.kind` directly. They read more clearly in tight streaming loops.
