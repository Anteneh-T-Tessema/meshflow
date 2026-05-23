"""Event-sourcing projections over WorkflowEventBus history.

Each projection is a pure, incremental reducer over WorkflowEvents.
They can be fed events one at a time (``feed``) or in bulk (``feed_all``),
and queried at any point in time.

Usage::

    from meshflow.core.events import WorkflowEventBus
    from meshflow.core.projections import (
        AuditTrailProjection,
        NodeLatencyProjection,
        PolicyViolationProjection,
        WorkflowSummaryProjection,
    )

    bus = WorkflowEventBus()

    # Build after the run
    audit = AuditTrailProjection()
    audit.feed_all(bus.history())
    timeline = audit.timeline(run_id="abc-123")

    # Or attach to a live bus
    summary = WorkflowSummaryProjection()
    async for event in bus.subscribe():
        summary.feed(event)
        if event.kind == EventKind.WORKFLOW_COMPLETE:
            print(summary.query(event.run_id))
            break
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from meshflow.core.events import EventKind, WorkflowEvent


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 2)


# ── 1. AuditTrailProjection ───────────────────────────────────────────────────

@dataclass
class AuditEntry:
    """One event in the audit timeline."""
    event_kind: str
    run_id: str
    node_id: str
    timestamp: float
    data: dict[str, Any]
    elapsed_ms: float  # ms since workflow_start for this run


class AuditTrailProjection:
    """Full ordered timeline of every event, per run_id.

    Useful for compliance reports, post-mortem analysis, and tracing.
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[AuditEntry]] = defaultdict(list)
        self._start_times: dict[str, float] = {}

    def feed(self, event: WorkflowEvent) -> None:
        run_id = event.run_id
        if event.kind == EventKind.WORKFLOW_START:
            self._start_times[run_id] = event.timestamp

        start = self._start_times.get(run_id, event.timestamp)
        entry = AuditEntry(
            event_kind=event.kind.value,
            run_id=run_id,
            node_id=event.node_id,
            timestamp=event.timestamp,
            data=dict(event.data),
            elapsed_ms=_ms(start, event.timestamp),
        )
        self._entries[run_id].append(entry)

    def feed_all(self, events: list[WorkflowEvent]) -> None:
        for e in events:
            self.feed(e)

    def timeline(self, run_id: str) -> list[AuditEntry]:
        """Return the ordered audit trail for *run_id*."""
        return list(self._entries.get(run_id, []))

    def all_run_ids(self) -> list[str]:
        return list(self._entries.keys())

    def to_dict(self, run_id: str) -> list[dict[str, Any]]:
        return [
            {
                "event": e.event_kind,
                "node_id": e.node_id,
                "elapsed_ms": e.elapsed_ms,
                "timestamp": e.timestamp,
                "data": e.data,
            }
            for e in self.timeline(run_id)
        ]


# ── 2. NodeLatencyProjection ──────────────────────────────────────────────────

@dataclass
class NodeLatencyStats:
    node_id: str
    call_count: int
    total_ms: float
    min_ms: float
    max_ms: float

    @property
    def avg_ms(self) -> float:
        return round(self.total_ms / self.call_count, 2) if self.call_count else 0.0


class NodeLatencyProjection:
    """Per-node execution latency aggregated across all runs.

    Tracks start/complete pairs. Useful for identifying slow nodes and
    capacity planning.
    """

    def __init__(self) -> None:
        self._starts: dict[tuple[str, str], float] = {}  # (run_id, node_id) → ts
        self._stats: dict[str, NodeLatencyStats] = {}

    def feed(self, event: WorkflowEvent) -> None:
        key = (event.run_id, event.node_id)
        if event.kind == EventKind.STEP_START and event.node_id:
            self._starts[key] = event.timestamp
        elif event.kind == EventKind.STEP_COMPLETE and event.node_id:
            start_ts = self._starts.pop(key, None)
            if start_ts is None:
                return
            duration = _ms(start_ts, event.timestamp)
            nid = event.node_id
            if nid not in self._stats:
                self._stats[nid] = NodeLatencyStats(
                    node_id=nid, call_count=0, total_ms=0.0,
                    min_ms=float("inf"), max_ms=float("-inf"),
                )
            s = self._stats[nid]
            s.call_count += 1
            s.total_ms += duration
            s.min_ms = min(s.min_ms, duration)
            s.max_ms = max(s.max_ms, duration)

    def feed_all(self, events: list[WorkflowEvent]) -> None:
        for e in events:
            self.feed(e)

    def query(self, node_id: str | None = None) -> list[NodeLatencyStats]:
        if node_id:
            s = self._stats.get(node_id)
            return [s] if s else []
        return sorted(self._stats.values(), key=lambda s: -s.avg_ms)

    def slowest(self, n: int = 5) -> list[NodeLatencyStats]:
        return self.query()[:n]


# ── 3. PolicyViolationProjection ──────────────────────────────────────────────

@dataclass
class ViolationRecord:
    run_id: str
    node_id: str
    kind: str  # "blocked" | "paused" | "hitl_required"
    timestamp: float
    data: dict[str, Any]


class PolicyViolationProjection:
    """Collects all policy-triggered events (blocked / paused / HITL).

    Useful for compliance dashboards and SLA monitoring.
    """

    def __init__(self) -> None:
        self._violations: list[ViolationRecord] = []

    _VIOLATION_KINDS = {
        EventKind.STEP_BLOCKED: "blocked",
        EventKind.STEP_PAUSED: "paused",
        EventKind.HITL_REQUIRED: "hitl_required",
    }

    def feed(self, event: WorkflowEvent) -> None:
        if event.kind in self._VIOLATION_KINDS:
            self._violations.append(ViolationRecord(
                run_id=event.run_id,
                node_id=event.node_id,
                kind=self._VIOLATION_KINDS[event.kind],
                timestamp=event.timestamp,
                data=dict(event.data),
            ))

    def feed_all(self, events: list[WorkflowEvent]) -> None:
        for e in events:
            self.feed(e)

    def query(
        self,
        run_id: str | None = None,
        kind: str | None = None,
    ) -> list[ViolationRecord]:
        results = self._violations
        if run_id:
            results = [v for v in results if v.run_id == run_id]
        if kind:
            results = [v for v in results if v.kind == kind]
        return results

    def violation_count(self, run_id: str | None = None) -> int:
        return len(self.query(run_id=run_id))

    def blocked_nodes(self, run_id: str) -> list[str]:
        return [v.node_id for v in self.query(run_id=run_id, kind="blocked")]


# ── 4. WorkflowSummaryProjection ──────────────────────────────────────────────

@dataclass
class WorkflowSummary:
    run_id: str
    workflow_name: str
    status: str  # "running" | "completed" | "failed"
    started_at: float
    completed_at: float | None
    duration_ms: float | None
    node_count: int
    blocked_count: int
    skipped_count: int
    paused_count: int
    total_tokens: int
    total_cost_usd: float
    carbon_g: float


class WorkflowSummaryProjection:
    """Per-run rollup: status, duration, node counts, cost, carbon.

    Use this as a high-level dashboard view.
    """

    def __init__(self) -> None:
        self._summaries: dict[str, WorkflowSummary] = {}

    def _get(self, run_id: str) -> WorkflowSummary:
        if run_id not in self._summaries:
            self._summaries[run_id] = WorkflowSummary(
                run_id=run_id,
                workflow_name="",
                status="running",
                started_at=time.time(),
                completed_at=None,
                duration_ms=None,
                node_count=0,
                blocked_count=0,
                skipped_count=0,
                paused_count=0,
                total_tokens=0,
                total_cost_usd=0.0,
                carbon_g=0.0,
            )
        return self._summaries[run_id]

    def feed(self, event: WorkflowEvent) -> None:
        s = self._get(event.run_id)

        if event.kind == EventKind.WORKFLOW_START:
            s.started_at = event.timestamp
            s.workflow_name = event.data.get("workflow", "")
            s.status = "running"

        elif event.kind == EventKind.STEP_COMPLETE:
            s.node_count += 1
            s.total_tokens += int(event.data.get("tokens", 0))
            s.total_cost_usd += float(event.data.get("cost_usd", 0.0))
            s.carbon_g += float(event.data.get("carbon_g", 0.0))

        elif event.kind == EventKind.STEP_BLOCKED:
            s.blocked_count += 1

        elif event.kind == EventKind.STEP_SKIPPED:
            s.skipped_count += 1

        elif event.kind == EventKind.STEP_PAUSED:
            s.paused_count += 1

        elif event.kind == EventKind.WORKFLOW_COMPLETE:
            s.status = "completed"
            s.completed_at = event.timestamp
            s.duration_ms = _ms(s.started_at, event.timestamp)

        elif event.kind == EventKind.WORKFLOW_FAILED:
            s.status = "failed"
            s.completed_at = event.timestamp
            s.duration_ms = _ms(s.started_at, event.timestamp)

    def feed_all(self, events: list[WorkflowEvent]) -> None:
        for e in events:
            self.feed(e)

    def query(self, run_id: str) -> WorkflowSummary | None:
        return self._summaries.get(run_id)

    def all(self) -> list[WorkflowSummary]:
        return list(self._summaries.values())

    def by_status(self, status: str) -> list[WorkflowSummary]:
        return [s for s in self._summaries.values() if s.status == status]


# ── 5. EventProjector (multi-projection coordinator) ─────────────────────────

class EventProjector:
    """Feeds all registered projections from a single event stream.

    Usage::

        proj = EventProjector()
        proj.feed_all(bus.history())

        # Access any projection
        proj.audit.timeline(run_id)
        proj.latency.slowest(5)
        proj.violations.violation_count()
        proj.summary.query(run_id)
    """

    def __init__(self) -> None:
        self.audit = AuditTrailProjection()
        self.latency = NodeLatencyProjection()
        self.violations = PolicyViolationProjection()
        self.summary = WorkflowSummaryProjection()
        self._custom: list[Any] = []

    def add_projection(self, projection: Any) -> None:
        """Register a custom projection that implements ``feed(event)``."""
        self._custom.append(projection)

    def feed(self, event: WorkflowEvent) -> None:
        self.audit.feed(event)
        self.latency.feed(event)
        self.violations.feed(event)
        self.summary.feed(event)
        for p in self._custom:
            p.feed(event)

    def feed_all(self, events: list[WorkflowEvent]) -> None:
        for e in events:
            self.feed(e)

    def report(self, run_id: str) -> dict[str, Any]:
        """One-shot summary report for a completed run."""
        s = self.summary.query(run_id)
        return {
            "run_id": run_id,
            "summary": {
                "workflow": s.workflow_name if s else "",
                "status": s.status if s else "unknown",
                "duration_ms": s.duration_ms if s else None,
                "nodes_executed": s.node_count if s else 0,
                "blocked": s.blocked_count if s else 0,
                "skipped": s.skipped_count if s else 0,
                "paused": s.paused_count if s else 0,
                "total_tokens": s.total_tokens if s else 0,
                "total_cost_usd": s.total_cost_usd if s else 0.0,
                "carbon_g": s.carbon_g if s else 0.0,
            },
            "audit_trail": self.audit.to_dict(run_id),
            "policy_violations": [
                {"node": v.node_id, "kind": v.kind, "data": v.data}
                for v in self.violations.query(run_id=run_id)
            ],
            "node_latencies": [
                {"node": s.node_id, "avg_ms": s.avg_ms, "calls": s.call_count}
                for s in self.latency.query()
            ],
        }


__all__ = [
    "AuditTrailProjection",
    "AuditEntry",
    "NodeLatencyProjection",
    "NodeLatencyStats",
    "PolicyViolationProjection",
    "ViolationRecord",
    "WorkflowSummaryProjection",
    "WorkflowSummary",
    "EventProjector",
]
