"""Sprint 26 — Streaming tests (StreamChunk, Team.stream, Crew.kickoff_stream).

All tests are deterministic (MESHFLOW_MOCK=1, EchoProvider).
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.core.streaming import StreamChunk
from meshflow.agents.crew import Crew, Process
from meshflow.agents.task import Task


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_streaming_agent(name: str = "agent", tokens: list[str] | None = None):
    """Return a mock agent whose .stream() yields the given tokens."""
    if tokens is None:
        tokens = ["Hello", " ", "world"]

    agent = MagicMock()
    agent.name = name
    agent.tools = []

    async def _stream(prompt: str, ctx=None):
        for t in tokens:
            yield t

    agent.stream = _stream
    agent.run = AsyncMock(return_value={
        "result": "".join(tokens),
        "agent_name": name,
        "tokens": len(tokens),
        "cost_usd": 0.001,
        "stated_confidence": 0.9,
    })
    return agent


async def _collect(gen) -> list[StreamChunk]:
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# 1. StreamChunk
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamChunk:
    def test_is_token(self):
        c = StreamChunk(kind="token", content="hi")
        assert c.is_token
        assert not c.is_done

    def test_is_done(self):
        c = StreamChunk(kind="done")
        assert c.is_done
        assert not c.is_token

    def test_node_name_default(self):
        c = StreamChunk(kind="token", content="x")
        assert c.node_name == ""

    def test_task_index_default(self):
        c = StreamChunk(kind="token")
        assert c.task_index == 0

    def test_metadata_default(self):
        c = StreamChunk(kind="done")
        assert c.metadata == {}

    def test_repr_token(self):
        c = StreamChunk(kind="token", content="abc")
        assert "abc" in repr(c)

    def test_repr_non_token(self):
        c = StreamChunk(kind="node_start", node_name="planner")
        assert "planner" in repr(c)

    def test_all_kinds_accepted(self):
        for kind in ("token", "node_start", "node_end", "task_start", "task_end", "done", "error"):
            StreamChunk(kind=kind)  # should not raise

    def test_metadata_stored(self):
        c = StreamChunk(kind="task_end", metadata={"tokens": 42})
        assert c.metadata["tokens"] == 42


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Team.stream — sequential
# ═══════════════════════════════════════════════════════════════════════════════

class TestTeamStreamSequential:
    @pytest.mark.asyncio
    async def test_yields_tokens(self):
        from meshflow import Agent, Team

        a1 = _make_streaming_agent("a1", ["tok1", "tok2"])
        a2 = _make_streaming_agent("a2", ["tok3"])

        team = MagicMock()
        team.name = "t"
        team.agents = [a1, a2]
        team.pattern = "sequential"
        team._policy = MagicMock()

        from meshflow.agents.team import Team as RealTeam
        t = object.__new__(RealTeam)
        t.name = "t"
        t.agents = [a1, a2]
        t.pattern = "sequential"
        t.policy = None
        t.budget_usd = 5.0
        from meshflow.core.schemas import policy_for_mode
        t.policy = policy_for_mode("standard")

        chunks = await _collect(await t.stream("do something"))
        token_chunks = [c for c in chunks if c.is_token]
        assert len(token_chunks) >= 3  # tok1, tok2, tok3
        texts = [c.content for c in token_chunks]
        assert "tok1" in texts
        assert "tok3" in texts

    @pytest.mark.asyncio
    async def test_yields_node_start_end(self):
        from meshflow.agents.team import Team as RealTeam
        from meshflow.core.schemas import policy_for_mode

        a1 = _make_streaming_agent("planner", ["plan"])
        a2 = _make_streaming_agent("executor", ["exec"])

        t = object.__new__(RealTeam)
        t.name = "t"
        t.agents = [a1, a2]
        t.pattern = "sequential"
        t.budget_usd = 5.0
        t.policy = policy_for_mode("standard")

        chunks = await _collect(await t.stream("task"))
        kinds = [c.kind for c in chunks]
        assert "node_start" in kinds
        assert "node_end" in kinds
        assert "done" in kinds

    @pytest.mark.asyncio
    async def test_done_chunk_last(self):
        from meshflow.agents.team import Team as RealTeam
        from meshflow.core.schemas import policy_for_mode

        a1 = _make_streaming_agent("a", ["x"])
        t = object.__new__(RealTeam)
        t.name = "t"
        t.agents = [a1]
        t.pattern = "sequential"
        t.budget_usd = 5.0
        t.policy = policy_for_mode("standard")

        chunks = await _collect(await t.stream("task"))
        assert chunks[-1].kind == "done"

    @pytest.mark.asyncio
    async def test_node_names_in_chunks(self):
        from meshflow.agents.team import Team as RealTeam
        from meshflow.core.schemas import policy_for_mode

        a1 = _make_streaming_agent("alice", ["a"])
        a2 = _make_streaming_agent("bob", ["b"])
        t = object.__new__(RealTeam)
        t.name = "t"
        t.agents = [a1, a2]
        t.pattern = "sequential"
        t.budget_usd = 5.0
        t.policy = policy_for_mode("standard")

        chunks = await _collect(await t.stream("task"))
        node_names = {c.node_name for c in chunks if c.node_name}
        assert "alice" in node_names
        assert "bob" in node_names


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Crew.kickoff_stream — sequential
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewKickoffStreamSequential:
    @pytest.mark.asyncio
    async def test_yields_tokens(self):
        a1 = _make_streaming_agent("researcher", ["fact1", " ", "fact2"])
        a2 = _make_streaming_agent("writer", ["summary"])
        t1 = Task(description="Research", expected_output="facts", agent=a1)
        t2 = Task(description="Write", expected_output="report", agent=a2)

        crew = Crew(agents=[a1, a2], tasks=[t1, t2], process=Process.sequential)
        chunks = await _collect(await crew.kickoff_stream())
        tokens = [c.content for c in chunks if c.is_token]
        assert "fact1" in tokens
        assert "summary" in tokens

    @pytest.mark.asyncio
    async def test_task_start_end_events(self):
        a = _make_streaming_agent("a", ["token"])
        t = Task(description="do", expected_output="done", agent=a)
        crew = Crew(agents=[a], tasks=[t])
        chunks = await _collect(await crew.kickoff_stream())
        kinds = [c.kind for c in chunks]
        assert "task_start" in kinds
        assert "task_end" in kinds
        assert "done" in kinds

    @pytest.mark.asyncio
    async def test_done_chunk_is_last(self):
        a = _make_streaming_agent("a", ["x"])
        t = Task(description="d", expected_output="e", agent=a)
        crew = Crew(agents=[a], tasks=[t])
        chunks = await _collect(await crew.kickoff_stream())
        assert chunks[-1].kind == "done"

    @pytest.mark.asyncio
    async def test_task_end_has_full_content(self):
        a = _make_streaming_agent("a", ["Hello", " ", "World"])
        t = Task(description="d", expected_output="e", agent=a)
        crew = Crew(agents=[a], tasks=[t])
        chunks = await _collect(await crew.kickoff_stream())
        task_end = next(c for c in chunks if c.kind == "task_end")
        assert task_end.content == "Hello World"

    @pytest.mark.asyncio
    async def test_task_output_set_after_stream(self):
        a = _make_streaming_agent("a", ["result_text"])
        t = Task(description="d", expected_output="e", agent=a)
        crew = Crew(agents=[a], tasks=[t])
        await _collect(await crew.kickoff_stream())
        assert t.output is not None
        assert t.output.raw == "result_text"

    @pytest.mark.asyncio
    async def test_task_index_in_chunks(self):
        a1 = _make_streaming_agent("a1", ["t1"])
        a2 = _make_streaming_agent("a2", ["t2"])
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2])
        chunks = await _collect(await crew.kickoff_stream())
        starts = [c for c in chunks if c.kind == "task_start"]
        assert starts[0].task_index == 0
        assert starts[1].task_index == 1

    @pytest.mark.asyncio
    async def test_inputs_substituted(self):
        received_prompts: list[str] = []

        async def capturing_stream(prompt: str, ctx=None):
            received_prompts.append(prompt)
            yield "ok"

        a = MagicMock()
        a.name = "a"
        a.tools = []
        a.stream = capturing_stream
        a.run = AsyncMock(return_value={"result": "ok", "agent_name": "a", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0})

        t = Task(description="Research {topic}", expected_output="e", agent=a)
        crew = Crew(agents=[a], tasks=[t])
        await _collect(await crew.kickoff_stream(inputs={"topic": "AI"}))
        assert "AI" in received_prompts[0]

    @pytest.mark.asyncio
    async def test_context_injected_in_second_task(self):
        a1 = _make_streaming_agent("a1", ["first task output"])
        a2_prompts: list[str] = []

        async def cap_stream(prompt: str, ctx=None):
            a2_prompts.append(prompt)
            yield "ok"

        a2 = MagicMock()
        a2.name = "a2"
        a2.tools = []
        a2.stream = cap_stream
        a2.run = AsyncMock(return_value={"result": "ok", "agent_name": "a2", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0})

        t1 = Task(description="first", expected_output="e", agent=a1)
        t2 = Task(description="second", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2])
        await _collect(await crew.kickoff_stream())
        # context from t1 injected before a2 gets called
        assert "first task output" in a2_prompts[0]

    @pytest.mark.asyncio
    async def test_task_end_metadata_has_tokens(self):
        a = _make_streaming_agent("a", ["one", " ", "two"])
        t = Task(description="d", expected_output="e", agent=a)
        crew = Crew(agents=[a], tasks=[t])
        chunks = await _collect(await crew.kickoff_stream())
        task_end = next(c for c in chunks if c.kind == "task_end")
        assert "tokens" in task_end.metadata


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Crew.kickoff_stream — parallel
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewKickoffStreamParallel:
    @pytest.mark.asyncio
    async def test_yields_from_all_tasks(self):
        a1 = _make_streaming_agent("a1", ["t1_token"])
        a2 = _make_streaming_agent("a2", ["t2_token"])
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2], process=Process.parallel)
        chunks = await _collect(await crew.kickoff_stream())
        tokens = {c.content for c in chunks if c.is_token}
        assert "t1_token" in tokens
        assert "t2_token" in tokens

    @pytest.mark.asyncio
    async def test_both_task_ends_present(self):
        a1 = _make_streaming_agent("a1", ["x"])
        a2 = _make_streaming_agent("a2", ["y"])
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2], process=Process.parallel)
        chunks = await _collect(await crew.kickoff_stream())
        ends = [c for c in chunks if c.kind == "task_end"]
        assert len(ends) == 2

    @pytest.mark.asyncio
    async def test_done_chunk_last_parallel(self):
        a1 = _make_streaming_agent("a1", ["x"])
        a2 = _make_streaming_agent("a2", ["y"])
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2], process=Process.parallel)
        chunks = await _collect(await crew.kickoff_stream())
        assert chunks[-1].kind == "done"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Agent.stream (existing, regression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentStreamRegression:
    @pytest.mark.asyncio
    async def test_stream_yields_strings(self):
        from meshflow import Agent
        agent = Agent(name="echo_agent", role="executor")
        collected = []
        async for token in agent.stream("Hello"):
            collected.append(token)
            assert isinstance(token, str)
        assert len(collected) > 0

    @pytest.mark.asyncio
    async def test_stream_content_nonempty(self):
        from meshflow import Agent
        agent = Agent(name="echo_agent", role="executor")
        text = ""
        async for token in agent.stream("Explain RAG"):
            text += token
        assert len(text) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Public API surface
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamingPublicAPI:
    def test_stream_chunk_importable(self):
        from meshflow import StreamChunk as SC
        assert SC is StreamChunk

    def test_stream_chunk_in_all(self):
        import meshflow
        assert hasattr(meshflow, "StreamChunk")

    def test_crew_has_kickoff_stream(self):
        assert hasattr(Crew, "kickoff_stream")

    def test_team_has_stream(self):
        from meshflow.agents.team import Team
        assert hasattr(Team, "stream")
