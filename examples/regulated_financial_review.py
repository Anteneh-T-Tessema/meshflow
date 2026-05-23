"""Regulated financial document review pipeline.

Demonstrates MeshFlow's regulated policy mode:
  - Immutable SHA-256 audit chain for every step
  - Human-in-the-loop gate for IRREVERSIBLE risk actions
  - Collusion detection across agents
  - Uncertainty scoring — steps above threshold are flagged
  - Carbon budget tracking

Run (no API key — simulated):
    python examples/regulated_financial_review.py

Run with real Claude:
    ANTHROPIC_API_KEY=sk-ant-... python examples/regulated_financial_review.py
"""
from __future__ import annotations

import asyncio

from meshflow.core.mesh import Mesh
from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.schemas import RiskTier, policy_for_mode
from meshflow.core.workflow import WorkflowDefinition


# ── Nodes ─────────────────────────────────────────────────────────────────────

class DocumentParserNode(MeshNode):
    """Extracts key financial figures from the document."""

    def __init__(self) -> None:
        super().__init__(id="parser", kind=NodeKind.PYTHON, capabilities=["parse"])

    async def run(self, node_input: NodeInput) -> NodeOutput:
        # In production this would call a real parser / LLM
        figures = {
            "revenue_q3": "$4.2M",
            "revenue_q4": "$3.8M",
            "ebitda_margin": "18.4%",
            "debt_ratio": "2.1x",
            "flagged_items": ["Q4 revenue decline (-9.5%)", "Debt ratio above covenant 2.0x"],
        }
        summary = (
            f"Q3 Revenue: {figures['revenue_q3']}  Q4 Revenue: {figures['revenue_q4']} "
            f"(-9.5%)  EBITDA: {figures['ebitda_margin']}  "
            f"Debt: {figures['debt_ratio']} — above 2.0x covenant. "
            f"Flagged: {', '.join(figures['flagged_items'])}"
        )
        return NodeOutput(
            content=summary,
            confidence=0.92,
            structured=figures,
            risk_tier=RiskTier.READ_ONLY,
        )


class RiskAssessorNode(MeshNode):
    """Scores each flagged item against regulatory thresholds."""

    def __init__(self) -> None:
        super().__init__(id="risk_assessor", kind=NodeKind.PYTHON, capabilities=["assess"])

    async def run(self, node_input: NodeInput) -> NodeOutput:
        assessment = (
            "RISK ASSESSMENT\n"
            "1. Q4 revenue decline (-9.5%): MEDIUM — within 10% variance threshold.\n"
            "2. Debt ratio 2.1x vs 2.0x covenant: HIGH — covenant breach triggers "
            "mandatory disclosure under SEC Reg FD within 4 business days.\n"
            "Recommendation: escalate to legal counsel before filing."
        )
        return NodeOutput(
            content=assessment,
            confidence=0.88,
            risk_tier=RiskTier.INTERNAL,
        )


class FilingRecommendationNode(MeshNode):
    """Produces the final filing recommendation — IRREVERSIBLE tier triggers HITL."""

    def __init__(self) -> None:
        super().__init__(
            id="filing_rec",
            kind=NodeKind.PYTHON,
            capabilities=["recommend"],
        )

    async def run(self, node_input: NodeInput) -> NodeOutput:
        recommendation = (
            "FILING RECOMMENDATION (DRAFT — pending legal review)\n"
            "File 8-K within 4 business days disclosing debt covenant breach.\n"
            "Include remediation plan and updated guidance.\n"
            "DO NOT file without written approval from General Counsel."
        )
        # IRREVERSIBLE tier — the governed runtime will pause for human review
        # when policy.human_in_loop is enabled (as in 'regulated' mode)
        return NodeOutput(
            content=recommendation,
            confidence=0.81,
            risk_tier=RiskTier.IRREVERSIBLE,
        )


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_regulated_pipeline() -> None:
    print("=" * 60)
    print("MeshFlow — Regulated Mode: Financial Document Review")
    print("=" * 60)

    policy = policy_for_mode(
        "regulated",
        budget_usd=3.0,
        max_steps=10,
    )

    print(f"\nPolicy mode      : {policy.mode.value}")
    print(f"Collusion audit  : {policy.enable_collusion_audit}")
    print(f"Uncertainty      : {policy.enable_uncertainty}")
    print("HITL threshold   : IRREVERSIBLE risk tier")
    print(f"Budget cap       : ${policy.budget_usd:.2f}")

    # Build the workflow
    wf = WorkflowDefinition(name="financial-review")
    parser   = DocumentParserNode()
    assessor = RiskAssessorNode()
    filing   = FilingRecommendationNode()

    wf.add_node(parser)
    wf.add_node(assessor)
    wf.add_node(filing)
    wf.add_edge(parser.id, assessor.id)
    wf.add_edge(assessor.id, filing.id)
    wf.set_terminal(filing.id)

    mesh = Mesh(policy=policy)

    print("\n--- Running regulated workflow ---")
    document_text = (
        "Q3-Q4 Financial Summary: Revenue declined 9.5% QoQ. "
        "EBITDA margin 18.4%. Debt-to-EBITDA 2.1x."
    )
    result = await mesh.run_workflow(wf, document_text)

    status = "completed" if result.completed else ("paused" if result.paused_nodes else "running")
    print(f"\nStatus         : {status}")
    print(f"Tokens used    : {result.total_tokens}")
    print(f"Cost           : ${result.total_cost_usd:.6f}")
    print(f"Steps          : {len(result.steps)}")
    print(f"Run ID         : {result.run_id}")

    if result.paused_nodes:
        print("\n[HITL GATE] Filing recommendation blocked — awaiting General Counsel approval.")
        print(f"  meshflow hitl approve {result.run_id} --reviewer gc-office --notes 'Approved'")
        print(f"  Paused at: {result.paused_nodes}")
    elif result.completed:
        print("\n[Chain valid] All steps hash-chained — tamper-evident audit trail complete.")
        print(f"  meshflow trace {result.run_id}")

    if result.blocked_nodes:
        print(f"\n[BLOCKED] {len(result.blocked_nodes)} node(s) blocked: {result.blocked_nodes}")
        print("  Set ANTHROPIC_API_KEY to run with a real LLM provider.")


if __name__ == "__main__":
    asyncio.run(run_regulated_pipeline())
