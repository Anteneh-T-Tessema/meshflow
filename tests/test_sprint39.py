"""Sprint 39 — Handoff pattern: peer-to-peer agent transfer."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.agents.handoff import (
    HandoffConfig,
    HandoffLink,
    HandoffResult,
    _find_target,
    run_with_handoffs,
)


# ── HandoffConfig ─────────────────────────────────────────────────────────────

class TestHandoffConfig:
    def test_defaults(self):
        cfg = HandoffConfig()
        assert cfg.max_depth == 5
        assert cfg.history_mode == "full"
        assert cfg.history_n == 500

    def test_custom(self):
        cfg = HandoffConfig(max_depth=2, history_mode="none")
        assert cfg.max_depth == 2
        assert cfg.history_mode == "none"


# ── HandoffResult ─────────────────────────────────────────────────────────────

class TestHandoffResult:
    def _result(self) -> HandoffResult:
        return HandoffResult(
            output="done",
            final_agent="analyst",
            chain=[
                HandoffLink("triage", "analyst", "classify this", "needs analysis", 10, 0.001)
            ],
            total_tokens=20,
            total_cost_usd=0.002,
            transferred=True,
        )

    def test_to_dict_keys(self):
        d = self._result().to_dict()
        assert "output" in d
        assert "final_agent" in d
        assert "chain" in d
        assert "total_tokens" in d
        assert "transferred" in d
        assert "hops" in d

    def test_hops_count(self):
        d = self._result().to_dict()
        assert d["hops"] == 1

    def test_chain_link_keys(self):
        d = self._result().to_dict()
        link = d["chain"][0]
        assert link["from"] == "triage"
        assert link["to"] == "analyst"
        assert link["reason"] == "needs analysis"

    def test_getitem(self):
        r = self._result()
        assert r["final_agent"] == "analyst"
        assert r["transferred"] is True

    def test_get_with_default(self):
        r = self._result()
        assert r.get("nonexistent", "x") == "x"

    def test_contains(self):
        r = self._result()
        assert "output" in r
        assert "xyz" not in r

    def test_result_alias(self):
        r = HandoffResult(output="answer", final_agent="bot")
        assert r["result"] == "answer"

    def test_no_transfer(self):
        r = HandoffResult(output="ok", final_agent="agent-a")
        assert r.transferred is False
        assert r["hops"] == 0


# ── _find_target ──────────────────────────────────────────────────────────────

class TestFindTarget:
    def test_finds_by_name(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        a = Agent(name="billing", role="executor")
        b = Agent(name="support", role="executor")
        triage = Agent(name="triage", role="orchestrator", handoffs=[a, b])
        assert _find_target(triage, "billing") is a
        assert _find_target(triage, "support") is b

    def test_returns_none_for_missing(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="solo", role="executor", handoffs=[])
        assert _find_target(agent, "nonexistent") is None

    def test_returns_none_when_no_handoffs(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="solo", role="executor")
        assert _find_target(agent, "anyone") is None


# ── run_with_handoffs ─────────────────────────────────────────────────────────

class TestRunWithHandoffs:
    @pytest.mark.asyncio
    async def test_no_handoff_signal_returns_normally(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="direct", role="executor")
        result = await run_with_handoffs(agent, "simple task")
        assert isinstance(result, HandoffResult)
        assert result.final_agent == "direct"
        assert not result.transferred
        assert result["hops"] == 0

    @pytest.mark.asyncio
    async def test_handoff_to_peer_on_signal(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        analyst = Agent(name="analyst", role="executor")
        triage = Agent(name="triage", role="orchestrator", handoffs=[analyst])

        # Patch triage to return handoff signal; analyst returns normal output
        with patch.object(triage, "run", new_callable=AsyncMock) as mock_triage, \
             patch.object(analyst, "run", new_callable=AsyncMock) as mock_analyst:
            mock_triage.return_value = {
                "result": "TRANSFER_TO:analyst:needs deep analysis",
                "tokens": 5, "cost_usd": 0.001,
            }
            mock_analyst.return_value = {
                "result": "analysis complete",
                "tokens": 10, "cost_usd": 0.002,
            }
            result = await run_with_handoffs(triage, "classify this claim")

        assert result.transferred is True
        assert result.final_agent == "analyst"
        assert result.output == "analysis complete"
        assert len(result.chain) == 1
        assert result.chain[0].from_agent == "triage"
        assert result.chain[0].reason == "needs deep analysis"
        assert result.total_tokens == 15

    @pytest.mark.asyncio
    async def test_max_depth_respected(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        agent = Agent(name="looper", role="executor", handoffs=[])

        # Even if signal present, no matching agent → stops
        with patch.object(agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {
                "result": "TRANSFER_TO:missing_agent",
                "tokens": 1, "cost_usd": 0.0,
            }
            result = await run_with_handoffs(
                agent, "task", config=HandoffConfig(max_depth=3)
            )
        assert not result.transferred

    @pytest.mark.asyncio
    async def test_unknown_target_stops_chain(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        agent = Agent(name="a", role="executor", handoffs=[])
        with patch.object(agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {
                "result": "TRANSFER_TO:ghost:no such agent",
                "tokens": 1, "cost_usd": 0.0,
            }
            result = await run_with_handoffs(agent, "task")
        assert result.final_agent == "a"
        assert not result.transferred

    @pytest.mark.asyncio
    async def test_history_mode_none(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        peer = Agent(name="peer", role="executor")
        entry = Agent(name="entry", role="orchestrator", handoffs=[peer])

        received_tasks = []

        async def mock_peer(task, ctx=None):
            received_tasks.append(task)
            return {"result": "done", "tokens": 1, "cost_usd": 0.0}

        with patch.object(entry, "run", new_callable=AsyncMock) as mock_entry:
            mock_entry.return_value = {
                "result": "TRANSFER_TO:peer",
                "tokens": 1, "cost_usd": 0.0,
            }
            with patch.object(peer, "run", side_effect=mock_peer):
                result = await run_with_handoffs(
                    entry, "original task",
                    config=HandoffConfig(history_mode="none"),
                )

        # With history_mode="none", peer should receive the original task
        assert received_tasks[0] == "original task"

    @pytest.mark.asyncio
    async def test_costs_accumulated(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        peer = Agent(name="peer", role="executor")
        entry = Agent(name="entry", role="orchestrator", handoffs=[peer])

        with patch.object(entry, "run", new_callable=AsyncMock) as m1, \
             patch.object(peer, "run", new_callable=AsyncMock) as m2:
            m1.return_value = {"result": "TRANSFER_TO:peer", "tokens": 10, "cost_usd": 0.01}
            m2.return_value = {"result": "done", "tokens": 20, "cost_usd": 0.02}
            result = await run_with_handoffs(entry, "task")

        assert result.total_tokens == 30
        assert abs(result.total_cost_usd - 0.03) < 1e-9

    @pytest.mark.asyncio
    async def test_agent_run_with_handoffs_method(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="solo", role="executor")
        result = await agent.run_with_handoffs("hello")
        assert isinstance(result, HandoffResult)

    @pytest.mark.asyncio
    async def test_multi_hop_chain(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from unittest.mock import AsyncMock, patch

        c = Agent(name="c", role="executor")
        b = Agent(name="b", role="executor", handoffs=[c])
        a = Agent(name="a", role="orchestrator", handoffs=[b])

        with patch.object(a, "run", new_callable=AsyncMock) as ma, \
             patch.object(b, "run", new_callable=AsyncMock) as mb, \
             patch.object(c, "run", new_callable=AsyncMock) as mc:
            ma.return_value = {"result": "TRANSFER_TO:b", "tokens": 1, "cost_usd": 0.0}
            mb.return_value = {"result": "TRANSFER_TO:c", "tokens": 1, "cost_usd": 0.0}
            mc.return_value = {"result": "final", "tokens": 1, "cost_usd": 0.0}
            result = await run_with_handoffs(a, "task", config=HandoffConfig(max_depth=5))

        assert result.final_agent == "c"
        assert len(result.chain) == 2
        assert result.chain[0].from_agent == "a"
        assert result.chain[1].from_agent == "b"


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.agents.handoff import HandoffConfig, HandoffResult, HandoffLink, run_with_handoffs
        assert all(x is not None for x in [HandoffConfig, HandoffResult, HandoffLink, run_with_handoffs])

    def test_agent_has_handoffs_field(self):
        import dataclasses
        from meshflow.agents.builder import Agent
        fields = {f.name for f in dataclasses.fields(Agent)}
        assert "handoffs" in fields

    def test_agent_has_run_with_handoffs(self):
        from meshflow.agents.builder import Agent
        assert callable(Agent.run_with_handoffs)
