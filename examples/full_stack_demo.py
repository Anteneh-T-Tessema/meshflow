"""Full-stack MeshFlow demo — agentic loop + multi-agent patterns + governance.

Demonstrates every major platform capability in one coherent scenario:
a regulated (HIPAA) clinical-document review pipeline.

Components used:
  - Compliance profile      → Mesh(compliance="hipaa")
  - Auto provider routing   → auto_model("critic", compliance="hipaa") → opus
  - ReActAgent              → agentic loop for document research
  - run_typed               → structured Pydantic output
  - Supervisor              → orchestrator delegates to specialists
  - AdversarialTeam         → Proposer → Attacker → Judge for high-stakes finding
  - AgentSession            → stateful multi-turn Q&A after the review
  - Agent library           → pre-built ResearchAgent, CriticAgent

Run (requires ANTHROPIC_API_KEY):
    python examples/full_stack_demo.py

    # Dry-run with mock LLM (no API key needed):
    MESHFLOW_MOCK=1 python examples/full_stack_demo.py
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

# ── Conditional mock for offline demo ────────────────────────────────────────
_MOCK = os.getenv("MESHFLOW_MOCK", "").strip() in ("1", "true", "yes")


def _patch_provider_if_mock() -> None:
    """Replace AnthropicProvider.complete with a canned mock."""
    if not _MOCK:
        return

    from meshflow.agents.base import AnthropicProvider

    async def _mock_complete(
        self: Any, model: str, messages: Any, system: str, max_tokens: int
    ) -> tuple[str, int, float]:
        last = messages[-1]["content"] if messages else ""

        # run_typed: system contains the JSON schema instruction
        if "You MUST respond with a JSON object matching this schema" in system:
            if "PHIRiskAssessment" in system or "phi_fields" in system:
                return (
                    '{"phi_fields_present": ["name", "DOB", "diagnosis"], '
                    '"risk_level": "medium", "minimum_necessary": true, '
                    '"recommendation": "Document is within HIPAA minimum-necessary scope for treatment referral."}',
                    100, 0.002,
                )
            if "steps" in system and "worker_name" in system:
                return (
                    '{"steps": [{"worker_name": "phi-scanner", "subtask": "scan for PHI"}, '
                    '{"worker_name": "risk-assessor", "subtask": "assess clinical risk"}]}',
                    120, 0.002,
                )
            # Generic structured fallback
            return ('{"result": "compliant", "notes": "No issues found."}', 80, 0.001)

        # Delegation plan (Supervisor fallback path)
        if "delegation plan" in system.lower() or "delegation plan" in last.lower():
            return (
                '{"steps": [{"worker_name": "phi-scanner", "subtask": "scan for PHI"}, '
                '{"worker_name": "risk-assessor", "subtask": "assess clinical risk"}]}',
                120, 0.002,
            )
        # Judge verdict
        if "verdict" in last.lower() and ("critique" in last.lower() or "proposed answer" in last.lower()):
            return (
                '{"verdict": "accept", "reasoning": "clinically accurate, no PHI exposed", '
                '"revised_answer": ""}',
                90, 0.001,
            )
        # ReAct format
        if "Thought:" in system or "Action:" in system or "Action Input:" in last:
            return (
                "Thought: I have all I need.\n"
                "Action: Final Answer\n"
                "Action Input: Document complies with HIPAA minimum-necessary standard. "
                "No impermissible disclosures detected.",
                80, 0.001,
            )
        # Synthesis (Supervisor)
        if "synthesise" in last.lower() or "synthesize" in last.lower():
            return (
                "DONE: The clinical document passes HIPAA review. PHI scope is minimal "
                "and purpose-limited. No remediation required.",
                100, 0.002,
            )
        # Generic fallback
        return (
            "No PHI violations detected. Document aligns with HIPAA minimum-necessary rule. "
            "CONFIDENCE:0.87",
            70, 0.001,
        )

    AnthropicProvider.complete = _mock_complete  # type: ignore[method-assign]


# ── Main demo ─────────────────────────────────────────────────────────────────


async def main() -> None:
    _patch_provider_if_mock()

    from pydantic import BaseModel

    from meshflow import (
        Agent,
        AdversarialTeam,
        AgentSession,
        Mesh,
        ReActAgent,
        Supervisor,
        auto_model,
        compliance_profile,
    )
    from meshflow.agents.router import auto_model
    from meshflow.tools.registry import tool

    # ── 0. Compliance profile ─────────────────────────────────────────────────
    profile = compliance_profile("hipaa")
    print(f"\n{'='*60}")
    print(f"Compliance: {profile.name}")
    print(f"  HITL threshold   : {profile.hitl_threshold}")
    print(f"  PHI scrubbing    : {profile.phi_scrubbing}")
    print(f"  Audit retention  : {profile.audit_retention_days} days (7 years)")
    print(f"  Verifiers active : {profile.verifier_domains}")

    # ── 1. Smart provider routing ─────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Provider routing (role × budget × compliance):")
    for role in ["orchestrator", "executor", "critic", "guardian"]:
        model = auto_model(role, budget_usd=0.50, compliance="hipaa")
        print(f"  {role:16s} → {model}")
    model_tight = auto_model("executor", budget_usd=0.003)
    print(f"  {'executor (tight)':16s} → {model_tight}  (budget < $0.01 → haiku)")

    # ── 2. ReActAgent — agentic research loop ─────────────────────────────────
    print(f"\n{'='*60}")
    print("ReActAgent: agentic document research")

    @tool(name="search_regulation", description="Search HIPAA regulation text")
    def search_regulation(query: str) -> str:
        return (
            f"[Regulation result for '{query}']: "
            "45 CFR §164.502 — Minimum necessary standard. "
            "Covered entities must make reasonable efforts to limit PHI to the minimum "
            "necessary to accomplish the intended purpose of use, disclosure, or request."
        )

    researcher = Agent(
        name="hipaa-researcher",
        role="researcher",
        model=auto_model("researcher", compliance="hipaa"),
        tools=[search_regulation],
        system_prompt=(
            "You are a HIPAA compliance researcher. Use the search tool to look up regulations "
            "before answering. Be precise and cite the CFR section."
        ),
    )

    react = ReActAgent(researcher, max_steps=4, reflect_every=3)
    react_result = await react.run(
        "What does HIPAA require for the minimum-necessary standard when sharing "
        "patient records with a specialist for treatment purposes?"
    )

    print(f"  Steps taken : {react_result.steps_taken}")
    print(f"  Finished    : {react_result.finished}")
    print(f"  Tokens used : {react_result.total_tokens}")
    print(f"  Answer      : {react_result.answer[:120]}...")

    # ── 3. run_typed — structured output ──────────────────────────────────────
    print(f"\n{'='*60}")
    print("run_typed: structured Pydantic output")

    class PHIRiskAssessment(BaseModel):
        phi_fields_present: list[str]
        risk_level: str  # "low" | "medium" | "high"
        minimum_necessary: bool
        recommendation: str

    assessor = Agent(
        name="phi-assessor",
        role="executor",
        model=auto_model("executor", compliance="hipaa"),
        system_prompt=(
            "You are a PHI risk assessor. Analyse the document for protected health information."
        ),
    )

    doc_snippet = (
        "Patient John D., DOB 1974-03-15, SSN redacted, "
        "Dx: T2DM, HbA1c 7.2%. Referred to Dr. Smith (Endocrinology) for treatment consultation."
    )

    assessment = await assessor.run_typed(
        f"Assess this clinical document snippet for PHI risk:\n\n{doc_snippet}",
        PHIRiskAssessment,
    )
    print(f"  PHI fields  : {assessment.phi_fields_present}")
    print(f"  Risk level  : {assessment.risk_level}")
    print(f"  Min-nec OK  : {assessment.minimum_necessary}")
    print(f"  Recommend   : {assessment.recommendation}")

    # ── 4. Supervisor — multi-agent delegation ────────────────────────────────
    print(f"\n{'='*60}")
    print("Supervisor: orchestrated multi-specialist review")

    orchestrator = Agent(name="compliance-orch", role="orchestrator",
                         model=auto_model("orchestrator", compliance="hipaa"))
    phi_scanner  = Agent(name="phi-scanner",    role="executor",
                         model=auto_model("executor", compliance="hipaa"))
    risk_assessor = Agent(name="risk-assessor", role="critic",
                          model=auto_model("critic", compliance="hipaa"))

    sv = Supervisor(orchestrator, [phi_scanner, risk_assessor], max_rounds=2)
    sv_result = await sv.run(
        f"Perform a full HIPAA compliance review of this document:\n\n{doc_snippet}"
    )

    print(f"  Rounds      : {sv_result.rounds}")
    print(f"  Workers     : {list(sv_result.worker_outputs)}")
    print(f"  Answer      : {sv_result.final_answer[:120]}...")

    # ── 5. AdversarialTeam — hallucination-resistant verdict ─────────────────
    print(f"\n{'='*60}")
    print("AdversarialTeam: Proposer → Attacker → Judge")

    proposer = Agent(name="proposer", role="executor",
                     model=auto_model("executor", compliance="hipaa"))
    attacker = Agent(name="attacker", role="critic",
                     model=auto_model("critic", compliance="hipaa"))
    judge    = Agent(name="judge",    role="orchestrator",
                     model=auto_model("orchestrator", compliance="hipaa"))

    team = AdversarialTeam(proposer, attacker, judge, max_revisions=1)
    adv_result = await team.run(
        f"Is this clinical document HIPAA-compliant? Provide a definitive finding.\n\n{doc_snippet}"
    )

    print(f"  Verdict     : {adv_result.verdict.upper()}")
    print(f"  Final answer: {adv_result.final_answer[:120]}...")
    print(f"  Tokens      : {adv_result.total_tokens}")

    # ── 6. AgentSession — interactive Q&A ────────────────────────────────────
    print(f"\n{'='*60}")
    print("AgentSession: multi-turn follow-up Q&A")

    qa_agent  = Agent(name="qa-assistant", role="executor",
                      model=auto_model("executor", compliance="hipaa"),
                      memory=True)
    session = AgentSession(qa_agent, system_context="HIPAA compliance context established.")

    questions = [
        "What was the main finding of the compliance review?",
        "What remediation steps should we take?",
    ]
    for q in questions:
        r = await session.chat(q)
        print(f"  Q: {q}")
        print(f"  A: {r.reply[:100]}...")

    print(f"\n  Session total tokens: {session.total_tokens}")

    # ── 7. Governed mesh run ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Mesh(compliance='hipaa'): fully governed run")

    mesh = Mesh(compliance="hipaa")
    print(f"  Policy mode : {mesh._policy.mode.value}")
    print(f"  PHI scrub   : {mesh._policy.scrub_phi}")
    print(f"  HITL enabled: {mesh._policy.require_human_review}")
    print(f"  Budget      : ${mesh._policy.budget_usd:.2f}")

    print(f"\n{'='*60}")
    print("Demo complete.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
