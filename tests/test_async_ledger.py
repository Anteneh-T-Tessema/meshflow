"""Tests for the AsyncLedgerWriter pipeline."""

from __future__ import annotations

import asyncio
import json
import pytest
from meshflow.core.ledger import ReplayLedger, AsyncLedgerWriter
from meshflow.core.runtime import StepRecord


def _record(run_id: str = "test-run", step_id: str = "step-1", node_id: str = "node-a") -> StepRecord:
    return StepRecord(
        run_id=run_id,
        step_id=step_id,
        node_id=node_id,
        node_kind="python",
        input_task="task",
        output_content="output",
        verdict="commit",
        blocked=False,
        block_reason="",
        uncertainty=0.0,
        cost_usd=0.01,
        tokens_used=12,
        carbon_gco2=0.001,
        duration_ms=3.0,
        timestamp="2026-05-21T00:00:00+00:00",
        metadata={"source": "test"},
    )


@pytest.mark.anyio
async def test_batch_accumulation():
    ledger = ReplayLedger(":memory:", enable_batching=True)
    if ledger._writer:
        ledger._writer.stop()

    writer = AsyncLedgerWriter(ledger, flush_interval=10.0, batch_size=3)
    await writer.write(_record(step_id="step-1"))
    await writer.write(_record(step_id="step-2"))

    # Should not be in the database yet
    runs = await ledger.list_runs()
    assert len(runs) == 0

    # Write 3rd record to hit batch size
    await writer.write(_record(step_id="step-3"))
    await asyncio.sleep(0.1)

    steps = await ledger.get_run("test-run")
    assert len(steps) == 3

    await ledger.aclose()
    writer.stop()


@pytest.mark.anyio
async def test_timer_based_flush():
    ledger = ReplayLedger(":memory:", enable_batching=True)
    if ledger._writer:
        ledger._writer.stop()

    writer = AsyncLedgerWriter(ledger, flush_interval=0.05, batch_size=100)
    await writer.write(_record(step_id="step-1"))

    # Not flushed yet
    runs = await ledger.list_runs()
    assert len(runs) == 0

    # Wait for the timer
    await asyncio.sleep(0.15)

    steps = await ledger.get_run("test-run")
    assert len(steps) == 1

    await ledger.aclose()
    writer.stop()


@pytest.mark.anyio
async def test_merkle_root_integrity():
    ledger = ReplayLedger(":memory:", enable_batching=True)
    if ledger._writer:
        ledger._writer.stop()

    writer = AsyncLedgerWriter(ledger, flush_interval=10.0, batch_size=3)
    await writer.write(_record(step_id="step-1"))
    await writer.write(_record(step_id="step-2"))
    await writer.write(_record(step_id="step-3"))
    await asyncio.sleep(0.1)

    steps = await ledger.get_run("test-run")
    assert len(steps) == 3

    for step in steps:
        meta = step.get("metadata")
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert "merkle_proof" in meta
        proof = meta["merkle_proof"]
        assert "proof" in proof
        assert "batch_root" in proof
        assert "master_root" in proof

    await ledger.aclose()
    writer.stop()


@pytest.mark.anyio
async def test_chain_verification():
    ledger = ReplayLedger(":memory:", enable_batching=True)
    await ledger.write(_record(step_id="step-1"))
    await ledger.write(_record(step_id="step-2"))

    # Closing/draining the writer
    await ledger.aclose()

    # Re-verify with a new ledger handle
    ledger_verify = ReplayLedger(ledger._db_path, enable_batching=False)
    res = await ledger_verify.verify_chain("test-run")
    assert res["valid"] is True
    assert len(res["errors"]) == 0
    await ledger_verify.aclose()


@pytest.mark.anyio
async def test_graceful_drain():
    ledger = ReplayLedger(":memory:", enable_batching=True)
    if ledger._writer:
        ledger._writer.stop()

    writer = AsyncLedgerWriter(ledger, flush_interval=10.0, batch_size=100)
    await writer.write(_record(step_id="step-1"))
    await writer.write(_record(step_id="step-2"))

    await writer.drain()

    steps = await ledger.get_run("test-run")
    assert len(steps) == 2

    await ledger.aclose()
    writer.stop()


@pytest.mark.anyio
async def test_shutdown_safety():
    ledger = ReplayLedger(":memory:", enable_batching=True)
    if ledger._writer:
        ledger._writer.stop()

    writer = AsyncLedgerWriter(ledger, flush_interval=10.0, batch_size=100)
    await writer.write(_record(step_id="step-1"))
    await writer.write(_record(step_id="step-2"))

    # Yield control to let worker loop start
    await asyncio.sleep(0.01)

    writer.stop()
    if writer._worker_task:
        try:
            await writer._worker_task
        except asyncio.CancelledError:
            pass

    steps = await ledger.get_run("test-run")
    assert len(steps) == 2

    await ledger.aclose()
