"""MeshFlow integrations — two-way bridges to every major agent ecosystem.

Import agents and tools FROM any framework into MeshFlow:

    from meshflow.integrations.langgraph import tool_from_langgraph, agent_from_langgraph
    from meshflow.integrations.crewai    import tool_from_crewai, team_from_crewai
    from meshflow.integrations.autogen   import tool_from_autogen, agent_from_autogen
    from meshflow.integrations.a2a       import A2AClient, A2AServer
    from meshflow.integrations.mcp_tools import tools_from_mcp_server
    from meshflow.integrations.ibm       import agent_from_watsonx
    from meshflow.integrations.openai    import agent_from_openai_assistant

All adapters degrade gracefully when the target framework is not installed.
"""
