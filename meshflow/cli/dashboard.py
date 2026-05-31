"""Terminal cost dashboard — lightweight TUI using only stdlib + rich.

Renders a live-updating terminal view of:
  - Recent run history (cost / tokens / status)
  - Agent health scores
  - Pending HITL approvals
  - Cost trend (sparkline)
  - Top costly nodes

No Streamlit or extra deps required; rich is already in MeshFlow's requirements.

Usage::

    # One-shot snapshot
    meshflow dashboard

    # Auto-refresh every 5 seconds
    meshflow dashboard --refresh 5

    # Programmatic
    from meshflow.cli.dashboard import TerminalDashboard
    dashboard = TerminalDashboard(ledger_db="meshflow_runs.db")
    await dashboard.render()          # single render
    await dashboard.watch(interval=5) # live refresh loop
"""

from __future__ import annotations

import asyncio
from typing import Any


# ── Sparkline helper ──────────────────────────────────────────────────────────

_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int = 20) -> str:
    """Render a mini ASCII sparkline from a list of floats."""
    if not values:
        return " " * width
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    # Resample to `width` points
    step = max(1, len(values) // width)
    sampled = [sum(values[i:i+step]) / len(values[i:i+step])
               for i in range(0, len(values), step)][:width]
    return "".join(_SPARK_CHARS[int((v - lo) / span * (len(_SPARK_CHARS) - 1))]
                   for v in sampled)


# ── TerminalDashboard ─────────────────────────────────────────────────────────

class TerminalDashboard:
    """Render a cost/metrics terminal dashboard without Streamlit.

    Parameters
    ----------
    ledger_db:   Path to the MeshFlow SQLite ledger.
    limit:       Number of recent runs to display.
    health_db:   Optional separate health SQLite path (defaults to ledger_db).
    """

    def __init__(
        self,
        ledger_db: str = "meshflow_runs.db",
        limit: int = 20,
    ) -> None:
        self._db = ledger_db
        self._limit = limit

    async def _load_data(self) -> dict[str, Any]:
        """Load all dashboard data from the ledger."""
        from meshflow.core.ledger import ReplayLedger
        from meshflow.agents.health import get_health_tracker

        data: dict[str, Any] = {
            "runs": [],
            "paused": [],
            "health": [],
            "error": "",
        }

        try:
            ledger = ReplayLedger(self._db)
            runs = await ledger.list_runs(limit=self._limit)
            data["runs"] = runs or []
            data["paused"] = await ledger.list_paused_runs() or []
        except Exception as exc:
            data["error"] = str(exc)

        try:
            tracker = get_health_tracker()
            data["health"] = [s.to_dict() for s in tracker.all_summaries()]
        except Exception:
            pass

        return data

    async def render(self, *, clear: bool = False) -> None:
        """Print a full dashboard snapshot to stdout."""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel
            from rich.columns import Columns
            from rich import box
            _has_rich = True
        except ImportError:
            _has_rich = False

        data = await self._load_data()

        if _has_rich:
            await self._render_rich(data, clear=clear)
        else:
            self._render_plain(data)

    async def _render_rich(self, data: dict[str, Any], *, clear: bool) -> None:
        from rich.console import Console
        from rich.table import Table
        from rich import box

        console = Console()
        if clear:
            console.clear()

        runs = data.get("runs", [])
        paused = data.get("paused", [])
        health = data.get("health", [])
        error = data.get("error", "")

        if error:
            console.print(f"[red]Ledger error: {error}[/red]")
            return

        # ── Header ─────────────────────────────────────────────────────────────
        total_cost = sum(r.get("total_cost_usd", 0) for r in runs)
        total_tok  = sum(r.get("total_tokens", 0) for r in runs)
        completed  = sum(1 for r in runs if not r.get("blocked_nodes"))
        costs      = [r.get("total_cost_usd", 0) for r in runs]
        spark      = _sparkline(costs[::-1])  # oldest → newest

        console.print()
        console.rule("[bold cyan]MeshFlow Dashboard[/bold cyan]")
        console.print(
            f"  [bold]Runs:[/bold] {len(runs)}  "
            f"[bold]Completed:[/bold] {completed}  "
            f"[bold]Paused:[/bold] {len(paused)}  "
            f"[bold]Total cost:[/bold] ${total_cost:.4f}  "
            f"[bold]Total tokens:[/bold] {total_tok:,}"
        )
        console.print(f"  [dim]Cost trend (newest→) {spark}[/dim]")
        console.print()

        # ── Recent runs table ─────────────────────────────────────────────────
        table = Table(title="Recent Runs", box=box.SIMPLE_HEAD, expand=True)
        table.add_column("Run ID",      style="cyan",  max_width=18)
        table.add_column("Workflow",    max_width=22)
        table.add_column("Cost USD",    justify="right", style="yellow")
        table.add_column("Tokens",      justify="right")
        table.add_column("Duration",    justify="right")
        table.add_column("Status",      max_width=10)

        for r in runs[:self._limit]:
            run_id  = (r.get("run_id") or "")[:16]
            wf_name = (r.get("workflow_name") or r.get("name") or "")[:20]
            cost    = r.get("total_cost_usd", 0)
            tokens  = r.get("total_tokens", 0)
            dur     = r.get("duration_s", 0)
            blocked = r.get("blocked_nodes", [])
            paused_ids = {p.get("run_id") for p in paused}
            if run_id in paused_ids:
                status, color = "PAUSED", "yellow"
            elif blocked:
                status, color = "BLOCKED", "red"
            else:
                status, color = "OK", "green"

            table.add_row(
                run_id, wf_name,
                f"${cost:.5f}",
                f"{tokens:,}",
                f"{dur:.1f}s",
                f"[{color}]{status}[/{color}]",
            )
        console.print(table)

        # ── Pending HITL ──────────────────────────────────────────────────────
        if paused:
            hitl_table = Table(title="Pending Approvals", box=box.SIMPLE_HEAD)
            hitl_table.add_column("Run ID",   style="yellow", max_width=20)
            hitl_table.add_column("Node",     max_width=20)
            hitl_table.add_column("Paused at")
            for p in paused:
                hitl_table.add_row(
                    (p.get("run_id") or "")[:18],
                    (p.get("paused_at_node") or "")[:18],
                    p.get("paused_at", "")[:19],
                )
            console.print(hitl_table)

        # ── Model health ──────────────────────────────────────────────────────
        if health:
            h_table = Table(title="Model Health", box=box.SIMPLE_HEAD)
            h_table.add_column("Model",     max_width=32)
            h_table.add_column("Score",     justify="right")
            h_table.add_column("p50 ms",    justify="right")
            h_table.add_column("p95 ms",    justify="right")
            h_table.add_column("Failures",  justify="right")
            for h in health:
                score = h["health_score"]
                color = "green" if score >= 0.9 else ("yellow" if score >= 0.7 else "red")
                h_table.add_row(
                    h["model"][:30],
                    f"[{color}]{score:.2f}[/{color}]",
                    f"{h['p50_latency_ms']:.0f}",
                    f"{h['p95_latency_ms']:.0f}",
                    str(h["failure_count"]),
                )
            console.print(h_table)

        console.rule()

    def _render_plain(self, data: dict[str, Any]) -> None:
        """Fallback plain-text render when rich is not installed."""
        runs   = data.get("runs", [])
        paused = data.get("paused", [])
        error  = data.get("error", "")

        if error:
            print(f"[ERROR] {error}")
            return

        print("\n=== MeshFlow Dashboard ===")
        total_cost = sum(r.get("total_cost_usd", 0) for r in runs)
        print(f"Runs: {len(runs)}  Paused: {len(paused)}  Total cost: ${total_cost:.4f}")
        print()
        print(f"{'Run ID':<18} {'Cost USD':>10} {'Tokens':>8} {'Status'}")
        print("-" * 55)
        paused_ids = {p.get("run_id") for p in paused}
        for r in runs[:self._limit]:
            run_id = (r.get("run_id") or "")[:16]
            cost   = r.get("total_cost_usd", 0)
            tokens = r.get("total_tokens", 0)
            blocked = r.get("blocked_nodes", [])
            status = "PAUSED" if run_id in paused_ids else ("BLOCKED" if blocked else "OK")
            print(f"{run_id:<18} ${cost:>9.5f} {tokens:>8,} {status}")

    async def watch(self, interval: float = 5.0) -> None:
        """Refresh the dashboard every *interval* seconds.  Press Ctrl-C to stop."""
        try:
            while True:
                await self.render(clear=True)
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            print("\n[dashboard] stopped.")


__all__ = ["TerminalDashboard"]
