# A2A Protocol

Agent-to-Agent (A2A) is MeshFlow's HTTP wire protocol for inter-agent communication with full task lifecycle management.

## AgentCard discovery

Every A2A server exposes `/.well-known/agent-card`:

```python
from meshflow import AgentCard, A2AServer

card = AgentCard(
    agent_id="researcher-01",
    name="Research Agent",
    description="Searches and summarizes information",
    capabilities=["web_search", "summarize"],
    endpoint="http://agent-host:9090",
)

server = A2AServer(agent=my_agent, port=9090, card=card)
server.start()   # daemon thread
```

## Send a task

```python
from meshflow import A2AClient, A2AMessage, A2AResponse

client = A2AClient(base_url="http://agent-host:9090")

# Discover capabilities
card = await client.get_card()
print(card.capabilities)

# Send task
response: A2AResponse = await client.send(
    task="Summarize the 2025 AI Safety Report",
    context={"format": "bullet_points"},
)
print(response.result)
print(response.cost_usd)
```

## Task lifecycle (SSE)

```python
from meshflow import A2ATask, A2ATaskStore, TaskState, TaskEventQueue

task_store = A2ATaskStore("tasks.db")
event_queue = TaskEventQueue()

# Create task
task = A2ATask(task_id="t-001", input="summarize report")
task_store.save(task)

# Subscribe to events (SSE)
async for event in event_queue.subscribe("t-001"):
    print(event.state, event.progress)
    if event.state == TaskState.COMPLETED:
        print(event.result)
        break
```

## TaskState machine

```
SUBMITTED → RUNNING → COMPLETED
                    ↘ FAILED
                    ↘ CANCELLED
           ↘ WAITING_FOR_HUMAN → RUNNING
```

## REST endpoints (auto-registered)

| Path | Description |
|------|-------------|
| `GET /.well-known/agent-card` | Agent capabilities discovery |
| `POST /tasks` | Submit a new task |
| `GET /tasks/{id}` | Get task status and result |
| `GET /tasks/{id}/events` | SSE task event stream |
| `DELETE /tasks/{id}` | Cancel a task |
