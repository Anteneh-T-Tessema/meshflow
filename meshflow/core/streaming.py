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

Every streaming surface (Agent, Team, Crew, StateGraph, Workflow) yields
``StreamChunk`` objects so callers can handle tokens uniformly regardless
of which layer is producing them.

Usage (existing API — unchanged)::

    async for chunk in agent.stream("Summarise this document"):
        if chunk.is_token:
            print(chunk.content, end="", flush=True)

v1.10.0 — routing events::

    # Sync streaming from Workflow
    for chunk in wf.stream("Analyse the market"):
        if chunk.is_token:
            print(chunk.content, end="", flush=True)
        elif chunk.is_routing:
            print(f"\\n[routing] tier={chunk.metadata['tier']} model={chunk.metadata['model']}")

    # Collect full output synchronously
    from meshflow.core.streaming import stream_collect
    text = stream_collect(wf.stream("Summarise Q3"))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal


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
                "routing"    — model tier selection event (v1.10.0); metadata
                               carries ``model``, ``tier``, ``is_local``,
                               and optionally ``cascade_escalation=True``
    content:    The token text (kind="token") or final output (kind="node_end").
    node_name:  Name of the agent / node producing this chunk.
    task_index: Zero-based task index (Crew streaming only).
    metadata:   Extra data — e.g. tokens, cost_usd, routing info.

    Routing metadata keys (kind="routing"):
        model               str   — model identifier selected
        tier                str   — tier name ("fast", "smart", "large", …)
        is_local            bool  — True for zero-cost local models
        cascade_escalation  bool  — True when this is a retry escalation
        escalation_number   int   — which escalation (1 = first retry, …)
        reason              str   — human-readable routing rationale
    """

    kind: Literal[
        "token", "node_start", "node_end",
        "task_start", "task_end", "done", "error",
        "routing",
    ]
    content: str = ""
    node_name: str = ""
    task_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def is_token(self) -> bool:
        return self.kind == "token"

    @property
    def is_done(self) -> bool:
        return self.kind == "done"

    @property
    def is_routing(self) -> bool:
        """True for tier-selection and cascade-escalation events."""
        return self.kind == "routing"

    @property
    def is_cascade_escalation(self) -> bool:
        """True when this routing event represents a cascade tier upgrade."""
        return self.kind == "routing" and bool(self.metadata.get("cascade_escalation"))

    def __repr__(self) -> str:
        if self.kind == "token":
            return f"StreamChunk(token={self.content!r})"
        if self.kind == "routing":
            tier = self.metadata.get("tier", "?")
            model = self.metadata.get("model", "?")
            return f"StreamChunk(routing, tier={tier!r}, model={model!r})"
        return f"StreamChunk(kind={self.kind!r}, node={self.node_name!r})"


# ── Sync collect helper ───────────────────────────────────────────────────────

def stream_collect(stream: Iterator["StreamChunk"]) -> str:
    """Collect all token chunks from a synchronous stream into a single string.

    Useful when you want streaming throughput during processing but a final
    string at the end::

        text = stream_collect(wf.stream("Write a haiku"))

    Equivalent async version for async streams::

        text = "".join([c.content async for c in stream if c.is_token])
    """
    return "".join(chunk.content for chunk in stream if chunk.is_token)


# ── Typed StreamChannel helpers (LangGraph Streaming v3 parity) ───────────────

from typing import AsyncIterator, Set  # noqa: E402


async def tokens(
    stream: AsyncIterator["StreamChunk"],
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
    stream: AsyncIterator["StreamChunk"],
) -> AsyncIterator["StreamChunk"]:
    """Yield only node_end chunks which carry cost/token metadata.

    Usage::

        async for chunk in cost_events(team.stream("Analyse this")):
            print(f"  {chunk.node_name}: {chunk.metadata.get('tokens', 0)} tokens")
    """
    async for chunk in stream:
        if chunk.kind == "node_end":
            yield chunk


async def routing_events(
    stream: AsyncIterator["StreamChunk"],
) -> AsyncIterator["StreamChunk"]:
    """Yield only routing event chunks (tier selection and cascade escalations).

    Usage::

        async for chunk in routing_events(wf.stream("big task")):
            tier = chunk.metadata.get("tier")
            model = chunk.metadata.get("model")
            esc = chunk.metadata.get("cascade_escalation", False)
            print(f"  {'↑' if esc else '→'} tier={tier} model={model}")
    """
    async for chunk in stream:
        if chunk.kind == "routing":
            yield chunk


async def filter_stream(
    stream: AsyncIterator["StreamChunk"],
    *,
    kinds: Set[str],
    node_name: str = "",
) -> AsyncIterator["StreamChunk"]:
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
    stream: AsyncIterator["StreamChunk"],
) -> AsyncIterator["StreamChunk"]:
    """Yield only task_end chunks from Crew streams (one per completed task).

    Usage::

        async for chunk in task_outputs(crew.kickoff_stream(inputs={...})):
            print(f"Task {chunk.task_index}: {chunk.content[:200]}")
    """
    async for chunk in stream:
        if chunk.kind == "task_end":
            yield chunk


# ── v1.10.0 — SSE / NDJSON / async helpers ───────────────────────────────────

import json as _json  # noqa: E402


def stream_to_sse(chunk: "StreamChunk", event: str = "") -> str:
    """Format a single :class:`StreamChunk` as a Server-Sent Events (SSE) string.

    The returned string is ready to be written directly to an SSE response body.
    Each SSE message ends with a double newline as required by the spec.

    Fields sent:
    - ``event:`` — the chunk kind (omitted when *event* is empty and kind is "token")
    - ``data:``  — JSON-encoded chunk payload

    Usage in FastAPI::

        from fastapi.responses import StreamingResponse
        from meshflow.core.streaming import stream_to_sse

        @app.get("/stream")
        async def endpoint(task: str):
            async def _gen():
                async for chunk in wf.astream(task):
                    yield stream_to_sse(chunk)
            return StreamingResponse(_gen(), media_type="text/event-stream")
    """
    payload = {
        "kind": chunk.kind,
        "content": chunk.content,
        "node_name": chunk.node_name,
    }
    if chunk.metadata:
        payload["metadata"] = chunk.metadata  # type: ignore[assignment]

    lines: list[str] = []
    ev_name = event or (chunk.kind if chunk.kind != "token" else "")
    if ev_name:
        lines.append(f"event: {ev_name}")
    lines.append(f"data: {_json.dumps(payload)}")
    lines.append("")  # blank line = end of SSE message
    return "\n".join(lines) + "\n"


def stream_to_ndjson(chunk: "StreamChunk") -> str:
    """Format a :class:`StreamChunk` as a single NDJSON line (newline-delimited JSON).

    Suitable for streaming APIs that use ``Content-Type: application/x-ndjson``
    or plain HTTP/2 server-push without SSE framing.

    Usage::

        async def _gen():
            async for chunk in wf.astream(task):
                yield stream_to_ndjson(chunk)
    """
    payload = {
        "kind": chunk.kind,
        "content": chunk.content,
        "node_name": chunk.node_name,
        "metadata": chunk.metadata,
    }
    return _json.dumps(payload) + "\n"


async def chunks_to_sse(
    stream: "AsyncIterator[StreamChunk]",
    include_routing: bool = True,
    include_metadata: bool = True,
) -> "AsyncIterator[str]":
    """Async generator that yields SSE-formatted strings from a chunk stream.

    Designed for direct use as a FastAPI / Starlette streaming body::

        from meshflow.core.streaming import chunks_to_sse

        @app.get("/stream")
        async def endpoint(task: str):
            return StreamingResponse(
                chunks_to_sse(wf.astream(task)),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    Parameters
    ----------
    stream:           Async generator of :class:`StreamChunk` objects.
    include_routing:  If False, routing/tier-selection events are omitted.
    include_metadata: If False, the ``metadata`` field is stripped from payloads.
    """
    async for chunk in stream:
        if not include_routing and chunk.kind == "routing":
            continue
        payload: dict = {
            "kind": chunk.kind,
            "content": chunk.content,
            "node_name": chunk.node_name,
        }
        if include_metadata and chunk.metadata:
            payload["metadata"] = chunk.metadata
        ev = chunk.kind if chunk.kind != "token" else ""
        lines = []
        if ev:
            lines.append(f"event: {ev}")
        lines.append(f"data: {_json.dumps(payload)}")
        lines.append("")
        yield "\n".join(lines) + "\n"


async def chunks_to_ndjson(
    stream: "AsyncIterator[StreamChunk]",
) -> "AsyncIterator[str]":
    """Async generator that yields NDJSON-formatted strings from a chunk stream.

    Usage::

        return StreamingResponse(
            chunks_to_ndjson(wf.astream(task)),
            media_type="application/x-ndjson",
        )
    """
    async for chunk in stream:
        yield stream_to_ndjson(chunk)


async def async_stream_collect(
    stream: "AsyncIterator[StreamChunk]",
) -> str:
    """Collect all token chunks from an async stream into a single string.

    Async equivalent of :func:`stream_collect` for use with :meth:`~meshflow.core.workflow.Workflow.astream`.

    Usage::

        from meshflow.core.streaming import async_stream_collect

        text = await async_stream_collect(wf.astream("Summarise the report"))
    """
    parts: list[str] = []
    async for chunk in stream:
        if chunk.is_token and chunk.content:
            parts.append(chunk.content)
    return "".join(parts)
