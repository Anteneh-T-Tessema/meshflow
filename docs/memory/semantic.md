# Semantic Memory Store

`SemanticMemoryStore` is a SQLite-backed vector store that embeds every entry and retrieves the most similar ones by cosine similarity — replacing BM25-only keyword search with genuine semantic retrieval.

```python
from meshflow.intelligence.semantic_memory import SemanticMemoryStore

store = SemanticMemoryStore()
store.store("fact_1", "Paris is the capital of France", metadata={"source": "wiki"})
store.store("fact_2", "Berlin is the capital of Germany")
store.store("fact_3", "The Eiffel Tower is in Paris")

results = store.search("What city is the Eiffel Tower in?", k=2)
for r in results:
    print(r.key, f"score={r.score:.3f}", r.text)
# fact_3  score=0.912  The Eiffel Tower is in Paris
# fact_1  score=0.741  Paris is the capital of France
```

## Construction

```python
from meshflow.intelligence.semantic_memory import SemanticMemoryStore
from meshflow.intelligence.embedding import get_embedding_provider

store = SemanticMemoryStore(
    db_path="meshflow_memory.db",   # use ":memory:" for in-process testing
    provider=get_embedding_provider(),  # auto-select best available
    max_entries=10_000,             # evict oldest when exceeded (None = no limit)
)

print(store.provider_name)   # e.g. "SentenceTransformerProvider"
print(store.embedding_dim)   # e.g. 384
```

## `SemanticMemoryEntry`

```python
from meshflow.intelligence.semantic_memory import SemanticMemoryEntry

entry: SemanticMemoryEntry
entry.key        # str — unique identifier
entry.text       # str — stored text
entry.embedding  # list[float] — unit-length vector
entry.metadata   # dict[str, Any]
entry.stored_at  # float — Unix timestamp
```

## `SemanticSearchResult`

```python
from meshflow.intelligence.semantic_memory import SemanticSearchResult

result: SemanticSearchResult
result.key        # str
result.text       # str
result.score      # float — cosine similarity in [-1, 1]; higher is more similar
result.metadata   # dict[str, Any]
result.stored_at  # float
```

## Write Operations

### `store()`

```python
entry = store.store(
    key="mem_001",
    text="The indemnification clause requires 30 days written notice.",
    metadata={"contract": "ServiceAgreement.pdf", "section": 12},
)
# If key exists, the entry is replaced.
```

### `store_batch()`

```python
entries = store.store_batch(
    entries=[("k1", "text one"), ("k2", "text two"), ("k3", "text three")],
    metadata=[{"source": "a"}, {"source": "b"}, {"source": "c"}],
)
# Embeds all texts in one provider call — more efficient than repeated store()
```

## Read Operations

### `search()`

```python
results = store.search(
    query="indemnification notice period",
    k=5,
    min_score=0.5,   # filter out results below this cosine similarity
)
```

### `get()`

```python
entry = store.get("mem_001")   # exact key lookup → SemanticMemoryEntry | None
```

### `list()`

```python
entries = store.list(limit=100, offset=0)   # ordered newest-first
```

### `count()`

```python
total = store.count()   # int — total entries in the store
```

## Delete Operations

```python
found = store.delete("mem_001")   # bool — True if the key existed
deleted = store.clear()           # int — count of deleted entries
```

## `EmbeddingProvider`

```python
from meshflow.intelligence.embedding import (
    EmbeddingProvider,
    get_embedding_provider,
    cosine_similarity,
)

provider = get_embedding_provider()   # auto-select best available

# Embed texts
vecs = provider.embed(["hello world", "hi there"])   # list[list[float]]

# Cosine similarity between two unit vectors
sim = cosine_similarity(vecs[0], vecs[1])   # float in [-1, 1]
```

### Provider Hierarchy

| Provider | Class | Dependency | Dimension |
|----------|-------|------------|-----------|
| Sentence Transformers | `SentenceTransformerProvider` | `pip install sentence-transformers` | 384 |
| Hash n-gram | `HashEmbeddingProvider` | none | 256 |

`get_embedding_provider()` tries `SentenceTransformerProvider` first and falls back to `HashEmbeddingProvider` when `sentence-transformers` is not installed. All providers produce L2-normalised vectors so that cosine similarity equals the dot product.

### Custom Embedding Provider

```python
from meshflow.intelligence.embedding import EmbeddingProvider

class MyProvider(EmbeddingProvider):
    @property
    def dim(self) -> int:
        return 1536

    @property
    def name(self) -> str:
        return "my-custom-provider"

    def embed(self, texts: list[str]) -> list[list[float]]:
        # call your embedding API
        ...

store = SemanticMemoryStore(provider=MyProvider())
```
