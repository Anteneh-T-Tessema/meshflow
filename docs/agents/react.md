# ReActAgent

`ReActAgent` wraps any MeshFlow `Agent` in a Reason-Act loop — the foundation of autonomous, multi-step tool use.

Without a loop, agents are one-shot LLM calls. `ReActAgent` gives them a plan-act-observe-reflect cycle that continues until the task is done or `max_steps` is reached.

---

## Basic usage

```python
from meshflow.agents.react import ReActAgent
from meshflow import Agent, tool, RiskTier

@tool(name="web_search", description="Search the web for information", risk=RiskTier.EXTERNAL_IO)
async def web_search(query: str) -> str:
    # your actual search implementation
    return f"Results for: {query}"

@tool(name="read_file", description="Read a local file", risk=RiskTier.READ_ONLY)
async def read_file(path: str) -> str:
    with open(path) as f:
        return f.read()

agent = Agent(
    name="researcher",
    role="researcher",
    model="claude-sonnet-4-6",
    tools=[web_search, read_file],
)
react = ReActAgent(agent, max_steps=8)

result = await react.run("Find the latest HIPAA enforcement actions from 2025")
print(result.answer)
print(f"Completed in {result.steps_taken} steps, {result.total_tokens} tokens")
```

---

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent` | `Agent` | required | A MeshFlow `Agent` with tools registered |
| `max_steps` | `int` | `10` | Hard limit on thought-act cycles |
| `reflect_every` | `int` | `0` | Inject a reflection prompt every N steps. `0` = disabled |

---

## The loop

Each iteration follows this strict format, enforced by the system prompt:

```
Thought: <reasoning about what to do next>
Action: <tool_name or "Final Answer">
Action Input: <JSON object for the tool, or final answer string>
```

After each tool call, the observation (tool result) is appended to the scratchpad:

```
Observation: <tool result>
```

The loop continues until the agent outputs `Action: Final Answer` or `max_steps` is hit.

---

## ReActResult fields

```python
result = await react.run("Research quantum computing applications in finance")

result.answer           # str — the final answer from the agent
result.steps            # list[ThoughtStep] — full step history
result.steps_taken      # int — how many cycles ran
result.total_tokens     # int — total tokens across all steps
result.total_cost_usd   # float — total cost in USD
result.finished         # bool — False if max_steps was reached without Final Answer
result.agent_name       # str — name of the underlying agent
```

---

## ThoughtStep fields

Each element of `result.steps` is a `ThoughtStep`:

```python
step = result.steps[0]

step.thought        # str — the agent's reasoning
step.action         # str — tool name or "Final Answer"
step.action_input   # dict | str — tool arguments or final answer
step.observation    # str — tool result (empty for Final Answer)
step.step           # int — 1-based step number
step.tokens         # int — tokens used in this step
step.cost_usd       # float — cost for this step
```

---

## Reflection

Enable periodic reflection to prevent the agent from getting stuck in loops:

```python
react = ReActAgent(agent, max_steps=12, reflect_every=4)
```

Every 4 steps, the loop injects:

> [Reflection check] Are you making progress? If you are going in circles, switch strategy or give a Final Answer.

---

## Inspecting steps

```python
result = await react.run("Analyse the top 5 AI frameworks by GitHub stars")

for step in result.steps:
    print(f"Step {step.step}: {step.action}")
    if step.action != "Final Answer":
        print(f"  Input: {step.action_input}")
        print(f"  Observation: {step.observation[:200]}")

if not result.finished:
    print(f"WARNING: hit max_steps={react._max_steps} without a Final Answer")
```

---

## When to use ReActAgent

Use `ReActAgent` when:

- The agent needs to call tools multiple times in sequence
- The number of tool calls is not known in advance
- The agent must adapt its plan based on what it discovers

Use a plain `Agent` when:

- The task is a single LLM call
- Tool use is expected but limited to one or two calls

Use `Team` when:

- Multiple specialist agents each handle a defined stage
- You want deterministic, policy-governed execution order

!!! warning
    `ReActAgent` gives the LLM autonomy over how many tool calls to make. Always set a `max_steps` budget appropriate to your cost constraints and set `risk=RiskTier.EXTERNAL_IO` on any tools that make network or database calls.
