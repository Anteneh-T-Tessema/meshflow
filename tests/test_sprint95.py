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


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Declarative Conditional Routing
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowConditionalRouting:
    def test_conditional_routing_agent_selection(self):
        wf = Workflow(state_schema=DemoState, initial_state={"rating": 10})
        
        a_high = Agent(name="high_agent", provider=MockProvider(["High branch executed"]))
        a_low = Agent(name="low_agent", provider=MockProvider(["Low branch executed"]))

        def select_branch(state):
            return "high" if state.rating > 5 else "low"

        wf.add_conditional(select_branch, {"high": a_high, "low": a_low})

        result = wf.run("test task")
        assert result.completed is True
        assert "High branch executed" in result.output
        assert "Low branch executed" not in result.output

    def test_conditional_routing_parallel(self):
        wf = Workflow(state_schema=DemoState, initial_state={"rating": 3})
        
        a_high = Agent(name="high_agent", provider=MockProvider(["High branch executed"]))
        a_parallel_1 = Agent(name="p1", provider=MockProvider(["p1 result"]))
        a_parallel_2 = Agent(name="p2", provider=MockProvider(["p2 result"]))

        def select_branch(state):
            return "high" if state.rating > 5 else "low"

        wf.add_conditional(select_branch, {
            "high": a_high,
            "low": [a_parallel_1, a_parallel_2]
        })

        result = wf.run("test task")
        assert result.completed is True
        assert "[Agent p1]" in result.output
        assert "[Agent p2]" in result.output

    def test_conditional_routing_sub_workflow(self):
        wf_parent = Workflow(state_schema=DemoState, initial_state={"rating": 8})
        
        wf_sub = Workflow(state_schema=DemoState)
        a_sub_1 = Agent(name="sub_agent", provider=MockProvider(['{"refined_output": "sub-workflow output", "rating": 12}']))
        wf_sub.add(a_sub_1)

        def select_branch(state):
            return "sub" if state.rating > 5 else "none"

        wf_parent.add_conditional(select_branch, {"sub": wf_sub})

        result = wf_parent.run("run parent")
        assert result.completed is True
        assert "sub-workflow output" in result.output
        assert result.state.rating == 12
        assert result.state.refined_output == "sub-workflow output"

    def test_conditional_routing_fallback_task(self):
        wf = Workflow()
        
        a_coder = Agent(name="coder", provider=MockProvider(["Code generated"]))
        a_writer = Agent(name="writer", provider=MockProvider(["Essay written"]))

        def route_by_task(task):
            return "code" if "code" in task else "write"

        wf.add_conditional(route_by_task, {"code": a_coder, "write": a_writer})

        result_code = wf.run("Write some code")
        assert "Code generated" in result_code.output

        result_write = wf.run("Write an essay")
        assert "Essay written" in result_write.output

# ═══════════════════════════════════════════════════════════════════════════════
# 8. Human-in-the-Loop Breakpoints
# ═══════════════════════════════════════════════════════════════════════════════

class HitlState(BaseModel):
    query: str = ""
    research_notes: str = ""
    feedback: str = ""
    refined_output: str = ""


class TestWorkflowBreakpoints:
    def test_interrupt_before_pauses_before_step(self):
        """Workflow pauses before agent_b runs, agent_a output is preserved."""
        a1 = Agent(name="agent_a", provider=MockProvider(["Alpha output"]))
        a2 = Agent(name="agent_b", provider=MockProvider(["Beta output"]))
        a3 = Agent(name="agent_c", provider=MockProvider(["Gamma output"]))

        wf = Workflow()
        wf.add(a1)
        wf.add(a2, interrupt_before=True)
        wf.add(a3)

        result = wf.run("test task")
        assert result.completed is False
        assert "agent_b" in result.paused_nodes
        assert "Alpha output" in result.output
        assert "Beta output" not in result.output

    def test_resume_completes_after_interrupt_before(self):
        """After pausing before agent_b, resume() runs agent_b and agent_c."""
        a1 = Agent(name="agent_a", provider=MockProvider(["Alpha output"]))
        a2 = Agent(name="agent_b", provider=MockProvider(["Beta output"]))
        a3 = Agent(name="agent_c", provider=MockProvider(["Gamma output"]))

        wf = Workflow()
        wf.add(a1)
        wf.add(a2, interrupt_before=True)
        wf.add(a3)

        result = wf.run("test task")
        assert result.completed is False

        result2 = wf.resume("test task")
        assert result2.completed is True
        assert "Gamma output" in result2.output

    def test_interrupt_after_pauses_after_step(self):
        """Workflow pauses after agent_a runs, before agent_b."""
        a1 = Agent(name="agent_a", provider=MockProvider(["Alpha output"]))
        a2 = Agent(name="agent_b", provider=MockProvider(["Beta output"]))

        wf = Workflow()
        wf.add(a1, interrupt_after=True)
        wf.add(a2)

        result = wf.run("test task")
        assert result.completed is False
        assert "agent_a" in result.paused_nodes
        assert "Alpha output" in result.output
        assert "Beta output" not in result.output

    def test_resume_completes_after_interrupt_after(self):
        """After pausing after agent_a, resume() runs agent_b."""
        a1 = Agent(name="agent_a", provider=MockProvider(["Alpha output"]))
        a2 = Agent(name="agent_b", provider=MockProvider(["Beta output"]))

        wf = Workflow()
        wf.add(a1, interrupt_after=True)
        wf.add(a2)

        result = wf.run("test task")
        assert result.completed is False

        result2 = wf.resume("test task")
        assert result2.completed is True
        assert "Beta output" in result2.output

    def test_human_input_propagated_to_state(self):
        """Resume with human_input injects feedback into state 'feedback' field."""
        a1 = Agent(name="agent_a", provider=MockProvider(['{"research_notes": "initial notes"}']))
        a2 = Agent(name="agent_b", provider=MockProvider(['{"refined_output": "final result"}']))

        wf = Workflow(state_schema=HitlState, initial_state={"query": "test"})
        wf.add(a1, interrupt_after=True)
        wf.add(a2)

        result = wf.run("research")
        assert result.completed is False
        assert wf.state.research_notes == "initial notes"

        result2 = wf.resume("research", human_input="focus on cost analysis")
        assert result2.completed is True
        assert wf.state.feedback == "focus on cost analysis"
        assert wf.state.refined_output == "final result"

    def test_resume_without_pause_raises(self):
        """Calling resume() when the workflow is not paused raises RuntimeError."""
        wf = Workflow()
        a1 = Agent(name="agent_a", provider=MockProvider(["output"]))
        wf.add(a1)

        with pytest.raises(RuntimeError, match="not paused"):
            wf.resume("task")

    def test_interrupt_before_parallel(self):
        """Workflow pauses before a parallel step."""
        a1 = Agent(name="agent_a", provider=MockProvider(["Alpha output"]))
        a2 = Agent(name="p1", provider=MockProvider(["P1 output"]))
        a3 = Agent(name="p2", provider=MockProvider(["P2 output"]))
        a4 = Agent(name="agent_b", provider=MockProvider(["Final output"]))

        wf = Workflow()
        wf.add(a1)
        wf.add_parallel(a2, a3, interrupt_before=True)
        wf.add(a4)

        result = wf.run("task")
        assert result.completed is False
        assert "Alpha output" in result.output
        assert "P1 output" not in result.output

        result2 = wf.resume("task")
        assert result2.completed is True
        assert "Final output" in result2.output

    def test_multiple_breakpoints(self):
        """Workflow pauses at each breakpoint sequentially."""
        a1 = Agent(name="a1", provider=MockProvider(["Output 1"]))
        a2 = Agent(name="a2", provider=MockProvider(["Output 2"]))
        a3 = Agent(name="a3", provider=MockProvider(["Output 3"]))

        wf = Workflow()
        wf.add(a1, interrupt_after=True)
        wf.add(a2, interrupt_after=True)
        wf.add(a3)

        # First run pauses after a1
        result1 = wf.run("task")
        assert result1.completed is False
        assert "a1" in result1.paused_nodes

        # First resume runs a2, then pauses after a2
        result2 = wf.resume("task")
        assert result2.completed is False
        assert "a2" in result2.paused_nodes

        # Second resume runs a3 and completes
        result3 = wf.resume("task")
        assert result3.completed is True
        assert "Output 3" in result3.output


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Vector-based Semantic Memory Recall
# ═══════════════════════════════════════════════════════════════════════════════

from meshflow.intelligence.memory import AgentMemory, _VectorIndex


def _simple_embed(text: str) -> list[float]:
    """Trivial bag-of-chars embedding for testing — deterministic and fast."""
    vec = [0.0] * 26
    for ch in text.lower():
        if 'a' <= ch <= 'z':
            vec[ord(ch) - ord('a')] += 1.0
    # Normalise
    mag = sum(v * v for v in vec) ** 0.5
    if mag > 0:
        vec = [v / mag for v in vec]
    return vec


class TestVectorMemory:
    def test_vector_index_cosine_search(self):
        """_VectorIndex returns closest vectors by cosine similarity."""
        idx = _VectorIndex()
        idx.add("hello world", [1.0, 0.0, 0.0])
        idx.add("foo bar", [0.0, 1.0, 0.0])
        idx.add("similar hello", [0.9, 0.1, 0.0])

        results = idx.search([1.0, 0.0, 0.0], top_k=2)
        assert len(results) == 2
        assert results[0][0] == "hello world"  # exact match
        assert results[1][0] == "similar hello"  # close match

    def test_agent_memory_with_embed_fn(self):
        """AgentMemory with embed_fn creates a vector index."""
        mem = AgentMemory(agent_id="test", embed_fn=_simple_embed)
        assert mem._vector_index is not None
        mem.add("The cat sat on the mat")
        mem.add("Python programming is great for data science")
        mem.add("The kitten played on the rug")

        results = mem.recall_semantic("cat mat sitting", top_k=2)
        assert len(results) == 2
        # The cat/kitten entries should rank higher than programming
        assert any("cat" in r or "kitten" in r for r in results)

    def test_agent_memory_without_embed_fn(self):
        """AgentMemory without embed_fn has no vector index."""
        mem = AgentMemory(agent_id="test")
        assert mem._vector_index is None

    def test_recall_semantic_raises_without_embed_fn(self):
        """recall_semantic raises RuntimeError without embed_fn."""
        mem = AgentMemory(agent_id="test")
        with pytest.raises(RuntimeError, match="embed_fn"):
            mem.recall_semantic("query")

    def test_hybrid_recall_combines_bm25_and_vector(self):
        """recall() with embed_fn uses hybrid BM25 + vector scoring."""
        mem = AgentMemory(agent_id="test", embed_fn=_simple_embed)
        mem.add("HIPAA compliance requirements for healthcare data")
        mem.add("Python best practices and coding standards")
        mem.add("Healthcare patient privacy regulations and HIPAA")

        results = mem.recall("HIPAA healthcare privacy", top_k=2)
        assert len(results) == 2
        # Both HIPAA-related entries should be in top 2
        assert all("HIPAA" in r or "Healthcare" in r or "healthcare" in r for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Token/Call Rate Limiting
# ═══════════════════════════════════════════════════════════════════════════════

from meshflow.core.rate_limiter import RateLimiter


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_rpm_tracking(self):
        """RateLimiter tracks requests in the sliding window."""
        limiter = RateLimiter(rpm=100)
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()
        assert limiter.requests_in_window == 3

    @pytest.mark.asyncio
    async def test_tpm_tracking(self):
        """RateLimiter tracks tokens in the sliding window."""
        limiter = RateLimiter(tpm=10000)
        await limiter.acquire(tokens=500)
        await limiter.acquire(tokens=300)
        assert limiter.tokens_in_window == 800

    @pytest.mark.asyncio
    async def test_rpm_blocks_when_exceeded(self):
        """RateLimiter with very short window blocks when RPM exceeded."""
        # Use a 0.1s window to test blocking without long waits
        limiter = RateLimiter(rpm=2, window_s=0.1)
        await limiter.acquire()
        await limiter.acquire()
        # Third acquire should block until window expires (~0.1s)
        import time
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05  # should have waited for window to expire

    @pytest.mark.asyncio
    async def test_agent_with_rate_limiter(self):
        """Agent with rpm= creates a rate limiter."""
        agent = Agent(name="rate_limited", provider=EchoProvider(), rpm=60, tpm=100000)
        built = agent._build()
        assert built._rate_limiter is not None
        assert built._rate_limiter.rpm == 60
        assert built._rate_limiter.tpm == 100000


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Custom Merge Reducers for Parallel Steps
# ═══════════════════════════════════════════════════════════════════════════════

class TestCustomMergeReducers:
    def test_custom_reducer_receives_all_results(self):
        """Custom reducer receives dict of {name: result} from parallel agents."""
        a1 = Agent(name="scorer_a", provider=MockProvider(["85"]))
        a2 = Agent(name="scorer_b", provider=MockProvider(["92"]))

        captured = {}

        def average_scores(results: dict) -> str:
            captured.update(results)
            scores = []
            for name, res in results.items():
                val = res.get("result", "0") if isinstance(res, dict) else str(res)
                try:
                    scores.append(float(val))
                except ValueError:
                    scores.append(0.0)
            avg = sum(scores) / len(scores) if scores else 0
            return f"Average score: {avg:.1f}"

        wf = Workflow()
        wf.add_parallel(a1, a2, reducer=average_scores)

        result = wf.run("Score this")
        assert "Average score:" in result.output
        assert "scorer_a" in captured
        assert "scorer_b" in captured

    def test_default_behavior_without_reducer(self):
        """Without a reducer, default [Agent name] format is used."""
        a1 = Agent(name="agent_x", provider=MockProvider(["X output"]))
        a2 = Agent(name="agent_y", provider=MockProvider(["Y output"]))

        wf = Workflow()
        wf.add_parallel(a1, a2)

        result = wf.run("task")
        assert "[Agent agent_x]" in result.output
        assert "[Agent agent_y]" in result.output
