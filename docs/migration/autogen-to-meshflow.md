# Migrating from AutoGen to MeshFlow

**Why now:** Microsoft is retiring AutoGen and migrating users to the new Microsoft Agent Framework.
MeshFlow wraps AutoGen natively — you can migrate in under 30 minutes with zero rewrites of your
existing agent logic.

---

## Why migrate to MeshFlow

| AutoGen | MeshFlow |
|---|---|
| Actively being sunset by Microsoft | Apache 2.0, independent, active development |
| No governance layer | DASC gate enforced on every step |
| No compliance profiles | HIPAA / SOX / GDPR / PCI / NERC built-in |
| No tamper-evident audit chain | SHA-256 ReplayLedger, cryptographically verifiable |
| No cost controls | `CostCap`, `ModelRouter`, `ContextCompactor` |
| No durable execution | SQLite / Redis / Postgres / S3 checkpoints |
| No HITL | Pause / resume with state preservation |
| No token optimization | `cache_control`, `stop_on_confidence`, context dedup |

---

## Migration paths

### Path A — Zero rewrite (recommended for first migration)

MeshFlow wraps your existing AutoGen agents with one function call. Your agent code is unchanged.

**Before (AutoGen):**
```python
import autogen

assistant = autogen.AssistantAgent(
    name="assistant",
    llm_config={"model": "gpt-4o", "api_key": "sk-..."},
    system_message="You are a helpful assistant.",
)

user_proxy = autogen.UserProxyAgent(
    name="user_proxy",
    human_input_mode="NEVER",
    code_execution_config={"work_dir": "coding"},
)

user_proxy.initiate_chat(assistant, message="Write a bubble sort in Python.")
```

**After (MeshFlow wrapper — no code changes to your agents):**
```python
import autogen
from meshflow.agents.adapters import from_autogen
from meshflow.core.mesh import Mesh

# Your existing AutoGen agents — unchanged
assistant = autogen.AssistantAgent(
    name="assistant",
    llm_config={"model": "gpt-4o", "api_key": "sk-..."},
    system_message="You are a helpful assistant.",
)

user_proxy = autogen.UserProxyAgent(
    name="user_proxy",
    human_input_mode="NEVER",
    code_execution_config={"work_dir": "coding"},
)

# Wrap in MeshFlow — governance added in 2 lines
mesh = Mesh(agents=[from_autogen(assistant), from_autogen(user_proxy)],
            compliance="standard")
result = await mesh.run("Write a bubble sort in Python.")

print(result.output)
print(f"cost=${result.total_cost_usd:.4f}  run_id={result.run_id}")
# meshflow replay <run_id>  → step-by-step debugger
```

---

### Path B — Native MeshFlow (recommended for new projects)

Replace AutoGen's agents with MeshFlow's native `Agent` class. Cleaner code,
full feature access, no AutoGen dependency.

**AutoGen pattern → MeshFlow equivalent:**

```python
# AutoGen: AssistantAgent
assistant = autogen.AssistantAgent(
    name="coder",
    system_message="Write clean, tested Python.",
    llm_config={"model": "gpt-4o"},
)

# MeshFlow equivalent
from meshflow import Agent
coder = Agent(
    name="coder",
    role="executor",
    model="claude-sonnet-4-6",        # or "gpt-4o" via OpenAI provider
    system_prompt="Write clean, tested Python.",
    memory=True,
)
```

```python
# AutoGen: GroupChat with speaker selection
groupchat = autogen.GroupChat(agents=[pm, engineer, qa], messages=[])
manager = autogen.GroupChatManager(groupchat=groupchat, llm_config=llm_config)
user_proxy.initiate_chat(manager, message="Build a rate limiter.")

# MeshFlow equivalent — supervised pattern
from meshflow import Team
team = Team(
    name="dev-team",
    agents=[pm, engineer, qa],
    pattern="supervised",    # orchestrator selects agents dynamically
    policy="standard",
)
result = await team.run("Build a rate limiter.")
```

```python
# AutoGen: ConversableAgent with code execution
agent = autogen.ConversableAgent(
    name="executor",
    code_execution_config={"executor": autogen.coding.LocalCommandLineCodeExecutor()},
)

# MeshFlow equivalent — with governed sandbox
from meshflow import Agent
from meshflow.tools.code_interpreter import CodeInterpreter

interpreter = CodeInterpreter(timeout=30, allow_modules=["numpy", "pandas"])
executor = Agent(
    name="executor",
    role="executor",
    tools=[interpreter],
)
```

---

## Feature mapping

| AutoGen concept | MeshFlow equivalent |
|---|---|
| `AssistantAgent` | `Agent(role="executor")` |
| `UserProxyAgent` | `Agent(role="executor")` with tools |
| `GroupChat` | `Team(pattern="supervised")` |
| `GroupChatManager` | Built into `Team` — no separate class needed |
| `ConversableAgent` | `Agent` with any role |
| `initiate_chat()` | `await team.run(task)` |
| `max_consecutive_auto_reply` | `max_steps` in `Policy` |
| `code_execution_config` | `Agent(tools=[CodeInterpreter()])` |
| `human_input_mode="ALWAYS"` | `policy="regulated"` + HITL |
| `llm_config` | `Agent(model="...", provider=...)` |
| `register_for_llm` / `register_for_execution` | `@tool(name=..., risk=RiskTier.READ_ONLY)` |
| `a_initiate_chat()` (async) | `await team.run()` (async by default) |
| Resume after crash | `DurableWorkflowExecutor.resume(run_id)` |

---

## Governance features you get automatically

After migrating, every run gets:

```
✓ SHA-256 audit chain    → meshflow dasc verify
✓ Cost tracking          → meshflow logs
✓ Step-by-step replay    → meshflow replay <run_id>
✓ Policy enforcement     → meshflow conformance python --level 5
✓ HITL checkpoints       → meshflow approve <run_id> <node_id>
✓ Compliance reports     → meshflow compliance report --framework hipaa
```

---

## Adding compliance (AutoGen had nothing here)

```python
# Standard — production default
mesh = Mesh(agents=[...], compliance="standard")

# HIPAA — adds PHI detection, HITL on critical steps, 7-year retention
mesh = Mesh(agents=[...], compliance="hipaa")

# SOX — immutable ledger, financial HITL gates
mesh = Mesh(agents=[...], compliance="sox")
```

---

## YAML equivalent of AutoGen GroupChat

AutoGen has no YAML support. MeshFlow lets you define the same pattern in config:

```yaml
kind: workflow
name: dev-team
policy: standard

nodes:
  - id: pm
    type: native
    role: planner
    model: claude-sonnet-4-6

  - id: engineer
    type: native
    role: executor
    model: claude-sonnet-4-6

  - id: qa
    type: native
    role: critic
    model: claude-haiku-4-5-20251001

edges:
  - from: pm
    to: engineer
  - from: engineer
    to: qa

budget:
  cost_usd: 2.00
```

Run it:
```bash
meshflow run workflow.yaml --task "Build a rate limiter in Python"
```

---

## Durable execution (AutoGen has no equivalent)

AutoGen workflows restart from scratch on any failure. MeshFlow checkpoints every step:

```python
from meshflow.core.durable import DurableWorkflowExecutor

executor = DurableWorkflowExecutor(db="runs.db")

# First run
result = executor.run(workflow_path="workflow.yaml", input="Build a rate limiter")

# If it crashes mid-run — resume from last checkpoint
result = executor.resume(run_id="run-abc123")
```

---

## Cost controls (AutoGen has no equivalent)

```python
from meshflow import Team, CostCap

team = Team(
    agents=[pm, engineer, qa],
    pattern="supervised",
    cost_cap=CostCap(usd=1.50),          # hard stop at $1.50
    stop_on_confidence=0.90,              # exit early when agent is confident
)
```

---

## Getting help

- Docs: [meshflow.dev/docs](https://meshflow.dev/docs)
- Migration support: open an issue at [github.com/Anteneh-T-Tessema/meshflow](https://github.com/Anteneh-T-Tessema/meshflow)
- CLI quickstart: `pip install meshflow && meshflow init`
