"""Multi-framework example — mix CrewAI, AutoGen, and native agents in one mesh."""
import asyncio
from meshflow import Mesh, Policy, AgentRole
from meshflow.agents.adapters import from_callable
from meshflow.agents.base import AgentConfig, ResearcherAgent


async def my_custom_researcher(task: str, context: dict) -> str:
    """A simple callable that acts as a research agent."""
    return f"Research findings for: {task}"


async def main():
    # Native MeshFlow agent
    researcher = ResearcherAgent(
        AgentConfig(role=AgentRole.RESEARCHER, model="claude-sonnet-4-6"),
        Policy(),
    )

    # Callable wrapped as a MeshFlow agent
    custom_agent = from_callable(
        my_custom_researcher,
        role=AgentRole.EXECUTOR,
        agent_id="custom-researcher",
    )

    mesh = Mesh(agents=[researcher, custom_agent])
    result = await mesh.run(
        task="What are the key differences between MeshFlow and LangGraph?",
        policy=Policy(budget_usd=0.25),
    )

    print(f"Run ID:  {result.run_id}")
    print(f"Status:  {result.status.value}")
    print(f"Output:  {result.output}")


if __name__ == "__main__":
    asyncio.run(main())
