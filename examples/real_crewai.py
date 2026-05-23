"""Real CrewAI crew wired into a MeshFlow governed pipeline.

Requires:  pip install crewai

This example shows exactly how to take an existing CrewAI Crew and submit it
to the MeshFlow control plane for governance, audit, and policy enforcement.
Your crew code does not change at all — you only change where you call it.

Before MeshFlow:
    result = crew.kickoff(inputs={"task": "..."})

After MeshFlow:
    node   = MeshNode.from_crewai("my_crew", crew)
    result = await Mesh().run_workflow(wf, task="...")
    # every step now has: identity, guardian scan, budget cap,
    # uncertainty scoring, audit ledger, HITL escalation
"""
from __future__ import annotations

import asyncio
import sys


def _check_crewai() -> None:
    try:
        import crewai  # noqa: F401
    except ImportError:
        print("CrewAI is not installed.")
        print("Install it with:  pip install crewai")
        sys.exit(1)


_check_crewai()

from crewai import Agent, Crew, Task  # noqa: E402

from meshflow import Mesh, MeshNode, Policy, WorkflowDefinition  # noqa: E402


# ── Define your CrewAI crew exactly as you normally would ─────────────────────

def build_research_crew(topic: str) -> Crew:
    analyst = Agent(
        role="Market Analyst",
        goal=f"Research {topic} and produce a structured market analysis",
        backstory=(
            "You are a seasoned market analyst with 10 years of experience "
            "researching enterprise software markets."
        ),
        verbose=False,
    )

    writer = Agent(
        role="Report Writer",
        goal="Transform research findings into a clear executive report",
        backstory="You turn complex data into concise, actionable executive summaries.",
        verbose=False,
    )

    research_task = Task(
        description=f"Research the {topic} market. Cover market size, key players, growth drivers, and risks.",
        expected_output="A structured research report with 4–6 key findings and confidence levels.",
        agent=analyst,
    )

    write_task = Task(
        description="Transform the research findings into a 2-page executive briefing.",
        expected_output="An executive briefing document ready for board presentation.",
        agent=writer,
        context=[research_task],
    )

    return Crew(
        agents=[analyst, writer],
        tasks=[research_task, write_task],
        verbose=False,
    )


# ── Wire it into MeshFlow ─────────────────────────────────────────────────────

async def main() -> None:
    topic = "agentic AI orchestration frameworks"
    crew  = build_research_crew(topic)

    # One line to wrap — your crew object is unchanged
    research_node = MeshNode.from_crewai("crewai_research", crew)

    # Build a governed pipeline around it
    wf = (
        WorkflowDefinition(
            "crewai_governed_pipeline",
            policy=Policy(
                budget_usd=5.00,
                enable_guardian=True,
                enable_uncertainty=True,
            ),
        )
        .add_node(research_node)
        .add_node(
            MeshNode.from_callable(
                "format_output",
                lambda task, ctx: f"[Formatted]\n{ctx.get('crewai_research_output', '')}",
            )
        )
        .add_edge("crewai_research", "format_output")
    )

    print("Running CrewAI crew inside MeshFlow governance...\n")

    result = await Mesh().run_workflow(wf, task=f"Research: {topic}")

    print(f"Status   : {'COMPLETED' if result.completed else 'FAILED'}")
    print(f"Duration : {result.duration_s:.2f}s")
    print(f"Cost     : ${result.total_cost_usd:.5f}")
    print(f"Tokens   : {result.total_tokens}")
    print(f"\nOutput:\n{result.output[:1500]}")
    print(f"\nLedger   : {result.ledger_db}  run_id={result.run_id}")


if __name__ == "__main__":
    asyncio.run(main())
