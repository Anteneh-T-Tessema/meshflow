"""Sprint 67 — Flows Decorator API tests.

Tests for @start, @listen, @router, Flow.kickoff(), Flow.plot().
All tests are deterministic — no API key required.
"""

from __future__ import annotations

import pytest
import meshflow
from meshflow.core.flows import Flow, FlowState, FlowResult, start, listen, router


# ── Helpers ───────────────────────────────────────────────────────────────────

class SimpleState(FlowState):
    topic: str = ""
    plan: str = ""
    research: str = ""
    draft: str = ""
    route: str = ""
    visited: list = []


# ══════════════════════════════════════════════════════════════════════════════
#  FlowState
# ══════════════════════════════════════════════════════════════════════════════

class TestFlowState:

    def test_default_instantiation(self):
        state = SimpleState()
        assert state.topic == ""
        assert state.plan == ""

    def test_field_assignment(self):
        state = SimpleState(topic="HIPAA")
        assert state.topic == "HIPAA"

    def test_update_fields(self):
        state = SimpleState()
        state.plan = "research HIPAA rules"
        assert state.plan == "research HIPAA rules"

    def test_subclassing(self):
        class MyState(FlowState):
            count: int = 0
        s = MyState(count=5)
        assert s.count == 5


# ══════════════════════════════════════════════════════════════════════════════
#  @start decorator
# ══════════════════════════════════════════════════════════════════════════════

class TestStartDecorator:

    def test_marks_method_as_entry(self):
        @start()
        def my_entry(self): ...
        assert getattr(my_entry, "__flow_start__", False) is True

    def test_decorated_method_still_callable(self):
        @start()
        def fn(self): return "hello"
        assert fn(None) == "hello"

    @pytest.mark.asyncio
    async def test_start_method_called_on_kickoff(self):
        class S(FlowState):
            called: bool = False

        class MyFlow(Flow[S]):
            @start()
            async def entry(self):
                self.state.called = True

        flow = MyFlow()
        result = await flow.kickoff()
        assert result.state.called is True


# ══════════════════════════════════════════════════════════════════════════════
#  @listen decorator
# ══════════════════════════════════════════════════════════════════════════════

class TestListenDecorator:

    def test_marks_method_with_trigger(self):
        @listen("plan")
        def on_plan(self, output): ...
        assert hasattr(on_plan, "__flow_listen__")

    @pytest.mark.asyncio
    async def test_listen_receives_start_output(self):
        class S(FlowState):
            received: str = ""

        class MyFlow(Flow[S]):
            @start()
            async def plan(self):
                return "plan_output"

            @listen("plan")
            async def research(self, plan_output):
                self.state.received = plan_output

        flow = MyFlow()
        result = await flow.kickoff()
        assert result.state.received == "plan_output"

    @pytest.mark.asyncio
    async def test_listen_chain(self):
        """@listen can chain: start → A → B."""
        class S(FlowState):
            steps: list = []

        class ChainFlow(Flow[S]):
            @start()
            async def step_a(self):
                self.state.steps.append("a")
                return "a_done"

            @listen("step_a")
            async def step_b(self, a_out):
                self.state.steps.append("b")
                return "b_done"

            @listen("step_b")
            async def step_c(self, b_out):
                self.state.steps.append("c")

        flow = ChainFlow()
        result = await flow.kickoff()
        assert result.state.steps == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_listen_by_method_reference(self):
        class S(FlowState):
            ok: bool = False

        class MyFlow(Flow[S]):
            @start()
            async def produce(self):
                return "data"

            @listen("produce")
            async def consume(self, data):
                self.state.ok = data == "data"

        result = await MyFlow().kickoff()
        assert result.state.ok is True


# ══════════════════════════════════════════════════════════════════════════════
#  @router decorator
# ══════════════════════════════════════════════════════════════════════════════

class TestRouterDecorator:

    def test_marks_method_as_router(self):
        @router("plan")
        def my_router(self): ...
        assert hasattr(my_router, "__flow_router__")

    @pytest.mark.asyncio
    async def test_router_selects_branch(self):
        class S(FlowState):
            branch_taken: str = ""

        class BranchFlow(Flow[S]):
            @start()
            async def plan(self):
                return "go_right"

            @router("plan")
            async def decide(self, plan_out):
                return plan_out  # "go_right"

            @listen(("plan", "go_right"))
            async def right_branch(self, _):
                self.state.branch_taken = "right"

            @listen(("plan", "go_left"))
            async def left_branch(self, _):
                self.state.branch_taken = "left"

        result = await BranchFlow().kickoff()
        assert result.state.branch_taken == "right"


# ══════════════════════════════════════════════════════════════════════════════
#  Flow.kickoff()
# ══════════════════════════════════════════════════════════════════════════════

class TestFlowKickoff:

    @pytest.mark.asyncio
    async def test_returns_flow_result(self):
        class S(FlowState):
            pass

        class MyFlow(Flow[S]):
            @start()
            async def run(self):
                pass

        result = await MyFlow().kickoff()
        assert isinstance(result, FlowResult)

    @pytest.mark.asyncio
    async def test_result_has_state(self):
        class S(FlowState):
            value: int = 0

        class MyFlow(Flow[S]):
            @start()
            async def run(self):
                self.state.value = 42

        result = await MyFlow().kickoff()
        assert result.state.value == 42

    @pytest.mark.asyncio
    async def test_inputs_applied_to_state(self):
        class S(FlowState):
            topic: str = ""

        class MyFlow(Flow[S]):
            @start()
            async def run(self):
                pass

        result = await MyFlow().kickoff(inputs={"topic": "GDPR"})
        assert result.state.topic == "GDPR"

    def test_kickoff_sync(self):
        class S(FlowState):
            done: bool = False

        class MyFlow(Flow[S]):
            @start()
            async def run(self):
                self.state.done = True

        result = MyFlow().kickoff_sync()
        assert result.state.done is True

    @pytest.mark.asyncio
    async def test_multiple_start_methods(self):
        """Multiple @start methods all fire on kickoff."""
        class S(FlowState):
            count: int = 0

        class MultiFlow(Flow[S]):
            @start()
            async def a(self):
                self.state.count += 1

            @start()
            async def b(self):
                self.state.count += 1

        result = await MultiFlow().kickoff()
        assert result.state.count == 2


# ══════════════════════════════════════════════════════════════════════════════
#  Flow.plot()
# ══════════════════════════════════════════════════════════════════════════════

class TestFlowPlot:

    def test_plot_returns_string(self):
        class S(FlowState):
            pass

        class MyFlow(Flow[S]):
            @start()
            async def run(self): pass

        diagram = MyFlow().plot()
        assert isinstance(diagram, str)
        assert len(diagram) > 0

    def test_plot_contains_node_names(self):
        class S(FlowState):
            pass

        class MyFlow(Flow[S]):
            @start()
            async def fetch(self): pass

            @listen("fetch")
            async def process(self, _): pass

        diagram = MyFlow().plot()
        assert "fetch" in diagram
        assert "process" in diagram


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_flow_exported(self):
        assert hasattr(meshflow, "Flow")

    def test_flow_state_exported(self):
        assert hasattr(meshflow, "FlowState")

    def test_flow_result_exported(self):
        assert hasattr(meshflow, "FlowResult")

    def test_decorators_exported(self):
        assert hasattr(meshflow, "flow_start")
        assert hasattr(meshflow, "flow_listen")
        assert hasattr(meshflow, "flow_router")

    def test_in_all(self):
        for sym in ("Flow", "FlowState", "FlowResult", "flow_start", "flow_listen", "flow_router"):
            assert sym in meshflow.__all__, f"{sym} missing from __all__"
