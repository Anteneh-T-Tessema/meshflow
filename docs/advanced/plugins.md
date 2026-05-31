# Plugin System

MeshFlow discovers plugins via Python entry points — no configuration required.

## Discover and load

```python
from meshflow import discover_plugins, load_plugin, verify_plugin, PluginInfo

# Discover all installed plugins
plugins: list[PluginInfo] = discover_plugins()
for p in plugins:
    print(p.name, p.version, p.entry_point)

# Load a specific plugin
plugin = load_plugin("meshflow-rag-haystack")

# Verify (check dependencies, API compatibility)
issues = verify_plugin("meshflow-rag-haystack")
if issues:
    print("Plugin issues:", issues)
```

## Entry point groups

| Group | Registers |
|-------|-----------|
| `meshflow.tools` | Additional `Tool` objects |
| `meshflow.providers` | Additional `LLMProvider` classes |
| `meshflow.guardrails` | Additional `Guardrail` classes |
| `meshflow.compliance` | Additional compliance profiles |

## Write a plugin

```toml
# pyproject.toml of your plugin package
[project.entry-points."meshflow.tools"]
my_special_tool = "my_package.tools:my_special_tool"

[project.entry-points."meshflow.guardrails"]
my_guardrail = "my_package.guardrails:MyCustomGuardrail"
```

```python
# my_package/tools.py
from meshflow import tool, RiskTier

@tool(name="my_special_tool", risk=RiskTier.EXTERNAL_IO)
async def my_special_tool(query: str) -> str:
    """Call my external service."""
    return await call_my_service(query)
```

After `pip install my-plugin-package`, it is auto-discovered on the next `discover_plugins()` call or server restart.

## Via CLI

```bash
meshflow plugins list           # all discovered plugins
meshflow plugins verify <name>  # check compatibility
```
