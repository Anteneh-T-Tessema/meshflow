# MeshFlow Quick Start

Get a governed multi-agent system running in under 5 minutes.

---

## Install

```bash
pip install meshflow
```

For specific LLM providers:

```bash
pip install "meshflow[openai]"      # OpenAI / GPT-4o
pip install "meshflow[gemini]"      # Google Gemini
pip install "meshflow[bedrock]"     # AWS Bedrock
pip install "meshflow[full]"        # all providers + RAG + OTEL
```

---

## Hello, Agent

```python
import meshflow

agent = meshflow.Agent(
    name="assistant",
    role="You are a helpful assistant.",
)

result = agent.run("What is the capital of France?")
print(result.output)
```

Set `ANTHROPIC_API_KEY` in your environment, or use the offline echo provider for testing:

```bash
MESHFLOW_MOCK=1 python my_script.py
```

---

## Tools

```python
from meshflow import Agent, tool, RiskTier

@tool(name="search_web", risk=RiskTier.EXTERNAL_IO)
def search_web(query: str) -> str:
    return f"Results for: {query}"

agent = Agent(
    name="researcher",
    role="You research topics thoroughly.",
    tools=[search_web],
)

result = agent.run("What are the latest AI safety papers?")
print(result.output)
```

---

## Team of Agents

```python
from meshflow import Agent, Team

planner  = Agent(name="planner",  role="You break tasks into steps.")
coder    = Agent(name="coder",    role="You write clean Python code.")
reviewer = Agent(name="reviewer", role="You review code for correctness.")

team = Team([planner, coder, reviewer], pattern="supervised")
result = team.run("Build a function that sorts a list of dicts by a key.")
print(result.output)
```

---

## Compliance Profiles

Apply governance policies with one line:

```python
from meshflow import Agent, compliance_profile

hipaa_policy = compliance_profile("hipaa")

agent = Agent(
    name="clinical-assistant",
    role="You answer clinical questions.",
    policy=hipaa_policy,
)
```

Built-in profiles: `hipaa`, `sox`, `gdpr`, `pci`, `nerc`.

---

## Guardrails

```python
from meshflow import Agent, PIIBlockGuardrail, LengthGuardrail

agent = Agent(
    name="safe-agent",
    role="You are a customer support agent.",
    input_guardrails=[PIIBlockGuardrail()],
    output_guardrails=[LengthGuardrail(max_chars=2000)],
)
```

---

## Structured Output

```python
from pydantic import BaseModel
from meshflow import StructuredAgent

class Summary(BaseModel):
    title: str
    key_points: list[str]
    sentiment: str

agent = StructuredAgent(name="summarizer", schema=Summary)
result = agent.run("MeshFlow 1.0 ships with 4,349 tests and full HIPAA compliance.")
print(result.parsed.key_points)
```

---

## State Graph (LangGraph-style)

```python
from typing import TypedDict
from meshflow import StateGraph, END

class State(TypedDict):
    message: str
    count: int

def increment(state: State) -> State:
    return {"count": state["count"] + 1}

def check(state: State) -> str:
    return "done" if state["count"] >= 3 else "increment"

graph = (
    StateGraph(State)
    .add_node("increment", increment)
    .add_conditional_edges("increment", check, {"done": END, "increment": "increment"})
    .set_entry_point("increment")
    .compile()
)

result = graph.invoke({"message": "hello", "count": 0})
print(result["count"])  # 3
```

---

## YAML Workflow

Define an entire workflow without Python:

```yaml
# workflow.yaml
kind: workflow
name: research-pipeline
nodes:
  - name: fetch
    role: "You fetch and summarize web content."
  - name: analyze
    role: "You analyze and extract key insights."
edges:
  - from: fetch
    to: analyze
compliance:
  profile: gdpr
```

```bash
meshflow run workflow.yaml --input "Summarize AI safety research from 2025"
```

---

## Evaluation

```yaml
# evals.yaml
suite: my-agent-eval
scenarios:
  - name: basic-math
    input: "What is 2 + 2?"
    expected: "4"
    judge: exact_match
  - name: summarization
    input: "Summarize: The sky is blue."
    judge: llm
    criteria: "Response is a concise summary"
```

```bash
meshflow eval run evals.yaml --save-baseline baseline.json
```

---

## Serve as HTTP API

```bash
meshflow serve --host 0.0.0.0 --port 8000
```

Then call from any language via the REST client:

```python
from meshflow import MeshFlowClient

client = MeshFlowClient("http://localhost:8000", api_key="your-key")
result = client.run_agent("assistant", "What is 2 + 2?")
print(result.output)
```

---

## API Keys

Generate a server API key for production:

```bash
meshflow keys generate --name prod-key --role operator
```

Pass the key to clients:

```python
client = MeshFlowClient("http://localhost:8000", api_key="mf-...")
```

---

## OpenTelemetry (OTEL)

Export spans to any OTLP-compatible backend (Jaeger, Tempo, Honeycomb, etc.):

```bash
meshflow serve --otlp-endpoint http://localhost:4318
```

Or configure at runtime:

```python
from meshflow import set_global_exporter, OTELExporter

set_global_exporter(OTELExporter(endpoint="http://localhost:4318"))
```

Check live exporter status:

```bash
meshflow serve  # then GET /otel/config
```

---

## Kubernetes / Helm

Deploy to Kubernetes using the bundled Helm chart:

```bash
helm install meshflow ./k8s/helm \
  --set apiKey=$ANTHROPIC_API_KEY \
  --set replicaCount=3
```

MeshFlow exposes `/health/live` and `/health/ready` for k8s probes automatically.

Run `meshflow doctor` before deploying to verify your environment is ready.

---

## Next Steps

- **[Feature Overview](index.md)** — full feature overview
- **[Security Policy](security-policy.md)** — security policy and vulnerability reporting
- **`meshflow --help`** — CLI reference
- **`meshflow doctor`** — diagnose your environment before deploying
