"""Tests for MeshFlow integrations — LangGraph, CrewAI, AutoGen, A2A, MCP, IBM, OpenAI."""

from __future__ import annotations

import pytest
from typing import Any

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool


# ── LangGraph ─────────────────────────────────────────────────────────────────


class TestLangGraphIntegration:
    def _make_lc_tool(self, name: str = "search", description: str = "A search tool") -> Any:
        """Simulate a LangChain BaseTool without requiring langchain installed."""

        class FakeLCTool:
            def __init__(self) -> None:
                self.name = name
                self.description = description
                self.tags: list[str] = []

            async def arun(self, input_str: str) -> str:
                return f"result for: {input_str}"

        return FakeLCTool()

    def _make_lc_graph(self) -> Any:
        class FakeGraph:
            async def ainvoke(self, inp: dict) -> dict:
                return {"output": f"graph result for: {inp.get('input', '')}"}

        return FakeGraph()

    def test_tool_from_langgraph(self):
        from meshflow.integrations.langgraph import tool_from_langgraph

        lc = self._make_lc_tool()
        mf = tool_from_langgraph(lc)
        assert isinstance(mf, Tool)
        assert mf.name == "search"
        assert "langgraph" in mf.tags

    def test_tools_from_langgraph_list(self):
        from meshflow.integrations.langgraph import tools_from_langgraph

        lc_tools = [self._make_lc_tool("t1"), self._make_lc_tool("t2")]
        mf_tools = tools_from_langgraph(lc_tools)
        assert len(mf_tools) == 2
        assert {t.name for t in mf_tools} == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_tool_from_langgraph_callable(self):
        from meshflow.integrations.langgraph import tool_from_langgraph

        lc = self._make_lc_tool()
        mf = tool_from_langgraph(lc)
        result = await mf.call(input="AI agents")
        assert "AI agents" in str(result)

    def test_tool_from_langgraph_risk(self):
        from meshflow.integrations.langgraph import tool_from_langgraph

        lc = self._make_lc_tool()
        mf = tool_from_langgraph(lc, risk=RiskTier.EXTERNAL_IO)
        assert mf.risk == RiskTier.EXTERNAL_IO

    def test_tool_missing_name_raises(self):
        from meshflow.integrations.langgraph import tool_from_langgraph

        with pytest.raises(TypeError, match="LangChain BaseTool"):
            tool_from_langgraph(object())

    @pytest.mark.asyncio
    async def test_agent_from_langgraph(self, monkeypatch):
        from meshflow.integrations.langgraph import agent_from_langgraph

        graph = self._make_lc_graph()
        agent = agent_from_langgraph(graph, name="lg_agent")
        assert agent.name == "lg_agent"

    def test_extract_output_from_dict(self):
        from meshflow.integrations.langgraph import _extract_lg_output

        assert _extract_lg_output({"output": "hello"}) == "hello"
        assert _extract_lg_output({"answer": "world"}) == "world"
        assert _extract_lg_output("plain string") == "plain string"

    @pytest.mark.asyncio
    async def test_node_from_langgraph(self):
        from meshflow.integrations.langgraph import node_from_langgraph
        from meshflow.core.node import MeshNode

        graph = self._make_lc_graph()
        node = node_from_langgraph(graph, "test_node")
        assert isinstance(node, MeshNode)


# ── CrewAI ────────────────────────────────────────────────────────────────────


class TestCrewAIIntegration:
    def _make_crew_tool(self, name: str = "web_scraper") -> Any:
        class FakeCrewTool:
            def __init__(self) -> None:
                self.name = name
                self.description = "A web scraping tool"

            def _run(self, argument: str) -> str:
                return f"scraped: {argument}"

        return FakeCrewTool()

    def _make_crew_agent(self, role: str = "researcher") -> Any:
        class FakeCrewAgent:
            def __init__(self) -> None:
                self.role = role
                self.backstory = f"Expert {role}"
                self.tools: list[Any] = []

        return FakeCrewAgent()

    def _make_crew(self, n_agents: int = 2) -> Any:
        class FakeCrew:
            def __init__(self) -> None:
                self.id = "fake_crew"
                self.agents = [
                    type(
                        "CA",
                        (),
                        {
                            "role": f"agent_{i}",
                            "backstory": f"Agent {i}",
                            "tools": [],
                        },
                    )()
                    for i in range(n_agents)
                ]

        return FakeCrew()

    def test_tool_from_crewai(self):
        from meshflow.integrations.crewai import tool_from_crewai

        ct = self._make_crew_tool()
        mf = tool_from_crewai(ct)
        assert isinstance(mf, Tool)
        assert mf.name == "web_scraper"
        assert "crewai" in mf.tags

    def test_tools_from_crewai_list(self):
        from meshflow.integrations.crewai import tools_from_crewai

        tools = tools_from_crewai([self._make_crew_tool("t1"), self._make_crew_tool("t2")])
        assert len(tools) == 2

    @pytest.mark.asyncio
    async def test_tool_from_crewai_callable(self):
        from meshflow.integrations.crewai import tool_from_crewai

        mf = tool_from_crewai(self._make_crew_tool())
        result = await mf.call(input="https://example.com")
        assert "scraped" in str(result)

    def test_agent_from_crewai(self):
        from meshflow.integrations.crewai import agent_from_crewai
        from meshflow.agents.builder import Agent

        ca = self._make_crew_agent("analyst")
        mf = agent_from_crewai(ca, name="analyst_agent")
        assert isinstance(mf, Agent)
        assert mf.name == "analyst_agent"

    def test_team_from_crewai(self):
        from meshflow.integrations.crewai import team_from_crewai
        from meshflow.agents.team import Team

        crew = self._make_crew(n_agents=3)
        team = team_from_crewai(crew, policy="dev")
        assert isinstance(team, Team)
        assert len(team.agents) == 3

    def test_team_from_crewai_empty_raises(self):
        from meshflow.integrations.crewai import team_from_crewai

        class EmptyCrew:
            id = "x"
            agents: list = []

        with pytest.raises(ValueError, match="no agents"):
            team_from_crewai(EmptyCrew())


# ── AutoGen ───────────────────────────────────────────────────────────────────


class TestAutoGenIntegration:
    def _make_autogen_agent(self, name: str = "assistant") -> Any:
        class FakeAutoGenAgent:
            def __init__(self) -> None:
                self.name = name
                self.system_message = "You are a helpful assistant."
                self._function_map: dict = {}

            def generate_reply(self, messages: list) -> str:
                return f"reply to: {messages[-1]['content']}"

        return FakeAutoGenAgent()

    def _make_autogen_tool_fn(self) -> Any:
        async def fetch_data(url: str) -> str:
            """Fetch data from a URL."""
            return f"data from {url}"

        return fetch_data

    def test_tool_from_autogen_callable(self):
        from meshflow.integrations.autogen import tool_from_autogen

        fn = self._make_autogen_tool_fn()
        mf = tool_from_autogen(fn)
        assert isinstance(mf, Tool)
        assert mf.name == "fetch_data"
        assert "Fetch data" in mf.description
        assert "autogen" in mf.tags

    def test_tool_from_autogen_function_tool_object(self):
        from meshflow.integrations.autogen import tool_from_autogen

        class FakeFunctionTool:
            name = "query_db"
            description = "Query the database"

            def func(self, query: str) -> str:
                return f"result: {query}"

        ft = FakeFunctionTool()
        mf = tool_from_autogen(ft)
        assert mf.name == "query_db"

    @pytest.mark.asyncio
    async def test_tool_from_autogen_callable_invokable(self):
        from meshflow.integrations.autogen import tool_from_autogen

        fn = self._make_autogen_tool_fn()
        mf = tool_from_autogen(fn)
        result = await mf.call(url="https://example.com")
        assert "example.com" in str(result)

    def test_agent_from_autogen(self):
        from meshflow.integrations.autogen import agent_from_autogen
        from meshflow.agents.builder import Agent

        aa = self._make_autogen_agent()
        mf = agent_from_autogen(aa, name="auto_agent")
        assert isinstance(mf, Agent)
        assert mf.name == "auto_agent"

    def test_team_from_autogen(self):
        from meshflow.integrations.autogen import team_from_autogen
        from meshflow.agents.team import Team

        agents = [self._make_autogen_agent(f"agent_{i}") for i in range(3)]
        team = team_from_autogen(agents, name="autogen_team", policy="dev")
        assert isinstance(team, Team)
        assert len(team.agents) == 3

    def test_mesh_tool_to_autogen(self):
        from meshflow.integrations.autogen import mesh_tool_to_autogen

        mf_tool = Tool(name="calc", description="A calculator", fn=lambda x: x * 2)
        autogen_fn = mesh_tool_to_autogen(mf_tool)
        assert callable(autogen_fn)
        assert autogen_fn.__name__ == "calc"

    def test_tool_from_autogen_invalid_raises(self):
        from meshflow.integrations.autogen import tool_from_autogen

        with pytest.raises(TypeError, match="AutoGen tool or callable"):
            tool_from_autogen(42)


# ── A2A Protocol ──────────────────────────────────────────────────────────────


class TestA2AProtocol:
    def test_agent_card_to_dict(self):
        from meshflow.integrations.a2a import AgentCard

        card = AgentCard(
            name="TestAgent",
            description="A test agent",
            url="http://localhost:8080",
            capabilities=["tasks/send"],
        )
        d = card.to_dict()
        assert d["name"] == "TestAgent"
        assert d["protocolVersion"] == "0.2.1"
        assert "tasks/send" in d["capabilities"]

    def test_a2a_task_lifecycle(self):
        from meshflow.integrations.a2a import A2ATask

        task = A2ATask(message="test task")
        assert task.state == "submitted"
        assert task.id != ""

        task.result = "done"
        task.state = "completed"
        d = task.to_dict()
        assert d["status"]["state"] == "completed"
        assert d["artifacts"][0]["parts"][0]["text"] == "done"

    def test_a2a_client_as_tool(self):
        from meshflow.integrations.a2a import A2AClient

        client = A2AClient("http://localhost:9999")
        tool = client.as_tool()
        assert isinstance(tool, Tool)
        assert tool.risk == RiskTier.EXTERNAL_IO
        assert "a2a" in tool.tags

    def test_extract_result_from_artifacts(self):
        from meshflow.integrations.a2a import A2AClient

        client = A2AClient("http://x")
        response = {
            "result": {
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"type": "text", "text": "hello world"}]}],
            }
        }
        assert client._extract_result(response) == "hello world"

    def test_extract_result_from_error(self):
        from meshflow.integrations.a2a import A2AClient

        client = A2AClient("http://x")
        response = {"error": {"code": -32000, "message": "server error"}}
        result = client._extract_result(response)
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_a2a_server_agent_card_route(self):
        from meshflow.integrations.a2a import A2AServer

        class FakeTeam:
            async def run(self, task: str) -> Any:
                class R:
                    output = f"result: {task}"

                return R()

        server = A2AServer(
            team=FakeTeam(),
            name="TestMeshAgent",
            description="Test agent",
            port=19999,
        )
        body, status = await server._route("GET", "/.well-known/agent.json", "")
        import json

        data = json.loads(body)
        assert data["name"] == "TestMeshAgent"
        assert status == "200 OK"

    @pytest.mark.asyncio
    async def test_a2a_server_task_send(self):
        import json
        from meshflow.integrations.a2a import A2AServer

        class FakeTeam:
            async def run(self, task: str) -> Any:
                class R:
                    output = f"handled: {task}"

                return R()

        server = A2AServer(team=FakeTeam(), name="T", description="d", port=19998)
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {
                    "id": "task-1",
                    "message": {"parts": [{"type": "text", "text": "do something"}]},
                },
            }
        )
        body, status = await server._route("POST", "/", payload)
        data = json.loads(body)
        assert status == "200 OK"
        result = data["result"]
        assert result["status"]["state"] == "completed"
        assert "handled" in result["artifacts"][0]["parts"][0]["text"]


# ── MCP Tool Loader ───────────────────────────────────────────────────────────


class TestMCPToolLoader:
    def test_mcp_tool_to_mesh_conversion(self):
        from meshflow.integrations.mcp_tools import MCPToolLoader

        loader = MCPToolLoader("http://localhost:3000/mcp")
        mcp_tool = {
            "name": "read_file",
            "description": "Read a file from the filesystem",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
        mf_tool = loader._mcp_tool_to_mesh(mcp_tool)
        assert isinstance(mf_tool, Tool)
        assert mf_tool.name == "read_file"
        assert "mcp" in mf_tool.tags
        assert "external" in mf_tool.tags

    def test_mcp_loader_default_risk(self):
        from meshflow.integrations.mcp_tools import MCPToolLoader

        loader = MCPToolLoader("http://x", default_risk=RiskTier.INTERNAL)
        mcp_tool = {"name": "t", "description": "d"}
        mf_tool = loader._mcp_tool_to_mesh(mcp_tool)
        assert mf_tool.risk == RiskTier.INTERNAL


# ── IBM watsonx ───────────────────────────────────────────────────────────────


class TestIBMIntegration:
    def test_tool_from_watsonx_function(self):
        from meshflow.integrations.ibm import tool_from_watsonx_function

        def query_db(sql: str) -> str:
            """Query the enterprise database."""
            return f"results for: {sql}"

        mf_tool = tool_from_watsonx_function(query_db)
        assert isinstance(mf_tool, Tool)
        assert mf_tool.name == "query_db"
        assert "watsonx" in mf_tool.tags
        assert "ibm" in mf_tool.tags

    def test_tool_from_watsonx_custom_name(self):
        from meshflow.integrations.ibm import tool_from_watsonx_function

        mf_tool = tool_from_watsonx_function(lambda x: x, name="custom_name", description="Custom")
        assert mf_tool.name == "custom_name"

    @pytest.mark.asyncio
    async def test_agent_from_watsonx_stub(self):
        from meshflow.integrations.ibm import agent_from_watsonx
        from meshflow.agents.builder import Agent

        agent = agent_from_watsonx(api_key="fake", project_id="fake", name="wx_agent", policy="dev")
        assert isinstance(agent, Agent)
        assert agent.name == "wx_agent"


# ── OpenAI Assistants ─────────────────────────────────────────────────────────


class TestOpenAIIntegration:
    def test_tool_from_openai_function(self):
        from meshflow.integrations.openai import tool_from_openai_function

        def search_web(query: str) -> str:
            """Search the web for information."""
            return f"results: {query}"

        mf_tool = tool_from_openai_function(search_web)
        assert isinstance(mf_tool, Tool)
        assert mf_tool.name == "search_web"
        assert "openai" in mf_tool.tags

    @pytest.mark.asyncio
    async def test_tool_from_openai_function_callable(self):
        from meshflow.integrations.openai import tool_from_openai_function

        async def async_search(query: str) -> str:
            return f"async result: {query}"

        mf_tool = tool_from_openai_function(async_search)
        result = await mf_tool.call(query="test")
        assert "async result" in str(result)

    def test_agent_from_openai_assistant(self):
        from meshflow.integrations.openai import agent_from_openai_assistant
        from meshflow.agents.builder import Agent

        agent = agent_from_openai_assistant(
            assistant_id="asst_fake",
            api_key="sk-fake",
            name="oai_agent",
            policy="dev",
        )
        assert isinstance(agent, Agent)
        assert agent.name == "oai_agent"
