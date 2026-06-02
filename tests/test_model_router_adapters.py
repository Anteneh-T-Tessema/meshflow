"""Tests for model_router= on MeshNode framework adapter factories."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock
from meshflow.core.node import (
    MeshNode, NodeInput, NodeOutput, NodeKind,
    _router_model, _patch_crewai_agents, _restore_crewai_agents,
    _patch_autogen_agent, _restore_autogen_agent,
)


# ── Mock router ───────────────────────────────────────────────────────────────

def _router(model: str) -> MagicMock:
    r = MagicMock()
    r.route.return_value = MagicMock(model=model)
    return r


# ── _router_model ─────────────────────────────────────────────────────────────

def test_router_model_returns_string():
    assert _router_model(_router("claude-haiku-4-5"), "task") == "claude-haiku-4-5"


def test_router_model_handles_exception():
    bad = MagicMock()
    bad.route.side_effect = RuntimeError("fail")
    assert _router_model(bad, "task") == ""


# ── CrewAI helpers ────────────────────────────────────────────────────────────

def _fake_crew(n: int = 2) -> MagicMock:
    crew = MagicMock()
    agents = []
    for i in range(n):
        a = MagicMock()
        a.llm = MagicMock(name=f"original_llm_{i}")
        agents.append(a)
    crew.agents = agents
    return crew


def test_patch_crewai_agents_stores_originals():
    crew = _fake_crew(2)
    originals = _patch_crewai_agents(crew, "claude-haiku-4-5")
    assert len(originals) == 2


def test_patch_crewai_agents_noop_on_empty_model():
    crew = _fake_crew(2)
    original_llms = [a.llm for a in crew.agents]
    originals = _patch_crewai_agents(crew, "")
    assert originals == []
    # LLMs should be unchanged
    for agent, orig in zip(crew.agents, original_llms):
        assert agent.llm is orig


def test_restore_crewai_agents_restores():
    crew = _fake_crew(2)
    original_llms = [a.llm for a in crew.agents]
    originals = _patch_crewai_agents(crew, "claude-haiku-4-5")
    _restore_crewai_agents(crew, originals)
    for agent, orig in zip(crew.agents, original_llms):
        assert agent.llm is orig


# ── AutoGen helpers ───────────────────────────────────────────────────────────

def _fake_autogen_agent(model: str = "gpt-4o") -> MagicMock:
    agent = MagicMock()
    agent.llm_config = {"config_list": [{"model": model, "api_key": "sk-test"}]}
    return agent


def test_patch_autogen_agent_updates_model():
    agent = _fake_autogen_agent("gpt-4o")
    _patch_autogen_agent(agent, "gpt-4o-mini")
    assert agent.llm_config["config_list"][0]["model"] == "gpt-4o-mini"


def test_patch_autogen_agent_noop_on_empty_model():
    agent = _fake_autogen_agent("gpt-4o")
    original_cfg = agent.llm_config
    result = _patch_autogen_agent(agent, "")
    # Should return None and not modify config
    assert result is None
    assert agent.llm_config is original_cfg


def test_restore_autogen_agent_restores():
    agent = _fake_autogen_agent("gpt-4o")
    original_cfg = agent.llm_config.copy()
    saved = _patch_autogen_agent(agent, "gpt-4o-mini")
    _restore_autogen_agent(agent, saved)
    assert agent.llm_config["config_list"][0]["model"] == "gpt-4o"


# ── from_crewai with model_router ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_from_crewai_without_router_calls_kickoff():
    crew = MagicMock()
    crew.kickoff.return_value = "crew result"
    node = MeshNode.from_crewai("crew_node", crew)
    out = await node.run(NodeInput(task="do work"))
    assert out.content == "crew result"
    crew.kickoff.assert_called_once_with(inputs={"task": "do work"})


@pytest.mark.asyncio
async def test_from_crewai_with_router_patches_and_restores():
    crew = _fake_crew(1)
    crew.kickoff.return_value = "routed result"
    original_llm = crew.agents[0].llm

    node = MeshNode.from_crewai("crew_node", crew, model_router=_router("claude-haiku-4-5"))
    out = await node.run(NodeInput(task="task"))

    assert out.content == "routed result"
    # Original LLM restored after call
    assert crew.agents[0].llm is original_llm


@pytest.mark.asyncio
async def test_from_crewai_with_router_restores_on_exception():
    crew = _fake_crew(1)
    crew.kickoff.side_effect = RuntimeError("crew failed")
    original_llm = crew.agents[0].llm

    node = MeshNode.from_crewai("crew_node", crew, model_router=_router("claude-haiku-4-5"))
    with pytest.raises(RuntimeError):
        await node.run(NodeInput(task="task"))

    # LLM should still be restored despite exception
    assert crew.agents[0].llm is original_llm


@pytest.mark.asyncio
async def test_from_crewai_metadata_flags_router():
    crew = MagicMock()
    crew.kickoff.return_value = "ok"
    node = MeshNode.from_crewai("n", crew, model_router=_router("x"))
    assert node.metadata.get("model_router") is True


@pytest.mark.asyncio
async def test_from_crewai_no_router_metadata_false():
    crew = MagicMock()
    crew.kickoff.return_value = "ok"
    node = MeshNode.from_crewai("n", crew)
    assert node.metadata.get("model_router") is False


# ── from_autogen with model_router ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_from_autogen_without_router():
    agent = MagicMock()
    agent.generate_reply.return_value = "autogen result"
    node = MeshNode.from_autogen("ag_node", agent)
    out = await node.run(NodeInput(task="hello"))
    assert out.content == "autogen result"


@pytest.mark.asyncio
async def test_from_autogen_with_router_patches_and_restores():
    agent = _fake_autogen_agent("gpt-4o")
    agent.generate_reply.return_value = "routed"
    original_cfg = {**agent.llm_config}

    node = MeshNode.from_autogen("ag_node", agent, model_router=_router("gpt-4o-mini"))
    out = await node.run(NodeInput(task="task"))

    assert out.content == "routed"
    # Config restored
    assert agent.llm_config["config_list"][0]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_from_autogen_with_router_restores_on_exception():
    agent = _fake_autogen_agent("gpt-4o")
    agent.generate_reply.side_effect = RuntimeError("autogen failed")

    node = MeshNode.from_autogen("ag_node", agent, model_router=_router("gpt-4o-mini"))
    with pytest.raises(RuntimeError):
        await node.run(NodeInput(task="task"))

    assert agent.llm_config["config_list"][0]["model"] == "gpt-4o"


# ── from_langgraph with model_router ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_from_langgraph_without_router():
    graph = AsyncMock()
    graph.ainvoke.return_value = {"messages": [{"content": "lg result"}]}
    node = MeshNode.from_langgraph("lg_node", graph)
    out = await node.run(NodeInput(task="query"))
    assert out.content == "lg result"


@pytest.mark.asyncio
async def test_from_langgraph_with_router_passes_configurable():
    graph = AsyncMock()
    graph.ainvoke.return_value = {"messages": [{"content": "routed result"}]}
    router = _router("claude-haiku-4-5")

    node = MeshNode.from_langgraph("lg_node", graph, model_router=router)
    out = await node.run(NodeInput(task="query"))

    assert out.content == "routed result"
    # Verify configurable model was passed
    call_kwargs = graph.ainvoke.call_args[1]
    assert call_kwargs.get("config", {}).get("configurable", {}).get("model") == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_from_langgraph_with_factory_mode():
    """graph_factory rebuilds the graph for the routed model."""
    built_graphs: list[str] = []

    def factory(model: str) -> AsyncMock:
        built_graphs.append(model)
        g = AsyncMock()
        g.ainvoke.return_value = {"messages": [{"content": f"result-{model}"}]}
        return g

    router = _router("claude-haiku-4-5")
    node = MeshNode.from_langgraph("lg_node", MagicMock(), model_router=router, graph_factory=factory)

    out = await node.run(NodeInput(task="query"))
    assert out.content == "result-claude-haiku-4-5"
    assert built_graphs == ["claude-haiku-4-5"]


@pytest.mark.asyncio
async def test_from_langgraph_factory_caches_graph():
    """Factory called only once per model, not on every run."""
    call_count = [0]

    def factory(model: str) -> AsyncMock:
        call_count[0] += 1
        g = AsyncMock()
        g.ainvoke.return_value = {"messages": [{"content": "ok"}]}
        return g

    router = _router("claude-haiku-4-5")
    node = MeshNode.from_langgraph("lg_node", MagicMock(), model_router=router, graph_factory=factory)

    await node.run(NodeInput(task="q1"))
    await node.run(NodeInput(task="q2"))
    assert call_count[0] == 1  # factory called once, result cached
