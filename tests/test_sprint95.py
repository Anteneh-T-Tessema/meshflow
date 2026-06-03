"""Sprint 95 — Parallel workflows, tool auto-schema, run_until loops, and memory compression."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshflow import Agent, Workflow, tool, Tool
from meshflow.agents.base import EchoProvider, SandboxProvider, LLMProvider
from meshflow.core.workflow import WorkflowResult
from meshflow.tools.registry import global_registry


class MockProvider(LLMProvider):
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.idx = 0

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        response_format: str | None = None,
    ) -> tuple[str, int, float]:
        resp = self.responses[self.idx]
        if self.idx < len(self.responses) - 1:
            self.idx += 1
        return resp, len(resp.split()), 0.0

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self.complete(model, messages, system, max_tokens)

    async def stream_complete(self, *args, **kwargs):
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Workflow.add_parallel
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowParallel:
    def test_add_parallel_appends_list(self):
        wf = Workflow()
        a1 = Agent(name="a1", provider=EchoProvider())
        a2 = Agent(name="a2", provider=EchoProvider())
        a3 = Agent(name="a3", provider=EchoProvider())

        wf.add(a1)
        wf.add_parallel(a2, a3)

        assert len(wf.agents) == 2
        assert wf.agents[0] is a1
        assert isinstance(wf.agents[1], list)
        assert wf.agents[1][0] is a2
        assert wf.agents[1][1] is a3

    def test_estimate_cost_handles_parallel(self):
        wf = Workflow()
        a1 = Agent(name="a1", model="claude-sonnet-4-6")
        a2 = Agent(name="a2", model="claude-sonnet-4-6")
        wf.add(a1).add_parallel(a2)

        est = wf.estimate_cost("test task")
        assert est.total_usd > 0.0
        assert len(est.lines) == 2
        assert est.lines[0].agent == "a1"
        assert est.lines[1].agent == "a2"

    def test_add_parallel_empty_raises(self):
        wf = Workflow()
        with pytest.raises(ValueError, match="at least one agent"):
            wf.add_parallel()

    def test_run_executes_parallel_steps(self):
        a1 = Agent(name="a1", provider=EchoProvider())
        a2 = Agent(name="a2", provider=EchoProvider("[echo: a2]"))
        a3 = Agent(name="a3", provider=EchoProvider("[echo: a3]"))
        a4 = Agent(name="a4", provider=EchoProvider())

        wf = Workflow()
        wf.add(a1).add_parallel(a2, a3).add(a4)

        result = wf.run("research AI")
        assert result.completed is True
        assert "[Agent a2]" in result.output
        assert "[Agent a3]" in result.output

    @pytest.mark.asyncio
    async def test_stream_yields_parallel_chunks(self):
        a1 = Agent(name="a1", provider=EchoProvider())
        a2 = Agent(name="a2", provider=EchoProvider("hello from a2"))
        a3 = Agent(name="a3", provider=EchoProvider("hello from a3"))

        wf = Workflow()
        wf.add(a1).add_parallel(a2, a3)

        chunks = []
        async for chunk in wf.astream("task"):
            chunks.append(chunk)

        node_starts = [c.node_name for c in chunks if c.kind == "node_start"]
        assert "a1" in node_starts
        assert "a2" in node_starts
        assert "a3" in node_starts


# ═══════════════════════════════════════════════════════════════════════════════
# 2. @tool auto-schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolAutoSchema:
    def test_direct_decorator_no_parentheses(self):
        @tool
        def add_nums(x: int, y: int) -> int:
            """Add two integers.
            
            x: First number
            y: Second number
            """
            return x + y

        assert isinstance(add_nums, Tool)
        assert add_nums.name == "add_nums"
        assert add_nums.description == "Add two integers."
        
        schema = add_nums.input_schema()
        assert schema["type"] == "object"
        assert "x" in schema["properties"]
        assert "y" in schema["properties"]
        assert schema["properties"]["x"]["type"] == "integer"
        assert schema["properties"]["x"]["description"] == "First number"
        assert schema["properties"]["y"]["description"] == "Second number"

    def test_google_and_sphinx_docstring_parsing(self):
        # Sphinx style
        @tool(description="Sphinx doc")
        def sphinx_tool(a: str, b: float):
            """Some function.
            
            :param a: The string param
            :parameter b: The float param
            """
            pass

        schema_sphinx = sphinx_tool.input_schema()
        assert schema_sphinx["properties"]["a"]["description"] == "The string param"
        assert schema_sphinx["properties"]["b"]["description"] == "The float param"

        # Google style
        @tool
        def google_tool(query: str, limit: int = 10):
            """Search query.
            
            Args:
                query (str): The search query text.
                limit (int): Max results.
            """
            pass

        schema_google = google_tool.input_schema()
        assert schema_google["properties"]["query"]["description"] == "The search query text."
        assert schema_google["properties"]["limit"]["description"] == "Max results."

        # Dash style
        @tool
        def dash_tool(val: bool):
            """Toggle value.
            
            val - A boolean flag.
            """
            pass

        schema_dash = dash_tool.input_schema()
        assert schema_dash["properties"]["val"]["description"] == "A boolean flag."


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Workflow.run_until loops
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowRunUntil:
    def test_run_until_float_confidence(self):
        responses = [
            "Attempt 1\nCONFIDENCE: 0.70",
            "Attempt 2\nCONFIDENCE: 0.90",
        ]
        agent = Agent(name="refiner", provider=MockProvider(responses))
        wf = Workflow()
        wf.agents = [agent]

        # Condition: float 0.85
        result = wf.run_until("Draft essay", 0.85, max_steps=3)
        assert "Attempt 2" in result.output

    def test_run_until_string_confidence(self):
        responses = [
            "Low confidence\nCONFIDENCE:0.50",
            "High confidence\nCONFIDENCE:0.95",
        ]
        agent = Agent(name="refiner", provider=MockProvider(responses))
        wf = Workflow()
        wf.agents = [agent]

        # Condition: string expression
        result = wf.run_until("Draft essay", "confidence >= 0.9", max_steps=3)
        assert "High confidence" in result.output

    def test_run_until_callable(self):
        responses = [
            "Draft one",
            "Draft two - fully polished",
        ]
        agent = Agent(name="refiner", provider=MockProvider(responses))
        wf = Workflow()
        wf.agents = [agent]

        # Condition: lambda checking for substring
        result = wf.run_until("Draft essay", lambda res: "polished" in res.output, max_steps=3)
        assert "fully polished" in result.output

    def test_run_until_max_steps(self):
        responses = [
            "Draft\nCONFIDENCE: 0.50"
        ]
        agent = Agent(name="refiner", provider=MockProvider(responses))
        wf = Workflow()
        wf.agents = [agent]

        # Should loop exactly max_steps times (using last response repeatedly)
        result = wf.run_until("Draft essay", 0.90, max_steps=4)
        assert "Draft" in result.output


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Agent memory compression
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentMemoryCompression:
    @pytest.mark.asyncio
    async def test_memory_compression_triggered(self):
        # Create an agent with memory enabled and small threshold
        agent = Agent(name="compress_agent", memory=True, memory_compress_threshold=50)
        built = agent._build()

        # Mock the LLM provider think call to return a summary
        built.think = AsyncMock(return_value=("[Summary of memories]", 10, 0.0))

        # Add initial working memory items
        built._memory.add("First very long working memory item that will exceed the threshold")
        built._memory.add("Second very long working memory item that will exceed the threshold")

        assert built._memory.working_count == 2
        assert built._memory.episodic_count == 0

        # Run step which triggers _maybe_compress_memory
        # The content returned by step will be added to memory, then compression will fire.
        result = await built.step("Task", {})
        
        # Working memory should be compressed: cleared except the most recent item
        # Episodic memory should contain the compressed summary item
        assert built._memory.episodic_count == 1
        assert "[Compressed Memory Summary]" in built._memory._episodic[0].content
        # Working count should be exactly 1 (retains only the most recent)
        assert built._memory.working_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Cost Cap Enforcement
# ═══════════════════════════════════════════════════════════════════════════════

from meshflow.core.workflow import CostCap
from meshflow.core.policy import BudgetExceededError

class MockCostProvider(LLMProvider):
    def __init__(self, cost_per_call: float):
        self.cost_per_call = cost_per_call

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        response_format: str | None = None,
    ) -> tuple[str, int, float]:
        return "response", 10, self.cost_per_call

    async def complete_with_tools(self, *args, **kwargs) -> tuple[str, int, float]:
        return "response", 10, self.cost_per_call

    async def stream_complete(self, *args, **kwargs):
        pass


class TestWorkflowCostCap:
    def test_cost_cap_enforced_sequential_run(self):
        wf = Workflow(cost_cap=CostCap(usd=0.05))
        a1 = Agent(name="a1", provider=MockCostProvider(0.03))
        a2 = Agent(name="a2", provider=MockCostProvider(0.03))
        a3 = Agent(name="a3", provider=MockCostProvider(0.01))
        wf.add(a1).add(a2).add(a3)

        result = wf.run("task")
        assert result.completed is False
        assert "a3" in result.blocked_nodes

    def test_cost_cap_enforced_parallel_run(self):
        wf = Workflow(cost_cap=CostCap(usd=0.05))
        a1 = Agent(name="a1", provider=MockCostProvider(0.03))
        a2 = Agent(name="a2", provider=MockCostProvider(0.03))
        a3 = Agent(name="a3", provider=MockCostProvider(0.01))
        wf.add_parallel(a1).add(a2).add(a3)

        result = wf.run("task")
        assert result.completed is False
        assert "a3" in result.blocked_nodes


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Structured Shared State
# ═══════════════════════════════════════════════════════════════════════════════

from pydantic import BaseModel

class DemoState(BaseModel):
    query: str = ""
    research_notes: str = ""
    rating: int = 0
    refined_output: str = ""


class TestWorkflowStructuredSharedState:
    def test_shared_state_basic_auto_merge(self):
        wf = Workflow(state_schema=DemoState, initial_state={"query": "test query"})
        assert wf.state.query == "test query"
        assert wf.state.research_notes == ""

        # a1 returns a JSON string containing research_notes
        a1 = Agent(name="a1", provider=MockProvider(['{"research_notes": "First research findings"}']))
        wf.add(a1)

        result = wf.run("search")
        assert result.completed is True
        assert result.state is not None
        assert result.state.research_notes == "First research findings"
        assert result.state.query == "test query"

    def test_shared_state_custom_mappers(self):
        wf = Workflow(state_schema=DemoState, initial_state={"query": "advanced test"})

        a1 = Agent(name="a1", provider=MockProvider(["output of agent 1"]))
        
        # custom input map: converts state to input task
        def inp_map(state):
            return f"Process this: {state.query}"
            
        # custom output map: mutates state
        def out_map(state, res):
            # res is a dict like {"result": "...", "tokens": ...}
            output_content = res.get("result", "") if isinstance(res, dict) else str(res)
            state.research_notes = f"custom_{output_content}"

        wf.add(a1, input_map=inp_map, output_map=out_map)

        result = wf.run("run")
        assert result.completed is True
        assert result.state.research_notes == "custom_output of agent 1"

    def test_shared_state_parallel(self):
        wf = Workflow(state_schema=DemoState, initial_state={"query": "parallel query"})

        a1 = Agent(name="a1", provider=MockProvider(['{"research_notes": "notes from a1"}']))
        a2 = Agent(name="a2", provider=MockProvider(['{"rating": 90}']))

        wf.add_parallel(a1, a2)

        result = wf.run("run parallel")
        assert result.completed is True
        assert result.state.research_notes == "notes from a1"
        assert result.state.rating == 90

    def test_shared_state_run_until(self):
        # We loop until rating is >= 9
        responses = [
            '{"refined_output": "draft 1", "rating": 5}',
            '{"refined_output": "final report", "rating": 9}',
        ]
        wf = Workflow(state_schema=DemoState, initial_state={"query": "loop test"})
        a1 = Agent(name="a1", provider=MockProvider(responses))
        wf.add(a1)

        result = wf.run_until("optimize", lambda res: res.state.rating >= 9, max_steps=3)
        assert result.completed is True
        assert result.state.rating == 9
        assert result.state.refined_output == "final report"



