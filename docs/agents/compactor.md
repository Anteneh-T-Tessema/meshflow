# ContextCompactor

`ContextCompactor` reduces conversation context size when it approaches the model's context window limit. Three strategies are available: Claude-native summarisation, sliding window truncation, and rolling summary accumulation.

---

## Quick start

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy

compactor = ContextCompactor(CompactionConfig(
    strategy=CompactionStrategy.SLIDING_WINDOW,
    max_tokens=8_000,
    preserve_last_n=4,
))
messages = [{"role": "user", "content": "..."}, ...]   # your conversation
compacted, stats = compactor.compact(messages)
print(f"Removed {stats.messages_removed} messages using {stats.strategy_used}")
```

---

## Strategies

### `CompactionStrategy.CLAUDE_NATIVE`

Delegates summarisation to a dedicated `claude-haiku-4-5-20251001` call that reads the full context and returns a compressed summary block. Best quality; costs one extra LLM call.

### `CompactionStrategy.SLIDING_WINDOW`

Drops the oldest messages until the context fits while preserving `preserve_last_n` messages. Zero extra cost; may lose important early context.

### `CompactionStrategy.SUMMARY`

Falls back to a passthrough summary insertion. Balances cost and quality for long multi-turn sessions.

---

## CompactionConfig fields

| Field | Type | Default | Description |
|---|---|---|---|
| `strategy` | `CompactionStrategy` | `CLAUDE_NATIVE` | Compaction algorithm |
| `max_tokens` | `int` | `8000` | Token budget — compaction triggers when messages exceed this |
| `preserve_last_n` | `int` | `4` | Messages preserved verbatim at the end of the window |

---

## compact() return value

`compact()` returns a tuple `(messages, CompactionStats)`:

| Field | Type | Description |
|---|---|---|
| `messages_removed` | `int` | Messages dropped |
| `strategy_used` | `str` | Strategy that ran: `"sliding_window"`, `"summary"`, `"none_needed"` |

---

## Explicit compaction

```python
from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy

compactor = ContextCompactor(CompactionConfig(
    strategy=CompactionStrategy.SLIDING_WINDOW,
    max_tokens=4000,
    preserve_last_n=2,
))
messages = [{"role": "user", "content": "..."}, ...]
compacted, stats = compactor.compact(messages)
print(f"Removed {stats.messages_removed} → {len(compacted)} messages remain")
```

---

## Exports

```python
from meshflow.core.compactor import ContextCompactor, CompactionConfig, CompactionStrategy
```
