"""Recover a checkpoint after an actual worker exits during its next step."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from meshflow import Workflow
from meshflow.core.ledger import ReplayLedger


CRASHING_WORKER = r"""
import os, sys
from meshflow import Workflow
class WorkerAgent:
    def __init__(self, name): self.name = name
    async def run(self, task, context=None):
        if self.name == "second": os._exit(23)
        with open(sys.argv[1], "a") as effects: effects.write(self.name + "\n")
        return {"result": self.name, "tokens": 7, "cost_usd": .01, "stated_confidence": .99}
Workflow().add_parallel(WorkerAgent("first")).add(WorkerAgent("second")).run("Summarize work")
"""


def test_checkpoint_recovers_after_worker_death_without_repeating_completed_step(tmp_path):
    ledger_path = str(tmp_path / "recovery.db")
    effects = tmp_path / "effects.txt"
    env = dict(os.environ, MESHFLOW_MOCK="1", MESHFLOW_LEDGER_DB=ledger_path)
    process = subprocess.run(
        [sys.executable, "-c", CRASHING_WORKER, str(effects)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert process.returncode == 23, process.stderr
    assert effects.read_text().splitlines() == ["first"]

    async def run_id():
        ledger = ReplayLedger(ledger_path)
        try:
            runs = await ledger.list_runs()
            assert len(runs) == 1
            assert await ledger.load_checkpoint_data(runs[0])
            return runs[0]
        finally:
            await ledger.aclose()

    recovered_run_id = asyncio.run(run_id())

    class WorkerAgent:
        def __init__(self, name):
            self.name = name

        async def run(self, task, context=None):
            with effects.open("a") as output:
                output.write(self.name + "\n")
            return {"result": self.name, "tokens": 7, "cost_usd": 0.01, "stated_confidence": 0.99}

    workflow = Workflow().add_parallel(WorkerAgent("first")).add(WorkerAgent("second"))
    result = workflow.resume_from_checkpoint(recovered_run_id, ledger_path)
    assert result.completed
    assert effects.read_text().splitlines() == ["first", "second"]
    assert result.run_id == recovered_run_id
    assert result.total_tokens == 14 and result.total_cost_usd == 0.02

    async def verify():
        ledger = ReplayLedger(ledger_path)
        try:
            assert (await ledger.verify_chain(recovered_run_id))["valid"]
            assert await ledger.load_checkpoint_data(recovered_run_id) is None
        finally:
            await ledger.aclose()

    asyncio.run(verify())
