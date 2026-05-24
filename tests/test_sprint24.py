"""Sprint 24 — CrewAI/LangGraph feature parity tests.

All tests are deterministic (MESHFLOW_MOCK=1 / EchoProvider).
No live API calls, no external services.
"""

from __future__ import annotations

import asyncio
import operator
import os
from dataclasses import dataclass
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

# ── imports ──────────────────────────────────────────────────────────────────

from meshflow.agents.task import Task, TaskOutput
from meshflow.agents.crew import Crew, CrewOutput, Process
from meshflow.agents.skills import SKILLS, Skill, list_skills, skill_prompt
from meshflow.core.state import (
    END,
    START,
    Channel,
    Command,
    Interrupt,
    StateGraph,
    add,
    first,
    interrupt,
    last,
    node,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_agent(name: str = "mock_agent", result: str = "done"):
    """Return a minimal mock agent compatible with Task."""
    agent = MagicMock()
    agent.name = name
    agent.tools = []
    agent.run = AsyncMock(return_value={
        "result": result,
        "agent_name": name,
        "tokens": 10,
        "cost_usd": 0.001,
        "stated_confidence": 0.9,
    })
    return agent


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TaskOutput
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskOutput:
    def test_str(self):
        out = TaskOutput(raw="hello", task_description="desc", agent_name="a")
        assert str(out) == "hello"

    def test_repr_preview(self):
        out = TaskOutput(raw="x" * 200, task_description="t", agent_name="ag")
        r = repr(out)
        assert "ag" in r
        assert len(r) < 200

    def test_defaults(self):
        out = TaskOutput(raw="ok")
        assert out.tokens == 0
        assert out.cost_usd == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Task
# ═══════════════════════════════════════════════════════════════════════════════

class TestTask:
    @pytest.mark.asyncio
    async def test_basic_run(self):
        agent = _make_agent(result="research complete")
        task = Task(description="Research AI", expected_output="5 findings", agent=agent)
        out = await task.run()
        assert out.raw == "research complete"
        assert out.agent_name == "mock_agent"
        assert task.output is not None

    @pytest.mark.asyncio
    async def test_placeholder_substitution(self):
        agent = _make_agent()
        calls = []

        async def capture_run(prompt: str, *a, **kw):
            calls.append(prompt)
            return {"result": "ok", "agent_name": "a", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0}

        agent.run = capture_run
        task = Task(description="Research {topic}", expected_output="ok", agent=agent)
        await task.run(inputs={"topic": "LLM governance"})
        assert "LLM governance" in calls[0]
        assert "{topic}" not in calls[0]

    @pytest.mark.asyncio
    async def test_no_agent_raises(self):
        task = Task(description="do something", expected_output="something", agent=None)
        with pytest.raises(ValueError, match="no agent"):
            await task.run()

    @pytest.mark.asyncio
    async def test_context_injected_in_prompt(self):
        prior_agent = _make_agent(name="prior", result="prior output here")
        prior_task = Task(description="Prior task", expected_output="prior out", agent=prior_agent)
        await prior_task.run()

        calls = []

        async def capture(prompt: str, *a, **kw):
            calls.append(prompt)
            return {"result": "ok", "agent_name": "b", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0}

        agent2 = MagicMock()
        agent2.name = "b"
        agent2.tools = []
        agent2.run = capture

        task2 = Task(description="Follow-up", expected_output="ok", agent=agent2, context=[prior_task])
        await task2.run()
        assert "prior output here" in calls[0]

    @pytest.mark.asyncio
    async def test_output_none_before_run(self):
        task = Task(description="t", expected_output="e", agent=_make_agent())
        assert task.output is None
        await task.run()
        assert task.output is not None

    @pytest.mark.asyncio
    async def test_extra_tools_merged_then_restored(self):
        agent = _make_agent()
        original_tools = ["tool_a"]
        agent.tools = original_tools.copy()

        extra = MagicMock()
        extra.name = "extra_tool"
        task = Task(description="t", expected_output="e", agent=agent, tools=[extra])
        await task.run()
        # Tools should be restored after run
        assert agent.tools == original_tools


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Process enum
# ═══════════════════════════════════════════════════════════════════════════════

class TestProcess:
    def test_values(self):
        assert Process.sequential == "sequential"
        assert Process.parallel == "parallel"
        assert Process.hierarchical == "hierarchical"

    def test_from_string(self):
        assert Process("sequential") is Process.sequential
        assert Process("parallel") is Process.parallel
        assert Process("hierarchical") is Process.hierarchical

    def test_str_enum(self):
        assert isinstance(Process.sequential, str)

    def test_crew_accepts_string_process(self):
        a = _make_agent()
        task = Task(description="t", expected_output="e", agent=a)
        crew = Crew(agents=[a], tasks=[task], process="sequential")
        assert crew.process is Process.sequential


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CrewOutput
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewOutput:
    def test_str_is_raw(self):
        outs = [TaskOutput(raw="final", task_description="t", agent_name="a")]
        co = CrewOutput(raw="final", tasks_output=outs, total_tokens=5, total_cost_usd=0.001)
        assert str(co) == "final"

    def test_repr(self):
        outs = [TaskOutput(raw="x")]
        co = CrewOutput(raw="x", tasks_output=outs)
        r = repr(co)
        assert "tasks=1" in r

    def test_totals(self):
        outs = [
            TaskOutput(raw="a", tokens=10, cost_usd=0.01),
            TaskOutput(raw="b", tokens=20, cost_usd=0.02),
        ]
        co = CrewOutput(raw="b", tasks_output=outs, total_tokens=30, total_cost_usd=0.03)
        assert co.total_tokens == 30
        assert pytest.approx(co.total_cost_usd, abs=1e-6) == 0.03


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Crew — sequential
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewSequential:
    @pytest.mark.asyncio
    async def test_single_task(self):
        a = _make_agent(result="done")
        t = Task(description="do it", expected_output="done", agent=a)
        crew = Crew(agents=[a], tasks=[t], process=Process.sequential)
        result = await crew.kickoff()
        assert result.raw == "done"
        assert len(result.tasks_output) == 1

    @pytest.mark.asyncio
    async def test_two_tasks_chain(self):
        a1 = _make_agent(name="a1", result="step1 result")
        a2 = _make_agent(name="a2", result="step2 result")
        t1 = Task(description="step 1", expected_output="ok", agent=a1)
        t2 = Task(description="step 2", expected_output="ok", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2])
        result = await crew.kickoff()
        assert result.raw == "step2 result"
        assert result.tasks_output[0].raw == "step1 result"
        assert result.tasks_output[1].raw == "step2 result"

    @pytest.mark.asyncio
    async def test_context_auto_injected(self):
        """Second task should automatically receive the first task as context."""
        a1 = _make_agent(name="a1", result="research findings")
        a2 = _make_agent(name="a2", result="report done")
        t1 = Task(description="research", expected_output="findings", agent=a1)
        t2 = Task(description="write report", expected_output="report", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2])
        await crew.kickoff()
        # t2.context was auto-set to [t1]
        assert t2.context is not None
        assert t1 in t2.context

    @pytest.mark.asyncio
    async def test_placeholder_inputs(self):
        calls = []

        async def capture(prompt: str, *a, **kw):
            calls.append(prompt)
            return {"result": "ok", "agent_name": "a", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0}

        a = MagicMock()
        a.name = "a"
        a.tools = []
        a.run = capture
        t = Task(description="Research {topic}", expected_output="findings", agent=a)
        crew = Crew(agents=[a], tasks=[t])
        await crew.kickoff(inputs={"topic": "governance"})
        assert "governance" in calls[0]

    @pytest.mark.asyncio
    async def test_empty_tasks_raises(self):
        a = _make_agent()
        with pytest.raises(ValueError):
            Crew(agents=[a], tasks=[])

    @pytest.mark.asyncio
    async def test_empty_agents_raises(self):
        t = Task(description="t", expected_output="e", agent=_make_agent())
        with pytest.raises(ValueError):
            Crew(agents=[], tasks=[t])

    @pytest.mark.asyncio
    async def test_totals_aggregated(self):
        a1 = _make_agent(name="a1", result="r1")
        a2 = _make_agent(name="a2", result="r2")
        a1.run = AsyncMock(return_value={"result": "r1", "agent_name": "a1", "tokens": 10, "cost_usd": 0.01, "stated_confidence": 0.9})
        a2.run = AsyncMock(return_value={"result": "r2", "agent_name": "a2", "tokens": 20, "cost_usd": 0.02, "stated_confidence": 0.9})
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2])
        result = await crew.kickoff()
        assert result.total_tokens == 30
        assert pytest.approx(result.total_cost_usd, abs=1e-6) == 0.03


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Crew — parallel
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewParallel:
    @pytest.mark.asyncio
    async def test_parallel_runs_all_tasks(self):
        a1 = _make_agent(name="a1", result="r1")
        a2 = _make_agent(name="a2", result="r2")
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2], process=Process.parallel)
        result = await crew.kickoff()
        assert len(result.tasks_output) == 2
        assert {o.raw for o in result.tasks_output} == {"r1", "r2"}

    @pytest.mark.asyncio
    async def test_parallel_final_is_last_task(self):
        a1 = _make_agent(result="first")
        a2 = _make_agent(result="last")
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2], process=Process.parallel)
        result = await crew.kickoff()
        # Final raw is last task's output (t2)
        assert result.raw == "last"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Crew — hierarchical
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewHierarchical:
    @pytest.mark.asyncio
    async def test_hierarchical_order(self):
        manager = _make_agent(name="manager", result="plan: do X then Y")
        worker = _make_agent(name="worker", result="executed X and Y")
        t_plan = Task(description="plan", expected_output="a plan", agent=manager)
        t_exec = Task(description="execute", expected_output="done", agent=worker)
        crew = Crew(agents=[manager, worker], tasks=[t_plan, t_exec], process=Process.hierarchical)
        result = await crew.kickoff()
        # Manager runs first; worker receives manager output as context
        assert result.tasks_output[0].raw == "plan: do X then Y"
        assert result.tasks_output[1].raw == "executed X and Y"

    @pytest.mark.asyncio
    async def test_hierarchical_context_from_manager(self):
        manager = _make_agent(name="manager", result="manager says: do this")
        worker_calls = []

        async def worker_run(prompt: str, *a, **kw):
            worker_calls.append(prompt)
            return {"result": "ok", "agent_name": "worker", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 1.0}

        worker = MagicMock()
        worker.name = "worker"
        worker.tools = []
        worker.run = worker_run

        t_plan = Task(description="plan", expected_output="plan", agent=manager)
        t_exec = Task(description="execute", expected_output="done", agent=worker)
        crew = Crew(agents=[manager, worker], tasks=[t_plan, t_exec], process=Process.hierarchical)
        await crew.kickoff()
        assert "manager says: do this" in worker_calls[0]

    @pytest.mark.asyncio
    async def test_hierarchical_single_task_falls_back(self):
        a = _make_agent(result="solo")
        t = Task(description="t", expected_output="e", agent=a)
        crew = Crew(agents=[a], tasks=[t], process=Process.hierarchical)
        result = await crew.kickoff()
        assert result.raw == "solo"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Skills library
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkills:
    def test_skills_dict_nonempty(self):
        assert len(SKILLS) >= 10

    def test_skill_is_frozen_dataclass(self):
        s = SKILLS["python"]
        assert isinstance(s, Skill)
        with pytest.raises((AttributeError, TypeError)):
            s.name = "changed"  # type: ignore[misc]

    def test_skill_has_description(self):
        for name, skill in SKILLS.items():
            assert skill.description, f"{name} has empty description"

    def test_list_skills_sorted(self):
        names = list_skills()
        assert names == sorted(names)
        assert "python" in names
        assert "security" in names
        assert "data_analysis" in names

    def test_skill_prompt_single(self):
        prompt = skill_prompt(["python"])
        assert "Python" in prompt
        assert len(prompt) > 20

    def test_skill_prompt_multiple(self):
        prompt = skill_prompt(["python", "security"])
        assert "Python" in prompt
        assert "security" in prompt.lower() or "cybersecurity" in prompt.lower()

    def test_skill_prompt_unknown_ignored(self):
        prompt = skill_prompt(["nonexistent_skill_xyz"])
        assert prompt == ""

    def test_skill_prompt_mixed_known_unknown(self):
        prompt = skill_prompt(["python", "nonexistent_skill_xyz"])
        assert "Python" in prompt

    def test_skill_prompt_empty_list(self):
        assert skill_prompt([]) == ""

    def test_all_skills_have_tags(self):
        for name, skill in SKILLS.items():
            assert isinstance(skill.tags, tuple), f"{name} tags must be tuple"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Agent skills= integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentSkills:
    def test_skills_augment_system_prompt(self):
        from meshflow.agents.builder import Agent
        agent = Agent(name="a", role="researcher", skills=["python", "sql"])
        built = agent._build()
        assert "Python" in built.config.system_prompt
        assert "SQL" in built.config.system_prompt

    def test_no_skills_unchanged_prompt(self):
        from meshflow.agents.builder import Agent
        agent = Agent(name="a", role="researcher", skills=[])
        built = agent._build()
        # Should still have the role prompt, just no skills
        assert built.config.system_prompt  # non-empty

    def test_custom_prompt_plus_skills(self):
        from meshflow.agents.builder import Agent
        agent = Agent(
            name="a",
            role="researcher",
            system_prompt="Custom base prompt.",
            skills=["writing"],
        )
        built = agent._build()
        assert "Custom base prompt." in built.config.system_prompt
        assert "write" in built.config.system_prompt.lower()

    def test_unknown_skills_ignored(self):
        from meshflow.agents.builder import Agent
        agent = Agent(name="a", role="executor", skills=["nonexistent_xyz"])
        built = agent._build()  # should not raise
        assert built is not None

    def test_mcps_list_stored(self):
        from meshflow.agents.builder import Agent
        agent = Agent(name="a", role="executor", mcps=["https://example.com/mcp"])
        assert len(agent.mcps) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 10. @node decorator
# ═══════════════════════════════════════════════════════════════════════════════

class TestNodeDecorator:
    def test_bare_node_marks_function(self):
        @node
        def search(state):
            return {"found": True}

        assert hasattr(search, "_is_meshflow_node")
        assert search._is_meshflow_node is True

    def test_bare_node_name_is_fn_name(self):
        @node
        def my_fn(state):
            return {}

        assert my_fn._node_name == "my_fn"

    def test_node_with_name(self):
        @node("custom_name")
        def fn(state):
            return {}

        assert fn._node_name == "custom_name"
        assert fn._is_meshflow_node is True

    def test_node_preserves_function(self):
        @node
        def add_one(state):
            return {"x": state["x"] + 1}

        result = add_one({"x": 5})
        assert result == {"x": 6}

    def test_node_with_none_arg(self):
        dec = node(None)
        assert callable(dec)

        @dec
        def fn(s):
            return {}

        assert fn._is_meshflow_node


# ═══════════════════════════════════════════════════════════════════════════════
# 11. interrupt / Command
# ═══════════════════════════════════════════════════════════════════════════════

class TestInterruptCommand:
    def test_interrupt_raises(self):
        with pytest.raises(Interrupt) as exc_info:
            interrupt("Please review this")
        assert exc_info.value.value == "Please review this"

    def test_interrupt_is_exception(self):
        assert issubclass(Interrupt, Exception)

    def test_command_defaults(self):
        cmd = Command()
        assert cmd.resume is None
        assert cmd.goto is None
        assert cmd.update == {}

    def test_command_with_resume(self):
        cmd = Command(resume="approved")
        assert cmd.resume == "approved"

    def test_command_with_goto(self):
        cmd = Command(goto="finalize")
        assert cmd.goto == "finalize"

    def test_command_with_update(self):
        cmd = Command(update={"reviewed": True})
        assert cmd.update["reviewed"] is True

    @pytest.mark.asyncio
    async def test_graph_raises_interrupted_error(self):
        @node
        def review(state):
            interrupt("human review needed")
            return {}

        graph = StateGraph()
        graph.add_node("review", review)
        graph.set_entry_point("review")
        compiled = graph.compile()

        with pytest.raises(InterruptedError) as exc_info:
            await compiled.run({"x": 1})
        assert "review" in str(exc_info.value)
        assert "human review needed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_graph_resume_from_interrupt(self):
        """After interrupt, resume from the interrupted node."""
        call_count = {"n": 0}

        def review(state):
            call_count["n"] += 1
            if call_count["n"] == 1:
                interrupt("check this")
            return {"approved": True}

        graph = StateGraph()
        graph.add_node("review", review)
        graph.set_entry_point("review")
        compiled = graph.compile()

        # First run: interrupted
        with pytest.raises(InterruptedError):
            await compiled.run({"approved": False})

        # Second run: resume
        result = await compiled.run(
            {"approved": False},
            resume=Command(resume="ok"),
        )
        assert result["approved"] is True

    @pytest.mark.asyncio
    async def test_resume_applies_update(self):
        def passthrough(state):
            return {}

        graph = StateGraph()
        graph.add_node("pass", passthrough)
        graph.set_entry_point("pass")
        compiled = graph.compile()

        result = await compiled.run(
            {"x": 1},
            resume=Command(update={"x": 99}),
        )
        # x overridden by Command.update
        assert result["x"] == 99


# ═══════════════════════════════════════════════════════════════════════════════
# 12. StateGraph with reducers (existing, regression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateGraphReducers:
    @pytest.mark.asyncio
    async def test_add_reducer(self):
        from typing import TypedDict

        class S(TypedDict):
            items: Annotated[list, add]

        @node
        def step1(state):
            return {"items": ["a", "b"]}

        @node
        def step2(state):
            return {"items": ["c"]}

        graph = StateGraph(S)
        graph.add_node("s1", step1)
        graph.add_node("s2", step2)
        graph.add_edge("s1", "s2")
        graph.set_entry_point("s1")
        result = await graph.run({"items": []})
        assert set(result["items"]) == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_last_reducer(self):
        from typing import TypedDict

        class S(TypedDict):
            val: Annotated[str, last]

        def s1(state):
            return {"val": "first"}

        def s2(state):
            return {"val": "second"}

        graph = StateGraph(S)
        graph.add_node("s1", s1)
        graph.add_node("s2", s2)
        graph.add_edge("s1", "s2")
        graph.set_entry_point("s1")
        result = await graph.run({"val": ""})
        assert result["val"] == "second"

    @pytest.mark.asyncio
    async def test_operator_add_reducer(self):
        from typing import TypedDict

        class S(TypedDict):
            count: Annotated[int, operator.add]

        def inc(state):
            return {"count": 1}

        graph = StateGraph(S)
        graph.add_node("inc", inc)
        graph.set_entry_point("inc")
        graph.set_finish_point("inc")
        result = await graph.run({"count": 5})
        assert result["count"] == 6

    @pytest.mark.asyncio
    async def test_conditional_edges(self):
        def decide(state):
            return "yes" if state.get("flag") else "no"

        def yes_branch(state):
            return {"result": "YES"}

        def no_branch(state):
            return {"result": "NO"}

        graph = StateGraph()
        graph.add_node("decide", decide)
        graph.add_node("yes_branch", yes_branch)
        graph.add_node("no_branch", no_branch)
        graph.add_conditional_edges("decide", lambda s: "yes" if s.get("flag") else "no", {
            "yes": "yes_branch",
            "no": "no_branch",
        })
        graph.set_entry_point("decide")
        graph.set_finish_point("yes_branch")
        graph.set_finish_point("no_branch")

        r1 = await graph.run({"flag": True})
        assert r1["result"] == "YES"

        r2 = await graph.run({"flag": False})
        assert r2["result"] == "NO"


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Integration: Task → Crew → CrewOutput flow
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrewIntegration:
    @pytest.mark.asyncio
    async def test_three_task_pipeline(self):
        a1 = _make_agent(name="researcher", result="3 key findings")
        a2 = _make_agent(name="analyst",    result="deeper analysis")
        a3 = _make_agent(name="writer",     result="final report")

        t1 = Task(description="Research {topic}", expected_output="findings", agent=a1)
        t2 = Task(description="Analyse findings", expected_output="analysis", agent=a2, context=[t1])
        t3 = Task(description="Write report",     expected_output="report",   agent=a3, context=[t2])

        crew = Crew(agents=[a1, a2, a3], tasks=[t1, t2, t3])
        result = await crew.kickoff(inputs={"topic": "AI governance"})

        assert result.raw == "final report"
        assert len(result.tasks_output) == 3
        assert result.tasks_output[2].agent_name == "writer"

    @pytest.mark.asyncio
    async def test_verbose_does_not_break(self, capsys):
        a1 = _make_agent(name="a", result="r1")
        a2 = _make_agent(name="b", result="r2")
        t1 = Task(description="t1", expected_output="e", agent=a1)
        t2 = Task(description="t2", expected_output="e", agent=a2)
        crew = Crew(agents=[a1, a2], tasks=[t1, t2], verbose=True)
        result = await crew.kickoff()
        assert result.raw == "r2"
        captured = capsys.readouterr()
        assert "[Crew]" in captured.out

    @pytest.mark.asyncio
    async def test_crew_with_real_echo_agent(self):
        """End-to-end with MESHFLOW_MOCK=1 EchoProvider — no live API."""
        from meshflow import Agent

        analyst = Agent(name="echo_analyst", role="researcher", skills=["data_analysis"])
        writer  = Agent(name="echo_writer",  role="executor",   skills=["writing"])

        research = Task(
            description="Research {topic} trends.",
            expected_output="5 bullet findings.",
            agent=analyst,
        )
        report = Task(
            description="Write an executive summary.",
            expected_output="2-paragraph summary.",
            agent=writer,
            context=[research],
        )

        crew = Crew(agents=[analyst, writer], tasks=[research, report], process=Process.sequential)
        result = await crew.kickoff(inputs={"topic": "AI governance"})

        # EchoProvider echoes the prompt; we just verify structure
        assert isinstance(result.raw, str)
        assert len(result.tasks_output) == 2
        assert result.total_tokens >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Public API surface (import smoke tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublicAPI:
    def test_task_importable_from_root(self):
        from meshflow import Task as T
        assert T is Task

    def test_crew_importable_from_root(self):
        from meshflow import Crew as C
        assert C is Crew

    def test_process_importable_from_root(self):
        from meshflow import Process as P
        assert P is Process

    def test_node_importable_from_root(self):
        from meshflow import node as n
        assert n is node

    def test_interrupt_importable_from_root(self):
        from meshflow import interrupt as i
        assert i is interrupt

    def test_command_importable_from_root(self):
        from meshflow import Command as Cmd
        assert Cmd is Command

    def test_skill_prompt_importable(self):
        from meshflow import skill_prompt as sp
        assert callable(sp)

    def test_list_skills_importable(self):
        from meshflow import list_skills as ls
        skills = ls()
        assert "python" in skills

    def test_skills_dict_importable(self):
        from meshflow import SKILLS as sk
        assert "python" in sk

    def test_version_unchanged_or_bumped(self):
        import meshflow
        major, minor, _ = meshflow.__version__.split(".")
        assert int(major) >= 0
        assert int(minor) >= 24  # Sprint 24
