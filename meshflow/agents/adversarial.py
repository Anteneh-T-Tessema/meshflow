"""AdversarialTeam — Proposer → Attacker → Judge.

The Proposer generates an answer.  The Attacker finds flaws, hallucinations, and
unsupported claims.  The Judge evaluates both sides and delivers a verdict.

This catches the class of errors that self-consistency cannot: a model that is
confidently wrong will still fail the adversarial probe.

Usage::

    from meshflow.agents.adversarial import AdversarialTeam
    from meshflow import Agent

    team = AdversarialTeam(
        proposer=Agent(name="proposer", role="executor"),
        attacker=Agent(name="attacker", role="critic"),
        judge=Agent(name="judge",    role="orchestrator"),
    )
    result = await team.run("Summarise GDPR Article 17 obligations")

    print(result.verdict)       # "accept" | "reject" | "revise"
    print(result.final_answer)  # the accepted or revised answer
    print(result.critique)      # what the attacker found
    print(result.reasoning)     # judge's explanation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from meshflow.agents.builder import Agent


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class AdversarialResult:
    verdict: Literal["accept", "reject", "revise"]
    final_answer: str
    proposal: str
    critique: str
    reasoning: str
    total_tokens: int
    total_cost_usd: float


# ── Prompts ───────────────────────────────────────────────────────────────────

_ATTACKER_SYSTEM = (
    "You are an adversarial critic. Your job is to find every flaw in the given answer:\n"
    "  - Factual errors or hallucinations\n"
    "  - Unsupported claims presented as facts\n"
    "  - Logical gaps or contradictions\n"
    "  - Missing critical information\n"
    "  - Overconfident or misleading statements\n\n"
    "Be thorough and adversarial. List every problem you find, even minor ones."
)

_JUDGE_SYSTEM = (
    "You are an impartial judge evaluating a proposal and its critique.\n\n"
    "Decide:\n"
    "  ACCEPT  — the proposal is correct and complete despite the critique\n"
    "  REJECT  — the proposal is fatally flawed and cannot be salvaged\n"
    "  REVISE  — the proposal has merit but needs the specific fixes listed in the critique\n\n"
    "Output JSON only:\n"
    '{"verdict": "accept"|"reject"|"revise", '
    '"reasoning": "...", '
    '"revised_answer": "...or empty string if accept/reject"}'
)


# ── AdversarialTeam ───────────────────────────────────────────────────────────

class AdversarialTeam:
    """Proposer → Attacker → Judge triple for hallucination-resistant answers.

    Parameters
    ----------
    proposer:
        Generates the initial answer.
    attacker:
        Finds flaws in the proposal.
    judge:
        Evaluates proposal + critique and delivers verdict.
    max_revisions:
        How many revise cycles to allow before accepting best effort (default 1).
    """

    def __init__(
        self,
        proposer: Agent,
        attacker: Agent,
        judge: Agent,
        max_revisions: int = 1,
    ) -> None:
        self._proposer = proposer
        self._attacker = attacker
        self._judge = judge
        self._max_revisions = max_revisions

    async def run(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AdversarialResult:
        ctx = context or {}
        total_tokens = 0
        total_cost = 0.0

        # ── Propose ───────────────────────────────────────────────────────────
        proposal_out = await self._proposer.run(task, ctx)
        total_tokens += proposal_out.get("tokens", 0)
        total_cost += proposal_out.get("cost_usd", 0.0)
        proposal = proposal_out.get("result", "")

        current_answer = proposal
        last_critique = ""
        last_reasoning = ""
        verdict: Literal["accept", "reject", "revise"] = "accept"

        for _ in range(self._max_revisions + 1):
            # ── Attack ────────────────────────────────────────────────────────
            attack_task = (
                f"Original task: {task}\n\n"
                f"Proposed answer:\n{current_answer}\n\n"
                "Find every flaw in this answer."
            )
            attack_out = await self._attacker.run(attack_task, ctx)
            total_tokens += attack_out.get("tokens", 0)
            total_cost += attack_out.get("cost_usd", 0.0)
            critique = attack_out.get("result", "")
            last_critique = critique

            # ── Judge ─────────────────────────────────────────────────────────
            judge_task = (
                f"Task: {task}\n\n"
                f"Proposed answer:\n{current_answer}\n\n"
                f"Critique:\n{critique}"
            )
            judge_out = await self._judge.run(judge_task, {"system_override": _JUDGE_SYSTEM})
            total_tokens += judge_out.get("tokens", 0)
            total_cost += judge_out.get("cost_usd", 0.0)
            judge_raw = judge_out.get("result", "")
            last_reasoning, verdict, current_answer = _parse_verdict(
                judge_raw, current_answer
            )

            if verdict in ("accept", "reject"):
                break

            # REVISE: loop with the revised answer as the new proposal

        return AdversarialResult(
            verdict=verdict,
            final_answer=current_answer,
            proposal=proposal,
            critique=last_critique,
            reasoning=last_reasoning,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
        )


# ── Parser ────────────────────────────────────────────────────────────────────

import json as _json
import re as _re


def _parse_verdict(
    raw: str,
    fallback_answer: str,
) -> tuple[str, Literal["accept", "reject", "revise"], str]:
    """Return (reasoning, verdict, answer) from judge output."""
    # Try JSON first
    m = _re.search(r"\{.*\}", raw, _re.DOTALL)
    if m:
        try:
            data = _json.loads(m.group())
            raw_verdict = str(data.get("verdict", "accept")).lower().strip()
            if raw_verdict not in ("accept", "reject", "revise"):
                raw_verdict = "accept"
            verdict: Literal["accept", "reject", "revise"] = raw_verdict  # type: ignore[assignment]
            reasoning = str(data.get("reasoning", ""))
            revised = str(data.get("revised_answer", "")).strip()
            answer = revised if (verdict == "revise" and revised) else fallback_answer
            return reasoning, verdict, answer
        except _json.JSONDecodeError:
            pass

    # Fallback: keyword scan
    lower = raw.lower()
    if "reject" in lower:
        return raw, "reject", fallback_answer
    if "revise" in lower:
        return raw, "revise", fallback_answer
    return raw, "accept", fallback_answer
