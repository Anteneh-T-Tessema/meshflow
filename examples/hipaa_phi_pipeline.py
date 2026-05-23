"""HIPAA-compliant clinical note processing pipeline.

Demonstrates MeshFlow's hipaa policy mode:
  - PHI scrubbing before every ledger write
  - Mandatory human-in-the-loop for irreversible outputs
  - Immutable SHA-256 audit chain
  - Budget + step caps

Run (no API key needed — uses simulated providers):
    python examples/hipaa_phi_pipeline.py

Run with real Claude:
    ANTHROPIC_API_KEY=sk-ant-... python examples/hipaa_phi_pipeline.py
"""
from __future__ import annotations

import asyncio

from meshflow.core.mesh import Mesh
from meshflow.core.schemas import policy_for_mode


# ── Sample clinical note (contains PHI for demo purposes) ─────────────────────

CLINICAL_NOTE = """\
Patient: John Smith, DOB 1975-03-12, MRN 4821-09
Phone: (555) 867-5309  SSN: 123-45-6789
Chief Complaint: Persistent cough for 3 weeks.
Assessment: Possible community-acquired pneumonia.
Plan: Start amoxicillin 500mg TID x 7 days.
Follow-up in 1 week or sooner if symptoms worsen.
"""


async def run_hipaa_pipeline() -> None:
    print("=" * 60)
    print("MeshFlow — HIPAA Mode Demo")
    print("=" * 60)

    # hipaa preset: scrub_phi=True, require_human_review=True,
    #               immutable_audit=True, enable_guardian=True
    policy = policy_for_mode(
        "hipaa",
        budget_usd=2.0,
        max_steps=5,
    )

    print(f"\nPolicy mode : {policy.mode.value}")
    print(f"PHI scrub   : {getattr(policy, 'scrub_phi', True)}")
    print(f"Immutable   : {getattr(policy, 'immutable_audit', True)}")
    print(f"Budget      : ${policy.budget_usd:.2f}")

    print("\n--- Input (contains PHI) ---")
    print(CLINICAL_NOTE.strip())

    task = (
        "Summarise the clinical note below for the discharge letter. "
        "Do not include the patient's name, date of birth, SSN, MRN, or phone number. "
        "Return only a 2-3 sentence clinical summary.\n\n"
        f"Note:\n{CLINICAL_NOTE}"
    )

    mesh = Mesh(policy=policy)

    print("\n--- Running pipeline ---")
    result = await mesh.run(task)

    print(f"\nStatus        : {result.status}")
    print(f"Total tokens  : {result.total_tokens}")
    print(f"Total cost    : ${result.total_cost_usd:.6f}")
    print(f"Ledger entries: {result.ledger_entries}")
    print(f"Run ID        : {result.run_id}")

    if result.status == "paused":
        print("\n[HITL] Run paused for human review — no PHI leaves without approval.")
        print(f"  meshflow hitl approve {result.run_id} --reviewer dr-chen")

    if result.output:
        print("\n--- Scrubbed output ---")
        print(str(result.output)[:500])

    if result.error:
        print(f"\n[Note] {result.error}")

    # Verify audit chain
    print("\n--- Audit chain ---")
    if result.run_id:
        print(f"  Run ID : {result.run_id}")
        print(f"  Verify : meshflow trace {result.run_id}")
    print("  PHI scrubber applied to all ledger writes — no raw PHI persisted.")


if __name__ == "__main__":
    asyncio.run(run_hipaa_pipeline())
