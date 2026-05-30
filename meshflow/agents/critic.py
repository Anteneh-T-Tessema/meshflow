"""CriticAgent — single-agent critic/challenger wrapper.

Wraps any Agent with a propose → challenge → refine loop. Lighter than
DebatePanel (which requires N debaters + arbiter). Designed for single-agent
quality improvement: code review, contract analysis, medical diagnosis,
compliance checking.

Unlike DebatePanel:
- Only two roles (proposer + critic), no arbiter required
- Configurable refinement turns
- Returns a CriticResult with full revision history
- Usable as a drop-in wrapper around any existing Agent

Usage::

    from meshflow import Agent
    from meshflow.agents.critic import CriticAgent

    analyst = Agent(name="analyst", role="researcher", model="claude-sonnet-4-6")
    critic  = Agent(name="critic",  role="critic",     model="claude-sonnet-4-6")

    wrapped = CriticAgent(proposer=analyst, critic=critic, max_refinements=2)
    result  = await wrapped.run("Analyse this contract for HIPAA violations.")

    print(result.final_answer)
    print(result.improvement_delta)   # quality score delta over refinement turns
    for turn in result.history:
        print(turn.role, turn.content[:80])

Configurable challenge prompt::

    wrapped = CriticAgent(
        proposer=analyst,
        critic=critic,
        challenge_prompt=(
            "Identify specific weaknesses, missing edge cases, or inaccuracies "
            "in the previous response. Be concise and constructive."
        ),
    )
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class CriticTurn:
    """A single turn in the propose-challenge-refine cycle."""

    turn_number: int
    role: str          # "proposal" | "critique" | "refinement"
    content: str
    confidence: float = 0.5
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn_number,
            "role": self.role,
            "content": self.content[:500],
            "confidence": round(self.confidence, 3),
            "latency_ms": round(self.latency_ms, 1),
        }


@dataclass
class CriticResult:
    """Output of a CriticAgent run.

    Attributes
    ----------
    final_answer:       The last refined answer from the proposer.
    history:            Full list of turns (proposal, critique, refinement …).
    refinements:        Number of refinement passes actually performed.
    initial_confidence: Estimated confidence of the first proposal (0–1).
    final_confidence:   Estimated confidence of the final refinement (0–1).
    improvement_delta:  ``final_confidence - initial_confidence``.
    total_cost_usd:     Aggregate LLM cost across all turns.
    total_tokens:       Aggregate token count.
    """

    final_answer: str
    history: list[CriticTurn] = field(default_factory=list)
    refinements: int = 0
    initial_confidence: float = 0.5
    final_confidence: float = 0.5
    total_cost_usd: float = 0.0
    total_tokens: int = 0

    @property
    def improvement_delta(self) -> float:
        return round(self.final_confidence - self.initial_confidence, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_answer": self.final_answer[:1000],
            "refinements": self.refinements,
            "initial_confidence": round(self.initial_confidence, 3),
            "final_confidence": round(self.final_confidence, 3),
            "improvement_delta": self.improvement_delta,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "history": [t.to_dict() for t in self.history],
        }


# ── Confidence extraction ─────────────────────────────────────────────────────

def _extract_confidence(text: str) -> float:
    """Extract a confidence score from LLM output.

    Looks for patterns like ``confidence: 0.85``, ``[0.9]``, ``score=0.7``,
    or ``I am 80% confident``. Falls back to 0.7 if nothing found.
    """
    patterns = [
        r"confidence[:\s=]+([0-9]\.[0-9]+)",
        r"\[([0-9]\.[0-9]+)\]",
        r"score[:\s=]+([0-9]\.[0-9]+)",
        r"([0-9]{1,3})\s*%\s*confident",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if val > 1.0:
                val = val / 100.0
            return min(max(val, 0.0), 1.0)
    return 0.7


# ── Default prompts ───────────────────────────────────────────────────────────

_DEFAULT_CHALLENGE_PROMPT = (
    "You are a rigorous critic. Review the following response and identify:\n"
    "1. Specific factual errors or omissions\n"
    "2. Logical gaps or unsupported claims\n"
    "3. Missing edge cases or caveats\n\n"
    "Be concise and constructive. Focus on the most important improvements.\n\n"
    "Response to review:\n{proposal}"
)

_DEFAULT_REFINE_PROMPT = (
    "You previously provided this answer:\n{proposal}\n\n"
    "A critic identified these issues:\n{critique}\n\n"
    "Please revise your answer to address all identified issues. "
    "Keep what was already correct. Be thorough and precise."
)


# ── CriticAgent ───────────────────────────────────────────────────────────────


class CriticAgent:
    """Wraps a proposer Agent with a critic/challenger refinement loop.

    Parameters
    ----------
    proposer:
        The primary Agent that generates proposals.
    critic:
        The Agent that challenges the proposal. If None, the same agent
        is reused with a critic system prompt override.
    max_refinements:
        Maximum number of propose → critique → refine cycles (default: 2).
    challenge_prompt:
        Template for the critique step. Use ``{proposal}`` as placeholder.
    refine_prompt:
        Template for the refinement step. Use ``{proposal}`` and ``{critique}``.
    stop_on_confidence:
        If the proposer's confidence score exceeds this threshold after any
        turn, stop early even if refinements remain (default: 0.92).
    """

    def __init__(
        self,
        proposer: Any,
        critic: Any | None = None,
        *,
        max_refinements: int = 2,
        challenge_prompt: str = _DEFAULT_CHALLENGE_PROMPT,
        refine_prompt: str = _DEFAULT_REFINE_PROMPT,
        stop_on_confidence: float = 0.92,
    ) -> None:
        self._proposer = proposer
        self._critic = critic or proposer
        self._max_refinements = max_refinements
        self._challenge_tmpl = challenge_prompt
        self._refine_tmpl = refine_prompt
        self._stop_conf = stop_on_confidence

    async def run(self, task: str, **kwargs: Any) -> CriticResult:
        """Run the propose → challenge → refine loop.

        Parameters
        ----------
        task:
            The task description to work on.
        **kwargs:
            Forwarded to the proposer and critic agents.

        Returns
        -------
        CriticResult
        """
        history: list[CriticTurn] = []
        total_cost = 0.0
        total_tokens = 0

        # ── Initial proposal ──────────────────────────────────────────────────
        t0 = time.monotonic()
        proposal_raw = await self._proposer.run(task, **kwargs)
        latency_ms = (time.monotonic() - t0) * 1000

        proposal = str(proposal_raw) if not isinstance(proposal_raw, str) else proposal_raw
        init_conf = _extract_confidence(proposal)
        history.append(CriticTurn(
            turn_number=1,
            role="proposal",
            content=proposal,
            confidence=init_conf,
            latency_ms=latency_ms,
        ))

        # Track cost/tokens if available
        if hasattr(proposal_raw, "_cost_usd"):
            total_cost += getattr(proposal_raw, "_cost_usd", 0.0)
        if hasattr(proposal_raw, "_tokens"):
            total_tokens += getattr(proposal_raw, "_tokens", 0)

        current_answer = proposal
        current_conf = init_conf

        # ── Refinement loop ───────────────────────────────────────────────────
        for i in range(self._max_refinements):
            if current_conf >= self._stop_conf:
                break

            # Challenge step
            challenge_text = self._challenge_tmpl.format(proposal=current_answer)
            t0 = time.monotonic()
            critique_raw = await self._critic.run(challenge_text, **kwargs)
            latency_ms = (time.monotonic() - t0) * 1000
            critique = str(critique_raw) if not isinstance(critique_raw, str) else critique_raw

            history.append(CriticTurn(
                turn_number=i * 2 + 2,
                role="critique",
                content=critique,
                confidence=_extract_confidence(critique),
                latency_ms=latency_ms,
            ))

            # Refinement step
            refine_text = self._refine_tmpl.format(
                proposal=current_answer, critique=critique
            )
            t0 = time.monotonic()
            refined_raw = await self._proposer.run(refine_text, **kwargs)
            latency_ms = (time.monotonic() - t0) * 1000
            refined = str(refined_raw) if not isinstance(refined_raw, str) else refined_raw
            new_conf = _extract_confidence(refined)

            history.append(CriticTurn(
                turn_number=i * 2 + 3,
                role="refinement",
                content=refined,
                confidence=new_conf,
                latency_ms=latency_ms,
            ))

            current_answer = refined
            current_conf = new_conf

        return CriticResult(
            final_answer=current_answer,
            history=history,
            refinements=len([t for t in history if t.role == "refinement"]),
            initial_confidence=init_conf,
            final_confidence=current_conf,
            total_cost_usd=total_cost,
            total_tokens=total_tokens,
        )


__all__ = ["CriticAgent", "CriticResult", "CriticTurn"]
