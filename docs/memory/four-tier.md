# Four-Tier Agent Memory

`AgentMemory` gives every agent a structured, retrievable memory that spans a single session — from immediate working context down to long-term procedural records.

```python
from meshflow.intelligence.memory import AgentMemory

mem = AgentMemory(agent_id="researcher", max_working=10)

mem.add("HIPAA §164.502 covers minimum-necessary disclosures.")
mem.add("Treatment purpose is a TPO exception — no authorization required.")

relevant = mem.recall("What are the HIPAA exceptions for treatment?", top_k=2)
# ["Treatment purpose is a TPO exception...", "HIPAA §164.502..."]

ctx = mem.context_string()   # formatted string ready for LLM prompt injection
```

## The Four Tiers

| Tier | Name | Capacity | Retrieval |
|------|------|----------|-----------|
| 1 | Working | last N items (default 10) | always in context |
| 2 | Episodic | compressed summaries (default 50) | demoted from working |
| 3 | Semantic | all content ever added | BM25 index search |
| 4 | Procedural | verifier / outcome records | append-only ledger |

**Promotion flow:** When working memory reaches `max_working`, the oldest entry is demoted to episodic. Episodic entries are kept until `max_episodic` is reached, at which point the oldest are dropped. Every entry added to working or episodic is also indexed in the BM25 semantic store.

## Core API

### `add()`

```python
mem.add("The client prefers bullet-point summaries.")
mem.add("Error: rate limit hit on attempt 3.", tier_hint="procedural")
mem.add("Step completed successfully.", metadata={"node": "executor-1"})
```

`tier_hint` defaults to `"working"`. Use `"procedural"` to write directly to Tier 4.

### `recall()`

```python
results = mem.recall("HIPAA treatment exceptions", top_k=3)
# list[str] — BM25-scored, with a +0.5 recency bonus for working-memory hits
```

### `recent()`

```python
last_5 = mem.recent(n=5)   # list[str] — newest working-memory entries first
```

### `context_string()`

```python
ctx = mem.context_string(max_chars=800, query="contract liability")
# Formats working-memory entries as "[step N] content"
# When query is provided, relevant entity facts are appended under [Entities]
```

### `record_outcome()`

```python
mem.record_outcome(
    node_id="executor-1",
    success=True,
    confidence=0.92,
    verifier_score=0.88,
)
# Writes to Tier 4 (procedural); visible in mem.stats()["procedural"]
```

## Snapshot / Restore

```python
snapshot = mem.to_snapshot()         # dict — all tiers serialised
mem2 = AgentMemory(agent_id="researcher")
mem2.from_snapshot(snapshot)         # restore from dict
```

Internally delegates to `snapshot_from_memory()` and `restore_memory()` from `meshflow.intelligence.memory_backends`.

## Auto-Consolidation

When the total character footprint of all tiers exceeds `consolidate_at_chars` (default 20,000), the lower-importance half of episodic memory is pruned automatically. Importance score = recency × 0.6 + content length × 0.4.

```python
mem = AgentMemory(
    auto_consolidate=True,
    consolidate_at_chars=20_000,
)

# Trigger manually at any time
dropped = mem.consolidate()   # returns the number of episodic entries dropped
```

## Stats

```python
print(mem.stats())
# {
#   "agent_id": "researcher",
#   "working": 8,
#   "episodic": 12,
#   "procedural": 3,
#   "semantic_index_size": 23,
#   "steps": 23
# }
```

## Memory on `Agent`

Enable memory on an agent with `memory=True`:

```python
from meshflow import Agent

agent = Agent(
    name="researcher",
    role="researcher",
    memory=True,                        # uses InMemoryBackend
    memory_session_id="session-abc",    # optional session namespace
)
```

### Memory Backends

| Backend | Import | Persistence |
|---------|--------|-------------|
| `InMemoryBackend` | `meshflow.intelligence.memory_backends` | per-process |
| `SQLiteMemoryBackend` | `meshflow.intelligence.memory_backends` | file on disk |
| `PostgresMemoryBackend` | `meshflow.intelligence.memory_backends` | PostgreSQL |

```python
from meshflow.intelligence.memory_backends import SQLiteMemoryBackend

agent = Agent(
    name="researcher",
    role="researcher",
    memory=True,
    memory_backend=SQLiteMemoryBackend(path="agent_memory.db"),
)
```
