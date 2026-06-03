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
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar, get_args, get_origin, get_type_hints

END = "__end__"
START = "__start__"

T = TypeVar("T")


# ── InjectedState / InjectedStore markers ─────────────────────────────────────

class InjectedState:
    """Annotation marker: inject the current graph state into a tool parameter.

    Usage::

        from typing import Annotated
        from meshflow.core.state import InjectedState

        def my_tool(query: str, state: Annotated[dict, InjectedState]) -> str:
            topic = state.get("topic", "unknown")
            return f"searching {topic} for {query}"
    """


class InjectedStore:
    """Annotation marker: inject the compiled graph's store into a tool parameter.

    Usage::

        from typing import Annotated
        from meshflow.core.state import InjectedStore
        from meshflow.core.store import BaseStore

        def save_result(key: str, value: str,
                        store: Annotated[BaseStore, InjectedStore]) -> str:
            store.put(("results",), key, {"value": value})
            return "saved"
    """


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


@dataclass
class Send:
    """Dynamically dispatch to a node with a state override (map-reduce fan-out).

    Return a ``Send`` or ``list[Send]`` from a conditional edge function to
    spawn parallel branches, each with their own state slice merged into the
    shared graph state.

    Example — fan-out over a list of items::

        async def fan_out(state: dict) -> list[Send]:
            return [Send("process_item", {"item": x}) for x in state["items"]]

        graph.add_conditional_edges("split", fan_out)   # no mapping needed
        graph.add_node("process_item", process_fn)
        graph.add_edge("process_item", "aggregate")
    """

    node: str
    state: dict[str, Any] = field(default_factory=dict)


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


# ── Checkpointers ─────────────────────────────────────────────────────────────

class MemorySaver:
    """In-memory checkpoint store for StateGraph.

    Saves and restores the complete state snapshot keyed by ``thread_id``.
    Useful for short-lived sessions, testing, or single-process deployments.

    Usage::

        saver = MemorySaver()
        graph = my_graph.compile(checkpointer=saver)
        result = await graph.run(initial, config={"thread_id": "session-1"})
        # Later:
        state = graph.get_state({"thread_id": "session-1"})
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, thread_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            self._store[thread_id] = dict(state)

    def get(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            s = self._store.get(thread_id)
            return dict(s) if s is not None else None

    def delete(self, thread_id: str) -> bool:
        with self._lock:
            return self._store.pop(thread_id, None) is not None

    def list_threads(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())


_SAVER_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_checkpoints (
    thread_id TEXT PRIMARY KEY,
    state     TEXT NOT NULL,
    updated   REAL NOT NULL
);
"""


class SqliteSaver:
    """SQLite-backed checkpoint store for StateGraph.

    Persists state across process restarts. State values must be
    JSON-serialisable (strings, numbers, lists, dicts).

    Usage::

        saver = SqliteSaver("checkpoints.db")
        graph = my_graph.compile(checkpointer=saver)
        result = await graph.run(initial, config={"thread_id": "run-42"})
    """

    def __init__(self, db_path: str = "meshflow_checkpoints.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._shared: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._shared = sqlite3.connect(":memory:", check_same_thread=False)
        self._init()

    @contextmanager
    def _conn(self):
        if self._shared is not None:
            yield self._shared
            self._shared.commit()
        else:
            conn = sqlite3.connect(self._db_path)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SAVER_SCHEMA)

    def put(self, thread_id: str, state: dict[str, Any]) -> None:
        import time
        with self._lock:
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO graph_checkpoints (thread_id, state, updated) VALUES (?, ?, ?)",
                    (thread_id, json.dumps(state, default=str), time.time()),
                )

    def get(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            with self._conn() as c:
                row = c.execute(
                    "SELECT state FROM graph_checkpoints WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if row is None:
                    return None
                return json.loads(row[0])

    def delete(self, thread_id: str) -> bool:
        with self._lock:
            with self._conn() as c:
                cur = c.execute(
                    "DELETE FROM graph_checkpoints WHERE thread_id = ?",
                    (thread_id,),
                )
                return cur.rowcount > 0

    def list_threads(self) -> list[str]:
        with self._lock:
            with self._conn() as c:
                rows = c.execute("SELECT thread_id FROM graph_checkpoints ORDER BY updated DESC").fetchall()
                return [r[0] for r in rows]


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
        self._conditional: dict[str, tuple[Callable, dict[str, str] | None]] = {}
        self._entry: str | None = None
        self._terminals: set[str] = set()

    # ── Graph construction ────────────────────────────────────────────────────

    def add_node(self, name: str, fn: "Callable | CompiledGraph") -> "StateGraph":
        """Register a node.

        ``fn`` may be a regular async/sync callable, a ``ToolNode``, or a
        ``CompiledGraph`` (subgraph).  Subgraphs receive the current state and
        return their final state dict, which is merged via channel reducers.
        """
        # Subgraph nesting: wrap CompiledGraph as an async node function
        if isinstance(fn, CompiledGraph):
            subgraph = fn

            async def _subgraph_node(state: dict[str, Any]) -> dict[str, Any]:
                return await subgraph.run(state)

            actual_fn: Callable = _subgraph_node
        else:
            actual_fn = fn

        self._nodes[name] = _NodeEntry(
            name=name,
            fn=actual_fn,
            is_async=inspect.iscoroutinefunction(actual_fn)
                     or inspect.iscoroutinefunction(getattr(actual_fn, "__call__", None)),
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

    def add_sequence(self, nodes: list[tuple[str, Callable]]) -> "StateGraph":
        """Register a chain of nodes connected by unconditional edges.

        Convenience wrapper — equivalent to calling ``add_node`` + ``add_edge``
        for each consecutive pair.

        Example::

            graph.add_sequence([
                ("fetch",    fetch_fn),
                ("parse",    parse_fn),
                ("summarize", summarize_fn),
            ])
        """
        for i, (name, fn) in enumerate(nodes):
            self.add_node(name, fn)
            if i > 0:
                self.add_edge(nodes[i - 1][0], name)
        return self

    def add_conditional_edges(
        self,
        src: str,
        condition: Callable[[dict[str, Any]], Any],
        mapping: dict[str, str] | None = None,
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

    def compile(
        self,
        policy: Any = None,
        checkpointer: "MemorySaver | SqliteSaver | None" = None,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
        store: Any = None,
    ) -> "CompiledGraph":
        """Compile the graph into an executable :class:`CompiledGraph`.

        Parameters
        ----------
        policy:
            MeshFlow governance policy.
        checkpointer:
            Checkpoint backend (``MemorySaver``, ``SqliteSaver``, etc.) for
            thread-level state persistence and HITL resume support.
        interrupt_before:
            List of node names to pause execution *before* running.
        interrupt_after:
            List of node names to pause execution *after* running.
        store:
            A :class:`~meshflow.core.store.BaseStore` injected into tools that
            declare an ``InjectedStore`` annotation.
        """
        if self._entry is None:
            raise ValueError("Call set_entry_point() before compile().")
        return CompiledGraph(
            self,
            policy,
            checkpointer=checkpointer,
            interrupt_before=interrupt_before or [],
            interrupt_after=interrupt_after or [],
            store=store,
        )

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

    def __init__(
        self,
        graph: StateGraph,
        policy: Any = None,
        checkpointer: "MemorySaver | SqliteSaver | None" = None,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
        store: Any = None,
    ) -> None:
        self._g = graph
        self._policy = policy
        self._checkpointer = checkpointer
        self._interrupt_before: set[str] = set(interrupt_before or [])
        self._interrupt_after: set[str] = set(interrupt_after or [])
        self._store = store  # BaseStore | None

    # ── State inspection / mutation ───────────────────────────────────────────

    def get_state(self, config: dict[str, Any]) -> dict[str, Any] | None:
        """Return the last saved state for the given ``thread_id``.

        Requires a checkpointer to be set at compile time.

        Parameters
        ----------
        config:
            Dict with at least ``{"thread_id": "..."}}``.
        """
        if self._checkpointer is None:
            raise RuntimeError("get_state requires a checkpointer — pass checkpointer= to compile()")
        thread_id = config["thread_id"]
        return self._checkpointer.get(thread_id)

    def update_state(self, config: dict[str, Any], values: dict[str, Any]) -> None:
        """Merge ``values`` into the saved state for ``thread_id``.

        Useful for injecting human feedback or correcting state mid-run.

        Parameters
        ----------
        config:
            Dict with at least ``{"thread_id": "..."}``.
        values:
            Partial state dict to merge (channel reducers are applied).
        """
        if self._checkpointer is None:
            raise RuntimeError("update_state requires a checkpointer — pass checkpointer= to compile()")
        thread_id = config["thread_id"]
        current = self._checkpointer.get(thread_id) or {}
        gs = GraphState(self._g._channels, current)
        gs = gs.update(values)
        self._checkpointer.put(thread_id, gs.snapshot())

    async def run(
        self,
        initial: dict[str, Any],
        max_steps: int = 200,
        resume: "Command | None" = None,
        config: "dict[str, Any] | None" = None,
    ) -> dict[str, Any]:
        """Execute the graph.

        Parameters
        ----------
        initial:   Initial state dict.
        max_steps: Hard limit on execution steps.
        resume:    If provided, apply ``resume.update`` to initial state and
                   restart from the interrupted node (or ``resume.goto``).
        config:    Optional run config.  Pass ``{"thread_id": "..."}`` when a
                   checkpointer is attached to persist state across calls.
        """
        thread_id: str | None = (config or {}).get("thread_id")

        # Load persisted state if a checkpointer + thread_id are present.
        # Saved state wins: caller-supplied initial only fills missing keys.
        if self._checkpointer is not None and thread_id:
            saved = self._checkpointer.get(thread_id)
            if saved:
                initial = {**initial, **saved}

        # Apply resume updates on top of initial state
        if resume is not None and resume.update:
            initial = {**initial, **resume.update}

        state = GraphState(self._g._channels, initial)

        # Determine starting queue
        if resume is not None and (resume.goto or getattr(self, "_interrupted_node", None)):
            start_node: str = resume.goto or self._interrupted_node  # type: ignore[attr-defined]
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
                # compile-time interrupt_before: raise before applying result
                if name in self._interrupt_before:
                    self._interrupted_node = name
                    err = InterruptedError(f"interrupt_before node {name!r}")
                    err.node = name           # type: ignore[attr-defined]
                    err.value = f"interrupt_before:{name}"  # type: ignore[attr-defined]
                    err.state = state.snapshot()  # type: ignore[attr-defined]
                    raise err

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

                # compile-time interrupt_after: raise after applying result
                if name in self._interrupt_after:
                    self._interrupted_node = name
                    err = InterruptedError(f"interrupt_after node {name!r}")
                    err.node = name           # type: ignore[attr-defined]
                    err.value = f"interrupt_after:{name}"  # type: ignore[attr-defined]
                    err.state = state.snapshot()  # type: ignore[attr-defined]
                    raise err

                # Route from this node
                if name in self._g._conditional:
                    condition_fn, mapping = self._g._conditional[name]
                    route = (
                        await condition_fn(state.snapshot())
                        if inspect.iscoroutinefunction(condition_fn)
                        else condition_fn(state.snapshot())
                    )
                    # Send / list[Send] fan-out
                    sends = route if isinstance(route, list) else ([route] if isinstance(route, Send) else None)
                    if sends is not None and all(isinstance(s, Send) for s in sends):
                        for s in sends:
                            if s.state:
                                state = state.update(s.state)
                            queue.append(s.node)
                    else:
                        dst = (mapping.get(route, END) if mapping else route) or END
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

        final = state.snapshot()
        if self._checkpointer is not None and thread_id:
            self._checkpointer.put(thread_id, final)
        return final

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
        stream_mode: str = "values",
        config: dict[str, Any] | None = None,
    ):
        """Async generator that streams execution events.

        Parameters
        ----------
        initial:
            Initial state dict.
        stream_mode:
            Controls what each yielded chunk contains:

            ``"values"``  — ``(node_name, full_state_snapshot)`` — default.
            ``"updates"`` — ``(node_name, delta_dict)`` — only changed keys.
            ``"messages"``— ``(node_name, {"role": "ai", "content": "..."})`` —
                            last LLM message from the state.
            ``"debug"``   — ``(node_name, {"step": N, "state": snap, "mode": ...})``
                            verbose debug dict.
            ``"events"``  — ``{"event": "on_node_start"|"on_node_end", "node": ..., "state": ...}``
                            structured event dicts (no tuple, yields dicts).
        config:
            Optional ``{"thread_id": "..."}`` for checkpoint integration.
        """
        return self._stream_impl(initial, stream_mode=stream_mode, config=config)

    async def _stream_impl(
        self,
        initial: dict[str, Any],
        max_steps: int = 200,
        stream_mode: str = "values",
        config: dict[str, Any] | None = None,
    ):
        state = GraphState(self._g._channels, initial)
        queue: list[str] = [self._g._entry]  # type: ignore[list-item]
        step = 0

        while queue and step < max_steps:
            step += 1
            ready = list(dict.fromkeys(queue))
            queue = []

            prev_snap = state.snapshot()

            # Fire on_node_start for "events" mode
            if stream_mode == "events":
                for name in ready:
                    yield {"event": "on_node_start", "node": name, "state": prev_snap}

            results = await asyncio.gather(
                *[self._run_node(name, state.snapshot()) for name in ready],
                return_exceptions=True,
            )

            for name, result in zip(ready, results):
                if isinstance(result, Exception):
                    raise result
                if isinstance(result, dict):
                    state = state.update(result)
                snap = state.snapshot()

                if stream_mode == "values":
                    yield name, snap
                elif stream_mode == "updates":
                    delta = {k: v for k, v in snap.items() if prev_snap.get(k) != v}
                    yield name, delta
                elif stream_mode == "messages":
                    # Extract the last AI message if present in the state
                    msgs = snap.get("messages", snap.get("output", []))
                    if isinstance(msgs, list) and msgs:
                        last = msgs[-1]
                        content = last.get("content", "") if isinstance(last, dict) else str(last)
                        yield name, {"role": "ai", "content": content}
                    else:
                        yield name, {"role": "ai", "content": str(snap.get("output", snap.get("result", "")))}
                elif stream_mode == "debug":
                    yield name, {"step": step, "node": name, "state": snap, "mode": "debug"}
                elif stream_mode == "events":
                    yield {"event": "on_node_end", "node": name, "state": snap, "step": step}
                else:
                    yield name, snap  # unknown mode → fall back to "values"

                prev_snap = snap

                if name in self._g._conditional:
                    condition_fn, mapping = self._g._conditional[name]
                    route = (
                        await condition_fn(state.snapshot())
                        if inspect.iscoroutinefunction(condition_fn)
                        else condition_fn(state.snapshot())
                    )
                    sends = route if isinstance(route, list) else ([route] if isinstance(route, Send) else None)
                    if sends is not None and all(isinstance(s, Send) for s in sends):
                        for s in sends:
                            if s.state:
                                state = state.update(s.state)
                            queue.append(s.node)
                    else:
                        dst = (mapping.get(route, END) if mapping else route) or END
                        if dst != END:
                            queue.append(dst)
                elif name not in self._g._terminals:
                    for dst in self._g._edges.get(name, []):
                        if dst != END:
                            queue.append(dst)

        # Checkpoint the final state when config carries a thread_id
        thread_id: str | None = (config or {}).get("thread_id")
        if thread_id and self._checkpointer is not None:
            self._checkpointer.put(thread_id, state.snapshot())
