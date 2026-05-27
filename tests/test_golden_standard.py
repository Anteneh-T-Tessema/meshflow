"""Tests for the golden-standard features: StateGraph, agent library, GroupChat, eval, YAML config."""

from __future__ import annotations

import asyncio
import operator
from typing import Annotated, Any, TypedDict

import pytest

# Import reducers at module level so Annotated[] works under PEP 563.
from meshflow.core.state import add as _add_reducer


# ── StateGraph ────────────────────────────────────────────────────────────────

class SimpleState(TypedDict):
    value: int
    messages: Annotated[list[str], operator.add]
    last_node: str


class _FanState(TypedDict):
    items: Annotated[list[str], _add_reducer]


def test_state_graph_linear():
    """Nodes run in order and state accumulates correctly."""
    from meshflow.core.state import StateGraph, END

    graph = StateGraph(SimpleState)

    def node_a(state):
        return {"value": state.get("value", 0) + 1, "messages": ["a"], "last_node": "a"}

    def node_b(state):
        return {"value": state["value"] + 10, "messages": ["b"], "last_node": "b"}

    graph.add_node("a", node_a)
    graph.add_node("b", node_b)
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    graph.set_entry_point("a")

    result = asyncio.run(graph.run({"value": 0, "messages": [], "last_node": ""}))

    assert result["value"] == 11
    assert result["messages"] == ["a", "b"]
    assert result["last_node"] == "b"


def test_state_graph_reducer_add():
    """Annotated[list, add] reducer appends across branches (module-level TypedDict)."""
    from meshflow.core.state import StateGraph, END

    graph = StateGraph(_FanState)

    def entry(state):
        return {"items": ["start"]}

    def branch_x(state):
        return {"items": ["x"]}

    def branch_y(state):
        return {"items": ["y"]}

    def merge(state):
        return {}

    graph.add_node("entry", entry)
    graph.add_node("x", branch_x)
    graph.add_node("y", branch_y)
    graph.add_node("merge", merge)
    graph.add_edge("entry", "x")
    graph.add_edge("entry", "y")
    graph.add_edge("x", "merge")
    graph.add_edge("y", "merge")
    graph.add_edge("merge", END)
    graph.set_entry_point("entry")

    result = asyncio.run(graph.run({"items": []}))
    # After fan-out/fan-in, all items should be accumulated
    assert "start" in result["items"]
    assert "x" in result["items"]
    assert "y" in result["items"]


class _RouteState(TypedDict):
    score: int
    route: str


def test_state_graph_conditional_edges():
    """Conditional routing sends state to the correct branch."""
    from meshflow.core.state import StateGraph, END

    graph = StateGraph(_RouteState)

    def compute(state):
        return {"score": 42}

    def high_path(state):
        return {"route": "high"}

    def low_path(state):
        return {"route": "low"}

    def route_fn(state):
        return "high" if state["score"] > 10 else "low"

    graph.add_node("compute", compute)
    graph.add_node("high_path", high_path)
    graph.add_node("low_path", low_path)
    graph.add_conditional_edges("compute", route_fn, {"high": "high_path", "low": "low_path"})
    graph.add_edge("high_path", END)
    graph.add_edge("low_path", END)
    graph.set_entry_point("compute")

    result = asyncio.run(graph.run({"score": 0, "route": ""}))
    assert result["route"] == "high"


def test_state_graph_async_nodes():
    """Async node functions are awaited correctly."""
    from meshflow.core.state import StateGraph, END

    graph = StateGraph()

    async def async_node(state):
        await asyncio.sleep(0)
        return {"result": "async_done"}

    graph.add_node("async_node", async_node)
    graph.add_edge("async_node", END)
    graph.set_entry_point("async_node")

    result = asyncio.run(graph.run({}))
    assert result["result"] == "async_done"


def test_state_graph_stream():
    """Stream yields (node_name, state) tuples."""
    from meshflow.core.state import StateGraph, END

    graph = StateGraph()

    def node_a(state):
        return {"step": 1}

    def node_b(state):
        return {"step": 2}

    graph.add_node("a", node_a)
    graph.add_node("b", node_b)
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    graph.set_entry_point("a")

    async def collect():
        events = []
        async for name, state in graph.compile().stream({}):
            events.append((name, state))
        return events

    events = asyncio.run(collect())
    names = [e[0] for e in events]
    assert "a" in names
    assert "b" in names


# ── Pre-built agent library ───────────────────────────────────────────────────

def test_agent_library_imports():
    """All pre-built agents can be imported."""
    from meshflow.agents.library import (
        ResearchAgent, CoderAgent, ReviewerAgent, AnalystAgent,
        WriterAgent, CriticAgent, PlannerAgent, SummarizerAgent,
        ExtractorAgent, ClassifierAgent, ValidatorAgent, TranslatorAgent,
        SQLAgent, APIAgent, AuditorAgent, ReporterAgent,
        DebugAgent, TeacherAgent, NegotiatorAgent, OrchestratorAgent, GuardianAgent,
    )
    agents = [
        ResearchAgent(), CoderAgent(), ReviewerAgent(), AnalystAgent(),
        WriterAgent(), CriticAgent(), PlannerAgent(), SummarizerAgent(),
        ExtractorAgent(), ClassifierAgent(), ValidatorAgent(), TranslatorAgent(),
        SQLAgent(), APIAgent(), AuditorAgent(), ReporterAgent(),
        DebugAgent(), TeacherAgent(), NegotiatorAgent(), OrchestratorAgent(), GuardianAgent(),
    ]
    assert len(agents) == 21
    for a in agents:
        assert hasattr(a, "run")
        assert a.name


def test_agent_library_namespace():
    """meshflow.agents namespace exposes pre-built agent factories."""
    import meshflow
    assert hasattr(meshflow.agents, "ResearchAgent")
    assert hasattr(meshflow.agents, "CoderAgent")
    assert hasattr(meshflow.agents, "CriticAgent")


def test_prebuilt_agent_custom_params():
    """Pre-built agents accept customization parameters."""
    from meshflow.agents.library import CoderAgent, ClassifierAgent, SummarizerAgent

    coder = CoderAgent(name="my_coder", language="TypeScript", model="claude-haiku-4-5-20251001")
    assert coder.name == "my_coder"
    assert "TypeScript" in coder.system_prompt

    clf = ClassifierAgent(categories=["spam", "ham", "uncertain"])
    assert "spam" in clf.system_prompt

    summ = SummarizerAgent(max_words=100)
    assert "100" in summ.system_prompt


# ── GroupChat ─────────────────────────────────────────────────────────────────

class _MockAgent:
    """Minimal agent mock that returns predictable responses."""

    def __init__(self, name: str, response: str = "TERMINATE") -> None:
        self.name = name
        self._response = response

    async def run(self, task: str, context: dict) -> dict:
        return {
            "result": self._response,
            "tokens": 10,
            "cost_usd": 0.001,
            "stated_confidence": 0.9,
        }


def test_groupchat_round_robin_terminates():
    """GroupChat terminates when TERMINATE keyword is seen."""
    from meshflow.agents.conversation import GroupChat, GroupChatManager

    a = _MockAgent("alice", "Some response")
    b = _MockAgent("bob", "TERMINATE — done")

    chat = GroupChat(agents=[a, b], max_turns=10, speaker_selection="round_robin")
    manager = GroupChatManager(chat, policy="dev")

    result = asyncio.run(manager.run("Hello"))
    assert result.terminated
    assert result.total_turns <= 10
    assert "bob" in [m.sender for m in result.messages]


def test_groupchat_max_turns_respected():
    """GroupChat stops at max_turns even without TERMINATE."""
    from meshflow.agents.conversation import GroupChat, GroupChatManager

    a = _MockAgent("alice", "I keep talking")
    chat = GroupChat(agents=[a], max_turns=3, speaker_selection="round_robin")
    manager = GroupChatManager(chat, policy="dev")

    result = asyncio.run(manager.run("Go"))
    assert result.total_turns == 3


def test_groupchat_custom_termination():
    """GroupChat supports callable termination condition."""
    from meshflow.agents.conversation import GroupChat, GroupChatManager, ChatMessage

    a = _MockAgent("alice", "done phrase here")

    def term(msgs: list[ChatMessage]) -> bool:
        return any("done phrase" in m.content for m in msgs)

    chat = GroupChat(agents=[a], max_turns=10, termination=term)
    manager = GroupChatManager(chat, policy="dev")

    result = asyncio.run(manager.run("Start"))
    assert result.terminated


def test_groupchat_transcript():
    """ConversationResult.transcript() returns formatted string."""
    from meshflow.agents.conversation import GroupChat, GroupChatManager

    a = _MockAgent("alice", "Hello TERMINATE")
    chat = GroupChat(agents=[a], max_turns=5)
    manager = GroupChatManager(chat, policy="dev")

    result = asyncio.run(manager.run("Hi"))
    transcript = result.transcript()
    assert "alice" in transcript
    assert "Hello" in transcript


def test_groupchat_stream():
    """GroupChatManager.stream() yields ChatMessage objects."""
    from meshflow.agents.conversation import GroupChat, GroupChatManager

    a = _MockAgent("alice", "Step one")
    b = _MockAgent("bob", "TERMINATE")
    chat = GroupChat(agents=[a, b], max_turns=5, speaker_selection="round_robin")
    manager = GroupChatManager(chat, policy="dev")

    async def collect():
        msgs = []
        async for msg in manager.stream("Start"):
            msgs.append(msg)
        return msgs

    msgs = asyncio.run(collect())
    assert len(msgs) >= 2
    senders = {m.sender for m in msgs}
    assert "user" in senders or "alice" in senders


# ── YAML Config ───────────────────────────────────────────────────────────────

MINIMAL_YAML = """\
version: "1.0"
policy:
  mode: dev
  budget_usd: 1.0
agents:
  - name: researcher
    role: researcher
    model: claude-haiku-4-5-20251001
  - name: writer
    role: executor
    model: claude-haiku-4-5-20251001
team:
  name: research_team
  pattern: sequential
  agents: [researcher, writer]
"""

WORKFLOW_YAML = """\
version: "1.0"
policy: dev
agents:
  - name: extractor
    role: executor
    model: claude-haiku-4-5-20251001
  - name: analyzer
    role: researcher
    model: claude-haiku-4-5-20251001
workflow:
  name: analyze_pipeline
  nodes:
    - id: step1
      agent: extractor
    - id: step2
      agent: analyzer
  edges:
    - from: step1
      to: step2
  terminal: step2
"""

GROUPCHAT_YAML = """\
version: "1.0"
policy: dev
agents:
  - name: alice
    role: researcher
  - name: bob
    role: executor
groupchat:
  agents: [alice, bob]
  max_turns: 5
  speaker_selection: round_robin
  termination: TERMINATE
"""


def test_loads_minimal_yaml():
    """loads() parses a minimal YAML string."""
    from meshflow.core.config import loads

    config = loads(MINIMAL_YAML)
    assert config.team is not None
    assert config.team.name == "research_team"
    assert len(config.agents) == 2
    assert "researcher" in config.agents
    assert "writer" in config.agents


def test_loads_workflow_yaml():
    """loads() builds a WorkflowDefinition from a workflow YAML."""
    from meshflow.core.config import loads

    config = loads(WORKFLOW_YAML)
    assert config.workflow is not None
    assert config.workflow.name == "analyze_pipeline"
    assert config.team is None


def test_loads_groupchat_yaml():
    """loads() builds a GroupChatManager from a groupchat YAML."""
    from meshflow.core.config import loads

    config = loads(GROUPCHAT_YAML)
    assert config.groupchat is not None


def test_loads_invalid_yaml_missing_version():
    """loads() raises ValueError when 'version' is missing."""
    from meshflow.core.config import loads

    with pytest.raises(ValueError, match="version"):
        loads("agents: []")


def test_loads_unknown_agent_in_team():
    """loads() raises ValueError when team references unknown agent."""
    from meshflow.core.config import loads

    bad = """\
version: "1.0"
agents:
  - name: alice
    role: executor
team:
  name: t
  pattern: sequential
  agents: [alice, unknown_bob]
"""
    with pytest.raises(ValueError, match="unknown_bob"):
        loads(bad)


def test_load_from_file(tmp_path):
    """load() reads and parses a real YAML file."""
    from meshflow.core.config import load

    p = tmp_path / "meshflow.yaml"
    p.write_text(MINIMAL_YAML)

    config = load(p)
    assert config.team is not None
    assert config.policy.mode.value == "dev"


def test_load_file_not_found():
    """load() raises FileNotFoundError for missing paths."""
    from meshflow.core.config import load

    with pytest.raises(FileNotFoundError):
        load("/nonexistent/path/meshflow.yaml")


def test_meshflow_load_api():
    """Top-level meshflow.load() is importable and works."""
    import meshflow

    config = meshflow.loads(MINIMAL_YAML)
    assert config.team is not None


# ── Eval framework ────────────────────────────────────────────────────────────

EVAL_DATA = {
    "version": "1.0",
    "name": "smoke_suite",
    "policy": "dev",
    "scenarios": [
        {
            "name": "contains_check",
            "input": "What is 2+2?",
            "expected_contains": ["4"],
        },
        {
            "name": "not_contains_check",
            "input": "What is 2+2?",
            "expected_not_contains": ["5", "6"],
        },
        {
            "name": "json_output",
            "input": "Return JSON",
            "eval_fn": "valid_json",
        },
        {
            "name": "python_code",
            "input": "Write a hello world function",
            "eval_fn": "check_runnable_python",
        },
    ],
}


def test_eval_suite_from_dict():
    """EvalSuite.from_dict() parses scenario definitions."""
    from meshflow.eval import EvalSuite

    suite = EvalSuite.from_dict(EVAL_DATA)
    assert suite.name == "smoke_suite"
    assert len(suite.scenarios) == 4


def test_eval_scenario_contains_pass():
    """Scenario with expected_contains passes when phrase is present."""
    from meshflow.eval import EvalScenario

    scenario = EvalScenario(name="test", input="x", expected_contains=["4"])
    result = scenario.evaluate("The answer is 4.", 10, 0.9)
    assert result.passed
    assert result.score == 1.0


def test_eval_scenario_contains_fail():
    """Scenario with expected_contains fails when phrase is absent."""
    from meshflow.eval import EvalScenario

    scenario = EvalScenario(name="test", input="x", expected_contains=["42"])
    result = scenario.evaluate("The answer is 4.", 10, 0.9)
    assert not result.passed


def test_eval_scenario_not_contains():
    """not_contains check blocks forbidden phrases."""
    from meshflow.eval import EvalScenario

    scenario = EvalScenario(name="test", input="x", expected_not_contains=["Berlin"])
    result = scenario.evaluate("The capital is Berlin.", 5, 0.9)
    assert not result.passed


def test_eval_scenario_valid_json():
    """valid_json built-in checker works."""
    from meshflow.eval.runner import _valid_json

    assert _valid_json('{"key": "value"}')
    assert not _valid_json("not json")
    assert _valid_json('Some text then {"key": 1} here')


def test_eval_scenario_check_runnable_python():
    """check_runnable_python built-in checker works."""
    from meshflow.eval.runner import _check_runnable_python

    assert _check_runnable_python("def hello():\n    return 'world'")
    assert _check_runnable_python("```python\ndef f(): pass\n```")
    assert not _check_runnable_python("def broken(:\n    pass")


def test_eval_scenario_token_budget():
    """max_tokens check fails when exceeded."""
    from meshflow.eval import EvalScenario

    scenario = EvalScenario(name="test", input="x", max_tokens=5)
    result = scenario.evaluate("short", 3, 0.9)
    assert result.checks.get("within_token_budget") is True

    result2 = scenario.evaluate("short", 10, 0.9)
    assert result2.checks.get("within_token_budget") is False


def test_eval_scenario_confidence_floor():
    """min_confidence check fails when confidence is too low."""
    from meshflow.eval import EvalScenario

    scenario = EvalScenario(name="test", input="x", min_confidence=0.8)
    result = scenario.evaluate("ok", 5, 0.9)
    assert result.checks.get("min_confidence") is True

    result2 = scenario.evaluate("ok", 5, 0.5)
    assert result2.checks.get("min_confidence") is False


def test_eval_suite_run():
    """EvalSuite.run() executes all scenarios against a mock agent."""
    from meshflow.eval import EvalSuite

    class _JsonAgent:
        async def run(self, task, context):
            return {"result": '{"answer": 4}', "tokens": 10, "stated_confidence": 0.9}

    suite = EvalSuite.from_dict({
        "name": "json_suite",
        "scenarios": [
            {"name": "s1", "input": "go", "eval_fn": "valid_json"},
            {"name": "s2", "input": "go", "expected_contains": ["4"]},
        ],
    })

    result = asyncio.run(suite.run(_JsonAgent()))
    assert result.total == 2
    assert result.passed == 2
    assert result.pass_rate == 1.0


def test_eval_suite_filter_by_tags():
    """filter() returns only matching scenarios."""
    from meshflow.eval import EvalSuite

    suite = EvalSuite.from_dict({
        "name": "tagged",
        "scenarios": [
            {"name": "smoke1", "input": "x", "tags": ["smoke"]},
            {"name": "slow1", "input": "x", "tags": ["slow"]},
            {"name": "smoke2", "input": "x", "tags": ["smoke", "regression"]},
        ],
    })
    smoke_suite = suite.filter(["smoke"])
    assert len(smoke_suite.scenarios) == 2
    assert all("smoke" in s.tags for s in smoke_suite.scenarios)


def test_eval_result_report():
    """EvalResult.report() returns a non-empty formatted string."""
    from meshflow.eval import EvalSuite

    class _Agent:
        async def run(self, task, context):
            return {"result": "Paris is the capital of France.", "tokens": 5, "stated_confidence": 0.95}

    suite = EvalSuite.from_dict({
        "name": "report_test",
        "scenarios": [
            {"name": "capital", "input": "Capital of France?", "expected_contains": ["Paris"]},
        ],
    })

    result = asyncio.run(suite.run(_Agent()))
    report = result.report()
    assert "PASS" in report
    assert "report_test" in report
    assert "100.0%" in report or "1.0" in report or "1/1" in report


# ── LangChain tool bridge ─────────────────────────────────────────────────────

def test_lc_tool_wraps_sync_tool():
    """lc_tool wraps a LangChain-style tool with _run method."""
    from meshflow.integrations.langchain import lc_tool
    from meshflow.tools.registry import Tool

    class _FakeLCTool:
        name = "fake_search"
        description = "Searches the web"

        def _run(self, input: str) -> str:
            return f"results for: {input}"

    mf_tool = lc_tool(_FakeLCTool())
    assert isinstance(mf_tool, Tool)
    assert mf_tool.name == "fake_search"

    result = asyncio.run(mf_tool.fn(input="test query"))
    assert "results for: test query" in result


def test_lc_tools_wraps_list():
    """lc_tools wraps a list of LangChain tools."""
    from meshflow.integrations.langchain import lc_tools

    class _FakeTool:
        def __init__(self, n):
            self.name = n
            self.description = "desc"

        def _run(self, input: str) -> str:
            return f"{self.name}:{input}"

    tools = lc_tools([_FakeTool("a"), _FakeTool("b")])
    assert len(tools) == 2
    assert {t.name for t in tools} == {"a", "b"}


def test_lc_tool_wraps_async_tool():
    """lc_tool wraps an async LangChain tool with _arun method."""
    from meshflow.integrations.langchain import lc_tool

    class _AsyncLCTool:
        name = "async_tool"
        description = "Async tool"

        async def _arun(self, input: str) -> str:
            return f"async:{input}"

    mf_tool = lc_tool(_AsyncLCTool())
    result = asyncio.run(mf_tool.fn(input="hello"))
    assert "async:hello" in result


# ── Top-level API ─────────────────────────────────────────────────────────────

def test_top_level_imports():
    """All new golden-standard exports are accessible from meshflow."""
    import meshflow

    assert hasattr(meshflow, "StateGraph")
    assert hasattr(meshflow, "END")
    assert hasattr(meshflow, "START")
    assert hasattr(meshflow, "add")
    assert hasattr(meshflow, "last")
    assert hasattr(meshflow, "first")
    assert hasattr(meshflow, "GroupChat")
    assert hasattr(meshflow, "GroupChatManager")
    assert hasattr(meshflow, "ConversationResult")
    assert hasattr(meshflow, "load")
    assert hasattr(meshflow, "loads")
    assert hasattr(meshflow, "MeshFlowConfig")
    assert hasattr(meshflow, "EvalSuite")
    assert hasattr(meshflow, "run_eval")
    assert hasattr(meshflow, "agents")
    assert meshflow.__version__ == "0.65.0"
