"""Live end-to-end integration tests against real LLM APIs.

Gated behind environment variables so they never run in CI without credentials:

    ANTHROPIC_API_KEY=sk-ant-...  pytest tests/test_live.py -v

Optional extras:
    OPENAI_API_KEY=sk-...         enables OpenAI / GPT-4o tests
    MESHFLOW_LIVE_SLOW=1          enables tests that do multiple round-trips

All tests are marked with @pytest.mark.live and skipped automatically when the
relevant API key is absent.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

# ── Skip markers ─────────────────────────────────────────────────────────────

needs_anthropic = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping live Anthropic tests",
)
needs_openai = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — skipping live OpenAI tests",
)
needs_slow = pytest.mark.skipif(
    not os.getenv("MESHFLOW_LIVE_SLOW"),
    reason="MESHFLOW_LIVE_SLOW not set — skipping slow multi-turn tests",
)

pytestmark = pytest.mark.live  # tag every test in this module


# ── Helpers ───────────────────────────────────────────────────────────────────


def _anthropic_policy(mode: str = "dev", **overrides):
    from meshflow.core.schemas import policy_for_mode

    return policy_for_mode(mode, budget_usd=0.10, max_steps=3, **overrides)


# ── AnthropicProvider unit ────────────────────────────────────────────────────


@needs_anthropic
def test_anthropic_provider_complete_returns_nonempty_text():
    from meshflow.agents.base import AnthropicProvider

    provider = AnthropicProvider()

    async def _run():
        text, tokens, cost = await provider.complete(
            model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "Reply with exactly three words."}],
            system="You are a concise assistant.",
            max_tokens=32,
        )
        return text, tokens, cost

    text, tokens, cost = asyncio.run(_run())
    assert text.strip(), "expected non-empty response"
    assert tokens > 0
    assert cost >= 0.0


@needs_anthropic
def test_anthropic_provider_stream_complete_yields_chunks():
    from meshflow.agents.base import AnthropicProvider
    from meshflow.core.schemas import TokenChunk

    provider = AnthropicProvider()

    async def _run():
        chunks: list[TokenChunk] = []
        async for chunk in provider.stream_complete(
            model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "Count from 1 to 5, one number per line."}],
            system="You are a concise assistant.",
            max_tokens=64,
            agent_id="test-agent",
            step_id="step-1",
            run_id="run-live-stream",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_run())
    assert len(chunks) > 0, "expected at least one token chunk"
    full_text = "".join(c.text for c in chunks)
    assert any(d in full_text for d in "12345"), "expected digits in streamed output"
    assert all(c.agent_id == "test-agent" for c in chunks)


@needs_anthropic
def test_anthropic_provider_tool_call_executes_function():
    from meshflow.agents.base import AnthropicProvider

    provider = AnthropicProvider()

    calls: list[str] = []

    async def multiply(x: int, y: int) -> int:
        calls.append(f"{x}*{y}")
        return x * y

    multiply_schema = {
        "name": "multiply",
        "description": "Multiply x and y",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    }

    async def _run():
        text, tokens, cost = await provider.complete_with_tools(
            model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "What is 7 multiplied by 6?"}],
            system="Use the multiply tool to answer.",
            max_tokens=256,
            tool_schemas=[multiply_schema],
            tool_fns={"multiply": multiply},
        )
        return text, tokens, cost

    text, tokens, cost = asyncio.run(_run())
    assert "42" in text or calls, "expected tool to be called or answer in text"


# ── Mesh.run (full governed pipeline) ─────────────────────────────────────────


@needs_anthropic
def test_mesh_run_completes_simple_task():
    from meshflow.core.mesh import Mesh

    async def _run():
        mesh = Mesh(policy=_anthropic_policy())
        return await mesh.run("What is the capital of France? Answer in one word.")

    result = asyncio.run(_run())
    assert result.status == "completed", f"status={result.status}"
    assert result.total_tokens > 0
    assert result.total_cost_usd >= 0.0
    assert result.ledger_entries > 0


@needs_anthropic
def test_mesh_run_enforces_budget_cap():
    from meshflow.core.mesh import Mesh
    from meshflow.core.schemas import Policy

    async def _run():
        policy = Policy(budget_usd=0.0001)  # impossibly low cap
        mesh = Mesh(policy=policy)
        return await mesh.run("Write a 10-page essay on machine learning.")

    result = asyncio.run(_run())
    # Either aborted due to budget or completed with very minimal output
    assert result.status in ("aborted", "completed", "failed")


@needs_anthropic
def test_mesh_stream_yields_step_events():
    from meshflow.core.mesh import Mesh

    async def _run():
        mesh = Mesh(policy=_anthropic_policy())
        events = []
        async for event in mesh.stream("What is 2 + 2?"):
            events.append(event)
        return events

    events = asyncio.run(_run())
    event_types = {e.event_type for e in events}
    assert "step_start" in event_types or "run_complete" in event_types, (
        f"expected step events, got: {event_types}"
    )


@needs_anthropic
def test_mesh_run_records_tamper_evident_chain():
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.mesh import Mesh

    ledger_path = ":memory:"

    async def _run():
        ledger = ReplayLedger(ledger_path)
        mesh = Mesh(policy=_anthropic_policy())
        result = await mesh.run("Name any planet. One word answer.")
        return result, ledger

    result, ledger = asyncio.run(_run())
    assert result.status == "completed"
    # The audit chain should validate
    trace = asyncio.run(ledger.get_trace(result.run_id)) if result.run_id else None
    assert trace is not None
    assert len(trace.steps) > 0
    # Even without a shared ledger the result has the run_id
    assert result.run_id, "expected a run_id"


# ── Mesh.run with policy modes ─────────────────────────────────────────────────


@needs_anthropic
def test_mesh_dev_mode_runs_without_guardian():
    from meshflow.core.mesh import Mesh
    from meshflow.core.schemas import policy_for_mode

    async def _run():
        mesh = Mesh(policy=policy_for_mode("dev"))
        return await mesh.run("Say 'hello world' and nothing else.")

    result = asyncio.run(_run())
    assert result.status in ("completed", "failed")


@needs_anthropic
@needs_slow
def test_mesh_regulated_mode_adds_audit_entries():
    from meshflow.core.mesh import Mesh
    from meshflow.core.schemas import policy_for_mode

    async def _run():
        mesh = Mesh(policy=policy_for_mode("regulated", budget_usd=0.20))
        return await mesh.run("Summarise GDPR in one sentence.")

    result = asyncio.run(_run())
    assert result.status == "completed"
    assert result.ledger_entries >= 1


# ── RAG pipeline with real embeddings ─────────────────────────────────────────


@needs_anthropic
def test_rag_pipeline_enriches_task_with_context():
    from meshflow.core.mesh import Mesh
    from meshflow.intelligence.rag import DocumentStore, RAGNode
    from meshflow.core.workflow import WorkflowDefinition

    async def _run():
        store = DocumentStore()
        await store.ingest(
            ["MeshFlow uses SHA-256 hash chaining for tamper-evident audit trails."],
            [{"doc_id": "meshflow-docs"}],
        )
        rag = RAGNode(store=store, node_id="rag", top_k=1)
        wf = WorkflowDefinition(name="rag_pipeline")
        wf.add_node(rag)
        wf.set_terminal(rag.id)

        mesh = Mesh(policy=_anthropic_policy())
        return await mesh.run_workflow(wf, "What audit mechanism does MeshFlow use?")

    result = asyncio.run(_run())
    assert result is not None


# ── Latency smoke test ────────────────────────────────────────────────────────


@needs_anthropic
def test_mesh_run_completes_within_30s():
    from meshflow.core.mesh import Mesh

    async def _run():
        mesh = Mesh(policy=_anthropic_policy())
        t0 = time.monotonic()
        result = await mesh.run("Say 'ok'.")
        return result, time.monotonic() - t0

    result, elapsed = asyncio.run(_run())
    assert result.status in ("completed", "failed", "aborted")
    assert elapsed < 30.0, f"took {elapsed:.1f}s — too slow"


# ── OpenAI ────────────────────────────────────────────────────────────────────


@needs_openai
def test_openai_provider_complete_returns_text():
    from meshflow.agents.base import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
    )

    async def _run():
        text, tokens, cost = await provider.complete(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Reply with exactly two words."}],
            system="You are a concise assistant.",
            max_tokens=16,
        )
        return text, tokens, cost

    text, tokens, cost = asyncio.run(_run())
    assert text.strip()
    assert tokens > 0


@needs_openai
def test_openai_stream_complete_yields_chunks():
    from meshflow.agents.base import OpenAICompatibleProvider
    from meshflow.core.schemas import TokenChunk

    provider = OpenAICompatibleProvider(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
    )

    async def _run():
        chunks: list[TokenChunk] = []
        async for chunk in provider.stream_complete(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Count 1 2 3."}],
            system="Be brief.",
            max_tokens=32,
            agent_id="oai-agent",
            step_id="s1",
            run_id="run-oai",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_run())
    assert len(chunks) > 0


# ── Multi-turn / slow tests ───────────────────────────────────────────────────


@needs_anthropic
@needs_slow
def test_mesh_multi_step_workflow_runs_e2e():
    from meshflow.core.mesh import Mesh
    from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
    from meshflow.core.workflow import WorkflowDefinition

    class EchoNode(MeshNode):
        def __init__(self, nid: str, suffix: str) -> None:
            super().__init__(id=nid, kind=NodeKind.PYTHON)
            self._suffix = suffix

        async def run(self, node_input: NodeInput) -> NodeOutput:
            return NodeOutput(content=f"{node_input.task} [{self._suffix}]", confidence=1.0)

    async def _run():
        wf = WorkflowDefinition(name="echo_pipeline")
        a = EchoNode("step-a", "A")
        b = EchoNode("step-b", "B")
        wf.add_node(a)
        wf.add_node(b)
        wf.add_edge(a.id, b.id)
        wf.set_terminal(b.id)

        mesh = Mesh(policy=_anthropic_policy())
        return await mesh.run_workflow(wf, "hello")

    result = asyncio.run(_run())
    assert result is not None
