"""Structured trace viewer — LangSmith-compatible JSON export + rich terminal display.

Closes the observability gap: transforms the MeshFlow replay ledger into a
structured trace document that:
  1. Exports as LangSmith-compatible JSON (importable into LangSmith / LangFuse).
  2. Renders as a rich terminal tree (no external deps required).

Usage::

    from meshflow.observability.trace_viewer import TraceViewer

    viewer = TraceViewer(ledger_db="meshflow_runs.db")

    # Terminal display
    await viewer.display("run-abc123")

    # LangSmith-format JSON export
    trace = await viewer.export_langsmith("run-abc123")
    with open("trace.json", "w") as f:
        import json; json.dump(trace, f, indent=2)

    # Inline dict for programmatic use
    trace_dict = await viewer.to_dict("run-abc123")

CLI::

    meshflow trace <run_id>               # terminal display
    meshflow trace <run_id> --format json # LangSmith JSON to stdout
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


# ── LangSmith trace schema ────────────────────────────────────────────────────

def _langsmith_run(
    run_id: str,
    name: str,
    run_type: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    start_time: str,
    end_time: str,
    extra: dict[str, Any] | None = None,
    error: str | None = None,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": run_id,
        "name": name,
        "run_type": run_type,   # "chain" | "llm" | "tool" | "retriever"
        "inputs": inputs,
        "outputs": outputs,
        "start_time": start_time,
        "end_time": end_time,
        "extra": extra or {},
        "serialized": {},
        "events": [],
        "tags": [],
    }
    if error:
        obj["error"] = error
    if parent_run_id:
        obj["parent_run_id"] = parent_run_id
    return obj


# ── TraceViewer ────────────────────────────────────────────────────────────────

class TraceViewer:
    """Builds structured traces from the MeshFlow replay ledger.

    Parameters
    ----------
    ledger_db: Path to the SQLite ledger used during the run.
    """

    def __init__(self, ledger_db: str = "meshflow_runs.db") -> None:
        self._db = ledger_db

    async def _load(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        from meshflow.core.ledger import ReplayLedger
        ledger = ReplayLedger(self._db)
        summary = await ledger.run_summary(run_id) or {}
        steps = await ledger.get_run(run_id)
        return summary, steps

    # ── Terminal display ──────────────────────────────────────────────────────

    async def display(self, run_id: str, *, width: int = 80) -> None:
        """Print a rich terminal trace tree for *run_id*."""
        summary, steps = await self._load(run_id)
        if not steps:
            print(f"[trace] No steps found for run_id={run_id!r}")
            return

        bar = "─" * width
        total_cost = sum(s.get("cost_usd", 0) for s in steps)
        total_tok = sum(s.get("tokens_used", 0) for s in steps)
        total_ms = sum(s.get("duration_ms", 0) for s in steps)

        print()
        print(f"  {bar}")
        print(f"  Trace: {run_id[:70]}")
        print(f"  {bar}")
        print(f"  Steps: {len(steps)}   "
              f"Cost: ${total_cost:.5f}   "
              f"Tokens: {total_tok:,}   "
              f"Duration: {total_ms/1000:.2f}s")
        print(f"  {bar}")
        print()

        for i, step in enumerate(steps):
            node = step.get("node_id", "?")
            kind = step.get("node_kind", "?")
            blocked = step.get("blocked", False)
            uncertain = step.get("uncertainty", 0.0)
            cost = step.get("cost_usd", 0.0)
            dur = step.get("duration_ms", 0.0)
            out = (step.get("output", "") or "")[:100].replace("\n", " ")
            reason = (step.get("block_reason", "") or "")

            icon = "X" if blocked else "v"
            prefix = "L-- " if i == len(steps) - 1 else "|-- "
            status = f"BLOCKED({reason[:30]})" if blocked else "OK"

            print(f"  {prefix}[{i+1:02d}] {icon}  {node:<22} {kind:<10} "
                  f"conf={1-uncertain:.2f}  {dur:.0f}ms  ${cost:.5f}  {status}")
            if out:
                print(f"  {'    ' if i == len(steps)-1 else '|   '}    > {out}")
            if i < len(steps) - 1:
                print("  |")

        print()

    # ── Dict export ───────────────────────────────────────────────────────────

    async def to_dict(self, run_id: str) -> dict[str, Any]:
        """Return a structured trace dict keyed by step_id."""
        summary, steps = await self._load(run_id)
        return {
            "run_id": run_id,
            "summary": summary,
            "steps": steps,
        }

    # ── LangSmith-format export ───────────────────────────────────────────────

    async def export_langsmith(self, run_id: str) -> list[dict[str, Any]]:
        """Export the trace as a list of LangSmith-compatible run objects.

        Each MeshFlow step maps to one LangSmith ``chain`` run, with the
        workflow itself as the root chain.  Import the resulting list into
        LangSmith with the REST API or via langsmith-sdk ``create_run()``.
        """
        summary, steps = await self._load(run_id)

        if not steps:
            return []

        # Root chain = the workflow
        first_ts = steps[0].get("timestamp", "") if steps else ""
        last_ts = steps[-1].get("timestamp", "") if steps else first_ts
        total_cost = sum(s.get("cost_usd", 0) for s in steps)
        total_tok = sum(s.get("tokens_used", 0) for s in steps)

        root = _langsmith_run(
            run_id=run_id,
            name=summary.get("workflow_name", "MeshFlow Run"),
            run_type="chain",
            inputs={"task": summary.get("task", "")},
            outputs={"output": summary.get("final_output", "")},
            start_time=first_ts,
            end_time=last_ts,
            extra={
                "total_cost_usd": total_cost,
                "total_tokens": total_tok,
                "framework": "meshflow",
            },
        )

        runs = [root]

        # One child run per step
        for step in steps:
            step_run = _langsmith_run(
                run_id=f"{run_id}/{step.get('step_id', 'unknown')}",
                name=step.get("node_id", "step"),
                run_type=_kind_to_langsmith(step.get("node_kind", "native")),
                inputs={"task": step.get("input_task", "")},
                outputs={
                    "output": step.get("output", ""),
                    "blocked": step.get("blocked", False),
                    "block_reason": step.get("block_reason", ""),
                },
                start_time=step.get("timestamp", ""),
                end_time=step.get("timestamp", ""),
                extra={
                    "cost_usd":      step.get("cost_usd", 0.0),
                    "tokens_used":   step.get("tokens_used", 0),
                    "duration_ms":   step.get("duration_ms", 0.0),
                    "uncertainty":   step.get("uncertainty", 0.0),
                    "verdict":       step.get("verdict", ""),
                    "node_kind":     step.get("node_kind", ""),
                },
                error=step.get("block_reason", "") if step.get("blocked") else None,
                parent_run_id=run_id,
            )
            runs.append(step_run)

        return runs

    async def export_langsmith_json(self, run_id: str, indent: int = 2) -> str:
        """Serialize the LangSmith export as a JSON string."""
        runs = await self.export_langsmith(run_id)
        return json.dumps(runs, indent=indent, default=str)


def _kind_to_langsmith(kind: str) -> str:
    mapping = {
        "native":    "chain",
        "langgraph": "chain",
        "crewai":    "chain",
        "autogen":   "chain",
        "mcp":       "tool",
        "human":     "chain",
        "http":      "tool",
        "python":    "tool",
        "subgraph":  "chain",
    }
    return mapping.get(kind.lower(), "chain")


__all__ = ["TraceViewer"]
