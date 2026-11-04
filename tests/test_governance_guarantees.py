"""Behavioral guarantees across public APIs, persistence, budgets and audit."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from meshflow import Agent, CostCap, Policy, Team, Workflow
from meshflow.core.flows import Flow, start
from meshflow.core.ledger import ReplayLedger
from meshflow.core.policy import BudgetExceededError, BudgetTracker
from meshflow.core.request_budget import active_budget, budgeted_request
from meshflow.core.runtime import record_hash
from meshflow.core.session import GovernedSession


@pytest.fixture(autouse=True)
def offline_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHFLOW_MOCK", "1")
    monkeypatch.setenv("MESHFLOW_LEDGER_DB", str(tmp_path / "runs.db"))


class CountingAgent:
    def __init__(self, name, calls, cost=0.01):
        self.name, self.calls, self.cost = name, calls, cost

    def request_cost_bound(self, **kwargs):
        return self.cost, 12

    async def run(self, task, context=None):
        self.calls.append(self.name)
        return {
            "result": "verified output CONFIDENCE: 0.99",
            "tokens": 12,
            "cost_usd": self.cost,
            "stated_confidence": 0.99,
        }


@pytest.mark.asyncio
async def test_direct_agent_persists_governed_record(monkeypatch):
    agent = Agent("direct", mode="sandbox")
    async with GovernedSession(Policy(enable_uncertainty=False)) as session:
        result = await agent.run("Summarize quarterly revenue")
        assert result["result"]
        assert result["run_id"] == session.run_id
        assert len(session.outcomes) == 1
        assert (await session.ledger.verify_chain(session.run_id))["valid"]
        run_id, path = session.run_id, session.ledger_db
    ledger = ReplayLedger(path)
    try:
        assert len(await ledger.get_run(run_id)) == 1
    finally:
        await ledger.aclose()


def test_parallel_workflow_records_every_agent():
    calls = []
    result = (
        Workflow(cost_cap=CostCap(1))
        .add_parallel(CountingAgent("one", calls), CountingAgent("two", calls))
        .run("Summarize data")
    )
    assert result.completed
    assert len(result.steps) == 2
    assert result.total_tokens == 24
    assert result.total_cost_usd == pytest.approx(0.02)
    assert result.run_id and result.ledger_db


@pytest.mark.asyncio
async def test_early_exit_reports_real_usage_and_stops():
    calls = []
    result = await Team(
        "early", [CountingAgent("one", calls), CountingAgent("two", calls)], stop_on_confidence=0.9
    ).run("Summarize data")
    assert calls == ["one"]
    assert result.run_id and len(result.steps) == 1
    assert result.total_tokens == 12
    assert result.total_cost_usd == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_flow_obeys_policy_and_records_usage():
    class Sample(Flow):
        @start()
        async def entry(self):
            return {"result": "flow output", "tokens": 9, "cost_usd": 0.03}

    result = await Sample(policy=Policy(enable_uncertainty=False)).kickoff()
    assert not result.error
    assert result.total_tokens == 9 and result.total_cost_usd == pytest.approx(0.03)
    assert result.run_id and len(result.steps) == 1


@pytest.mark.asyncio
async def test_zero_trust_failure_blocks_without_dispatch():
    calls = []
    async with GovernedSession(Policy(enable_uncertainty=False)) as session:

        def broken(*args, **kwargs):
            raise RuntimeError("authorization service unavailable")

        session.runtime._zero_trust = SimpleNamespace(
            _policy=SimpleNamespace(continuous_auth=True, spotlighting=False),
            _cont_auth=SimpleNamespace(authorize=broken),
        )
        result = await session.agent(CountingAgent("blocked", calls), "task")
        assert result["blocked"] and not calls
        assert "control_error" in result["blocked_by"]
        assert (await session.ledger.verify_chain(session.run_id))["valid"]


@pytest.mark.asyncio
async def test_parallel_requests_cannot_spend_same_remaining_budget():
    tracker = BudgetTracker(Policy(budget_usd=0.025, budget_tokens=10000))
    calls = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def create(**kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=100))

    token = active_budget.set(tracker)
    try:
        first = asyncio.create_task(
            budgeted_request(
                create, rates=(0.01, 0.01), model="bounded", messages=[], max_tokens=2000
            )
        )
        await entered.wait()
        with pytest.raises(BudgetExceededError):
            await budgeted_request(
                create, rates=(0.01, 0.01), model="bounded", messages=[], max_tokens=2000
            )
        assert len(calls) == 1
        release.set()
        await first
        assert not tracker._reserved
    finally:
        release.set()
        active_budget.reset(token)


@pytest.mark.asyncio
async def test_request_failure_retains_reserved_spend():
    tracker = BudgetTracker(Policy(budget_usd=0.03))

    async def create(**kwargs):
        raise ConnectionError("response lost after dispatch")

    token = active_budget.set(tracker)
    try:
        with pytest.raises(ConnectionError):
            await budgeted_request(
                create, rates=(0.01, 0.01), model="bounded", messages=[], max_tokens=2000
            )
        with pytest.raises(BudgetExceededError):
            await budgeted_request(
                create, rates=(0.01, 0.01), model="bounded", messages=[], max_tokens=2000
            )
    finally:
        active_budget.reset(token)


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_task", "a" * 250 + "tampered"),
        ("output_content", "b" * 250 + "tampered"),
        ("cost_usd", 100),
        ("tokens_used", 999),
        ("metadata", {"hash_version": 2, "policy": "tampered"}),
        ("block_reason", "changed"),
        ("node_kind", "http"),
    ],
)
@pytest.mark.asyncio
async def test_v2_audit_detects_complete_record_tampering(make_step_record, field, value):
    record = make_step_record(output="b" * 300)
    record.input_task = "a" * 300
    record.entry_hash = record_hash(record)
    data = asdict(record)
    data[field] = value
    assert record_hash(data) != record.entry_hash


@pytest.mark.asyncio
async def test_database_tamper_detected_after_reopen(tmp_path, make_step_record):
    path = str(tmp_path / "audit.db")
    ledger = ReplayLedger(path, enable_batching=False)
    record = make_step_record(output="b" * 300)
    await ledger.write(record)
    await ledger.aclose()
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE step_records SET cost_usd=900 WHERE step_id=?", (record.step_id,))
    ledger = ReplayLedger(path)
    try:
        assert not (await ledger.verify_chain(record.run_id))["valid"]
    finally:
        await ledger.aclose()


def test_reconstructed_workflow_resumes_without_repeating_completed_step(monkeypatch, tmp_path):
    calls = []

    def build():
        return (
            Workflow()
            .add_parallel(CountingAgent("first", calls))
            .add(CountingAgent("second", calls), interrupt_before=True)
        )

    paused = build().run("Summarize data")
    assert not paused.completed and calls == ["first"]
    resumed = build().resume_from_checkpoint(paused.run_id, paused.ledger_db)
    assert resumed.completed
    assert calls == ["first", "second"]
    assert resumed.run_id == paused.run_id
    assert len(resumed.steps) == 2 and resumed.total_tokens == 24


@pytest.mark.asyncio
async def test_interleaved_batched_runs_keep_independent_valid_chains(tmp_path, make_step_record):
    ledger = ReplayLedger(str(tmp_path / "batches.db"), enable_batching=True)
    try:
        for run, step in [("a", "a1"), ("b", "b1"), ("a", "a2"), ("b", "b2")]:
            record = make_step_record(run_id=run)
            record.step_id = step
            record.entry_hash = record_hash(record)
            await ledger.write(record)
        await asyncio.wait_for(ledger._writer.drain(), timeout=2)
        for run in ("a", "b"):
            result = await ledger.verify_chain(run)
            assert result["valid"], result["errors"]
            assert result["steps_verified"] == 2
    finally:
        await ledger.aclose()


@pytest.mark.asyncio
async def test_legacy_hash_chain_remains_verifiable(make_step_record):
    record = make_step_record()
    record.metadata = {}
    record.entry_hash = record_hash(record)
    ledger = ReplayLedger(":memory:")
    try:
        await ledger.write(record)
        assert (await ledger.verify_chain(record.run_id))["valid"]
    finally:
        await ledger.aclose()


@pytest.mark.asyncio
async def test_strict_custom_provider_without_bounds_never_dispatches():
    calls = []

    class UnknownProvider:
        async def complete(self, **kwargs):
            calls.append("dispatched")
            return "output", 1, 0.01

    from meshflow.core.request_budget import budgeted_completion

    token = active_budget.set(BudgetTracker(Policy(strict_budget=True)))
    try:
        with pytest.raises(BudgetExceededError, match="request_cost_bound"):
            await budgeted_completion(UnknownProvider(), "complete", model="custom", max_tokens=10)
        assert not calls
    finally:
        active_budget.reset(token)


@pytest.mark.asyncio
async def test_strict_cache_miss_enforces_provider_bound_and_hit_is_free():
    from meshflow.cache.core import InMemoryCache
    from meshflow.cache.provider import CachedProvider
    from meshflow.core.request_budget import budgeted_completion

    calls = []

    class BoundedProvider:
        def request_cost_bound(self, **kwargs):
            return 0.02, 10

        async def complete(self, **kwargs):
            calls.append("dispatched")
            return "cached output", 10, 0.02

    cached = CachedProvider(BoundedProvider(), InMemoryCache(semantic=False))
    tracker = BudgetTracker(Policy(budget_usd=0.02, strict_budget=True))
    token = active_budget.set(tracker)
    try:
        kwargs = dict(model="custom", messages=[], system="", max_tokens=10)
        first = await budgeted_completion(cached, "complete", **kwargs)
        second = await budgeted_completion(cached, "complete", **kwargs)
        assert first[2] == 0.02 and second[2] == 0
        assert calls == ["dispatched"]
        assert tracker._provider_usd == 0.02
    finally:
        active_budget.reset(token)


@pytest.mark.asyncio
async def test_legacy_mesh_executor_persists_audit_records(monkeypatch):
    from meshflow import Mesh, policy_for_mode
    from meshflow.core.session import default_ledger_path

    result = await Mesh(policy=policy_for_mode("dev")).run("Summarize quarterly revenue")
    ledger = ReplayLedger(default_ledger_path())
    try:
        records = await ledger.get_run(result.run_id)
        assert records
        assert (await ledger.verify_chain(result.run_id))["valid"]
    finally:
        await ledger.aclose()
