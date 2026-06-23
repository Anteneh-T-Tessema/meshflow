"""Ledger backend tests — SQLite default and PostgreSQL backend contract."""

from __future__ import annotations

import asyncio

import pytest

from meshflow.core.ledger import (
    PostgresLedgerBackend,
    ReplayLedger,
    S3LedgerArchiveBackend,
    SQLiteLedgerBackend,
)
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
        if "schema_migrations" in sql:
            return []  # no migrations applied yet
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


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: list[dict] = []

    def put_object(self, **kwargs: object) -> dict:
        self.objects.append(kwargs)
        return {"ETag": '"fake"'}


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
    conn.step_rows = [
        {
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
        }
    ]
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


def test_s3_archive_backend_writes_immutable_json_export():
    client = _FakeS3Client()
    backend = S3LedgerArchiveBackend("s3://meshflow-archive/runs", client=client)

    result = asyncio.run(backend.archive_json("run-1", '{"run_id": "run-1"}'))

    assert result.uri == "s3://meshflow-archive/runs/run-1.json"
    assert result.bytes_written == len(b'{"run_id": "run-1"}')
    assert result.sha256
    assert client.objects[0]["Bucket"] == "meshflow-archive"
    assert client.objects[0]["Key"] == "runs/run-1.json"
    assert client.objects[0]["ContentType"] == "application/json"
    assert client.objects[0]["IfNoneMatch"] == "*"
    assert client.objects[0]["Metadata"]["meshflow-run-id"] == "run-1"


def test_replay_ledger_archives_run_to_s3_backend():
    ledger = ReplayLedger(":memory:")
    client = _FakeS3Client()
    backend = S3LedgerArchiveBackend("s3://meshflow-archive/history", client=client)
    asyncio.run(ledger.write(_record(run_id="run-archive", step_id="step-archive")))

    result = asyncio.run(
        ledger.archive_run("run-archive", "s3://meshflow-archive/history", backend=backend)
    )

    assert result.key == "history/run-archive.json"
    body = client.objects[0]["Body"].decode("utf-8")
    assert '"run_id": "run-archive"' in body
    assert '"node_id": "node-a"' in body

def test_replay_ledger_archive_rejects_unknown_run():
    ledger = ReplayLedger(":memory:")
    backend = S3LedgerArchiveBackend("s3://meshflow-archive/history", client=_FakeS3Client())

    with pytest.raises(ValueError, match="unknown run_id"):
        asyncio.run(ledger.archive_run("missing", "s3://meshflow-archive/history", backend=backend))



def test_async_ledger_writer_batching():
    # Force batching enabled for test
    ledger = ReplayLedger(":memory:", enable_batching=True)
    try:
        # Create records to write
        r1 = _record(run_id="batch-run", step_id="s1")
        r2 = _record(run_id="batch-run", step_id="s2")

        # Write them
        asyncio.run(ledger.write(r1))
        asyncio.run(ledger.write(r2))

        # Check that they are not immediately in backend, or they are flushed by ensure_flushed
        # ReplayLedger.get_run calls ensure_flushed internally, which triggers flush/drain
        steps = asyncio.run(ledger.get_run("batch-run"))
        assert len(steps) == 2
        assert steps[0]["step_id"] == "s1"
        assert steps[1]["step_id"] == "s2"

        # Merkle tree and master root should be in metadata
        assert "merkle_proof" in steps[0]["metadata"]
        assert "merkle_proof" in steps[1]["metadata"]
        assert "master_root" in steps[0]["metadata"]["merkle_proof"]
        assert steps[0]["prev_hash"] == ""
        assert steps[1]["prev_hash"] == steps[0]["entry_hash"]
    finally:
        asyncio.run(ledger.aclose())

