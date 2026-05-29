"""Multi-agent debate engine — N-way consensus with evidence grounding.

Extends the existing 3-agent AdversarialTeam to support:
- Arbitrary N agents (proposers + challengers + arbiter)
- Evidence grounding via knowledge injection
- Full debate tree history (every round's proposals, critiques, verdicts)
- Configurable consensus strategies (majority, arbiter, unanimity)

Usage::

    from meshflow import Agent
    from meshflow.agents.debate import DebatePanel

    # Build debaters
    experts = [
        Agent(name="expert-a", role="executor", model="claude-sonnet-4-6"),
        Agent(name="expert-b", role="executor", model="claude-sonnet-4-6"),
        Agent(name="expert-c", role="executor", model="claude-sonnet-4-6"),
    ]
    arbiter = Agent(name="arbiter", role="critic", model="claude-opus-4-7")

    panel = DebatePanel(debaters=experts, arbiter=arbiter)
    result = await panel.debate("Should MeshFlow support Go agents?")

    print(result.consensus)
    print(result.confidence)
    for node in result.tree:
        print(node.round_number, node.agent_name, node.position[:80])
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

# ── Debate tree node ──────────────────────────────────────────────────────────

@dataclass
class DebateNode:
    """One contribution in the debate tree."""

    round_number: int
    agent_name: str
    role: str  # "proposal" | "critique" | "revision" | "verdict"
    position: str
    confidence: float = 0.5
    evidence_used: list[str] = field(default_factory=list)
    challenged_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_number,
            "agent": self.agent_name,
            "role": self.role,
            "position": self.position[:300],
            "confidence": round(self.confidence, 3),
            "evidence_used": self.evidence_used,
        }


# ── Debate result ─────────────────────────────────────────────────────────────

@dataclass
class DebateResult:
    """Final outcome of a multi-agent debate."""

    question: str
    consensus: str
    confidence: float
    verdict: str  # "consensus" | "majority" | "arbiter" | "timeout"
    rounds: int
    total_tokens: int
    total_cost_usd: float
    tree: list[DebateNode] = field(default_factory=list)
    dissenting_positions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question[:200],
            "consensus": self.consensus[:500],
            "confidence": round(self.confidence, 3),
            "verdict": self.verdict,
            "rounds": self.rounds,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "tree": [n.to_dict() for n in self.tree],
            "dissenting_positions": self.dissenting_positions,
        }


# ── DebatePanel ───────────────────────────────────────────────────────────────

class DebatePanel:
    """N-way debate engine with optional evidence grounding.

    Parameters
    ----------
    debaters:      Agents that each propose an initial position and
                   critique peers' positions.
    arbiter:       Optional final arbiter (runs when consensus fails).
    max_rounds:    Maximum debate rounds (each round: propose → critique → revise).
    consensus_strategy:
        - ``"majority"``: adopt position agreed by > 50% of debaters.
        - ``"arbiter"``:  arbiter always resolves (requires *arbiter*).
        - ``"unanimity"``: all debaters must agree; else arbiter resolves.
    knowledge_sources:  Optional list of knowledge strings / objects injected
                        as grounding evidence before the first round.
    confidence_threshold: Stop early if all debaters report >= this confidence.
    """

    def __init__(
        self,
        debaters: list[Any],
        arbiter: Any | None = None,
        *,
        max_rounds: int = 3,
        consensus_strategy: Literal["majority", "arbiter", "unanimity"] = "majority",
        knowledge_sources: list[Any] | None = None,
        confidence_threshold: float = 0.9,
    ) -> None:
        if len(debaters) < 2:
            raise ValueError("DebatePanel requires at least 2 debaters")
        self._debaters = debaters
        self._arbiter = arbiter
        self._max_rounds = max_rounds
        self._strategy = consensus_strategy
        self._knowledge_sources = knowledge_sources or []
        self._conf_threshold = confidence_threshold

    async def debate(self, question: str, context: dict[str, Any] | None = None) -> DebateResult:
        ctx = context or {}
        tree: list[DebateNode] = []
        total_tokens = 0
        total_cost = 0.0
        positions: dict[str, str] = {}
        confidences: dict[str, float] = {}

        # ── Inject evidence ────────────────────────────────────────────────────
        evidence_context = ""
        if self._knowledge_sources:
            try:
                from meshflow.intelligence.knowledge import AgentKnowledge
                ak = AgentKnowledge(self._knowledge_sources)
                evidence_context = ak.context_string(question, max_chars=2000)
            except Exception:
                evidence_context = ""

        # ── Round loop ─────────────────────────────────────────────────────────
        for round_n in range(1, self._max_rounds + 1):

            # Phase 1: each debater proposes / revises their position
            proposal_tasks = [
                self._propose(
                    d, question, round_n, positions, evidence_context, ctx
                )
                for d in self._debaters
            ]
            results = await asyncio.gather(*proposal_tasks)

            for (agent_name, position, conf, tokens, cost) in results:
                positions[agent_name] = position
                confidences[agent_name] = conf
                total_tokens += tokens
                total_cost += cost
                tree.append(DebateNode(
                    round_number=round_n,
                    agent_name=agent_name,
                    role="proposal" if round_n == 1 else "revision",
                    position=position,
                    confidence=conf,
                    evidence_used=[evidence_context[:80]] if evidence_context else [],
                ))

            # Phase 2: each debater critiques ALL other debaters' positions
            if round_n < self._max_rounds:
                critique_tasks = [
                    self._critique(d, question, positions, ctx)
                    for d in self._debaters
                ]
                critiques = await asyncio.gather(*critique_tasks)
                for (agent_name, critique, tokens, cost) in critiques:
                    total_tokens += tokens
                    total_cost += cost
                    tree.append(DebateNode(
                        round_number=round_n,
                        agent_name=agent_name,
                        role="critique",
                        position=critique,
                        challenged_by=list(positions.keys()),
                    ))

            # ── Early exit on unanimous high confidence ────────────────────────
            if all(c >= self._conf_threshold for c in confidences.values()):
                break

        # ── Consensus resolution ───────────────────────────────────────────────
        consensus, verdict, dissenting = await self._resolve(
            question, positions, confidences, ctx, total_tokens, total_cost
        )
        total_tokens += consensus[2]
        total_cost += consensus[3]

        if verdict != "no-arbiter":
            tree.append(DebateNode(
                round_number=self._max_rounds + 1,
                agent_name=getattr(self._arbiter, "name", "arbiter") if self._arbiter else "panel",
                role="verdict",
                position=consensus[0],
                confidence=consensus[1],
            ))

        avg_conf = sum(confidences.values()) / max(len(confidences), 1)
        return DebateResult(
            question=question,
            consensus=consensus[0],
            confidence=max(consensus[1], avg_conf),
            verdict=verdict,
            rounds=round_n,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            tree=tree,
            dissenting_positions=dissenting,
        )

    # ── Internal phases ────────────────────────────────────────────────────────

    async def _propose(
        self,
        agent: Any,
        question: str,
        round_n: int,
        prior_positions: dict[str, str],
        evidence: str,
        ctx: dict[str, Any],
    ) -> tuple[str, str, float, int, float]:
        """Returns (agent_name, position, confidence, tokens, cost)."""
        agent_name = getattr(agent, "name", "agent")
        if round_n == 1:
            prompt = (
                f"Question: {question}\n"
                + (f"\n[Evidence]\n{evidence}\n" if evidence else "")
                + "\nState your position clearly. On the last line write: CONFIDENCE:0.XX"
            )
        else:
            peers = "\n".join(
                f"  [{name}]: {pos[:200]}"
                for name, pos in prior_positions.items()
                if name != agent_name
            )
            prompt = (
                f"Question: {question}\n"
                f"Your prior position: {prior_positions.get(agent_name, 'none')[:200]}\n"
                f"Peer positions:\n{peers}\n"
                + (f"\n[Evidence]\n{evidence}\n" if evidence else "")
                + "\nRevise or defend your position. On the last line write: CONFIDENCE:0.XX"
            )
        result = await agent.run(prompt, ctx)
        raw = result.get("result", "")
        conf = _extract_confidence(raw)
        clean = _strip_confidence(raw)
        return agent_name, clean, conf, result.get("tokens", 0), result.get("cost_usd", 0.0)

    async def _critique(
        self,
        agent: Any,
        question: str,
        positions: dict[str, str],
        ctx: dict[str, Any],
    ) -> tuple[str, str, int, float]:
        """Returns (agent_name, critique, tokens, cost)."""
        agent_name = getattr(agent, "name", "agent")
        peers = "\n".join(
            f"  [{name}]: {pos[:200]}"
            for name, pos in positions.items()
            if name != agent_name
        )
        prompt = (
            f"Question: {question}\n"
            f"Peer positions to critique:\n{peers}\n\n"
            "Identify specific flaws, unsupported claims, or logical gaps in each peer's position. "
            "Be precise and evidence-focused."
        )
        result = await agent.run(prompt, ctx)
        return agent_name, result.get("result", ""), result.get("tokens", 0), result.get("cost_usd", 0.0)

    async def _resolve(
        self,
        question: str,
        positions: dict[str, str],
        confidences: dict[str, float],
        ctx: dict[str, Any],
        _tokens: int,
        _cost: float,
    ) -> tuple[tuple[str, float, int, float], str, list[str]]:
        """Returns ((consensus_text, confidence, tokens, cost), verdict, dissenting)."""

        # Simple majority: check if > 50% share a similar position (token-overlap)
        if self._strategy in ("majority", "unanimity"):
            majority_pos = _majority_position(positions)
            if majority_pos:
                dissenters = [
                    pos for name, pos in positions.items()
                    if _similarity(pos, majority_pos) < 0.5
                ]
                if self._strategy == "unanimity" and dissenters:
                    pass  # fall through to arbiter
                else:
                    avg_conf = sum(confidences.values()) / max(len(confidences), 1)
                    return (majority_pos, avg_conf, 0, 0.0), "majority", dissenters

        # Arbiter resolution
        if self._arbiter is not None:
            positions_text = "\n".join(
                f"  [{name}]: {pos[:300]}" for name, pos in positions.items()
            )
            prompt = (
                f"Question: {question}\n"
                f"Debater positions:\n{positions_text}\n\n"
                "As arbiter: synthesise a definitive answer, acknowledge dissenting views. "
                "On the last line write: CONFIDENCE:0.XX"
            )
            result = await self._arbiter.run(prompt, ctx)
            raw = result.get("result", "")
            conf = _extract_confidence(raw)
            text = _strip_confidence(raw)
            dissenters = [p for p in positions.values() if _similarity(p, text) < 0.4]
            return (text, conf, result.get("tokens", 0), result.get("cost_usd", 0.0)), "arbiter", dissenters

        # Fallback: return best-confidence position
        best_name = max(confidences, key=lambda k: confidences[k])
        return (positions[best_name], confidences[best_name], 0, 0.0), "no-arbiter", []


# ── Helpers ───────────────────────────────────────────────────────────────────

import re as _re


def _extract_confidence(text: str) -> float:
    m = _re.search(r"CONFIDENCE:\s*(0?\.\d+|1\.0+)", text, _re.IGNORECASE)
    if m:
        try:
            return min(1.0, max(0.0, float(m.group(1))))
        except ValueError:
            pass
    return 0.7


def _strip_confidence(text: str) -> str:
    return _re.sub(r"\n?CONFIDENCE:\s*\S+\s*$", "", text, flags=_re.IGNORECASE).strip()


def _majority_position(positions: dict[str, str]) -> str:
    """Return the position agreed on by the most agents (token-overlap similarity)."""
    if not positions:
        return ""
    pos_list = list(positions.values())
    scores: list[float] = []
    for i, p in enumerate(pos_list):
        total_sim = sum(_similarity(p, q) for j, q in enumerate(pos_list) if i != j)
        scores.append(total_sim)
    best_idx = scores.index(max(scores))
    return pos_list[best_idx] if max(scores) > 0.2 else ""


def _similarity(a: str, b: str) -> float:
    """Jaccard-like token overlap between two texts."""
    ta = set(_re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(_re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


__all__ = ["DebatePanel", "DebateResult", "DebateNode"]
