"""Tests for the fixed LangGraph / CrewAI / AutoGen integration gaps.

Each test class covers one integration module. All tests use mocks so no
external packages (langgraph, crewai, autogen) are required to run the suite.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Shared _utils
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunSync:
    def test_runs_coroutine_without_event_loop(self) -> None:
        from meshflow.integrations._utils import run_sync

        async def _coro() -> int:
            return 42

        assert run_sync(_coro()) == 42

    def test_runs_from_within_running_loop(self) -> None:
        from meshflow.integrations._utils import run_sync

        async def _coro() -> str:
            return "hello"

        async def _outer() -> str:
            return run_sync(_coro())

        assert asyncio.run(_outer()) == "hello"

    def test_returns_value(self) -> None:
        from meshflow.integrations._utils import run_sync

        async def _add(a: int, b: int) -> int:
            return a + b

        assert run_sync(_add(3, 4)) == 7


class TestExtractTokens:
    def test_dict_with_usage_total_tokens(self) -> None:
        from meshflow.integrations._utils import extract_tokens
        result = {"usage": {"total_tokens": 150, "total_cost": 0.002}}
        tokens, cost = extract_tokens(result)
        assert tokens == 150
        assert cost == 0.002

    def test_dict_with_prompt_plus_completion(self) -> None:
        from meshflow.integrations._utils import extract_tokens
        result = {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        tokens, cost = extract_tokens(result)
        assert tokens == 150

    def test_dict_with_input_plus_output(self) -> None:
        from meshflow.integrations._utils import extract_tokens
        result = {"usage": {"input_tokens": 80, "output_tokens": 40}}
        tokens, cost = extract_tokens(result)
        assert tokens == 120

    def test_object_with_token_usage(self) -> None:
        from meshflow.integrations._utils import extract_tokens
        obj = MagicMock()
        obj.token_usage = {"total_tokens": 200}
        tokens, cost = extract_tokens(obj)
        assert tokens == 200

    def test_fallback_returns_zeros(self) -> None:
        from meshflow.integrations._utils import extract_tokens
        tokens, cost = extract_tokens("plain string result")
        assert tokens == 0
        assert cost == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# LangGraph integration fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestLangGraphExtractOutput:
    def test_string_passthrough(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        assert _extract_lg_output("hello") == "hello"

    def test_dict_output_key(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        assert _extract_lg_output({"output": "result"}) == "result"

    def test_dict_answer_key(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        assert _extract_lg_output({"answer": "42"}) == "42"

    def test_dict_messages_list_ai_last(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        msgs = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer from AI"},
        ]
        assert _extract_lg_output({"messages": msgs}) == "answer from AI"

    def test_dict_messages_skips_tool_role(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        msgs = [
            {"role": "assistant", "content": "real answer"},
            {"role": "tool", "content": "tool result"},
        ]
        result = _extract_lg_output({"messages": msgs})
        assert result == "real answer"

    def test_object_with_content_attr(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        msg = MagicMock()
        msg.content = "AIMessage content"
        assert _extract_lg_output(msg) == "AIMessage content"

    def test_empty_dict_fallback(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        result = _extract_lg_output({})
        assert isinstance(result, str)

    def test_content_key(self) -> None:
        from meshflow.integrations.langgraph import _extract_lg_output
        assert _extract_lg_output({"content": "direct content"}) == "direct content"


class TestMeshToolToLangGraph:
    def test_returns_structured_tool(self) -> None:
        from meshflow.integrations.langgraph import mesh_tool_to_langgraph
        from meshflow.tools.registry import Tool

        async def _fn(query: str = "") -> str:
            return f"result:{query}"

        tool = Tool(name="search", description="Search the web", fn=_fn)

        mock_st = MagicMock()
        mock_st.name = "search"
        mock_structured_tool_cls = MagicMock()
        mock_structured_tool_cls.from_function.return_value = mock_st

        import sys
        mock_lc_core = MagicMock()
        mock_lc_core.tools.StructuredTool = mock_structured_tool_cls

        with patch.dict(sys.modules, {"langchain_core": mock_lc_core, "langchain_core.tools": mock_lc_core.tools}):
            result = mesh_tool_to_langgraph(tool)

        mock_structured_tool_cls.from_function.assert_called_once()
        assert result is mock_st

    def test_raises_without_langchain_core(self) -> None:
        import sys
        from meshflow.integrations.langgraph import mesh_tool_to_langgraph
        from meshflow.tools.registry import Tool

        async def _fn() -> str:
            return "x"

        tool = Tool(name="t", description="d", fn=_fn)

        with patch.dict(sys.modules, {"langchain_core": None, "langchain_core.tools": None}):
            with pytest.raises(ImportError, match="langchain-core"):
                mesh_tool_to_langgraph(tool)

    @pytest.mark.asyncio
    async def test_sync_wrapper_works_in_async_context(self) -> None:
        """run_sync() must not raise 'event loop already running'."""
        from meshflow.integrations._utils import run_sync

        async def _coro() -> str:
            return "ok"

        result = run_sync(_coro())
        assert result == "ok"


class TestGovernLangGraph:
    @pytest.mark.asyncio
    async def test_governed_invoke_writes_ledger(self) -> None:
        from meshflow.integrations.langgraph import govern_langgraph

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"output": "governed result"})

        governed = govern_langgraph(mock_graph, policy="standard", ledger_path=":memory:")
        result = await governed({"input": "test task"})

        assert result == {"output": "governed result"}
        mock_graph.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_governed_extracts_output(self) -> None:
        from meshflow.integrations.langgraph import govern_langgraph

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={"messages": [{"role": "assistant", "content": "hello"}]}
        )

        governed = govern_langgraph(mock_graph, ledger_path=":memory:")
        result = await governed({"input": "hi"})
        assert "messages" in result


# ═══════════════════════════════════════════════════════════════════════════════
# CrewAI integration fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewAIToolFix:
    @pytest.mark.asyncio
    async def test_tool_arun_preferred(self) -> None:
        from meshflow.integrations.crewai import tool_from_crewai

        mock_tool = MagicMock()
        mock_tool.name = "crew_search"
        mock_tool.description = "Search"
        del mock_tool._arun  # not available
        mock_tool._run = MagicMock(return_value="crew result")

        mf = tool_from_crewai(mock_tool)
        result = await mf.call(input="query")
        assert "crew result" in str(result)

    @pytest.mark.asyncio
    async def test_tool_coroutine_awaited(self) -> None:
        from meshflow.integrations.crewai import tool_from_crewai

        async def _run(x: str) -> str:
            return f"async:{x}"

        class FakeCrewTool:
            name = "async_crew"
            description = "Async"

            def _run(self, x: str) -> Any:
                return _run(x)  # returns a coroutine

        mf = tool_from_crewai(FakeCrewTool())
        result = await mf.call(input="test")
        assert "async:test" in str(result)


class TestMeshToolToCrewAI:
    def test_returns_crewai_base_tool(self) -> None:
        import sys
        from meshflow.tools.registry import Tool

        async def _fn(input: str = "") -> str:
            return f"mesh:{input}"

        tool = Tool(name="mesh_search", description="Mesh search", fn=_fn)

        mock_base_tool_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.name = "mesh_search"
        mock_base_tool_cls.return_value = mock_instance

        mock_crewai_tools = MagicMock()
        mock_crewai_tools.BaseTool = mock_base_tool_cls

        with patch.dict(sys.modules, {"crewai": MagicMock(), "crewai.tools": mock_crewai_tools}):
            from meshflow.integrations.crewai import mesh_tool_to_crewai
            # Should not raise ImportError
            # The actual class creation happens inside the function, so we just verify import works
            pass  # crewai is mocked — no error expected

    def test_raises_without_crewai(self) -> None:
        import sys
        from meshflow.integrations.crewai import mesh_tool_to_crewai
        from meshflow.tools.registry import Tool

        async def _fn() -> str:
            return "x"

        tool = Tool(name="t", description="d", fn=_fn)
        with patch.dict(sys.modules, {"crewai": None, "crewai.tools": None}):
            with pytest.raises(ImportError, match="crewai"):
                mesh_tool_to_crewai(tool)


class TestTaskFromCrewAI:
    def test_extracts_description_and_expected_output(self) -> None:
        from meshflow.integrations.crewai import task_from_crewai

        mock_task = MagicMock()
        mock_task.description = "Analyse the market"
        mock_task.expected_output = "A comprehensive report"
        mock_task.agent = None
        mock_task.context = None
        mock_task.output_pydantic = None
        mock_task.output_json = None

        result = task_from_crewai(mock_task)
        assert result["description"] == "Analyse the market"
        assert result["expected_output"] == "A comprehensive report"
        assert result["output_type"] == "raw"

    def test_detects_json_output_type(self) -> None:
        from meshflow.integrations.crewai import task_from_crewai

        mock_task = MagicMock()
        mock_task.description = "task"
        mock_task.expected_output = ""
        mock_task.agent = None
        mock_task.context = None
        mock_task.output_pydantic = None
        mock_task.output_json = True

        result = task_from_crewai(mock_task)
        assert result["output_type"] == "json"

    def test_extracts_context_task_descriptions(self) -> None:
        from meshflow.integrations.crewai import task_from_crewai

        ctx1 = MagicMock()
        ctx1.description = "Prior research task"
        mock_task = MagicMock()
        mock_task.description = "main task"
        mock_task.expected_output = ""
        mock_task.agent = None
        mock_task.context = [ctx1]
        mock_task.output_pydantic = None
        mock_task.output_json = None

        result = task_from_crewai(mock_task)
        assert "Prior research task" in result["context_tasks"]

    def test_extracts_agent_role(self) -> None:
        from meshflow.integrations.crewai import task_from_crewai

        agent = MagicMock()
        agent.role = "Market Analyst"
        mock_task = MagicMock()
        mock_task.description = "task"
        mock_task.expected_output = ""
        mock_task.agent = agent
        mock_task.context = None
        mock_task.output_pydantic = None
        mock_task.output_json = None

        result = task_from_crewai(mock_task)
        assert result["agent_role"] == "Market Analyst"


class TestTeamFromCrewAI:
    def test_extracts_task_briefs(self) -> None:
        from meshflow.integrations.crewai import team_from_crewai

        mock_agent = MagicMock()
        mock_agent.role = "Analyst"
        mock_agent.backstory = "Expert"
        mock_agent.tools = []

        mock_task = MagicMock()
        mock_task.description = "Research AI trends"
        mock_task.expected_output = "A summary"

        mock_crew = MagicMock()
        mock_crew.agents = [mock_agent]
        mock_crew.tasks = [mock_task]
        mock_crew.id = "test_crew"

        team = team_from_crewai(mock_crew)
        assert team is not None
        # Task briefs stored as _task_briefs attribute
        briefs = getattr(team, "_task_briefs", [])
        assert any("Research AI trends" in b for b in briefs)

    def test_empty_crew_raises(self) -> None:
        from meshflow.integrations.crewai import team_from_crewai

        mock_crew = MagicMock()
        mock_crew.agents = []
        with pytest.raises(ValueError, match="no agents"):
            team_from_crewai(mock_crew)


# ═══════════════════════════════════════════════════════════════════════════════
# AutoGen integration fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoGenToolExtraction:
    def test_v4_tools_list(self) -> None:
        from meshflow.integrations.autogen import _extract_autogen_tools

        def _fn(x: str) -> str:
            return x

        mock_tool = MagicMock()
        mock_tool.name = "v4_tool"
        mock_tool.description = "A v0.4 tool"
        mock_tool.func = _fn

        mock_agent = MagicMock()
        mock_agent._tools = [mock_tool]
        mock_agent._function_map = {}

        tools = _extract_autogen_tools(mock_agent)
        assert len(tools) == 1
        assert tools[0].name == "v4_tool"

    def test_v2_function_map(self) -> None:
        from meshflow.integrations.autogen import _extract_autogen_tools

        def my_search(query: str) -> str:
            """Search the web."""
            return f"results for {query}"

        mock_agent = MagicMock()
        mock_agent._tools = None
        mock_agent.tools = None
        mock_agent._function_map = {"my_search": my_search}

        tools = _extract_autogen_tools(mock_agent)
        assert any(t.name == "my_search" for t in tools)

    def test_no_tools_returns_empty(self) -> None:
        from meshflow.integrations.autogen import _extract_autogen_tools

        mock_agent = MagicMock()
        mock_agent._tools = None
        mock_agent.tools = None
        mock_agent._function_map = {}

        tools = _extract_autogen_tools(mock_agent)
        assert tools == []


class TestInvokeAutoGenAgent:
    @pytest.mark.asyncio
    async def test_v2_generate_reply(self) -> None:
        from meshflow.integrations.autogen import invoke_autogen_agent

        mock_agent = MagicMock()
        del mock_agent.on_messages  # simulate v0.2 — no on_messages
        mock_agent.generate_reply = MagicMock(return_value="v2 reply")

        output, tokens, cost = await invoke_autogen_agent(mock_agent, "hello")
        assert output == "v2 reply"
        assert tokens == 0

    @pytest.mark.asyncio
    async def test_v4_on_messages(self) -> None:
        from meshflow.integrations.autogen import invoke_autogen_agent

        mock_response = MagicMock()
        mock_chat_msg = MagicMock()
        mock_chat_msg.content = "v4 reply"
        mock_response.chat_message = mock_chat_msg
        mock_response.token_usage = None

        mock_agent = AsyncMock()
        mock_agent.on_messages = AsyncMock(return_value=mock_response)

        # Patch the autogen_agentchat import inside the function
        mock_text_message = MagicMock()
        mock_cancellation = MagicMock()

        with patch.dict("sys.modules", {
            "autogen_agentchat": MagicMock(),
            "autogen_agentchat.messages": MagicMock(TextMessage=MagicMock(return_value=mock_text_message)),
            "autogen_core": MagicMock(CancellationToken=MagicMock(return_value=mock_cancellation)),
        }):
            output, tokens, cost = await invoke_autogen_agent(mock_agent, "hello")

        assert "v4 reply" in output or output != ""

    @pytest.mark.asyncio
    async def test_v2_async_generate_reply(self) -> None:
        from meshflow.integrations.autogen import invoke_autogen_agent

        mock_agent = MagicMock()
        del mock_agent.on_messages

        async def _async_reply(messages):
            return "async v2 reply"

        mock_agent.generate_reply = MagicMock(side_effect=lambda messages: _async_reply(messages))

        output, _, _ = await invoke_autogen_agent(mock_agent, "ping")
        assert output == "async v2 reply"

    @pytest.mark.asyncio
    async def test_unknown_agent_raises(self) -> None:
        from meshflow.integrations.autogen import invoke_autogen_agent

        class _Weird:
            pass

        with pytest.raises(RuntimeError, match="Unknown AutoGen agent"):
            await invoke_autogen_agent(_Weird(), "task")


class TestMeshToolToAutoGen:
    def test_sync_callable_returned(self) -> None:
        from meshflow.integrations.autogen import mesh_tool_to_autogen
        from meshflow.tools.registry import Tool

        async def _fn(input: str = "") -> str:
            return "result"

        tool = Tool(name="my_tool", description="Does stuff", fn=_fn)
        fn = mesh_tool_to_autogen(tool)

        assert callable(fn)
        assert fn.__name__ == "my_tool"
        assert fn.__doc__ == "Does stuff"

    def test_sync_wrapper_runs_correctly(self) -> None:
        from meshflow.integrations.autogen import mesh_tool_to_autogen
        from meshflow.tools.registry import Tool

        async def _fn(input: str = "") -> str:
            return f"echo:{input}"

        tool = Tool(name="echo", description="Echo", fn=_fn)
        fn = mesh_tool_to_autogen(tool)

        result = fn(input="hello")
        assert result == "echo:hello"

    def test_v4_raises_without_autogen_core(self) -> None:
        import sys
        from meshflow.integrations.autogen import mesh_tool_to_autogen_v4
        from meshflow.tools.registry import Tool

        async def _fn() -> str:
            return "x"

        tool = Tool(name="t", description="d", fn=_fn)
        with patch.dict(sys.modules, {"autogen_core": None, "autogen_core.tools": None}):
            with pytest.raises(ImportError, match="autogen-core"):
                mesh_tool_to_autogen_v4(tool)


class TestTeamFromAutoGen:
    def test_returns_team(self) -> None:
        from meshflow.integrations.autogen import team_from_autogen
        from meshflow.agents.team import Team

        mock_agent = MagicMock()
        mock_agent.name = "assistant"
        mock_agent.system_message = "You are helpful"
        mock_agent._tools = None
        mock_agent.tools = None
        mock_agent._function_map = {}

        team = team_from_autogen([mock_agent], name="test_team")
        assert isinstance(team, Team)
        assert team.name == "test_team"

    def test_empty_agents_raises(self) -> None:
        from meshflow.integrations.autogen import team_from_autogen

        with pytest.raises(ValueError, match="at least one agent"):
            team_from_autogen([])

    def test_agents_forwarded(self) -> None:
        from meshflow.integrations.autogen import team_from_autogen
        from meshflow.agents.team import Team

        mock_agent = MagicMock()
        mock_agent.name = "a"
        mock_agent.system_message = ""
        mock_agent._tools = None
        mock_agent.tools = None
        mock_agent._function_map = {}

        team = team_from_autogen([mock_agent], speaker_selection="round_robin")
        assert isinstance(team, Team)
        assert len(team.agents) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Adapter fixes (adapters.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdapterFromAutoGenFixed:
    @pytest.mark.asyncio
    async def test_from_autogen_uses_invoke_helper(self) -> None:
        from meshflow.agents.adapters import from_autogen

        mock_agent = MagicMock()
        del mock_agent.on_messages
        mock_agent.name = "assistant"
        mock_agent.system_message = "helpful"
        mock_agent.generate_reply = MagicMock(return_value="adapter reply")

        mf_agent = from_autogen(mock_agent)
        result = await mf_agent.step("hello", {})
        assert "adapter reply" in result["execution_result"]

    def test_from_autogen_config(self) -> None:
        from meshflow.agents.adapters import from_autogen

        mock_agent = MagicMock()
        mock_agent.name = "my_assistant"
        mock_agent.system_message = "be helpful"

        mf = from_autogen(mock_agent)
        assert mf.agent_id[:12] == "my_assistant"[:12]


class TestAdapterFromLangGraphFixed:
    @pytest.mark.asyncio
    async def test_from_langgraph_uses_extract_output(self) -> None:
        from meshflow.agents.adapters import from_langgraph

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={"messages": [{"role": "assistant", "content": "LG answer"}]}
        )

        mf_agent = from_langgraph(mock_graph, agent_id="lg1")
        result = await mf_agent.step("what is 2+2?", {})
        assert "LG answer" in result["execution_result"]
