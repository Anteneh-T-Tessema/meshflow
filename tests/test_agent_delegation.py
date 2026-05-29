"""Tests for dynamic LLM-driven delegation between agents."""

import pytest
from unittest.mock import AsyncMock
from meshflow import Agent, AgentRole


@pytest.mark.asyncio
async def test_agent_delegation_tool_registration():
    # Create target delegate agent
    delegate = Agent(name="delegate_agent", role=AgentRole.EXECUTOR)
    
    # Create source agent with the delegate
    source = Agent(name="source_agent", role=AgentRole.PLANNER, delegates=[delegate])
    
    # Build the source agent
    built_source = source._build()
    
    # Verify that the delegation tools exist
    tool_names = [t.name for t in built_source._tools]
    assert "delegate_to_delegate_agent" in tool_names
    assert "ask_question_to_delegate_agent" in tool_names
    
    # Find the tools
    delegate_tool = next(t for t in built_source._tools if t.name == "delegate_to_delegate_agent")
    ask_tool = next(t for t in built_source._tools if t.name == "ask_question_to_delegate_agent")
    
    # Mock the delegate.run method to return a predictable response
    delegate.run = AsyncMock(return_value={"result": "delegate response"})
    
    # Call the delegate tool
    res_delegate = await delegate_tool.call(task="perform subtask")
    assert res_delegate == "delegate response"
    delegate.run.assert_called_with("perform subtask")
    
    # Call the ask tool
    delegate.run.reset_mock()
    res_ask = await ask_tool.call(question="how are you?")
    assert res_ask == "delegate response"
    delegate.run.assert_called_with("how are you?")
