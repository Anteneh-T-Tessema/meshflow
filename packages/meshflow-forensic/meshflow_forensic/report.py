"""ForensicReport + IncidentTimeline — structured evidence export."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshflow_forensic.gate import DascGate


@dataclass
class IncidentEvent:
    timestamp: str
    agent_id: str
    action: str
    verdict: str
    effective_tier: int
    tainted: bool = False
    reason: str = ""


@dataclass
class IncidentTimeline:
    """Reconstructs the agent action sequence from the audit ledger."""
    run_id: str
    events: list[IncidentEvent] = field(default_factory=list)
    chain_valid: bool = True

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def summary(self) -> str:
        lines = [f"Run: {self.run_id}  |  {len(self.events)} events  |  chain={'✓' if self.chain_valid else '✗'}"]
        for ev in self.events:
            taint = " [TAINTED]" if ev.tainted else ""
            lines.append(f"  {ev.timestamp[:19]}  {ev.agent_id:20s} {ev.action[:30]:30s} → {ev.verdict}{taint}")
        return "\n".join(lines)


@dataclass
class ForensicReport:
    """Complete forensic evidence package for one agent run."""
    run_id: str
    generated_at: str
    meshflow_forensic_version: str
    chain_valid: bool
    total_entries: int
    verdict_counts: dict[str, int]
    tainted_agents: list[str]
    timeline: IncidentTimeline
    raw_entries: list[dict[str, Any]]

    @classmethod
    def from_gate(cls, gate: "DascGate") -> "ForensicReport":
        """Build a ForensicReport from a completed DascGate run."""
        from meshflow_forensic import __version__

        entries = gate._ledger.all_entries()
        chain_valid = gate._ledger.verify_chain()

        verdict_counts: dict[str, int] = {}
        tainted: list[str] = []
        events: list[IncidentEvent] = []

        for e in entries:
            v = str(e.get("verdict", ""))
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

        for e in entries:
            is_tainted = "tainted=true" in (e.get("reason") or "")
            if is_tainted and e.get("agent_id") not in tainted:
                tainted.append(e["agent_id"])
            events.append(IncidentEvent(
                timestamp=str(e.get("timestamp", "")),
                agent_id=str(e.get("agent_id", "")),
                action=str(e.get("action", "")),
                verdict=str(e.get("verdict", "")),
                effective_tier=int(e.get("effective_tier", 1)),
                tainted=is_tainted,
                reason=str(e.get("reason", "")),
            ))

        timeline = IncidentTimeline(
            run_id=gate.run_id,
            events=events,
            chain_valid=chain_valid,
        )

        return cls(
            run_id=gate.run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            meshflow_forensic_version=__version__,
            chain_valid=chain_valid,
            total_entries=len(entries),
            verdict_counts=verdict_counts,
            tainted_agents=tainted,
            timeline=timeline,
            raw_entries=entries,
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def to_html(self) -> str:
        rows = "".join(
            f"<tr><td>{e.timestamp[:19]}</td><td>{e.agent_id}</td>"
            f"<td>{e.action}</td><td>{e.verdict}</td>"
            f"<td>{'⚠ tainted' if e.tainted else '—'}</td></tr>"
            for e in self.timeline.events
        )
        chain_badge = "✓ valid" if self.chain_valid else "✗ BROKEN"
        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Forensic Report — {self.run_id}</title>
<style>
  body{{font-family:monospace;background:#0d0d0d;color:#c9d1d9;padding:2rem}}
  h1{{color:#58a6ff}}table{{border-collapse:collapse;width:100%}}
  th,td{{border:1px solid #30363d;padding:.4rem .8rem;text-align:left}}
  th{{background:#161b22;color:#8b949e}}
  .COMMIT{{color:#3fb950}}.REJECT{{color:#f85149}}.ESCALATE{{color:#d29922}}
</style>
</head>
<body>
<h1>Forensic Report — {self.run_id}</h1>
<p>Generated: {self.generated_at} &nbsp;|&nbsp; Chain: <strong>{chain_badge}</strong>
&nbsp;|&nbsp; Entries: {self.total_entries}</p>
<table>
<thead><tr><th>Timestamp</th><th>Agent</th><th>Action</th><th>Verdict</th><th>Taint</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""

    def passed(self) -> bool:
        """True when chain is valid and no REJECT verdicts."""
        return self.chain_valid and self.verdict_counts.get("REJECT", 0) == 0
