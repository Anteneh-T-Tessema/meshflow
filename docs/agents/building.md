# Building Agents

`Agent` is MeshFlow's declarative builder — create governed agents without subclassing.

---

## Minimal example

```python
from meshflow import Agent

researcher = Agent(
    name="researcher",
    role="researcher",
    model="claude-sonnet-4-6",
)
result = await researcher.run("What is prompt caching?")
print(result["result"])
```

---

## Agent fields

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Unique identifier for this agent |
| `role` | `str \| AgentRole` | `"executor"` | `planner`, `researcher`, `executor`, `critic`, `orchestrator`, `guardian` |
| `model` | `str` | `""` | Model name — provider auto-inferred. Empty → `MESHFLOW_MODEL` env var |
| `llm` | `Any` | `None` | Pre-built `LLM` instance or any `LLMProvider`. Overrides `model=` |
| `tools` | `list` | `[]` | `Tool` objects or tool name strings |
| `skills` | `list[str]` | `[]` | Built-in skill names that augment the system prompt |
| `mcps` | `list` | `[]` | MCP server URLs (str) or `StdioServerParams` objects |
| `input_guardrails` | `list` | `[]` | Guardrail instances applied to the task text before the LLM |
| `output_guardrails` | `list` | `[]` | Guardrail instances applied to LLM output before returning |
| `knowledge` | `list` | `[]` | Knowledge sources — file paths, text, `VectorStore`, or `KnowledgeSource` |
| `memory` | `bool` | `False` | Enable cross-step memory |
| `memory_backend` | `Any` | `None` | `MemoryBackend` instance or `"sqlite://path.db"` shorthand |
| `memory_session_id` | `str` | `""` | Session ID for memory persistence. Defaults to `agent.name` |
| `cache` | `Any` | `None` | `LLMCache` instance, `True` (→ `InMemoryCache`), or `False` |
| `healing` | `Any` | `None` | `HealingPolicy` instance for automatic retries on low confidence |
| `teachable` | `bool` | `False` | Wrap with `TeachableAgent` to learn from corrections |
| `handoffs` | `list` | `[]` | Peer agents this agent can transfer control to |
| `delegates` | `list[Agent]` | `[]` | Sub-agents this agent can delegate subtasks to via tools |
| `system_prompt` | `str` | `""` | Override the default role prompt |
| `risk` | `RiskTier` | `READ_ONLY` | Risk tier: `READ_ONLY`, `INTERNAL`, `EXTERNAL_IO`, `DESTRUCTIVE` |
| `policy` | `Policy \| str \| None` | `None` | Governance policy. Defaults to `"standard"` |
| `model_router` | `Any` | `None` | `ModelRouter` — auto-selects model tier per task |
| `context_pruner` | `Any` | `None` | `SlidingWindowPruner` or `SummaryPruner` — auto-prunes context |
| `mode` | `str` | `"production"` | `"production"` or `"sandbox"` (sandbox skips LLM calls) |

---

## Model auto-detection

MeshFlow infers the provider from the model name:

```python
Agent(name="a", model="gpt-4o")              # → OpenAI
Agent(name="b", model="claude-opus-4-7")      # → Anthropic
Agent(name="c", model="gemini-2.0-flash")     # → Google
Agent(name="d", model="llama3.2")             # → local Ollama
Agent(name="e", model="groq/llama-3.1-70b")   # → LiteLLM
```

Use the `LLM` entry point to pass credentials explicitly:

```python
from meshflow import LLM, Agent

agent = Agent(
    name="analyst",
    llm=LLM("gpt-4o", api_key="sk-..."),
)
```

Omit `model=` entirely and MeshFlow picks the best available model from your environment:

```python
agent = Agent(name="g", role="researcher")   # auto-detects from API keys in env
```

---

## run()

Runs the agent and returns a result dict.

```python
result = await agent.run("Explain HIPAA §164.502")

result["result"]             # str — the agent's response
result["agent_name"]         # str — agent name
result["role"]               # str — agent role
result["tokens"]             # int — total tokens used
result["cost_usd"]           # float — cost in USD
result["stated_confidence"]  # float — 0.0–1.0 confidence extracted from output
result["blocked"]            # bool — True if a guardrail blocked the response
```

Pass optional context:

```python
result = await agent.run("Summarise the report", context={"format": "bullet_points"})
```

---

## stream()

Async generator that yields token strings as they are produced:

```python
async for token in agent.stream("Write a Python decorator"):
    print(token, end="", flush=True)
```

---

## run_typed()

Run and parse the response into a Pydantic model. Retries once on invalid JSON:

```python
from pydantic import BaseModel

class Report(BaseModel):
    title: str
    findings: list[str]
    confidence: float

result: Report = await agent.run_typed("Analyse Q3 earnings", Report)
print(result.title)
print(result.findings)
```

---

## run_structured()

Like `run_typed()` but returns a `StructuredOutputResult` with token and cost metadata:

```python
from pydantic import BaseModel

class Plan(BaseModel):
    steps: list[str]
    risks: list[str]

out = await agent.run_structured("Plan a database migration", Plan, max_retries=3)
print(out.data.steps)       # validated Plan instance
print(out.attempts)         # int — how many LLM calls it took
print(out.tokens)           # total tokens across all attempts
```

!!! tip
    Use `agent.with_structured_output(Schema)` for a reusable bound agent that always returns the typed data directly, without the wrapper.

    ```python
    analyst = Agent(name="analyst", role="researcher")
    structured = analyst.with_structured_output(Report)
    report: Report = await structured.run("Summarise Q3 earnings")
    ```

---

## Healing

Automatically retry on failure or low confidence:

```python
from meshflow.agents.healing import HealingPolicy

agent = Agent(
    name="researcher",
    role="researcher",
    healing=HealingPolicy(min_confidence=0.7, max_attempts=3),
)

# Per-call override
result = await agent.run_with_healing("Research X", policy=HealingPolicy(max_attempts=2))
```

---

## Handoffs

Transfer control to a peer agent mid-conversation:

```python
support = Agent(name="support", role="executor", handoffs=[billing_agent])

# If the agent responds with TRANSFER_TO:billing, control passes to billing_agent
result = await support.run_with_handoffs("I need to update my payment method")
result.final_agent   # which agent handled it last
result.transfers     # list of handoff events
```

---

## Delegates

Give an agent sub-agents it can delegate work to as tools:

```python
researcher = Agent(name="researcher", role="researcher")
writer     = Agent(name="writer",     role="executor")

orchestrator = Agent(
    name="orchestrator",
    role="orchestrator",
    delegates=[researcher, writer],
)
# orchestrator can now call delegate_to_researcher(...) and delegate_to_writer(...)
result = await orchestrator.run("Write a report on quantum computing")
```

---

## MCP server integration

Connect to any HTTP SSE MCP server:

```python
agent = Agent(
    name="mcp_agent",
    role="executor",
    mcps=["https://mcp.example.com/sse"],
)
# All tools from the MCP server are registered automatically
```
