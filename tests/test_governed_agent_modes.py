"""Public agent modes share governance, accounting, and stream cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from meshflow import Agent, Policy
from meshflow.core.request_budget import report_stream_usage
from meshflow.core.schemas import TokenChunk
from meshflow.core.session import GovernedSession, GovernanceBlockedError


@pytest.fixture(autouse=True)
def isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHFLOW_LEDGER_DB", str(tmp_path / "runs.db"))
    monkeypatch.setenv("MESHFLOW_MOCK", "1")


class Provider:
    def __init__(self, response='{"answer": 42}'):
        self.response = response
        self.calls = 0
        self.closed = False
        self.release = asyncio.Event()

    def request_cost_bound(self, **kwargs):
        return 0.02, 20

    async def complete(self, **kwargs):
        self.calls += 1
        return self.response, 9, 0.01

    async def complete_with_tools(self, **kwargs):
        return await self.complete(**kwargs)

    async def stream_complete(self, **kwargs):
        self.calls += 1
        try:
            yield TokenChunk(text="a", agent_id="mode", step_id="step")
            await self.release.wait()
            yield TokenChunk(text="b", agent_id="mode", step_id="step")
            report_stream_usage(SimpleNamespace(prompt_tokens=6, completion_tokens=2), (1.0, 1.0))
        finally:
            self.closed = True


class Answer(BaseModel):
    answer: int


def policy():
    return Policy(enable_uncertainty=False, deterministic_gate=False)


@pytest.mark.asyncio
async def test_typed_output_preserves_model_and_records_usage():
    provider = Provider()
    async with GovernedSession(policy()) as session:
        value = await Agent("typed", provider=provider).run_typed("Return an answer", Answer)
        assert isinstance(value, Answer) and value.answer == 42
        assert len(session.outcomes) == 1
        record = session.outcomes[0].record
        assert record.tokens_used == 9 and record.cost_usd == 0.01
        assert (await session.ledger.verify_chain(session.run_id))["valid"]


@pytest.mark.asyncio
async def test_structured_retries_preserve_exception_and_record_failure():
    from meshflow.agents.structured import StructuredOutputError

    provider = Provider("invalid JSON")
    async with GovernedSession(policy()) as session:
        with pytest.raises(StructuredOutputError):
            await Agent("structured", provider=provider).run_structured(
                "Return JSON", Answer, max_retries=2
            )
        assert provider.calls == 2
        assert len(session.outcomes) == 1
        assert not session.outcomes[0].ok
        assert session.outcomes[0].record.tokens_used == 18
        assert session.outcomes[0].record.cost_usd == 0.02
        assert (await session.ledger.verify_chain(session.run_id))["valid"]


@pytest.mark.asyncio
async def test_stream_is_live_and_settles_terminal_usage():
    provider = Provider()
    async with GovernedSession(policy()) as session:
        iterator = Agent("stream", provider=provider).stream("Generate text")
        assert await asyncio.wait_for(anext(iterator), timeout=2) == "a"
        assert not session.outcomes
        provider.release.set()
        assert [part async for part in iterator] == ["b"]
        assert provider.closed
        record = session.outcomes[0].record
        assert record.output_content == "ab"
        assert record.tokens_used == 8
        assert record.cost_usd == pytest.approx(0.008)
        assert not session.runtime._budget._reserved


@pytest.mark.asyncio
async def test_stream_close_persists_partial_output_and_retains_bound():
    provider = Provider()
    async with GovernedSession(policy()) as session:
        iterator = Agent("cancelled", provider=provider).stream("Generate text")
        assert await asyncio.wait_for(anext(iterator), timeout=2) == "a"
        await asyncio.wait_for(iterator.aclose(), timeout=2)
        assert provider.closed
        record = session.outcomes[0].record
        assert record.blocked and record.block_reason == "stream:cancelled"
        assert record.output_content == "a"
        assert record.cost_usd == 0.02 and record.tokens_used == 20
        assert not session.runtime._budget._reserved
        assert (await session.ledger.verify_chain(session.run_id))["valid"]


@pytest.mark.asyncio
async def test_stream_guardian_denial_prevents_dispatch():
    provider = Provider()
    async with GovernedSession(policy()) as session:
        session.runtime._guardian.evaluate_message = lambda message: (False, "test_denial")
        with pytest.raises(GovernanceBlockedError, match="test_denial"):
            _ = [part async for part in Agent("blocked", provider=provider).stream("Generate text")]
        assert provider.calls == 0
        assert len(session.outcomes) == 1
        assert session.outcomes[0].record.blocked


@pytest.mark.asyncio
async def test_flow_returning_agent_result_does_not_double_charge():
    from meshflow.core.flows import Flow, start

    provider = Provider()

    class NestedFlow(Flow):
        @start()
        async def answer(self):
            return await Agent("child", provider=provider).run("Return an answer")

    result = await NestedFlow(policy=policy()).kickoff()
    assert not result.error
    assert result.total_tokens == 9
    assert result.total_cost_usd == 0.01
    assert len(result.steps) == 2
    assert sum(step.record.cost_usd for step in result.steps) == 0.01


@pytest.mark.asyncio
async def test_standalone_stream_can_close_from_another_task():
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.session import active_session, default_ledger_path

    provider = Provider()
    iterator = Agent("standalone", provider=provider, policy=policy()).stream("Generate text")
    assert await asyncio.wait_for(anext(iterator), timeout=2) == "a"
    await asyncio.wait_for(iterator.aclose(), timeout=2)
    assert active_session.get() is None
    assert provider.closed
    ledger = ReplayLedger(default_ledger_path())
    try:
        runs = await ledger.list_runs()
        assert len(runs) == 1
        assert (await ledger.get_run(runs[0]))[0]["blocked"]
    finally:
        await ledger.aclose()


@pytest.mark.asyncio
async def test_workflow_stream_shares_one_strict_budget_and_run():
    from meshflow import Workflow, CostCap
    from meshflow.core.ledger import ReplayLedger

    first, second = Provider(), Provider()
    first.release.set()
    second.release.set()
    workflow = Workflow(cost_cap=CostCap(0.025)).add(
        Agent("first", provider=first), Agent("second", provider=second)
    )
    chunks = [chunk async for chunk in workflow.astream("Generate text")]
    assert first.calls == 1 and second.calls == 0
    assert any(chunk.kind == "error" for chunk in chunks)
    done = chunks[-1]
    assert done.kind == "done" and done.metadata["cost_usd"] == pytest.approx(0.008)
    ledger = ReplayLedger(done.metadata["ledger_db"])
    try:
        records = await ledger.get_run(done.metadata["run_id"])
        assert len(records) == 2 and records[-1]["blocked"]
        assert (await ledger.verify_chain(done.metadata["run_id"]))["valid"]
    finally:
        await ledger.aclose()


@pytest.mark.asyncio
async def test_parallel_workflow_stream_close_finalizes_children():
    from meshflow import Workflow, CostCap
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.session import default_ledger_path

    first, second = Provider(), Provider()
    workflow = Workflow(cost_cap=CostCap(0.1)).add_parallel(
        Agent("first", provider=first), Agent("second", provider=second)
    )
    iterator = workflow.astream("Generate text")
    while (await asyncio.wait_for(anext(iterator), timeout=2)).kind != "token":
        pass
    await asyncio.wait_for(iterator.aclose(), timeout=2)
    assert first.closed and second.closed
    ledger = ReplayLedger(default_ledger_path())
    try:
        runs = await ledger.list_runs()
        assert len(runs) == 1
        records = await ledger.get_run(runs[0])
        assert len(records) == 2 and all(record["blocked"] for record in records)
        assert sum(record["cost_usd"] for record in records) == 0.04
    finally:
        await ledger.aclose()


def test_sync_workflow_stream_close_stops_worker_and_persists_partial_output():
    from meshflow import Workflow, CostCap
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.session import default_ledger_path

    provider = Provider()
    workflow = Workflow(cost_cap=CostCap(0.1)).add(Agent("sync", provider=provider))
    iterator = workflow.stream("Generate text")
    while next(iterator).kind != "token":
        pass
    iterator.close()
    assert provider.closed

    async def verify():
        ledger = ReplayLedger(default_ledger_path())
        try:
            runs = await ledger.list_runs()
            records = await ledger.get_run(runs[0])
            assert len(records) == 1 and records[0]["blocked"]
            assert records[0]["output_content"] == "a"
        finally:
            await ledger.aclose()

    asyncio.run(verify())
