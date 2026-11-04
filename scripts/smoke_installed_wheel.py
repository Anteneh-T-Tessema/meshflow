"""Offline smoke check; run with a clean wheel environment's `python -I`."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile

import meshflow
from meshflow import Agent, Policy
from meshflow.budget.reservations import ReservationStore
from meshflow.core.ledger import ReplayLedger
from meshflow.core.request_budget import budgeted_request
from meshflow.core.session import GovernedSession
from meshflow.streaming.sync_bridge import sync_stream


async def main() -> None:
    package = Path(meshflow.__file__).resolve().parent
    assert package.is_relative_to(Path(sys.prefix).resolve()), package
    assert (package / "py.typed").is_file(), "Missing typing marker"
    assert all(callable(item) for item in (ReservationStore, budgeted_request, sync_stream))
    os.environ["MESHFLOW_MOCK"] = "1"
    with tempfile.TemporaryDirectory(prefix="meshflow-wheel-smoke-") as directory:
        path = str(Path(directory) / "audit.db")
        async with GovernedSession(
            Policy(enable_uncertainty=False), ledger_db=path
        ) as session:
            result = await Agent("wheel-smoke", mode="sandbox").run("Summarize quarterly revenue")
            assert result["result"]
            assert result["run_id"] == session.run_id
            assert len(session.outcomes) == 1
            run_id = session.run_id
        ledger = ReplayLedger(path)
        try:
            assert len(await ledger.get_run(run_id)) == 1
            assert (await ledger.verify_chain(run_id))["valid"]
        finally:
            await ledger.aclose()
    print(f"Installed wheel governance and durable audit passed: {package}")


if __name__ == "__main__":
    asyncio.run(main())
