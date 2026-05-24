"""Built-in skill library for MeshFlow agents.

Skills augment an agent's system prompt with domain-specific capability
descriptions, similar to backstory/goal tuning in CrewAI.

Usage:
    from meshflow import Agent
    from meshflow.agents.skills import SKILLS, skill_prompt

    agent = Agent(
        name="analyst",
        role="researcher",
        skills=["python", "data_analysis", "sql"],
    )
    # → system prompt automatically gains Python + data analysis + SQL context

    # Or inspect the built-in library:
    print(SKILLS.keys())
    print(skill_prompt(["python", "security"]))
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    """A named capability that augments an agent's system prompt."""

    name: str
    description: str   # displayed in agent system prompt
    tags: tuple[str, ...] = ()


# ── Built-in skill library ────────────────────────────────────────────────────

SKILLS: dict[str, Skill] = {
    "python": Skill(
        name="python",
        description=(
            "You are an expert Python programmer. Write idiomatic, PEP-8-compliant, "
            "type-annotated code. Prefer standard library solutions. Include docstrings "
            "for public functions and handle edge cases explicitly."
        ),
        tags=("programming", "backend"),
    ),
    "javascript": Skill(
        name="javascript",
        description=(
            "You are an expert JavaScript/TypeScript developer. Write modern ES2022+ code, "
            "prefer functional patterns, and use TypeScript types wherever possible."
        ),
        tags=("programming", "frontend"),
    ),
    "data_analysis": Skill(
        name="data_analysis",
        description=(
            "You excel at data analysis, statistical reasoning, and interpreting metrics. "
            "Always quantify uncertainty, cite sample sizes, and distinguish correlation "
            "from causation."
        ),
        tags=("data", "analytics"),
    ),
    "sql": Skill(
        name="sql",
        description=(
            "You are an expert in SQL and relational databases. Write optimized, readable "
            "queries. Prefer CTEs over subqueries. Flag full-table scans and missing indexes."
        ),
        tags=("data", "database"),
    ),
    "web_search": Skill(
        name="web_search",
        description=(
            "You are skilled at finding, evaluating, and synthesising information from the web. "
            "Prefer primary sources, flag paywalled content, and always cite URLs."
        ),
        tags=("research",),
    ),
    "code_review": Skill(
        name="code_review",
        description=(
            "You thoroughly review code for correctness, performance, security vulnerabilities, "
            "and maintainability. Structure feedback as: CRITICAL / SUGGESTION / NITPICK."
        ),
        tags=("quality", "programming"),
    ),
    "writing": Skill(
        name="writing",
        description=(
            "You write clearly, concisely, and engagingly for technical audiences. "
            "Use active voice, short paragraphs, and concrete examples. Avoid jargon unless "
            "the audience is domain-expert."
        ),
        tags=("communication",),
    ),
    "legal": Skill(
        name="legal",
        description=(
            "You have expertise in legal analysis and contract review. Flag regulatory risks, "
            "ambiguous clauses, and missing standard protections. Always recommend consulting "
            "a licensed attorney for final decisions."
        ),
        tags=("regulated", "compliance"),
    ),
    "medical": Skill(
        name="medical",
        description=(
            "You have clinical and biomedical knowledge. Provide evidence-based information, "
            "cite clinical studies, and always recommend consulting a licensed healthcare "
            "professional before acting on medical information."
        ),
        tags=("regulated", "healthcare"),
    ),
    "security": Skill(
        name="security",
        description=(
            "You specialize in cybersecurity, threat modelling, and secure software design. "
            "Apply OWASP Top 10 and MITRE ATT&CK frameworks. Flag injection, authentication, "
            "and privilege-escalation risks explicitly."
        ),
        tags=("security",),
    ),
    "api_design": Skill(
        name="api_design",
        description=(
            "You design clean, RESTful APIs following OpenAPI 3.x standards. Apply resource "
            "naming conventions, proper HTTP semantics, and versioning strategies. "
            "Include error response schemas."
        ),
        tags=("programming", "architecture"),
    ),
    "devops": Skill(
        name="devops",
        description=(
            "You are skilled in CI/CD, containerisation (Docker/K8s), infrastructure-as-code "
            "(Terraform/Helm), and cloud platform operations (AWS/GCP/Azure)."
        ),
        tags=("infrastructure",),
    ),
    "machine_learning": Skill(
        name="machine_learning",
        description=(
            "You have deep expertise in machine learning, model training, evaluation metrics, "
            "and MLOps. Apply best practices for reproducibility, fairness, and monitoring."
        ),
        tags=("data", "ai"),
    ),
    "finance": Skill(
        name="finance",
        description=(
            "You have expertise in financial analysis, accounting principles (GAAP/IFRS), "
            "and regulatory compliance (SOX, Basel III). Quantify risks in monetary terms."
        ),
        tags=("regulated", "finance"),
    ),
    "product": Skill(
        name="product",
        description=(
            "You think like a product manager. Frame decisions around user value, feasibility, "
            "and business impact. Use the RICE or MoSCoW frameworks for prioritisation."
        ),
        tags=("business",),
    ),
}


def skill_prompt(skills: list[str]) -> str:
    """Return a combined system-prompt snippet for the given skill names.

    Unknown skill names are silently ignored so partial matches degrade gracefully.
    """
    parts: list[str] = []
    for name in skills:
        s = SKILLS.get(name)
        if s:
            parts.append(s.description)
    return "\n\n".join(parts)


def list_skills() -> list[str]:
    """Return all available built-in skill names."""
    return sorted(SKILLS.keys())
