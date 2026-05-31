# Conversation Evals

Evaluate agents across full multi-turn sessions to verify context retention, coherent follow-ups, and policy compliance over an entire conversation.

```python
from meshflow.eval.conversation_eval import ConversationEval, ConversationCase, Turn

case = ConversationCase(
    name="hipaa-followup",
    turns=[
        Turn(user="What is the HIPAA minimum necessary standard?",
             must_contain=["minimum necessary"]),
        Turn(user="Give me a concrete example of how to apply it.",
             must_contain=["example"]),
        Turn(user="What are the penalties for violations?",
             must_contain=["penalty", "fine"]),
    ],
)

runner = ConversationEval()
result = await runner.run(case, agent=my_agent)
print(result.summary())
```

## `Turn`

One turn in a multi-turn conversation scenario.

| Field | Type | Description |
|---|---|---|
| `user` | str | User message sent to the agent |
| `must_contain` | list[str] | Case-insensitive substrings the response must include |
| `must_not_contain` | list[str] | Substrings that must not appear in the response |
| `judge_rubric` | str | Per-turn rubric override passed to `LLMJudge` |
| `min_score` | float | Minimum judge score for this turn (0–1); overrides instance threshold |

## `ConversationCase`

```python
case = ConversationCase(
    name="hipaa-followup",       # unique identifier
    turns=[...],                  # list[Turn]
    system_prompt="You are a HIPAA compliance assistant.",
    tags=["compliance", "smoke"],
    metadata={"owner": "legal-team"},
)
```

## `ConversationEval`

```python
runner = ConversationEval(
    judge=None,           # auto-creates LLMJudge (EchoProvider fallback in tests)
    pass_threshold=0.6,   # minimum judge score to pass a turn
)
```

### `run(case, *, agent=None, provider=None, model="claude-haiku-4-5") -> ConversationResult`

Run a single `ConversationCase`. Pass either a MeshFlow `Agent` or a raw `provider` + `model`.

```python
# With a MeshFlow agent
result = await runner.run(case, agent=my_agent)

# With a raw provider (useful in tests)
from meshflow.agents.base import EchoProvider
result = await runner.run(case, provider=EchoProvider("ok"), model="claude-haiku-4-5")
```

The runner maintains a full message `history` between turns, so each assistant reply is visible to the agent on the next turn.

### `run_suite(cases, **kwargs) -> list[ConversationResult]`

Run multiple `ConversationCase` objects concurrently.

```python
results = await runner.run_suite([case1, case2, case3], agent=my_agent)
```

## `TurnResult`

Per-turn evaluation result stored in `ConversationResult.turn_results`.

| Field | Type | Description |
|---|---|---|
| `turn_idx` | int | 1-based turn index |
| `user_message` | str | The user message for this turn |
| `agent_response` | str | The agent's reply |
| `contains_passed` | bool | All `must_contain` checks passed |
| `not_contains_passed` | bool | All `must_not_contain` checks passed |
| `judge_score` | float | LLM judge score (0–1) |
| `judge_reasoning` | str | Judge's one-sentence explanation |
| `passed` | bool | All checks AND judge score met threshold |
| `duration_ms` | float | Latency for this turn |

A turn passes only when `contains_passed AND not_contains_passed AND judge_score >= effective_min`, where `effective_min = max(pass_threshold, turn.min_score)`.

## `ConversationResult`

```python
result.case_name        # str
result.turn_results     # list[TurnResult]
result.total_duration_ms  # float
result.turns_passed     # int — turns where passed=True
result.turns_failed     # int
result.avg_score        # float — mean judge score across all turns
result.passed           # bool — True only if every turn passed
result.summary()        # "[PASS] hipaa-followup — 3/3 turns passed, avg score 0.88, 1250ms"
result.to_dict()        # serialisable dict
```

## Full Example

```python
import asyncio
from meshflow.eval.conversation_eval import ConversationEval, ConversationCase, Turn

async def main():
    case = ConversationCase(
        name="policy-awareness",
        system_prompt="You are a compliance assistant. Never speculate.",
        turns=[
            Turn(
                user="Is PHI allowed in email?",
                must_contain=["PHI", "encrypt"],
                must_not_contain=["no problem", "fine to send"],
                judge_rubric="Score for regulatory accuracy and caution.",
                min_score=0.75,
            ),
            Turn(
                user="What encryption standard is required?",
                must_contain=["AES", "TLS", "encrypt"],
                judge_rubric="Score for technical accuracy.",
            ),
            Turn(
                user="Summarise what you just told me.",
                judge_rubric="Score for coherent summary referencing prior turns.",
                min_score=0.8,
            ),
        ],
    )

    runner = ConversationEval(pass_threshold=0.7)
    result = await runner.run(case, agent=my_agent)
    print(result.summary())
    for t in result.turn_results:
        print(f"  Turn {t.turn_idx}: {'PASS' if t.passed else 'FAIL'} — {t.judge_reasoning}")

asyncio.run(main())
```
