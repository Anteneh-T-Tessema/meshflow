"""Sprint 68 — StateGraph enhancements: Send, subgraphs, add_sequence, checkpointers.

All tests are deterministic and require no API key.
"""

from __future__ import annotations

import pytest
from typing import Annotated, TypedDict

import meshflow
from meshflow.core.state import (
    StateGraph, add, Send,
    MemorySaver, SqliteSaver,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

class CountState(TypedDict):
    count: Annotated[int, lambda a, b: a + b]
    items: Annotated[list[str], add]
    last_node: str


async def _inc(state: dict) -> dict:
    return {"count": 1, "last_node": "inc"}


async def _double(state: dict) -> dict:
    return {"count": state.get("count", 0), "last_node": "double"}


async def _noop(state: dict) -> dict:
    return {}


# ══════════════════════════════════════════════════════════════════════════════
#  Send — map-reduce fan-out
# ══════════════════════════════════════════════════════════════════════════════

class TestSend:

    def test_send_dataclass_fields(self):
        s = Send(node="process", state={"x": 1})
        assert s.node == "process"
        assert s.state == {"x": 1}

    def test_send_default_empty_state(self):
        s = Send(node="foo")
        assert s.state == {}

    @pytest.mark.asyncio
    async def test_single_send_routes_to_node(self):
        class S(TypedDict):
            value: str

        async def start(state: dict) -> dict:
            return {}

        async def target(state: dict) -> dict:
            return {"value": "reached"}

        def router(state: dict) -> Send:
            return Send("target")

        graph = StateGraph(S)
        graph.add_node("start", start)
        graph.add_node("target", target)
        graph.add_conditional_edges("start", router)
        graph.set_entry_point("start")
        graph.set_finish_point("target")

        result = await graph.run({"value": ""})
        assert result["value"] == "reached"

    @pytest.mark.asyncio
    async def test_list_of_sends_fan_out(self):
        """fan-out: multiple Send objects spawn parallel branches."""

        class S(TypedDict):
            items: Annotated[list[str], add]

        async def split(state: dict) -> dict:
            return {}

        async def process(state: dict) -> dict:
            return {"items": [state.get("current", "x")]}

        def router(state: dict) -> list[Send]:
            return [
                Send("process", {"current": "a"}),
                Send("process", {"current": "b"}),
            ]

        graph = StateGraph(S)
        graph.add_node("split", split)
        graph.add_node("process", process)
        graph.add_conditional_edges("split", router)
        graph.set_entry_point("split")
        graph.set_finish_point("process")

        result = await graph.run({"items": []})
        assert "a" in result["items"] or "b" in result["items"]

    @pytest.mark.asyncio
    async def test_send_merges_state(self):
        """Send.state should be merged into graph state before the target runs."""

        class S(TypedDict):
            payload: str

        async def gate(state: dict) -> dict:
            return {}

        async def worker(state: dict) -> dict:
            return {"payload": state.get("payload", "") + "_processed"}

        def router(state: dict) -> Send:
            return Send("worker", {"payload": "injected"})

        graph = StateGraph(S)
        graph.add_node("gate", gate)
        graph.add_node("worker", worker)
        graph.add_conditional_edges("gate", router)
        graph.set_entry_point("gate")
        graph.set_finish_point("worker")

        result = await graph.run({"payload": ""})
        assert "injected" in result["payload"]

    def test_send_exported_from_meshflow(self):
        assert hasattr(meshflow, "Send")
        assert "Send" in meshflow.__all__


# ══════════════════════════════════════════════════════════════════════════════
#  add_sequence
# ══════════════════════════════════════════════════════════════════════════════

class TestAddSequence:

    @pytest.mark.asyncio
    async def test_sequence_runs_in_order(self):
        order: list[str] = []

        async def step_a(state: dict) -> dict:
            order.append("a")
            return {}

        async def step_b(state: dict) -> dict:
            order.append("b")
            return {}

        async def step_c(state: dict) -> dict:
            order.append("c")
            return {}

        graph = StateGraph()
        graph.add_sequence([("a", step_a), ("b", step_b), ("c", step_c)])
        graph.set_entry_point("a")
        graph.set_finish_point("c")

        await graph.run({})
        assert order == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_sequence_state_flows_through(self):
        class S(TypedDict):
            value: Annotated[int, lambda a, b: a + b]

        async def add1(state: dict) -> dict:
            return {"value": 1}

        async def add2(state: dict) -> dict:
            return {"value": 2}

        graph = StateGraph(S)
        graph.add_sequence([("add1", add1), ("add2", add2)])
        graph.set_entry_point("add1")
        graph.set_finish_point("add2")

        result = await graph.run({"value": 0})
        assert result["value"] == 3

    def test_sequence_registers_all_nodes(self):
        async def f(s): return {}
        async def g(s): return {}

        graph = StateGraph()
        graph.add_sequence([("f", f), ("g", g)])
        assert "f" in graph._nodes
        assert "g" in graph._nodes

    def test_sequence_wires_edges(self):
        async def f(s): return {}
        async def g(s): return {}
        async def h(s): return {}

        graph = StateGraph()
        graph.add_sequence([("f", f), ("g", g), ("h", h)])
        assert "g" in graph._edges.get("f", [])
        assert "h" in graph._edges.get("g", [])

    def test_sequence_single_node(self):
        async def solo(s): return {}
        graph = StateGraph()
        graph.add_sequence([("solo", solo)])
        assert "solo" in graph._nodes

    def test_add_sequence_returns_graph_for_chaining(self):
        async def f(s): return {}
        graph = StateGraph()
        result = graph.add_sequence([("f", f)])
        assert result is graph


# ══════════════════════════════════════════════════════════════════════════════
#  Subgraph nesting
# ══════════════════════════════════════════════════════════════════════════════

class TestSubgraphNesting:

    @pytest.mark.asyncio
    async def test_compiled_graph_as_node(self):
        """A CompiledGraph used as a node should run and merge its output."""

        class S(TypedDict):
            value: Annotated[int, lambda a, b: a + b]

        async def inner_node(state: dict) -> dict:
            return {"value": 10}

        inner = StateGraph(S)
        inner.add_node("inner_node", inner_node)
        inner.set_entry_point("inner_node")
        inner.set_finish_point("inner_node")
        compiled_inner = inner.compile()

        async def outer_start(state: dict) -> dict:
            return {"value": 5}

        outer = StateGraph(S)
        outer.add_node("start", outer_start)
        outer.add_node("sub", compiled_inner)     # <-- subgraph as node
        outer.add_edge("start", "sub")
        outer.set_entry_point("start")
        outer.set_finish_point("sub")

        result = await outer.run({"value": 0})
        # outer_start adds 5 → state=5; inner returns accumulated 5+10=15;
        # outer reducer adds that: 5+15=20
        assert result["value"] == 20

    @pytest.mark.asyncio
    async def test_subgraph_receives_parent_state(self):
        """Subgraph node gets the current state snapshot as input."""

        class S(TypedDict):
            msg: str

        async def inner_echo(state: dict) -> dict:
            return {"msg": state.get("msg", "") + "_inner"}

        inner = StateGraph(S)
        inner.add_node("echo", inner_echo)
        inner.set_entry_point("echo")
        inner.set_finish_point("echo")

        outer = StateGraph(S)
        outer.add_node("sub", inner.compile())
        outer.set_entry_point("sub")
        outer.set_finish_point("sub")

        result = await outer.run({"msg": "hello"})
        assert result["msg"] == "hello_inner"


# ══════════════════════════════════════════════════════════════════════════════
#  MemorySaver
# ══════════════════════════════════════════════════════════════════════════════

class TestMemorySaver:

    def test_put_and_get(self):
        saver = MemorySaver()
        saver.put("t1", {"x": 1})
        assert saver.get("t1") == {"x": 1}

    def test_get_nonexistent_returns_none(self):
        assert MemorySaver().get("nope") is None

    def test_overwrite(self):
        saver = MemorySaver()
        saver.put("t1", {"x": 1})
        saver.put("t1", {"x": 99})
        assert saver.get("t1") == {"x": 99}

    def test_delete_existing(self):
        saver = MemorySaver()
        saver.put("t1", {"x": 1})
        assert saver.delete("t1") is True
        assert saver.get("t1") is None

    def test_delete_nonexistent(self):
        assert MemorySaver().delete("nope") is False

    def test_list_threads(self):
        saver = MemorySaver()
        saver.put("a", {})
        saver.put("b", {})
        threads = saver.list_threads()
        assert "a" in threads and "b" in threads

    def test_isolation_between_threads(self):
        saver = MemorySaver()
        saver.put("t1", {"val": 1})
        saver.put("t2", {"val": 2})
        t1 = saver.get("t1")
        t2 = saver.get("t2")
        assert t1 is not None and t1["val"] == 1
        assert t2 is not None and t2["val"] == 2

    def test_exported_from_meshflow(self):
        assert hasattr(meshflow, "MemorySaver")

    @pytest.mark.asyncio
    async def test_graph_saves_state_after_run(self):
        class S(TypedDict):
            result: str

        async def worker(state: dict) -> dict:
            return {"result": "done"}

        saver = MemorySaver()
        graph = StateGraph(S)
        graph.add_node("worker", worker)
        graph.set_entry_point("worker")
        graph.set_finish_point("worker")
        compiled = graph.compile(checkpointer=saver)

        await compiled.run({"result": ""}, config={"thread_id": "run1"})
        saved = saver.get("run1")
        assert saved is not None
        assert saved["result"] == "done"

    @pytest.mark.asyncio
    async def test_graph_loads_saved_state(self):
        """A second run with the same thread_id starts from the saved state."""

        class S(TypedDict):
            count: Annotated[int, lambda a, b: a + b]

        async def inc(state: dict) -> dict:
            return {"count": 1}

        saver = MemorySaver()
        graph = StateGraph(S)
        graph.add_node("inc", inc)
        graph.set_entry_point("inc")
        graph.set_finish_point("inc")
        compiled = graph.compile(checkpointer=saver)

        await compiled.run({"count": 0}, config={"thread_id": "sess"})
        await compiled.run({"count": 0}, config={"thread_id": "sess"})
        final = saver.get("sess")
        assert final is not None
        # After two runs: 0 + 1 = 1 (saved), then 1 (loaded) + 1 = 2
        assert final["count"] == 2

    def test_get_state_method(self):
        class S(TypedDict):
            x: int

        saver = MemorySaver()
        saver.put("t1", {"x": 42})
        graph = StateGraph(S)

        async def fn(s): return {}
        graph.add_node("fn", fn)
        graph.set_entry_point("fn")
        graph.set_finish_point("fn")
        compiled = graph.compile(checkpointer=saver)

        state = compiled.get_state({"thread_id": "t1"})
        assert state is not None and state["x"] == 42

    def test_get_state_without_checkpointer_raises(self):
        graph = StateGraph()
        async def fn(s): return {}
        graph.add_node("fn", fn)
        graph.set_entry_point("fn")
        compiled = graph.compile()

        with pytest.raises(RuntimeError, match="checkpointer"):
            compiled.get_state({"thread_id": "t1"})

    def test_update_state_method(self):
        class S(TypedDict):
            x: int

        saver = MemorySaver()
        saver.put("t1", {"x": 1})

        graph = StateGraph(S)
        async def fn(s): return {}
        graph.add_node("fn", fn)
        graph.set_entry_point("fn")
        graph.set_finish_point("fn")
        compiled = graph.compile(checkpointer=saver)

        compiled.update_state({"thread_id": "t1"}, {"x": 99})
        t1 = saver.get("t1")
        assert t1 is not None and t1["x"] == 99

    def test_update_state_without_checkpointer_raises(self):
        graph = StateGraph()
        async def fn(s): return {}
        graph.add_node("fn", fn)
        graph.set_entry_point("fn")
        compiled = graph.compile()

        with pytest.raises(RuntimeError, match="checkpointer"):
            compiled.update_state({"thread_id": "t1"}, {"x": 1})


# ══════════════════════════════════════════════════════════════════════════════
#  SqliteSaver
# ══════════════════════════════════════════════════════════════════════════════

class TestSqliteSaver:

    def _saver(self):
        return SqliteSaver(":memory:")

    def test_put_and_get(self):
        saver = self._saver()
        saver.put("t1", {"x": 1, "msg": "hello"})
        assert saver.get("t1") == {"x": 1, "msg": "hello"}

    def test_get_nonexistent_returns_none(self):
        assert self._saver().get("nope") is None

    def test_overwrite(self):
        saver = self._saver()
        saver.put("t1", {"x": 1})
        saver.put("t1", {"x": 99})
        assert saver.get("t1")["x"] == 99

    def test_delete_existing(self):
        saver = self._saver()
        saver.put("t1", {"x": 1})
        assert saver.delete("t1") is True
        assert saver.get("t1") is None

    def test_delete_nonexistent(self):
        assert self._saver().delete("nope") is False

    def test_list_threads(self):
        saver = self._saver()
        saver.put("a", {})
        saver.put("b", {})
        threads = saver.list_threads()
        assert "a" in threads and "b" in threads

    def test_exported_from_meshflow(self):
        assert hasattr(meshflow, "SqliteSaver")

    @pytest.mark.asyncio
    async def test_graph_with_sqlite_checkpointer(self):
        class S(TypedDict):
            result: str

        async def worker(state: dict) -> dict:
            return {"result": "persisted"}

        saver = self._saver()
        graph = StateGraph(S)
        graph.add_node("worker", worker)
        graph.set_entry_point("worker")
        graph.set_finish_point("worker")
        compiled = graph.compile(checkpointer=saver)

        await compiled.run({"result": ""}, config={"thread_id": "sql-run"})
        saved = saver.get("sql-run")
        assert saved is not None and saved["result"] == "persisted"


# ══════════════════════════════════════════════════════════════════════════════
#  add_conditional_edges — optional mapping (Send-only routing)
# ══════════════════════════════════════════════════════════════════════════════

class TestConditionalEdgesNoMapping:

    @pytest.mark.asyncio
    async def test_string_routing_without_mapping(self):
        """When mapping=None, the condition return value is used directly as node name."""

        class S(TypedDict):
            path: str

        async def gate(state: dict) -> dict:
            return {}

        async def left(state: dict) -> dict:
            return {"path": "left"}

        async def right(state: dict) -> dict:
            return {"path": "right"}

        def router(state: dict) -> str:
            return "left"

        graph = StateGraph(S)
        graph.add_node("gate", gate)
        graph.add_node("left", left)
        graph.add_node("right", right)
        graph.add_conditional_edges("gate", router)   # no mapping
        graph.set_entry_point("gate")
        graph.set_finish_point("left")
        graph.set_finish_point("right")

        result = await graph.run({"path": ""})
        assert result["path"] == "left"


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_send_in_all(self):
        assert "Send" in meshflow.__all__

    def test_memory_saver_in_all(self):
        assert "MemorySaver" in meshflow.__all__

    def test_sqlite_saver_in_all(self):
        assert "SqliteSaver" in meshflow.__all__

    def test_version_bumped(self):
        assert meshflow.__version__ >= "0.77.0"
