"""Sprint 62 — Correlation / Distributed Tracing.

W3C traceparent-compatible trace IDs that flow across agent-to-agent calls,
with a span hierarchy stored in SQLite for post-hoc audit.

TraceContext  — immutable W3C traceparent carrier (trace_id + span_id).
Span          — a timed unit of work with parent linkage.
SpanKind      — ROOT | AGENT | TOOL | LLM | A2A | GUARDRAIL
SpanStatus    — OK | ERROR | UNSET
TraceStore    — SQLite-backed span repository.
Tracer        — create / finish spans; propagate context.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SpanKind(str, Enum):
    ROOT      = "root"
    AGENT     = "agent"
    TOOL      = "tool"
    LLM       = "llm"
    A2A       = "a2a"
    GUARDRAIL = "guardrail"
    INTERNAL  = "internal"


class SpanStatus(str, Enum):
    OK    = "ok"
    ERROR = "error"
    UNSET = "unset"


_DDL = """
CREATE TABLE IF NOT EXISTS trace_spans (
    span_id      TEXT    PRIMARY KEY,
    trace_id     TEXT    NOT NULL,
    parent_id    TEXT,
    name         TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'unset',
    start_ts     REAL    NOT NULL,
    end_ts       REAL,
    duration_ms  REAL,
    agent_name   TEXT    NOT NULL DEFAULT '',
    run_id       TEXT    NOT NULL DEFAULT '',
    error        TEXT    NOT NULL DEFAULT '',
    attributes   TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ts_trace  ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_ts_parent ON trace_spans(parent_id);
CREATE INDEX IF NOT EXISTS idx_ts_run    ON trace_spans(run_id);
"""


# ── TraceContext (W3C traceparent) ────────────────────────────────────────────

@dataclass(frozen=True)
class TraceContext:
    """Immutable W3C traceparent-compatible context carrier."""

    trace_id: str
    span_id:  str
    sampled:  bool = True

    @classmethod
    def new_root(cls) -> "TraceContext":
        return cls(
            trace_id=uuid.uuid4().hex,
            span_id=os.urandom(8).hex(),
        )

    @classmethod
    def child(cls, parent: "TraceContext") -> "TraceContext":
        return cls(trace_id=parent.trace_id, span_id=os.urandom(8).hex())

    def traceparent(self) -> str:
        flag = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flag}"

    @classmethod
    def from_traceparent(cls, header: str) -> Optional["TraceContext"]:
        parts = header.split("-")
        if len(parts) < 4:
            return None
        return cls(trace_id=parts[1], span_id=parts[2], sampled=parts[3] == "01")


# ── Span ──────────────────────────────────────────────────────────────────────

@dataclass
class Span:
    span_id:    str
    trace_id:   str
    name:       str
    kind:       SpanKind
    start_ts:   float
    parent_id:  Optional[str]  = None
    end_ts:     Optional[float] = None
    status:     SpanStatus      = SpanStatus.UNSET
    agent_name: str             = ""
    run_id:     str             = ""
    error:      str             = ""
    attributes: dict[str, Any]  = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end_ts is None:
            return None
        return (self.end_ts - self.start_ts) * 1000

    @property
    def is_finished(self) -> bool:
        return self.end_ts is not None

    def finish(self, status: SpanStatus = SpanStatus.OK, error: str = "") -> None:
        self.end_ts = time.time()
        self.status = status
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id":     self.span_id,
            "trace_id":    self.trace_id,
            "parent_id":   self.parent_id,
            "name":        self.name,
            "kind":        self.kind.value,
            "status":      self.status.value,
            "start_ts":    self.start_ts,
            "end_ts":      self.end_ts,
            "duration_ms": self.duration_ms,
            "agent_name":  self.agent_name,
            "run_id":      self.run_id,
            "error":       self.error,
            "attributes":  self.attributes,
        }


# ── TraceStore ────────────────────────────────────────────────────────────────

class TraceStore:
    """SQLite-backed span repository."""

    def __init__(self, db_path: str = "meshflow_traces.db") -> None:
        self._db_path = db_path
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    def save(self, span: Span) -> None:
        import json
        self._conn().execute(
            """INSERT OR REPLACE INTO trace_spans
               (span_id,trace_id,parent_id,name,kind,status,start_ts,end_ts,
                duration_ms,agent_name,run_id,error,attributes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                span.span_id, span.trace_id, span.parent_id, span.name,
                span.kind.value, span.status.value, span.start_ts, span.end_ts,
                span.duration_ms, span.agent_name, span.run_id, span.error,
                json.dumps(span.attributes),
            ),
        )
        self._conn().commit()

    def get(self, span_id: str) -> Optional[Span]:
        row = self._conn().execute(
            "SELECT * FROM trace_spans WHERE span_id=?", (span_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def get_trace(self, trace_id: str) -> list[Span]:
        rows = self._conn().execute(
            "SELECT * FROM trace_spans WHERE trace_id=? ORDER BY start_ts ASC",
            (trace_id,),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def get_for_run(self, run_id: str) -> list[Span]:
        rows = self._conn().execute(
            "SELECT * FROM trace_spans WHERE run_id=? ORDER BY start_ts ASC", (run_id,)
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def count(self, trace_id: str = "") -> int:
        if trace_id:
            return self._conn().execute(
                "SELECT COUNT(*) FROM trace_spans WHERE trace_id=?", (trace_id,)
            ).fetchone()[0]
        return self._conn().execute("SELECT COUNT(*) FROM trace_spans").fetchone()[0]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Span:
        import json
        d = dict(row)
        return Span(
            span_id=d["span_id"], trace_id=d["trace_id"], parent_id=d["parent_id"],
            name=d["name"], kind=SpanKind(d["kind"]), status=SpanStatus(d["status"]),
            start_ts=d["start_ts"], end_ts=d["end_ts"], agent_name=d["agent_name"],
            run_id=d["run_id"], error=d["error"] or "",
            attributes=json.loads(d["attributes"]),
        )


# ── Tracer ────────────────────────────────────────────────────────────────────

class Tracer:
    """Create and finish spans; propagate W3C trace context."""

    def __init__(self, store: TraceStore) -> None:
        self._store = store

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[TraceContext] = None,
        agent_name: str = "",
        run_id: str = "",
        attributes: Optional[dict[str, Any]] = None,
    ) -> tuple[Span, TraceContext]:
        if parent is None:
            ctx = TraceContext.new_root()
        else:
            ctx = TraceContext.child(parent)
        span = Span(
            span_id=ctx.span_id,
            trace_id=ctx.trace_id,
            parent_id=parent.span_id if parent else None,
            name=name,
            kind=kind,
            start_ts=time.time(),
            agent_name=agent_name,
            run_id=run_id,
            attributes=attributes or {},
        )
        self._store.save(span)
        return span, ctx

    def finish_span(
        self,
        span: Span,
        status: SpanStatus = SpanStatus.OK,
        error: str = "",
    ) -> Span:
        span.finish(status=status, error=error)
        self._store.save(span)
        return span

    def get_trace(self, trace_id: str) -> list[Span]:
        return self._store.get_trace(trace_id)
