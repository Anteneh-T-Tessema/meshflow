"""MeshFlow ACP (Agent Communication Protocol) bridge.

ACP is IBM's open agent communication protocol (BeeAI / IBM Research).
This module implements an ACP-compatible bridge that lets MeshFlow agents
participate in ACP ecosystems alongside IBM BeeAI agents and other
ACP-compliant runtimes.

ACP defines:
  - Agent discovery (/.well-known/acp)
  - Run lifecycle (POST /runs, GET /runs/{id}, DELETE /runs/{id})
  - Streaming output (GET /runs/{id}/events — SSE)
  - Tool/task schema (JSON Schema agent card)

References:
  - https://github.com/i-am-bee/acp
  - https://github.com/i-am-bee/beeai-framework

Usage::

    from meshflow.acp import ACPServer, ACPClient, ACPAgentCard

    # Expose a MeshFlow agent as an ACP server
    agent = Agent(name="researcher", role="researcher")
    server = ACPServer(agent, port=8001)
    server.start()
    # Discoverable at http://localhost:8001/.well-known/acp

    # Call a remote ACP agent from MeshFlow
    client = ACPClient("http://remote-beeai-agent:8001")
    result = await client.run("Summarise this document.")

    # Use as a MeshFlow tool (wraps any ACP endpoint as a @tool)
    from meshflow.acp import acp_tool
    beeai_researcher = acp_tool("http://beeai-host:8001", name="beeai_researcher")
    agent = Agent(name="planner", role="planner", tools=[beeai_researcher])
"""

from meshflow.acp.bridge import ACPServer, ACPClient, ACPAgentCard, acp_tool

__all__ = ["ACPServer", "ACPClient", "ACPAgentCard", "acp_tool"]
