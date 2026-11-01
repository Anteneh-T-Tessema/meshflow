"""Bounded local-provider smoke checks; never select a paid cloud provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from meshflow import Agent, Policy
from meshflow.agents.providers import OllamaProvider
from meshflow.core.session import GovernedSession


async def validate(model: str, host: str) -> dict[str, object]:
    provider = OllamaProvider(model=model, host=host)
    policy = Policy(
        strict_budget=True,
        enable_uncertainty=False,
        deterministic_gate=False,
        budget_usd=0.01,
        budget_tokens=20000,
    )
    with tempfile.TemporaryDirectory(prefix="meshflow-live-") as directory:
        async with GovernedSession(policy, str(Path(directory) / "audit.db")) as session:
            agent = Agent(
                "local-smoke",
                model=model,
                provider=provider,
                system_prompt="Follow the instruction exactly. Keep the response under five words.",
            )
            result = await asyncio.wait_for(agent.run("Reply with the word READY."), timeout=90)
            if result.get("blocked") or not result.get("result", "").strip():
                raise RuntimeError("Governed completion did not produce output")
            pieces = [text async for text in _timed_stream(agent)]
            if not "".join(pieces).strip():
                raise RuntimeError("Governed stream did not produce output")
            integrity = await session.ledger.verify_chain(session.run_id)
            if not integrity["valid"]:
                raise RuntimeError("Live provider audit chain failed verification")
            records = [step.record for step in session.outcomes]
            if len(records) != 2 or any(
                r.blocked or r.cost_usd != 0 or r.tokens_used <= 0 for r in records
            ):
                raise RuntimeError("Unexpected local-provider accounting")
            return {
                "model": model,
                "completion": "passed",
                "streaming": "passed",
                "audit": "passed",
                "steps": len(records),
                "tokens": sum(r.tokens_used for r in records),
                "cost_usd": 0,
            }


async def _timed_stream(agent: Agent):
    async with asyncio.timeout(90):
        async for text in agent.stream("Reply with the word READY."):
            yield text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(validate(args.model, args.host)), indent=2))
