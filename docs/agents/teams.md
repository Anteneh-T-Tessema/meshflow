# Teams

`Team` groups agents into a governed collaboration — no workflow graph required.

```python
from meshflow import Agent, Team

planner    = Agent(name="planner",    role="planner")
researcher = Agent(name="researcher", role="researcher")
writer     = Agent(name="writer",     role="executor")
critic     = Agent(name="critic",     role="critic")

team = Team(
    name="research_team",
    agents=[planner, researcher, writer, critic],
    pattern="supervised",
    policy="standard",
)
result = await team.run("Write a market analysis on electric vehicles")
```

---

## Team fields

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Identifier for this team |
| `agents` | `list[Agent]` | required | At least one agent |
| `pattern` | `TeamPattern` | `"sequential"` | Coordination pattern (see below) |
| `policy` | `Policy \| str \| None` | `None` | Governance policy — defaults to `"standard"` |
| `budget_usd` | `float` | `5.0` | Cost budget for the entire team run |

---

## Patterns

### sequential

Each agent runs in order. Output feeds the next agent as additional context.

```python
team = Team(
    name="pipeline",
    agents=[planner, researcher, writer],
    pattern="sequential",
)
# planner → researcher (sees planner output) → writer (sees both)
```

Use when each agent's work genuinely depends on the previous step.

---

### supervised

Sequential, but the **last agent is always a supervisor or critic** that can review and veto.

```python
team = Team(
    name="dev_team",
    agents=[coder, reviewer],   # reviewer runs last on every execution
    pattern="supervised",
)
```

The reviewer receives the full accumulated output of all previous agents. Use this for any pipeline where quality or safety review is mandatory.

---

### parallel

Fan-out / fan-in: all agents except the last run concurrently; the final agent synthesises their outputs.

```python
team = Team(
    name="multi_perspective",
    agents=[entry, analyst_a, analyst_b, synthesizer],
    pattern="parallel",
)
# entry → analyst_a ─┐
#                    ├→ synthesizer
# entry → analyst_b ─┘
```

Requires at least 3 agents. With fewer than 3, falls back to sequential.

---

### hierarchical

The **first agent acts as orchestrator** and drives the remaining agents sequentially.

```python
team = Team(
    name="managed_pipeline",
    agents=[orchestrator, researcher, writer],
    pattern="hierarchical",
)
# orchestrator runs first, then researcher, then writer
```

Use when you want an LLM orchestrator to plan before workers execute.

---

### reflective

Generate → critique loop between exactly 2 agents. The critic loops back to the generator until `confidence >= 0.9` or 5 iterations.

```python
team = Team(
    name="quality_loop",
    agents=[generator, critic],
    pattern="reflective",
)
```

Use when output quality is more important than speed.

---

## team.run()

Returns a `WorkflowResult`:

```python
result = await team.run("Analyse Q3 revenue data")

result.output          # str — final agent's output
result.steps           # list of step results
result.total_tokens    # int
result.total_cost_usd  # float
result.blocked         # bool — True if any guardrail blocked
```

Pass extra context as a dict:

```python
result = await team.run("Analyse revenue", context={"quarter": "Q3", "format": "executive"})
```

---

## team.stream()

Yields `StreamChunk` objects as each agent produces tokens:

```python
async for chunk in await team.stream("Draft a product roadmap"):
    if chunk.kind == "node_start":
        print(f"\n[{chunk.node_name}] starting...")
    elif chunk.is_token:
        print(chunk.content, end="", flush=True)
    elif chunk.kind == "node_end":
        print(f"\n[{chunk.node_name}] done")
```

See [Streaming](streaming.md) for the full `StreamChunk` reference.

---

## YAML definition

```yaml
version: "1.0"

agents:
  - name: planner
    role: planner
    model: claude-sonnet-4-6

  - name: researcher
    role: researcher
    model: claude-sonnet-4-6
    memory: true

  - name: writer
    role: executor
    model: claude-haiku-4-5-20251001

  - name: critic
    role: critic
    model: claude-sonnet-4-6

team:
  name: research_team
  pattern: supervised
  agents: [planner, researcher, writer, critic]
```

---

## Team vs GroupChat vs Crew

| | Team | GroupChat | Crew |
|---|---|---|---|
| Execution model | Structured graph | Open conversation | Task-based |
| Agent ordering | Fixed by pattern | Dynamic per turn | Defined per task |
| Termination | Final node completes | Keyword or callable | All tasks complete |
| Best for | Pipelines with clear stages | Collaborative exploration | Independent parallel tasks |

Use `Team` when your workflow has clear sequential or parallel stages. Use `GroupChat` when agents need to iterate conversationally. Use `Crew` when tasks are independent and can be assigned to specific agents.
