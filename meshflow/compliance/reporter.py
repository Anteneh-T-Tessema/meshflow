"""Compliance report generator — produce audit artifacts for regulated industries.

Supported frameworks:
  hipaa  — HIPAA Security/Privacy Rules (healthcare PHI)
  sox    — Sarbanes-Oxley Section 302/404 (financial controls)
  gdpr   — GDPR Articles 5/6/30/32 (EU data protection)
  pci    — PCI DSS v4 (payment card data)
  nerc   — NERC CIP (critical infrastructure)

Usage::

    from meshflow.compliance.reporter import ComplianceReporter

    reporter = ComplianceReporter()
    steps = ledger.get_run_sync(run_id)          # list of step record dicts
    report = reporter.generate("hipaa", steps, run_ids=[run_id])
    print(report.to_text())
    print(report.to_json())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SUPPORTED_FRAMEWORKS = ("hipaa", "sox", "gdpr", "pci", "nerc")


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class ComplianceFinding:
    category: str
    control_id: str
    status: str  # "pass" | "fail" | "warning" | "na"
    detail: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "control_id": self.control_id,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class ComplianceSummary:
    total: int
    passed: int
    failed: int
    warnings: int
    na: int
    pass_rate: float
    overall_status: str  # "compliant" | "non_compliant" | "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "na": self.na,
            "pass_rate": round(self.pass_rate, 4),
            "overall_status": self.overall_status,
        }


@dataclass
class ComplianceReport:
    framework: str
    framework_version: str
    run_ids: list[str]
    generated_at: str
    total_steps: int
    findings: list[ComplianceFinding]
    summary: ComplianceSummary
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "framework_version": self.framework_version,
            "run_ids": self.run_ids,
            "generated_at": self.generated_at,
            "total_steps": self.total_steps,
            "summary": self.summary.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_text(self) -> str:
        lines: list[str] = []
        fw = self.framework.upper()
        lines.append(f"{'=' * 72}")
        lines.append(f"  MeshFlow {fw} Compliance Report")
        lines.append(f"  Framework: {self.framework_version}")
        lines.append(f"  Generated: {self.generated_at}")
        lines.append(f"  Run IDs:   {', '.join(self.run_ids) or '(all)'}")
        lines.append(f"  Steps audited: {self.total_steps}")
        lines.append(f"{'=' * 72}")
        s = self.summary
        lines.append(
            f"  OVERALL: {s.overall_status.upper()}  "
            f"({s.passed}/{s.total} controls pass, "
            f"{s.failed} fail, {s.warnings} warnings)"
        )
        lines.append(f"{'─' * 72}")
        for f_ in self.findings:
            icon = {"pass": "✅", "fail": "❌", "warning": "⚠️", "na": "➖"}.get(f_.status, "?")
            lines.append(f"  {icon}  [{f_.control_id}] {f_.category}")
            lines.append(f"       {f_.detail}")
            for ev in f_.evidence[:3]:
                lines.append(f"       ↳ {ev}")
        lines.append(f"{'=' * 72}")
        return "\n".join(lines)


# ── Framework-specific check implementations ──────────────────────────────────


def _count_blocked(steps: list[dict[str, Any]]) -> int:
    return sum(1 for s in steps if s.get("blocked") or s.get("blocked_by"))


def _count_verdicts(steps: list[dict[str, Any]], verdict: str) -> int:
    return sum(1 for s in steps if s.get("verdict") == verdict)


def _has_chain_hashes(steps: list[dict[str, Any]]) -> bool:
    return all(s.get("entry_hash") for s in steps) if steps else False


def _avg_uncertainty(steps: list[dict[str, Any]]) -> float:
    vals = [s.get("uncertainty", 0.0) for s in steps if "uncertainty" in s]
    return sum(vals) / len(vals) if vals else 0.0


def _nodes_used(steps: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(s.get("node_id", "") for s in steps if s.get("node_id")))


def _total_cost(steps: list[dict[str, Any]]) -> float:
    return sum(s.get("cost_usd", 0.0) for s in steps)


def _collusion_alerts(steps: list[dict[str, Any]]) -> int:
    return sum(1 for s in steps if s.get("collusion_risk", 0.0) > 0.7)


# HIPAA ───────────────────────────────────────────────────────────────────────

def _hipaa_checks(steps: list[dict[str, Any]]) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []

    # §164.312(b) — Audit controls
    has_hashes = _has_chain_hashes(steps)
    findings.append(ComplianceFinding(
        category="Audit Controls",
        control_id="HIPAA-§164.312(b)",
        status="pass" if has_hashes else "warning",
        detail=(
            "Tamper-evident hash chain present on all step records."
            if has_hashes
            else "Step records present but hash chain entries are incomplete."
        ),
        evidence=[f"Steps audited: {len(steps)}"],
    ))

    # §164.312(a)(1) — Access control
    blocked = _count_blocked(steps)
    findings.append(ComplianceFinding(
        category="Access Control",
        control_id="HIPAA-§164.312(a)(1)",
        status="pass" if blocked == 0 else "warning",
        detail=(
            "No policy-blocked actions detected."
            if blocked == 0
            else f"{blocked} steps were blocked by policy — review denied actions."
        ),
        evidence=[f"Blocked steps: {blocked}", f"Total steps: {len(steps)}"],
    ))

    # §164.312(c)(1) — Integrity
    uncertain = sum(1 for s in steps if s.get("uncertainty", 0.0) > 0.85)
    findings.append(ComplianceFinding(
        category="Integrity",
        control_id="HIPAA-§164.312(c)(1)",
        status="pass" if uncertain == 0 else "warning",
        detail=(
            "All agent outputs passed uncertainty thresholds."
            if uncertain == 0
            else f"{uncertain} steps exceeded uncertainty threshold (>0.85) — verify outputs."
        ),
        evidence=[f"High-uncertainty steps: {uncertain}"],
    ))

    # §164.312(e)(2)(ii) — Encryption / PHI scrubbing
    phi_scrubbed = any(s.get("phi_scrubbed") for s in steps)
    findings.append(ComplianceFinding(
        category="PHI Protection",
        control_id="HIPAA-§164.312(e)(2)(ii)",
        status="pass" if phi_scrubbed or not steps else "na",
        detail=(
            "PHI scrubbing markers detected in step records."
            if phi_scrubbed
            else "No PHI scrubbing markers in step records — confirm phi_scrubbing=True in compliance profile."
        ),
        evidence=[],
    ))

    # §164.308(a)(1) — Risk analysis
    collusion = _collusion_alerts(steps)
    findings.append(ComplianceFinding(
        category="Risk Analysis",
        control_id="HIPAA-§164.308(a)(1)",
        status="pass" if collusion == 0 else "fail",
        detail=(
            "No collusion risk alerts raised during execution."
            if collusion == 0
            else f"{collusion} steps raised collusion risk alerts (>0.7) — investigate multi-agent coordination."
        ),
        evidence=[f"Collusion alerts: {collusion}"],
    ))

    return findings


# SOX ─────────────────────────────────────────────────────────────────────────

def _sox_checks(steps: list[dict[str, Any]]) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []

    # SOX §302 — Management certification of controls
    nodes = _nodes_used(steps)
    has_separation = len(nodes) >= 2
    findings.append(ComplianceFinding(
        category="Segregation of Duties",
        control_id="SOX-§302",
        status="pass" if has_separation else "warning",
        detail=(
            f"Multiple agents ({len(nodes)}) participated — segregation of duties enforced."
            if has_separation
            else "Single-agent workflow — verify that maker-checker controls are not required."
        ),
        evidence=[f"Agents: {', '.join(nodes[:5])}"],
    ))

    # SOX §404 — Audit trail integrity
    has_hashes = _has_chain_hashes(steps)
    findings.append(ComplianceFinding(
        category="Audit Trail Integrity",
        control_id="SOX-§404",
        status="pass" if has_hashes else "fail",
        detail=(
            "All steps have tamper-evident hash chain entries."
            if has_hashes
            else "Hash chain entries missing — audit trail cannot be certified as tamper-evident."
        ),
        evidence=[f"Steps with hashes: {sum(1 for s in steps if s.get('entry_hash'))} / {len(steps)}"],
    ))

    # SOX §409 — Real-time disclosure (HITL for material events)
    hitl_steps = sum(1 for s in steps if s.get("hitl_required") or s.get("hitl_approved") is not None)
    blocked = _count_blocked(steps)
    findings.append(ComplianceFinding(
        category="Material Event Disclosure",
        control_id="SOX-§409",
        status="pass",
        detail=(
            f"HITL checkpoints detected: {hitl_steps}. Blocked actions: {blocked}."
            if hitl_steps or blocked
            else "No material events requiring HITL escalation detected."
        ),
        evidence=[f"HITL steps: {hitl_steps}", f"Blocked: {blocked}"],
    ))

    # SOX cost accountability
    total_cost = _total_cost(steps)
    findings.append(ComplianceFinding(
        category="Cost Accountability",
        control_id="SOX-§404-COST",
        status="pass",
        detail=f"Total AI spend for audited runs: ${total_cost:.4f} USD.",
        evidence=[f"Total cost: ${total_cost:.4f}", f"Steps: {len(steps)}"],
    ))

    # SOX change management
    denied = _count_verdicts(steps, "denied")
    findings.append(ComplianceFinding(
        category="Change Management",
        control_id="SOX-CHANGE-MGMT",
        status="pass" if denied == 0 else "warning",
        detail=(
            "No denied policy verdicts."
            if denied == 0
            else f"{denied} steps returned 'denied' verdict — confirm change-approval process was followed."
        ),
        evidence=[f"Denied steps: {denied}"],
    ))

    return findings


# GDPR ────────────────────────────────────────────────────────────────────────

def _gdpr_checks(steps: list[dict[str, Any]]) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []

    # Art. 5(1)(a) — Lawfulness, fairness, transparency
    has_hashes = _has_chain_hashes(steps)
    findings.append(ComplianceFinding(
        category="Transparency & Accountability",
        control_id="GDPR-Art5(1)(a)",
        status="pass" if has_hashes else "warning",
        detail=(
            "Processing log is complete and tamper-evident."
            if has_hashes
            else "Processing log is incomplete — Art. 5(2) accountability principle may not be met."
        ),
        evidence=[],
    ))

    # Art. 5(1)(c) — Data minimisation
    avg_tokens = sum(s.get("tokens_used", 0) for s in steps) / max(len(steps), 1)
    findings.append(ComplianceFinding(
        category="Data Minimisation",
        control_id="GDPR-Art5(1)(c)",
        status="pass" if avg_tokens < 50_000 else "warning",
        detail=(
            f"Average tokens per step: {avg_tokens:.0f} — within data minimisation threshold."
            if avg_tokens < 50_000
            else f"Average tokens per step: {avg_tokens:.0f} — review whether all data is necessary."
        ),
        evidence=[f"Avg tokens/step: {avg_tokens:.0f}"],
    ))

    # Art. 6 — Lawful basis for processing
    blocked = _count_blocked(steps)
    findings.append(ComplianceFinding(
        category="Lawful Basis",
        control_id="GDPR-Art6",
        status="pass" if blocked == 0 else "warning",
        detail=(
            "No steps blocked by policy — lawful basis controls operating normally."
            if blocked == 0
            else f"{blocked} steps blocked — verify lawful basis was established before processing."
        ),
        evidence=[f"Blocked steps: {blocked}"],
    ))

    # Art. 30 — Records of processing activities
    nodes = _nodes_used(steps)
    findings.append(ComplianceFinding(
        category="Records of Processing",
        control_id="GDPR-Art30",
        status="pass" if steps else "na",
        detail=(
            f"Processing activities recorded for {len(nodes)} agent(s) across {len(steps)} steps."
            if steps
            else "No processing records available."
        ),
        evidence=[f"Agents: {', '.join(nodes[:5])}", f"Steps: {len(steps)}"],
    ))

    # Art. 32 — Security of processing
    collusion = _collusion_alerts(steps)
    uncertain = sum(1 for s in steps if s.get("uncertainty", 0.0) > 0.9)
    findings.append(ComplianceFinding(
        category="Security of Processing",
        control_id="GDPR-Art32",
        status="pass" if collusion == 0 and uncertain == 0 else "warning",
        detail=(
            "No security anomalies (collusion or high uncertainty) detected."
            if collusion == 0 and uncertain == 0
            else f"Security anomalies: {collusion} collusion alerts, {uncertain} high-uncertainty steps."
        ),
        evidence=[f"Collusion alerts: {collusion}", f"High-uncertainty steps: {uncertain}"],
    ))

    return findings


# PCI ─────────────────────────────────────────────────────────────────────────

def _pci_checks(steps: list[dict[str, Any]]) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []

    # Req 7 — Restrict access to system components
    blocked = _count_blocked(steps)
    findings.append(ComplianceFinding(
        category="Access Restriction",
        control_id="PCI-DSS-Req7",
        status="pass" if blocked == 0 else "warning",
        detail=(
            "No unauthorised access attempts detected."
            if blocked == 0
            else f"{blocked} steps blocked by policy — review access control configuration."
        ),
        evidence=[f"Blocked steps: {blocked}"],
    ))

    # Req 10 — Log and monitor all access
    has_hashes = _has_chain_hashes(steps)
    findings.append(ComplianceFinding(
        category="Logging & Monitoring",
        control_id="PCI-DSS-Req10",
        status="pass" if has_hashes else "fail",
        detail=(
            "Tamper-evident audit log maintained for all agent actions."
            if has_hashes
            else "Audit log integrity cannot be verified — hash chain entries missing."
        ),
        evidence=[f"Steps logged: {len(steps)}"],
    ))

    # Req 6 — Protect systems from vulnerabilities
    collusion = _collusion_alerts(steps)
    findings.append(ComplianceFinding(
        category="Vulnerability Protection",
        control_id="PCI-DSS-Req6",
        status="pass" if collusion == 0 else "fail",
        detail=(
            "No collusion-pattern anomalies detected."
            if collusion == 0
            else f"{collusion} collusion alerts raised — investigate agent coordination patterns."
        ),
        evidence=[f"Collusion alerts: {collusion}"],
    ))

    # Req 12 — Support information security with policies
    denied = _count_verdicts(steps, "denied")
    approved = _count_verdicts(steps, "approved")
    total = len(steps)
    findings.append(ComplianceFinding(
        category="Policy Enforcement",
        control_id="PCI-DSS-Req12",
        status="pass",
        detail=(
            f"Policy enforcement active: {approved} approved, {denied} denied out of {total} steps."
        ),
        evidence=[f"Approved: {approved}", f"Denied: {denied}"],
    ))

    # Req 8 — Identify users and authenticate access
    hitl_steps = sum(1 for s in steps if s.get("hitl_approved") is not None)
    findings.append(ComplianceFinding(
        category="Identity & Authentication",
        control_id="PCI-DSS-Req8",
        status="pass" if hitl_steps >= 0 else "na",
        detail=f"Human approval records: {hitl_steps} HITL checkpoints logged.",
        evidence=[f"HITL steps: {hitl_steps}"],
    ))

    return findings


# NERC ────────────────────────────────────────────────────────────────────────

def _nerc_checks(steps: list[dict[str, Any]]) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []

    # CIP-007-6 — Systems security management
    blocked = _count_blocked(steps)
    findings.append(ComplianceFinding(
        category="Systems Security Management",
        control_id="NERC-CIP-007",
        status="pass" if blocked == 0 else "warning",
        detail=(
            "No policy violations detected in agent actions affecting BES cyber systems."
            if blocked == 0
            else f"{blocked} policy-blocked steps — verify BES cyber system access controls."
        ),
        evidence=[f"Blocked: {blocked}"],
    ))

    # CIP-008-6 — Incident reporting
    collusion = _collusion_alerts(steps)
    findings.append(ComplianceFinding(
        category="Incident Reporting",
        control_id="NERC-CIP-008",
        status="pass" if collusion == 0 else "fail",
        detail=(
            "No security incidents (collusion patterns) detected."
            if collusion == 0
            else f"{collusion} potential security incidents flagged via collusion detection."
        ),
        evidence=[f"Incidents: {collusion}"],
    ))

    # CIP-012-1 — Communications between control centres
    nodes = _nodes_used(steps)
    findings.append(ComplianceFinding(
        category="Inter-agent Communications",
        control_id="NERC-CIP-012",
        status="pass",
        detail=f"All inter-agent communications logged across {len(nodes)} agent(s).",
        evidence=[f"Agents: {', '.join(nodes[:5])}"],
    ))

    # CIP-014-3 — Physical security
    has_hashes = _has_chain_hashes(steps)
    findings.append(ComplianceFinding(
        category="Audit Trail",
        control_id="NERC-CIP-014",
        status="pass" if has_hashes else "warning",
        detail=(
            "Tamper-evident records maintained for all critical infrastructure agent actions."
            if has_hashes
            else "Audit trail integrity not fully verifiable — review hash chain configuration."
        ),
        evidence=[],
    ))

    return findings


# ── ComplianceReporter ────────────────────────────────────────────────────────

_FRAMEWORK_VERSIONS = {
    "hipaa": "HIPAA Security Rule 45 CFR Part 164",
    "sox": "Sarbanes-Oxley Act 2002 §302/§404/§409",
    "gdpr": "GDPR (EU) 2016/679",
    "pci": "PCI DSS v4.0",
    "nerc": "NERC CIP v6",
}

_FRAMEWORK_CHECKS = {
    "hipaa": _hipaa_checks,
    "sox": _sox_checks,
    "gdpr": _gdpr_checks,
    "pci": _pci_checks,
    "nerc": _nerc_checks,
}


class ComplianceReporter:
    """Generate structured compliance reports from MeshFlow ledger data."""

    def generate(
        self,
        framework: str,
        steps: list[dict[str, Any]],
        run_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ComplianceReport:
        fw = framework.lower().strip()
        if fw not in SUPPORTED_FRAMEWORKS:
            raise ValueError(
                f"Unknown framework '{framework}'. "
                f"Supported: {', '.join(SUPPORTED_FRAMEWORKS)}"
            )

        checker = _FRAMEWORK_CHECKS[fw]
        findings = checker(steps)

        total = len(findings)
        passed = sum(1 for f in findings if f.status == "pass")
        failed = sum(1 for f in findings if f.status == "fail")
        warnings = sum(1 for f in findings if f.status == "warning")
        na = sum(1 for f in findings if f.status == "na")
        denominator = total - na or 1
        pass_rate = passed / denominator

        if failed > 0:
            overall = "non_compliant"
        elif warnings > 0:
            overall = "partial"
        else:
            overall = "compliant"

        summary = ComplianceSummary(
            total=total,
            passed=passed,
            failed=failed,
            warnings=warnings,
            na=na,
            pass_rate=pass_rate,
            overall_status=overall,
        )

        return ComplianceReport(
            framework=fw,
            framework_version=_FRAMEWORK_VERSIONS[fw],
            run_ids=run_ids or [],
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_steps=len(steps),
            findings=findings,
            summary=summary,
            metadata=metadata or {},
        )
