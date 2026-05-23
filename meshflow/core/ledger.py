"""ReplayLedger — append-only, replayable run ledger.

Every governed step writes a StepRecord here. The ledger answers:
  - Why did run X fail?
  - Which node made this decision?
  - What evidence was in scope at step N?
  - Can I replay from checkpoint 4?
  - Was this action policy-approved?
  - What was the total carbon cost?

The ledger is append-only by design. Records are never modified after write.
Schema migrations are applied on every startup — safe to run on existing databases.
"""

from __future__ import annotations

import base64
import datetime
import gzip
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from meshflow.core.runtime import StepRecord

# ── Schema migration registry ─────────────────────────────────────────────────
# Each entry: (version, sql).  Applied in order; never modified once shipped.
# SQLite does not support ADD COLUMN IF NOT EXISTS — we wrap in try/except.
# Postgres supports it natively.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE step_records ADD COLUMN prev_hash TEXT DEFAULT ''"),
    (2, "ALTER TABLE step_records ADD COLUMN entry_hash TEXT DEFAULT ''"),
    (3, "ALTER TABLE step_records ADD COLUMN output_compressed INTEGER DEFAULT 0"),
    (4, "ALTER TABLE workflow_checkpoints ADD COLUMN reviewer_id TEXT DEFAULT ''"),
    (5, "ALTER TABLE workflow_checkpoints ADD COLUMN review_notes TEXT DEFAULT ''"),
    (6, "ALTER TABLE workflow_checkpoints ADD COLUMN approved INTEGER DEFAULT -1"),
    (7, "ALTER TABLE step_records ADD COLUMN tenant_id TEXT DEFAULT 'default'"),
    (8, "ALTER TABLE workflow_checkpoints ADD COLUMN tenant_id TEXT DEFAULT 'default'"),
]

_CREATE_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


# ── Compression helpers ───────────────────────────────────────────────────────

_COMPRESS_THRESHOLD = 10_000  # bytes: compress output > 10 KB


def _compress_output(text: str) -> tuple[str, bool]:
    """Return (stored_value, was_compressed)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= _COMPRESS_THRESHOLD:
        return text, False
    compressed = base64.b64encode(gzip.compress(encoded, compresslevel=6)).decode("ascii")
    return compressed, True


def _decompress_output(value: str, compressed: bool) -> str:
    if not compressed:
        return value
    try:
        return gzip.decompress(base64.b64decode(value)).decode("utf-8")
    except Exception:
        return value


_CREATE_SQLITE_STEPS_SQL = """
CREATE TABLE IF NOT EXISTS step_records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL,
    step_id           TEXT    NOT NULL UNIQUE,
    node_id           TEXT    NOT NULL,
    node_kind         TEXT    NOT NULL,
    input_task        TEXT,
    output_content    TEXT,
    output_compressed INTEGER NOT NULL DEFAULT 0,
    verdict           TEXT,
    blocked           INTEGER NOT NULL DEFAULT 0,
    block_reason      TEXT    DEFAULT '',
    uncertainty       REAL    DEFAULT 0.0,
    cost_usd          REAL    DEFAULT 0.0,
    tokens_used       INTEGER DEFAULT 0,
    carbon_gco2       REAL    DEFAULT 0.0,
    duration_ms       REAL    DEFAULT 0.0,
    timestamp         TEXT,
    prev_hash         TEXT    DEFAULT '',
    entry_hash        TEXT    DEFAULT '',
    tenant_id         TEXT    NOT NULL DEFAULT 'default',
    metadata          TEXT    DEFAULT '{}'
)
"""

_CREATE_SQLITE_CHECKPOINTS_SQL = """
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    run_id        TEXT PRIMARY KEY,
    data          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    reviewer_id   TEXT DEFAULT '',
    review_notes  TEXT DEFAULT '',
    approved      INTEGER DEFAULT -1,
    tenant_id     TEXT NOT NULL DEFAULT 'default'
)
"""

_CREATE_POSTGRES_STEPS_SQL = """
CREATE TABLE IF NOT EXISTS step_records (
    id                BIGSERIAL PRIMARY KEY,
    run_id            TEXT    NOT NULL,
    step_id           TEXT    NOT NULL UNIQUE,
    node_id           TEXT    NOT NULL,
    node_kind         TEXT    NOT NULL,
    input_task        TEXT,
    output_content    TEXT,
    output_compressed BOOLEAN NOT NULL DEFAULT FALSE,
    verdict           TEXT,
    blocked           BOOLEAN NOT NULL DEFAULT FALSE,
    block_reason      TEXT    DEFAULT '',
    uncertainty       DOUBLE PRECISION DEFAULT 0.0,
    cost_usd          DOUBLE PRECISION DEFAULT 0.0,
    tokens_used       INTEGER DEFAULT 0,
    carbon_gco2       DOUBLE PRECISION DEFAULT 0.0,
    duration_ms       DOUBLE PRECISION DEFAULT 0.0,
    timestamp         TEXT,
    prev_hash         TEXT    DEFAULT '',
    entry_hash        TEXT    DEFAULT '',
    tenant_id         TEXT    NOT NULL DEFAULT 'default',
    metadata          JSONB DEFAULT '{}'::jsonb
)
"""

_CREATE_POSTGRES_CHECKPOINTS_SQL = """
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    run_id        TEXT PRIMARY KEY,
    data          JSONB NOT NULL,
    created_at    TEXT NOT NULL,
    reviewer_id   TEXT DEFAULT '',
    review_notes  TEXT DEFAULT '',
    approved      INTEGER DEFAULT -1,
    tenant_id     TEXT NOT NULL DEFAULT 'default'
)
"""

_CREATE_INDEX_RUN = "CREATE INDEX IF NOT EXISTS idx_run_id    ON step_records(run_id)"
_CREATE_INDEX_NODE = "CREATE INDEX IF NOT EXISTS idx_node_id   ON step_records(node_id)"
_CREATE_INDEX_TS = "CREATE INDEX IF NOT EXISTS idx_timestamp ON step_records(timestamp)"


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


@dataclass(frozen=True)
class LedgerArchiveResult:
    """Result returned after exporting a run to immutable archive storage."""

    run_id: str
    uri: str
    bucket: str
    key: str
    bytes_written: int
    sha256: str


class S3LedgerArchiveBackend:
    """Write-once S3 archive target for exported ReplayLedger runs.

    The backend stores each run export as ``{prefix}/{run_id}.json`` and sends
    ``IfNoneMatch="*"`` on upload so an existing archive object is not replaced.
    ``boto3`` is imported lazily to keep local SQLite usage dependency-light.
    """

    def __init__(self, uri: str, client: Any = None) -> None:
        self.uri = uri
        self.bucket, self.prefix = _parse_s3_uri(uri)
        self._client = client

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 ledger archive requires boto3. Install boto3 or pass an S3-compatible client."
            ) from exc
        self._client = boto3.client("s3")
        return self._client

    async def archive_json(self, run_id: str, payload: str) -> LedgerArchiveResult:
        key = _archive_key(self.prefix, run_id)
        body = payload.encode("utf-8")
        sha256 = hashlib.sha256(body).hexdigest()
        client = self._client_or_create()
        try:
            client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                Metadata={
                    "meshflow-run-id": run_id,
                    "meshflow-sha256": sha256,
                },
                IfNoneMatch="*",
            )
        except Exception as exc:
            if _is_precondition_failed(exc):
                raise FileExistsError(
                    f"S3 archive object already exists: s3://{self.bucket}/{key}"
                ) from exc
            raise
        return LedgerArchiveResult(
            run_id=run_id,
            uri=f"s3://{self.bucket}/{key}",
            bucket=self.bucket,
            key=key,
            bytes_written=len(body),
            sha256=sha256,
        )


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
            self._conn.execute(_CREATE_MIGRATIONS_TABLE_SQL)
        self._run_migrations_sqlite()

    def _run_migrations_sqlite(self) -> None:
        applied = {
            r[0] for r in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for version, sql in _MIGRATIONS:
            if version in applied:
                continue
            try:
                with self._conn:
                    self._conn.execute(sql)
                    self._conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, now),
                    )
            except sqlite3.OperationalError:
                # Column already exists — mark as applied anyway
                try:
                    with self._conn:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                            (version, now),
                        )
                except Exception:
                    pass

    def close(self) -> None:
        self._conn.close()

    async def write(self, record: StepRecord) -> None:
        stored_output, was_compressed = _compress_output(record.output_content)
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO step_records
                  (run_id, step_id, node_id, node_kind, input_task, output_content,
                   output_compressed, verdict, blocked, block_reason, uncertainty,
                   cost_usd, tokens_used, carbon_gco2, duration_ms, timestamp,
                   prev_hash, entry_hash, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                _record_values(
                    record, sqlite=True, stored_output=stored_output, was_compressed=was_compressed
                ),
            )

    async def get_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM step_records WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [_normalize_row(dict(r)) for r in rows]

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
        return cast(dict[str, Any], json.loads(row[0]))

    async def delete_checkpoint(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM workflow_checkpoints WHERE run_id=?", (run_id,))

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

    def __init__(
        self,
        dsn: str,
        pool: Any = None,
        *,
        min_size: int | None = None,
        max_size: int | None = None,
        command_timeout: float | None = None,
    ) -> None:
        self.db_path = dsn
        self._dsn = dsn
        self._pool = pool
        self._initialized = False
        # Pool sizing: env vars override constructor kwargs
        import os
        self._min_size = int(os.environ.get("MESHFLOW_PG_POOL_MIN", min_size or 2))
        self._max_size = int(os.environ.get("MESHFLOW_PG_POOL_MAX", max_size or 10))
        self._command_timeout = float(
            os.environ.get("MESHFLOW_PG_TIMEOUT", command_timeout or 30.0)
        )

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL ledger requires asyncpg. Install meshflow with asyncpg "
                    "or use a SQLite ledger path."
                ) from exc
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=self._command_timeout,
                statement_cache_size=100,  # prepared statement cache per connection
            )
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
            await conn.execute(_CREATE_MIGRATIONS_TABLE_SQL)
            await self._run_migrations_postgres(conn)

    async def _run_migrations_postgres(self, conn: Any) -> None:
        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for version, sql in _MIGRATIONS:
            if version in applied:
                continue
            pg_sql = sql.replace("ADD COLUMN ", "ADD COLUMN IF NOT EXISTS ")
            try:
                await conn.execute(pg_sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES ($1, $2) "
                    "ON CONFLICT DO NOTHING",
                    version,
                    now,
                )
            except Exception:
                pass

    async def close(self) -> None:
        if self._pool is not None and hasattr(self._pool, "close"):
            await self._pool.close()

    async def write(self, record: StepRecord) -> None:
        pool = await self._ensure_pool()
        stored_output, was_compressed = _compress_output(record.output_content)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO step_records
                  (run_id, step_id, node_id, node_kind, input_task, output_content,
                   output_compressed, verdict, blocked, block_reason, uncertainty,
                   cost_usd, tokens_used, carbon_gco2, duration_ms, timestamp,
                   prev_hash, entry_hash, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::jsonb)
                ON CONFLICT (step_id) DO UPDATE SET
                  output_content=EXCLUDED.output_content,
                  output_compressed=EXCLUDED.output_compressed,
                  verdict=EXCLUDED.verdict,
                  blocked=EXCLUDED.blocked,
                  block_reason=EXCLUDED.block_reason,
                  uncertainty=EXCLUDED.uncertainty,
                  cost_usd=EXCLUDED.cost_usd,
                  tokens_used=EXCLUDED.tokens_used,
                  carbon_gco2=EXCLUDED.carbon_gco2,
                  duration_ms=EXCLUDED.duration_ms,
                  timestamp=EXCLUDED.timestamp,
                  prev_hash=EXCLUDED.prev_hash,
                  entry_hash=EXCLUDED.entry_hash,
                  metadata=EXCLUDED.metadata
                """,
                *_record_values(
                    record, sqlite=False, stored_output=stored_output, was_compressed=was_compressed
                ),
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
            await conn.execute("DELETE FROM workflow_checkpoints WHERE run_id=$1", run_id)

    async def list_paused_runs(self) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT run_id, created_at FROM workflow_checkpoints ORDER BY created_at"
            )
        return [{"run_id": r["run_id"], "paused_at": r["created_at"]} for r in rows]


class ReplayLedger:
    """Append-only, replayable ledger facade with multi-tenant namespace isolation.

    SQLite is the default backend. PostgreSQL is selected automatically when
    ``db_path`` starts with ``postgres://`` or ``postgresql://``. A custom
    backend can be injected for tests or future storage engines.

    Usage::

        ledger = ReplayLedger("meshflow_runs.db")   # or ":memory:" for tests
        ledger = ReplayLedger("postgresql://user:pass@host/db", tenant_id="acme")

        await ledger.write(step_record)

        steps   = await ledger.get_run("run-id-abc")
        summary = await ledger.run_summary("run-id-abc")
        run_ids = await ledger.list_runs()
    """

    def __init__(
        self,
        db_path: str = "meshflow_runs.db",
        backend: LedgerBackend | None = None,
        tenant_id: str = "default",
    ) -> None:
        self._db_path = db_path
        self._tenant_id = tenant_id
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
        backend = self._backend
        if isinstance(backend, SQLiteLedgerBackend) and self._tenant_id != "default":
            stored_output, was_compressed = _compress_output(record.output_content)
            vals = _record_values(
                record, sqlite=True, stored_output=stored_output, was_compressed=was_compressed
            )
            with backend._conn:
                backend._conn.execute(
                    """
                    INSERT OR REPLACE INTO step_records
                      (run_id, step_id, node_id, node_kind, input_task, output_content,
                       output_compressed, verdict, blocked, block_reason, uncertainty,
                       cost_usd, tokens_used, carbon_gco2, duration_ms, timestamp,
                       prev_hash, entry_hash, metadata, tenant_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (*vals, self._tenant_id),
                )
        else:
            await backend.write(record)

    # ── Query ─────────────────────────────────────────────────────────────────

    async def get_run(self, run_id: str) -> list[dict[str, Any]]:
        return await self._backend.get_run(run_id)

    async def list_runs(self) -> list[str]:
        backend = self._backend
        if isinstance(backend, SQLiteLedgerBackend):
            rows = backend._conn.execute(
                "SELECT run_id FROM step_records WHERE tenant_id=? GROUP BY run_id ORDER BY MAX(id) DESC",
                (self._tenant_id,),
            ).fetchall()
            return [r[0] for r in rows]
        return await backend.list_runs()

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

    async def export_run_csv(self, run_id: str) -> str:
        """Export a full run as a CSV string (compliance/audit artifact)."""
        import csv
        import io

        records = await self.get_run(run_id)
        if not records:
            return ""

        fieldnames = [
            "run_id", "step_id", "node_id", "node_kind", "verdict",
            "blocked", "block_reason", "uncertainty", "cost_usd",
            "tokens_used", "carbon_gco2", "duration_ms", "timestamp",
            "entry_hash", "prev_hash",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = {k: r.get(k, "") for k in fieldnames}
            row["run_id"] = run_id
            writer.writerow(row)
        return buf.getvalue()

    async def verify_chain(self, run_id: str) -> dict[str, Any]:
        """Verify the tamper-evident hash chain for a run.

        Recomputes each record's ``entry_hash`` from its canonical fields and
        checks that ``prev_hash`` matches the previous record's ``entry_hash``.
        Any mismatch indicates that a record was modified after it was written.

        Returns::

            {
                "run_id": "...",
                "valid": True,           # False if any chain break found
                "steps_verified": 12,
                "errors": [],            # list of human-readable chain breaks
            }
        """
        from meshflow.core.runtime import _hash_record

        records = await self.get_run(run_id)
        errors: list[str] = []
        prev_hash = ""

        for i, r in enumerate(records):
            step_id = r.get("step_id", "?")

            # Verify that prev_hash matches what we computed for the previous record
            stored_prev = r.get("prev_hash", "")
            if stored_prev != prev_hash:
                errors.append(
                    f"step {i} ({step_id}): prev_hash mismatch "
                    f"(expected={prev_hash[:12]}... got={stored_prev[:12]}...)"
                )

            # Recompute entry_hash from stored fields
            expected_hash = _hash_record(
                run_id=r.get("run_id", ""),
                step_id=step_id,
                node_id=r.get("node_id", ""),
                input_task=str(r.get("input_task", ""))[:200],
                output_content=str(r.get("output_content", ""))[:200],
                verdict=r.get("verdict", ""),
                blocked=bool(r.get("blocked", False)),
                timestamp=r.get("timestamp", ""),
                prev_hash=stored_prev,
            )
            stored_hash = r.get("entry_hash", "")
            if stored_hash != expected_hash:
                errors.append(f"step {i} ({step_id}): entry_hash mismatch — record was modified")

            prev_hash = stored_hash or expected_hash

        return {
            "run_id": run_id,
            "valid": len(errors) == 0,
            "steps_verified": len(records),
            "errors": errors,
        }

    async def archive_run(
        self,
        run_id: str,
        archive_uri: str,
        backend: S3LedgerArchiveBackend | None = None,
    ) -> LedgerArchiveResult:
        """Export a run and store it in write-once S3 archive storage."""
        if not _is_s3_uri(archive_uri):
            raise ValueError(f"Unsupported archive URI: {archive_uri!r}")
        records = await self.get_run(run_id)
        if not records:
            raise ValueError(f"Cannot archive unknown run_id={run_id!r}")
        payload = json.dumps({"run_id": run_id, "steps": records}, indent=2)
        archive = backend or S3LedgerArchiveBackend(archive_uri)
        return await archive.archive_json(run_id, payload)

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

    # ── Eval result storage ───────────────────────────────────────────────────

    async def save_eval_result(self, result: Any) -> str:
        """Persist an EvalResult to the ledger as a checkpoint entry.

        Eval results are stored in the checkpoints table under the key
        ``eval:<suite_name>:<timestamp>`` so they survive across process
        restarts and can be retrieved for regression diffing.

        Returns the storage key.
        """
        from meshflow.eval.baseline import EvalBaseline

        baseline = EvalBaseline.from_result(result)
        key = f"eval:{result.suite_name}:{baseline.timestamp}"
        await self.save_checkpoint(key, {"_type": "eval_result", **baseline.to_dict()})
        return key

    async def list_eval_results(self, suite_name: str | None = None) -> list[dict[str, Any]]:
        """Return stored eval results, optionally filtered by suite_name.

        Each entry is an ``EvalBaseline.to_dict()`` payload augmented with
        a ``storage_key`` field.
        """
        paused = await self._backend.list_paused_runs()
        results = []
        for row in paused:
            run_id = row.get("run_id", "")
            if not run_id.startswith("eval:"):
                continue
            data = await self._backend.load_checkpoint_data(run_id)
            if data is None or data.get("_type") != "eval_result":
                continue
            if suite_name and data.get("suite_name") != suite_name:
                continue
            entry = {k: v for k, v in data.items() if k != "_type"}
            entry["storage_key"] = run_id
            results.append(entry)
        return results

    # ── GDPR right-to-erasure ─────────────────────────────────────────────────

    async def delete_run(self, run_id: str) -> int:
        """Delete all step records and checkpoint for a run. Returns rows deleted."""
        backend = self._backend
        if isinstance(backend, SQLiteLedgerBackend):
            with backend._conn:
                n1 = backend._conn.execute(
                    "DELETE FROM step_records WHERE run_id=?", (run_id,)
                ).rowcount
                n2 = backend._conn.execute(
                    "DELETE FROM workflow_checkpoints WHERE run_id=?", (run_id,)
                ).rowcount
            return (n1 or 0) + (n2 or 0)
        return 0

    async def delete_tenant(self, tenant_id: str) -> int:
        """Delete all records for a tenant namespace. Returns rows deleted."""
        backend = self._backend
        if isinstance(backend, SQLiteLedgerBackend):
            with backend._conn:
                n1 = backend._conn.execute(
                    "DELETE FROM step_records WHERE tenant_id=?", (tenant_id,)
                ).rowcount
                n2 = backend._conn.execute(
                    "DELETE FROM workflow_checkpoints WHERE tenant_id=?", (tenant_id,)
                ).rowcount
            return (n1 or 0) + (n2 or 0)
        return 0

    async def anonymize_run(self, run_id: str) -> int:
        """Replace input/output content with [REDACTED] while preserving audit structure."""
        backend = self._backend
        if isinstance(backend, SQLiteLedgerBackend):
            with backend._conn:
                n = backend._conn.execute(
                    "UPDATE step_records SET input_task='[REDACTED]', output_content='[REDACTED]' "
                    "WHERE run_id=?",
                    (run_id,),
                ).rowcount
            return n or 0
        return 0


def _is_postgres_dsn(value: str) -> bool:
    return value.startswith(("postgres://", "postgresql://"))


def _is_s3_uri(value: str) -> bool:
    return value.startswith("s3://")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def _archive_key(prefix: str, run_id: str) -> str:
    filename = f"{run_id}.json"
    if not prefix:
        return filename
    return f"{prefix.rstrip('/')}/{filename}"


def _is_precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = response.get("Error", {}).get("Code")
    return code in {"PreconditionFailed", "412"}


def _record_values(
    record: StepRecord,
    *,
    sqlite: bool,
    stored_output: str = "",
    was_compressed: bool = False,
) -> tuple[Any, ...]:
    metadata = json.dumps(record.metadata)
    output = stored_output if stored_output else record.output_content
    compressed_flag = int(was_compressed) if sqlite else bool(was_compressed)
    return (
        record.run_id,
        record.step_id,
        record.node_id,
        record.node_kind,
        record.input_task,
        output,
        compressed_flag,
        record.verdict,
        int(record.blocked) if sqlite else bool(record.blocked),
        record.block_reason,
        record.uncertainty,
        record.cost_usd,
        record.tokens_used,
        record.carbon_gco2,
        record.duration_ms,
        record.timestamp,
        record.prev_hash,
        record.entry_hash,
        metadata,
    )


def _normalize_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    # Decompress output_content transparently
    compressed = bool(data.pop("output_compressed", False))
    if compressed and "output_content" in data:
        data["output_content"] = _decompress_output(str(data["output_content"]), True)
    metadata = data.get("metadata")
    if isinstance(metadata, str):
        try:
            data["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            pass
    return data
