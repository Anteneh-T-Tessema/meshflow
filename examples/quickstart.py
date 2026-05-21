"""Simulated quickstart — no API key needed. Uses a mock agent.

Demonstrates the full MeshFlow API surface without spending tokens.
"""
import asyncio
from meshflow.core.mesh import Mesh
from meshflow.core.schemas import AgentRole, Policy
from meshflow.agents.adapters import from_callable


async def mock_researcher(task: str, context: dict) -> str:
    """Simulates a researcher — no LLM call."""
    return (
        f"[Simulated research on: '{task}']\n"
        "Finding 1: Multi-agent systems improve complex task accuracy by 40%.\n"
        "Finding 2: Token efficiency engines reduce cost by up to 30%.\n"
        "Finding 3: Deterministic gates prevent 95% of prompt injection attacks."
    )


async def mock_executor(task: str, context: dict) -> str:
    """Simulates an executor — no LLM call."""
    research = context.get("execution_result", context.get("research", ""))
    return f"[Simulated execution]\nTask: {task}\nBased on: {research[:100]}...\nResult: Done."


async def main() -> None:
    researcher = from_callable(mock_researcher, role=AgentRole.RESEARCHER, agent_id="sim-researcher")
    executor   = from_callable(mock_executor,   role=AgentRole.EXECUTOR,   agent_id="sim-executor")

    mesh = Mesh(
        agents=[researcher, executor],
        policy=Policy(
            budget_usd=0.10,
            budget_tokens=50_000,
            deterministic_gate=False,   # skip gate for simulation
            enable_guardian=False,
        ),
    )

    print("MeshFlow Quickstart (simulated)\n" + "=" * 40)
    result = await mesh.run(task="Explain the benefits of multi-agent orchestration")

    print(f"Status   : {result.status.value}")
    print(f"Run ID   : {result.run_id[:8]}...")
    print(f"Duration : {result.duration_s:.2f}s")
    print(f"Cost     : ${result.total_cost_usd:.4f} (simulated — $0.0000 real)")
    print(f"Agents   : {len(result.agent_states)}")
    print(f"\nOutput:\n{result.output}")


if __name__ == "__main__":
    asyncio.run(main())
