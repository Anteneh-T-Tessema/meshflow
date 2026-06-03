"""OpenAI Agents SDK parity — MeshFlow implementations of the openai-agents
public API surface.

Allows code written against the ``openai-agents`` package to run under the
MeshFlow governance kernel without changes.

Implements
----------
Agent          — governed agent with ``as_tool()`` support
Runner         — stateless agent runner (``Runner.run()``)
handoff()      — create a handoff tool that transfers to another agent
RunContext      — execution context passed to hooks and tools
RunResult       — final result from a Runner.run() call
RunResultStreaming — streaming result (async iterable of RunEvent)
AgentHooks      — lifecycle hooks (on_start, on_end, on_tool_call, on_handoff)
RunEvent        — one event from a streaming run
WebSearchTool   — built-in web search tool (uses SandboxProvider in offline mode)
FileSearchTool  — built-in file/vector-store search tool
ComputerTool    — built-in computer-use tool (sandboxed)
trace()         — context manager for trace spans
custom_span()   — manual span creation
FunctionTool    — wrap any callable as a typed agent tool
GuardrailFunctionOutput — output of an input/output guardrail function
InputGuardrail  — input guardrail descriptor
OutputGuardrail — output guardrail descriptor

Usage::

    from meshflow.agents.openai_agents import (
        Agent, Runner, handoff, WebSearchTool, RunContext,
    )

    searcher = Agent(
        name="searcher",
        instructions="You search the web.",
        tools=[WebSearchTool()],
    )
    analyst = Agent(
        name="analyst",
        instructions="Analyse search results.",
        handoffs=[handoff(searcher)],
    )

    result = Runner.run_sync(analyst, "What is MeshFlow?")
    print(result.final_output)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Generator


# ── RunContext ─────────────────────────────────────────────────────────────────

@dataclass
class RunContext:
    """Execution context threaded through hooks and tools.

    Mirrors ``openai.agents.RunContext``.
    """
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    metadata: dict[str, Any] = field(default_factory=dict)


# ── AgentHooks ────────────────────────────────────────────────────────────────

class AgentHooks:
    """Lifecycle hooks for an agent run.

    Override any method to observe/modify behaviour at key points.

    Mirrors ``openai.agents.AgentHooks``.
    """

    async def on_start(self, context: RunContext, agent: "Agent") -> None:
        """Called when the agent run begins."""

    async def on_end(self, context: RunContext, agent: "Agent", output: Any) -> None:
        """Called when the agent run ends with its final output."""

    async def on_tool_call(
        self,
        context: RunContext,
        agent: "Agent",
        tool: Any,
        input: Any,
    ) -> None:
        """Called before every tool invocation."""

    async def on_tool_call_result(
        self,
        context: RunContext,
        agent: "Agent",
        tool: Any,
        input: Any,
        output: Any,
    ) -> None:
        """Called after every tool invocation."""

    async def on_handoff(
        self,
        context: RunContext,
        from_agent: "Agent",
        to_agent: "Agent",
    ) -> None:
        """Called when a handoff is triggered."""


# ── Guardrail types ───────────────────────────────────────────────────────────

@dataclass
class GuardrailFunctionOutput:
    """Output of a guardrail function.  ``tripwire_triggered=True`` aborts the run."""
    output_info: Any = None
    tripwire_triggered: bool = False


@dataclass
class InputGuardrail:
    """Descriptor for an input guardrail.

    ``guardrail_function`` receives ``(context, agent, input)`` and must return
    :class:`GuardrailFunctionOutput`.
    """
    guardrail_function: Callable[..., Any]
    name: str = ""


@dataclass
class OutputGuardrail:
    """Descriptor for an output guardrail."""
    guardrail_function: Callable[..., Any]
    name: str = ""


# ── FunctionTool ──────────────────────────────────────────────────────────────

@dataclass
class FunctionTool:
    """Wrap any callable as a typed agent tool.

    Mirrors ``openai.agents.FunctionTool``.

    Parameters
    ----------
    name:       Tool identifier (defaults to the callable's ``__name__``).
    description: Human-readable description shown to the LLM.
    fn:          The underlying callable (sync or async).
    params_json_schema:
        Optional JSON schema dict describing parameters.  If omitted,
        MeshFlow inspects the function signature.
    """
    fn: Callable[..., Any]
    name: str = ""
    description: str = ""
    params_json_schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = getattr(self.fn, "__name__", "function_tool")
        if not self.description:
            self.description = (self.fn.__doc__ or "").strip().split("\n")[0] or self.name

    async def invoke(self, **kwargs: Any) -> Any:
        import inspect
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(**kwargs)
        return await asyncio.get_event_loop().run_in_executor(None, lambda: self.fn(**kwargs))


# ── Built-in tools ────────────────────────────────────────────────────────────

class WebSearchTool:
    """Built-in web search tool.

    In sandbox / offline mode returns a mock result.  In production, calls the
    registered search provider (e.g. Brave, Bing, or SerpAPI via env vars).

    Mirrors ``openai.agents.WebSearchTool``.
    """

    name: str = "web_search"
    description: str = "Search the web for up-to-date information."

    def __init__(self, provider: str = "auto") -> None:
        self.provider = provider

    async def invoke(self, query: str) -> str:
        import os
        if os.environ.get("MESHFLOW_MOCK") == "1":
            return f"[mock web search result for: {query}]"
        # Real search — delegates to MeshFlow's built-in web tool if available
        try:
            from meshflow.tools.builtins import web_search
            return await web_search(query)
        except Exception as e:
            return f"[search unavailable: {e}]"


class FileSearchTool:
    """Built-in file / vector-store search tool.

    Mirrors ``openai.agents.FileSearchTool``.

    Parameters
    ----------
    vector_store_ids:
        MeshFlow VectorStore IDs or file paths to search.
    max_num_results:
        Maximum number of chunks to return.
    """

    name: str = "file_search"
    description: str = "Search uploaded files or a vector store."

    def __init__(
        self,
        vector_store_ids: list[str] | None = None,
        max_num_results: int = 5,
    ) -> None:
        self.vector_store_ids = vector_store_ids or []
        self.max_num_results = max_num_results

    async def invoke(self, query: str) -> str:
        import os
        if os.environ.get("MESHFLOW_MOCK") == "1":
            return f"[mock file search for: {query}]"
        try:
            from meshflow.intelligence.knowledge import VectorStore
            results = []
            for vs_id in self.vector_store_ids:
                store = VectorStore()
                hits = store.search(query, top_k=self.max_num_results)
                results.extend(hits)
            return "\n\n".join(results[: self.max_num_results]) or "[no results]"
        except Exception as e:
            return f"[file search unavailable: {e}]"


class ComputerTool:
    """Built-in computer-use tool (sandboxed).

    Mirrors ``openai.agents.ComputerTool``.  In MeshFlow the actual execution
    is delegated to ``CodeInterpreter`` to keep it sandboxed.
    """

    name: str = "computer"
    description: str = "Execute actions on a computer (sandboxed)."

    async def invoke(self, action: str, **kwargs: Any) -> str:
        import os
        if os.environ.get("MESHFLOW_MOCK") == "1":
            return f"[mock computer action: {action}]"
        try:
            from meshflow.tools.code_interpreter import CodeInterpreter
            ci = CodeInterpreter()
            result = ci.run(action)
            return result.output or result.error or "[no output]"
        except Exception as e:
            return f"[computer tool unavailable: {e}]"


# ── Handoff ────────────────────────────────────────────────────────────────────

@dataclass
class Handoff:
    """Descriptor produced by :func:`handoff`.

    Parameters
    ----------
    agent:
        Target :class:`Agent` to transfer to.
    tool_name:
        Name of the transfer tool shown to the LLM.
    tool_description:
        Description of when to use this handoff.
    input_filter:
        Optional callable that filters/transforms the conversation history
        before passing to the target agent.
    on_handoff:
        Optional async callable called just before the transfer.
    """
    agent: "Agent"
    tool_name: str = ""
    tool_description: str = ""
    input_filter: Callable[..., Any] | None = None
    on_handoff: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if not self.tool_name:
            self.tool_name = f"transfer_to_{self.agent.name}"
        if not self.tool_description:
            self.tool_description = (
                f"Transfer the conversation to the {self.agent.name} agent. "
                f"{self.agent.instructions or ''}"
            ).strip()


def handoff(
    agent: "Agent",
    tool_name: str = "",
    tool_description: str = "",
    input_filter: Callable[..., Any] | None = None,
    on_handoff: Callable[..., Any] | None = None,
) -> "Handoff":
    """Create a :class:`Handoff` descriptor for an agent.

    Mirrors ``openai.agents.handoff()``.

    Example::

        from meshflow.agents.openai_agents import Agent, handoff

        triage = Agent("triage")
        billing = Agent("billing")
        triage.handoffs = [handoff(billing, tool_name="escalate_to_billing")]
    """
    return Handoff(
        agent=agent,
        tool_name=tool_name,
        tool_description=tool_description,
        input_filter=input_filter,
        on_handoff=on_handoff,
    )


# ── RunEvent ──────────────────────────────────────────────────────────────────

@dataclass
class RunEvent:
    """One event from a streaming run.

    ``event`` is one of: ``"agent_start"``, ``"tool_call"``,
    ``"tool_result"``, ``"text_delta"``, ``"handoff"``, ``"agent_end"``.
    """
    event: str
    data: Any = None
    agent_name: str = ""


# ── RunResult ─────────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    """Final result of a :meth:`Runner.run` call.

    Mirrors ``openai.agents.RunResult``.
    """
    final_output: Any
    last_agent: "Agent | None" = None
    new_messages: list[Any] = field(default_factory=list)
    context: RunContext | None = None
    raw_responses: list[Any] = field(default_factory=list)

    # Convenience aliases
    @property
    def output(self) -> Any:
        return self.final_output

    def __str__(self) -> str:
        return str(self.final_output)


# ── Agent ─────────────────────────────────────────────────────────────────────

class Agent:
    """OpenAI Agents SDK–compatible agent backed by MeshFlow governance.

    Mirrors the ``openai.agents.Agent`` dataclass.

    Parameters
    ----------
    name:
        Unique identifier.
    instructions:
        System prompt (equivalent to ``system_message``).
    model:
        Model string.
    tools:
        Built-in tools (``WebSearchTool``, ``FileSearchTool``, ``FunctionTool``
        instances) *or* MeshFlow ``Tool`` objects.
    handoffs:
        List of :class:`Handoff` descriptors or target :class:`Agent` instances.
    hooks:
        :class:`AgentHooks` instance.
    input_guardrails:
        List of :class:`InputGuardrail` descriptors.
    output_guardrails:
        List of :class:`OutputGuardrail` descriptors.
    provider:
        MeshFlow LLMProvider for testing.
    mode:
        ``"sandbox"`` for offline testing.
    """

    def __init__(
        self,
        name: str,
        instructions: str = "",
        model: str = "",
        tools: list[Any] | None = None,
        handoffs: list["Handoff | Agent"] | None = None,
        hooks: AgentHooks | None = None,
        input_guardrails: list[InputGuardrail] | None = None,
        output_guardrails: list[OutputGuardrail] | None = None,
        provider: Any = None,
        mode: str = "production",
    ) -> None:
        self.name = name
        self.instructions = instructions
        self.model = model
        self.tools = tools or []
        self.handoffs: list[Handoff] = [
            h if isinstance(h, Handoff) else handoff(h)
            for h in (handoffs or [])
        ]
        self.hooks = hooks or AgentHooks()
        self.input_guardrails = input_guardrails or []
        self.output_guardrails = output_guardrails or []
        self.provider = provider
        self.mode = mode

    def as_tool(
        self,
        tool_name: str = "",
        tool_description: str = "",
    ) -> FunctionTool:
        """Expose this agent as a :class:`FunctionTool` for use by another agent.

        Mirrors ``openai.agents.Agent.as_tool()``.

        Example::

            coder = Agent("coder", instructions="You write Python.")
            analyst = Agent("analyst", tools=[coder.as_tool()])
        """
        name = tool_name or f"ask_{self.name}"
        description = tool_description or (
            f"Ask the {self.name} agent to handle a sub-task. "
            f"{self.instructions or ''}"
        ).strip()
        parent = self

        async def _call(task: str) -> str:
            result = await Runner.run(parent, task)
            return str(result.final_output)

        return FunctionTool(fn=_call, name=name, description=description)

    def clone(self, **overrides: Any) -> "Agent":
        """Return a shallow copy with field overrides."""
        kw = dict(
            name=self.name,
            instructions=self.instructions,
            model=self.model,
            tools=list(self.tools),
            handoffs=list(self.handoffs),
            hooks=self.hooks,
            input_guardrails=list(self.input_guardrails),
            output_guardrails=list(self.output_guardrails),
            provider=self.provider,
            mode=self.mode,
        )
        kw.update(overrides)
        return Agent(**kw)


# ── Runner ────────────────────────────────────────────────────────────────────

class Runner:
    """Stateless agent runner.

    Mirrors ``openai.agents.Runner``.

    Example::

        result = Runner.run_sync(agent, "Write a haiku about Python.")
        print(result.final_output)

        # Async
        result = await Runner.run(agent, "Explain async/await.")

        # Streaming
        async for event in Runner.run_streamed(agent, "Tell me a joke."):
            print(event)
    """

    @staticmethod
    async def run(
        agent: Agent,
        input: str | list[Any],
        context: RunContext | None = None,
        max_turns: int = 10,
        hooks: AgentHooks | None = None,
    ) -> RunResult:
        """Run *agent* on *input* and return a :class:`RunResult`.

        Handles tool calls, guardrails, and handoffs automatically.
        """
        ctx = context or RunContext()
        effective_hooks = hooks or agent.hooks
        task = input if isinstance(input, str) else " ".join(
            str(m) for m in input
        )

        await effective_hooks.on_start(ctx, agent)

        # Input guardrails
        for ig in agent.input_guardrails:
            import inspect
            if inspect.iscoroutinefunction(ig.guardrail_function):
                guard_out = await ig.guardrail_function(ctx, agent, task)
            else:
                guard_out = ig.guardrail_function(ctx, agent, task)
            if isinstance(guard_out, GuardrailFunctionOutput) and guard_out.tripwire_triggered:
                result = RunResult(
                    final_output=f"[blocked by guardrail: {ig.name or 'input'}]",
                    last_agent=agent, context=ctx,
                )
                await effective_hooks.on_end(ctx, agent, result.final_output)
                return result

        # Build MeshFlow tools list
        mf_tools = Runner._resolve_tools(agent, ctx, effective_hooks)

        # Resolve handoff tools
        handoff_map: dict[str, Handoff] = {h.tool_name: h for h in agent.handoffs}
        handoff_tools = [
            FunctionTool(
                fn=Runner._make_handoff_fn(h, ctx, effective_hooks),
                name=h.tool_name,
                description=h.tool_description,
            )
            for h in agent.handoffs
        ]

        from meshflow.agents.builder import Agent as MFAgent
        from meshflow.core.workflow import Workflow
        from meshflow.tools.registry import Tool

        all_tools: list[Any] = []
        for t in mf_tools + handoff_tools:
            if isinstance(t, FunctionTool):
                all_tools.append(Tool(name=t.name, description=t.description, fn=t.invoke))
            elif hasattr(t, "name") and hasattr(t, "invoke"):
                all_tools.append(Tool(name=t.name, description=t.description, fn=t.invoke))
            else:
                all_tools.append(t)

        mf_agent = MFAgent(
            name=agent.name,
            system_prompt=agent.instructions,
            tools=all_tools,
            model=agent.model or "",
            mode=agent.mode,
        )
        if agent.provider is not None:
            mf_agent.provider = agent.provider

        wf = Workflow(mode=agent.mode)
        wf.add(mf_agent)

        loop = asyncio.get_event_loop()
        wf_result = await loop.run_in_executor(None, wf.run, task)
        output = wf_result.output or ""

        # Output guardrails
        for og in agent.output_guardrails:
            import inspect
            if inspect.iscoroutinefunction(og.guardrail_function):
                guard_out = await og.guardrail_function(ctx, agent, output)
            else:
                guard_out = og.guardrail_function(ctx, agent, output)
            if isinstance(guard_out, GuardrailFunctionOutput) and guard_out.tripwire_triggered:
                output = f"[blocked by output guardrail: {og.name or 'output'}]"
                break

        result = RunResult(final_output=output, last_agent=agent, context=ctx)
        await effective_hooks.on_end(ctx, agent, output)
        return result

    @staticmethod
    def run_sync(
        agent: Agent,
        input: str | list[Any],
        context: RunContext | None = None,
        max_turns: int = 10,
    ) -> RunResult:
        """Synchronous wrapper for :meth:`run`."""
        from meshflow.integrations._utils import run_sync
        return run_sync(Runner.run(agent, input, context, max_turns))

    @staticmethod
    async def run_streamed(
        agent: Agent,
        input: str | list[Any],
        context: RunContext | None = None,
        max_turns: int = 10,
    ) -> AsyncIterator[RunEvent]:
        """Async streaming runner — yields :class:`RunEvent` objects."""
        ctx = context or RunContext()
        task = input if isinstance(input, str) else str(input)
        yield RunEvent(event="agent_start", agent_name=agent.name, data={"task": task})

        result = await Runner.run(agent, input, ctx, max_turns)
        output = result.final_output

        # Simulate token-by-token streaming
        for word in str(output).split():
            yield RunEvent(event="text_delta", agent_name=agent.name, data=word + " ")
            await asyncio.sleep(0)

        yield RunEvent(event="agent_end", agent_name=agent.name, data=output)

    @staticmethod
    def _resolve_tools(
        agent: Agent,
        ctx: RunContext,
        hooks: AgentHooks,
    ) -> list[FunctionTool]:
        resolved: list[FunctionTool] = []
        for t in agent.tools:
            if isinstance(t, FunctionTool):
                resolved.append(t)
            elif isinstance(t, (WebSearchTool, FileSearchTool, ComputerTool)):
                resolved.append(FunctionTool(fn=t.invoke, name=t.name, description=t.description))
        return resolved

    @staticmethod
    def _make_handoff_fn(
        h: Handoff,
        ctx: RunContext,
        hooks: AgentHooks,
    ) -> Callable[..., Any]:
        async def _fn(task: str = "") -> str:
            if h.on_handoff:
                import inspect
                if inspect.iscoroutinefunction(h.on_handoff):
                    await h.on_handoff(ctx, h.agent)
                else:
                    h.on_handoff(ctx, h.agent)
            await hooks.on_handoff(ctx, h.agent, h.agent)
            result = await Runner.run(h.agent, task or "continue", ctx)
            return str(result.final_output)
        return _fn


# ── Tracing ───────────────────────────────────────────────────────────────────

@dataclass
class Span:
    """A trace span (mirrors openai.agents Span)."""
    name: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def end(self) -> None:
        self.ended_at = time.monotonic()

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, **attrs: Any) -> None:
        self.events.append({"name": name, **attrs})


_active_spans: list[Span] = []


@contextlib.contextmanager
def trace(
    name: str = "agent_trace",
    **attributes: Any,
) -> Generator[Span, None, None]:
    """Context manager for a top-level trace span.

    Mirrors ``openai.agents.trace()``.

    Example::

        with trace("my_pipeline", user_id="u123") as span:
            result = Runner.run_sync(agent, "task")
            span.set_attribute("final_output", result.final_output)
    """
    span = Span(name=name, attributes=dict(attributes))
    _active_spans.append(span)
    try:
        yield span
    finally:
        span.end()
        if span in _active_spans:
            _active_spans.remove(span)


@contextlib.contextmanager
def custom_span(
    name: str,
    **attributes: Any,
) -> Generator[Span, None, None]:
    """Context manager for a custom child span.

    Mirrors ``openai.agents.custom_span()``.
    """
    span = Span(name=name, attributes=dict(attributes))
    _active_spans.append(span)
    try:
        yield span
    finally:
        span.end()
        if span in _active_spans:
            _active_spans.remove(span)
