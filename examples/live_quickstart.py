"""Live quickstart — requires ANTHROPIC_API_KEY in .env or environment.

Runs a real 2-agent mesh (Researcher → Executor) on a simple task.
Expected cost: ~$0.01–0.05 depending on output length.
"""
import asyncio
import os

from meshflow import Mesh, Policy
from meshflow.core.schemas import AgentRole


def check_api_key() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("sk-ant-..."):
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set.\n"
            "1. Copy .env.example to .env\n"
            "2. Add your key: ANTHROPIC_API_KEY=sk-ant-...\n"
            "3. Re-run: python examples/live_quickstart.py"
        )


async def main() -> None:
    check_api_key()

    mesh = Mesh(
        policy=Policy(
            budget_usd=0.10,
            budget_tokens=20_000,
            timeout_s=90.0,
            validate_handoffs=False,   # skip critic to reduce cost
        )
    )

    print("MeshFlow Live Quickstart\n" + "=" * 40)
    print("Task: What are the top 3 advantages of using a policy-first approach in multi-agent systems?\n")

    result = await mesh.run(
        task="What are the top 3 advantages of using a policy-first approach in multi-agent systems?"
    )

    print(f"Status   : {result.status.value}")
    print(f"Cost     : ${result.total_cost_usd:.4f}")
    print(f"Tokens   : {result.total_tokens:,}")
    print(f"Duration : {result.duration_s:.2f}s")
    if result.error:
        print(f"Error    : {result.error}")
    print(f"\nOutput:\n{result.output}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
