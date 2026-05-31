# Cross-Session Memory

`CrossSessionMemoryStore` persists memories across separate agent sessions and process restarts.

```python
from meshflow import CrossSessionMemoryStore, CrossSessionEntry

store = CrossSessionMemoryStore("cross_session.db")

# Store a memory from session A
store.save("user-alice", "Prefers JSON output over prose", tags=["preference"])

# Retrieve in session B (or a week later)
entries: list[CrossSessionEntry] = store.retrieve("user-alice", query="output format")
for e in entries:
    print(e.content, e.tags, e.created_at)

# Inject into an agent at session start
agent = Agent(name="assistant", role="executor")
session = AgentSession(agent, system_context=store.context_string("user-alice"))
```

## CrossSessionEntry fields

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Namespace key (e.g. user ID, tenant ID) |
| `content` | `str` | Memory text |
| `tags` | `list[str]` | Optional tags for filtering |
| `created_at` | `str` | ISO 8601 timestamp |
| `score` | `float` | Relevance score when returned from semantic search |

## Methods

```python
store.save(key, content, tags=[])           # store a memory
store.retrieve(key, query="", top_k=5)      # semantic search over memories
store.list(key)                             # all entries for a key
store.delete(key, entry_id)                 # remove one entry
store.clear(key)                            # remove all entries for a key
store.context_string(key, max_chars=1000)   # formatted string for system_context=
```

## With AgentSession

```python
from meshflow import CrossSessionMemoryStore, Agent
from meshflow.agents.session import AgentSession

store = CrossSessionMemoryStore("memories.db")
agent = Agent(name="assistant", role="executor")

async def chat_with_memory(user_id: str, message: str) -> str:
    ctx = store.context_string(user_id)
    session = AgentSession(agent, system_context=ctx)
    result = await session.chat(message)
    # Save anything worth remembering
    if "remember" in message.lower():
        store.save(user_id, result.reply[:200], tags=["user-stated"])
    return result.reply
```
