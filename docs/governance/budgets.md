# Budgets — ThinkingBudget, EffortBudget, BudgetConfig

MeshFlow provides fine-grained token and effort budgets for Claude's extended thinking. These are standalone data classes — instantiate them to configure budgets, validate spend, or integrate into your own orchestration layer.

---

## Quick start

```python
import os; os.environ["MESHFLOW_MOCK"] = "1"
from meshflow import BudgetConfig, ThinkingBudget, EffortBudget, BudgetViolation

# Configure a budget
budget = BudgetConfig(
    thinking=ThinkingBudget(tokens=8000, enabled=True),
    effort=EffortBudget(level="high"),
    usd_cap=2.00,
)

# Validate token usage
budget.check_thinking_tokens(used=5000)   # OK
try:
    budget.check_thinking_tokens(used=9000)   # exceeds 8000
except BudgetViolation as e:
    print(f"Budget exceeded: {e}")

# Validate USD spend
budget.check_usd(1.50)   # OK
try:
    budget.check_usd(2.50)   # exceeds $2.00
except BudgetViolation as e:
    print(f"Cost cap hit: {e}")
```

---

## ThinkingBudget

Controls how many tokens Claude may spend on internal chain-of-thought reasoning (extended thinking). Maps to Anthropic's `thinking.budget_tokens` parameter.

| Field | Type | Default | Description |
|---|---|---|---|
| `tokens` | `int` | `2000` | Maximum internal reasoning tokens per step |
| `enabled` | `bool` | `True` | Whether extended thinking is active |

```python
from meshflow import ThinkingBudget

tb = ThinkingBudget(tokens=16_000, enabled=True)
print(tb.to_api_param())   # {"type": "enabled", "budget_tokens": 16000}
```

---

## EffortBudget

Maps a human-readable effort level to a `ThinkingBudget`:

| Level | Thinking tokens | Use when |
|---|---|---|
| `"low"` | 1,024 | Simple classification, routing decisions |
| `"medium"` | 4,096 | Standard Q&A, summaries |
| `"high"` | 16,000 | Deep analysis, drafting, reasoning |
| `"max"` | 32,000 | Maximum reasoning for complex compliance tasks |

```python
from meshflow import EffortBudget

eb = EffortBudget(level="high")
print(eb.tokens)                     # 16000
print(eb.to_thinking_budget().tokens)  # 16000
```

---

## BudgetConfig

Combines `ThinkingBudget`, `EffortBudget`, and a USD cap into a single config object.

| Field | Type | Default | Description |
|---|---|---|---|
| `thinking` | `ThinkingBudget \| None` | `None` | Explicit token budget |
| `effort` | `EffortBudget \| None` | `None` | Effort level (used if `thinking` is unset) |
| `usd_cap` | `float` | `0.0` | Hard USD ceiling — `0.0` means no cap |

```python
from meshflow import BudgetConfig, ThinkingBudget, EffortBudget

# thinking takes priority over effort when both are set
bc = BudgetConfig(
    thinking=ThinkingBudget(tokens=8000),
    effort=EffortBudget(level="max"),   # ignored — thinking wins
)
print(bc.resolved_thinking_budget().tokens)  # 8000
```

---

## BudgetViolation

`BudgetViolation` is raised by `check_thinking_tokens()` and `check_usd()` when a cap is exceeded:

```python
from meshflow import BudgetViolation

try:
    BudgetConfig(usd_cap=0.10).check_usd(0.20)
except BudgetViolation as e:
    print(e)   # "USD budget exceeded: spent $0.2000 > cap $0.1000"
```

---

## BudgetUsage

`BudgetUsage` is a tracking dataclass for recording actual spend:

```python
from meshflow import BudgetUsage

usage = BudgetUsage(usd_spent=0.05, thinking_tokens_used=500,
                    output_tokens_used=300, input_tokens_used=200)
print(usage.to_dict())   # {"total_tokens": 1000, "usd_spent": 0.05, ...}
```

---

## Exports

```python
from meshflow import (
    BudgetConfig, ThinkingBudget, EffortBudget,
    BudgetViolation, BudgetUsage,
)
from meshflow.core.budget_config import BudgetConfig, ThinkingBudget, EffortBudget
```
