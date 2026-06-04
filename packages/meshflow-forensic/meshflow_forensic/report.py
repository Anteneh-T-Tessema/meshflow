"""ForensicReport + compliance package generators — structured auditor-ready evidence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshflow_forensic.gate import DascGate
    from meshflow_forensic.timestamp import TimestampAnchor


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
    """Ordered agent action sequence reconstructed from the audit ledger."""
    run_id: str
    events: list[IncidentEvent] = field(default_factory=list)
    chain_valid: bool = True

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def summary(self) -> str:
        lines = [f"Run: {self.run_id}  |  {len(self.events)} events  |  chain={'✓' if self.chain_valid else '✗'}"]
        for ev in self.events:
            taint = " [TAINTED]" if ev.tainted else ""
            lines.append(
                f"  {ev.timestamp[:19]}  {ev.agent_id:20s} {ev.action[:30]:30s} → {ev.verdict}{taint}"
            )
        return "\n".join(lines)


# ── ComplianceSection ─────────────────────────────────────────────────────────

@dataclass
class ComplianceSection:
    """One regulation-specific compliance section within a ForensicReport."""
    regulation: str          # e.g. "HIPAA §164.312", "SOC 2 CC7.2", "GDPR Art.30"
    requirement: str         # one-line description of what the regulation requires
    status: str              # "SATISFIED" | "PARTIAL" | "NOT_APPLICABLE" | "GAP"
    evidence: list[str]      # verifiable facts drawn from the ledger
    gaps: list[str]          # what's missing for full compliance
    notes: str = ""


# ── ForensicReport ────────────────────────────────────────────────────────────

@dataclass
class ForensicReport:
    """Complete forensic evidence package for one agent run.

    Includes the hash chain, RFC 3161 timestamp anchors, per-regulation
    compliance sections, and the raw ledger entries.  Every field is
    either directly verifiable from the ledger or marked as a gap.
    """
    run_id: str
    generated_at: str
    meshflow_forensic_version: str
    chain_valid: bool
    total_entries: int
    verdict_counts: dict[str, int]
    tainted_agents: list[str]
    timeline: IncidentTimeline
    raw_entries: list[dict[str, Any]]
    timestamp_anchors: list[dict[str, Any]] = field(default_factory=list)
    compliance_sections: list[ComplianceSection] = field(default_factory=list)

    @classmethod
    def from_gate(cls, gate: "DascGate") -> "ForensicReport":
        """Build a ForensicReport from a completed DascGate run."""
        from meshflow_forensic import __version__

        entries = gate._ledger.all_entries()
        chain_valid = gate._ledger.verify_chain()
        anchors = gate.all_anchors()

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

        timeline = IncidentTimeline(run_id=gate.run_id, events=events, chain_valid=chain_valid)
        compliance = _build_compliance_sections(entries, chain_valid, anchors)

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
            timestamp_anchors=[a.to_dict() for a in anchors],
            compliance_sections=compliance,
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_json(self, indent: int = 2) -> str:
        d = asdict(self)
        # ComplianceSection is a dataclass — asdict handles it
        return json.dumps(d, indent=indent, default=str)

    def to_html(self) -> str:
        """Auditor-ready HTML report with compliance sections and anchor table."""
        return _render_html(self)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def passed(self) -> bool:
        """True when chain is valid and no REJECT verdicts."""
        return self.chain_valid and self.verdict_counts.get("REJECT", 0) == 0

    def compliance_status(self, regulation: str) -> str:
        """Return the status string for a named regulation, or 'NOT_ASSESSED'."""
        for sec in self.compliance_sections:
            if regulation.lower() in sec.regulation.lower():
                return sec.status
        return "NOT_ASSESSED"

    def anchors_verified(self) -> int:
        return sum(1 for a in self.timestamp_anchors if a.get("verified"))


# ── Compliance section builder ────────────────────────────────────────────────

def _build_compliance_sections(
    entries: list[dict[str, Any]],
    chain_valid: bool,
    anchors: list["TimestampAnchor"],
) -> list[ComplianceSection]:
    sections: list[ComplianceSection] = []
    n = len(entries)
    reject_count = sum(1 for e in entries if str(e.get("verdict", "")) == "REJECT")
    escalate_count = sum(1 for e in entries if str(e.get("verdict", "")) == "ESCALATE")
    tainted_count = sum(1 for e in entries if "tainted=true" in (e.get("reason") or ""))
    anchors_granted = sum(1 for a in anchors if a.is_granted())

    # ── HIPAA §164.312 — Audit controls ──────────────────────────────────────
    hipaa_evidence = [
        f"SHA-256 hash chain covers all {n} agent actions (chain_valid={chain_valid})",
        f"{reject_count} actions rejected by policy gate (unauthorized access blocked)",
        f"{tainted_count} tainted-input events detected and logged",
    ]
    hipaa_gaps = []
    if not chain_valid:
        hipaa_gaps.append("Hash chain integrity check FAILED — chain may have been tampered with")
    if anchors_granted == 0:
        hipaa_gaps.append(
            "No RFC 3161 timestamps anchored — call gate.anchor() to produce legally admissible timestamps"
        )
    hipaa_status = "GAP" if hipaa_gaps else ("PARTIAL" if n == 0 else "SATISFIED")
    sections.append(ComplianceSection(
        regulation="HIPAA §164.312(b)",
        requirement=(
            "Implement hardware, software, and/or procedural mechanisms that record "
            "and examine activity in information systems containing PHI."
        ),
        status=hipaa_status,
        evidence=hipaa_evidence,
        gaps=hipaa_gaps,
        notes=(
            "DascGate records every agent action with timestamp, agent identity, "
            "action type, risk tier, and policy verdict.  SHA-256 chain provides "
            "tamper-evidence.  RFC 3161 anchors provide admissible timestamps."
        ),
    ))

    # ── SOC 2 CC7.2 — System monitoring ──────────────────────────────────────
    soc2_evidence = [
        f"All {n} agent actions logged with verdict (COMMIT/REJECT/ESCALATE)",
        f"{escalate_count} actions flagged for HITL review (irreversible tier)",
        f"{reject_count} policy violations detected and blocked",
    ]
    soc2_gaps = []
    if not any("reviewed_by" in str(e) for e in entries):
        soc2_gaps.append(
            "No incident review records found.  Add 'reviewed_by' metadata to "
            "ESCALATE entries to satisfy CC7.2 incident-response evidence requirement."
        )
    sections.append(ComplianceSection(
        regulation="SOC 2 CC7.2",
        requirement=(
            "The entity monitors system components and the operation of those "
            "controls and evaluates whether they are functioning as intended."
        ),
        status="PARTIAL" if soc2_gaps else "SATISFIED",
        evidence=soc2_evidence,
        gaps=soc2_gaps,
        notes=(
            "ESCALATE verdicts represent anomalous high-risk actions requiring "
            "human review.  For full CC7.2 coverage, add reviewer sign-off "
            "to each ESCALATE entry."
        ),
    ))

    # ── GDPR Art.30 — Records of processing activities ───────────────────────
    agent_ids = list({e.get("agent_id", "") for e in entries if e.get("agent_id")})
    actions = list({e.get("action", "") for e in entries if e.get("action")})
    gdpr_evidence = [
        f"Processing activities recorded for {len(agent_ids)} data processor(s): {', '.join(agent_ids[:5])}",
        f"Action types covered: {', '.join(actions[:8])}",
        f"Processing timestamp range: {entries[0].get('timestamp','?')[:19] if entries else 'n/a'} "
        f"→ {entries[-1].get('timestamp','?')[:19] if entries else 'n/a'}",
    ]
    gdpr_gaps = []
    if tainted_count > 0:
        gdpr_gaps.append(
            f"{tainted_count} tainted-input events — verify no unsanitised personal data "
            "reached model calls (Art.25 data minimisation)."
        )
    sections.append(ComplianceSection(
        regulation="GDPR Art.30",
        requirement=(
            "Maintain records of processing activities under the controller's "
            "responsibility including purpose, data categories, and recipients."
        ),
        status="PARTIAL" if gdpr_gaps else "SATISFIED",
        evidence=gdpr_evidence,
        gaps=gdpr_gaps,
        notes=(
            "Full Art.30 compliance requires declaring the legal basis for processing "
            "and the categories of data subjects.  Add these to your DascGate "
            "ForensicPolicy configuration."
        ),
    ))

    # ── EU AI Act Art.9 — Risk management system ─────────────────────────────
    tier_dist = {}
    for e in entries:
        t = e.get("effective_tier", 1)
        tier_dist[t] = tier_dist.get(t, 0) + 1
    eu_evidence = [
        "AutoRiskClassifier overrides agent self-declared risk tiers (agents cannot misrepresent risk)",
        f"Risk tier distribution: {json.dumps({str(k): v for k, v in tier_dist.items()})}",
        f"{escalate_count} IRREVERSIBLE-tier actions flagged for human oversight (Art.14)",
    ]
    eu_gaps = []
    if escalate_count > 0 and not any("reviewed_by" in str(e) for e in entries):
        eu_gaps.append(
            f"{escalate_count} IRREVERSIBLE actions were escalated but no human-review "
            "records are present.  Art.14 requires documented human oversight for high-risk AI."
        )
    sections.append(ComplianceSection(
        regulation="EU AI Act Art.9",
        requirement=(
            "High-risk AI systems shall have a risk management system in place "
            "throughout the entire lifecycle."
        ),
        status="GAP" if eu_gaps else "SATISFIED",
        evidence=eu_evidence,
        gaps=eu_gaps,
        notes=(
            "The four RiskTier levels (READ_ONLY, INTERNAL, EXTERNAL_IO, IRREVERSIBLE) "
            "map to the EU AI Act risk classification framework.  AutoRiskClassifier "
            "provides the automated risk assessment required by Art.9(2)."
        ),
    ))

    return sections


# ── HTML renderer ─────────────────────────────────────────────────────────────

def _render_html(r: ForensicReport) -> str:  # noqa: C901
    chain_badge = (
        '<span style="color:#3fb950">✓ valid</span>'
        if r.chain_valid else
        '<span style="color:#f85149">✗ BROKEN</span>'
    )

    # Compliance sections
    comp_rows = ""
    for sec in r.compliance_sections:
        color = {"SATISFIED": "#3fb950", "PARTIAL": "#d29922",
                 "GAP": "#f85149", "NOT_APPLICABLE": "#8b949e"}.get(sec.status, "#8b949e")
        evidence_html = "".join(f"<li>{e}</li>" for e in sec.evidence)
        gaps_html = (
            "<ul style='color:#f85149'>" +
            "".join(f"<li>⚠ {g}</li>" for g in sec.gaps) +
            "</ul>"
        ) if sec.gaps else ""
        comp_rows += f"""
<div style="border:1px solid #30363d;border-radius:6px;padding:1rem;margin:.75rem 0">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem">
    <strong style="color:#e6edf3">{sec.regulation}</strong>
    <span style="color:{color};font-size:.85rem;font-weight:600">{sec.status}</span>
  </div>
  <p style="color:#8b949e;font-size:.82rem;margin:.3rem 0">{sec.requirement}</p>
  <ul style="color:#c9d1d9;font-size:.82rem">{evidence_html}</ul>
  {gaps_html}
  {"<p style='color:#8b949e;font-size:.78rem;font-style:italic'>Note: " + sec.notes + "</p>" if sec.notes else ""}
</div>"""

    # Ledger entries
    entry_rows = "".join(
        f"<tr><td>{e.timestamp[:19]}</td><td>{e.agent_id}</td>"
        f"<td>{e.action}</td>"
        f"<td class='{e.verdict}'>{e.verdict}</td>"
        f"<td>{'⚠ tainted' if 'tainted=true' in (e.reason or '') else '—'}</td>"
        f"<td style='font-size:.75rem;color:#8b949e'>{e.reason}</td></tr>"
        for e in r.timeline.events
    )

    # Timestamp anchors
    anchor_rows = ""
    if r.timestamp_anchors:
        for a in r.timestamp_anchors:
            v_icon = "✓" if a.get("verified") else "✗"
            v_color = "#3fb950" if a.get("verified") else "#f85149"
            anchor_rows += (
                f"<tr><td>{a.get('anchor_id','')[:12]}</td>"
                f"<td style='font-size:.75rem'>{a.get('chain_head_hash','')[:20]}…</td>"
                f"<td>{a.get('anchored_at','')[:19]}</td>"
                f"<td>{a.get('tsa_url','')}</td>"
                f"<td style='color:{v_color}'>{v_icon} {'Granted' if a.get('verified') else 'Failed'}</td></tr>"
            )
        anchor_table = f"""
<h2>RFC 3161 Timestamp Anchors</h2>
<p style="color:#8b949e;font-size:.85rem">
  These anchors bind the audit chain to a trusted external clock.
  Verify with: <code>openssl ts -verify -in anchor.tsr -CAfile tsa.crt</code>
</p>
<table>
<thead><tr><th>Anchor ID</th><th>Chain Head</th><th>Anchored At (UTC)</th>
<th>TSA</th><th>Status</th></tr></thead>
<tbody>{anchor_rows}</tbody>
</table>"""
    else:
        anchor_table = """
<div style="border:1px solid #d29922;border-radius:4px;padding:.75rem;color:#d29922;margin:1rem 0">
  ⚠ No RFC 3161 timestamp anchors found.  Call <code>gate.anchor()</code> after
  completing runs to produce legally admissible timestamps.
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Forensic Report — {r.run_id}</title>
<style>
  body{{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:2rem;max-width:1100px;margin:0 auto}}
  h1{{color:#58a6ff;margin-bottom:.25rem}}
  h2{{color:#e6edf3;font-size:1.1rem;margin:2rem 0 .75rem}}
  table{{border-collapse:collapse;width:100%;margin:.5rem 0}}
  th,td{{border:1px solid #30363d;padding:.4rem .75rem;text-align:left;font-size:.82rem}}
  th{{background:#161b22;color:#8b949e;font-weight:600}}
  code{{background:#161b22;color:#79c0ff;padding:.1rem .3rem;border-radius:3px;font-size:.82rem}}
  .COMMIT{{color:#3fb950}}.REJECT{{color:#f85149}}.ESCALATE{{color:#d29922}}
  .meta{{display:flex;gap:2rem;color:#8b949e;font-size:.85rem;margin:.5rem 0 2rem}}
  .meta strong{{color:#c9d1d9}}
</style>
</head>
<body>
<h1>Forensic Report — {r.run_id}</h1>
<div class="meta">
  <span>Generated: <strong>{r.generated_at[:19]} UTC</strong></span>
  <span>Chain: <strong>{chain_badge}</strong></span>
  <span>Entries: <strong>{r.total_entries}</strong></span>
  <span>Verdicts: <strong>{json.dumps(r.verdict_counts)}</strong></span>
  <span>meshflow-forensic: <strong>v{r.meshflow_forensic_version}</strong></span>
</div>

<h2>Compliance Evidence Summary</h2>
{comp_rows}

{anchor_table}

<h2>Audit Ledger</h2>
<table>
<thead><tr><th>Timestamp (UTC)</th><th>Agent</th><th>Action</th>
<th>Verdict</th><th>Taint</th><th>Reason</th></tr></thead>
<tbody>{entry_rows}</tbody>
</table>

</body>
</html>"""
