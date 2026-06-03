"""meshflow-forensic — Deterministic AI governance kernel.

A standalone, zero-dependency forensic layer for AI agent systems.  Implements
the DASC (Deterministic Audit Safety Compliance) pattern with:

- ``DascGate``         — deterministic policy kernel (same input → same verdict)
- ``AutoRiskClassifier`` — overrides agent self-declared risk tiers
- ``TaintGraph``       — cross-agent IFC taint propagation
- ``CompensationExecutor`` — runs declared rollback plans on REJECT
- ``AuditLedger``      — SHA-256 hash-chained tamper-evident log
- ``ForensicReport``   — structured evidence export (JSON / HTML)
- ``EUAIActChecker``   — validates controls against EU AI Act high-risk criteria
- ``IncidentTimeline`` — reconstructs agent action sequence from ledger

Works standalone (no MeshFlow required) or as part of the MeshFlow stack.

Install::

    pip install meshflow-forensic            # standalone
    pip install meshflow-forensic[meshflow]  # with full MeshFlow integration

Quick start::

    from meshflow_forensic import DascGate, ForensicReport

    gate = DascGate.create(run_id="run_001")
    # ... run your agents through gate.evaluate(intent) ...

    report = ForensicReport.from_gate(gate)
    print(report.to_json())
"""

from __future__ import annotations

from meshflow_forensic.gate import (
    DascGate,
    AutoRiskClassifier,
    TaintGraph,
    CompensationExecutor,
    AuditLedger,
)
from meshflow_forensic.schemas import (
    Intent,
    ActionVerdict,
    LedgerEntry,
    RiskTier,
    ForensicPolicy,
)
from meshflow_forensic.report import ForensicReport, IncidentTimeline
from meshflow_forensic.eu_ai_act import EUAIActChecker, EUAIActResult, HighRiskCategory

__version__ = "1.0.0"
__all__ = [
    # Core gate
    "DascGate",
    "AutoRiskClassifier",
    "TaintGraph",
    "CompensationExecutor",
    "AuditLedger",
    # Schemas
    "Intent",
    "ActionVerdict",
    "LedgerEntry",
    "RiskTier",
    "ForensicPolicy",
    # Reporting
    "ForensicReport",
    "IncidentTimeline",
    # EU AI Act
    "EUAIActChecker",
    "EUAIActResult",
    "HighRiskCategory",
]
