"""Legal-critical NDA review pipeline.

Demonstrates MeshFlow's legal-critical policy mode:
  - Every claim requires a cited source
  - Deterministic gate: identical inputs must produce identical verdicts
  - Mandatory human review for all output
  - Maximum audit verbosity (all intermediate reasoning persisted)
  - Collusion + uncertainty detection

This is suitable for law firms, M&A diligence, and regulatory submissions.

Run (simulated — no API key):
    python examples/legal_critical_nda_review.py

Run with real Claude:
    ANTHROPIC_API_KEY=sk-ant-... python examples/legal_critical_nda_review.py
"""
from __future__ import annotations

import asyncio

from meshflow.core.mesh import Mesh
from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.schemas import RiskTier, policy_for_mode
from meshflow.core.workflow import WorkflowDefinition


# ── Sample NDA excerpt ────────────────────────────────────────────────────────

NDA_TEXT = """\
NON-DISCLOSURE AGREEMENT — EXCERPT

Section 4.1 – Definition of Confidential Information
"Confidential Information" means any non-public information disclosed by
Disclosing Party to Receiving Party, whether oral, written, or electronic,
that is designated as confidential or that reasonably should be understood
to be confidential given the nature of the information and circumstances
of disclosure.

Section 4.2 – Exclusions
Confidential Information does not include information that:
(a) is or becomes publicly available without breach of this Agreement;
(b) was rightfully known to Receiving Party prior to disclosure;
(c) is rightfully received from a third party without restriction.

Section 7.1 – Term
This Agreement shall remain in effect for three (3) years from the
Effective Date, unless earlier terminated by mutual written consent.

Section 9.3 – Governing Law
This Agreement shall be governed by the laws of the State of Delaware,
without regard to conflict of law principles.
"""


# ── Nodes ─────────────────────────────────────────────────────────────────────

class ClauseExtractorNode(MeshNode):
    """Extracts and categorises clauses, citing section numbers."""

    def __init__(self) -> None:
        super().__init__(id="extractor", kind=NodeKind.PYTHON, capabilities=["extract"])

    async def run(self, node_input: NodeInput) -> NodeOutput:
        clauses = {
            "definition": "§4.1 — Standard broad definition; covers oral/written/electronic.",
            "exclusions": "§4.2(a-c) — Three standard carve-outs: public domain, prior knowledge, third-party receipt.",
            "term": "§7.1 — 3-year term; terminable by mutual written consent.",
            "governing_law": "§9.3 — Delaware law, no conflict-of-law clause.",
        }
        content = "\n".join(f"[{k.upper()}] {v}" for k, v in clauses.items())
        return NodeOutput(
            content=content,
            confidence=0.95,
            structured={"clauses": clauses, "citations": ["§4.1", "§4.2", "§7.1", "§9.3"]},
            risk_tier=RiskTier.READ_ONLY,
        )


class RiskFlagNode(MeshNode):
    """Flags non-standard clauses against market standard NDAs, with citations."""

    def __init__(self) -> None:
        super().__init__(id="risk_flagger", kind=NodeKind.PYTHON, capabilities=["flag"])

    async def run(self, node_input: NodeInput) -> NodeOutput:
        flags = [
            {
                "clause": "§4.1",
                "issue": "Definition includes 'oral' disclosures without written confirmation requirement.",
                "risk": "MEDIUM",
                "market_standard": "ABA Model NDA §2 requires written confirmation within 30 days.",
                "recommendation": "Add written confirmation requirement for oral disclosures.",
            },
            {
                "clause": "§7.1",
                "issue": "3-year term is shorter than standard 5-year for technology NDAs.",
                "risk": "LOW",
                "market_standard": "NVCA model NDA uses 5-year term for tech deals.",
                "recommendation": "Negotiate to 5 years if trade secrets involved.",
            },
            {
                "clause": "§9.3",
                "issue": "No conflict-of-law clause specified.",
                "risk": "LOW",
                "market_standard": "Standard practice to include explicit conflict-of-law provision.",
                "recommendation": "Acceptable as-is for domestic US parties.",
            },
        ]
        lines = []
        for f in flags:
            lines.append(
                f"[{f['risk']}] {f['clause']}: {f['issue']}\n"
                f"  Standard: {f['market_standard']}\n"
                f"  Rec: {f['recommendation']}"
            )
        return NodeOutput(
            content="\n\n".join(lines),
            confidence=0.89,
            structured={"flags": flags, "total_flags": len(flags)},
            risk_tier=RiskTier.INTERNAL,
        )


class LegalOpinionNode(MeshNode):
    """Produces the final legal opinion — requires human countersignature."""

    def __init__(self) -> None:
        super().__init__(id="opinion", kind=NodeKind.PYTHON, capabilities=["opine"])

    async def run(self, node_input: NodeInput) -> NodeOutput:
        opinion = (
            "LEGAL OPINION DRAFT — ATTORNEY REVIEW REQUIRED\n\n"
            "This NDA is substantially in market-standard form with two negotiation points:\n\n"
            "1. §4.1 Oral disclosures: Recommend adding 30-day written confirmation clause "
            "(ABA Model NDA §2). Risk: MEDIUM.\n\n"
            "2. §7.1 Term: 3 years is acceptable for a pilot engagement. If relationship "
            "becomes strategic, renegotiate to 5 years (NVCA standard).\n\n"
            "Governing law (Delaware) and exclusions (§4.2) are standard. "
            "No material issues identified that would prevent execution.\n\n"
            "CAVEAT: This opinion is AI-assisted. An attorney must review and countersign "
            "before this opinion is communicated to any party.\n\n"
            "Citations: ABA Model NDA (2023), NVCA Model NDA (2024), §§4.1-4.2, 7.1, 9.3"
        )
        # IRREVERSIBLE — legal opinion that could be relied upon externally
        return NodeOutput(
            content=opinion,
            confidence=0.87,
            risk_tier=RiskTier.IRREVERSIBLE,
            structured={
                "recommend_sign": True,
                "conditions": ["Add oral confirmation clause", "Review term if strategic"],
                "citations": ["ABA Model NDA (2023)", "NVCA Model NDA (2024)"],
            },
        )


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_legal_critical_pipeline() -> None:
    print("=" * 60)
    print("MeshFlow — Legal-Critical Mode: NDA Review")
    print("=" * 60)

    policy = policy_for_mode(
        "legal-critical",
        budget_usd=5.0,
        max_steps=15,
    )

    print(f"\nPolicy mode         : {policy.mode.value}")
    print(f"Deterministic gate  : {getattr(policy, 'deterministic_gate', True)}")
    print(f"Require human review: {getattr(policy, 'require_human_review', True)}")
    print(f"Collusion audit     : {policy.enable_collusion_audit}")
    print(f"Immutable audit     : {getattr(policy, 'immutable_audit', True)}")
    print(f"Budget cap          : ${policy.budget_usd:.2f}")

    print("\n--- NDA excerpt (first 200 chars) ---")
    print(NDA_TEXT[:200].strip() + "…")

    wf = WorkflowDefinition(name="nda-review")
    extractor = ClauseExtractorNode()
    flagger   = RiskFlagNode()
    opinion   = LegalOpinionNode()

    wf.add_node(extractor)
    wf.add_node(flagger)
    wf.add_node(opinion)
    wf.add_edge(extractor.id, flagger.id)
    wf.add_edge(flagger.id, opinion.id)
    wf.set_terminal(opinion.id)

    mesh = Mesh(policy=policy)

    print("\n--- Running legal-critical workflow ---")
    result = await mesh.run_workflow(wf, NDA_TEXT)

    status = "completed" if result.completed else ("paused" if result.paused_nodes else "running")
    print(f"\nStatus          : {status}")
    print(f"Tokens          : {result.total_tokens}")
    print(f"Cost            : ${result.total_cost_usd:.6f}")
    print(f"Steps           : {len(result.steps)}")
    print(f"Run ID          : {result.run_id}")

    if result.paused_nodes:
        print("\n[HITL GATE] Legal opinion paused — attorney countersignature required.")
        print(f"  meshflow hitl approve {result.run_id} \\")
        print("      --reviewer attorney-smith --notes 'Reviewed and countersigned'")
        print(f"  Paused at: {result.paused_nodes}")
    elif result.completed:
        print("\n[Audit] Full chain of reasoning persisted with SHA-256 hash links.")
        print(f"  meshflow trace {result.run_id}         # view chain")
        print(f"  meshflow trace {result.run_id} --export nda_audit.json")

    if result.blocked_nodes:
        print(f"\n[BLOCKED] {result.blocked_nodes}")
        print("  Set ANTHROPIC_API_KEY to run with a real LLM provider.")


if __name__ == "__main__":
    asyncio.run(run_legal_critical_pipeline())
