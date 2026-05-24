"""Unified streaming types for MeshFlow — tokens from any layer.

Every streaming surface (Agent, Team, Crew, StateGraph) yields ``StreamChunk``
objects so callers can handle tokens uniformly regardless of which layer is
producing them.

Usage:
    # Agent (single agent)
    async for chunk in agent.stream("Summarise this document"):
        if chunk.is_token:
            print(chunk.content, end="", flush=True)

    # Team (multi-agent, one node at a time)
    async for chunk in team.stream("Build a rate limiter"):
        if chunk.kind == "node_start":
            print(f"\\n[{chunk.node_name}] thinking...")
        elif chunk.is_token:
            print(chunk.content, end="", flush=True)

    # Crew (task-by-task streaming)
    async for chunk in crew.kickoff_stream(inputs={"topic": "AI"}):
        if chunk.kind == "task_start":
            print(f"\\n--- Task {chunk.task_index+1}: {chunk.node_name} ---")
        elif chunk.is_token:
            print(chunk.content, end="", flush=True)
        elif chunk.kind == "task_end":
            print(f"  [{chunk.metadata.get('tokens', '?')} tokens]")
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
