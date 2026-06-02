"""Tests for meshflow/integrations/haystack.py — offline, no Haystack required."""
from __future__ import annotations

import pytest
from meshflow.integrations.haystack import (
    HaystackResult,
    HaystackStepAdapter,
    governed_haystack_pipeline,
)
from meshflow.core.node import NodeInput, NodeKind
from meshflow.core.schemas import RiskTier


# ── Mock pipeline helpers ─────────────────────────────────────────────────────

class _SimplePipeline:
    """Minimal mock: returns a fixed answer."""
    def __init__(self, answer: str = "Paris", documents: list | None = None):
        self._answer = answer
        self._docs = documents or []

    def run(self, inputs: dict) -> dict:
        result: dict = {"answers": [{"answer": self._answer}]}
        if self._docs:
            result["documents"] = self._docs
        return result


class _PiiPipeline:
    """Returns PHI in the answer text."""
    def run(self, inputs: dict) -> dict:
        return {
            "answers": [{"answer": "Patient SSN is 123-45-6789 and DOB 01/15/1980"}],
            "documents": [{"content": "Record for patient 123-45-6789"}],
        }


class _V2Pipeline:
    """Haystack v2 style: component-keyed result, no 'answers' key."""
    def run(self, inputs: dict) -> dict:
        return {
            "llm": {"replies": ["The capital of France is Paris."]},
            "retrieved_documents": [{"content": "France is in Western Europe."}],
        }


# ── HaystackResult ────────────────────────────────────────────────────────────

def test_haystack_result_to_node_output():
    r = HaystackResult(raw={}, answer="Paris", documents=[], pii_detected=False)
    out = r.to_node_output()
    assert out.content == "Paris"
    assert out.metadata["pii_detected"] is False


def test_haystack_result_pii_metadata():
    r = HaystackResult(raw={}, answer="[MASKED]", pii_detected=True, pii_kinds=["ssn"])
    out = r.to_node_output()
    assert out.metadata["pii_detected"] is True
    assert "ssn" in out.metadata["pii_kinds"]


# ── HaystackStepAdapter construction ─────────────────────────────────────────

def test_adapter_is_meshnode():
    from meshflow.core.node import MeshNode
    adapter = HaystackStepAdapter(_SimplePipeline(), node_id="retriever")
    assert isinstance(adapter, MeshNode)


def test_adapter_defaults():
    adapter = HaystackStepAdapter(_SimplePipeline())
    assert adapter.id == "haystack"
    assert adapter.kind == NodeKind.PYTHON
    assert adapter.risk_profile == RiskTier.READ_ONLY
    assert adapter.metadata["integration"] == "haystack"
    assert adapter.metadata["compliance_profile"] == "gdpr"


def test_adapter_custom_node_id():
    adapter = HaystackStepAdapter(_SimplePipeline(), node_id="clinical-retriever")
    assert adapter.id == "clinical-retriever"


def test_adapter_accepts_interceptor():
    from unittest.mock import MagicMock
    adapter = HaystackStepAdapter(_SimplePipeline())
    interceptor = MagicMock()
    adapter.set_tool_call_interceptor(interceptor)
    assert adapter._tool_call_interceptor is interceptor


# ── Basic run ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adapter_run_returns_answer():
    adapter = HaystackStepAdapter(_SimplePipeline("Paris"), pii_scan=False)
    out = await adapter.run(NodeInput(task="What is the capital of France?"))
    assert out.content == "Paris"


@pytest.mark.asyncio
async def test_adapter_run_passes_task_as_query():
    received: list[dict] = []

    class CapturePipeline:
        def run(self, inputs: dict) -> dict:
            received.append(inputs)
            return {"answers": [{"answer": "ok"}]}

    adapter = HaystackStepAdapter(CapturePipeline(), pii_scan=False)
    await adapter.run(NodeInput(task="my specific query"))
    assert received[0]["query"] == "my specific query"


@pytest.mark.asyncio
async def test_adapter_custom_query_key():
    received: list[dict] = []

    class CapturePipeline:
        def run(self, inputs: dict) -> dict:
            received.append(inputs)
            return {"answers": [{"answer": "ok"}]}

    adapter = HaystackStepAdapter(CapturePipeline(), pii_scan=False, query_key="question")
    await adapter.run(NodeInput(task="custom key test"))
    assert "question" in received[0]
    assert received[0]["question"] == "custom key test"


@pytest.mark.asyncio
async def test_adapter_haystack_v2_result():
    """Handles v2-style component-keyed result without 'answers' key."""
    adapter = HaystackStepAdapter(_V2Pipeline(), answer_key="answers", pii_scan=False)
    out = await adapter.run(NodeInput(task="What is France?"))
    # Falls back to JSON dump since 'answers' key is absent
    assert "France" in out.content or "llm" in out.content


@pytest.mark.asyncio
async def test_adapter_structured_output_contains_raw():
    adapter = HaystackStepAdapter(_SimplePipeline("Rome"), pii_scan=False)
    out = await adapter.run(NodeInput(task="Capital of Italy?"))
    assert "answers" in out.structured


# ── PII scanning ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adapter_pii_scan_detects_and_masks():
    adapter = HaystackStepAdapter(_PiiPipeline(), pii_scan=True, mask_pii=True)
    out = await adapter.run(NodeInput(task="patient records"))
    assert out.metadata["pii_detected"] is True
    assert "123-45-6789" not in out.content


@pytest.mark.asyncio
async def test_adapter_pii_scan_block_on_pii():
    adapter = HaystackStepAdapter(
        _PiiPipeline(), pii_scan=True, mask_pii=False, block_on_pii=True
    )
    out = await adapter.run(NodeInput(task="patient records"))
    assert "[blocked:" in out.content
    assert out.confidence == 0.0
    assert out.metadata["blocked_by"] == "pii_scan"


@pytest.mark.asyncio
async def test_adapter_pii_scan_disabled_passes_raw():
    adapter = HaystackStepAdapter(_PiiPipeline(), pii_scan=False)
    out = await adapter.run(NodeInput(task="patient records"))
    # PII passes through when scanning is off
    assert "123-45-6789" in out.content


@pytest.mark.asyncio
async def test_adapter_no_pii_no_flag():
    adapter = HaystackStepAdapter(_SimplePipeline("Paris"), pii_scan=True)
    out = await adapter.run(NodeInput(task="capital?"))
    assert out.metadata.get("pii_detected") is False


# ── Documents ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adapter_documents_in_structured():
    pipeline = _SimplePipeline(
        "Paris",
        documents=[{"content": "France info"}, {"content": "Europe info"}],
    )
    adapter = HaystackStepAdapter(pipeline, pii_scan=False)
    out = await adapter.run(NodeInput(task="France?"))
    assert "documents" in out.structured


@pytest.mark.asyncio
async def test_adapter_documents_pii_masked():
    pipeline = _SimplePipeline(
        "answer",
        documents=[{"content": "SSN 123-45-6789 for patient"}],
    )
    adapter = HaystackStepAdapter(pipeline, pii_scan=True, mask_pii=True)
    out = await adapter.run(NodeInput(task="records"))
    docs = out.structured.get("documents", [])
    assert not any("123-45-6789" in d.get("content", "") for d in docs)


# ── governed_haystack_pipeline factory ───────────────────────────────────────

def test_factory_returns_adapter():
    adapter = governed_haystack_pipeline(_SimplePipeline())
    assert isinstance(adapter, HaystackStepAdapter)


def test_factory_passes_options():
    adapter = governed_haystack_pipeline(
        _SimplePipeline(),
        node_id="my-retriever",
        compliance_profile="hipaa",
        pii_scan=True,
        mask_pii=False,
        block_on_pii=True,
    )
    assert adapter.id == "my-retriever"
    assert adapter._compliance_profile == "hipaa"
    assert adapter._block_on_pii is True
    assert adapter._mask_pii is False


@pytest.mark.asyncio
async def test_factory_runs_end_to_end():
    adapter = governed_haystack_pipeline(_SimplePipeline("Berlin"), pii_scan=False)
    out = await adapter.run(NodeInput(task="Capital of Germany?"))
    assert out.content == "Berlin"
