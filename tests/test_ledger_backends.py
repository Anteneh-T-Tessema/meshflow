"""Ledger backend tests — SQLite default and PostgreSQL backend contract."""
from __future__ import annotations

import asyncio

from meshflow.core.ledger import PostgresLedgerBackend, ReplayLedger, SQLiteLedgerBackend
from meshflow.core.runtime import StepRecord


def _record(run_id: str = "run-pg", step_id: str = "step-1") -> StepRecord:
    return StepRecord(
        run_id=run_id,
        step_id=step_id,
        node_id="node-a",
        node_kind="python",
        input_task="task",
        output_content="output",
        verdict="commit",
        blocked=False,
        block_reason="",
        uncertainty=0.0,
        cost_usd=0.01,
        tokens_used=12,
        carbon_gco2=0.001,
        duration_ms=3.0,
        timestamp="2026-05-21T00:00:00+00:00",
        metadata={"source": "test"},
    )


class _Acquire:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_FakeConn":
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.step_rows: list[dict] = []
        self.run_rows: list[dict] = []
        self.checkpoint_row: dict | None = None
        self.paused_rows: list[dict] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        if "GROUP BY run_id" in sql:
            return self.run_rows
        if "workflow_checkpoints" in sql:
            return self.paused_rows
        return self.step_rows

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        return self.checkpoint_row


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self.closed = False

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)

    async def close(self) -> None:
        self.closed = True


def test_replay_ledger_defaults_to_sqlite_backend():
    ledger = ReplayLedger(":memory:")

    assert isinstance(ledger._backend, SQLiteLedgerBackend)


def test_replay_ledger_selects_postgres_backend_for_dsn():
    ledger = ReplayLedger("postgresql://meshflow:secret@localhost/meshflow")

    assert isinstance(ledger._backend, PostgresLedgerBackend)
    assert ledger._db_path == "postgresql://meshflow:secret@localhost/meshflow"


def test_custom_backend_can_be_injected():
    conn = _FakeConn()
    backend = PostgresLedgerBackend("postgresql://example/db", pool=_FakePool(conn))
    ledger = ReplayLedger(backend=backend)

    assert ledger._backend is backend


def test_postgres_backend_writes_and_queries_with_same_contract():
    conn = _FakeConn()
    conn.step_rows = [{
        "id": 1,
        "run_id": "run-pg",
        "step_id": "step-1",
        "node_id": "node-a",
        "node_kind": "python",
        "input_task": "task",
        "output_content": "output",
        "verdict": "commit",
        "blocked": False,
        "block_reason": "",
        "uncertainty": 0.0,
        "cost_usd": 0.01,
        "tokens_used": 12,
        "carbon_gco2": 0.001,
        "duration_ms": 3.0,
        "timestamp": "2026-05-21T00:00:00+00:00",
        "metadata": '{"source": "test"}',
    }]
    conn.run_rows = [{"run_id": "run-pg"}]
    backend = PostgresLedgerBackend("postgresql://example/db", pool=_FakePool(conn))

    asyncio.run(backend.write(_record()))
    steps = asyncio.run(backend.get_run("run-pg"))
    runs = asyncio.run(backend.list_runs())

    assert any("ON CONFLICT (step_id)" in sql for sql, _ in conn.executed)
    assert steps[0]["node_id"] == "node-a"
    assert steps[0]["metadata"] == {"source": "test"}
    assert runs == ["run-pg"]


def test_postgres_backend_checkpoint_api_matches_sqlite_contract():
    conn = _FakeConn()
    conn.checkpoint_row = {"data": '{"context": {"approved": true}}'}
    conn.paused_rows = [{"run_id": "run-1", "created_at": "now"}]
    backend = PostgresLedgerBackend("postgresql://example/db", pool=_FakePool(conn))

    asyncio.run(backend.save_checkpoint("run-1", {"context": {"approved": True}}))
    loaded = asyncio.run(backend.load_checkpoint_data("run-1"))
    paused = asyncio.run(backend.list_paused_runs())
    asyncio.run(backend.delete_checkpoint("run-1"))

    assert loaded == {"context": {"approved": True}}
    assert paused == [{"run_id": "run-1", "paused_at": "now"}]
    assert any("workflow_checkpoints" in sql for sql, _ in conn.executed)


def test_postgres_backend_close_closes_pool():
    pool = _FakePool(_FakeConn())
    backend = PostgresLedgerBackend("postgresql://example/db", pool=pool)

    asyncio.run(backend.close())

    assert pool.closed is True
