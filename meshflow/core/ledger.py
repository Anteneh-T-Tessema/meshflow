"""ReplayLedger — append-only, replayable run ledger.

Every governed step writes a StepRecord here. The ledger answers:
  - Why did run X fail?
  - Which node made this decision?
  - What evidence was in scope at step N?
  - Can I replay from checkpoint 4?
  - Was this action policy-approved?
  - What was the total carbon cost?

The ledger is append-only by design. Records are never modified after write.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any, Protocol

from meshflow.core.runtime import StepRecord

_CREATE_SQLITE_STEPS_SQL = """
CREATE TABLE IF NOT EXISTS step_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    step_id         TEXT    NOT NULL UNIQUE,
    node_id         TEXT    NOT NULL,
    node_kind       TEXT    NOT NULL,
    input_task      TEXT,
    output_content  TEXT,
    verdict         TEXT,
    blocked         INTEGER NOT NULL DEFAULT 0,
    block_reason    TEXT    DEFAULT '',
    uncertainty     REAL    DEFAULT 0.0,
    cost_usd        REAL    DEFAULT 0.0,
    tokens_used     INTEGER DEFAULT 0,
    carbon_gco2     REAL    DEFAULT 0.0,
    duration_ms     REAL    DEFAULT 0.0,
    timestamp       TEXT,
    metadata        TEXT    DEFAULT '{}'
)
"""

_CREATE_SQLITE_CHECKPOINTS_SQL = """
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    run_id      TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

_CREATE_POSTGRES_STEPS_SQL = """
CREATE TABLE IF NOT EXISTS step_records (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT    NOT NULL,
    step_id         TEXT    NOT NULL UNIQUE,
    node_id         TEXT    NOT NULL,
    node_kind       TEXT    NOT NULL,
    input_task      TEXT,
    output_content  TEXT,
    verdict         TEXT,
    blocked         BOOLEAN NOT NULL DEFAULT FALSE,
    block_reason    TEXT    DEFAULT '',
    uncertainty     DOUBLE PRECISION DEFAULT 0.0,
    cost_usd        DOUBLE PRECISION DEFAULT 0.0,
    tokens_used     INTEGER DEFAULT 0,
    carbon_gco2     DOUBLE PRECISION DEFAULT 0.0,
    duration_ms     DOUBLE PRECISION DEFAULT 0.0,
    timestamp       TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb
)
"""

_CREATE_POSTGRES_CHECKPOINTS_SQL = """
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    run_id      TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    created_at  TEXT NOT NULL
)
"""

_CREATE_INDEX_RUN   = "CREATE INDEX IF NOT EXISTS idx_run_id    ON step_records(run_id)"
_CREATE_INDEX_NODE  = "CREATE INDEX IF NOT EXISTS idx_node_id   ON step_records(node_id)"
_CREATE_INDEX_TS    = "CREATE INDEX IF NOT EXISTS idx_timestamp ON step_records(timestamp)"


class LedgerBackend(Protocol):
    """Storage contract used by ReplayLedger.

    Backends are async at the public boundary so local SQLite and networked
    PostgreSQL can share one API.
    """

    db_path: str

    async def write(self, record: StepRecord) -> None: ...
    async def get_run(self, run_id: str) -> list[dict[str, Any]]: ...
    async def list_runs(self) -> list[str]: ...
    async def save_checkpoint(self, run_id: str, data: dict[str, Any]) -> None: ...
    async def load_checkpoint_data(self, run_id: str) -> dict[str, Any] | None: ...
    async def delete_checkpoint(self, run_id: str) -> None: ...
    async def list_paused_runs(self) -> list[dict[str, Any]]: ...


class SQLiteLedgerBackend:
    """Append-only, replayable run ledger backed by synchronous SQLite.

    Uses a single persistent connection so in-memory databases work
    across multiple calls within the same process.
    """

    def __init__(self, db_path: str = "meshflow_runs.db") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_SQLITE_STEPS_SQL)
            self._conn.execute(_CREATE_SQLITE_CHECKPOINTS_SQL)
            self._conn.execute(_CREATE_INDEX_RUN)
            self._conn.execute(_CREATE_INDEX_NODE)
            self._conn.execute(_CREATE_INDEX_TS)

    def close(self) -> None:
        self._conn.close()

    async def write(self, record: StepRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO step_records
                  (run_id, step_id, node_id, node_kind, input_task, output_content,
                   verdict, blocked, block_reason, uncertainty, cost_usd, tokens_used,
                   carbon_gco2, duration_ms, timestamp, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                _record_values(record, sqlite=True),
            )

    async def get_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM step_records WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    async def list_runs(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT run_id FROM step_records GROUP BY run_id ORDER BY MAX(id) DESC"
        ).fetchall()
        return [r[0] for r in rows]

    async def save_checkpoint(self, run_id: str, data: dict[str, Any]) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO workflow_checkpoints (run_id, data, created_at)
                VALUES (?, ?, ?)
                """,
                (run_id, json.dumps(data), now),
            )

    async def load_checkpoint_data(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT data FROM workflow_checkpoints WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    async def delete_checkpoint(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM workflow_checkpoints WHERE run_id=?", (run_id,)
            )

    async def list_paused_runs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT run_id, created_at FROM workflow_checkpoints ORDER BY created_at"
        ).fetchall()
        return [{"run_id": r[0], "paused_at": r[1]} for r in rows]


class PostgresLedgerBackend:
    """Append-only, replayable run ledger backed by PostgreSQL.

    The connection pool is created lazily on first use so constructing a
    ReplayLedger from a DSN does not perform network I/O. This keeps CLI and
    tests predictable while still supporting durable multi-process storage.
    """

    def __init__(self, dsn: str, pool: Any = None) -> None:
        self.db_path = dsn
        self._dsn = dsn
        self._pool = pool
        self._initialized = False

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL ledger requires asyncpg. Install meshflow with asyncpg "
                    "or use a SQLite ledger path."
                ) from exc
            self._pool = await asyncpg.create_pool(self._dsn)
        if not self._initialized:
            await self._init_db()
            self._initialized = True
        return self._pool

    async def _init_db(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_POSTGRES_STEPS_SQL)
            await conn.execute(_CREATE_POSTGRES_CHECKPOINTS_SQL)
            await conn.execute(_CREATE_INDEX_RUN)
            await conn.execute(_CREATE_INDEX_NODE)
            await conn.execute(_CREATE_INDEX_TS)

    async def close(self) -> None:
        if self._pool is not None and hasattr(self._pool, "close"):
            await self._pool.close()

    async def write(self, record: StepRecord) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO step_records
                  (run_id, step_id, node_id, node_kind, input_task, output_content,
                   verdict, blocked, block_reason, uncertainty, cost_usd, tokens_used,
                   carbon_gco2, duration_ms, timestamp, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb)
                ON CONFLICT (step_id) DO UPDATE SET
                  run_id=EXCLUDED.run_id,
                  node_id=EXCLUDED.node_id,
                  node_kind=EXCLUDED.node_kind,
                  input_task=EXCLUDED.input_task,
                  output_content=EXCLUDED.output_content,
                  verdict=EXCLUDED.verdict,
                  blocked=EXCLUDED.blocked,
                  block_reason=EXCLUDED.block_reason,
                  uncertainty=EXCLUDED.uncertainty,
                  cost_usd=EXCLUDED.cost_usd,
                  tokens_used=EXCLUDED.tokens_used,
                  carbon_gco2=EXCLUDED.carbon_gco2,
                  duration_ms=EXCLUDED.duration_ms,
                  timestamp=EXCLUDED.timestamp,
                  metadata=EXCLUDED.metadata
                """,
                *_record_values(record, sqlite=False),
            )

    async def get_run(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM step_records WHERE run_id=$1 ORDER BY id", run_id
            )
        return [_normalize_row(r) for r in rows]

    async def list_runs(self) -> list[str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT run_id FROM step_records GROUP BY run_id ORDER BY MAX(id) DESC"
            )
        return [r["run_id"] for r in rows]

    async def save_checkpoint(self, run_id: str, data: dict[str, Any]) -> None:
        pool = await self._ensure_pool()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_checkpoints (run_id, data, created_at)
                VALUES ($1, $2::jsonb, $3)
                ON CONFLICT (run_id) DO UPDATE SET
                  data=EXCLUDED.data,
                  created_at=EXCLUDED.created_at
                """,
                run_id,
                json.dumps(data),
                now,
            )

    async def load_checkpoint_data(self, run_id: str) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM workflow_checkpoints WHERE run_id=$1", run_id
            )
        if row is None:
            return None
        data = row["data"]
        return data if isinstance(data, dict) else json.loads(data)

    async def delete_checkpoint(self, run_id: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM workflow_checkpoints WHERE run_id=$1", run_id
            )

    async def list_paused_runs(self) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT run_id, created_at FROM workflow_checkpoints ORDER BY created_at"
            )
        return [{"run_id": r["run_id"], "paused_at": r["created_at"]} for r in rows]


class ReplayLedger:
    """Append-only, replayable ledger facade.

    SQLite is the default backend. PostgreSQL is selected automatically when
    ``db_path`` starts with ``postgres://`` or ``postgresql://``. A custom
    backend can be injected for tests or future storage engines.

    Usage::

        ledger = ReplayLedger("meshflow_runs.db")   # or ":memory:" for tests
        ledger = ReplayLedger("postgresql://user:pass@host/db")

        await ledger.write(step_record)

        steps   = await ledger.get_run("run-id-abc")
        summary = await ledger.run_summary("run-id-abc")
        run_ids = await ledger.list_runs()
    """

    def __init__(self, db_path: str = "meshflow_runs.db", backend: LedgerBackend | None = None) -> None:
        self._db_path = db_path
        if backend is not None:
            self._backend = backend
            self._db_path = getattr(backend, "db_path", db_path)
        elif _is_postgres_dsn(db_path):
            self._backend = PostgresLedgerBackend(db_path)
        else:
            self._backend = SQLiteLedgerBackend(db_path)

    def __del__(self) -> None:
        close = getattr(self._backend, "close", None)
        if close is None:
            return
        try:
            result = close()
            if hasattr(result, "close"):
                result.close()
        except Exception:
            pass

    async def aclose(self) -> None:
        """Close backend resources, including async PostgreSQL pools."""
        close = getattr(self._backend, "close", None)
        if close is None:
            return
        result = close()
        if hasattr(result, "__await__"):
            await result

    # ── Write ─────────────────────────────────────────────────────────────────

    async def write(self, record: StepRecord) -> None:
        await self._backend.write(record)

    # ── Query ─────────────────────────────────────────────────────────────────

    async def get_run(self, run_id: str) -> list[dict[str, Any]]:
        return await self._backend.get_run(run_id)

    async def list_runs(self) -> list[str]:
        return await self._backend.list_runs()

    async def run_summary(self, run_id: str) -> dict[str, Any]:
        records = await self.get_run(run_id)
        if not records:
            return {"run_id": run_id, "steps": 0}
        return {
            "run_id": run_id,
            "steps": len(records),
            "nodes": [r["node_id"] for r in records],
            "total_cost_usd": round(sum(r["cost_usd"] for r in records), 6),
            "total_tokens": sum(r["tokens_used"] for r in records),
            "total_carbon_gco2": round(sum(r["carbon_gco2"] for r in records), 4),
            "blocked_steps": sum(1 for r in records if r["blocked"]),
            "verdicts": [r["verdict"] for r in records],
            "timestamps": {
                "start": records[0]["timestamp"],
                "end": records[-1]["timestamp"],
            },
        }

    async def get_checkpoint(self, run_id: str, step_index: int) -> dict[str, Any] | None:
        """Return the step record at a given index for replay/branching."""
        records = await self.get_run(run_id)
        if 0 <= step_index < len(records):
            return records[step_index]
        return None

    async def export_run(self, run_id: str) -> str:
        """Export a full run as a JSON string for archiving or transfer."""
        records = await self.get_run(run_id)
        return json.dumps({"run_id": run_id, "steps": records}, indent=2)

    # ── Durable HITL checkpoints ──────────────────────────────────────────────

    async def save_checkpoint(self, run_id: str, data: dict[str, Any]) -> None:
        """Persist a paused workflow state so it can survive process restarts."""
        await self._backend.save_checkpoint(run_id, data)

    async def load_checkpoint_data(self, run_id: str) -> dict[str, Any] | None:
        """Load a paused workflow state by run_id. Returns None if not found."""
        return await self._backend.load_checkpoint_data(run_id)

    async def delete_checkpoint(self, run_id: str) -> None:
        """Remove a checkpoint after the workflow has successfully resumed."""
        await self._backend.delete_checkpoint(run_id)

    async def list_paused_runs(self) -> list[dict[str, Any]]:
        """Return all currently paused (checkpointed) runs."""
        return await self._backend.list_paused_runs()


def _is_postgres_dsn(value: str) -> bool:
    return value.startswith(("postgres://", "postgresql://"))


def _record_values(record: StepRecord, *, sqlite: bool) -> tuple[Any, ...]:
    metadata = json.dumps(record.metadata)
    return (
        record.run_id,
        record.step_id,
        record.node_id,
        record.node_kind,
        record.input_task,
        record.output_content,
        record.verdict,
        int(record.blocked) if sqlite else bool(record.blocked),
        record.block_reason,
        record.uncertainty,
        record.cost_usd,
        record.tokens_used,
        record.carbon_gco2,
        record.duration_ms,
        record.timestamp,
        metadata,
    )


def _normalize_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    metadata = data.get("metadata")
    if isinstance(metadata, str):
        try:
            data["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            pass
    return data
