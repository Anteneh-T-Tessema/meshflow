"""meshflow new — code generators for agents, teams, and tools."""

from __future__ import annotations

import sys
from pathlib import Path


_AGENT_TEMPLATE = '''\
"""Agent: {name}."""
from meshflow import Agent, RiskTier

{name_snake} = Agent(
    name="{name}",
    role="{role}",           # planner | researcher | executor | critic | orchestrator | guardian
    model="claude-sonnet-4-6",
    memory=True,
    risk=RiskTier.READ_ONLY,
    system_prompt=(
        "You are {name}. {role_hint}"
    ),
)
'''

_TEAM_TEMPLATE = '''\
"""Team: {name}."""
import asyncio
from meshflow import Agent, Team

# Import or define your agents here
agent_a = Agent(name="agent_a", role="planner")
agent_b = Agent(name="agent_b", role="executor")
agent_c = Agent(name="agent_c", role="critic")

{name_snake} = Team(
    name="{name}",
    agents=[agent_a, agent_b, agent_c],
    pattern="supervised",   # sequential | parallel | hierarchical | supervised
    policy="standard",      # dev | standard | regulated | legal-critical
)

async def main() -> None:
    result = await {name_snake}.run("Your task here")
    print(result.output)
    print(f"run_id={{result.run_id}}")

if __name__ == "__main__":
    asyncio.run(main())
'''

_TOOL_TEMPLATE = '''\
"""Tool: {name}."""
from meshflow import tool, RiskTier

@tool(
    name="{name}",
    description="{description}",
    risk=RiskTier.{risk},
    tags={tags},
)
async def {name_snake}({params}) -> str:
    """
    {description}

    Replace this implementation with your real logic.
    """
    raise NotImplementedError("Implement {name}")
'''

_ROLE_HINTS = {
    "planner": "Decompose tasks into clear, ordered steps with explicit inputs and outputs.",
    "researcher": "Gather and synthesise information. Cite sources. Flag uncertainty.",
    "executor": "Execute the given step precisely. Declare irreversible actions before taking them.",
    "critic": "Review outputs for correctness, completeness, and reasoning quality.",
    "orchestrator": "Coordinate agents, route tasks, and synthesise results.",
    "guardian": "Review proposed actions for safety and compliance. Block or escalate violations.",
}

_RISK_MAP = {
    "readonly": ("READ_ONLY", "pure reads, no side effects"),
    "internal": ("INTERNAL", "mutates internal state only"),
    "external": ("EXTERNAL_IO", "network, filesystem, or external API calls"),
    "irreversible": ("IRREVERSIBLE", "deletes, deploys, or financial transactions"),
}


def run_new(kind: str, name: str) -> None:
    kind = kind.lower()
    if kind == "agent":
        _new_agent(name)
    elif kind == "team":
        _new_team(name)
    elif kind == "tool":
        _new_tool(name)
    else:
        print(f"  ✗  Unknown kind '{kind}'. Choose: agent | team | tool")
        sys.exit(1)


def _new_agent(name: str) -> None:
    snake = _to_snake(name)
    print(f"\n  ? Role for {name}?")
    roles = list(_ROLE_HINTS.keys())
    for i, r in enumerate(roles, 1):
        print(f"    {i}) {r:<14} — {_ROLE_HINTS[r][:55]}")
    role = _choose_option(roles, default="executor")

    dest = Path(f"{snake}.py")
    if dest.exists():
        print(f"  ✗  {dest} already exists.")
        sys.exit(1)

    src = _AGENT_TEMPLATE.format(
        name=name,
        name_snake=snake,
        role=role,
        role_hint=_ROLE_HINTS[role],
    )
    dest.write_text(src)
    _ok(dest, f"Agent '{name}' ({role})")


def _new_team(name: str) -> None:
    snake = _to_snake(name)
    dest = Path(f"{snake}.py")
    if dest.exists():
        print(f"  ✗  {dest} already exists.")
        sys.exit(1)

    src = _TEAM_TEMPLATE.format(name=name, name_snake=snake)
    dest.write_text(src)
    _ok(dest, f"Team '{name}'")


def _new_tool(name: str) -> None:
    snake = _to_snake(name)

    desc_raw = _prompt("Description", default=f"A tool that {name.replace('-', ' ')}")
    description = desc_raw or f"A tool that {name}"

    print("\n  ? Risk tier?")
    risk_keys = list(_RISK_MAP.keys())
    for i, k in enumerate(risk_keys, 1):
        tier, hint = _RISK_MAP[k]
        print(f"    {i}) {k:<14} ({hint})")
    risk_key = _choose_option(risk_keys, default="readonly")
    risk_tier, _ = _RISK_MAP[risk_key]

    params_raw = _prompt(
        "Parameters (comma-separated, e.g. query: str, limit: int)", default="input: str"
    )
    params = params_raw.strip() or "input: str"

    tags_raw = _prompt("Tags (comma-separated, e.g. search,web)", default="")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    dest = Path(f"{snake}.py")
    if dest.exists():
        print(f"  ✗  {dest} already exists.")
        sys.exit(1)

    src = _TOOL_TEMPLATE.format(
        name=name,
        name_snake=snake,
        description=description,
        risk=risk_tier,
        tags=repr(tags),
        params=params,
    )
    dest.write_text(src)
    _ok(dest, f"Tool '{name}' ({risk_tier})")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _to_snake(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


def _prompt(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        val = input(f"  ? {question}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val or default


def _choose_option(options: list[str], default: str) -> str:
    default_idx = options.index(default) + 1 if default in options else 1
    while True:
        raw = _prompt(f"Choose 1-{len(options)}", default=str(default_idx))
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print(f"    Please enter a number between 1 and {len(options)}.")


def _ok(dest: Path, label: str) -> None:
    print()
    print(f"  ✓  Created {dest}")
    print(f"     {label}")
    print()
