# GroupChat

`GroupChat` enables AutoGen-style multi-agent conversations where agents take turns responding to each other until the task is complete.

---

## Basic usage

```python
from meshflow.agents.conversation import GroupChat, GroupChatManager
from meshflow import Agent

researcher = Agent(name="researcher", role="researcher")
coder      = Agent(name="coder",      role="executor")
critic     = Agent(name="critic",     role="critic")

chat = GroupChat(
    agents=[researcher, coder, critic],
    max_turns=12,
    speaker_selection="auto",
    termination="TERMINATE",
)
manager = GroupChatManager(chat, policy="standard")
result  = await manager.run("Build a REST API for a todo list")

print(result.last_message)
print(result.transcript())
```

---

## GroupChat fields

| Field | Type | Default | Description |
|---|---|---|---|
| `agents` | `list[Agent]` | required | At least one agent |
| `max_turns` | `int` | `20` | Hard cap on conversation turns |
| `speaker_selection` | `SpeakerStrategy` | `"round_robin"` | How the next speaker is chosen (see below) |
| `termination` | `str \| callable` | `"TERMINATE"` | Stop condition |
| `speaker_fn` | `callable \| None` | `None` | Custom speaker selection function — required when `speaker_selection="custom"` |
| `allowed_transitions` | `dict[str, list[str]] \| None` | `None` | Restrict which agents can follow which |

---

## Speaker selection modes

### round_robin

Agents speak in the order they appear in `agents`, cycling indefinitely.

```python
chat = GroupChat(agents=[a, b, c], speaker_selection="round_robin")
# Turn order: a → b → c → a → b → c → ...
```

### random

A random agent is picked each turn.

```python
chat = GroupChat(agents=[a, b, c], speaker_selection="random")
```

### auto

An LLM selects the most appropriate next speaker based on the conversation history and each agent's role. Falls back to round-robin if LLM selection fails.

```python
chat = GroupChat(agents=[researcher, coder, critic], speaker_selection="auto")
# The LLM picks whoever is most useful next given what's been said
```

### custom

Provide your own selection function:

```python
def my_selector(messages, agents):
    # messages: list[ChatMessage], agents: list[Agent]
    # Return the next Agent to speak
    if any("error" in m.content.lower() for m in messages[-3:]):
        return next(a for a in agents if a.name == "debugger")
    return agents[0]

chat = GroupChat(
    agents=[researcher, coder, debugger],
    speaker_selection="custom",
    speaker_fn=my_selector,
)
```

---

## Termination

### String keyword

The conversation ends when any agent's message contains the string:

```python
chat = GroupChat(agents=[a, b], termination="TERMINATE")
# Any agent that includes "TERMINATE" in its response ends the chat
```

The `GroupChatManager` system message tells agents to include `TERMINATE` when the task is done.

### Callable

```python
def done_when_approved(messages):
    return any(
        m.sender == "approver" and "APPROVED" in m.content
        for m in messages
    )

chat = GroupChat(agents=[worker, approver], termination=done_when_approved)
```

---

## Allowed transitions

Constrain which agents can speak after which:

```python
chat = GroupChat(
    agents=[planner, coder, reviewer],
    speaker_selection="round_robin",
    allowed_transitions={
        "planner":  ["coder"],
        "coder":    ["reviewer"],
        "reviewer": ["planner", "coder"],  # reviewer can send back to either
    },
)
```

When `allowed_transitions` is set, the `speaker_selection` strategy operates only over the allowed candidates for the current speaker.

---

## GroupChatManager fields

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | `GroupChat` | required | The GroupChat to manage |
| `policy` | `Policy \| str \| None` | `None` | Governance policy — defaults to `"standard"` |
| `system_message` | `str` | see below | Injected into every agent's turn context |

Default system message:

> You are participating in a group discussion. Collaborate, build on others' ideas, and help the team reach the best answer. When the task is complete, include 'TERMINATE' in your final message.

---

## ConversationResult fields

```python
result = await manager.run("Design a caching layer for a REST API")

result.last_message           # str — final agent's message
result.messages               # list[ChatMessage] — full conversation
result.total_turns            # int — how many turns ran
result.total_tokens           # int — total tokens across all turns
result.total_cost_usd         # float — total cost
result.terminated             # bool — True if termination condition was met
result.participants           # list[str] — agent names that participated

result.transcript()           # str — full formatted conversation
result.messages_from("coder") # list[ChatMessage] — filter by sender
```

---

## ChatMessage fields

```python
msg = result.messages[0]

msg.sender    # str — agent name or "user"
msg.content   # str — message text
msg.turn      # int — turn number
msg.metadata  # dict — {"tokens": int, "cost_usd": float, "confidence": float}
```

---

## Streaming

Yield `ChatMessage` objects as they are produced:

```python
async for msg in manager.stream("Build a microservice for user authentication"):
    print(f"[{msg.sender}]: {msg.content[:100]}...")
```

---

## YAML definition

```yaml
version: "1.0"

agents:
  - name: researcher
    role: researcher
    model: claude-sonnet-4-6
  - name: coder
    role: executor
    model: claude-sonnet-4-6
  - name: critic
    role: critic
    model: claude-sonnet-4-6

groupchat:
  agents: [researcher, coder, critic]
  max_turns: 15
  speaker_selection: auto
  termination: "TERMINATE"
```

---

## GroupChat vs Team vs ReActAgent

| | GroupChat | Team | ReActAgent |
|---|---|---|---|
| Execution | Conversational turns | Structured pipeline | Autonomous tool loop |
| Agent ordering | Dynamic | Fixed by pattern | Single agent, multi-step |
| Best for | Collaborative exploration, debate | Defined workflow stages | Autonomous task completion |

Use `GroupChat` when agents need to build on each other's responses iteratively. Use `Team` when each stage has a clear, fixed role.
