# Tool Streaming

MeshFlow exposes a granular `ToolStreamEvent` hierarchy so you can observe tool call lifecycle events — input construction, execution start, partial results, and completion — as they happen in real time.

---

## Quick start

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
import asyncio
from meshflow import Agent
from meshflow.streaming.tool_stream import stream_tool_calls, ToolStreamEvent

agent = Agent(name="assistant", role="executor")

async def main():
    async for event in stream_tool_calls(agent, "List the top 3 EU AI Act obligations"):
        print(type(event).__name__, end=" ")  # TextDeltaEvent, ToolCallStartEvent, etc.

asyncio.run(main())
```

---

## ToolStreamEvent hierarchy

All events inherit from `ToolStreamEvent` and carry a `type` discriminator field.

| Event type | Fields | When emitted |
|---|---|---|
| `tool_input_delta` | `tool_use_id`, `partial_json` | As the model streams JSON input for a tool call |
| `tool_start` | `tool_name`, `tool_use_id`, `input` | When a tool call is fully parsed and about to execute |
| `tool_result` | `tool_name`, `tool_use_id`, `output`, `error` | When the tool returns (success or error) |
| `text_delta` | `text` | Streaming text tokens from the model |
| `message_stop` | — | Stream complete |

---

## `stream_tool_calls`

```python
async for event in stream_tool_calls(
    agent,
    task,
    emit_text_deltas=True,   # include TextDeltaEvent in stream (default False)
):
    ...
```

---

## SSE integration (FastAPI)

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from meshflow.streaming.tool_stream import stream_tool_calls
from meshflow import Agent

app = FastAPI()
agent = Agent(name="assistant", role="executor")

@app.get("/stream")
async def stream(task: str):
    return StreamingResponse(
        tool_events_to_sse(stream_tool_calls(agent, task)),
        media_type="text/event-stream",
    )
```

Each SSE event is a JSON-serialised `ToolStreamEvent`.

---

## Governance

Tool streaming does not bypass governance. Every tool call still passes through `StepRuntime` — policy gates and cost tracking fire synchronously before the tool executes, even during streaming.

---

## Exports

```python
from meshflow.streaming.tool_stream import (
    stream_tool_calls,
    ToolStreamEvent,
    ToolCallStartEvent,
    ToolCallEndEvent,
    TextDeltaEvent,
    ToolResultEndEvent,
    ToolStreamResult,
)
```
