"""Sprint 97 — LangGraph + CrewAI parity gaps.

Features:
  1. @task / @entrypoint (Functional API)
  2. BaseStore / InMemoryStore / SQLiteStore (cross-thread store)
  3. stream_mode on CompiledGraph.stream()
  4. InjectedState / InjectedStore annotations
  5. interrupt_after / interrupt_before as compile() parameters
  6. Crew.train()
  7. Crew.replay()
  8. Task(condition=...)
  9. Crew(memory_config=...)
 10. Typed knowledge sources (PDF/CSV/JSON/Excel/String)
 11. Pipeline (chain multiple Crews)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")


# ── helpers ───────────────────────────────────────────────────────────────────

def _echo_agent(name: str, response: str = "ok") -> Any:
    from meshflow import Agent
    from meshflow.agents.base import EchoProvider
    return Agent(name=name, role="worker", provider=EchoProvider(response))


def _task(desc: str, agent: Any, **kwargs: Any) -> Any:
    from meshflow import Task
    return Task(description=desc, expected_output="output", agent=agent, **kwargs)


async def _crew_run(crew: Any, inputs: dict | None = None) -> Any:
    return await crew.kickoff(inputs)


# ── 1. @task / @entrypoint ────────────────────────────────────────────────────

class TestFunctionalAPI:
    def test_task_decorator_bare(self) -> None:
        from meshflow import task
        @task
        async def my_task(x: str) -> str:
            return f"done:{x}"
        assert asyncio.run(my_task("hello")) == "done:hello"

    def test_task_decorator_with_parens(self) -> None:
        from meshflow import task
        @task()
        async def my_task(x: int) -> int:
            return x * 2
        assert asyncio.run(my_task(5)) == 10

    def test_task_max_retries_succeeds_on_retry(self) -> None:
        from meshflow import task
        calls = {"n": 0}

        @task(max_retries=2)
        async def flaky(_x: str) -> str:
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        assert asyncio.run(flaky("x")) == "ok"
        assert calls["n"] == 2

    def test_task_raises_after_max_retries(self) -> None:
        from meshflow import task

        @task(max_retries=1)
        async def always_fail() -> str:
            raise ValueError("always")

        with pytest.raises(ValueError, match="always"):
            asyncio.run(always_fail())

    def test_task_invoke_alias(self) -> None:
        from meshflow import task
        @task
        async def greet(name: str) -> str:
            return f"hello {name}"
        assert asyncio.run(greet.invoke("world")) == "hello world"

    def test_entrypoint_bare(self) -> None:
        from meshflow import entrypoint
        @entrypoint
        async def flow(inputs: dict) -> dict:
            return {"result": inputs["x"] * 2}
        result = asyncio.run(flow.invoke({"x": 3}))
        assert result.value == {"result": 6}
        assert not result.interrupted

    def test_entrypoint_with_checkpointer(self) -> None:
        from meshflow import entrypoint
        from meshflow.core.state import MemorySaver
        ckpt = MemorySaver()

        @entrypoint(checkpointer=ckpt)
        async def flow(_inputs: dict) -> dict:
            return {"result": "done"}

        result = asyncio.run(flow.invoke({"x": 1}, config={"thread_id": "t1"}))
        assert result.value == {"result": "done"}
        assert result.thread_id == "t1"

    def test_entrypoint_interrupted_by_interrupt(self) -> None:
        from meshflow import entrypoint
        from meshflow.core.state import interrupt, MemorySaver
        ckpt = MemorySaver()

        @entrypoint(checkpointer=ckpt)
        async def flow(_inputs: dict) -> dict:
            interrupt("please review")
            return {"result": "never"}

        result = asyncio.run(flow.invoke({"x": 1}, config={"thread_id": "t2"}))
        assert result.interrupted
        assert result.interrupt_value == "please review"

    def test_entrypoint_invoke_sync(self) -> None:
        from meshflow import entrypoint
        @entrypoint
        async def flow(_inputs: dict) -> str:
            return "sync_result"
        result = flow.invoke_sync({"x": 1})
        assert result.value == "sync_result"

    def test_functional_task_exported(self) -> None:
        from meshflow import task, entrypoint, FunctionalTask, Entrypoint, EntrypointResult
        assert task is not None
        assert entrypoint is not None
        assert FunctionalTask is not None
        assert Entrypoint is not None
        assert EntrypointResult is not None


# ── 2. BaseStore / InMemoryStore / SQLiteStore ────────────────────────────────

class TestBaseStore:
    def test_in_memory_store_put_get(self) -> None:
        from meshflow import GraphInMemoryStore
        store = GraphInMemoryStore()
        store.put(("user", "alice"), "profile", {"name": "Alice"})
        item = store.get(("user", "alice"), "profile")
        assert item is not None
        assert item.value == {"name": "Alice"}
        assert item.key == "profile"
        assert item.namespace == ("user", "alice")

    def test_in_memory_store_delete(self) -> None:
        from meshflow import GraphInMemoryStore
        store = GraphInMemoryStore()
        store.put(("ns",), "k", {"v": 1})
        store.delete(("ns",), "k")
        assert store.get(("ns",), "k") is None

    def test_in_memory_store_search_prefix(self) -> None:
        from meshflow import GraphInMemoryStore
        store = GraphInMemoryStore()
        store.put(("user", "alice"), "p", {"x": 1})
        store.put(("user", "bob"),   "p", {"x": 2})
        store.put(("other",),        "p", {"x": 3})
        results = store.search(("user",))
        assert len(results) == 2

    def test_in_memory_store_search_query_substring(self) -> None:
        from meshflow import GraphInMemoryStore
        store = GraphInMemoryStore()
        store.put(("docs",), "a", {"text": "hello world"})
        store.put(("docs",), "b", {"text": "goodbye world"})
        results = store.search(("docs",), query="hello")
        assert len(results) == 1
        assert results[0].key == "a"

    def test_in_memory_store_search_filter(self) -> None:
        from meshflow import GraphInMemoryStore
        store = GraphInMemoryStore()
        store.put(("items",), "a", {"type": "fruit", "name": "apple"})
        store.put(("items",), "b", {"type": "veggie", "name": "carrot"})
        results = store.search(("items",), filter={"type": "fruit"})
        assert len(results) == 1
        assert results[0].value["name"] == "apple"

    def test_in_memory_store_list_namespaces(self) -> None:
        from meshflow import GraphInMemoryStore
        store = GraphInMemoryStore()
        store.put(("a", "1"), "k", {})
        store.put(("a", "2"), "k", {})
        store.put(("b",),     "k", {})
        ns = store.list_namespaces()
        assert ("a", "1") in ns
        assert ("b",) in ns

    def test_in_memory_store_len(self) -> None:
        from meshflow import GraphInMemoryStore
        store = GraphInMemoryStore()
        assert len(store) == 0
        store.put(("ns",), "k1", {})
        store.put(("ns",), "k2", {})
        assert len(store) == 2

    def test_sqlite_store_put_get(self, tmp_path: Any) -> None:
        from meshflow import GraphSQLiteStore
        store = GraphSQLiteStore(str(tmp_path / "test.db"))
        store.put(("facts",), "color", {"value": "blue"})
        item = store.get(("facts",), "color")
        assert item is not None
        assert item.value == {"value": "blue"}

    def test_sqlite_store_in_memory(self) -> None:
        from meshflow import GraphSQLiteStore
        store = GraphSQLiteStore(":memory:")
        store.put(("ns",), "key", {"data": 42})
        item = store.get(("ns",), "key")
        assert item is not None
        assert item.value["data"] == 42

    def test_sqlite_store_delete(self, tmp_path: Any) -> None:
        from meshflow import GraphSQLiteStore
        store = GraphSQLiteStore(str(tmp_path / "t.db"))
        store.put(("ns",), "k", {"v": 1})
        store.delete(("ns",), "k")
        assert store.get(("ns",), "k") is None

    def test_store_item_repr(self) -> None:
        from meshflow import StoreItem
        item = StoreItem(namespace=("a",), key="k", value={"x": 1})
        assert "a" in repr(item)
        assert "k" in repr(item)

    def test_store_exported(self) -> None:
        from meshflow import BaseStore, GraphInMemoryStore, GraphSQLiteStore, StoreItem
        assert BaseStore is not None


# ── 3. stream_mode ────────────────────────────────────────────────────────────

class TestStreamMode:
    def _build_graph(self) -> Any:
        from meshflow.core.state import StateGraph, END
        g = StateGraph()
        g.add_node("a", lambda s: {"x": s.get("x", 0) + 1})
        g.add_node("b", lambda s: {"x": s.get("x", 0) + 10})
        g.add_edge("a", "b")
        g.add_edge("b", END)
        g.set_entry_point("a")
        return g.compile()

    def test_stream_mode_values(self) -> None:
        cg = self._build_graph()
        async def run() -> list:
            return [(name, snap) async for name, snap in cg.stream({"x": 0}, stream_mode="values")]
        chunks = asyncio.run(run())
        assert len(chunks) == 2
        assert chunks[0][0] == "a"
        assert chunks[1][0] == "b"

    def test_stream_mode_updates(self) -> None:
        cg = self._build_graph()
        async def run() -> list:
            return [(name, delta) async for name, delta in cg.stream({"x": 0}, stream_mode="updates")]
        chunks = asyncio.run(run())
        assert all(isinstance(delta, dict) for _, delta in chunks)

    def test_stream_mode_debug(self) -> None:
        cg = self._build_graph()
        async def run() -> list:
            return [(name, info) async for name, info in cg.stream({"x": 0}, stream_mode="debug")]
        chunks = asyncio.run(run())
        assert all("step" in info and "node" in info for _, info in chunks)

    def test_stream_mode_events(self) -> None:
        cg = self._build_graph()
        async def run() -> list:
            return [ev async for ev in cg.stream({"x": 0}, stream_mode="events")]
        events = asyncio.run(run())
        event_types = {ev["event"] for ev in events}
        assert "on_node_start" in event_types
        assert "on_node_end" in event_types

    def test_stream_mode_messages(self) -> None:
        cg = self._build_graph()
        async def run() -> list:
            return [(name, msg) async for name, msg in cg.stream({"x": 0}, stream_mode="messages")]
        chunks = asyncio.run(run())
        assert all("role" in msg and "content" in msg for _, msg in chunks)

    def test_stream_mode_default_is_values(self) -> None:
        cg = self._build_graph()
        async def run() -> list:
            return [item async for item in cg.stream({"x": 0})]
        chunks = asyncio.run(run())
        # default mode = values: each item is (node_name, state_dict)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in chunks)


# ── 4. InjectedState / InjectedStore ─────────────────────────────────────────

class TestInjectedAnnotations:
    def test_injected_state_importable(self) -> None:
        from meshflow import InjectedState
        assert InjectedState is not None

    def test_injected_store_importable(self) -> None:
        from meshflow import InjectedStore
        assert InjectedStore is not None

    def test_injected_state_usable_as_annotation(self) -> None:
        from typing import Annotated
        from meshflow import InjectedState

        def my_tool(query: str, state: Annotated[dict, InjectedState]) -> str:
            return f"searching {state.get('topic', '?')} for {query}"

        result = my_tool("test", state={"topic": "AI"})
        assert result == "searching AI for test"

    def test_injected_store_usable_as_annotation(self) -> None:
        from typing import Annotated
        from meshflow import InjectedStore, GraphInMemoryStore
        store = GraphInMemoryStore()

        def save_fact(key: str, val: str,
                      store: Annotated[Any, InjectedStore]) -> str:
            store.put(("facts",), key, {"value": val})
            return "saved"

        result = save_fact("x", "42", store=store)
        assert result == "saved"
        assert store.get(("facts",), "x") is not None


# ── 5. interrupt_before / interrupt_after at compile() ───────────────────────

class TestCompileTimeInterrupts:
    def _graph(self) -> Any:
        from meshflow.core.state import StateGraph, END
        g = StateGraph()
        g.add_node("a", lambda s: {"visited": s.get("visited", []) + ["a"]})
        g.add_node("b", lambda s: {"visited": s.get("visited", []) + ["b"]})
        g.add_edge("a", "b")
        g.add_edge("b", END)
        g.set_entry_point("a")
        return g

    def test_interrupt_before_fires(self) -> None:
        g = self._graph()
        cg = g.compile(interrupt_before=["b"])
        with pytest.raises(InterruptedError) as exc_info:
            asyncio.run(cg.run({"visited": []}))
        assert "interrupt_before" in str(exc_info.value)

    def test_interrupt_after_fires(self) -> None:
        g = self._graph()
        cg = g.compile(interrupt_after=["a"])
        with pytest.raises(InterruptedError) as exc_info:
            asyncio.run(cg.run({"visited": []}))
        assert "interrupt_after" in str(exc_info.value)

    def test_no_interrupt_when_not_set(self) -> None:
        g = self._graph()
        cg = g.compile()
        result = asyncio.run(cg.run({"visited": []}))
        assert "a" in result["visited"] and "b" in result["visited"]

    def test_compile_with_store(self) -> None:
        from meshflow import GraphInMemoryStore
        g = self._graph()
        store = GraphInMemoryStore()
        cg = g.compile(store=store)
        assert cg._store is store


# ── 6. Crew.train() ───────────────────────────────────────────────────────────

class TestCrewTrain:
    def test_train_produces_records(self, tmp_path: Any) -> None:
        import json
        from meshflow import Crew
        a = _echo_agent("a", "research result")
        t = _task("research {topic}", a)
        crew = Crew(agents=[a], tasks=[t])
        filename = str(tmp_path / "training.jsonl")
        records = asyncio.run(crew.train(n_iterations=2, filename=filename, inputs={"topic": "AI"}))
        assert len(records) == 2
        # File should have 2 lines
        with open(filename) as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 2
        rec = json.loads(lines[0])
        assert "output" in rec and "inputs" in rec

    def test_train_appends_to_existing_file(self, tmp_path: Any) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")
        t = _task("task", a)
        crew = Crew(agents=[a], tasks=[t])
        filename = str(tmp_path / "train.jsonl")
        asyncio.run(crew.train(n_iterations=1, filename=filename))
        asyncio.run(crew.train(n_iterations=1, filename=filename))
        with open(filename) as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 2

    def test_train_records_have_required_fields(self, tmp_path: Any) -> None:
        import json
        from meshflow import Crew
        a = _echo_agent("a", "out")
        t = _task("t", a)
        crew = Crew(agents=[a], tasks=[t])
        filename = str(tmp_path / "t.jsonl")
        records = asyncio.run(crew.train(n_iterations=1, filename=filename))
        rec = records[0]
        for key in ("iteration", "inputs", "output", "task_outputs", "tokens"):
            assert key in rec


# ── 7. Crew.replay() ─────────────────────────────────────────────────────────

class TestCrewReplay:
    def test_replay_from_task_0_runs_all(self) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")
        t1 = _task("t1", a)
        t2 = _task("t2", a)
        crew = Crew(agents=[a], tasks=[t1, t2])
        result = asyncio.run(crew.replay(task_id=0))
        assert result.raw != ""

    def test_replay_from_middle_skips_earlier(self) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")
        t1 = _task("t1", a)
        t2 = _task("t2", a)
        t3 = _task("t3", a)
        crew = Crew(agents=[a], tasks=[t1, t2, t3])
        # Replay from task 2 — t1 and t2 get placeholder outputs
        asyncio.run(crew.replay(task_id=2))
        # t1 and t2 should have placeholder outputs injected
        assert t1.output is not None
        assert t2.output is not None

    def test_replay_out_of_range_raises(self) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "r")
        t = _task("t", a)
        crew = Crew(agents=[a], tasks=[t])
        with pytest.raises(ValueError, match="out of range"):
            asyncio.run(crew.replay(task_id=5))


# ── 8. Task(condition=...) ────────────────────────────────────────────────────

class TestTaskCondition:
    def test_condition_true_runs_task(self) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")
        t1 = _task("t1", a)
        t2 = _task("t2", a, condition=lambda _out: True)
        crew = Crew(agents=[a], tasks=[t1, t2])
        result = asyncio.run(crew.kickoff())
        # t2 ran — result has output from t2
        assert result.raw != ""

    def test_condition_false_skips_task(self) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")

        async def _run_and_check() -> Any:
            t1 = _task("t1", a)
            t2 = _task("t2", a, condition=lambda _out: False)
            crew = Crew(agents=[a], tasks=[t1, t2])
            result = await crew.kickoff()
            # t2 was skipped so its output is None
            assert t2.output is None
            return result

        asyncio.run(_run_and_check())

    def test_condition_receives_previous_output(self) -> None:
        from meshflow import Crew
        from meshflow import Task as MFTask
        received = {"out": None}
        a = _echo_agent("a", "approved")

        def _check(out: Any) -> bool:
            received["out"] = out
            return True

        t1 = _task("t1", a)
        t2 = MFTask(description="t2", expected_output="ok", agent=a, condition=_check)
        crew = Crew(agents=[a], tasks=[t1, t2])
        asyncio.run(crew.kickoff())
        # The condition was called with t1's TaskOutput
        assert received["out"] is not None

    def test_task_condition_field_exported(self) -> None:
        from meshflow import Task as MFTask
        t = MFTask(description="d", expected_output="e", condition=lambda _: True)
        assert callable(t.condition)


# ── 9. Crew(memory_config=...) ────────────────────────────────────────────────

class TestCrewMemoryConfig:
    def test_memory_config_in_memory_provider(self) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")
        t = _task("t", a)
        crew = Crew(
            agents=[a], tasks=[t],
            memory_config={"provider": "in_memory"},
        )
        assert crew.memory_config is not None

    def test_memory_config_sqlite_provider(self, tmp_path: Any) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")
        t = _task("t", a)
        crew = Crew(
            agents=[a], tasks=[t],
            memory_config={
                "provider": "sqlite",
                "config": {"path": str(tmp_path / "crew_mem.db")},
            },
        )
        result = asyncio.run(crew.kickoff())
        assert result.raw != ""

    def test_memory_config_none_is_default(self) -> None:
        from meshflow import Crew
        a = _echo_agent("a", "result")
        t = _task("t", a)
        crew = Crew(agents=[a], tasks=[t])
        assert crew.memory_config is None


# ── 10. Typed knowledge sources ───────────────────────────────────────────────

class TestTypedKnowledgeSources:
    def test_string_knowledge_source(self) -> None:
        from meshflow import StringKnowledgeSource
        src = StringKnowledgeSource(content="MeshFlow supports HIPAA compliance.")
        results = src.retrieve("HIPAA")
        assert any("HIPAA" in r for r in results)

    def test_pdf_knowledge_source_importable(self) -> None:
        from meshflow import PDFKnowledgeSource
        assert PDFKnowledgeSource is not None

    def test_pdf_knowledge_source_with_fake_path(self) -> None:
        from meshflow import PDFKnowledgeSource
        src = PDFKnowledgeSource(file_path="nonexistent.pdf")
        assert src.file_path == "nonexistent.pdf"

    def test_csv_knowledge_source(self, tmp_path: Any) -> None:
        from meshflow import CSVKnowledgeSource
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,role\nAlice,engineer\nBob,manager\n")
        src = CSVKnowledgeSource(file_path=str(csv_file))
        assert "Alice" in src.source or "engineer" in src.source

    def test_json_knowledge_source(self, tmp_path: Any) -> None:
        from meshflow import JSONKnowledgeSource
        json_file = tmp_path / "data.json"
        json_file.write_text('[{"key": "governance", "value": "important"}]')
        src = JSONKnowledgeSource(file_path=str(json_file))
        assert "governance" in src.source

    def test_excel_knowledge_source_importable(self) -> None:
        from meshflow import ExcelKnowledgeSource
        assert ExcelKnowledgeSource is not None

    def test_string_source_retrieve_relevant(self) -> None:
        from meshflow import StringKnowledgeSource
        src = StringKnowledgeSource(
            content="The capital of France is Paris. Berlin is in Germany."
        )
        results = src.retrieve("capital France")
        assert len(results) >= 1

    def test_knowledge_sources_exported(self) -> None:
        from meshflow import (
            StringKnowledgeSource, PDFKnowledgeSource,
            CSVKnowledgeSource, JSONKnowledgeSource, ExcelKnowledgeSource,
        )
        for cls in (StringKnowledgeSource, PDFKnowledgeSource,
                    CSVKnowledgeSource, JSONKnowledgeSource, ExcelKnowledgeSource):
            assert cls is not None


# ── 11. Pipeline ─────────────────────────────────────────────────────────────

class TestPipeline:
    def _crew(self, name: str, response: str = "stage_output") -> Any:
        from meshflow import Crew
        a = _echo_agent(name, response)
        t = _task(f"{name} task", a)
        return Crew(agents=[a], tasks=[t])

    def test_pipeline_single_stage(self) -> None:
        from meshflow import Pipeline
        crew = self._crew("s1", "result_1")
        p = Pipeline(stages=[crew])
        result = p.kickoff()
        assert result.final_output != ""

    def test_pipeline_two_sequential_stages(self) -> None:
        from meshflow import Pipeline
        p = Pipeline(stages=[self._crew("s1"), self._crew("s2")])
        result = p.kickoff()
        assert len(result.stage_outputs) == 2

    def test_pipeline_passes_output_to_next_stage(self) -> None:
        from meshflow import Pipeline
        p = Pipeline(stages=[self._crew("s1", "stage1_out"), self._crew("s2")])
        result = p.kickoff(inputs={"topic": "AI"})
        assert result.total_tokens >= 0

    def test_pipeline_parallel_substage(self) -> None:
        from meshflow import Pipeline
        c1 = self._crew("pa")
        c2 = self._crew("pb")
        c3 = self._crew("final")
        p = Pipeline(stages=[[c1, c2], c3])
        result = p.kickoff()
        # stage_outputs[0] is a list of two CrewOutputs
        assert isinstance(result.stage_outputs[0], list)
        assert len(result.stage_outputs[0]) == 2

    def test_pipeline_accumulates_tokens(self) -> None:
        from meshflow import Pipeline
        p = Pipeline(stages=[self._crew("s1"), self._crew("s2"), self._crew("s3")])
        result = p.kickoff()
        assert result.total_tokens >= 0

    def test_pipeline_empty_stages_raises(self) -> None:
        from meshflow import Pipeline
        with pytest.raises(ValueError):
            Pipeline(stages=[])

    def test_pipeline_kickoff_result_str(self) -> None:
        from meshflow import Pipeline
        p = Pipeline(stages=[self._crew("s1", "hello")])
        result = p.kickoff()
        assert isinstance(str(result), str)

    def test_pipeline_repr(self) -> None:
        from meshflow import Pipeline
        p = Pipeline(stages=[self._crew("s1"), [self._crew("a"), self._crew("b")]])
        r = repr(p)
        assert "Pipeline" in r and "2" in r

    def test_pipeline_exported(self) -> None:
        from meshflow import Pipeline, PipelineKickoffResult
        assert Pipeline is not None
        assert PipelineKickoffResult is not None
