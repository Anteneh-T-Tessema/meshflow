"""Sprint 100 — AutoGen 0.4+ parity (AssistantAgent, UserProxyAgent,
SocietyOfMind, MagenticOne, AgentRuntime, termination conditions, topic pub/sub)
and OpenAI Agents SDK parity (Agent, Runner, handoff, built-in tools,
AgentHooks, guardrails, trace/custom_span, FunctionTool, as_tool()).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.agents.base import EchoProvider


def _echo_provider(reply: str = "ok") -> EchoProvider:
    return EchoProvider(reply)


# ══════════════════════════════════════════════════════════════════════════════
# AutoGen 0.4+ — Message types
# ══════════════════════════════════════════════════════════════════════════════

class TestAutogenMessages:
    def test_text_message_fields(self) -> None:
        from meshflow.agents.autogen_v4 import TextMessage
        m = TextMessage(content="hello", source="user")
        assert m.content == "hello"
        assert m.source == "user"
        assert m.type == "TextMessage"

    def test_chat_message_alias(self) -> None:
        from meshflow.agents.autogen_v4 import ChatMessage
        m = ChatMessage(content="hi", role="user")
        assert m.role == "user"

    def test_tool_call_message(self) -> None:
        from meshflow.agents.autogen_v4 import ToolCallMessage
        m = ToolCallMessage(content=[{"name": "search", "args": {}}])
        assert m.type == "ToolCallMessage"

    def test_tool_call_result_message(self) -> None:
        from meshflow.agents.autogen_v4 import ToolCallResultMessage
        m = ToolCallResultMessage(content=[{"result": "found it"}])
        assert m.type == "ToolCallResultMessage"

    def test_exported_from_meshflow(self) -> None:
        from meshflow import TextMessage, AutoGenChatMessage, ToolCallMessage, ToolCallResultMessage
        assert TextMessage is not None
        assert AutoGenChatMessage is not None


# ══════════════════════════════════════════════════════════════════════════════
# CancellationToken
# ══════════════════════════════════════════════════════════════════════════════

class TestCancellationToken:
    def test_not_cancelled_by_default(self) -> None:
        from meshflow.agents.autogen_v4 import CancellationToken
        t = CancellationToken()
        assert not t.cancelled

    def test_cancel_sets_flag(self) -> None:
        from meshflow.agents.autogen_v4 import CancellationToken
        t = CancellationToken()
        t.cancel()
        assert t.cancelled

    def test_callback_called_on_cancel(self) -> None:
        from meshflow.agents.autogen_v4 import CancellationToken
        called = []
        t = CancellationToken()
        t.add_callback(lambda: called.append(1))
        t.cancel()
        assert called == [1]

    def test_exported(self) -> None:
        from meshflow import CancellationToken
        assert CancellationToken is not None


# ══════════════════════════════════════════════════════════════════════════════
# Termination conditions
# ══════════════════════════════════════════════════════════════════════════════

class TestTerminationConditions:
    def _msgs(self, n: int) -> list[Any]:
        from meshflow.agents.autogen_v4 import TextMessage
        return [TextMessage(content=f"msg{i}") for i in range(n)]

    def test_max_message_not_triggered(self) -> None:
        from meshflow.agents.autogen_v4 import MaxMessageTermination
        cond = MaxMessageTermination(5)
        assert not cond(self._msgs(3))

    def test_max_message_triggered(self) -> None:
        from meshflow.agents.autogen_v4 import MaxMessageTermination
        cond = MaxMessageTermination(3)
        assert cond(self._msgs(3))

    def test_text_mention_triggered(self) -> None:
        from meshflow.agents.autogen_v4 import TextMessage, TextMentionTermination
        cond = TextMentionTermination("TERMINATE")
        msgs = [TextMessage(content="hello"), TextMessage(content="TERMINATE")]
        assert cond(msgs)

    def test_text_mention_not_triggered(self) -> None:
        from meshflow.agents.autogen_v4 import TextMessage, TextMentionTermination
        cond = TextMentionTermination("TERMINATE")
        assert not cond([TextMessage(content="keep going")])

    def test_or_condition(self) -> None:
        from meshflow.agents.autogen_v4 import MaxMessageTermination, TextMentionTermination, OrTerminationCondition, TextMessage
        cond = OrTerminationCondition([MaxMessageTermination(10), TextMentionTermination("STOP")])
        assert cond([TextMessage(content="STOP")])
        assert not cond([TextMessage(content="keep going")])

    def test_and_condition(self) -> None:
        from meshflow.agents.autogen_v4 import MaxMessageTermination, TextMentionTermination, AndTerminationCondition, TextMessage
        cond = AndTerminationCondition([MaxMessageTermination(2), TextMentionTermination("STOP")])
        msgs = [TextMessage(content="STOP"), TextMessage(content="STOP")]
        assert cond(msgs)

    def test_exported(self) -> None:
        from meshflow import MaxMessageTermination, TextMentionTermination, OrTerminationCondition, AndTerminationCondition
        assert MaxMessageTermination is not None


# ══════════════════════════════════════════════════════════════════════════════
# Topic pub/sub
# ══════════════════════════════════════════════════════════════════════════════

class TestTopicPubSub:
    def test_default_topic_id(self) -> None:
        from meshflow.agents.autogen_v4 import DefaultTopicId
        t = DefaultTopicId()
        assert t.type == "default"

    def test_type_subscription(self) -> None:
        from meshflow.agents.autogen_v4 import TypeSubscription
        s = TypeSubscription(topic_type="alerts", agent_type="monitor")
        assert s.topic_type == "alerts"

    def test_default_subscription(self) -> None:
        from meshflow.agents.autogen_v4 import DefaultSubscription
        s = DefaultSubscription(agent_type="worker")
        assert s.agent_type == "worker"

    def test_exported(self) -> None:
        from meshflow import DefaultTopicId, TypeSubscription, DefaultSubscription
        assert DefaultTopicId is not None


# ══════════════════════════════════════════════════════════════════════════════
# AssistantAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestAssistantAgent:
    def _agent(self, reply: str = "I can help.") -> Any:
        from meshflow.agents.autogen_v4 import AssistantAgent
        return AssistantAgent("helper", provider=_echo_provider(reply), mode="sandbox")

    def test_on_messages_returns_response(self) -> None:
        from meshflow.agents.autogen_v4 import TextMessage
        agent = self._agent()
        resp = asyncio.run(agent.on_messages([TextMessage(content="hi")]))
        assert resp.chat_message.content != ""

    def test_response_source_is_agent_name(self) -> None:
        from meshflow.agents.autogen_v4 import TextMessage
        agent = self._agent()
        resp = asyncio.run(agent.on_messages([TextMessage(content="hi")]))
        assert resp.chat_message.source == "helper"

    def test_cancelled_token_returns_cancelled_message(self) -> None:
        from meshflow.agents.autogen_v4 import TextMessage, CancellationToken
        agent = self._agent()
        token = CancellationToken()
        token.cancel()
        resp = asyncio.run(agent.on_messages([TextMessage(content="hi")], token))
        assert "cancel" in resp.chat_message.content.lower()

    def test_on_reset_clears_history(self) -> None:
        from meshflow.agents.autogen_v4 import TextMessage
        agent = self._agent()
        asyncio.run(agent.on_messages([TextMessage(content="hi")]))
        assert len(agent._message_history) > 0
        asyncio.run(agent.on_reset())
        assert agent._message_history == []

    def test_run_sync_returns_string(self) -> None:
        agent = self._agent("answered!")
        result = agent.run_sync("what is 2+2?")
        assert isinstance(result, str)

    def test_exported(self) -> None:
        from meshflow import AssistantAgent
        assert AssistantAgent is not None


# ══════════════════════════════════════════════════════════════════════════════
# UserProxyAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestUserProxyAgent:
    def test_auto_reply(self) -> None:
        from meshflow.agents.autogen_v4 import UserProxyAgent, TextMessage
        proxy = UserProxyAgent(max_auto_reply=2, auto_reply_message="continue")
        resp = asyncio.run(proxy.on_messages([TextMessage(content="go")]))
        assert resp.chat_message.content == "continue"

    def test_auto_reply_exhaustion_returns_terminate(self) -> None:
        from meshflow.agents.autogen_v4 import UserProxyAgent, TextMessage
        proxy = UserProxyAgent(max_auto_reply=0)
        resp = asyncio.run(proxy.on_messages([TextMessage(content="go")]))
        assert resp.chat_message.content == "TERMINATE"

    def test_input_func(self) -> None:
        from meshflow.agents.autogen_v4 import UserProxyAgent, TextMessage
        proxy = UserProxyAgent(input_func=lambda _: "custom reply")
        resp = asyncio.run(proxy.on_messages([TextMessage(content="what?")]))
        assert resp.chat_message.content == "custom reply"

    def test_on_reset_resets_reply_count(self) -> None:
        from meshflow.agents.autogen_v4 import UserProxyAgent, TextMessage
        proxy = UserProxyAgent(max_auto_reply=1, auto_reply_message="ok")
        asyncio.run(proxy.on_messages([TextMessage(content="hi")]))
        assert proxy._reply_count == 1
        asyncio.run(proxy.on_reset())
        assert proxy._reply_count == 0

    def test_exported(self) -> None:
        from meshflow import UserProxyAgent
        assert UserProxyAgent is not None


# ══════════════════════════════════════════════════════════════════════════════
# SocietyOfMind
# ══════════════════════════════════════════════════════════════════════════════

class TestSocietyOfMind:
    def _team(self, n_agents: int = 2, max_msg: int = 4) -> Any:
        from meshflow.agents.autogen_v4 import AssistantAgent, SocietyOfMind, MaxMessageTermination
        agents = [
            AssistantAgent(f"agent{i}", provider=_echo_provider(f"answer{i}"), mode="sandbox")
            for i in range(n_agents)
        ]
        return SocietyOfMind(agents, termination_condition=MaxMessageTermination(max_msg))

    def test_run_returns_string(self) -> None:
        team = self._team()
        result = team.run("What is Python?")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_arun_returns_string(self) -> None:
        team = self._team()
        result = asyncio.run(team.arun("Explain AI."))
        assert isinstance(result, str)

    def test_single_agent_team(self) -> None:
        team = self._team(n_agents=1, max_msg=2)
        result = team.run("hello")
        assert isinstance(result, str)

    def test_cancellation_respected(self) -> None:
        from meshflow.agents.autogen_v4 import CancellationToken, MaxMessageTermination, AssistantAgent, SocietyOfMind
        agents = [AssistantAgent("a", provider=_echo_provider("ok"), mode="sandbox")]
        team = SocietyOfMind(agents, termination_condition=MaxMessageTermination(100))
        token = CancellationToken()
        token.cancel()
        result = asyncio.run(team.arun("go", token))
        assert isinstance(result, str)

    def test_exported(self) -> None:
        from meshflow import SocietyOfMind
        assert SocietyOfMind is not None


# ══════════════════════════════════════════════════════════════════════════════
# MagenticOne
# ══════════════════════════════════════════════════════════════════════════════

class TestMagenticOne:
    def _team(self) -> Any:
        from meshflow.agents.autogen_v4 import AssistantAgent, MagenticOne
        orchestrator = AssistantAgent(
            "orch",
            provider=_echo_provider("AGENT:coder\nSUBTASK:write code"),
            mode="sandbox",
        )
        coder = AssistantAgent(
            "coder",
            description="writes code",
            provider=_echo_provider("def hello(): pass"),
            mode="sandbox",
        )
        return MagenticOne(orchestrator=orchestrator, agents=[coder], max_rounds=3)

    def test_run_returns_result(self) -> None:
        team = self._team()
        result = team.run("Write a function.")
        assert isinstance(result.output, str)

    def test_result_has_rounds_used(self) -> None:
        team = self._team()
        result = team.run("Do something.")
        assert result.rounds_used >= 1

    def test_result_has_agent_turns(self) -> None:
        team = self._team()
        result = team.run("Do something.")
        assert isinstance(result.agent_turns, dict)

    def test_magentic_one_result_repr(self) -> None:
        from meshflow.agents.autogen_v4 import MagenticOneResult
        r = MagenticOneResult(output="done", rounds_used=3, agent_turns={"coder": 2})
        assert "rounds=3" in repr(r)

    def test_done_signal_ends_early(self) -> None:
        from meshflow.agents.autogen_v4 import AssistantAgent, MagenticOne
        orch = AssistantAgent("orch", provider=_echo_provider("DONE"), mode="sandbox")
        worker = AssistantAgent("w", provider=_echo_provider("done"), mode="sandbox")
        team = MagenticOne(orchestrator=orch, agents=[worker], max_rounds=10)
        result = team.run("task")
        assert result.rounds_used == 1  # stopped immediately after DONE

    def test_arun(self) -> None:
        team = self._team()
        result = asyncio.run(team.arun("async task"))
        assert isinstance(result.output, str)

    def test_exported(self) -> None:
        from meshflow import MagenticOne, MagenticOneResult
        assert MagenticOne is not None
        assert MagenticOneResult is not None


# ══════════════════════════════════════════════════════════════════════════════
# AgentRuntime
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentRuntime:
    def test_register_and_publish(self) -> None:
        from meshflow.agents.autogen_v4 import (
            AgentRuntime, AssistantAgent, TextMessage,
            TypeSubscription, DefaultTopicId,
        )
        runtime = AgentRuntime()
        agent = AssistantAgent("worker", provider=_echo_provider("ok"), mode="sandbox")
        runtime.register("worker", lambda: agent)
        runtime.add_subscription(TypeSubscription(topic_type="default", agent_type="worker"))

        async def _run():
            await runtime.publish_message(
                TextMessage(content="hello"),
                DefaultTopicId(),
            )
            await runtime.run_until_idle()

        asyncio.run(_run())

    def test_exported(self) -> None:
        from meshflow import AgentRuntime
        assert AgentRuntime is not None


# ══════════════════════════════════════════════════════════════════════════════
# OpenAI Agents SDK — FunctionTool
# ══════════════════════════════════════════════════════════════════════════════

class TestFunctionTool:
    def test_name_from_function(self) -> None:
        from meshflow.agents.openai_agents import FunctionTool
        def my_tool(x: str) -> str: return x
        ft = FunctionTool(fn=my_tool)
        assert ft.name == "my_tool"

    def test_explicit_name(self) -> None:
        from meshflow.agents.openai_agents import FunctionTool
        ft = FunctionTool(fn=lambda x: x, name="custom")
        assert ft.name == "custom"

    def test_invoke_sync(self) -> None:
        from meshflow.agents.openai_agents import FunctionTool
        ft = FunctionTool(fn=lambda x: f"result:{x}", name="t")
        result = asyncio.run(ft.invoke(x="hello"))
        assert result == "result:hello"

    def test_invoke_async(self) -> None:
        from meshflow.agents.openai_agents import FunctionTool
        async def atool(x: str) -> str: return f"async:{x}"
        ft = FunctionTool(fn=atool, name="at")
        result = asyncio.run(ft.invoke(x="world"))
        assert result == "async:world"

    def test_exported(self) -> None:
        from meshflow import FunctionTool
        assert FunctionTool is not None


# ══════════════════════════════════════════════════════════════════════════════
# Built-in tools
# ══════════════════════════════════════════════════════════════════════════════

class TestBuiltInTools:
    def test_web_search_mock(self) -> None:
        from meshflow.agents.openai_agents import WebSearchTool
        tool = WebSearchTool()
        result = asyncio.run(tool.invoke(query="meshflow python"))
        assert "mock web search" in result.lower() or "meshflow" in result.lower()

    def test_file_search_mock(self) -> None:
        from meshflow.agents.openai_agents import FileSearchTool
        tool = FileSearchTool()
        result = asyncio.run(tool.invoke(query="what is meshflow"))
        assert isinstance(result, str)

    def test_computer_tool_mock(self) -> None:
        from meshflow.agents.openai_agents import ComputerTool
        tool = ComputerTool()
        result = asyncio.run(tool.invoke(action="click button"))
        assert isinstance(result, str)

    def test_tools_have_name_and_description(self) -> None:
        from meshflow.agents.openai_agents import WebSearchTool, FileSearchTool, ComputerTool
        for tool_cls in [WebSearchTool, FileSearchTool, ComputerTool]:
            t = tool_cls()
            assert t.name
            assert t.description

    def test_exported(self) -> None:
        from meshflow import WebSearchTool, FileSearchTool, ComputerTool
        assert WebSearchTool is not None
        assert FileSearchTool is not None
        assert ComputerTool is not None


# ══════════════════════════════════════════════════════════════════════════════
# handoff()
# ══════════════════════════════════════════════════════════════════════════════

class TestHandoff:
    def test_handoff_default_tool_name(self) -> None:
        from meshflow.agents.openai_agents import Agent, handoff
        target = Agent("billing", mode="sandbox")
        h = handoff(target)
        assert h.tool_name == "transfer_to_billing"

    def test_handoff_explicit_name(self) -> None:
        from meshflow.agents.openai_agents import Agent, handoff
        target = Agent("billing", mode="sandbox")
        h = handoff(target, tool_name="escalate")
        assert h.tool_name == "escalate"

    def test_handoff_in_agent_handoffs(self) -> None:
        from meshflow.agents.openai_agents import Agent, handoff
        billing = Agent("billing", mode="sandbox")
        triage = Agent("triage", handoffs=[handoff(billing)], mode="sandbox")
        assert len(triage.handoffs) == 1
        assert triage.handoffs[0].tool_name == "transfer_to_billing"

    def test_agent_in_handoffs_auto_wrapped(self) -> None:
        from meshflow.agents.openai_agents import Agent
        billing = Agent("billing", mode="sandbox")
        triage = Agent("triage", handoffs=[billing], mode="sandbox")
        assert triage.handoffs[0].tool_name == "transfer_to_billing"

    def test_exported(self) -> None:
        from meshflow import handoff, Handoff
        assert handoff is not None
        assert Handoff is not None


# ══════════════════════════════════════════════════════════════════════════════
# Agent.as_tool()
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentAsTool:
    def test_as_tool_returns_function_tool(self) -> None:
        from meshflow.agents.openai_agents import Agent, FunctionTool
        agent = Agent("coder", instructions="You write code.", mode="sandbox",
                      provider=_echo_provider("def f(): pass"))
        tool = agent.as_tool()
        assert isinstance(tool, FunctionTool)
        assert "coder" in tool.name

    def test_as_tool_custom_name(self) -> None:
        from meshflow.agents.openai_agents import Agent
        agent = Agent("coder", mode="sandbox", provider=_echo_provider("code"))
        tool = agent.as_tool(tool_name="write_code")
        assert tool.name == "write_code"

    def test_as_tool_invoke(self) -> None:
        from meshflow.agents.openai_agents import Agent
        agent = Agent("helper", mode="sandbox", provider=_echo_provider("done!"))
        tool = agent.as_tool()
        result = asyncio.run(tool.invoke(task="do something"))
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════════════
# Agent.clone()
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentClone:
    def test_clone_creates_new_instance(self) -> None:
        from meshflow.agents.openai_agents import Agent
        a = Agent("orig", instructions="original", mode="sandbox")
        b = a.clone(name="copy", instructions="copied")
        assert b.name == "copy"
        assert b.instructions == "copied"
        assert a.name == "orig"


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

class TestRunner:
    def _agent(self, reply: str = "answer") -> Any:
        from meshflow.agents.openai_agents import Agent
        return Agent("test_agent", mode="sandbox", provider=_echo_provider(reply))

    def test_run_sync_returns_run_result(self) -> None:
        from meshflow.agents.openai_agents import Runner, RunResult
        result = Runner.run_sync(self._agent(), "hello")
        assert isinstance(result, RunResult)

    def test_final_output_is_string(self) -> None:
        from meshflow.agents.openai_agents import Runner
        result = Runner.run_sync(self._agent("42"), "what is 6*7?")
        assert isinstance(result.final_output, str)

    def test_output_property_alias(self) -> None:
        from meshflow.agents.openai_agents import Runner
        result = Runner.run_sync(self._agent("hello"), "task")
        assert result.output == result.final_output

    def test_last_agent_set(self) -> None:
        from meshflow.agents.openai_agents import Runner
        agent = self._agent()
        result = Runner.run_sync(agent, "task")
        assert result.last_agent is agent

    def test_context_attached(self) -> None:
        from meshflow.agents.openai_agents import Runner, RunContext
        ctx = RunContext(metadata={"user": "alice"})
        result = Runner.run_sync(self._agent(), "task", context=ctx)
        assert result.context is ctx

    def test_async_run(self) -> None:
        from meshflow.agents.openai_agents import Runner
        result = asyncio.run(Runner.run(self._agent("async!"), "task"))
        assert result.final_output is not None

    def test_streamed_run_yields_events(self) -> None:
        from meshflow.agents.openai_agents import Runner, RunEvent

        async def _collect():
            events = []
            async for ev in Runner.run_streamed(self._agent("hi"), "task"):
                events.append(ev)
            return events

        events = asyncio.run(_collect())
        assert any(e.event == "agent_start" for e in events)
        assert any(e.event == "agent_end" for e in events)

    def test_exported(self) -> None:
        from meshflow import Runner, OAIAgent, RunContext, AgentRunResult
        assert Runner is not None
        assert OAIAgent is not None


# ══════════════════════════════════════════════════════════════════════════════
# AgentHooks
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentHooks:
    def test_hooks_called_on_run(self) -> None:
        from meshflow.agents.openai_agents import Agent, Runner, AgentHooks, RunContext

        log: list[str] = []

        class MyHooks(AgentHooks):
            async def on_start(self, ctx: RunContext, agent: Agent) -> None:
                log.append("start")

            async def on_end(self, ctx: RunContext, agent: Agent, output: Any) -> None:
                log.append("end")

        agent = Agent("h", mode="sandbox", provider=_echo_provider("ok"), hooks=MyHooks())
        Runner.run_sync(agent, "task")
        assert "start" in log
        assert "end" in log

    def test_exported(self) -> None:
        from meshflow import AgentHooks
        assert AgentHooks is not None


# ══════════════════════════════════════════════════════════════════════════════
# Guardrails
# ══════════════════════════════════════════════════════════════════════════════

class TestOAIGuardrails:
    def test_input_guardrail_blocks(self) -> None:
        from meshflow.agents.openai_agents import (
            Agent, Runner, InputGuardrail, GuardrailFunctionOutput,
        )

        def block_all(ctx: Any, agent: Any, input: Any) -> GuardrailFunctionOutput:
            return GuardrailFunctionOutput(tripwire_triggered=True)

        agent = Agent(
            "guarded",
            input_guardrails=[InputGuardrail(block_all, name="block")],
            mode="sandbox",
            provider=_echo_provider("should not appear"),
        )
        result = Runner.run_sync(agent, "blocked task")
        assert "blocked by guardrail" in result.final_output

    def test_output_guardrail_blocks(self) -> None:
        from meshflow.agents.openai_agents import (
            Agent, Runner, OutputGuardrail, GuardrailFunctionOutput,
        )

        def block_output(ctx: Any, agent: Any, output: Any) -> GuardrailFunctionOutput:
            return GuardrailFunctionOutput(tripwire_triggered=True)

        agent = Agent(
            "output_guarded",
            output_guardrails=[OutputGuardrail(block_output, name="out_block")],
            mode="sandbox",
            provider=_echo_provider("some output"),
        )
        result = Runner.run_sync(agent, "task")
        assert "blocked by output guardrail" in result.final_output

    def test_guardrail_pass_through(self) -> None:
        from meshflow.agents.openai_agents import (
            Agent, Runner, InputGuardrail, GuardrailFunctionOutput,
        )

        def allow_all(ctx: Any, agent: Any, input: Any) -> GuardrailFunctionOutput:
            return GuardrailFunctionOutput(tripwire_triggered=False)

        agent = Agent(
            "allowed",
            input_guardrails=[InputGuardrail(allow_all)],
            mode="sandbox",
            provider=_echo_provider("success"),
        )
        result = Runner.run_sync(agent, "task")
        assert "blocked" not in result.final_output

    def test_exported(self) -> None:
        from meshflow import InputGuardrail, OutputGuardrail, GuardrailFunctionOutput
        assert InputGuardrail is not None


# ══════════════════════════════════════════════════════════════════════════════
# trace / custom_span
# ══════════════════════════════════════════════════════════════════════════════

class TestTracing:
    def test_trace_context_manager(self) -> None:
        from meshflow.agents.openai_agents import trace
        with trace("my_trace", user_id="u1") as span:
            span.set_attribute("step", 1)
            span.add_event("checkpoint")
        assert span.ended_at is not None
        assert span.attributes["step"] == 1

    def test_custom_span(self) -> None:
        from meshflow.agents.openai_agents import custom_span
        with custom_span("sub_task", key="val") as span:
            pass
        assert span.ended_at is not None
        assert span.attributes["key"] == "val"

    def test_span_name(self) -> None:
        from meshflow.agents.openai_agents import trace
        with trace("pipeline_run") as span:
            assert span.name == "pipeline_run"

    def test_exported(self) -> None:
        from meshflow import oai_trace, custom_span, AgentTraceSpan
        assert oai_trace is not None
        assert custom_span is not None
        assert AgentTraceSpan is not None


# ══════════════════════════════════════════════════════════════════════════════
# Version
# ══════════════════════════════════════════════════════════════════════════════

class TestVersion:
    def test_version_bumped(self) -> None:
        import meshflow
        major, minor, patch = meshflow.__version__.split(".")
        assert int(major) >= 1
        assert int(minor) >= 13

    def test_pyproject_version_matches(self) -> None:
        import tomllib
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(os.path.normpath(path), "rb") as f:
            data = tomllib.load(f)
        assert data["project"]["version"] == "1.14.0"
