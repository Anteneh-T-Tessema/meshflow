# Framework Adapters

Wrap any existing LangGraph, CrewAI, AutoGen, or callable with MeshFlow governance — no rewrite needed.

## govern() — universal wrapper

```python
from meshflow import govern

# Wrap any existing app (LangGraph graph, CrewAI crew, callable)
governed_app = govern(your_existing_app, policy=compliance_profile("hipaa"))
result = await governed_app.run("task")
```

## LangGraph

```python
from meshflow import from_langgraph
from langgraph.graph import StateGraph  # your existing graph

# Your existing LangGraph graph
lg_graph = StateGraph(MyState).add_node(...).compile()

# Wrap with governance — adds audit, compliance, rate limiting
governed = from_langgraph(lg_graph, policy=policy_for_mode("legal-critical"))
result = await governed.run({"messages": [], "task": "summarize"})
```

## CrewAI

```python
from meshflow import from_crewai
from crewai import Crew, Agent, Task  # your existing crew

crew = Crew(agents=[...], tasks=[...])
governed = from_crewai(crew, compliance=compliance_profile("sox"))
result = await governed.kickoff(inputs={"topic": "quarterly earnings"})
```

## AutoGen

```python
from meshflow import from_autogen
import autogen

ag_agent = autogen.AssistantAgent(name="assistant", llm_config={...})
governed = from_autogen(ag_agent, policy=policy_for_mode("standard"))
result = await governed.run("analyze this data")
```

## Plain callable

```python
from meshflow import from_callable

async def my_pipeline(task: str) -> str:
    # ... existing logic
    return result

governed = from_callable(my_pipeline, name="my-pipeline")
result = await governed.run("task input")
```

## What governance adds

All adapters route execution through `StepRuntime`:

- ✅ Policy evaluation (DENY wins)
- ✅ Compliance profile enforcement
- ✅ Audit ledger writes
- ✅ Rate limiting
- ✅ Cost tracking
- ✅ SLA recording
- ✅ Webhook notifications on violations
