# Response Caching

`CachedProvider` wraps any `LLMProvider` with a transparent cache — repeated or near-duplicate prompts return instantly at zero cost.

```python
from meshflow import Agent
from meshflow.cache import SQLiteCache

agent = Agent(
    name="analyst",
    role="researcher",
    cache=SQLiteCache("meshflow_cache.db"),  # persists across restarts
)

# In-memory cache (per-process only):
agent = Agent(name="a", role="executor", cache=True)   # uses InMemoryCache
```

## Cache Backends

### `InMemoryCache`

Thread-safe LRU cache. Data is lost when the process exits.

```python
from meshflow.cache import InMemoryCache

cache = InMemoryCache(
    max_size=1000,               # LRU eviction when full
    similarity_threshold=0.95,  # cosine threshold for semantic fuzzy matching
    semantic=True,               # enable embedding-based near-duplicate lookup
)
cache.stats()
# {"backend": "in_memory", "size": 42, "hits": 100, "misses": 5, "hit_rate": 0.95}
```

### `SQLiteCache`

Persistent cache backed by SQLite. Survives restarts and is shared across worker processes.

```python
from meshflow.cache import SQLiteCache

cache = SQLiteCache(
    path="meshflow_cache.db",
    max_size=10_000,             # oldest rows evicted beyond this limit
    similarity_threshold=0.95,
    ttl_s=3600.0,                # expire entries after 1 hour (None = no expiry)
    semantic=True,
)
cache.stats()
# {"backend": "sqlite", "path": "...", "size": 500, "hit_rate": 0.87}
```

Use `path=":memory:"` in tests to get a persistent-connection in-process SQLite store without writing to disk.

## `LLMCache` Abstract Interface

Both backends implement `LLMCache`:

```python
cache.get(key)                       # exact-key lookup → CacheEntry | None
cache.put(entry)                     # store a CacheEntry
cache.get_semantic(model, system, messages)  # fuzzy lookup → CacheEntry | None
cache.invalidate(key)                # remove one entry
cache.clear()                        # remove all entries
cache.stats()                        # hit/miss statistics dict
```

## `CachedProvider`

`Agent(cache=...)` wraps the underlying provider automatically. To wrap a provider manually:

```python
from meshflow.agents.base import AnthropicProvider
from meshflow.cache import InMemoryCache
from meshflow.cache.provider import CachedProvider

provider = CachedProvider(
    provider=AnthropicProvider(),
    cache=InMemoryCache(),
)
```

On each `complete()` call `CachedProvider`:

1. Computes a deterministic SHA-256 key from `(model, system, messages)`.
2. Returns the cached response on an exact-key hit (cost reported as `$0.00`).
3. Falls back to a semantic (embedding cosine) lookup if exact miss.
4. Calls the underlying provider on cache miss, stores the result.

Streaming (`stream_complete`) bypasses the cache — chunks cannot be cheaply reassembled.

## Semantic Fuzzy Matching

When `semantic=True`, the cache embeds the last user message and finds the stored entry with the highest cosine similarity. If similarity `>= similarity_threshold` the stored response is returned instead of making an LLM call.

The embedding chain used is the same as `VectorStore`: sentence-transformers → numpy BoW → char n-gram (no extra dependencies required).

```python
# Lower threshold = more aggressive cache hits (but risk of wrong answers)
cache = SQLiteCache("c.db", similarity_threshold=0.90)
```

## Anthropic Prompt Caching (`cache_control`)

`AnthropicProvider` automatically applies Anthropic's server-side prompt caching when a `TokenBudgetTracker` is active. This is different from MeshFlow's response cache — it caches the KV computation of large system prompts and tool definitions on Anthropic's side.

```python
from meshflow.optimization.tracker import TokenBudgetTracker, active_tracker

with TokenBudgetTracker(max_tokens=100_000) as tracker:
    active_tracker.set(tracker)
    result = await agent.step("Summarise the contract")
    # System prompt and tool schemas are sent with cache_control: {"type": "ephemeral"}
    # Anthropic caches the KV state; subsequent calls pay 10% of the input token price
```

**Cost impact:**

- Cached input tokens cost approximately **10% of the normal input rate**
- Typical savings: **70–90% on system-prompt tokens** for agents that share a large system prompt across many calls
- Applies automatically to system prompts and the last tool schema in `complete_with_tools()`
- Requires `anthropic-beta: prompt-caching-2024-07-31` header (added automatically)
