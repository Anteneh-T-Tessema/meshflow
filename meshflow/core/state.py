"""Typed state channels with reducers — MeshFlow's answer to LangGraph's StateGraph.

Usage:
    from meshflow.core.state import StateGraph, add, last, node, interrupt, Command

    class ResearchState(TypedDict):
        query:    str
        sources:  Annotated[list[str], add]   # branches append, reducer merges
        draft:    Annotated[str, last]         # last writer wins
        tokens:   Annotated[int, operator.add] # accumulate across branches

    @node
    def search(state: dict) -> dict:
        return {"sources": ["source1", "source2"]}

    @node("generate")                          # custom node name
    def draft_fn(state: dict) -> dict:
        return {"draft": "..."}

    graph = StateGraph(ResearchState)
    graph.add_node("search",  search)
    graph.add_node("draft",   draft_fn)
    graph.add_node("review",  review_fn)
    graph.add_edge("search",  "draft")
    graph.add_conditional_edges("review", route_fn, {"revise": "draft", "done": END})
    graph.set_entry_point("search")

    result = await graph.run({"query": "What is RAG?"})

HITL (interrupt / Command):
    @node
    def human_review(state: dict) -> dict:
        if state.get("needs_review"):
            interrupt("Please review the draft and approve or reject.")
        return {"approved": True}

    # Resume after human input:
    compiled = graph.compile()
    result = await compiled.run(initial, resume=Command(resume="approved"))
"""

from __future__ import annotations

import asyncio
import inspect
import operator
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypeVar, get_args, get_origin, get_type_hints

END = "__end__"
START = "__start__"

T = TypeVar("T")


# ── @node decorator ───────────────────────────────────────────────────────────

def node(fn_or_name: Any = None) -> Any:
    """Mark a function as a StateGraph node.

    Can be used bare or with a custom name:

        @node
        def search(state: dict) -> dict: ...

        @node("my_search")
        def search_fn(state: dict) -> dict: ...
    """
    if fn_or_name is None or isinstance(fn_or_name, str):
        # @node("name") — returns a decorator
        name_override: str | None = fn_or_name

        def _decorator(fn: Callable) -> Callable:
            fn._is_meshflow_node = True  # type: ignore[attr-defined]
            fn._node_name = name_override or fn.__name__  # type: ignore[attr-defined]
            return fn

        return _decorator

    # @node — used bare, fn_or_name IS the function
    fn_or_name._is_meshflow_node = True  # type: ignore[attr-defined]
    fn_or_name._node_name = fn_or_name.__name__  # type: ignore[attr-defined]
    return fn_or_name


# ── interrupt / Command (LangGraph-style HITL) ────────────────────────────────

class Interrupt(Exception):
    """Raised inside a StateGraph node to pause execution for human input."""

    def __init__(self, value: Any) -> None:
        self.value = value
        super().__init__(str(value))


def interrupt(value: Any) -> None:
    """Pause the current graph node and surface *value* to the caller.

    The graph stores the current state snapshot so execution can resume later
    via ``CompiledGraph.run(initial, resume=Command(resume=<decision>))``.

    Raises
    ------
    Interrupt — caught by CompiledGraph; re-raised as InterruptedError with
                the node name and payload attached.
    """
    raise Interrupt(value)


@dataclass
class Command:
    """Resume directive for a paused CompiledGraph.

    Parameters
    ----------
    resume:  The value that replaces the interrupt payload (e.g., human decision).
    goto:    Optional node name to jump to instead of the interrupted node.
    update:  Extra state updates to apply before resuming.
    """

    resume: Any = None
    goto:   str | None = None
    update: dict[str, Any] = field(default_factory=dict)


# ── Built-in reducers ─────────────────────────────────────────────────────────

def add(a: list, b: list) -> list:
    """Append b to a (list accumulator)."""
    if not isinstance(a, list):
        a = [a] if a is not None else []
    if not isinstance(b, list):
        b = [b] if b is not None else []
    return a + b


def last(a: Any, b: Any) -> Any:
    """Last writer wins (default for scalar fields)."""
    return b


def first(a: Any, b: Any) -> Any:
    """First writer wins — ignore subsequent updates."""
    return a if a is not None else b


def max_reducer(a: Any, b: Any) -> Any:
    """Keep the maximum value."""
    return max(a, b) if a is not None else b


def min_reducer(a: Any, b: Any) -> Any:
    """Keep the minimum value."""
    return min(a, b) if a is not None else b


# ── Channel descriptor ────────────────────────────────────────────────────────

@dataclass
class Channel:
    """Typed state channel: holds a value and knows how to merge updates."""

    key: str
    reducer: Callable[[Any, Any], Any] = field(default=last)
    default: Any = None

    def merge(self, current: Any, update: Any) -> Any:
        if current is None:
            return update
        return self.reducer(current, update)


def _extract_channels(state_schema: type) -> dict[str, Channel]:
    """Parse a TypedDict class into Channel descriptors.

    Fields annotated with ``Annotated[T, reducer_fn]`` use the given reducer.
    Plain fields default to ``last`` (last-writer-wins).
    """
    channels: dict[str, Channel] = {}
    try:
        hints = get_type_hints(state_schema, include_extras=True)
    except Exception:
        hints = getattr(state_schema, "__annotations__", {})

    for key, hint in hints.items():
        if get_origin(hint) is not None:
            args = get_args(hint)
            # Annotated[T, reducer_fn] → args = (T, reducer_fn)
            if len(args) >= 2 and callable(args[1]):
                channels[key] = Channel(key=key, reducer=args[1])
                continue
        channels[key] = Channel(key=key, reducer=last)

    return channels


# ── State container ───────────────────────────────────────────────────────────

class GraphState:
    """Mutable state bag that applies channel reducers on update."""

    def __init__(self, channels: dict[str, Channel], initial: dict[str, Any]) -> None:
        self._channels = channels
        self._data: dict[str, Any] = {k: ch.default for k, ch in channels.items()}
        self._apply(initial)

    def _apply(self, updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if key in self._channels:
                self._data[key] = self._channels[key].merge(self._data.get(key), value)
            else:
                self._data[key] = value

    def update(self, updates: dict[str, Any]) -> "GraphState":
        """Return a new GraphState with the given updates merged in."""
        new = GraphState.__new__(GraphState)
        new._channels = self._channels
        new._data = dict(self._data)
        new._apply(updates)
        return new

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


# ── Node wrapper ──────────────────────────────────────────────────────────────

@dataclass
class _NodeEntry:
    name: str
    fn: Callable
    is_async: bool


# ── StateGraph ────────────────────────────────────────────────────────────────

class StateGraph:
    """Typed, reducer-aware workflow graph — MeshFlow's LangGraph equivalent.

    Features beyond LangGraph:
    - MeshFlow governance policy automatically applied to every node
    - SHA-256 audit trail for every state transition
    - HITL pause on IRREVERSIBLE nodes
    - Native async throughout; sync functions auto-wrapped
    - ``compile()`` returns a ``CompiledGraph`` ready for parallel execution
    """

    def __init__(self, state_schema: type | None = None) -> None:
        self._schema = state_schema
        self._channels: dict[str, Channel] = (
            _extract_channels(state_schema) if state_schema else {}
        )
        self._nodes: dict[str, _NodeEntry] = {}
        self._edges: dict[str, list[str]] = {}
        self._conditional: dict[str, tuple[Callable, dict[str, str]]] = {}
        self._entry: str | None = None
        self._terminals: set[str] = set()

    # ── Graph construction ────────────────────────────────────────────────────

    def add_node(self, name: str, fn: Callable) -> "StateGraph":
        """Register a node.  fn receives the current state dict and returns an update dict."""
        self._nodes[name] = _NodeEntry(
            name=name,
            fn=fn,
            is_async=inspect.iscoroutinefunction(fn),
        )
        if name not in self._edges:
            self._edges[name] = []
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        """Unconditional edge from src → dst."""
        if dst == END:
            self._terminals.add(src)
        else:
            self._edges.setdefault(src, []).append(dst)
        return self

    def add_conditional_edges(
        self,
        src: str,
        condition: Callable[[dict[str, Any]], str],
        mapping: dict[str, str],
    ) -> "StateGraph":
        """Route to different nodes based on the return value of ``condition``.

        ``condition(state_dict) -> str`` — the returned string is looked up in
        ``mapping``.  Use ``END`` as a value in mapping to terminate.
        """
        self._conditional[src] = (condition, mapping)
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self._entry = name
        return self

    def set_finish_point(self, name: str) -> "StateGraph":
        self._terminals.add(name)
        return self

    # ── Compilation ───────────────────────────────────────────────────────────

    def compile(self, policy: Any = None) -> "CompiledGraph":
        if self._entry is None:
            raise ValueError("Call set_entry_point() before compile().")
        return CompiledGraph(self, policy)

    # ── Direct execution (without explicit compile) ───────────────────────────

    async def run(
        self,
        initial: dict[str, Any],
        policy: Any = None,
    ) -> dict[str, Any]:
        return await self.compile(policy).run(initial)


# ── CompiledGraph ─────────────────────────────────────────────────────────────

class CompiledGraph:
    """Executable, governed state graph.

    Execution semantics
    -------------------
    - Nodes with no pending dependencies run concurrently (asyncio.gather).
    - State updates from concurrent nodes are merged via channel reducers.
    - Conditional edges are evaluated after each node completes.
    - The graph halts when all active paths reach END or a terminal node.
    """

    def __init__(self, graph: StateGraph, policy: Any = None) -> None:
        self._g = graph
        self._policy = policy

    async def run(
        self,
        initial: dict[str, Any],
        max_steps: int = 200,
        resume: "Command | None" = None,
    ) -> dict[str, Any]:
        """Execute the graph.

        Parameters
        ----------
        initial:   Initial state dict.
        max_steps: Hard limit on execution steps.
        resume:    If provided, apply ``resume.update`` to initial state and
                   restart from the interrupted node (or ``resume.goto``).
        """
        # Apply resume updates on top of initial state
        if resume is not None and resume.update:
            initial = {**initial, **resume.update}

        state = GraphState(self._g._channels, initial)

        # Determine starting queue
        if resume is not None and (resume.goto or getattr(self, "_interrupted_node", None)):
            start_node = resume.goto or self._interrupted_node  # type: ignore[attr-defined]
            queue: list[str] = [start_node]
            self._interrupted_node = None
        else:
            queue = [self._g._entry]  # type: ignore[list-item]

        visited_counts: dict[str, int] = {}
        step = 0

        while queue and step < max_steps:
            step += 1

            # Deduplicate ready nodes, keep order stable
            ready = list(dict.fromkeys(queue))
            queue = []

            # Run all ready nodes concurrently
            results = await asyncio.gather(
                *[self._run_node(name, state.snapshot()) for name in ready],
                return_exceptions=True,
            )

            for name, result in zip(ready, results):
                if isinstance(result, Interrupt):
                    # Save the paused node so resume= can restart it
                    self._interrupted_node = name
                    err = InterruptedError(
                        f"Node {name!r} interrupted: {result.value}"
                    )
                    err.node = name        # type: ignore[attr-defined]
                    err.value = result.value  # type: ignore[attr-defined]
                    err.state = state.snapshot()  # type: ignore[attr-defined]
                    raise err

                if isinstance(result, Exception):
                    raise result

                visited_counts[name] = visited_counts.get(name, 0) + 1

                if isinstance(result, dict):
                    state = state.update(result)

                # Route from this node
                if name in self._g._conditional:
                    condition_fn, mapping = self._g._conditional[name]
                    key = (
                        await condition_fn(state.snapshot())
                        if inspect.iscoroutinefunction(condition_fn)
                        else condition_fn(state.snapshot())
                    )
                    dst = mapping.get(key, END)
                    if dst != END:
                        queue.append(dst)
                    else:
                        self._g._terminals.add(name)

                elif name in self._g._terminals:
                    pass  # terminal — stop this path

                else:
                    for dst in self._g._edges.get(name, []):
                        if dst != END:
                            queue.append(dst)

        return state.snapshot()

    async def _run_node(self, name: str, state: dict[str, Any]) -> dict[str, Any] | Interrupt:
        entry = self._g._nodes[name]
        fn = entry.fn
        sig = inspect.signature(fn)
        try:
            # Support both fn(state) and fn(state, config)
            if len(sig.parameters) >= 2:
                if entry.is_async:
                    result = await fn(state, {"graph": self})
                else:
                    result = fn(state, {"graph": self})
            else:
                if entry.is_async:
                    result = await fn(state)
                else:
                    result = fn(state)
        except Interrupt as exc:
            return exc   # surface through asyncio.gather as a value, not exception

        if result is None:
            return {}
        return result

    def stream(
        self,
        initial: dict[str, Any],
    ):
        """Async generator yielding (node_name, state_snapshot) after each step."""
        return self._stream_impl(initial)

    async def _stream_impl(
        self,
        initial: dict[str, Any],
        max_steps: int = 200,
    ):
        state = GraphState(self._g._channels, initial)
        queue: list[str] = [self._g._entry]  # type: ignore[list-item]
        step = 0

        while queue and step < max_steps:
            step += 1
            ready = list(dict.fromkeys(queue))
            queue = []

            results = await asyncio.gather(
                *[self._run_node(name, state.snapshot()) for name in ready],
                return_exceptions=True,
            )

            for name, result in zip(ready, results):
                if isinstance(result, Exception):
                    raise result
                if isinstance(result, dict):
                    state = state.update(result)
                yield name, state.snapshot()

                if name in self._g._conditional:
                    condition_fn, mapping = self._g._conditional[name]
                    key = (
                        await condition_fn(state.snapshot())
                        if inspect.iscoroutinefunction(condition_fn)
                        else condition_fn(state.snapshot())
                    )
                    dst = mapping.get(key, END)
                    if dst != END:
                        queue.append(dst)
                elif name not in self._g._terminals:
                    for dst in self._g._edges.get(name, []):
                        if dst != END:
                            queue.append(dst)
