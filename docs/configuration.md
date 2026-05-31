# Configuration

MeshFlow can be configured with environment variables, a `meshflow.yaml` file, or both.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API key. Required when using Claude models. |
| `OPENAI_API_KEY` | — | OpenAI API key. Required when using GPT models. |
| `MESHFLOW_MODEL` | `claude-sonnet-4-6` | Default model used when `model=` is not set on an agent. |
| `MESHFLOW_MOCK` | `0` | Set to `1` to run all agents in mock/sandbox mode (no LLM calls). |

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export MESHFLOW_MODEL=claude-sonnet-4-6

# Offline testing — no API key needed
MESHFLOW_MOCK=1 python my_script.py
```

---

## meshflow.yaml

Define your entire multi-agent system declaratively. `load()` returns a runnable `MeshFlowConfig`.

```python
from meshflow.core.config import load

config = load("meshflow.yaml")
result = await config.run("Build a research report on LLMs")
```

### Full example

```yaml
version: "1.0"

policy:
  mode: regulated       # dev | standard | regulated | legal-critical | hipaa
  budget_usd: 5.0
  max_steps: 20

agents:
  - name: planner
    role: planner
    model: claude-sonnet-4-6

  - name: researcher
    role: researcher
    model: claude-sonnet-4-6
    memory: true
    tools:
      - web_search
      - read_file

  - name: writer
    role: executor
    model: claude-haiku-4-5-20251001
    system_prompt: "Write clear, concise reports."

  - name: critic
    role: critic
    model: claude-sonnet-4-6

team:
  name: research_team
  pattern: supervised        # sequential | parallel | hierarchical | supervised | reflective
  agents: [planner, researcher, writer, critic]
```

### With a workflow graph instead of a team

```yaml
version: "1.0"

agents:
  - name: ingest
    role: researcher
    model: claude-sonnet-4-6

  - name: analyze
    role: executor
    model: claude-sonnet-4-6

  - name: report
    role: executor
    model: claude-haiku-4-5-20251001

workflow:
  name: pipeline
  nodes:
    - id: ingest
      agent: ingest
    - id: analyze
      agent: analyze
    - id: report
      agent: report
  edges:
    - from: ingest
      to: analyze
    - from: analyze
      to: report
  terminal: report
```

### With a GroupChat

```yaml
version: "1.0"

agents:
  - name: researcher
    role: researcher
    model: claude-sonnet-4-6
  - name: coder
    role: executor
    model: claude-sonnet-4-6

groupchat:
  agents: [researcher, coder]
  max_turns: 12
  speaker_selection: round_robin
  termination: "TERMINATE"
```

---

## Environment variable expansion

`${VAR}` references in YAML values are expanded from the environment automatically:

```yaml
version: "1.0"

agents:
  - name: researcher
    role: researcher
    model: ${MESHFLOW_MODEL}
```

Pass `env_expand=False` to `load()` to disable this.

---

## load() and loads()

```python
from meshflow.core.config import load, loads

# From a file
config = load("meshflow.yaml")
config = load("meshflow.yaml", env_expand=False)   # skip ${VAR} expansion

# From a string (useful in tests)
yaml_text = """
version: "1.0"
agents:
  - name: agent
    role: executor
    model: claude-sonnet-4-6
"""
config = loads(yaml_text)
```

### MeshFlowConfig attributes

| Attribute | Type | Description |
|---|---|---|
| `policy` | `Policy` | Resolved governance policy |
| `agents` | `dict[str, Agent]` | Name → Agent mapping |
| `team` | `Team \| None` | Team object if `team:` is declared |
| `workflow` | `WorkflowDefinition \| None` | Workflow graph if `workflow:` is declared |
| `groupchat` | `GroupChatManager \| None` | GroupChat if `groupchat:` is declared |
| `raw` | `dict` | Original parsed YAML |

### Policy modes

| Mode | Use case |
|---|---|
| `dev` | Development — relaxed limits |
| `standard` | General production workloads |
| `regulated` | Financial, healthcare, legal |
| `legal-critical` | Legal review, highest strictness |
| `hipaa` | PHI handling, HIPAA-specific controls |

!!! tip
    When neither `team:`, `workflow:`, nor `groupchat:` is declared, MeshFlow automatically creates a sequential team of all defined agents.
