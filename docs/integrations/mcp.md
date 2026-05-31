# MCP Integration

MeshFlow can both **expose** agent tools as an MCP server and **consume** external MCP servers as agent tools.

## Expose tools as MCP server

```python
from meshflow import MCPServer, MCPToolEntry, mcp_from_config

# Build from registered tools
server = MCPServer(
    name="my-agent-tools",
    tools=[web_search, calculator, read_file],
    port=3000,
)
server.start()

# From config dict
server = mcp_from_config({
    "name": "my-tools",
    "port": 3000,
    "tools": ["web_search", "calculator"],
})
```

Exposes standard MCP endpoints: `tools/list`, `tools/call`.

## Consume external MCP servers

### HTTP SSE server

```python
from meshflow import Agent

agent = Agent(
    name="mcp-agent",
    role="executor",
    mcps=["https://mcp.example.com/sse"],  # HTTP SSE MCP server
)
# All tools from the MCP server are auto-discovered and added
result = await agent.run("Search for recent AI papers")
```

### Stdio server

```python
from meshflow.mcp.client import StdioServerParams

agent = Agent(
    name="local-mcp",
    role="executor",
    mcps=[StdioServerParams(command="npx", args=["@my/mcp-server"])],
)
```

## MCPClient (low-level)

```python
from meshflow import MCPClient, MCPClientSession, MCPRemoteTool, MCPClientError

async with MCPClient("https://mcp.example.com/sse") as client:
    session: MCPClientSession = await client.connect()
    
    tools: list[MCPRemoteTool] = await session.list_tools()
    for tool in tools:
        print(tool.name, tool.description)
    
    result = await session.call_tool("web_search", {"query": "MeshFlow docs"})
    print(result.content)
```

## Multiple MCP servers

```python
agent = Agent(
    name="multi-mcp",
    role="executor",
    mcps=[
        "https://browser-mcp.example.com/sse",    # browser automation
        "https://database-mcp.example.com/sse",   # SQL queries
        StdioServerParams(command="python", args=["local_tools.py"]),
    ],
)
```
