"""ReplayLedger — append-only, replayable run ledger backed by aiosqlite.

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

import json
import sqlite3
from typing import Any

from meshflow.core.runtime import StepRecord

_CREATE_SQL = """
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

_CREATE_CHECKPOINTS_SQL = """
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    run_id      TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

_CREATE_INDEX_RUN   = "CREATE INDEX IF NOT EXISTS idx_run_id    ON step_records(run_id)"
_CREATE_INDEX_NODE  = "CREATE INDEX IF NOT EXISTS idx_node_id   ON step_records(node_id)"
_CREATE_INDEX_TS    = "CREATE INDEX IF NOT EXISTS idx_timestamp ON step_records(timestamp)"


class ReplayLedger:
    """Append-only, replayable run ledger backed by synchronous SQLite.

    Uses a single persistent connection so in-memory databases work
    across multiple calls within the same process.

    Usage::

        ledger = ReplayLedger("meshflow_runs.db")   # or ":memory:" for tests

        await ledger.write(step_record)

        steps   = await ledger.get_run("run-id-abc")
        summary = await ledger.run_summary("run-id-abc")
        run_ids = await ledger.list_runs()
    """

    def __init__(self, db_path: str = "meshflow_runs.db") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_SQL)
            self._conn.execute(_CREATE_CHECKPOINTS_SQL)
            self._conn.execute(_CREATE_INDEX_RUN)
            self._conn.execute(_CREATE_INDEX_NODE)
            self._conn.execute(_CREATE_INDEX_TS)

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ── Write ─────────────────────────────────────────────────────────────────

    async def write(self, record: StepRecord) -> None:
        self._write_sync(record)

    def _write_sync(self, record: StepRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO step_records
                  (run_id, step_id, node_id, node_kind, input_task, output_content,
                   verdict, blocked, block_reason, uncertainty, cost_usd, tokens_used,
                   carbon_gco2, duration_ms, timestamp, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.run_id, record.step_id, record.node_id, record.node_kind,
                    record.input_task, record.output_content, record.verdict,
                    int(record.blocked), record.block_reason, record.uncertainty,
                    record.cost_usd, record.tokens_used, record.carbon_gco2,
                    record.duration_ms, record.timestamp,
                    json.dumps(record.metadata),
                ),
            )

    # ── Query ─────────────────────────────────────────────────────────────────

    async def get_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM step_records WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    async def list_runs(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT run_id FROM step_records ORDER BY id DESC"
        ).fetchall()
        return [r[0] for r in rows]

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
        import datetime
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
        """Load a paused workflow state by run_id. Returns None if not found."""
        row = self._conn.execute(
            "SELECT data FROM workflow_checkpoints WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    async def delete_checkpoint(self, run_id: str) -> None:
        """Remove a checkpoint after the workflow has successfully resumed."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM workflow_checkpoints WHERE run_id=?", (run_id,)
            )

    async def list_paused_runs(self) -> list[dict[str, Any]]:
        """Return all currently paused (checkpointed) runs."""
        rows = self._conn.execute(
            "SELECT run_id, created_at FROM workflow_checkpoints ORDER BY created_at"
        ).fetchall()
        return [{"run_id": r[0], "paused_at": r[1]} for r in rows]
