# Retrieval-Augmented Generation (RAG)

MeshFlow's knowledge system gives any agent document awareness with no extra setup — embeddings degrade gracefully from sentence-transformers to pure-Python char n-grams.

```python
from meshflow import Agent

# Option A — pass file paths or text snippets directly
agent = Agent(
    name="analyst",
    role="researcher",
    knowledge=["report.pdf", "overview.txt", "https://docs.example.com"],
)

# Option B — build a shared VectorStore
from meshflow.intelligence.knowledge import VectorStore
store = VectorStore.from_texts([
    "MeshFlow is a governed multi-agent orchestration framework.",
    "It supports HIPAA, SOX, GDPR compliance out of the box.",
])
agent = Agent(name="analyst", role="researcher", knowledge=[store])
```

## `VectorStore`

### Construction

```python
from meshflow.intelligence.knowledge import VectorStore

# From a list of strings
store = VectorStore.from_texts(["chunk 1", "chunk 2", "chunk 3"])

# From a single file (auto-chunked)
store = VectorStore.from_file("legal_brief.pdf", chunk_size=500, overlap=50)

# From a directory (recursive, default extensions: .txt .md .py .json .yaml .yml .csv)
store = VectorStore.from_directory("docs/", extensions=[".md", ".txt"], chunk_size=500)

# Incremental
store = VectorStore()
store.add_texts(["additional chunk"])
```

PDF loading requires `pip install pypdf`. Other formats are plain UTF-8 text.

### Query

```python
results = store.query("HIPAA minimum necessary standard", top_k=3)
# list[str] — most relevant chunks by cosine similarity
```

## `KnowledgeSource`

Fine-grained control over retrieval parameters per source.

```python
from meshflow.intelligence.knowledge import KnowledgeSource

ks = KnowledgeSource(
    source="legal_docs/",   # file path, directory, URL, raw text, or VectorStore
    chunk_size=300,
    overlap=30,
    top_k=5,
)
results = ks.retrieve("indemnification clauses")
```

`source` can be:

- A file path (`.txt`, `.md`, `.py`, `.json`, `.yaml`, `.csv`, `.pdf`)
- A directory path (all matching files loaded recursively)
- A raw text string (chunked and indexed in-memory)
- A `VectorStore` instance (used directly)

## `AgentKnowledge`

Aggregates multiple sources and retrieves across all of them.

```python
from meshflow.intelligence.knowledge import AgentKnowledge, KnowledgeSource, VectorStore

knowledge = AgentKnowledge(
    sources=[
        "contracts/",                                      # directory
        KnowledgeSource(source="policy.md", top_k=3),     # single file
        VectorStore.from_texts(["raw fact 1", "raw fact 2"]),
    ],
    top_k=5,   # total chunks returned across all sources
)

chunks = knowledge.retrieve("Force majeure clauses")
context = knowledge.context_string("Force majeure", max_chars=2000)
```

### `context_blocks_cached()`

Returns Anthropic `cache_control` message blocks for prompt caching:

```python
blocks = knowledge.context_blocks_cached("HIPAA PHI handling", max_chars=2000)
messages = [
    {"role": "user", "content": [
        *blocks,
        {"type": "text", "text": f"Task: {task}"},
    ]}
]
```

Each chunk becomes a `{"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}` block, enabling Anthropic server-side caching of frequently-used knowledge.

## Embedding Chain

The embedding backend is selected automatically (no configuration needed):

| Priority | Provider | Dependency | Quality |
|----------|----------|------------|---------|
| 1 | `sentence-transformers` (`all-MiniLM-L6-v2`) | `pip install sentence-transformers` | High |
| 2 | numpy bag-of-words (1024-dim) | `pip install numpy` | Medium |
| 3 | char n-gram hashing (512-dim) | none | Baseline |

## `HybridRetriever` — BM25 + Dense RRF

`HybridRetriever` combines sparse BM25 keyword search with dense vector similarity using Reciprocal Rank Fusion (RRF).

```python
from meshflow.intelligence.rag import HybridRetriever

retriever = HybridRetriever(
    texts=["doc chunk 1", "doc chunk 2", "doc chunk 3"],
    bm25_weight=0.4,    # weight for BM25 scores
    dense_weight=0.6,   # weight for embedding scores
)
results = retriever.query("contract termination", top_k=3)

agent = Agent(name="a", role="researcher", knowledge=[retriever])
```

## `SelfCorrectingRAG` — Retrieve → Grade → Refine

`SelfCorrectingRAG` runs a retrieve-then-grade loop: if retrieved chunks score below the relevance threshold, it refines the query and retrieves again.

```python
from meshflow.intelligence.rag import SelfCorrectingRAG

rag = SelfCorrectingRAG(
    retriever=retriever,
    threshold=0.7,     # minimum relevance score to accept chunks
    max_rounds=3,      # maximum refinement iterations
)
# Used as a knowledge source in Agent
agent = Agent(name="a", role="researcher", knowledge=[rag])
```

## `RAGTokenBudget`

Limits the total token count of retrieved context to control cost.

```python
from meshflow.intelligence.rag import RAGTokenBudget

budget = RAGTokenBudget(max_tokens=1500)
trimmed = budget.trim(chunks)   # list[str] — truncated to fit the budget
```

When a `TokenBudgetTracker` is active, `AgentKnowledge.retrieve()` automatically trims RAG context to 15% of the remaining token budget.
