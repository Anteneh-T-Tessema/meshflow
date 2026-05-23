"""meshflow init — scaffold a governed multi-agent project in 60 seconds."""

from __future__ import annotations

import sys
from pathlib import Path


# ── Templates ─────────────────────────────────────────────────────────────────

_MAIN_RESEARCH = '''\
"""Research pipeline — {name}."""
import asyncio
from meshflow import Agent, Team, tool, RiskTier

@tool(name="search", description="Search for information", risk=RiskTier.READ_ONLY)
async def search(query: str) -> str:
    # Replace with a real search API (Tavily, Exa, SerpAPI, etc.)
    return f"[simulated] Results for: {{query}}"

planner    = Agent(name="planner",    role="planner",    memory=True)
researcher = Agent(name="researcher", role="researcher", tools=[search], memory=True)
writer     = Agent(name="writer",     role="executor")
critic     = Agent(name="critic",     role="critic")

team = Team(
    name="{name}",
    agents=[planner, researcher, writer, critic],
    pattern="supervised",
    policy="{policy}",
)

async def main() -> None:
    result = await team.run(
        "Research and summarise the latest in AI agent frameworks.",
    )
    print("\\n" + "="*60)
    print(result.output)
    print(f"\\nrun_id={{result.run_id}}  cost=${{result.total_cost_usd:.4f}}")

if __name__ == "__main__":
    asyncio.run(main())
'''

_MAIN_CODE = '''\
"""Code assistant — {name}."""
import asyncio
from meshflow import Agent, Team, tool, RiskTier

@tool(name="run_code", description="Execute Python code safely", risk=RiskTier.INTERNAL)
async def run_code(code: str) -> str:
    # Replace with a real sandbox (e.g. E2B, Modal, subprocess with limits)
    return f"[simulated] Executed:\\n{{code[:200]}}"

planner  = Agent(name="planner",  role="planner")
coder    = Agent(name="coder",    role="executor", tools=[run_code], memory=True)
reviewer = Agent(name="reviewer", role="critic")

team = Team(
    name="{name}",
    agents=[planner, coder, reviewer],
    pattern="supervised",
    policy="{policy}",
)

async def main() -> None:
    result = await team.run(
        "Write a Python function that implements a sliding-window rate limiter.",
    )
    print("\\n" + "="*60)
    print(result.output)
    print(f"\\nrun_id={{result.run_id}}  cost=${{result.total_cost_usd:.4f}}")

if __name__ == "__main__":
    asyncio.run(main())
'''

_MAIN_LEGAL = '''\
"""Legal document review — {name}."""
import asyncio
from meshflow import Agent, Team, tool, RiskTier, policy_for_mode

@tool(name="extract_clauses", description="Extract key clauses from a document", risk=RiskTier.READ_ONLY)
async def extract_clauses(text: str) -> str:
    return f"[simulated] Extracted clauses from {{len(text)}} chars"

@tool(name="check_compliance", description="Check clause against policy rules", risk=RiskTier.INTERNAL)
async def check_compliance(clause: str) -> str:
    return f"[simulated] Compliance check passed for: {{clause[:80]}}"

extractor = Agent(name="extractor", role="researcher", tools=[extract_clauses])
analyst   = Agent(name="analyst",   role="executor",   tools=[check_compliance])
reviewer  = Agent(name="reviewer",  role="critic",     risk=RiskTier.INTERNAL)

team = Team(
    name="{name}",
    agents=[extractor, analyst, reviewer],
    pattern="sequential",
    policy="{policy}",
)

async def main() -> None:
    contract = """
    Section 7.2: Vendor may process Customer Data only to provide the Services.
    Section 12.1: Liability is capped at fees paid in the prior 12 months.
    """
    result = await team.run(f"Review this contract for risk:\\n{{contract}}")
    print("\\n" + "="*60)
    print(result.output)
    print(f"\\nrun_id={{result.run_id}}")
    if result.paused_nodes:
        print(f"\\n[HITL] Awaiting approval for: {{result.paused_nodes}}")
        print("  Run: meshflow approve <run_id> <node_id>")

if __name__ == "__main__":
    asyncio.run(main())
'''

_MAIN_CUSTOM = '''\
"""Custom agent team — {name}."""
import asyncio
from meshflow import Agent, Team, tool, RiskTier

# Define your tools
@tool(name="my_tool", description="A custom tool", risk=RiskTier.READ_ONLY)
async def my_tool(input: str) -> str:
    return f"Tool result for: {{input}}"

# Define your agents
agent_a = Agent(name="agent_a", role="planner",    memory=True)
agent_b = Agent(name="agent_b", role="executor",   tools=[my_tool])
agent_c = Agent(name="agent_c", role="critic")

# Form a team
team = Team(
    name="{name}",
    agents=[agent_a, agent_b, agent_c],
    pattern="supervised",   # sequential | parallel | hierarchical | supervised
    policy="{policy}",      # dev | standard | regulated | legal-critical
)

async def main() -> None:
    result = await team.run("Your task here")
    print(result.output)
    print(f"run_id={{result.run_id}}  paused={{result.paused_nodes}}")

if __name__ == "__main__":
    asyncio.run(main())
'''

_ENV_EXAMPLE = """\
# MeshFlow environment
ANTHROPIC_API_KEY=your_key_here

# Optional: persistent ledger (default: meshflow_runs.db)
# MESHFLOW_DB=meshflow_runs.db

# Optional: OpenTelemetry tracing
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
"""

_README = """\
# {name}

A governed multi-agent system built with [MeshFlow](https://github.com/meshflow-ai/meshflow).

## Quickstart

```bash
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
python main.py
```

## Commands

```bash
# View all runs
meshflow logs

# Replay a specific run step-by-step
meshflow replay <run_id>

# Approve a paused HITL node
meshflow approve <run_id> <node_id>

# Resume a paused HITL workflow
meshflow resume <run_id>

# Tail live events for a run
meshflow watch <run_id>

# Check governance conformance
meshflow conformance python --level 5
```

## Policy mode: `{policy}`

| Mode | Governance level |
|------|-----------------|
| `dev` | Fast tracing, minimal gates |
| `standard` | Audit ledger, policy basics |
| `regulated` | HITL gates, stricter audit |
| `legal-critical` | Evidence, citations, human review |
"""

_TEMPLATES = {
    "research": _MAIN_RESEARCH,
    "code": _MAIN_CODE,
    "legal": _MAIN_LEGAL,
    "custom": _MAIN_CUSTOM,
}

_TEMPLATE_LABELS = {
    "research": "Research pipeline (planner → researcher → writer → critic)",
    "code": "Code assistant  (planner → coder → reviewer)",
    "legal": "Document review (extractor → analyst → reviewer + HITL)",
    "custom": "Blank canvas    (customize agents, tools, patterns)",
}

_POLICY_LABELS = {
    "dev": "dev            — fast, minimal gates (prototyping)",
    "standard": "standard       — audit + policy basics (production)",
    "regulated": "regulated      — HITL + immutable audit (finance/health)",
    "legal-critical": "legal-critical — evidence + citations + review gates",
}


# ── Public entry point ────────────────────────────────────────────────────────


def run_init(name: str | None = None) -> None:
    _print_banner()

    project_name = name or _prompt("Project name", default="my-agent-team")
    project_name = project_name.strip().replace(" ", "-") or "my-agent-team"

    template = _choose("What are you building?", list(_TEMPLATE_LABELS.keys()), _TEMPLATE_LABELS)
    policy = _choose("Policy mode?", list(_POLICY_LABELS.keys()), _POLICY_LABELS)

    dest = Path(project_name)
    if dest.exists():
        print(f"\n  ✗  Directory '{project_name}' already exists. Choose a different name.")
        sys.exit(1)

    dest.mkdir(parents=True)

    main_src = _TEMPLATES[template].format(name=project_name, policy=policy)
    (dest / "main.py").write_text(main_src)
    (dest / ".env.example").write_text(_ENV_EXAMPLE)
    (dest / "README.md").write_text(_README.format(name=project_name, policy=policy))

    _print_success(project_name, policy)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _print_banner() -> None:
    print()
    print("  ███╗   ███╗███████╗███████╗██╗  ██╗")
    print("  ████╗ ████║██╔════╝██╔════╝██║  ██║")
    print("  ██╔████╔██║█████╗  ███████╗███████║")
    print("  ██║╚██╔╝██║██╔══╝  ╚════██║██╔══██║")
    print("  ██║ ╚═╝ ██║███████╗███████║██║  ██║")
    print("  ╚═╝     ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝")
    print()
    print("  Build agents. Form teams. Govern everything.")
    print()


def _prompt(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        val = input(f"  ? {question}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val or default


def _choose(question: str, options: list[str], labels: dict[str, str]) -> str:
    print(f"\n  ? {question}")
    for i, key in enumerate(options, 1):
        print(f"    {i}) {labels[key]}")
    while True:
        raw = _prompt(f"Choose 1-{len(options)}", default="1")
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print(f"    Please enter a number between 1 and {len(options)}.")


def _print_success(name: str, policy: str) -> None:
    print()
    print(f"  ✓  Created {name}/")
    print(f"     ├── main.py          your agent team ({policy} mode)")
    print("     ├── .env.example     add ANTHROPIC_API_KEY here")
    print("     └── README.md        commands and docs")
    print()
    print("  Next steps:")
    print()
    print(f"    cd {name}")
    print("    cp .env.example .env")
    print("    # Add your ANTHROPIC_API_KEY to .env")
    print("    python main.py")
    print()
    print("  Once it runs:")
    print()
    print("    meshflow logs                   # view all runs")
    print("    meshflow replay <run_id>         # step-through debugger")
    print("    meshflow conformance python --level 5")
    print()
