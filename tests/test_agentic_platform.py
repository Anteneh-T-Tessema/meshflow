"""Tests: ReActAgent, run_typed, Supervisor, AdversarialTeam, AgentSession, ComplianceProfiles."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_agent(name: str = "agent", role: str = "executor") -> Any:
    from meshflow.agents.builder import Agent
    return Agent(name=name, role=role)


def _fake_built(responses: list[str]) -> Any:
    """A real-like _BuiltAgent that returns canned responses from think()."""
    state = {"n": 0}

    async def _think(messages: Any, system: Any = None) -> tuple[str, int, float]:
        idx = min(state["n"], len(responses) - 1)
        state["n"] += 1
        return responses[idx], 10, 0.001

    from meshflow.agents.builder import _BuiltAgent, AgentConfig
    from meshflow.core.schemas import Policy, AgentRole
    config = AgentConfig(agent_id="test", role=AgentRole.EXECUTOR)
    built = _BuiltAgent(config, Policy(), [], False)
    built.think = _think  # type: ignore[method-assign]
    return built


def _async_run_mock(responses: list[dict[str, Any]]) -> Any:
    """AsyncMock for agent.run() that cycles through response dicts."""
    state = {"n": 0}

    async def _run(task: Any = "", context: Any = None) -> dict[str, Any]:
        idx = min(state["n"], len(responses) - 1)
        state["n"] += 1
        return responses[idx]

    return _run


# ── ReActAgent ────────────────────────────────────────────────────────────────


class TestReActAgent:
    def test_imports(self) -> None:
        from meshflow import ReActAgent, ReActResult, ThoughtStep
        assert ReActAgent is not None

    @pytest.mark.asyncio
    async def test_final_answer_first_step(self) -> None:
        from meshflow.agents.react import ReActAgent

        responses = ["Thought: I know.\nAction: Final Answer\nAction Input: Paris"]
        agent = _make_agent()
        react = ReActAgent(agent, max_steps=5)

        with patch.object(agent, "_build", return_value=_fake_built(responses)):
            result = await react.run("Capital of France?")

        assert result.finished is True
        assert result.steps_taken == 1
        assert "Paris" in result.answer

    @pytest.mark.asyncio
    async def test_tool_then_final_answer(self) -> None:
        from meshflow.agents.react import ReActAgent
        from meshflow.tools.registry import tool

        @tool(name="greet", description="Greet")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        responses = [
            'Thought: greet.\nAction: greet\nAction Input: {"name": "World"}',
            "Thought: done.\nAction: Final Answer\nAction Input: Hello, World!",
        ]
        agent = _make_agent()
        agent.tools = [greet]
        react = ReActAgent(agent, max_steps=5)

        with patch.object(agent, "_build", return_value=_fake_built(responses)):
            result = await react.run("Say hello")

        assert result.finished is True
        assert result.steps[0].observation == "Hello, World!"

    @pytest.mark.asyncio
    async def test_max_steps_hit(self) -> None:
        from meshflow.agents.react import ReActAgent

        responses = ['Thought: loop.\nAction: missing_tool\nAction Input: {}']
        agent = _make_agent()

        with patch.object(agent, "_build", return_value=_fake_built(responses)):
            result = await ReActAgent(agent, max_steps=3).run("task")

        assert result.finished is False
        assert result.steps_taken == 3

    @pytest.mark.asyncio
    async def test_unknown_tool_observation(self) -> None:
        from meshflow.agents.react import ReActAgent

        responses = [
            'Thought: use.\nAction: ghost_tool\nAction Input: {}',
            "Thought: failed.\nAction: Final Answer\nAction Input: fallback",
        ]
        agent = _make_agent()

        with patch.object(agent, "_build", return_value=_fake_built(responses)):
            result = await ReActAgent(agent, max_steps=5).run("task")

        assert "not found" in result.steps[0].observation

    def test_dataclass_fields(self) -> None:
        from meshflow.agents.react import ThoughtStep, ReActResult
        step = ThoughtStep("thought", "Final Answer", "answer", "", 1, 50, 0.005)
        assert step.tokens == 50
        result = ReActResult("answer", [step], 1, 50, 0.005, True, "agent")
        assert result.finished is True


# ── run_typed ─────────────────────────────────────────────────────────────────


class TestRunTyped:
    @pytest.mark.asyncio
    async def test_parses_pydantic_model(self) -> None:
        try:
            from pydantic import BaseModel
        except ImportError:
            pytest.skip("pydantic not installed")

        from meshflow.agents.builder import _BuiltAgent, AgentConfig
        from meshflow.core.schemas import Policy, AgentRole

        class Out(BaseModel):
            name: str
            score: float

        built = _fake_built(['{"name": "Alice", "score": 0.9}'])
        result = await built.run_typed("task", Out)
        assert result.name == "Alice"

    @pytest.mark.asyncio
    async def test_retries_on_bad_json(self) -> None:
        try:
            from pydantic import BaseModel
        except ImportError:
            pytest.skip("pydantic not installed")

        class Out(BaseModel):
            value: int

        built = _fake_built(["not json at all", '{"value": 7}'])
        result = await built.run_typed("task", Out)
        assert result.value == 7

    @pytest.mark.asyncio
    async def test_raises_after_two_failures(self) -> None:
        try:
            from pydantic import BaseModel
        except ImportError:
            pytest.skip("pydantic not installed")

        class Out(BaseModel):
            value: int

        built = _fake_built(["not json", "still not json"])
        with pytest.raises(ValueError):
            await built.run_typed("task", Out)

    @pytest.mark.asyncio
    async def test_raises_for_non_pydantic(self) -> None:
        built = _fake_built(["anything"])
        with pytest.raises(TypeError):
            await built.run_typed("task", dict)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_agent_run_typed(self) -> None:
        try:
            from pydantic import BaseModel
        except ImportError:
            pytest.skip("pydantic not installed")

        class Plan(BaseModel):
            steps: list[str]

        agent = _make_agent("p", "orchestrator")
        with patch.object(agent, "_build", return_value=_fake_built(['{"steps": ["a", "b"]}'])):
            result = await agent.run_typed("plan", Plan)

        assert result.steps == ["a", "b"]


# ── Supervisor ────────────────────────────────────────────────────────────────


class TestSupervisor:
    def test_imports(self) -> None:
        from meshflow import Supervisor, SupervisorResult
        assert Supervisor is not None

    @pytest.mark.asyncio
    async def test_done_on_first_synthesis(self) -> None:
        from meshflow.agents.supervisor import Supervisor

        orch = _make_agent("orch", "orchestrator")
        worker = _make_agent("worker", "executor")

        orch_run_responses = [
            {"result": "no plan", "tokens": 10, "cost_usd": 0.0},          # plan (fallback)
            {"result": "DONE: Final answer.", "tokens": 10, "cost_usd": 0.0},  # synthesis
        ]
        worker_run_responses = [
            {"result": "worker result", "tokens": 10, "cost_usd": 0.0},
        ]

        with (
            patch.object(orch, "run", side_effect=_async_run_mock(orch_run_responses)),
            patch.object(orch, "run_typed", side_effect=Exception("no pydantic")),
            patch.object(worker, "run", side_effect=_async_run_mock(worker_run_responses)),
        ):
            result = await Supervisor(orch, [worker]).run("task")

        assert "Final answer" in result.final_answer
        assert result.rounds == 1

    @pytest.mark.asyncio
    async def test_max_rounds_enforced(self) -> None:
        from meshflow.agents.supervisor import Supervisor

        orch = _make_agent("orch", "orchestrator")
        worker = _make_agent("w", "executor")

        orch_responses = [{"result": "not done", "tokens": 5, "cost_usd": 0.0}] * 10
        worker_responses = [{"result": "partial", "tokens": 5, "cost_usd": 0.0}] * 10

        with (
            patch.object(orch, "run", side_effect=_async_run_mock(orch_responses)),
            patch.object(orch, "run_typed", side_effect=Exception("fallback")),
            patch.object(worker, "run", side_effect=_async_run_mock(worker_responses)),
        ):
            result = await Supervisor(orch, [worker], max_rounds=2).run("task")

        assert result.rounds == 2

    def test_worker_registry(self) -> None:
        from meshflow.agents.supervisor import Supervisor

        orch = _make_agent("orch")
        w1 = _make_agent("alpha")
        w2 = _make_agent("beta")
        sv = Supervisor(orch, [w1, w2])
        assert set(sv._workers) == {"alpha", "beta"}

    @pytest.mark.asyncio
    async def test_missing_worker_recorded(self) -> None:
        try:
            from pydantic import BaseModel
        except ImportError:
            pytest.skip("pydantic not installed")

        from meshflow.agents.supervisor import Supervisor, _make_plan_model

        orch = _make_agent("orch", "orchestrator")
        worker = _make_agent("real", "executor")

        PlanModel = _make_plan_model()
        if PlanModel is None:
            pytest.skip("pydantic not installed")

        fake_plan = PlanModel(steps=[{"worker_name": "ghost", "subtask": "do it"}])

        orch_run_responses = [{"result": "DONE: ok", "tokens": 5, "cost_usd": 0.0}]
        worker_responses = [{"result": "real output", "tokens": 5, "cost_usd": 0.0}]

        async def fake_run_typed(*a: Any, **kw: Any) -> Any:
            return fake_plan

        with (
            patch.object(orch, "run_typed", side_effect=fake_run_typed),
            patch.object(orch, "run", side_effect=_async_run_mock(orch_run_responses)),
            patch.object(worker, "run", side_effect=_async_run_mock(worker_responses)),
        ):
            result = await Supervisor(orch, [worker]).run("task")

        assert "ghost" in result.worker_outputs
        assert "not found" in result.worker_outputs["ghost"]


# ── AdversarialTeam ───────────────────────────────────────────────────────────


class TestAdversarialTeam:
    def test_imports(self) -> None:
        from meshflow import AdversarialTeam, AdversarialResult
        assert AdversarialTeam is not None

    @pytest.mark.asyncio
    async def test_accept_verdict(self) -> None:
        from meshflow.agents.adversarial import AdversarialTeam

        proposer = _make_agent("p", "executor")
        attacker = _make_agent("a", "critic")
        judge = _make_agent("j", "orchestrator")

        proposer_r = [{"result": "Paris", "tokens": 10, "cost_usd": 0.0}]
        attacker_r = [{"result": "no issues", "tokens": 10, "cost_usd": 0.0}]
        judge_r = [{"result": '{"verdict": "accept", "reasoning": "ok", "revised_answer": ""}', "tokens": 10, "cost_usd": 0.0}]

        with (
            patch.object(proposer, "run", side_effect=_async_run_mock(proposer_r)),
            patch.object(attacker, "run", side_effect=_async_run_mock(attacker_r)),
            patch.object(judge, "run", side_effect=_async_run_mock(judge_r)),
        ):
            result = await AdversarialTeam(proposer, attacker, judge).run("task")

        assert result.verdict == "accept"
        assert result.proposal == "Paris"

    @pytest.mark.asyncio
    async def test_reject_verdict(self) -> None:
        from meshflow.agents.adversarial import AdversarialTeam

        proposer = _make_agent("p")
        attacker = _make_agent("a")
        judge = _make_agent("j")

        with (
            patch.object(proposer, "run", side_effect=_async_run_mock([{"result": "wrong", "tokens": 5, "cost_usd": 0.0}])),
            patch.object(attacker, "run", side_effect=_async_run_mock([{"result": "bad", "tokens": 5, "cost_usd": 0.0}])),
            patch.object(judge, "run", side_effect=_async_run_mock([{"result": '{"verdict": "reject", "reasoning": "bad", "revised_answer": ""}', "tokens": 5, "cost_usd": 0.0}])),
        ):
            result = await AdversarialTeam(proposer, attacker, judge).run("task")

        assert result.verdict == "reject"

    @pytest.mark.asyncio
    async def test_revise_uses_revised_answer(self) -> None:
        from meshflow.agents.adversarial import AdversarialTeam

        proposer = _make_agent("p")
        attacker = _make_agent("a")
        judge = _make_agent("j")

        proposer_r = [{"result": "draft", "tokens": 5, "cost_usd": 0.0}]
        attacker_r = [{"result": "needs work", "tokens": 5, "cost_usd": 0.0}, {"result": "ok now", "tokens": 5, "cost_usd": 0.0}]
        judge_r = [
            {"result": '{"verdict": "revise", "reasoning": "improve", "revised_answer": "Improved answer"}', "tokens": 5, "cost_usd": 0.0},
            {"result": '{"verdict": "accept", "reasoning": "good", "revised_answer": ""}', "tokens": 5, "cost_usd": 0.0},
        ]

        with (
            patch.object(proposer, "run", side_effect=_async_run_mock(proposer_r)),
            patch.object(attacker, "run", side_effect=_async_run_mock(attacker_r)),
            patch.object(judge, "run", side_effect=_async_run_mock(judge_r)),
        ):
            result = await AdversarialTeam(proposer, attacker, judge, max_revisions=1).run("task")

        assert result.final_answer == "Improved answer"

    def test_parse_verdict_keywords(self) -> None:
        from meshflow.agents.adversarial import _parse_verdict

        _, v, _ = _parse_verdict("this is rejected", "orig")
        assert v == "reject"
        _, v, _ = _parse_verdict("please revise this", "orig")
        assert v == "revise"
        _, v, _ = _parse_verdict("looks correct", "orig")
        assert v == "accept"

    @pytest.mark.asyncio
    async def test_tokens_accumulated(self) -> None:
        from meshflow.agents.adversarial import AdversarialTeam

        proposer = _make_agent("p")
        attacker = _make_agent("a")
        judge = _make_agent("j")

        with (
            patch.object(proposer, "run", side_effect=_async_run_mock([{"result": "r", "tokens": 10, "cost_usd": 0.001}])),
            patch.object(attacker, "run", side_effect=_async_run_mock([{"result": "c", "tokens": 10, "cost_usd": 0.001}])),
            patch.object(judge, "run", side_effect=_async_run_mock([{"result": '{"verdict":"accept","reasoning":"ok","revised_answer":""}', "tokens": 10, "cost_usd": 0.001}])),
        ):
            result = await AdversarialTeam(proposer, attacker, judge).run("task")

        assert result.total_tokens == 30


# ── AgentSession ──────────────────────────────────────────────────────────────


class TestAgentSession:
    def test_imports(self) -> None:
        from meshflow import AgentSession, SessionResult, Turn
        assert AgentSession is not None

    @pytest.mark.asyncio
    async def test_single_turn(self) -> None:
        from meshflow.agents.session import AgentSession

        agent = _make_agent("assistant")
        session = AgentSession(agent)

        run_r = [{"result": "Hello back!", "tokens": 10, "cost_usd": 0.001}]
        with patch.object(agent, "run", side_effect=_async_run_mock(run_r)):
            result = await session.chat("Hello!")

        assert result.reply == "Hello back!"
        assert result.turn_number == 1
        assert len(session.history) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_multi_turn_accumulates_tokens(self) -> None:
        from meshflow.agents.session import AgentSession

        agent = _make_agent("assistant")
        session = AgentSession(agent)

        run_r = [{"result": f"r{i}", "tokens": 10, "cost_usd": 0.001} for i in range(5)]
        with patch.object(agent, "run", side_effect=_async_run_mock(run_r)):
            await session.chat("m1")
            await session.chat("m2")
            await session.chat("m3")

        assert session.total_tokens == 30

    @pytest.mark.asyncio
    async def test_reset(self) -> None:
        from meshflow.agents.session import AgentSession

        agent = _make_agent("assistant")
        session = AgentSession(agent)

        run_r = [{"result": "reply", "tokens": 10, "cost_usd": 0.001}]
        with patch.object(agent, "run", side_effect=_async_run_mock(run_r)):
            await session.chat("hello")

        session.reset()
        assert session.history == []
        assert session.total_tokens == 0

    @pytest.mark.asyncio
    async def test_compression_on_overflow(self) -> None:
        from meshflow.agents.session import AgentSession

        agent = _make_agent("assistant")
        session = AgentSession(agent, max_history_turns=4)

        run_r = [{"result": f"r{i}", "tokens": 10, "cost_usd": 0.001} for i in range(20)]
        with patch.object(agent, "run", side_effect=_async_run_mock(run_r)):
            for i in range(5):
                await session.chat(f"msg {i}")

        # After 5 turns (10 history items) > max_history_turns=4, compression should fire
        assert len(session.history) <= 4

    @pytest.mark.asyncio
    async def test_history_includes_both_roles(self) -> None:
        from meshflow.agents.session import AgentSession, Turn

        agent = _make_agent("assistant")
        session = AgentSession(agent)

        run_r = [{"result": "reply", "tokens": 5, "cost_usd": 0.0}]
        with patch.object(agent, "run", side_effect=_async_run_mock(run_r)):
            await session.chat("question")

        roles = [t.role for t in session.history]
        assert "user" in roles
        assert "assistant" in roles


# ── Compliance profiles ───────────────────────────────────────────────────────


class TestComplianceProfiles:
    def test_imports(self) -> None:
        from meshflow import ComplianceProfile, compliance_profile, list_profiles
        assert ComplianceProfile is not None

    @pytest.mark.parametrize("name,threshold", [
        ("hipaa", 0.70),
        ("sox", 0.75),
        ("gdpr", 0.72),
        ("pci", 0.80),
        ("pci-dss", 0.80),
        ("nerc", 0.85),
        ("standard", 0.90),
        ("research", 0.95),
    ])
    def test_hitl_thresholds(self, name: str, threshold: float) -> None:
        from meshflow.core.compliance import compliance_profile
        assert compliance_profile(name).hitl_threshold == threshold

    def test_hipaa_phi_scrubbing(self) -> None:
        from meshflow.core.compliance import compliance_profile
        assert compliance_profile("hipaa").phi_scrubbing is True

    def test_sox_no_phi_scrubbing(self) -> None:
        from meshflow.core.compliance import compliance_profile
        assert compliance_profile("sox").phi_scrubbing is False

    def test_hipaa_retention_7_years(self) -> None:
        from meshflow.core.compliance import compliance_profile
        assert compliance_profile("hipaa").audit_retention_days == 2555

    def test_to_policy_returns_policy(self) -> None:
        from meshflow.core.compliance import compliance_profile
        from meshflow.core.schemas import Policy
        assert isinstance(compliance_profile("hipaa").to_policy(), Policy)

    def test_hipaa_policy_scrub_phi_set(self) -> None:
        from meshflow.core.compliance import compliance_profile
        assert compliance_profile("hipaa").to_policy().scrub_phi is True

    def test_unknown_profile_raises(self) -> None:
        from meshflow.core.compliance import compliance_profile
        with pytest.raises(ValueError, match="Unknown compliance profile"):
            compliance_profile("gdpr-california-edition")

    def test_list_profiles_unique(self) -> None:
        from meshflow.core.compliance import list_profiles
        names = list_profiles()
        assert len(names) == len(set(names))
        assert "HIPAA" in names
        assert "SOX" in names
        assert "GDPR" in names

    def test_case_insensitive(self) -> None:
        from meshflow.core.compliance import compliance_profile
        assert compliance_profile("HIPAA").hitl_threshold == compliance_profile("hipaa").hitl_threshold

    def test_mesh_compliance_kwarg(self) -> None:
        from meshflow.core.mesh import Mesh
        mesh = Mesh(compliance="hipaa")
        assert mesh._compliance_profile is not None
        assert mesh._compliance_profile.name == "HIPAA"

    def test_mesh_compliance_sets_legal_critical_policy(self) -> None:
        from meshflow.core.mesh import Mesh
        from meshflow.core.schemas import PolicyMode
        mesh = Mesh(compliance="hipaa")
        assert mesh._policy.mode == PolicyMode.LEGAL_CRITICAL

    def test_mesh_no_compliance_no_profile(self) -> None:
        from meshflow.core.mesh import Mesh
        assert Mesh()._compliance_profile is None

    def test_mesh_explicit_policy_not_overridden(self) -> None:
        from meshflow.core.mesh import Mesh
        from meshflow.core.schemas import Policy, PolicyMode
        pol = Policy(mode=PolicyMode.STANDARD)
        mesh = Mesh(compliance="hipaa", policy=pol)
        assert mesh._policy.mode == PolicyMode.STANDARD

    def test_case_insensitive_lookup(self) -> None:
        from meshflow.core.compliance import compliance_profile
        p1 = compliance_profile("HIPAA")
        p2 = compliance_profile("hipaa")
        assert p1.hitl_threshold == p2.hitl_threshold

    @pytest.mark.parametrize("name", ["hipaa", "sox", "gdpr", "pci", "nerc", "standard"])
    def test_all_profiles_have_verifier_domains_list(self, name: str) -> None:
        from meshflow.core.compliance import compliance_profile
        assert isinstance(compliance_profile(name).verifier_domains, list)

    def test_require_evidence_on_regulated_profiles(self) -> None:
        from meshflow.core.compliance import compliance_profile
        for name in ["hipaa", "sox", "gdpr", "pci", "nerc"]:
            assert compliance_profile(name).require_evidence is True

    def test_standard_no_require_evidence(self) -> None:
        from meshflow.core.compliance import compliance_profile
        assert compliance_profile("standard").require_evidence is False


# ── ProviderRouter ────────────────────────────────────────────────────────────


class TestProviderRouter:
    def test_imports(self) -> None:
        from meshflow import ProviderRouter, auto_provider, auto_model
        assert ProviderRouter is not None
        assert auto_model is not None

    def test_compliance_always_gives_opus(self) -> None:
        from meshflow.agents.router import ProviderRouter, _OPUS
        r = ProviderRouter()
        for regime in ["hipaa", "sox", "gdpr", "pci", "pci-dss", "nerc"]:
            _, model = r.route("executor", budget_usd=5.0, compliance=regime)
            assert model == _OPUS, f"{regime} should map to opus, got {model}"

    def test_low_budget_gives_haiku(self) -> None:
        from meshflow.agents.router import ProviderRouter, _HAIKU
        r = ProviderRouter()
        _, model = r.route("executor", budget_usd=0.001)
        assert model == _HAIKU

    def test_guardian_always_opus(self) -> None:
        from meshflow.agents.router import ProviderRouter, _OPUS
        r = ProviderRouter()
        _, model = r.route("guardian", budget_usd=1.0)
        assert model == _OPUS

    def test_planner_gets_sonnet(self) -> None:
        from meshflow.agents.router import ProviderRouter, _SONNET
        r = ProviderRouter()
        _, model = r.route("planner", budget_usd=0.5)
        assert model == _SONNET

    def test_custom_override(self) -> None:
        from meshflow.agents.router import ProviderRouter, _HAIKU
        r = ProviderRouter()
        r.set_rule("researcher", model=_HAIKU)
        _, model = r.route("researcher", budget_usd=1.0)
        assert model == _HAIKU

    def test_compliance_beats_low_budget(self) -> None:
        from meshflow.agents.router import ProviderRouter, _OPUS
        r = ProviderRouter()
        # Low budget but compliance → opus
        _, model = r.route("executor", budget_usd=0.001, compliance="hipaa")
        assert model == _OPUS

    def test_provider_returned_is_anthropic_provider(self) -> None:
        from meshflow.agents.router import ProviderRouter
        from meshflow.agents.base import AnthropicProvider
        r = ProviderRouter()
        provider, _ = r.route("executor")
        assert isinstance(provider, AnthropicProvider)

    def test_explain_returns_string(self) -> None:
        from meshflow.agents.router import ProviderRouter
        r = ProviderRouter()
        msg = r.explain("planner", budget_usd=0.5)
        assert "model=" in msg

    def test_explain_compliance_reason(self) -> None:
        from meshflow.agents.router import ProviderRouter
        r = ProviderRouter()
        msg = r.explain("executor", budget_usd=5.0, compliance="hipaa")
        assert "compliance" in msg.lower()

    def test_auto_model_function(self) -> None:
        from meshflow.agents.router import auto_model, _OPUS
        model = auto_model("executor", compliance="hipaa")
        assert model == _OPUS

    def test_auto_model_tight_budget(self) -> None:
        from meshflow.agents.router import auto_model, _HAIKU
        model = auto_model("executor", budget_usd=0.005)
        assert model == _HAIKU

    def test_agent_role_enum_input(self) -> None:
        from meshflow.agents.router import ProviderRouter
        from meshflow.core.schemas import AgentRole
        r = ProviderRouter()
        _, model_str = r.route(AgentRole.GUARDIAN)
        _, model_enum = r.route("guardian")
        assert model_str == model_enum

    def test_unknown_role_falls_back_to_sonnet(self) -> None:
        from meshflow.agents.router import ProviderRouter, _SONNET
        r = ProviderRouter()
        _, model = r.route("some-new-role-not-in-table", budget_usd=1.0)
        assert model == _SONNET


# ── AgentMemory (4-tier) ──────────────────────────────────────────────────────


class TestAgentMemory:
    def test_imports(self) -> None:
        from meshflow import AgentMemory, MemoryItem
        assert AgentMemory is not None
        assert MemoryItem is not None

    def test_working_memory_accumulates(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t", max_working=5)
        for i in range(4):
            mem.add(f"entry {i}")
        assert mem.working_count == 4

    def test_overflow_promotes_to_episodic(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t", max_working=3)
        for i in range(4):
            mem.add(f"entry {i}")
        assert mem.working_count == 3
        assert mem.episodic_count == 1

    def test_recall_finds_relevant(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t", max_working=10)
        mem.add("HIPAA requires minimum-necessary principle for PHI disclosure.")
        mem.add("SOX requires dual-authorization for journal entries over $10k.")
        mem.add("Treatment is a TPO exception — no authorization needed.")

        results = mem.recall("What are HIPAA treatment exceptions?", top_k=2)
        joined = " ".join(results).lower()
        assert "treatment" in joined or "tpo" in joined.upper() or "hipaa" in joined

    def test_recall_overflow_still_searchable(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t", max_working=2)
        mem.add("Treatment purpose is a TPO exception.")
        mem.add("Audit logs must be retained 6 years.")
        mem.add("Business associates require BAAs.")  # pushes oldest to episodic

        # Oldest entry is now in episodic but still in BM25 index
        results = mem.recall("treatment TPO exception", top_k=3)
        all_text = " ".join(results)
        assert "TPO" in all_text or "treatment" in all_text.lower()

    def test_context_string_not_empty(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t")
        mem.add("some content here")
        ctx = mem.context_string()
        assert "some content here" in ctx

    def test_context_string_respects_max_chars(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t")
        for i in range(20):
            mem.add("x" * 100)
        ctx = mem.context_string(max_chars=200)
        assert len(ctx) <= 250  # slight overhead for prefix/newlines

    def test_record_outcome_goes_to_procedural(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t")
        mem.record_outcome("node1", success=True, confidence=0.9, verifier_score=0.85)
        assert mem.stats()["procedural"] == 1
        assert mem.working_count == 0  # procedural doesn't go to working

    def test_reset_clears_all_tiers(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t", max_working=2)
        for i in range(4):
            mem.add(f"entry {i}")
        mem.record_outcome("n", True, 0.9)
        mem.reset()
        assert mem.total_items == 0
        assert mem.working_count == 0
        assert mem.episodic_count == 0

    def test_disabled_memory_no_ops(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t", enabled=False)
        mem.add("should be ignored")
        assert mem.total_items == 0
        assert mem.recall("anything") == []
        assert mem.context_string() == ""

    def test_stats_returns_dict(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="test-agent")
        mem.add("data")
        stats = mem.stats()
        assert stats["agent_id"] == "test-agent"
        assert "working" in stats
        assert "episodic" in stats
        assert "procedural" in stats
        assert "steps" in stats

    def test_built_agent_uses_agentmemory(self) -> None:
        from meshflow import Agent
        from meshflow.intelligence.memory import AgentMemory

        agent = Agent(name="mem-test", role="executor", memory=True)
        built = agent._build()
        assert isinstance(built._memory, AgentMemory)
        assert built._memory._enabled is True

    def test_built_agent_memory_disabled(self) -> None:
        from meshflow import Agent
        from meshflow.intelligence.memory import AgentMemory

        agent = Agent(name="no-mem", role="executor", memory=False)
        built = agent._build()
        assert isinstance(built._memory, AgentMemory)
        assert built._memory._enabled is False

    def test_recall_API_on_built_agent(self) -> None:
        from meshflow import Agent

        agent = Agent(name="recall-test", role="executor", memory=True)
        built = agent._build()
        built._memory.add("The patient has type 2 diabetes.")
        built._memory.add("Referral was approved by Dr. Smith.")

        results = built.recall("patient diagnosis")
        assert isinstance(results, list)
        assert len(results) >= 1
        assert any("diabetes" in r or "patient" in r for r in results)

    def test_bm25_index_searches_episodic(self) -> None:
        from meshflow.intelligence.memory import AgentMemory
        mem = AgentMemory(agent_id="t", max_working=1)
        mem.add("Alpha content about regulations")
        mem.add("Beta content about compliance")  # pushes alpha to episodic

        # Alpha is episodic but still in BM25
        results = mem.recall("regulations alpha", top_k=3)
        joined = " ".join(results)
        assert "Alpha" in joined or "regulations" in joined.lower()


# ── Agent.stream() ────────────────────────────────────────────────────────────


class TestAgentStream:
    def test_stream_method_exists(self) -> None:
        from meshflow import Agent
        agent = Agent(name="s", role="executor")
        assert hasattr(agent, "stream")

    @pytest.mark.asyncio
    async def test_stream_yields_tokens(self) -> None:
        from meshflow import Agent
        from meshflow.core.schemas import TokenChunk
        from unittest.mock import patch

        agent = Agent(name="s", role="executor")

        async def fake_stream(**kw: Any) -> Any:
            for word in ["Hello", " ", "world"]:
                yield TokenChunk(text=word, agent_id="s", step_id="step", run_id="run")

        with patch.object(agent._build()._provider.__class__, "stream_complete", fake_stream):
            # We need to patch the provider on the built agent
            built = agent._build()

            async def _fake_stream_complete(self_inner: Any, **kw: Any) -> Any:
                for word in ["Hello", " ", "world"]:
                    yield TokenChunk(text=word, agent_id="s", step_id="step", run_id="run")

            built._provider.stream_complete = _fake_stream_complete.__get__(  # type: ignore[attr-defined]
                built._provider, type(built._provider)
            )

            with patch.object(agent, "_build", return_value=built):
                tokens = []
                async for t in agent.stream("say hello"):
                    tokens.append(t)

        assert "".join(tokens) == "Hello world"


# ── GroupChat ─────────────────────────────────────────────────────────────────


class TestGroupChat:
    def _make_agent(self, name: str, reply: str = "ok") -> Any:
        from meshflow import Agent
        from unittest.mock import AsyncMock, patch

        agent = Agent(name=name, role="executor")
        agent.run = AsyncMock(return_value={  # type: ignore[method-assign]
            "result": reply,
            "tokens": 10,
            "cost_usd": 0.001,
            "stated_confidence": 0.9,
        })
        return agent

    def test_groupchat_requires_at_least_one_agent(self) -> None:
        from meshflow.agents.conversation import GroupChat
        with pytest.raises(ValueError, match="at least one"):
            GroupChat(agents=[])

    def test_groupchat_custom_requires_speaker_fn(self) -> None:
        from meshflow.agents.conversation import GroupChat
        a = self._make_agent("a")
        with pytest.raises(ValueError, match="speaker_fn"):
            GroupChat(agents=[a], speaker_selection="custom")

    def test_round_robin_cycles(self) -> None:
        from meshflow.agents.conversation import GroupChat
        a, b, c = self._make_agent("a"), self._make_agent("b"), self._make_agent("c")
        chat = GroupChat(agents=[a, b, c], speaker_selection="round_robin")
        assert chat._pick_next().name == "a"
        assert chat._pick_next().name == "b"
        assert chat._pick_next().name == "c"
        assert chat._pick_next().name == "a"  # wraps

    def test_random_speaker_returns_valid_agent(self) -> None:
        from meshflow.agents.conversation import GroupChat
        a, b = self._make_agent("a"), self._make_agent("b")
        chat = GroupChat(agents=[a, b], speaker_selection="random")
        for _ in range(10):
            chosen = chat._pick_next()
            assert chosen in (a, b)

    def test_custom_speaker_fn(self) -> None:
        from meshflow.agents.conversation import GroupChat
        a, b = self._make_agent("a"), self._make_agent("b")

        def always_b(messages: Any, agents: Any) -> Any:
            return b

        chat = GroupChat(agents=[a, b], speaker_selection="custom", speaker_fn=always_b)
        for _ in range(3):
            assert chat._pick_next().name == "b"

    def test_termination_by_keyword(self) -> None:
        from meshflow.agents.conversation import GroupChat, ChatMessage
        chat = GroupChat(agents=[self._make_agent("a")], termination="TERMINATE")
        chat._add(ChatMessage(sender="a", content="All done. TERMINATE"))
        assert chat._should_terminate()

    def test_termination_by_callable(self) -> None:
        from meshflow.agents.conversation import GroupChat, ChatMessage
        chat = GroupChat(
            agents=[self._make_agent("a")],
            termination=lambda msgs: len(msgs) >= 2,
        )
        chat._add(ChatMessage(sender="user", content="go"))
        assert not chat._should_terminate()
        chat._add(ChatMessage(sender="a", content="done"))
        assert chat._should_terminate()

    def test_no_termination_when_empty(self) -> None:
        from meshflow.agents.conversation import GroupChat
        chat = GroupChat(agents=[self._make_agent("a")])
        assert not chat._should_terminate()

    @pytest.mark.asyncio
    async def test_manager_run_stops_at_max_turns(self) -> None:
        from meshflow.agents.conversation import GroupChat, GroupChatManager
        a = self._make_agent("a", reply="still going")
        chat = GroupChat(agents=[a], max_turns=3, termination="STOP_NEVER_USED")
        manager = GroupChatManager(chat)
        result = await manager.run("start")
        assert result.total_turns == 3

    @pytest.mark.asyncio
    async def test_manager_run_stops_on_terminate_keyword(self) -> None:
        from meshflow.agents.conversation import GroupChat, GroupChatManager
        a = self._make_agent("a", reply="task done TERMINATE")
        chat = GroupChat(agents=[a], max_turns=20)
        manager = GroupChatManager(chat)
        result = await manager.run("start")
        assert result.total_turns == 1  # terminates on turn 1
        assert result.terminated

    @pytest.mark.asyncio
    async def test_manager_run_result_fields(self) -> None:
        from meshflow.agents.conversation import GroupChat, GroupChatManager
        a = self._make_agent("a", reply="TERMINATE")
        chat = GroupChat(agents=[a], max_turns=5)
        manager = GroupChatManager(chat)
        result = await manager.run("hello")
        assert "a" in result.participants
        assert result.total_tokens > 0
        assert result.last_message

    def test_conversation_result_transcript(self) -> None:
        from meshflow.agents.conversation import ConversationResult, ChatMessage
        msgs = [
            ChatMessage(sender="user", content="hello"),
            ChatMessage(sender="a", content="world"),
        ]
        r = ConversationResult(
            messages=msgs,
            total_turns=1,
            total_tokens=20,
            total_cost_usd=0.01,
            terminated=True,
            last_message="world",
            participants=["a"],
        )
        transcript = r.transcript()
        assert "user" in transcript
        assert "world" in transcript

    def test_conversation_result_messages_from(self) -> None:
        from meshflow.agents.conversation import ConversationResult, ChatMessage
        msgs = [
            ChatMessage(sender="alice", content="A"),
            ChatMessage(sender="bob", content="B"),
            ChatMessage(sender="alice", content="A2"),
        ]
        r = ConversationResult(
            messages=msgs, total_turns=3, total_tokens=0,
            total_cost_usd=0.0, terminated=True, last_message="A2",
            participants=["alice", "bob"],
        )
        assert len(r.messages_from("alice")) == 2
        assert len(r.messages_from("bob")) == 1

    @pytest.mark.asyncio
    async def test_manager_stream_yields_messages(self) -> None:
        from meshflow.agents.conversation import GroupChat, GroupChatManager
        a = self._make_agent("a", reply="step1 TERMINATE")
        chat = GroupChat(agents=[a], max_turns=5)
        manager = GroupChatManager(chat)
        msgs = []
        async for m in manager.stream("go"):
            msgs.append(m)
        assert len(msgs) >= 2  # seed + at least one agent turn
        senders = [m.sender for m in msgs]
        assert "user" in senders
        assert "a" in senders

    @pytest.mark.asyncio
    async def test_groupchat_exported_from_meshflow(self) -> None:
        from meshflow import GroupChat, GroupChatManager, ConversationResult
        assert GroupChat is not None
        assert GroupChatManager is not None
        assert ConversationResult is not None


# ── GovernedToolRegistry ──────────────────────────────────────────────────────


class TestGovernedToolRegistry:
    @pytest.mark.asyncio
    async def test_register_and_call_sync(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry, ToolPermission
        reg = ToolRegistry()

        @reg.register("add", permissions=[ToolPermission.READ_ONLY])
        def add(x: int, y: int) -> int:
            return x + y

        result = await reg.call("add", agent_id="tester", args={"x": 3, "y": 4})
        assert result == 7

    @pytest.mark.asyncio
    async def test_register_and_call_async(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry, ToolPermission
        reg = ToolRegistry()

        @reg.register("greet", permissions=[ToolPermission.READ_ONLY])
        async def greet(name: str) -> str:
            return f"Hello {name}"

        result = await reg.call("greet", agent_id="a", args={"name": "World"})
        assert result == "Hello World"

    @pytest.mark.asyncio
    async def test_tool_not_found_raises(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry, ToolNotFoundError
        reg = ToolRegistry()
        with pytest.raises(ToolNotFoundError):
            await reg.call("missing_tool", agent_id="a")

    @pytest.mark.asyncio
    async def test_permission_denied_by_registry(self) -> None:
        from meshflow.agents.tool_registry import (
            ToolRegistry, ToolPermission, PermissionDeniedError
        )
        reg = ToolRegistry(allowed_permissions=[ToolPermission.READ_ONLY])

        @reg.register("dangerous", permissions=[ToolPermission.CODE_EXEC])
        def dangerous() -> str:
            return "boom"

        with pytest.raises(PermissionDeniedError):
            await reg.call("dangerous", agent_id="a")

    @pytest.mark.asyncio
    async def test_audit_log_records_success(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry, ToolPermission
        reg = ToolRegistry()
        reg.register_tool("noop", lambda: "ok", permissions=[ToolPermission.READ_ONLY])
        await reg.call("noop", agent_id="agent-x")
        log = reg.audit_log(agent_id="agent-x")
        assert len(log) == 1
        assert log[0].outcome == "success"
        assert log[0].tool_name == "noop"

    @pytest.mark.asyncio
    async def test_audit_log_records_error(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry, ToolPermission
        reg = ToolRegistry()

        def broken() -> str:
            raise RuntimeError("oops")

        reg.register_tool("broken", broken, permissions=[ToolPermission.READ_ONLY])
        with pytest.raises(RuntimeError):
            await reg.call("broken", agent_id="a")
        log = reg.audit_log(tool_name="broken")
        assert log[0].outcome == "error"
        assert "oops" in log[0].error

    def test_list_tools(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry, ToolPermission
        reg = ToolRegistry()
        reg.register_tool("t1", lambda: None, permissions=[ToolPermission.NETWORK])
        reg.register_tool("t2", lambda: None, permissions=[ToolPermission.READ_ONLY])
        all_tools = reg.list_tools()
        assert len(all_tools) == 2
        network_tools = reg.list_tools(permission=ToolPermission.NETWORK)
        assert len(network_tools) == 1
        assert network_tools[0]["name"] == "t1"

    def test_get_schema_reflects_fn_signature(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry, ToolPermission
        reg = ToolRegistry()

        def search(query: str, max_results: int) -> str:
            return ""

        reg.register_tool("search", search, permissions=[ToolPermission.NETWORK])
        schema = reg.get_schema("search")
        assert "query" in schema["properties"]
        assert "max_results" in schema["properties"]
        assert "query" in schema["required"]

    def test_len_and_contains(self) -> None:
        from meshflow.agents.tool_registry import ToolRegistry
        reg = ToolRegistry()
        reg.register_tool("a", lambda: None)
        reg.register_tool("b", lambda: None)
        assert len(reg) == 2
        assert "a" in reg
        assert "c" not in reg

    def test_exported_from_meshflow(self) -> None:
        from meshflow import GovernedToolRegistry, ToolPermission, PermissionDeniedError
        assert GovernedToolRegistry is not None
        assert ToolPermission.NETWORK is not None
        assert PermissionDeniedError is not None


# ── DurableWorkflowExecutor ───────────────────────────────────────────────────


class TestDurableWorkflowExecutor:
    def test_init_memory_backend(self) -> None:
        from meshflow import DurableWorkflowExecutor
        ex = DurableWorkflowExecutor(run_id="test-42", backend="memory")
        assert ex.run_id == "test-42"
        assert ex.status() == {}

    def test_init_sqlite_backend(self) -> None:
        from meshflow import DurableWorkflowExecutor
        ex = DurableWorkflowExecutor(run_id="r1", backend="sqlite", db_path=":memory:")
        assert ex.run_id == "r1"

    def test_auto_run_id_generated(self) -> None:
        from meshflow import DurableWorkflowExecutor
        ex = DurableWorkflowExecutor(backend="memory")
        assert len(ex.run_id) > 0

    def test_memory_store_save_and_load(self) -> None:
        from meshflow.core.durable import _MemoryStore
        from meshflow.core.node import NodeOutput
        store = _MemoryStore()
        out = NodeOutput(content="hello", tokens_used=10, confidence=0.9)
        store.save("run1", "node_a", out)
        loaded = store.load("run1", "node_a")
        assert loaded is not None
        assert loaded.content == "hello"
        assert loaded.tokens_used == 10

    def test_memory_store_miss_returns_none(self) -> None:
        from meshflow.core.durable import _MemoryStore
        store = _MemoryStore()
        assert store.load("no-run", "no-node") is None

    def test_sqlite_store_save_and_load(self) -> None:
        from meshflow.core.durable import _SQLiteStore
        from meshflow.core.node import NodeOutput
        store = _SQLiteStore(":memory:")
        out = NodeOutput(content="sqlite-content", tokens_used=5, model="claude-sonnet-4-6")
        store.save("run2", "node_b", out)
        loaded = store.load("run2", "node_b")
        assert loaded is not None
        assert loaded.content == "sqlite-content"
        assert loaded.model == "claude-sonnet-4-6"

    def test_sqlite_store_all_completed(self) -> None:
        from meshflow.core.durable import _SQLiteStore
        from meshflow.core.node import NodeOutput
        store = _SQLiteStore(":memory:")
        store.save("run3", "a", NodeOutput(content="a"))
        store.save("run3", "b", NodeOutput(content="b"))
        completed = store.all_completed("run3")
        assert set(completed.keys()) == {"a", "b"}

    def test_sqlite_store_delete(self) -> None:
        from meshflow.core.durable import _SQLiteStore
        from meshflow.core.node import NodeOutput
        store = _SQLiteStore(":memory:")
        store.save("run4", "n1", NodeOutput(content="x"))
        store.delete("run4")
        assert store.load("run4", "n1") is None

    def test_clear_removes_checkpoints(self) -> None:
        from meshflow import DurableWorkflowExecutor
        from meshflow.core.durable import _MemoryStore
        from meshflow.core.node import NodeOutput
        ex = DurableWorkflowExecutor(run_id="r5", backend="memory")
        ex._store.save("r5", "node_x", NodeOutput(content="cached"))
        assert ex.is_completed("node_x")
        ex.clear()
        assert not ex.is_completed("node_x")

    @pytest.mark.asyncio
    async def test_wrap_node_replays_from_cache(self) -> None:
        from meshflow import DurableWorkflowExecutor
        from meshflow.core.node import MeshNode, NodeKind, NodeInput, NodeOutput
        call_count = 0

        async def real_runner(ni: NodeInput) -> NodeOutput:
            nonlocal call_count
            call_count += 1
            return NodeOutput(content="fresh result", tokens_used=100)

        node = MeshNode(id="n1", kind=NodeKind.PYTHON, _runner=real_runner)
        ex = DurableWorkflowExecutor(run_id="replay-test", backend="memory")
        wrapped = ex._wrap_node(node)

        # First call — runs real runner and saves checkpoint
        out1 = await wrapped.run(NodeInput(task="go"))
        assert out1.content == "fresh result"
        assert call_count == 1

        # Second call — replays from checkpoint, never calls real runner again
        out2 = await wrapped.run(NodeInput(task="go"))
        assert out2.content == "fresh result"
        assert out2.metadata.get("_from_checkpoint") is True
        assert call_count == 1  # unchanged

    def test_status_shows_checkpointed_nodes(self) -> None:
        from meshflow import DurableWorkflowExecutor
        from meshflow.core.node import NodeOutput
        ex = DurableWorkflowExecutor(run_id="stat-test", backend="memory")
        ex._store.save("stat-test", "alpha", NodeOutput(content="done"))
        ex._store.save("stat-test", "beta", NodeOutput(content="done"))
        s = ex.status()
        assert s == {"alpha": "completed", "beta": "completed"}


# ── SwarmTRM Embeddings ───────────────────────────────────────────────────────


class TestSwarmEmbeddings:
    def test_char_ngram_returns_correct_dim(self) -> None:
        from meshflow.swarm.embeddings import CharNgramEmbedder
        emb = CharNgramEmbedder(dim=128)
        v = emb.embed("hello world")
        assert len(v) == 128

    def test_char_ngram_unit_norm(self) -> None:
        import math
        from meshflow.swarm.embeddings import CharNgramEmbedder
        emb = CharNgramEmbedder(dim=64)
        v = emb.embed("HIPAA minimum necessary disclosure")
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-5

    def test_char_ngram_similar_texts_closer_than_dissimilar(self) -> None:
        import math
        from meshflow.swarm.embeddings import CharNgramEmbedder
        emb = CharNgramEmbedder(dim=256)

        def cosine(a: list, b: list) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb + 1e-9)

        hipaa1 = emb.embed("HIPAA minimum necessary disclosure")
        hipaa2 = emb.embed("HIPAA minimum necessary requirement")
        unrelated = emb.embed("machine learning gradient descent optimizer")
        assert cosine(hipaa1, hipaa2) > cosine(hipaa1, unrelated)

    def test_char_ngram_deterministic(self) -> None:
        from meshflow.swarm.embeddings import CharNgramEmbedder
        emb = CharNgramEmbedder(dim=64)
        v1 = emb.embed("same text input")
        v2 = emb.embed("same text input")
        assert v1 == v2

    def test_char_ngram_empty_text(self) -> None:
        from meshflow.swarm.embeddings import CharNgramEmbedder
        emb = CharNgramEmbedder(dim=32)
        v = emb.embed("")
        assert len(v) == 32
        assert all(x == 0.0 for x in v)

    def test_numpy_bow_returns_correct_dim(self) -> None:
        from meshflow.swarm.embeddings import NumpyBowEmbedder
        emb = NumpyBowEmbedder(dim=128)
        v = emb.embed("HIPAA data privacy")
        assert v.shape == (128,)

    def test_numpy_bow_deterministic(self) -> None:
        from meshflow.swarm.embeddings import NumpyBowEmbedder
        emb = NumpyBowEmbedder(dim=64)
        v1 = emb.embed("consistent input text")
        v2 = emb.embed("consistent input text")
        import numpy as np
        assert np.allclose(v1, v2)

    def test_get_embedder_returns_something(self) -> None:
        from meshflow.swarm.embeddings import get_embedder
        emb = get_embedder(dim=64)
        assert emb is not None
        v = emb.embed("test")
        assert len(v) >= 64

    def test_embed_text_convenience(self) -> None:
        from meshflow.swarm.embeddings import embed_text
        v = embed_text("HIPAA compliance", dim=64)
        assert isinstance(v, list)
        assert len(v) == 64

    def test_get_embedder_cached(self) -> None:
        from meshflow.swarm.embeddings import get_embedder
        e1 = get_embedder(dim=128)
        e2 = get_embedder(dim=128)
        assert e1 is e2  # same instance from lru_cache


# ── Event Sourcing Projections ────────────────────────────────────────────────


class TestEventProjections:
    def _make_event(self, kind, run_id="run-1", node_id="", data=None, ts=None):
        from meshflow.core.events import WorkflowEvent, EventKind
        return WorkflowEvent(
            kind=EventKind(kind),
            run_id=run_id,
            node_id=node_id,
            data=data or {},
            timestamp=ts or 1000.0,
        )

    def test_audit_trail_timeline_order(self) -> None:
        from meshflow.core.projections import AuditTrailProjection
        proj = AuditTrailProjection()
        proj.feed(self._make_event("workflow_start", ts=1000.0))
        proj.feed(self._make_event("step_start", node_id="n1", ts=1001.0))
        proj.feed(self._make_event("step_complete", node_id="n1", ts=1002.0))
        proj.feed(self._make_event("workflow_complete", ts=1003.0))
        timeline = proj.timeline("run-1")
        assert len(timeline) == 4
        assert timeline[0].event_kind == "workflow_start"
        assert timeline[-1].event_kind == "workflow_complete"

    def test_audit_trail_elapsed_ms(self) -> None:
        from meshflow.core.projections import AuditTrailProjection
        proj = AuditTrailProjection()
        proj.feed(self._make_event("workflow_start", ts=1000.0))
        proj.feed(self._make_event("step_complete", node_id="n1", ts=1002.5))
        timeline = proj.timeline("run-1")
        assert timeline[1].elapsed_ms == pytest.approx(2500.0, abs=1.0)

    def test_audit_trail_to_dict(self) -> None:
        from meshflow.core.projections import AuditTrailProjection
        proj = AuditTrailProjection()
        proj.feed(self._make_event("workflow_start", ts=1000.0))
        d = proj.to_dict("run-1")
        assert isinstance(d, list)
        assert d[0]["event"] == "workflow_start"

    def test_audit_trail_all_run_ids(self) -> None:
        from meshflow.core.projections import AuditTrailProjection
        proj = AuditTrailProjection()
        proj.feed(self._make_event("workflow_start", run_id="r1"))
        proj.feed(self._make_event("workflow_start", run_id="r2"))
        assert set(proj.all_run_ids()) == {"r1", "r2"}

    def test_node_latency_tracking(self) -> None:
        from meshflow.core.projections import NodeLatencyProjection
        proj = NodeLatencyProjection()
        proj.feed(self._make_event("step_start", node_id="alpha", ts=1000.0))
        proj.feed(self._make_event("step_complete", node_id="alpha", ts=1000.5))  # 500ms
        stats = proj.query("alpha")
        assert len(stats) == 1
        assert stats[0].avg_ms == pytest.approx(500.0, abs=1.0)
        assert stats[0].call_count == 1

    def test_node_latency_aggregates_multiple_calls(self) -> None:
        from meshflow.core.projections import NodeLatencyProjection
        proj = NodeLatencyProjection()
        for i, (ts_start, ts_end) in enumerate([(1000, 1001), (2000, 2002)]):
            proj.feed(self._make_event("step_start", run_id=f"r{i}", node_id="n1", ts=float(ts_start)))
            proj.feed(self._make_event("step_complete", run_id=f"r{i}", node_id="n1", ts=float(ts_end)))
        stats = proj.query("n1")
        assert stats[0].call_count == 2
        assert stats[0].avg_ms == pytest.approx(1500.0, abs=1.0)

    def test_node_latency_slowest(self) -> None:
        from meshflow.core.projections import NodeLatencyProjection
        proj = NodeLatencyProjection()
        for nid, dur in [("fast", 0.1), ("slow", 2.0), ("medium", 1.0)]:
            proj.feed(self._make_event("step_start", node_id=nid, ts=0.0))
            proj.feed(self._make_event("step_complete", node_id=nid, ts=dur))
        slowest = proj.slowest(2)
        assert slowest[0].node_id == "slow"
        assert len(slowest) == 2

    def test_policy_violation_blocked(self) -> None:
        from meshflow.core.projections import PolicyViolationProjection
        proj = PolicyViolationProjection()
        proj.feed(self._make_event("step_blocked", node_id="danger"))
        proj.feed(self._make_event("step_complete", node_id="safe"))
        assert proj.violation_count() == 1
        assert proj.blocked_nodes("run-1") == ["danger"]

    def test_policy_violation_filter_by_kind(self) -> None:
        from meshflow.core.projections import PolicyViolationProjection
        proj = PolicyViolationProjection()
        proj.feed(self._make_event("step_blocked", node_id="n1"))
        proj.feed(self._make_event("step_paused", node_id="n2"))
        proj.feed(self._make_event("hitl_required", node_id="n3"))
        assert len(proj.query(kind="blocked")) == 1
        assert len(proj.query(kind="paused")) == 1
        assert len(proj.query()) == 3

    def test_workflow_summary_completed(self) -> None:
        from meshflow.core.projections import WorkflowSummaryProjection
        proj = WorkflowSummaryProjection()
        proj.feed(self._make_event("workflow_start", ts=1000.0, data={"workflow": "test-wf"}))
        proj.feed(self._make_event("step_complete", ts=1001.0, data={"tokens": 500, "cost_usd": 0.01, "carbon_g": 0.05}))
        proj.feed(self._make_event("step_complete", ts=1002.0, data={"tokens": 300, "cost_usd": 0.005}))
        proj.feed(self._make_event("workflow_complete", ts=1005.0))
        s = proj.query("run-1")
        assert s is not None
        assert s.status == "completed"
        assert s.node_count == 2
        assert s.total_tokens == 800
        assert s.duration_ms == pytest.approx(5000.0, abs=1.0)
        assert s.workflow_name == "test-wf"

    def test_workflow_summary_failed(self) -> None:
        from meshflow.core.projections import WorkflowSummaryProjection
        proj = WorkflowSummaryProjection()
        proj.feed(self._make_event("workflow_start", ts=1000.0))
        proj.feed(self._make_event("workflow_failed", ts=1003.0))
        s = proj.query("run-1")
        assert s.status == "failed"

    def test_event_projector_coordinates_all(self) -> None:
        from meshflow.core.projections import EventProjector
        proj = EventProjector()
        events = [
            self._make_event("workflow_start", ts=0.0, data={"workflow": "coord-test"}),
            self._make_event("step_start", node_id="n1", ts=0.1),
            self._make_event("step_complete", node_id="n1", ts=0.6, data={"tokens": 100}),
            self._make_event("step_blocked", node_id="n2", ts=0.7),
            self._make_event("workflow_complete", ts=1.0),
        ]
        proj.feed_all(events)
        assert len(proj.audit.timeline("run-1")) == 5
        assert proj.violations.violation_count() == 1
        s = proj.summary.query("run-1")
        assert s.status == "completed"

    def test_event_projector_report(self) -> None:
        from meshflow.core.projections import EventProjector
        proj = EventProjector()
        proj.feed(self._make_event("workflow_start", ts=0.0))
        proj.feed(self._make_event("step_complete", node_id="n1", ts=0.5, data={"tokens": 50}))
        proj.feed(self._make_event("workflow_complete", ts=1.0))
        report = proj.report("run-1")
        assert "summary" in report
        assert "audit_trail" in report
        assert "policy_violations" in report
        assert report["summary"]["status"] == "completed"

    def test_projections_exported_from_meshflow(self) -> None:
        from meshflow import (
            AuditTrailProjection, NodeLatencyProjection,
            PolicyViolationProjection, WorkflowSummaryProjection,
            EventProjector,
        )
        assert EventProjector is not None

    def test_feed_all_equivalent_to_sequential_feed(self) -> None:
        from meshflow.core.projections import AuditTrailProjection
        events = [
            self._make_event("workflow_start", ts=0.0),
            self._make_event("step_complete", node_id="n1", ts=0.5),
        ]
        p1 = AuditTrailProjection()
        p2 = AuditTrailProjection()
        p1.feed_all(events)
        for e in events:
            p2.feed(e)
        assert len(p1.timeline("run-1")) == len(p2.timeline("run-1"))
