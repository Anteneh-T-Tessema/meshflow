"""AgentSkillRegistry — declarative skill metadata for LLM-driven delegation.

Closes the CrewAI dynamic-role-delegation gap: instead of picking a worker by
name, the supervisor asks this registry "which agent has the skill best matching
this task?" using BM25 over skill descriptions.

Usage::

    from meshflow.agents.skill_registry import AgentSkillRegistry, AgentSkillProfile

    registry = AgentSkillRegistry()
    registry.register(AgentSkillProfile(
        agent_name="data-analyst",
        skills=["pandas", "sql", "data_visualization"],
        description="Expert in tabular data analysis and visualisation with SQL and Python.",
        proficiency={"pandas": 0.95, "sql": 0.90, "data_visualization": 0.80},
    ))
    registry.register(AgentSkillProfile(
        agent_name="legal-reviewer",
        skills=["contract_review", "compliance", "gdpr"],
        description="Expert in legal document review, contract analysis, and GDPR compliance.",
    ))

    # BM25 selection
    best = registry.select_best("analyse the sales CSV and chart top products")
    print(best.agent_name)  # "data-analyst"

    # LLM-driven selection (uses an Agent to reason over the registry)
    best = await registry.select_llm(
        task="check this NDA for GDPR violations",
        selector_agent=orchestrator_agent,
    )
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


# ── Profile ───────────────────────────────────────────────────────────────────

@dataclass
class AgentSkillProfile:
    """Describes one agent's skills and area of expertise."""

    agent_name: str
    skills: list[str] = field(default_factory=list)
    description: str = ""
    proficiency: dict[str, float] = field(default_factory=dict)  # skill → 0.0–1.0
    max_concurrent_tasks: int = 1
    preferred_model: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def _searchable_text(self) -> str:
        return " ".join([self.agent_name, self.description] + self.skills + self.tags)


# ── BM25 helpers ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _bm25(query_tokens: list[str], doc_tokens: list[str], avg_len: float, n: int, df: dict[str, int]) -> float:
    K1, B = 1.5, 0.75
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    dlen = len(doc_tokens)
    score = 0.0
    for qt in query_tokens:
        f = tf.get(qt, 0)
        if not f:
            continue
        idf = math.log((n - df.get(qt, 0) + 0.5) / (df.get(qt, 0) + 0.5) + 1)
        score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dlen / max(avg_len, 1)))
    return score


# ── Registry ──────────────────────────────────────────────────────────────────

class AgentSkillRegistry:
    """BM25 + LLM-driven agent selection from a skill profile registry."""

    def __init__(self) -> None:
        self._profiles: dict[str, AgentSkillProfile] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, profile: AgentSkillProfile) -> None:
        self._profiles[profile.agent_name] = profile

    def unregister(self, agent_name: str) -> None:
        self._profiles.pop(agent_name, None)

    def get(self, agent_name: str) -> AgentSkillProfile | None:
        return self._profiles.get(agent_name)

    def list_profiles(self) -> list[AgentSkillProfile]:
        return list(self._profiles.values())

    # ── BM25 selection ─────────────────────────────────────────────────────────

    def select_best(self, task: str, top_k: int = 1) -> AgentSkillProfile | None:
        """Return the agent(s) whose skill profile best matches *task* via BM25."""
        profiles = list(self._profiles.values())
        if not profiles:
            return None

        q_tokens = _tokenize(task)
        docs = [_tokenize(p._searchable_text) for p in profiles]
        n = len(docs)
        avg_len = sum(len(d) for d in docs) / max(n, 1)
        df: dict[str, int] = {}
        for doc in docs:
            for tok in set(doc):
                df[tok] = df.get(tok, 0) + 1

        scored = [
            (_bm25(q_tokens, doc, avg_len, n, df), p)
            for p, doc in zip(profiles, docs)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        best = [p for _, p in scored[:top_k] if _ > 0]
        return best[0] if best else profiles[0]

    def rank_all(self, task: str) -> list[tuple[float, AgentSkillProfile]]:
        """Return all profiles sorted by BM25 relevance to *task*."""
        profiles = list(self._profiles.values())
        if not profiles:
            return []
        q_tokens = _tokenize(task)
        docs = [_tokenize(p._searchable_text) for p in profiles]
        n = len(docs)
        avg_len = sum(len(d) for d in docs) / max(n, 1)
        df: dict[str, int] = {}
        for doc in docs:
            for tok in set(doc):
                df[tok] = df.get(tok, 0) + 1
        scored = [
            (_bm25(q_tokens, doc, avg_len, n, df), p)
            for p, doc in zip(profiles, docs)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    # ── LLM-driven selection ───────────────────────────────────────────────────

    async def select_llm(
        self,
        task: str,
        selector_agent: Any,
        *,
        top_candidates: int = 3,
    ) -> AgentSkillProfile | None:
        """Ask *selector_agent* to pick the best agent for *task*.

        First narrows to the top *top_candidates* via BM25, then calls the
        LLM to make the final pick.  Returns the LLM-chosen profile.
        """
        profiles = list(self._profiles.values())
        if not profiles:
            return None

        # Narrow to BM25 top-K
        ranked = self.rank_all(task)[:top_candidates]
        if not ranked:
            return profiles[0]

        candidates_text = "\n".join(
            f"- {p.agent_name}: {p.description} (skills: {', '.join(p.skills)})"
            for _, p in ranked
        )

        prompt = (
            f"Task: {task}\n\n"
            f"Available agents (pre-filtered by relevance):\n{candidates_text}\n\n"
            f"Which single agent name is BEST suited to this task? "
            f"Reply with ONLY the agent name — no explanation."
        )

        result = await selector_agent.run(prompt)
        chosen_name = (result.get("result", "") or "").strip().split()[0].rstrip(".,:;")

        # Match back to a profile
        if chosen_name in self._profiles:
            return self._profiles[chosen_name]

        # Fuzzy fallback: partial match
        for name, profile in self._profiles.items():
            if chosen_name.lower() in name.lower() or name.lower() in chosen_name.lower():
                return profile

        # Final fallback: BM25 best
        return ranked[0][1] if ranked else profiles[0]

    # ── Registry summary ──────────────────────────────────────────────────────

    def describe(self) -> str:
        lines = [f"AgentSkillRegistry ({len(self._profiles)} agents):"]
        for p in self._profiles.values():
            lines.append(f"  {p.agent_name}: {', '.join(p.skills)}")
        return "\n".join(lines)


__all__ = ["AgentSkillProfile", "AgentSkillRegistry"]
