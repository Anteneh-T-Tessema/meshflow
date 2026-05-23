"""Tests for the agent creation, team collaboration, tool registry, and message bus."""

from __future__ import annotations

import pytest

from meshflow import (
    Agent,
    Team,
    MessageBus,
    Tool,
    tool,
    Message,
    AgentRole,
)
from meshflow.agents.builder import _ROLE_PROMPTS
from meshflow.tools.registry import ToolRegistry as TR


# ── Agent builder ─────────────────────────────────────────────────────────────


class TestAgentBuilder:
    def test_string_role_resolved(self):
        agent = Agent(name="r", role="researcher")
        assert agent.role == AgentRole.RESEARCHER

    def test_default_role_is_executor(self):
        agent = Agent(name="e")
        assert agent.role == AgentRole.EXECUTOR

    def test_policy_string_resolved(self):
        agent = Agent(name="p", policy="standard")
        from meshflow.core.schemas import Policy

        assert isinstance(agent.policy, Policy)

    def test_to_mesh_node_returns_node(self):
        from meshflow.core.node import MeshNode

        agent = Agent(name="n", role="executor")
        node = agent.to_mesh_node()
        assert isinstance(node, MeshNode)
        assert node.id == "n"

    @pytest.mark.asyncio
    async def test_run_returns_dict_without_llm(self, monkeypatch):
        async def fake_think(self, messages, system=None):
            return "fake result", 10, 0.001

        from meshflow.agents import base

        monkeypatch.setattr(base.BaseAgent, "think", fake_think)

        agent = Agent(name="test_agent", role="executor")
        result = await agent.run("do something")
        assert "result" in result
        assert result["result"] == "fake result"
        assert result["role"] == "executor"

    @pytest.mark.asyncio
    async def test_memory_stores_steps(self, monkeypatch):
        async def fake_think(self, messages, system=None):
            return "memory result", 5, 0.0

        from meshflow.agents import base

        monkeypatch.setattr(base.BaseAgent, "think", fake_think)

        agent = Agent(name="mem_agent", role="researcher", memory=True)
        await agent.run("step 1")
        built = agent._build()
        assert built._memory_enabled is True

    def test_tools_attached(self):
        t = Tool(name="search", description="searches", fn=lambda q: q)
        agent = Agent(name="a", tools=[t])
        assert len(agent.tools) == 1

    def test_all_roles_have_prompts(self):
        for role in AgentRole:
            assert role in _ROLE_PROMPTS


# ── ToolRegistry ──────────────────────────────────────────────────────────────


class TestToolRegistry:
    def test_register_and_get(self):
        reg = TR()
        t = Tool(name="calc", description="calculator", fn=lambda x: x * 2)
        reg.register(t)
        assert reg.get("calc") is t

    def test_get_missing_raises(self):
        reg = TR()
        with pytest.raises(KeyError, match="not found"):
            reg.get("nonexistent")

    def test_search_by_keyword(self):
        reg = TR()
        reg.register(Tool(name="web_search", description="search the web", fn=lambda q: q))
        reg.register(Tool(name="calculator", description="math operations", fn=lambda x: x))
        results = reg.search("web")
        assert len(results) == 1
        assert results[0].name == "web_search"

    def test_search_by_tag(self):
        reg = TR()
        reg.register(Tool(name="t1", description="d1", fn=lambda: None, tags=["web"]))
        reg.register(Tool(name="t2", description="d2", fn=lambda: None, tags=["math"]))
        results = reg.search(tags=["web"])
        assert len(results) == 1
        assert results[0].name == "t1"

    def test_catalog_is_serialisable(self):
        reg = TR()
        reg.register(Tool(name="x", description="y", fn=lambda a: a))
        catalog = reg.catalog()
        assert isinstance(catalog, list)
        assert catalog[0]["name"] == "x"

    def test_contains(self):
        reg = TR()
        reg.register(Tool(name="ping", description="ping", fn=lambda: "pong"))
        assert "ping" in reg
        assert "pong" not in reg

    @pytest.mark.asyncio
    async def test_tool_call_sync(self):
        t = Tool(name="double", description="doubles", fn=lambda x: x * 2)
        result = await t.call(x=5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_tool_call_async(self):
        async def async_fn(x: int) -> int:
            return x + 1

        t = Tool(name="inc", description="increments", fn=async_fn)
        result = await t.call(x=9)
        assert result == 10

    def test_tool_decorator_registers(self):
        reg = TR()

        @tool(name="decorated_tool", description="a decorated tool", registry=reg)
        def my_tool(x: int) -> int:
            return x * 3

        assert "decorated_tool" in reg
        assert isinstance(my_tool, Tool)

    @pytest.mark.asyncio
    async def test_tool_decorator_callable(self):
        reg = TR()

        @tool(name="adder", description="adds two numbers", registry=reg)
        def adder(a: int, b: int) -> int:
            return a + b

        result = await adder.call(a=3, b=4)
        assert result == 7


# ── MessageBus ────────────────────────────────────────────────────────────────


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_send_to_subscriber(self):
        bus = MessageBus()
        received: list[Message] = []

        async def handler(msg: Message) -> None:
            received.append(msg)

        bus.subscribe("agent-b", handler)
        msg = Message(sender_id="agent-a", receiver_id="agent-b", content="hello")
        routed = await bus.send(msg)

        assert len(received) == 1
        assert received[0].content == "hello"
        assert "agent-b" in routed.delivered_to

    @pytest.mark.asyncio
    async def test_send_no_subscriber_delivers_nothing(self):
        bus = MessageBus()
        msg = Message(sender_id="a", receiver_id="ghost", content="hi")
        routed = await bus.send(msg)
        assert routed.delivered_to == []

    @pytest.mark.asyncio
    async def test_broadcast_reaches_all(self):
        bus = MessageBus()
        received_a: list[str] = []
        received_b: list[str] = []

        async def ha(msg: Message) -> None:
            received_a.append(msg.content)

        async def hb(msg: Message) -> None:
            received_b.append(msg.content)

        bus.subscribe("a", ha)
        bus.subscribe("b", hb)
        msg = Message(sender_id="x", receiver_id="*", content="broadcast")
        await bus.broadcast(msg)

        assert received_a == ["broadcast"]
        assert received_b == ["broadcast"]

    @pytest.mark.asyncio
    async def test_history_recorded(self):
        bus = MessageBus()

        async def noop(msg: Message) -> None:
            pass

        bus.subscribe("tgt", noop)
        await bus.send(Message(sender_id="src", receiver_id="tgt", content="x"))
        assert len(bus.history()) == 1

    @pytest.mark.asyncio
    async def test_conversation_filter(self):
        bus = MessageBus()
        received: list[Message] = []

        async def h(msg: Message) -> None:
            received.append(msg)

        bus.subscribe("b", h)
        bus.subscribe("a", h)

        await bus.send(Message(sender_id="a", receiver_id="b", content="ping"))
        await bus.send(Message(sender_id="b", receiver_id="a", content="pong"))
        await bus.send(Message(sender_id="c", receiver_id="a", content="other"))

        convo = bus.conversation("a", "b")
        assert len(convo) == 2
        assert all(m.content in ("ping", "pong") for m in convo)

    def test_unsubscribe(self):
        bus = MessageBus()

        async def dummy_handler(msg: Message) -> None:
            pass

        bus.subscribe("x", dummy_handler)
        bus.unsubscribe("x")
        assert "x" not in bus._subscribers


# ── Team ─────────────────────────────────────────────────────────────────────


class TestTeam:
    def _mock_agent(self, name: str, role: str = "executor", monkeypatch=None):
        """Return an Agent whose LLM call is mocked."""
        if monkeypatch:

            async def fake_think(self_inner, messages, system=None):
                return f"{name}_output", 5, 0.0

            from meshflow.agents import base

            monkeypatch.setattr(base.BaseAgent, "think", fake_think)
        return Agent(name=name, role=role)

    def test_team_requires_agents(self):
        with pytest.raises(ValueError, match="at least one"):
            Team(name="empty", agents=[])

    def test_sequential_workflow_built(self):
        a = Agent(name="a1", role="planner")
        b = Agent(name="b1", role="executor")
        team = Team(name="seq", agents=[a, b], pattern="sequential")
        wf = team._build_workflow()
        assert wf._terminal == ["b1"]

    def test_hierarchical_workflow_built(self):
        a = Agent(name="orch", role="orchestrator")
        b = Agent(name="worker", role="executor")
        team = Team(name="hier", agents=[a, b], pattern="hierarchical")
        wf = team._build_workflow()
        assert wf._terminal == ["worker"]

    def test_supervised_workflow_built(self):
        a = Agent(name="w1", role="executor")
        b = Agent(name="w2", role="executor")
        c = Agent(name="sup", role="critic")
        team = Team(name="sup_team", agents=[a, b, c], pattern="supervised")
        wf = team._build_workflow()
        assert wf._terminal == ["sup"]

    def test_policy_string_resolves(self):
        a = Agent(name="x")
        team = Team(name="t", agents=[a], policy="dev")
        from meshflow.core.schemas import Policy

        assert isinstance(team.policy, Policy)

    @pytest.mark.asyncio
    async def test_team_run_sequential(self, monkeypatch):
        async def fake_think(self_inner, messages, system=None):
            return "output", 5, 0.0

        from meshflow.agents import base

        monkeypatch.setattr(base.BaseAgent, "think", fake_think)

        a = Agent(name="ta", role="planner")
        b = Agent(name="tb", role="executor")
        team = Team(name="run_test", agents=[a, b], pattern="sequential", policy="dev")
        result = await team.run("test task")
        assert result.output != ""
