"""Unified streaming types and typed channel projections for MeshFlow.

Typed StreamChannels (closes LangGraph Streaming v3 gap)
---------------------------------------------------------
Filter a stream to only the data you care about::

    from meshflow import Agent
    from meshflow.core.streaming import tokens, cost_events, filter_stream

    agent = Agent(name="writer", role="executor")

    # Only token deltas
    async for text in tokens(agent.stream("Write a poem")):
        print(text, end="", flush=True)

    # Only cost/token metadata events
    async for chunk in cost_events(agent.stream("Analyse this")):
        print(f"node={chunk.node_name} tokens={chunk.metadata.get('tokens')}")

    # Custom filter — only node_end events
    async for chunk in filter_stream(agent.stream("..."), kinds={"node_end"}):
        print(f"{chunk.node_name}: {chunk.content[:80]}")

Every streaming surface (Agent, Team, Crew, StateGraph) yields ``StreamChunk``
objects so callers can handle tokens uniformly regardless of which layer is
producing them.

Usage (existing API — unchanged)::

    async for chunk in agent.stream("Summarise this document"):
        if chunk.is_token:
            print(chunk.content, end="", flush=True)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class StreamChunk:
    """A single streaming event from any MeshFlow layer.

    Fields
    ------
    kind:       "token"      — a piece of generated text (check ``content``)
                "node_start" — a graph node / agent has started
                "node_end"   — a graph node / agent finished
                "task_start" — a Crew task has started
                "task_end"   — a Crew task finished (full output in ``content``)
                "done"       — stream complete, no more chunks
                "error"      — an error occurred (message in ``content``)
    content:    The token text (kind="token") or final output (kind="task_end").
    node_name:  Name of the agent / node producing this chunk.
    task_index: Zero-based task index (Crew streaming only).
    metadata:   Extra data — e.g. tokens, cost_usd, state snapshot.
    """

    kind: Literal["token", "node_start", "node_end", "task_start", "task_end", "done", "error"]
    content: str = ""
    node_name: str = ""
    task_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_token(self) -> bool:
        return self.kind == "token"

    @property
    def is_done(self) -> bool:
        return self.kind == "done"

    def __repr__(self) -> str:
        if self.kind == "token":
            return f"StreamChunk(token={self.content!r})"
        return f"StreamChunk(kind={self.kind!r}, node={self.node_name!r})"


# ── Typed StreamChannel helpers (LangGraph Streaming v3 parity) ───────────────

from typing import AsyncIterator, Set  # noqa: E402


async def tokens(
    stream: AsyncIterator[StreamChunk],
) -> AsyncIterator[str]:
    """Yield only the text content of token chunks.

    Usage::

        async for text in tokens(agent.stream("Write a poem")):
            print(text, end="", flush=True)
    """
    async for chunk in stream:
        if chunk.kind == "token" and chunk.content:
            yield chunk.content


async def cost_events(
    stream: AsyncIterator[StreamChunk],
) -> AsyncIterator[StreamChunk]:
    """Yield only node_end chunks which carry cost/token metadata.

    Usage::

        async for chunk in cost_events(team.stream("Analyse this")):
            print(f"  {chunk.node_name}: {chunk.metadata.get('tokens', 0)} tokens")
    """
    async for chunk in stream:
        if chunk.kind == "node_end":
            yield chunk


async def filter_stream(
    stream: AsyncIterator[StreamChunk],
    *,
    kinds: Set[str],
    node_name: str = "",
) -> AsyncIterator[StreamChunk]:
    """Yield only chunks whose ``kind`` is in *kinds*, optionally filtered by node name.

    Usage::

        # Only errors
        async for chunk in filter_stream(stream, kinds={"error"}):
            print("Error:", chunk.content)

        # Only tokens from a specific agent
        async for chunk in filter_stream(stream, kinds={"token"}, node_name="critic"):
            print(chunk.content, end="")
    """
    async for chunk in stream:
        if chunk.kind not in kinds:
            continue
        if node_name and chunk.node_name != node_name:
            continue
        yield chunk


async def task_outputs(
    stream: AsyncIterator[StreamChunk],
) -> AsyncIterator[StreamChunk]:
    """Yield only task_end chunks from Crew streams (one per completed task).

    Usage::

        async for chunk in task_outputs(crew.kickoff_stream(inputs={...})):
            print(f"Task {chunk.task_index}: {chunk.content[:200]}")
    """
    async for chunk in stream:
        if chunk.kind == "task_end":
            yield chunk
