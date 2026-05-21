"""Basic MeshFlow example — minimal working usage."""
import asyncio
from meshflow import Mesh, Policy


async def main():
    mesh = Mesh()
    result = await mesh.run(
        task="Explain the three main differences between transformer and RNN architectures.",
        policy=Policy(
            budget_usd=0.10,
            budget_tokens=50_000,
            timeout_s=60.0,
            validate_handoffs=True,
        ),
    )
    print(f"Status:  {result.status.value}")
    print(f"Cost:    ${result.total_cost_usd:.4f}")
    print(f"Tokens:  {result.total_tokens:,}")
    print(f"Time:    {result.duration_s:.2f}s")
    print(f"\nOutput:\n{result.output}")


if __name__ == "__main__":
    asyncio.run(main())
