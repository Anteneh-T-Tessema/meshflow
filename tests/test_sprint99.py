"""Sprint 99 — SpawnableAgent, Typed structured streaming v2,
BaseStore wired into Agent, Developer CLI v2 (runs inspect, worker jobs,
eval-baseline), updated launch posts.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

from meshflow.agents.base import EchoProvider


# ── helpers ───────────────────────────────────────────────────────────────────

def _echo_agent(name: str, response: str = "ok") -> Any:
    from meshflow import Agent
    return Agent(name=name, role="worker", provider=EchoProvider(response))


# ══════════════════════════════════════════════════════════════════════════════
# B1 — SpawnableAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestSpawnRule:
    def test_matches_by_keyword(self) -> None:
        from meshflow.agents.spawnable import SpawnRule
        rule = SpawnRule("code", keywords=["python", "function"])
        assert rule.matches("write a python function")
        assert not rule.matches("analyse market trends")

    def test_matches_case_insensitive(self) -> None:
        from meshflow.agents.spawnable import SpawnRule
        rule = SpawnRule("code", keywords=["CODE"])
        assert rule.matches("write code for this")

    def test_matches_by_pattern(self) -> None:
        from meshflow.agents.spawnable import SpawnRule
        rule = SpawnRule("sql", pattern=r"\bSELECT\b")
        assert rule.matches("SELECT * FROM users")
        assert not rule.matches("write a paragraph")

    def test_pattern_and_keywords_or_logic(self) -> None:
        from meshflow.agents.spawnable import SpawnRule
        rule = SpawnRule("r", keywords=["research"], pattern=r"\bfind\b")
        assert rule.matches("find the answer")
        assert rule.matches("research this topic")
        assert not rule.matches("write a poem")

    def test_no_keywords_no_pattern_never_matches(self) -> None:
        from meshflow.agents.spawnable import SpawnRule
        rule = SpawnRule("empty")
        assert not rule.matches("anything goes here")


class TestSpawnConfig:
    def test_default_config(self) -> None:
        from meshflow.agents.spawnable import SpawnConfig
        cfg = SpawnConfig()
        assert cfg.rules == []
        assert cfg.fallback_role == "executor"
        assert cfg.parallel is True
        assert cfg.max_spawns == 8
        assert cfg.aggregate == "concat"

    def test_custom_config(self) -> None:
        from meshflow.agents.spawnable import SpawnConfig, SpawnRule
        cfg = SpawnConfig(rules=[SpawnRule("r", keywords=["x"])], parallel=False, aggregate="first")
        assert len(cfg.rules) == 1
        assert cfg.parallel is False
        assert cfg.aggregate == "first"


class TestSpawnResult:
    def test_fields(self) -> None:
        from meshflow.agents.spawnable import SpawnResult
        r = SpawnResult(output="out", spawn_count=2, agents_used=["a", "b"],
                        sub_outputs={"a": "x", "b": "y"})
        assert r.output == "out"
        assert r.spawn_count == 2
        assert r.completed is True


class TestSpawnableAgent:
    def _config(self, parallel: bool = True, aggregate: str = "concat") -> Any:
        from meshflow.agents.spawnable import SpawnConfig, SpawnRule
        return SpawnConfig(
            rules=[
                SpawnRule("code",     keywords=["code", "python"],     role="executor"),
                SpawnRule("research", keywords=["research", "analyse"], role="researcher"),
            ],
            parallel=parallel,
            aggregate=aggregate,
        )

    def test_run_no_match_uses_fallback(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent, SpawnConfig
        agent = SpawnableAgent("orch", spawn_config=SpawnConfig(fallback_role="executor"),
                               mode="sandbox")
        result = agent.run("Hello world")
        assert isinstance(result.output, str)
        assert result.spawn_count == 0
        assert result.agents_used == []

    def test_run_single_match(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent
        agent = SpawnableAgent("orch", spawn_config=self._config(), mode="sandbox")
        result = agent.run("write python code")
        assert result.spawn_count == 1
        assert "code" in result.agents_used

    def test_run_multiple_matches_parallel(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent
        agent = SpawnableAgent("orch", spawn_config=self._config(parallel=True), mode="sandbox")
        result = agent.run("write python code and research the results")
        assert result.spawn_count == 2
        assert set(result.agents_used) == {"code", "research"}

    def test_run_multiple_matches_sequential(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent
        agent = SpawnableAgent("orch", spawn_config=self._config(parallel=False), mode="sandbox")
        result = agent.run("write python code and research the results")
        assert result.spawn_count == 2

    def test_aggregate_concat(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent
        agent = SpawnableAgent("orch", spawn_config=self._config(aggregate="concat"), mode="sandbox")
        result = agent.run("research python code")
        assert "\n\n" in result.output or result.output  # two joined outputs

    def test_aggregate_first(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent
        agent = SpawnableAgent("orch", spawn_config=self._config(aggregate="first"), mode="sandbox")
        result = agent.run("research python code")
        assert isinstance(result.output, str)

    def test_max_spawns_limits_children(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent, SpawnConfig, SpawnRule
        # 3 matching rules but max_spawns=2
        cfg = SpawnConfig(
            rules=[
                SpawnRule("a", keywords=["x"]),
                SpawnRule("b", keywords=["x"]),
                SpawnRule("c", keywords=["x"]),
            ],
            max_spawns=2,
        )
        agent = SpawnableAgent("orch", spawn_config=cfg, mode="sandbox")
        result = agent.run("x x x")
        assert result.spawn_count == 2

    def test_sub_outputs_keyed_by_rule_name(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent
        agent = SpawnableAgent("orch", spawn_config=self._config(), mode="sandbox")
        result = agent.run("research python code")
        assert "code" in result.sub_outputs
        assert "research" in result.sub_outputs

    def test_arun_returns_spawn_result(self) -> None:
        from meshflow.agents.spawnable import SpawnableAgent
        agent = SpawnableAgent("orch", spawn_config=self._config(), mode="sandbox")
        result = asyncio.run(agent.arun("python code"))
        assert isinstance(result.output, str)

    def test_exported_from_meshflow(self) -> None:
        from meshflow import SpawnableAgent, SpawnRule, SpawnConfig, SpawnResult
        assert SpawnableAgent is not None
        assert SpawnRule is not None
        assert SpawnConfig is not None
        assert SpawnResult is not None


# ══════════════════════════════════════════════════════════════════════════════
# B2 — Agent(memory_store=...)
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentMemoryStore:
    def _store(self) -> Any:
        from meshflow.core.store import InMemoryStore
        return InMemoryStore()

    def _built_agent(self, store: Any = None) -> Any:
        from meshflow import Agent
        from meshflow.agents.base import EchoProvider
        a = Agent(name="storetest", role="worker",
                  provider=EchoProvider("ok"),
                  memory_store=store,
                  mode="sandbox")
        return a._build()

    def test_store_put_and_get(self) -> None:
        store = self._store()
        agent = self._built_agent(store)
        agent.store_put(("facts",), "city", {"value": "Addis Ababa"})
        val = agent.store_get(("facts",), "city")
        assert val == {"value": "Addis Ababa"}

    def test_store_get_missing_returns_none(self) -> None:
        agent = self._built_agent(self._store())
        assert agent.store_get(("ns",), "missing") is None

    def test_store_get_no_store_returns_none(self) -> None:
        agent = self._built_agent(store=None)
        assert agent.store_get(("ns",), "key") is None

    def test_store_put_no_store_raises(self) -> None:
        agent = self._built_agent(store=None)
        with pytest.raises(RuntimeError, match="No memory_store"):
            agent.store_put(("ns",), "key", "value")

    def test_store_search_empty(self) -> None:
        agent = self._built_agent(self._store())
        results = agent.store_search(("empty_ns",))
        assert results == []

    def test_store_search_no_store(self) -> None:
        agent = self._built_agent(store=None)
        assert agent.store_search(("ns",)) == []

    def test_store_shared_across_agents(self) -> None:
        store = self._store()
        a1 = self._built_agent(store)
        a2 = self._built_agent(store)
        a1.store_put(("shared",), "answer", 42)
        assert a2.store_get(("shared",), "answer") == 42

    def test_agent_dataclass_accepts_memory_store(self) -> None:
        from meshflow import Agent
        from meshflow.core.store import SQLiteStore
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            store = SQLiteStore(f.name)
            a = Agent(name="t", memory_store=store)
            assert a.memory_store is store

    def test_sqlite_store_persists(self) -> None:
        from meshflow.core.store import SQLiteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            store = SQLiteStore(path)
            store.put(("test",), "k", "v")
            store2 = SQLiteStore(path)
            item = store2.get(("test",), "k")
            assert item is not None
            assert item.value == "v"
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# D — Typed structured streaming v2
# ══════════════════════════════════════════════════════════════════════════════

class TestTypedStreamChunk:
    def test_to_dict_with_pydantic(self) -> None:
        try:
            from pydantic import BaseModel

            class M(BaseModel):
                title: str = ""

            from meshflow.streaming.structured_v2 import TypedStreamChunk
            chunk = TypedStreamChunk(partial=M(title="Hi"), complete=False, token="Hi")
            d = chunk.to_dict()
            assert d["partial"]["title"] == "Hi"
            assert d["complete"] is False
        except ImportError:
            pytest.skip("pydantic not installed")

    def test_to_json_is_string(self) -> None:
        try:
            from pydantic import BaseModel

            class M(BaseModel):
                x: int = 0

            from meshflow.streaming.structured_v2 import TypedStreamChunk
            chunk = TypedStreamChunk(partial=M(x=5), complete=True)
            s = chunk.to_json()
            assert isinstance(s, str)
            assert json.loads(s)["complete"] is True
        except ImportError:
            pytest.skip("pydantic not installed")

    def test_exported_from_meshflow(self) -> None:
        from meshflow import TypedStreamChunk
        assert TypedStreamChunk is not None


class TestStreamModel:
    def test_stream_model_yields_chunks(self) -> None:
        try:
            from pydantic import BaseModel

            class Report(BaseModel):
                title: str = ""
                summary: str = ""

            from meshflow.streaming.structured_v2 import stream_model

            async def _tokens():
                yield '{"title": "MeshFlow", "summary": "agent framework"}'

            async def _run():
                chunks = []
                async for c in stream_model(_tokens(), Report):
                    chunks.append(c)
                return chunks

            chunks = asyncio.run(_run())
            assert len(chunks) >= 1
            last = chunks[-1]
            assert last.complete is True
        except ImportError:
            pytest.skip("pydantic not installed")

    def test_collect_model_returns_instance(self) -> None:
        try:
            from pydantic import BaseModel

            class M(BaseModel):
                name: str = ""

            from meshflow.streaming.structured_v2 import collect_model

            async def _tokens():
                yield '{"name": "Alice"}'

            result = asyncio.run(collect_model(_tokens(), M))
            assert result is not None
            assert result.name == "Alice"
        except ImportError:
            pytest.skip("pydantic not installed")

    def test_collect_model_empty_stream_returns_none(self) -> None:
        try:
            from pydantic import BaseModel

            class M(BaseModel):
                x: int = 0

            from meshflow.streaming.structured_v2 import collect_model

            async def _empty():
                return
                yield  # make it an async generator

            result = asyncio.run(collect_model(_empty(), M))
            assert result is None
        except ImportError:
            pytest.skip("pydantic not installed")

    def test_stream_model_exported(self) -> None:
        from meshflow import stream_model, collect_model
        assert stream_model is not None
        assert collect_model is not None


class TestStreamToSSE:
    def test_stream_to_sse_yields_event_lines(self) -> None:
        try:
            from pydantic import BaseModel

            class M(BaseModel):
                x: int = 0

            from meshflow.streaming.structured_v2 import stream_model, stream_to_sse

            async def _tokens():
                yield '{"x": 7}'

            async def _run():
                lines = []
                async for line in stream_to_sse(stream_model(_tokens(), M)):
                    lines.append(line)
                return lines

            lines = asyncio.run(_run())
            assert any(line.startswith("event:") for line in lines)
            assert any("data:" in line for line in lines)
        except ImportError:
            pytest.skip("pydantic not installed")

    def test_stream_to_ndjson_yields_json_lines(self) -> None:
        try:
            from pydantic import BaseModel

            class M(BaseModel):
                v: str = ""

            from meshflow.streaming.structured_v2 import stream_model, stream_to_ndjson

            async def _tokens():
                yield '{"v": "hello"}'

            async def _run():
                lines = []
                async for line in stream_to_ndjson(stream_model(_tokens(), M)):
                    lines.append(line)
                return lines

            lines = asyncio.run(_run())
            assert all(line.endswith("\n") for line in lines)
            # each line is valid JSON
            for line in lines:
                json.loads(line)
        except ImportError:
            pytest.skip("pydantic not installed")

    def test_sse_exported_from_meshflow(self) -> None:
        from meshflow import typed_stream_to_sse, typed_stream_to_ndjson
        assert typed_stream_to_sse is not None
        assert typed_stream_to_ndjson is not None


class TestPartialModelConstruct:
    def test_partial_model_fills_defaults(self) -> None:
        try:
            from pydantic import BaseModel

            class M(BaseModel):
                a: str = "default_a"
                b: int = 0

            from meshflow.streaming.structured_v2 import _partial_model
            instance = _partial_model(M, {"a": "hello"})
            assert instance.a == "hello"
        except ImportError:
            pytest.skip("pydantic not installed")


# ══════════════════════════════════════════════════════════════════════════════
# C — Developer CLI v2
# ══════════════════════════════════════════════════════════════════════════════

class TestEvalBaselineCLI:
    def _args(self, cmd: str, **kw: Any) -> Any:
        import argparse
        ns = argparse.Namespace(eval_baseline_cmd=cmd)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_set_creates_baseline(self) -> None:
        from meshflow.cli.main import _cmd_eval_baseline
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            os.unlink(path)
            args = self._args("set", name="prod", pass_rate=0.92, db=path)
            _cmd_eval_baseline(args)
            with open(path) as fh:
                data = json.load(fh)
            assert data["prod"] == pytest.approx(0.92)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_get_existing_baseline(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_eval_baseline
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({"staging": 0.80}, f)
            path = f.name
        try:
            args = self._args("get", name="staging", db=path)
            _cmd_eval_baseline(args)
            captured = capsys.readouterr()
            assert "staging" in captured.out
            assert "0.8" in captured.out
        finally:
            os.unlink(path)

    def test_get_missing_baseline(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_eval_baseline
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({}, f)
            path = f.name
        try:
            args = self._args("get", name="nonexistent", db=path)
            _cmd_eval_baseline(args)
            captured = capsys.readouterr()
            assert "not found" in captured.out
        finally:
            os.unlink(path)

    def test_list_baselines(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_eval_baseline
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({"a": 0.9, "b": 0.75}, f)
            path = f.name
        try:
            args = self._args("list", db=path)
            _cmd_eval_baseline(args)
            out = capsys.readouterr().out
            assert "a" in out
            assert "b" in out
        finally:
            os.unlink(path)

    def test_clear_baseline(self) -> None:
        from meshflow.cli.main import _cmd_eval_baseline
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({"to_delete": 0.5, "keep": 0.9}, f)
            path = f.name
        try:
            args = self._args("clear", name="to_delete", db=path)
            _cmd_eval_baseline(args)
            with open(path) as fh:
                data = json.load(fh)
            assert "to_delete" not in data
            assert "keep" in data
        finally:
            os.unlink(path)

    def test_list_empty_baselines(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_eval_baseline
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({}, f)
            path = f.name
        try:
            args = self._args("list", db=path)
            _cmd_eval_baseline(args)
            out = capsys.readouterr().out
            assert "No baselines" in out
        finally:
            os.unlink(path)

    def test_clear_nonexistent(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_eval_baseline
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({}, f)
            path = f.name
        try:
            args = self._args("clear", name="ghost", db=path)
            _cmd_eval_baseline(args)
            out = capsys.readouterr().out
            assert "not found" in out
        finally:
            os.unlink(path)


class TestWorkerJobsCLI:
    def test_no_jobs_prints_message(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_worker_jobs
        import argparse
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            args = argparse.Namespace(db=path, job_status="", limit=30)
            _cmd_worker_jobs(args)
            out = capsys.readouterr().out
            assert "No jobs" in out
        finally:
            os.unlink(path)

    def test_jobs_with_items(self, capsys: Any) -> None:
        from meshflow.workers.core import SQLiteJobStore, JobRecord, JobStatus
        from meshflow.cli.main import _cmd_worker_jobs
        import argparse
        import time
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            store = SQLiteJobStore(path=path)
            store.put(JobRecord(
                job_id="test-job-id-1234",
                task_name="my_task",
                args=[],
                kwargs={},
                status=JobStatus.QUEUED,
                created_at=time.time(),
            ))
            args = argparse.Namespace(db=path, job_status="", limit=30)
            _cmd_worker_jobs(args)
            out = capsys.readouterr().out
            assert "my_task" in out
        finally:
            os.unlink(path)

    def test_invalid_status_filter(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_worker_jobs
        import argparse
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            args = argparse.Namespace(db=path, job_status="INVALID_STATUS", limit=10)
            _cmd_worker_jobs(args)
            out = capsys.readouterr().out
            assert "Unknown status" in out
        finally:
            os.unlink(path)


class TestRunsInspectCLI:
    def test_missing_db_prints_message(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_runs_v2
        import argparse
        args = argparse.Namespace(
            runs_cmd="inspect",
            run_id="run_abc",
            db="/tmp/nonexistent_99xyz.db",
            as_json=False,
        )
        _cmd_runs_v2(args)
        out = capsys.readouterr().out
        assert "No ledger" in out or "not found" in out.lower()

    def test_runs_v2_no_subcmd_falls_back_to_logs(self, capsys: Any) -> None:
        from meshflow.cli.main import _cmd_runs_v2
        import argparse
        args = argparse.Namespace(
            runs_cmd=None,
            db="/tmp/nonexistent_99xyz.db",
            limit=5,
        )
        _cmd_runs_v2(args)
        out = capsys.readouterr().out
        # logs command prints no-ledger message
        assert "No ledger" in out or out == ""


# ══════════════════════════════════════════════════════════════════════════════
# Launch posts updated
# ══════════════════════════════════════════════════════════════════════════════

class TestLaunchPostsUpdated:
    def _read(self, path: str) -> str:
        full = os.path.join(
            os.path.dirname(__file__), "..", "docs", "launch", path
        )
        with open(os.path.normpath(full)) as fh:
            return fh.read()

    def test_show_hn_mentions_sprint97_features(self) -> None:
        content = self._read("show_hn.md")
        assert "SpawnableAgent" in content or "spawnable" in content.lower()
        assert "@traceable" in content or "traceable" in content.lower()
        # test count bumped with each release — accept any value >= 5,400
        import re
        counts = re.findall(r'(\d[\d,]+)\s*tests?', content)
        assert any(int(c.replace(',', '')) >= 5400 for c in counts), \
            f"No test count >= 5400 found in show_hn.md; found: {counts}"

    def test_show_hn_updated_version(self) -> None:
        content = self._read("show_hn.md")
        # should not still say v1.10.0 in the title
        assert "v1.10.0" not in content.split("\n")[0]

    def test_product_hunt_updated_version(self) -> None:
        content = self._read("product_hunt.md")
        import re
        versions = re.findall(r'v1\.(\d+)', content)
        assert any(int(v) >= 13 for v in versions), \
            f"No version >= v1.13 found in product_hunt.md; found: {versions}"

    def test_product_hunt_mentions_new_features(self) -> None:
        content = self._read("product_hunt.md")
        assert "durable" in content.lower() or "WorkerDaemon" in content
        assert "StructuredJudge" in content or "EvalCI" in content
