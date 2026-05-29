"""Sprint 43 — Human feedback store + fine-tuning export bridge.

Collects thumbs-up/down and corrections on agent outputs.
Feedback records are stored in SQLite and can be exported as fine-tuning JSONL
(bridges directly into Sprint 37's FinetuneExporter).

Usage::

    from meshflow.eval.feedback import FeedbackRecord, FeedbackStore

    store = FeedbackStore(":memory:")

    # Record feedback after a run
    store.save(FeedbackRecord(
        run_id="run-abc",
        agent_name="billing-agent",
        task="What is my invoice total?",
        original_output="Your invoice total is $120.",
        score=0.9,
        correction="",       # empty → no correction needed
        reviewer="alice",
    ))

    # Export corrections as fine-tuning JSONL
    jsonl = store.export_finetune(agent_name="billing-agent")
    print(jsonl)              # OpenAI JSONL format
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeedbackRecord:
    """One piece of human feedback on an agent output.

    Attributes
    ----------
    run_id:           The run_id of the agent step being rated.
    agent_name:       Which agent produced the output.
    task:             The original task/prompt.
    original_output:  What the agent said.
    score:            0.0 (bad) → 1.0 (perfect).
    correction:       Human-corrected output (empty if output was fine).
    reviewer:         Identifier for who submitted this feedback.
    metadata:         Any extra context (session_id, user_id, …).
    feedback_id:      Auto-generated unique ID.
    created_at:       Unix timestamp.
    """

    run_id: str
    agent_name: str
    task: str
    original_output: str
    score: float = 1.0
    correction: str = ""
    reviewer: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    feedback_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    created_at: float = field(default_factory=time.time)

    @property
    def has_correction(self) -> bool:
        return bool(self.correction.strip())

    @property
    def preferred_output(self) -> str:
        """The best output: correction if provided, else original."""
        return self.correction if self.has_correction else self.original_output

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id":     self.feedback_id,
            "run_id":          self.run_id,
            "agent_name":      self.agent_name,
            "task":            self.task,
            "original_output": self.original_output,
            "score":           self.score,
            "correction":      self.correction,
            "reviewer":        self.reviewer,
            "metadata":        self.metadata,
            "created_at":      self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FeedbackRecord":
        return cls(
            run_id=d.get("run_id", ""),
            agent_name=d.get("agent_name", ""),
            task=d.get("task", ""),
            original_output=d.get("original_output", ""),
            score=d.get("score", 1.0),
            correction=d.get("correction", ""),
            reviewer=d.get("reviewer", ""),
            metadata=d.get("metadata", {}),
            feedback_id=d.get("feedback_id", str(uuid.uuid4())[:12]),
            created_at=d.get("created_at", time.time()),
        )


class FeedbackStore:
    """SQLite-backed store for human feedback records.

    Parameters
    ----------
    path:  ``":memory:"`` for in-process tests; file path for production.
    """

    def __init__(self, path: str = "meshflow_feedback.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._conn
        return sqlite3.connect(self._path, check_same_thread=False)

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                agent_name  TEXT NOT NULL,
                data        TEXT NOT NULL,
                score       REAL NOT NULL DEFAULT 1.0,
                created_at  REAL NOT NULL DEFAULT 0
            )
        """)
        conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def save(self, record: FeedbackRecord) -> None:
        """Persist a feedback record."""
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO feedback
               (feedback_id, run_id, agent_name, data, score, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                record.feedback_id,
                record.run_id,
                record.agent_name,
                json.dumps(record.to_dict()),
                record.score,
                record.created_at,
            ),
        )
        conn.commit()

    def get(self, feedback_id: str) -> FeedbackRecord | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT data FROM feedback WHERE feedback_id=?", (feedback_id,)
        ).fetchone()
        return FeedbackRecord.from_dict(json.loads(row[0])) if row else None

    def get_by_run(self, run_id: str) -> FeedbackRecord | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT data FROM feedback WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return FeedbackRecord.from_dict(json.loads(row[0])) if row else None

    def list(
        self,
        *,
        agent_name: str = "",
        min_score: float = 0.0,
        max_score: float = 1.0,
        limit: int = 100,
    ) -> list[FeedbackRecord]:
        """Return feedback records, optionally filtered."""
        conn = self._connect()
        sql = "SELECT data FROM feedback WHERE score>=? AND score<=?"
        params: list[Any] = [min_score, max_score]
        if agent_name:
            sql += " AND agent_name=?"
            params.append(agent_name)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [FeedbackRecord.from_dict(json.loads(r[0])) for r in rows]

    def delete(self, feedback_id: str) -> bool:
        conn = self._connect()
        cur = conn.execute("DELETE FROM feedback WHERE feedback_id=?", (feedback_id,))
        conn.commit()
        return cur.rowcount > 0

    # ── Export to fine-tuning JSONL ───────────────────────────────────────────

    def export_finetune(
        self,
        *,
        agent_name: str = "",
        min_score: float = 0.0,
        format: str = "openai",
        corrections_only: bool = False,
    ) -> str:
        """Export feedback as fine-tuning JSONL.

        Each record becomes one training example with the *preferred_output*
        (correction if provided, else original_output).

        Parameters
        ----------
        agent_name:       Filter to a specific agent (empty → all agents).
        min_score:        Minimum score threshold.
        format:           ``"openai"`` | ``"anthropic"`` | ``"generic"`` | ``"sharegpt"``
        corrections_only: Only export records that have a human correction.
        """
        records = self.list(agent_name=agent_name, min_score=min_score)
        if corrections_only:
            records = [r for r in records if r.has_correction]

        lines: list[str] = []
        for rec in records:
            example = _to_finetune_example(rec, format)
            lines.append(json.dumps(example))
        return "\n".join(lines)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self, agent_name: str = "") -> dict[str, Any]:
        """Return aggregate feedback statistics."""
        records = self.list(agent_name=agent_name, limit=10_000)
        if not records:
            return {"count": 0, "avg_score": 0.0, "corrections": 0}
        scores = [r.score for r in records]
        corrections = sum(1 for r in records if r.has_correction)
        return {
            "count":       len(records),
            "avg_score":   round(sum(scores) / len(scores), 4),
            "min_score":   min(scores),
            "max_score":   max(scores),
            "corrections": corrections,
            "correction_rate": round(corrections / len(records), 4),
        }

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]


# ── Fine-tuning format helpers ─────────────────────────────────────────────────

def _to_finetune_example(rec: FeedbackRecord, fmt: str) -> dict[str, Any]:
    output = rec.preferred_output
    if fmt == "openai":
        messages = [{"role": "user", "content": rec.task},
                    {"role": "assistant", "content": output}]
        return {"messages": messages}
    if fmt == "anthropic":
        return {"human": rec.task, "assistant": output}
    if fmt == "sharegpt":
        return {"conversations": [
            {"from": "human", "value": rec.task},
            {"from": "gpt",   "value": output},
        ]}
    # generic
    return {
        "prompt":     rec.task,
        "completion": output,
        "metadata": {
            "run_id":     rec.run_id,
            "agent_name": rec.agent_name,
            "score":      rec.score,
            "reviewer":   rec.reviewer,
        },
    }


class FeedbackCollector:
    """Aggregates human feedback from a :class:`FeedbackStore`.

    Bridges HITL feedback into fine-tuning preparation by exposing per-run
    summaries and exportable (prompt, output, correction) training pairs.

    Usage::

        from meshflow.eval.feedback import FeedbackCollector, FeedbackStore

        store = FeedbackStore("meshflow_feedback.db")
        collector = FeedbackCollector(store)

        print(collector.summary("run-abc123"))
        pairs = collector.export_training_pairs()
    """

    def __init__(self, store: FeedbackStore) -> None:
        self._store = store

    def summary(self, run_id: str) -> dict[str, Any]:
        """Return aggregate statistics for all feedback on *run_id*."""
        records = self._store.list(limit=10_000)
        run_records = [r for r in records if r.run_id == run_id]
        if not run_records:
            return {"run_id": run_id, "count": 0, "avg_score": 0.0, "corrections": 0}
        scores = [r.score for r in run_records]
        return {
            "run_id": run_id,
            "count": len(run_records),
            "avg_score": round(sum(scores) / len(scores), 4),
            "min_score": min(scores),
            "max_score": max(scores),
            "corrections": sum(1 for r in run_records if r.has_correction),
            "correction_rate": round(
                sum(1 for r in run_records if r.has_correction) / len(run_records), 4
            ),
        }

    def export_training_pairs(
        self,
        *,
        agent_name: str = "",
        corrections_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Export (prompt, output, correction) tuples suitable for fine-tuning JSONL.

        Parameters
        ----------
        agent_name:       Filter to a specific agent (empty → all agents).
        corrections_only: Only include records that carry a human correction.
        """
        records = self._store.list(agent_name=agent_name, limit=100_000)
        if corrections_only:
            records = [r for r in records if r.has_correction]
        return [
            {
                "prompt":      r.task,
                "output":      r.original_output,
                "correction":  r.correction,
                "score":       r.score,
                "agent_name":  r.agent_name,
                "run_id":      r.run_id,
                "reviewer":    r.reviewer,
            }
            for r in records
        ]

    def global_summary(self) -> dict[str, Any]:
        """Return aggregate statistics across all stored feedback."""
        return self._store.stats()


__all__ = ["FeedbackRecord", "FeedbackStore", "FeedbackCollector"]
