"""Fine-tuning data export — convert agent traces to JSONL training data.

Reads run records from a :class:`~meshflow.core.ledger.ReplayLedger` (or a
plain list of dicts) and emits JSONL in the format expected by OpenAI,
Anthropic, or a generic messages format.

Usage::

    from meshflow.export import FinetuneExporter, ExportFormat

    exporter = FinetuneExporter(
        ledger_path="meshflow_runs.db",
        format=ExportFormat.openai,
        min_confidence=0.7,
        max_records=5000,
    )
    # Write to file
    exporter.export("training_data.jsonl")

    # Or get records as a list
    records = exporter.collect()

    # From CLI:
    # meshflow export-traces --format openai --output traces.jsonl --min-confidence 0.8
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class ExportFormat(str, Enum):
    """Output format for fine-tuning data."""

    openai     = "openai"      # {messages: [{role, content}]}
    anthropic  = "anthropic"   # {human: ..., assistant: ...}
    generic    = "generic"     # {prompt, completion, metadata}
    sharegpt   = "sharegpt"    # {conversations: [{from, value}]}


@dataclass
class TraceRecord:
    """One training example derived from an agent trace."""

    run_id: str
    agent_name: str
    task: str
    response: str
    system_prompt: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": self.task})
        messages.append({"role": "assistant", "content": self.response})
        return {"messages": messages}

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "system": self.system_prompt,
            "human": self.task,
            "assistant": self.response,
        }

    def to_generic(self) -> dict[str, Any]:
        return {
            "prompt": self.task,
            "completion": self.response,
            "metadata": {
                "run_id": self.run_id,
                "agent_name": self.agent_name,
                "confidence": self.confidence,
                "tokens": self.tokens,
                "timestamp": self.timestamp,
                **self.metadata,
            },
        }

    def to_sharegpt(self) -> dict[str, Any]:
        conversations = []
        if self.system_prompt:
            conversations.append({"from": "system", "value": self.system_prompt})
        conversations.append({"from": "human", "value": self.task})
        conversations.append({"from": "gpt", "value": self.response})
        return {"conversations": conversations}

    def to_format(self, fmt: ExportFormat) -> dict[str, Any]:
        if fmt == ExportFormat.openai:
            return self.to_openai()
        if fmt == ExportFormat.anthropic:
            return self.to_anthropic()
        if fmt == ExportFormat.sharegpt:
            return self.to_sharegpt()
        return self.to_generic()


# ── Filtering ─────────────────────────────────────────────────────────────────

@dataclass
class ExportFilter:
    """Quality and selection filters applied during export."""

    min_confidence: float = 0.0
    max_records: int | None = None
    agent_names: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    since_ts: float | None = None          # Unix timestamp
    until_ts: float | None = None
    exclude_blocked: bool = True
    deduplicate: bool = True               # skip identical (task, response) pairs

    def accepts(self, record: TraceRecord) -> bool:
        if self.exclude_blocked and not record.response:
            return False
        if record.confidence < self.min_confidence:
            return False
        if self.agent_names and record.agent_name not in self.agent_names:
            return False
        if self.run_ids and record.run_id not in self.run_ids:
            return False
        if self.since_ts is not None and record.timestamp < self.since_ts:
            return False
        if self.until_ts is not None and record.timestamp > self.until_ts:
            return False
        return True


# ── FinetuneExporter ──────────────────────────────────────────────────────────

class FinetuneExporter:
    """Export agent traces as fine-tuning JSONL.

    Parameters
    ----------
    ledger_path:    Path to the SQLite ledger file (or ``None`` to supply
                    raw records via :meth:`collect_from`).
    format:         Output format (:class:`ExportFormat`).
    min_confidence: Minimum ``stated_confidence`` to include (0–1). Default 0.
    max_records:    Cap on output records (``None`` = no cap).
    agent_names:    Only export traces from these agent names (empty = all).
    run_ids:        Only export these run IDs (empty = all).
    deduplicate:    Skip records whose (task, response) pair already appeared.
    """

    def __init__(
        self,
        ledger_path: str | None = None,
        *,
        format: ExportFormat = ExportFormat.openai,
        min_confidence: float = 0.0,
        max_records: int | None = None,
        agent_names: list[str] | None = None,
        run_ids: list[str] | None = None,
        deduplicate: bool = True,
        since_ts: float | None = None,
        until_ts: float | None = None,
    ) -> None:
        self.ledger_path = ledger_path
        self.format = format
        self._filter = ExportFilter(
            min_confidence=min_confidence,
            max_records=max_records,
            agent_names=agent_names or [],
            run_ids=run_ids or [],
            deduplicate=deduplicate,
            since_ts=since_ts,
            until_ts=until_ts,
        )
        self._raw_records: list[dict[str, Any]] = []

    # ── Data loading ───────────────────────────────────────────────────────────

    def collect_from(self, records: list[dict[str, Any]]) -> "FinetuneExporter":
        """Manually supply raw step records (bypasses ledger)."""
        self._raw_records = list(records)
        return self

    def _load_from_ledger(self) -> list[dict[str, Any]]:
        """Read step records from the SQLite ledger."""
        if not self.ledger_path:
            return []
        try:
            import sqlite3
            conn = sqlite3.connect(self.ledger_path, timeout=10)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM runs ORDER BY ts ASC").fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ── Record conversion ──────────────────────────────────────────────────────

    def _to_trace(self, raw: dict[str, Any]) -> TraceRecord | None:
        """Convert a raw ledger row or step dict to a :class:`TraceRecord`."""
        # Support both ReplayLedger rows and plain agent result dicts
        task = raw.get("task") or raw.get("input") or raw.get("content", "")
        response = (
            raw.get("output")
            or raw.get("result")
            or raw.get("response")
            or raw.get("content", "")
        )
        if not task or not response:
            return None

        return TraceRecord(
            run_id=str(raw.get("run_id") or raw.get("id", "")),
            agent_name=str(raw.get("agent_id") or raw.get("agent_name") or raw.get("node_id", "unknown")),
            task=str(task),
            response=str(response),
            system_prompt=str(raw.get("system_prompt", "")),
            tokens=int(raw.get("tokens", 0)),
            cost_usd=float(raw.get("cost_usd", 0.0)),
            confidence=float(raw.get("stated_confidence", raw.get("confidence", 1.0))),
            timestamp=float(raw.get("ts", raw.get("timestamp", time.time()))),
            metadata={k: v for k, v in raw.items()
                      if k not in ("task", "input", "output", "result", "response",
                                   "content", "run_id", "agent_id", "agent_name",
                                   "system_prompt", "tokens", "cost_usd",
                                   "stated_confidence", "confidence", "ts", "timestamp")},
        )

    # ── Collection + filtering ─────────────────────────────────────────────────

    def collect(self) -> list[TraceRecord]:
        """Return all passing :class:`TraceRecord` objects."""
        raw_list = self._raw_records or self._load_from_ledger()
        seen: set[tuple[str, str]] = set()
        results: list[TraceRecord] = []

        for raw in raw_list:
            if self._filter.max_records and len(results) >= self._filter.max_records:
                break
            record = self._to_trace(raw)
            if record is None:
                continue
            if not self._filter.accepts(record):
                continue
            if self._filter.deduplicate:
                key = (record.task, record.response)
                if key in seen:
                    continue
                seen.add(key)
            results.append(record)

        return results

    # ── Export ─────────────────────────────────────────────────────────────────

    def iter_jsonl(self) -> Iterator[str]:
        """Yield JSONL lines (one per training example)."""
        for record in self.collect():
            yield json.dumps(record.to_format(self.format), ensure_ascii=False)

    def export(self, path: str) -> int:
        """Write JSONL to *path*. Returns the number of records written."""
        count = 0
        with open(path, "w", encoding="utf-8") as f:
            for line in self.iter_jsonl():
                f.write(line + "\n")
                count += 1
        return count

    def export_str(self) -> str:
        """Return the full JSONL as a string (useful for testing)."""
        return "\n".join(self.iter_jsonl())

    def stats(self) -> dict[str, Any]:
        records = self.collect()
        if not records:
            return {"count": 0}
        total_tokens = sum(r.tokens for r in records)
        avg_confidence = sum(r.confidence for r in records) / len(records)
        agents = list({r.agent_name for r in records})
        return {
            "count": len(records),
            "total_tokens": total_tokens,
            "avg_confidence": round(avg_confidence, 4),
            "agents": sorted(agents),
            "format": self.format.value,
        }
