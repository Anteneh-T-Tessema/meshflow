# MeshFlow as an MCP Tool — Client Setup Guide

Connect MeshFlow to any MCP-compatible LLM client. Every tool call MeshFlow receives runs through the full governance stack: compliance profiles, cost governance, Zero Trust, SHA-256 audit chain.

---

## Claude Desktop

**1. Install MeshFlow**

```bash
pip install meshflow
```

**2. Edit `claude_desktop_config.json`**

Location:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "meshflow-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY_HERE"
      }
    }
  }
}
```

**Sandbox mode (no API key, for testing):**

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "meshflow-mcp",
      "env": {
        "MESHFLOW_MOCK": "1"
      }
    }
  }
}
```

**3. Restart Claude Desktop.** The MeshFlow tools will appear in the tool picker.

**Available tools in Claude Desktop:**

- `meshflow_run` — Run any task through a governed multi-agent pipeline
- `meshflow_run_agent` — Call a specific registered agent by name
- `meshflow_approve_hitl` — Approve or reject a paused human-in-the-loop checkpoint

**Example prompt to Claude:**
> "Use MeshFlow to research and summarise the latest EU AI Act compliance requirements, with GDPR mode enabled."

---

## Cursor

**1. Install MeshFlow**

```bash
pip install meshflow
```

**2. Add to Cursor settings** (`~/.cursor/mcp.json` or Cursor → Settings → MCP):

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "meshflow-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY_HERE"
      }
    }
  }
}
```

**3. Restart Cursor.** MeshFlow tools are now available in Composer and chat.

---

## Zed

**1. Install MeshFlow**

```bash
pip install meshflow
```

**2. Add to `~/.config/zed/settings.json`:**

```json
{
  "context_servers": {
    "meshflow": {
      "command": {
        "path": "meshflow-mcp",
        "args": [],
        "env": {
          "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY_HERE"
        }
      }
    }
  }
}
```

---

## Continue.dev

**1. Install MeshFlow**

```bash
pip install meshflow
```

**2. Add to `~/.continue/config.json`:**

```json
{
  "mcpServers": [
    {
      "name": "meshflow",
      "command": "meshflow-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY_HERE"
      }
    }
  ]
}
```

---

## Without `pip install` — using `uvx`

No installation required. Any MCP client that supports shell commands can use `uvx`:

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "uvx",
      "args": ["meshflow", "mcp-stdio"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY_HERE"
      }
    }
  }
}
```

---

## Custom agents via config file

To expose specific agents as MCP tools rather than the default generic `meshflow_run`:

**1. Create `meshflow.yaml`:**

```yaml
version: "1.0"

agents:
  - name: clinical-researcher
    role: researcher
    model: claude-sonnet-4-6
    policy: hipaa
    system_prompt: "You are a HIPAA-compliant clinical research assistant."

  - name: financial-analyst
    role: executor
    model: claude-sonnet-4-6
    policy: sox
    system_prompt: "You are a SOX-compliant financial analyst."
```

**2. Point the MCP server at it:**

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "meshflow-mcp",
      "args": ["--config", "/path/to/meshflow.yaml"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY_HERE"
      }
    }
  }
}
```

Each agent in the config becomes its own tool: `clinical_researcher`, `financial_analyst`, etc.

---

## Using MeshFlow as an Anthropic API tool (no MCP required)

```python
from meshflow.integrations.anthropic import (
    meshflow_as_anthropic_tool,
    meshflow_tool_handler,
    meshflow_tool_result_block,
)
import anthropic

client = anthropic.Anthropic()
tool = meshflow_as_anthropic_tool(include_policy_param=True)

messages = [{"role": "user", "content": "Research the latest HIPAA requirements"}]

while True:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        tools=[tool],
        messages=messages,
    )

    if response.stop_reason == "end_turn":
        print(response.content[0].text)
        break

    if response.stop_reason == "tool_use":
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await meshflow_tool_handler(block.name, block.input)
                tool_results.append(meshflow_tool_result_block(block.id, result))
        messages.append({"role": "user", "content": tool_results})
```

---

## Using MeshFlow as an OpenAI tool

```python
from meshflow.integrations.openai import meshflow_as_openai_tool
from meshflow import Mesh, policy_for_mode
import openai, json, asyncio

client = openai.OpenAI()
tool = meshflow_as_openai_tool()

response = client.chat.completions.create(
    model="gpt-4o",
    tools=[tool],
    messages=[{"role": "user", "content": "Research AI safety compliance frameworks"}],
)

if response.choices[0].finish_reason == "tool_calls":
    tc = response.choices[0].message.tool_calls[0]
    args = json.loads(tc.function.arguments)

    # Run through MeshFlow
    result = asyncio.run(Mesh().run(args["task"]))
    print(result.summary())
```

---

## Verifying the audit trail

After any MeshFlow tool call, verify the tamper-evident chain:

```bash
meshflow audit export --run-id <run_id> --format json --out run.json
meshflow audit verify-chain --run-id <run_id>
# or with the reference verifier (no meshflow import needed):
python meshflow_verify_chain.py run.json
```

The reference verifier is defined in [docs/audit_chain_spec.md](../audit_chain_spec.md).
