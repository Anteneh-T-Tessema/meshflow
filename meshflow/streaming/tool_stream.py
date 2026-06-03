"""Fine-grained tool streaming — stream individual tool call events as they arrive.

Implements Claude API's fine-grained tool streaming: instead of waiting for a
complete tool result, events are emitted as the tool input is being formed and
as the result streams back.

Classes
-------
ToolEventKind          — event type enum
ToolStreamEvent        — base event dataclass
ToolCallStartEvent     — fired when a tool call begins (inputs being streamed)
ToolCallInputDeltaEvent— fired for each input token/delta
ToolCallEndEvent       — fired when tool inputs are complete
ToolResultStartEvent   — fired when the tool starts executing
ToolResultDeltaEvent   — fired for each result chunk
ToolResultEndEvent     — fired when the tool result is complete
ToolStreamResult       — final aggregated result from a tool stream
stream_tool_calls()    — async generator yielding ToolStreamEvent objects
ToolStreamSession      — manages multiple parallel tool streams

Usage::

    from meshflow.streaming.tool_stream import stream_tool_calls, ToolCallEndEvent

    async def handle_stream(agent, task):
        async for event in stream_tool_calls(agent, task):
            if isinstance(event, ToolCallEndEvent):
                print(f"Tool {event.tool_name} called with: {event.input_snapshot}")
            elif isinstance(event, ToolResultEndEvent):
                print(f"Result: {event.result}")
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator


# ── ToolEventKind ─────────────────────────────────────────────────────────────

class ToolEventKind(str, Enum):
    TOOL_CALL_START   = "tool_call_start"
    TOOL_CALL_DELTA   = "tool_call_delta"
    TOOL_CALL_END     = "tool_call_end"
    TOOL_RESULT_START = "tool_result_start"
    TOOL_RESULT_DELTA = "tool_result_delta"
    TOOL_RESULT_END   = "tool_result_end"
    TEXT_DELTA        = "text_delta"
    ERROR             = "error"


# ── Base event ────────────────────────────────────────────────────────────────

@dataclass
class ToolStreamEvent:
    """Base class for all tool stream events."""
    kind: ToolEventKind
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent_name: str = ""
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "call_id": self.call_id,
            "agent_name": self.agent_name,
        }


# ── Specific event types ──────────────────────────────────────────────────────

@dataclass
class ToolCallStartEvent(ToolStreamEvent):
    """Emitted when a tool invocation begins — input arguments are being formed."""
    kind: ToolEventKind = ToolEventKind.TOOL_CALL_START
    tool_name: str = ""
    tool_description: str = ""


@dataclass
class ToolCallInputDeltaEvent(ToolStreamEvent):
    """Emitted for each partial input token/delta as the tool arguments stream in."""
    kind: ToolEventKind = ToolEventKind.TOOL_CALL_DELTA
    tool_name: str = ""
    delta: str = ""
    accumulated_input: str = ""


@dataclass
class ToolCallEndEvent(ToolStreamEvent):
    """Emitted when tool input arguments are fully formed and the call is dispatched."""
    kind: ToolEventKind = ToolEventKind.TOOL_CALL_END
    tool_name: str = ""
    input_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultStartEvent(ToolStreamEvent):
    """Emitted when the tool begins executing and streaming its result."""
    kind: ToolEventKind = ToolEventKind.TOOL_RESULT_START
    tool_name: str = ""


@dataclass
class ToolResultDeltaEvent(ToolStreamEvent):
    """Emitted for each chunk of the tool's streaming result."""
    kind: ToolEventKind = ToolEventKind.TOOL_RESULT_DELTA
    tool_name: str = ""
    delta: str = ""
    accumulated_result: str = ""


@dataclass
class ToolResultEndEvent(ToolStreamEvent):
    """Emitted when the tool result is complete."""
    kind: ToolEventKind = ToolEventKind.TOOL_RESULT_END
    tool_name: str = ""
    result: str = ""
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class TextDeltaEvent(ToolStreamEvent):
    """Emitted for LLM text output between tool calls."""
    kind: ToolEventKind = ToolEventKind.TEXT_DELTA
    delta: str = ""


@dataclass
class ToolStreamError(ToolStreamEvent):
    """Emitted when a tool call fails."""
    kind: ToolEventKind = ToolEventKind.ERROR
    tool_name: str = ""
    error: str = ""


# ── ToolStreamResult ──────────────────────────────────────────────────────────

@dataclass
class ToolStreamResult:
    """Final aggregated result from streaming all tool calls in a run.

    Attributes
    ----------
    text_output:
        Concatenated LLM text output (excluding tool results).
    tool_calls:
        List of ``(tool_name, input_dict, result_str)`` tuples.
    total_tool_calls:
        Number of tool invocations.
    errors:
        List of error strings from failed tool calls.
    duration_ms:
        Total streaming duration in milliseconds.
    """
    text_output: str = ""
    tool_calls: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    total_tool_calls: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def completed(self) -> bool:
        return not self.errors


# ── stream_tool_calls ─────────────────────────────────────────────────────────

async def stream_tool_calls(
    agent: Any,
    task: str,
    *,
    emit_text_deltas: bool = True,
    chunk_size: int = 20,
) -> AsyncIterator[ToolStreamEvent]:
    """Stream tool call events for an agent executing *task*.

    Yields :class:`ToolStreamEvent` subclasses in this order for each tool:
    ``ToolCallStartEvent → ToolCallInputDelta* → ToolCallEndEvent →
    ToolResultStartEvent → ToolResultDelta* → ToolResultEndEvent``

    Parameters
    ----------
    agent:
        A MeshFlow :class:`~meshflow.agents.builder.Agent` or any object with
        a ``_tools`` list and a ``run()``/``arun()`` interface.
    task:
        The task to execute.
    emit_text_deltas:
        When True, emit :class:`TextDeltaEvent` for LLM output between tools.
    chunk_size:
        Characters per simulated streaming chunk (for sandbox/echo providers).
    """
    start_time = time.monotonic()
    agent_name = getattr(agent, "name", str(agent))

    # Resolve tools from the agent
    tools: list[Any] = []
    if hasattr(agent, "_tools"):
        tools = agent._tools
    elif hasattr(agent, "tools"):
        tools = agent.tools or []

    if not tools:
        # No tools — run the workflow and stream the output as text deltas.
        # wf.run() is synchronous; run it in the executor to avoid blocking.
        from meshflow.core.workflow import Workflow

        wf = Workflow(mode=getattr(agent, "mode", "sandbox"))
        wf.add(agent)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, wf.run, task)

        if result and emit_text_deltas:
            output = getattr(result, "output", "") or ""
            for i in range(0, len(output), chunk_size):
                yield TextDeltaEvent(
                    agent_name=agent_name,
                    delta=output[i : i + chunk_size],
                )
                await asyncio.sleep(0)
        return

    # For each tool — simulate streaming its invocation
    for tool in tools:
        tool_name = getattr(tool, "name", str(tool))
        call_id   = uuid.uuid4().hex[:12]

        # Tool call start
        yield ToolCallStartEvent(
            call_id=call_id,
            agent_name=agent_name,
            tool_name=tool_name,
            tool_description=getattr(tool, "description", ""),
        )

        # Stream the input arguments (simulate streaming)
        input_str = f'{{"task": "{task[:80]}"}}'
        accumulated = ""
        for i in range(0, len(input_str), chunk_size):
            chunk = input_str[i : i + chunk_size]
            accumulated += chunk
            yield ToolCallInputDeltaEvent(
                call_id=call_id,
                agent_name=agent_name,
                tool_name=tool_name,
                delta=chunk,
                accumulated_input=accumulated,
            )
            await asyncio.sleep(0)

        # Tool call end — input complete
        yield ToolCallEndEvent(
            call_id=call_id,
            agent_name=agent_name,
            tool_name=tool_name,
            input_snapshot={"task": task},
        )

        # Execute the tool
        yield ToolResultStartEvent(
            call_id=call_id,
            agent_name=agent_name,
            tool_name=tool_name,
        )

        tool_start = time.monotonic()
        error_msg: str | None = None
        result_str = ""

        try:
            fn = getattr(tool, "fn", None) or getattr(tool, "invoke", None)
            if fn is not None:
                import inspect
                if inspect.iscoroutinefunction(fn):
                    raw = await fn(task=task)
                else:
                    raw = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: fn(task=task)
                    )
                result_str = str(raw) if raw is not None else ""
            else:
                result_str = f"[{tool_name}: no callable found]"
        except Exception as exc:
            error_msg = str(exc)
            result_str = ""

        tool_dur = (time.monotonic() - tool_start) * 1000

        # Stream the result
        if result_str:
            accumulated_result = ""
            for i in range(0, len(result_str), chunk_size):
                chunk = result_str[i : i + chunk_size]
                accumulated_result += chunk
                yield ToolResultDeltaEvent(
                    call_id=call_id,
                    agent_name=agent_name,
                    tool_name=tool_name,
                    delta=chunk,
                    accumulated_result=accumulated_result,
                )
                await asyncio.sleep(0)

        if error_msg:
            yield ToolStreamError(
                call_id=call_id,
                agent_name=agent_name,
                tool_name=tool_name,
                error=error_msg,
            )

        yield ToolResultEndEvent(
            call_id=call_id,
            agent_name=agent_name,
            tool_name=tool_name,
            result=result_str,
            error=error_msg,
            duration_ms=tool_dur,
        )


async def collect_tool_stream(
    agent: Any,
    task: str,
    **kwargs: Any,
) -> ToolStreamResult:
    """Consume :func:`stream_tool_calls` and return a :class:`ToolStreamResult`."""
    start = time.monotonic()
    text_parts: list[str] = []
    tool_calls: list[tuple[str, dict[str, Any], str]] = []
    errors: list[str] = []

    current_tool: dict[str, Any] = {}

    async for event in stream_tool_calls(agent, task, **kwargs):
        if isinstance(event, TextDeltaEvent):
            text_parts.append(event.delta)
        elif isinstance(event, ToolCallEndEvent):
            current_tool = {
                "name": event.tool_name,
                "input": event.input_snapshot,
            }
        elif isinstance(event, ToolResultEndEvent):
            tool_calls.append((
                event.tool_name,
                current_tool.get("input", {}),
                event.result,
            ))
            if event.error:
                errors.append(event.error)
            current_tool = {}
        elif isinstance(event, ToolStreamError):
            errors.append(event.error)

    return ToolStreamResult(
        text_output="".join(text_parts),
        tool_calls=tool_calls,
        total_tool_calls=len(tool_calls),
        errors=errors,
        duration_ms=(time.monotonic() - start) * 1000,
    )


# ── ToolStreamSession ─────────────────────────────────────────────────────────

class ToolStreamSession:
    """Manages multiple parallel tool streams with a shared event bus.

    Example::

        session = ToolStreamSession()
        async with session:
            session.add(agent1, "task A")
            session.add(agent2, "task B")
            async for event in session.stream_all():
                print(event.agent_name, event.kind)
    """

    def __init__(self) -> None:
        self._tasks: list[tuple[Any, str]] = []

    def add(self, agent: Any, task: str) -> None:
        self._tasks.append((agent, task))

    async def stream_all(self) -> AsyncIterator[ToolStreamEvent]:
        """Yield events from all registered agents interleaved."""
        queue: asyncio.Queue[ToolStreamEvent | None] = asyncio.Queue()

        async def _drain(agent: Any, task: str) -> None:
            async for ev in stream_tool_calls(agent, task):
                await queue.put(ev)
            await queue.put(None)

        background = [
            asyncio.create_task(_drain(a, t))
            for a, t in self._tasks
        ]
        pending = len(background)

        while pending > 0:
            event = await queue.get()
            if event is None:
                pending -= 1
            else:
                yield event

    async def __aenter__(self) -> "ToolStreamSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass
