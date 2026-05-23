"""Legal-critical MeshFlow demo: evidence-first contract review.

This example is intentionally dependency-free. It shows the product wedge:
existing agents can be wrapped as MeshNodes, but legal-critical mode requires
evidence, citations, review gates, and replayable audit records.
"""

from __future__ import annotations

import asyncio

from meshflow import Mesh, MeshNode, NodeOutput, PolicyMode, WorkflowDefinition, policy_for_mode
from meshflow.core.schemas import RiskTier


CONTRACT_SNIPPET = """
Section 7.2: Vendor may process Customer Data only to provide the Services.
Section 9.4: Either party may terminate for uncured material breach after 30 days notice.
Section 12.1: Liability is capped at fees paid in the prior 12 months, excluding confidentiality breach.
"""


async def extract_obligations(task: str, context: dict) -> NodeOutput:
    return NodeOutput(
        content="Extracted material obligations and risk-relevant clauses.",
        structured={
            "claims": [
                {
                    "claim": "Vendor data processing is purpose-limited to providing the Services.",
                    "citation": "Section 7.2",
                    "evidence": "Vendor may process Customer Data only to provide the Services.",
                    "confidence": 0.93,
                },
                {
                    "claim": "Termination for material breach requires 30 days notice and cure opportunity.",
                    "citation": "Section 9.4",
                    "evidence": "Either party may terminate for uncured material breach after 30 days notice.",
                    "confidence": 0.91,
                },
                {
                    "claim": "Confidentiality breach is carved out of the liability cap.",
                    "citation": "Section 12.1",
                    "evidence": "Liability is capped at fees paid in the prior 12 months, excluding confidentiality breach.",
                    "confidence": 0.89,
                },
            ],
            "contract_text": CONTRACT_SNIPPET,
        },
        tokens_used=180,
        confidence=0.91,
    )


async def verify_citations(task: str, context: dict) -> NodeOutput:
    claims = context.get("claims", [])
    verified = [
        claim
        for claim in claims
        if claim.get("citation") and claim.get("evidence") in context.get("contract_text", "")
    ]
    missing = len(claims) - len(verified)
    return NodeOutput(
        content=f"Verified {len(verified)} cited claims; missing_evidence={missing}.",
        structured={
            "verified_claims": verified,
            "missing_evidence": missing,
            "ready_for_review": missing == 0,
        },
        tokens_used=60,
        confidence=1.0 if missing == 0 else 0.5,
    )


async def prepare_review_packet(task: str, context: dict) -> NodeOutput:
    lines = ["Legal review packet:"]
    for claim in context.get("verified_claims", []):
        lines.append(f"- {claim['claim']} [{claim['citation']}]")
    lines.append("Reviewer decision required before external advice or contract changes.")
    return NodeOutput(
        content="\n".join(lines),
        structured={"review_packet_ready": True},
        tokens_used=90,
        confidence=0.88,
    )


async def external_advice_boundary(task: str, context: dict) -> NodeOutput:
    return NodeOutput(content="This should run only after human approval.", confidence=1.0)


async def main() -> None:
    policy = policy_for_mode(PolicyMode.LEGAL_CRITICAL, budget_usd=1.0)
    workflow = (
        WorkflowDefinition("legal_contract_review", policy=policy)
        .add_node(MeshNode.from_callable("extract", extract_obligations))
        .add_node(MeshNode.from_callable("verify", verify_citations))
        .add_node(
            MeshNode.from_callable(
                "review_packet",
                prepare_review_packet,
                risk=RiskTier.INTERNAL,
                capabilities=["legal_review_packet"],
            )
        )
        .add_node(
            MeshNode.from_callable(
                "external_advice",
                external_advice_boundary,
                risk=RiskTier.EXTERNAL_IO,
                capabilities=["external_legal_advice"],
            )
        )
        .add_edge("extract", "verify")
        .add_edge("verify", "review_packet", condition="ready_for_review == True")
        .add_edge("review_packet", "external_advice")
        .set_terminal("external_advice")
    )

    result = await Mesh(policy=policy).run_workflow(
        workflow,
        task="Review contract snippet for obligations and liability risk.",
    )
    print(result.output)
    print(f"run_id={result.run_id} paused_nodes={result.paused_nodes}")


if __name__ == "__main__":
    asyncio.run(main())
