# Knowledge Sources

`AgentKnowledge` combines multiple retrieval sources and injects relevant chunks into every agent step.

```python
from meshflow import Agent, VectorStore, KnowledgeSource, AgentKnowledge

# Simplest: pass file paths or text directly to Agent
agent = Agent(
    name="docs-agent",
    role="researcher",
    knowledge=["docs/", "README.md", "Compliance is required for HIPAA workflows."],
)

# Full control
vs = VectorStore.from_directory("docs/")
ks1 = KnowledgeSource(source=vs, top_k=3, max_chars=2000)
ks2 = KnowledgeSource(source="internal_policy.txt", chunk_size=400)
ak = AgentKnowledge([ks1, ks2], top_k=5, max_chars=4096)
```

## KnowledgeSource parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `source` | required | File path, raw text, `VectorStore`, or any object with `.query(text, top_k)` |
| `top_k` | `3` | Max chunks returned per query |
| `chunk_size` | `400` | Characters per chunk when indexing a file or text |
| `max_chars` | `None` | Hard cap on total characters returned (overridden by AgentKnowledge) |

## AgentKnowledge parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sources` | required | List of `KnowledgeSource` objects |
| `top_k` | `5` | Total chunks to retrieve across all sources |
| `max_chars` | `4096` | Hard cap on total injected context — enforced with truncate strategy |

## Retrieval methods

```python
ak = AgentKnowledge([ks1, ks2])

# Plain text injection
text = ak.context_string("my query", max_chars=2000)

# Anthropic prompt-caching blocks (used automatically by Agent when provider supports it)
blocks = ak.context_blocks_cached("my query", max_chars=2000)
```

## Advanced: HybridRetriever + SelfCorrectingRAG

```python
from meshflow import HybridRetriever, SelfCorrectingRAG, LLMRanker, KnowledgeSource

# BM25 + dense Reciprocal Rank Fusion
retriever = HybridRetriever(texts=["doc1 text", "doc2 text"])
retriever.add_texts(["more docs"])
results = retriever.query("governance", top_k=5)

# Grade → refine loop
rag = SelfCorrectingRAG(
    retriever=retriever,
    agent=Agent(name="ranker", role="researcher"),
    grade_threshold=0.7,
    max_correction_rounds=2,
)
answer = await rag.run("What is the HIPAA minimum necessary rule?")

# Wire into Agent as a knowledge source
ks = KnowledgeSource(source=rag, top_k=3)
agent = Agent(name="smart", role="researcher", knowledge=[ks])
```
