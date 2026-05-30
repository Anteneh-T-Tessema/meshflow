"""RoleRouter — LLM-driven dynamic role assignment.

First-mover capability: neither CrewAI nor any of the six competing frameworks
supports LLM-driven role assignment where an orchestrator decides which agent
role to create dynamically based on task content.

The RoleRouter accepts a task description and emits an AgentSpec (role, goal,
tools, model tier) by reasoning about the task. The returned spec can be used
to instantiate a fresh Agent at runtime — enabling workflows that adapt their
agent roster based on what the task actually requires.

Usage::

    from meshflow.agents.role_router import RoleRouter, AgentSpec

    router = RoleRouter(orchestrator_agent)
    spec = await router.route("Analyse CVE-2025-59528 and propose a patch")

    print(spec.role)         # "security_researcher"
    print(spec.tools)        # ["web_search", "code_interpreter"]
    print(spec.model_tier)   # "large"

    # Instantiate the agent from the spec
    agent = spec.to_agent()
    result = await agent.run(spec.goal)

Roles available::

    planner, researcher, executor, critic, orchestrator, guardian,
    security_researcher, compliance_analyst, data_analyst,
    code_reviewer, legal_analyst, medical_advisor, financial_analyst

YAML-configurable::

    role_router:
      available_roles:
        - researcher
        - executor
        - critic
      default_role: executor
      model: claude-sonnet-4-6
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


# ── AgentSpec ─────────────────────────────────────────────────────────────────


@dataclass
class AgentSpec:
    """Dynamically generated agent specification.

    Attributes
    ----------
    role:       Chosen agent role (e.g. ``"security_researcher"``).
    goal:       Task-specific goal for this agent.
    tools:      Recommended tool names for this task.
    model_tier: Recommended model tier (``"nano"`` / ``"small"`` / ``"medium"`` / ``"large"``).
    rationale:  LLM's reasoning for this role selection.
    confidence: Confidence in the role selection (0–1).
    """

    role: str
    goal: str
    tools: list[str] = field(default_factory=list)
    model_tier: str = "medium"
    rationale: str = ""
    confidence: float = 0.8

    # Model tier → model string mapping
    _TIER_MODELS: dict[str, str] = field(default_factory=lambda: {
        "nano":   "claude-haiku-4-5-20251001",
        "small":  "claude-haiku-4-5-20251001",
        "medium": "claude-sonnet-4-6",
        "large":  "claude-opus-4-8",
    })

    def to_agent(self, name: str | None = None) -> Any:
        """Instantiate a MeshFlow Agent from this spec."""
        from meshflow import Agent
        agent_name = name or f"{self.role}-{id(self)}"
        model = self._TIER_MODELS.get(self.model_tier, "claude-sonnet-4-6")
        return Agent(
            name=agent_name,
            role=self.role,
            model=model,
            tools=self.tools,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "goal": self.goal[:300],
            "tools": self.tools,
            "model_tier": self.model_tier,
            "rationale": self.rationale[:300],
            "confidence": round(self.confidence, 3),
        }


# ── Available roles and their tool recommendations ─────────────────────────


_ROLE_CATALOGUE: dict[str, dict[str, Any]] = {
    "planner": {
        "description": "High-level task decomposition and workflow planning",
        "tools": [],
        "keywords": ["plan", "decompose", "structure", "outline", "coordinate"],
        "tier": "medium",
    },
    "researcher": {
        "description": "Information gathering, literature review, web research",
        "tools": ["web_search", "document_reader"],
        "keywords": ["research", "find", "search", "discover", "investigate", "analyse"],
        "tier": "medium",
    },
    "executor": {
        "description": "Task execution based on clear instructions",
        "tools": [],
        "keywords": ["execute", "run", "perform", "complete", "do"],
        "tier": "small",
    },
    "critic": {
        "description": "Quality review, error detection, output validation",
        "tools": [],
        "keywords": ["review", "critique", "validate", "check", "audit", "verify"],
        "tier": "medium",
    },
    "orchestrator": {
        "description": "Multi-agent coordination and task delegation",
        "tools": [],
        "keywords": ["coordinate", "delegate", "manage", "orchestrate", "assign"],
        "tier": "large",
    },
    "guardian": {
        "description": "Policy enforcement, compliance checking, safety monitoring",
        "tools": [],
        "keywords": ["compliance", "policy", "safety", "enforce", "monitor", "guard"],
        "tier": "medium",
    },
    "security_researcher": {
        "description": "CVE analysis, penetration testing, security audit",
        "tools": ["web_search", "code_interpreter"],
        "keywords": ["cve", "vulnerability", "security", "exploit", "patch", "pentest"],
        "tier": "large",
    },
    "compliance_analyst": {
        "description": "HIPAA, SOC2, GDPR, PCI compliance analysis",
        "tools": ["document_reader"],
        "keywords": ["hipaa", "soc2", "gdpr", "pci", "compliance", "regulatory", "audit trail"],
        "tier": "large",
    },
    "data_analyst": {
        "description": "Data processing, statistical analysis, visualisation",
        "tools": ["python_repl", "calculator"],
        "keywords": ["data", "statistics", "analyse", "chart", "metric", "dataset", "csv"],
        "tier": "medium",
    },
    "code_reviewer": {
        "description": "Code quality review, bug detection, refactoring suggestions",
        "tools": ["code_interpreter"],
        "keywords": ["code", "review", "bug", "refactor", "function", "class", "python", "typescript"],
        "tier": "large",
    },
    "legal_analyst": {
        "description": "Contract analysis, legal risk assessment, clause extraction",
        "tools": ["document_reader"],
        "keywords": ["contract", "legal", "clause", "liability", "agreement", "terms"],
        "tier": "large",
    },
    "medical_advisor": {
        "description": "Medical literature review, clinical guidance (non-diagnostic)",
        "tools": ["web_search", "document_reader"],
        "keywords": ["medical", "clinical", "diagnosis", "treatment", "medication", "healthcare"],
        "tier": "large",
    },
    "financial_analyst": {
        "description": "Financial modelling, risk analysis, investment research",
        "tools": ["python_repl", "calculator", "web_search"],
        "keywords": ["financial", "revenue", "roi", "investment", "budget", "forecast", "market"],
        "tier": "large",
    },
}


# ── Heuristic role selection ──────────────────────────────────────────────────


def _heuristic_role(task: str) -> tuple[str, float]:
    """Fast keyword-based role selection as a fallback when no LLM is available."""
    task_lower = task.lower()
    best_role = "executor"
    best_score = 0
    for role, info in _ROLE_CATALOGUE.items():
        score = sum(1 for kw in info["keywords"] if kw in task_lower)
        if score > best_score:
            best_score = score
            best_role = role
    confidence = min(0.5 + best_score * 0.1, 0.85)
    return best_role, confidence


# ── RoleRouter ────────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """You are a role assignment specialist for a multi-agent AI system.
Given a task description, select the most appropriate agent role from the available catalogue
and return a JSON response.

Available roles: {roles}

Return ONLY valid JSON with this exact structure:
{{
  "role": "<role_name>",
  "goal": "<refined task goal for the agent>",
  "tools": ["<tool1>", "<tool2>"],
  "model_tier": "<nano|small|medium|large>",
  "rationale": "<1-2 sentence reasoning>",
  "confidence": <0.0-1.0>
}}"""


class RoleRouter:
    """Dynamically assigns agent roles using LLM reasoning.

    Parameters
    ----------
    orchestrator:
        The Agent used to reason about role selection. Typically a
        medium-tier model is sufficient.
    available_roles:
        Restrict routing to a subset of roles. Defaults to all 13 roles.
    default_role:
        Fallback role if the LLM response cannot be parsed (default: ``"executor"``).
    use_heuristic_fallback:
        If True, use keyword heuristics when the LLM fails (default: True).
    """

    def __init__(
        self,
        orchestrator: Any | None = None,
        *,
        available_roles: list[str] | None = None,
        default_role: str = "executor",
        use_heuristic_fallback: bool = True,
    ) -> None:
        self._agent = orchestrator
        self._roles = available_roles or list(_ROLE_CATALOGUE.keys())
        self._default = default_role
        self._use_heuristic = use_heuristic_fallback

    async def route(self, task: str) -> AgentSpec:
        """Classify *task* and return an AgentSpec.

        Parameters
        ----------
        task:
            The task description to classify.

        Returns
        -------
        AgentSpec with role, goal, tools, model_tier, and rationale.
        """
        if self._agent is not None:
            try:
                return await self._llm_route(task)
            except Exception:
                pass

        if self._use_heuristic:
            return self._heuristic_route(task)

        # Hard fallback — default role
        role_info = _ROLE_CATALOGUE.get(self._default, {})
        return AgentSpec(
            role=self._default,
            goal=task,
            tools=list(role_info.get("tools", [])),
            model_tier=role_info.get("tier", "medium"),
            rationale="Default fallback role",
            confidence=0.5,
        )

    async def _llm_route(self, task: str) -> AgentSpec:
        """Use the orchestrator LLM to select a role."""
        roles_str = ", ".join(self._roles)
        prompt = (
            f"Task description: {task}\n\n"
            "Select the most appropriate agent role and return JSON."
        )
        system = _SYSTEM_PROMPT.format(roles=roles_str)

        provider = getattr(self._agent, "_provider", None) or getattr(self._agent, "provider", None)
        if provider is None:
            raise RuntimeError("Orchestrator agent has no provider")

        text, _, _ = await provider.complete(
            model=getattr(self._agent, "model", "claude-sonnet-4-6"),
            messages=[{"role": "user", "content": prompt}],
            system=system,
            max_tokens=512,
        )

        # Parse JSON from response
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found in role router response")

        data = json.loads(json_match.group())
        role = data.get("role", self._default)
        if role not in _ROLE_CATALOGUE:
            role = self._default

        role_info = _ROLE_CATALOGUE.get(role, {})
        return AgentSpec(
            role=role,
            goal=data.get("goal", task),
            tools=data.get("tools", list(role_info.get("tools", []))),
            model_tier=data.get("model_tier", role_info.get("tier", "medium")),
            rationale=data.get("rationale", ""),
            confidence=float(data.get("confidence", 0.8)),
        )

    def _heuristic_route(self, task: str) -> AgentSpec:
        """Keyword-based role selection — no LLM required."""
        role, confidence = _heuristic_role(task)
        if role not in self._roles:
            role = self._default
        role_info = _ROLE_CATALOGUE.get(role, {})
        return AgentSpec(
            role=role,
            goal=task,
            tools=list(role_info.get("tools", [])),
            model_tier=role_info.get("tier", "medium"),
            rationale=f"Keyword heuristic: matched patterns for '{role}'",
            confidence=confidence,
        )

    def catalogue(self) -> dict[str, dict[str, Any]]:
        """Return the full role catalogue for introspection."""
        return {r: _ROLE_CATALOGUE[r] for r in self._roles if r in _ROLE_CATALOGUE}


__all__ = ["RoleRouter", "AgentSpec"]
