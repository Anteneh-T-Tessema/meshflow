"""Sprint 14 — SSE events, WebSocket bus, OpenAI adapter parity.

Tests cover:
  1. WorkflowEventBus SSE format + subscribe/filter
  2. MessageBus backends: InMemoryBusBackend unchanged, WebSocketBusBackend protocol
  3. OpenAI adapter: team_from_openai_agents, mesh_tool_to_openai_function
  4. Server: /events and /ws/bus routes registered
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# 1. WorkflowEventBus SSE
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkflowEventSSE:
    def test_to_sse_format(self) -> None:
        from meshflow.core.events import EventKind, WorkflowEvent

        event = WorkflowEvent(
            kind=EventKind.STEP_COMPLETE,
            run_id="run-abc",
            node_id="planner",
            data={"cost_usd": 0.001},
        )
        sse = event.to_sse()
        assert sse.startswith("event: step_complete\n")
        assert "data:" in sse
        assert sse.endswith("\n\n")

        payload = json.loads(sse.split("data:", 1)[1].split("\n")[0].strip())
        assert payload["kind"] == "step_complete"
        assert payload["run_id"] == "run-abc"
        assert payload["node_id"] == "planner"

    def test_to_dict(self) -> None:
        from meshflow.core.events import EventKind, WorkflowEvent

        event = WorkflowEvent(kind=EventKind.WORKFLOW_START, run_id="r1")
        d = event.to_dict()
        assert d["kind"] == "workflow_start"
        assert d["run_id"] == "r1"

    @pytest.mark.asyncio
    async def test_subscribe_receives_emitted_events(self) -> None:
        from meshflow.core.events import EventKind, WorkflowEvent, WorkflowEventBus

        bus = WorkflowEventBus()
        await bus.emit(WorkflowEvent(kind=EventKind.WORKFLOW_START, run_id="r1"))

        received: list = []

        async def _collect() -> None:
            async for evt in bus.subscribe(run_id="r1", replay_history=True):
                received.append(evt)
                break  # take one and stop

        await asyncio.wait_for(_collect(), timeout=2.0)
        assert len(received) == 1
        assert received[0].run_id == "r1"

    @pytest.mark.asyncio
    async def test_subscribe_filters_by_run_id(self) -> None:
        from meshflow.core.events import EventKind, WorkflowEvent, WorkflowEventBus

        bus = WorkflowEventBus()
        await bus.emit(WorkflowEvent(kind=EventKind.STEP_COMPLETE, run_id="run-A"))
        await bus.emit(WorkflowEvent(kind=EventKind.STEP_COMPLETE, run_id="run-B"))

        events = bus.history(run_id="run-A")
        assert all(e.run_id == "run-A" for e in events)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_collect_until_workflow_complete(self) -> None:
        from meshflow.core.events import EventKind, WorkflowEvent, WorkflowEventBus

        bus = WorkflowEventBus()

        async def _emit() -> None:
            await bus.emit(WorkflowEvent(kind=EventKind.STEP_START, run_id="r1"))
            await bus.emit(WorkflowEvent(kind=EventKind.STEP_COMPLETE, run_id="r1"))
            await bus.emit(WorkflowEvent(kind=EventKind.WORKFLOW_COMPLETE, run_id="r1"))

        asyncio.create_task(_emit())
        events = await bus.collect(run_id="r1", until=EventKind.WORKFLOW_COMPLETE, timeout=5.0)
        kinds = [e.kind for e in events]
        assert EventKind.WORKFLOW_COMPLETE in kinds

    @pytest.mark.asyncio
    async def test_global_event_bus_is_singleton(self) -> None:
        from meshflow.core.events import global_event_bus, WorkflowEventBus

        assert isinstance(global_event_bus, WorkflowEventBus)
        # Same object on repeated import
        from meshflow.core.events import global_event_bus as b2
        assert global_event_bus is b2

    @pytest.mark.asyncio
    async def test_slow_subscriber_drops_events_not_blocks(self) -> None:
        from meshflow.core.events import EventKind, WorkflowEvent, WorkflowEventBus

        bus = WorkflowEventBus(maxsize=2)  # tiny queue
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        self_queues = bus._queues
        self_queues.append(q)

        # Emitting 5 events into a maxsize=2 subscriber queue should not raise
        for i in range(5):
            await bus.emit(WorkflowEvent(kind=EventKind.STEP_START, run_id=f"r{i}"))

        # The bus itself never raises; slow subscriber simply drops events
        assert bus._history  # history always kept

    def test_event_kind_values(self) -> None:
        from meshflow.core.events import EventKind

        assert EventKind.STEP_START.value == "step_start"
        assert EventKind.HITL_REQUIRED.value == "hitl_required"
        assert EventKind.WORKFLOW_COMPLETE.value == "workflow_complete"


# ─────────────────────────────────────────────────────────────────────────────
# 2. MessageBus / backends
# ─────────────────────────────────────────────────────────────────────────────


class TestInMemoryBusBackend:
    @pytest.mark.asyncio
    async def test_default_backend_is_in_memory(self) -> None:
        from meshflow.agents.messaging import InMemoryBusBackend, MessageBus

        bus = MessageBus()
        assert isinstance(bus._backend, InMemoryBusBackend)

    @pytest.mark.asyncio
    async def test_subscribe_and_send(self) -> None:
        from meshflow.agents.messaging import MessageBus
        from meshflow.core.schemas import Message

        bus = MessageBus()
        received: list = []

        async def handler(msg):
            received.append(msg)

        bus.subscribe("agent-b", handler)
        msg = Message(sender_id="agent-a", receiver_id="agent-b", content="hello")
        await bus.send(msg)

        assert len(received) == 1
        assert received[0].content == "hello"

    @pytest.mark.asyncio
    async def test_broadcast_reaches_all(self) -> None:
        from meshflow.agents.messaging import MessageBus
        from meshflow.core.schemas import Message

        bus = MessageBus()
        log: list[str] = []

        for name in ("a", "b", "c"):
            async def make_handler(n=name):
                async def h(msg):
                    log.append(n)
                return h
            bus.subscribe(name, await make_handler())

        await bus.broadcast(Message(sender_id="x", receiver_id="*", content="hi"))
        assert sorted(log) == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self) -> None:
        from meshflow.agents.messaging import MessageBus
        from meshflow.core.schemas import Message

        bus = MessageBus()
        received: list = []

        async def handler(msg):
            received.append(msg)

        bus.subscribe("agent-x", handler)
        bus.unsubscribe("agent-x")
        await bus.send(Message(sender_id="y", receiver_id="agent-x", content="hi"))
        assert received == []

    @pytest.mark.asyncio
    async def test_history_records_sent_messages(self) -> None:
        from meshflow.agents.messaging import MessageBus
        from meshflow.core.schemas import Message

        bus = MessageBus()
        await bus.send(Message(sender_id="a", receiver_id="b", content="c1"))
        await bus.send(Message(sender_id="a", receiver_id="b", content="c2"))
        assert len(bus.history()) == 2

    @pytest.mark.asyncio
    async def test_conversation_returns_messages_between_pair(self) -> None:
        from meshflow.agents.messaging import MessageBus
        from meshflow.core.schemas import Message

        bus = MessageBus()
        bus.subscribe("b", AsyncMock())
        await bus.send(Message(sender_id="a", receiver_id="b", content="one"))
        await bus.send(Message(sender_id="b", receiver_id="a", content="two"))
        await bus.send(Message(sender_id="x", receiver_id="y", content="unrelated"))

        convo = bus.conversation("a", "b")
        assert len(convo) == 2

    @pytest.mark.asyncio
    async def test_connect_disconnect_noop_for_in_memory(self) -> None:
        from meshflow.agents.messaging import MessageBus

        bus = MessageBus()
        await bus.connect()
        await bus.disconnect()  # should not raise


class TestWebSocketBusBackend:
    def test_instantiation(self) -> None:
        from meshflow.agents.messaging import WebSocketBusBackend

        backend = WebSocketBusBackend("ws://localhost:8000/ws/bus", api_key="test")
        assert backend._url == "ws://localhost:8000/ws/bus"
        assert backend._api_key == "test"

    def test_publish_before_connect_raises(self) -> None:
        from meshflow.agents.messaging import WebSocketBusBackend

        backend = WebSocketBusBackend("ws://localhost:9999/ws/bus")
        with pytest.raises(RuntimeError, match="not connected"):
            asyncio.run(backend.publish({"content": "hi"}))

    @pytest.mark.asyncio
    async def test_message_bus_accepts_ws_backend(self) -> None:
        from meshflow.agents.messaging import MessageBus, WebSocketBusBackend

        backend = WebSocketBusBackend("ws://localhost:8000/ws/bus")
        bus = MessageBus(backend=backend)
        assert bus._backend is backend

    @pytest.mark.asyncio
    async def test_in_memory_backend_satisfies_protocol(self) -> None:
        from meshflow.agents.messaging import BusBackend, InMemoryBusBackend

        backend = InMemoryBusBackend()
        assert isinstance(backend, BusBackend)

    @pytest.mark.asyncio
    async def test_ws_backend_satisfies_protocol(self) -> None:
        from meshflow.agents.messaging import BusBackend, WebSocketBusBackend

        backend = WebSocketBusBackend("ws://localhost:8000/ws/bus")
        assert isinstance(backend, BusBackend)

    @pytest.mark.asyncio
    async def test_ws_backend_dispatches_incoming_to_local_subscribers(self) -> None:
        """Simulate remote messages arriving via the WS backend queue."""
        from meshflow.agents.messaging import MessageBus, WebSocketBusBackend
        from meshflow.core.schemas import Message

        backend = WebSocketBusBackend("ws://localhost:8000/ws/bus")

        # Bypass network: prime the internal queue directly
        await backend._queue.put({
            "sender_id": "remote-agent",
            "receiver_id": "local-agent",
            "content": "ping from remote",
            "trace_id": "",
            "metadata": {},
        })

        bus = MessageBus(backend=backend)
        received: list[Message] = []

        async def handler(msg: Message) -> None:
            received.append(msg)

        bus.subscribe("local-agent", handler)

        # Drain one remote message manually (simulates the background task)
        async for msg_dict in backend.incoming():
            msg = Message(**msg_dict)
            await bus._deliver_locally(msg, __import__("meshflow.agents.messaging", fromlist=["RoutedMessage"]).RoutedMessage(message=msg))
            break

        assert len(received) == 1
        assert received[0].content == "ping from remote"


# ─────────────────────────────────────────────────────────────────────────────
# 3. OpenAI adapter parity
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenAIAdapterParity:
    def _make_oai_agent(self, name: str = "helper") -> MagicMock:
        agent = MagicMock()
        agent.name = name
        agent.instructions = "You are a helpful assistant."
        agent.tools = []
        return agent

    def test_team_from_openai_agents_returns_team(self) -> None:
        from meshflow.integrations.openai import team_from_openai_agents
        from meshflow.agents.team import Team

        agents = [self._make_oai_agent(f"agent_{i}") for i in range(3)]
        team = team_from_openai_agents(agents, name="oai_team", policy="dev")
        assert isinstance(team, Team)
        assert team.name == "oai_team"
        assert len(team.agents) == 3

    def test_team_from_openai_agents_empty_raises(self) -> None:
        from meshflow.integrations.openai import team_from_openai_agents

        with pytest.raises(ValueError, match="at least one agent"):
            team_from_openai_agents([])

    def test_team_from_openai_agents_respects_pattern(self) -> None:
        from meshflow.integrations.openai import team_from_openai_agents
        from meshflow.agents.team import Team

        agents = [self._make_oai_agent()]
        team = team_from_openai_agents(agents, pattern="parallel")
        assert isinstance(team, Team)
        assert team.pattern == "parallel"

    def test_mesh_tool_to_openai_function_schema(self) -> None:
        from meshflow.integrations.openai import mesh_tool_to_openai_function
        from meshflow.tools.registry import Tool

        async def search(query: str, max_results: int = 5) -> str:
            return f"results for {query}"

        tool = Tool(name="web_search", description="Search the web.", fn=search)
        schema = mesh_tool_to_openai_function(tool)

        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "web_search"
        assert fn["description"] == "Search the web."
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert params["properties"]["query"]["type"] == "string"
        assert "query" in params["required"]
        # max_results has a default — not required
        assert "max_results" not in params["required"]

    def test_mesh_tool_to_openai_function_int_param(self) -> None:
        from meshflow.integrations.openai import mesh_tool_to_openai_function
        from meshflow.tools.registry import Tool

        async def add(x: int, y: int) -> int:
            return x + y

        tool = Tool(name="add", description="Add two numbers.", fn=add)
        schema = mesh_tool_to_openai_function(tool)
        props = schema["function"]["parameters"]["properties"]
        assert props["x"]["type"] == "integer"
        assert props["y"]["type"] == "integer"
        assert sorted(schema["function"]["parameters"]["required"]) == ["x", "y"]

    def test_mesh_tool_to_openai_function_bool_and_float(self) -> None:
        from meshflow.integrations.openai import mesh_tool_to_openai_function
        from meshflow.tools.registry import Tool

        async def configure(verbose: bool, threshold: float) -> str:
            return "ok"

        tool = Tool(name="configure", description="Configure.", fn=configure)
        schema = mesh_tool_to_openai_function(tool)
        props = schema["function"]["parameters"]["properties"]
        assert props["verbose"]["type"] == "boolean"
        assert props["threshold"]["type"] == "number"

    def test_mesh_tool_to_openai_function_no_params(self) -> None:
        from meshflow.integrations.openai import mesh_tool_to_openai_function
        from meshflow.tools.registry import Tool

        async def ping() -> str:
            return "pong"

        tool = Tool(name="ping", description="Ping.", fn=ping)
        schema = mesh_tool_to_openai_function(tool)
        assert schema["function"]["parameters"]["properties"] == {}
        assert schema["function"]["parameters"]["required"] == []

    def test_existing_agent_from_openai_agents_sdk_still_works(self) -> None:
        from meshflow.integrations.openai import agent_from_openai_agents_sdk
        from meshflow.agents.builder import Agent

        oai_agent = self._make_oai_agent("my_helper")
        mf_agent = agent_from_openai_agents_sdk(oai_agent, name="my_helper", policy="dev")
        assert isinstance(mf_agent, Agent)
        assert mf_agent.name == "my_helper"

    def test_team_uses_each_oai_agent_name(self) -> None:
        from meshflow.integrations.openai import team_from_openai_agents

        agents = [self._make_oai_agent(f"oai_{i}") for i in range(2)]
        team = team_from_openai_agents(agents, name="named_team")
        names = [a.name for a in team.agents]
        assert "oai_0" in names
        assert "oai_1" in names


# ─────────────────────────────────────────────────────────────────────────────
# 4. Server route registration
# ─────────────────────────────────────────────────────────────────────────────


class TestServerRoutes:
    @pytest.mark.asyncio
    async def test_events_and_ws_bus_routes_registered(self) -> None:
        from meshflow.runtime.server import _build_app

        app = await _build_app(api_keys=set())
        route_paths = {r.resource.canonical for r in app.router.routes()}
        assert "/events" in route_paths
        assert "/ws/bus" in route_paths

    @pytest.mark.asyncio
    async def test_events_requires_auth_when_keys_set(self) -> None:
        from aiohttp.test_utils import TestClient, TestServer
        from meshflow.runtime.server import _build_app

        app = await _build_app(api_keys={"secret"})
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/events")
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_ws_bus_requires_auth_when_keys_set(self) -> None:
        from aiohttp.test_utils import TestClient, TestServer
        from meshflow.runtime.server import _build_app

        app = await _build_app(api_keys={"secret"})
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/ws/bus")
            assert resp.status == 401
