"""MeshFlow × Haystack integration.

Wraps any Haystack pipeline as a governed MeshNode so it runs through
the full StepRuntime kernel: compliance profiles, PII/PHI detection,
cost governance, Zero Trust, and tamper-evident audit.

Quickstart::

    from meshflow.integrations.haystack import governed_haystack_pipeline
    from meshflow import Workflow, Agent

    adapter = governed_haystack_pipeline(
        haystack_pipeline=your_pipeline,
        compliance_profile="gdpr",
        pii_scan=True,
    )

    wf = Workflow()
    wf.add(adapter, Agent("summariser"))
    result = wf.run("Retrieve clinical notes for patient 42 and summarise risks")

Offline / test usage (no Haystack installed)::

    from meshflow.integrations.haystack import HaystackStepAdapter
    from meshflow.core.node import NodeKind
    from meshflow.core.schemas import RiskTier

    class MockPipeline:
        def run(self, inputs):
            return {"answers": [{"answer": "mock result"}]}

    adapter = HaystackStepAdapter(MockPipeline(), node_id="retriever")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from meshflow.core.node import MeshNode, NodeInput, NodeOutput, NodeKind
from meshflow.core.schemas import RiskTier


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class HaystackResult:
    """Structured result from a governed Haystack pipeline run."""

    raw: dict[str, Any]
    answer: str
    documents: list[dict[str, Any]] = field(default_factory=list)
    pii_detected: bool = False
    pii_kinds: list[str] = field(default_factory=list)
    tokens_used: int = 0

    def to_node_output(self) -> NodeOutput:
        meta: dict[str, Any] = {"pii_detected": self.pii_detected}
        if self.pii_kinds:
            meta["pii_kinds"] = self.pii_kinds
        return NodeOutput(
            content=self.answer,
            structured=self.raw,
            tokens_used=self.tokens_used,
            metadata=meta,
        )


# ── Adapter ───────────────────────────────────────────────────────────────────

class HaystackStepAdapter(MeshNode):
    """A MeshNode that runs a Haystack pipeline as a governed step.

    Parameters
    ----------
    pipeline:
        Any object with a ``run(inputs: dict) -> dict`` method.
        Compatible with Haystack Pipeline and BasePipeline.
    node_id:
        Identifier used in the audit ledger and step records.
    compliance_profile:
        One of "gdpr", "hipaa", "sox", "pci", "iso27001", "ccpa", "dora",
        "eu_ai_act", "nerc", or "" (none).  Applied via MeshFlow's compliance
        layer when this node is run through StepRuntime.
    pii_scan:
        If True, scan retrieved documents for PHI/PII/credentials before
        passing them downstream.  Detected items are logged in node output
        metadata; the step is blocked if ``block_on_pii=True``.
    mask_pii:
        If True (default), mask detected PII in the content rather than
        blocking.  Only meaningful when ``pii_scan=True``.
    block_on_pii:
        If True, block the step entirely when PII is detected in retrieved
        content.  Defaults to False (mask and continue).
    query_key:
        Key used when passing the task string into ``pipeline.run()``.
        Defaults to ``"query"`` (Haystack v1 convention).
    answer_key:
        Top-level key in the pipeline result dict that contains the primary
        answer/summary text.  Falls back to a full JSON dump if not found.
    """

    def __init__(
        self,
        pipeline: Any,
        node_id: str = "haystack",
        compliance_profile: str = "gdpr",
        pii_scan: bool = True,
        mask_pii: bool = True,
        block_on_pii: bool = False,
        query_key: str = "query",
        answer_key: str = "answers",
    ) -> None:
        super().__init__(
            id=node_id,
            kind=NodeKind.PYTHON,
            risk_profile=RiskTier.READ_ONLY,
            metadata={
                "integration": "haystack",
                "compliance_profile": compliance_profile,
                "pii_scan": pii_scan,
            },
        )
        self._pipeline = pipeline
        self._compliance_profile = compliance_profile
        self._pii_scan = pii_scan
        self._mask_pii = mask_pii
        self._block_on_pii = block_on_pii
        self._query_key = query_key
        self._answer_key = answer_key
        self._tool_call_interceptor: Any = None  # injected by StepRuntime

    def set_tool_call_interceptor(self, interceptor: Any) -> None:
        self._tool_call_interceptor = interceptor

    async def run(self, node_input: NodeInput) -> NodeOutput:
        result = self._run_pipeline(node_input.task)
        haystack_result = self._parse_result(result)

        if self._pii_scan:
            haystack_result = self._apply_pii_scan(haystack_result)
            if self._block_on_pii and haystack_result.pii_detected:
                return NodeOutput(
                    content=f"[blocked: PII detected in retrieved content — kinds: {haystack_result.pii_kinds}]",
                    confidence=0.0,
                    metadata={"blocked_by": "pii_scan", "pii_kinds": haystack_result.pii_kinds},
                )

        return haystack_result.to_node_output()

    def _run_pipeline(self, task: str) -> dict[str, Any]:
        """Call the pipeline synchronously; wrap coroutines if needed."""
        import inspect
        result = self._pipeline.run({self._query_key: task})
        if inspect.isawaitable(result):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(result)
        return result if isinstance(result, dict) else {"_raw": str(result)}

    def _parse_result(self, raw: dict[str, Any]) -> HaystackResult:
        """Extract answer text and documents from the pipeline result."""
        # answers key — Haystack v1 ExtractiveQA and GenerativeQA
        answers = raw.get(self._answer_key, [])
        if answers and isinstance(answers, list):
            first = answers[0]
            answer_text = (
                first.get("answer") or first.get("content") or str(first)
                if isinstance(first, dict) else str(first)
            )
        else:
            # Haystack v2 returns component-keyed dicts; fall back to JSON
            answer_text = json.dumps(raw, ensure_ascii=False)[:2000]

        documents: list[dict[str, Any]] = []
        for key in ("documents", "retrieved_documents", "context"):
            docs = raw.get(key)
            if docs and isinstance(docs, list):
                for d in docs:
                    documents.append(
                        d if isinstance(d, dict) else {"content": str(d)}
                    )
                break

        return HaystackResult(raw=raw, answer=answer_text, documents=documents)

    def _apply_pii_scan(self, result: HaystackResult) -> HaystackResult:
        """Scan answer and document content for PHI/PII; mask or flag."""
        try:
            from meshflow.security.sensitive_data import SensitiveDataDetector
        except ImportError:
            return result

        detector = SensitiveDataDetector()
        all_text = result.answer + " ".join(
            d.get("content", "") for d in result.documents
        )
        matches = detector.detect(all_text)
        if not matches:
            return result

        kinds = list({m.kind for m in matches})
        if self._mask_pii:
            result.answer = detector.mask(result.answer)
            result.documents = [
                {**d, "content": detector.mask(d.get("content", ""))}
                for d in result.documents
            ]
            # Keep raw in sync so structured output reflects masking
            for doc_key in ("documents", "retrieved_documents", "context"):
                if doc_key in result.raw and isinstance(result.raw[doc_key], list):
                    result.raw = {
                        **result.raw,
                        doc_key: result.documents,
                    }
                    break

        result.pii_detected = True
        result.pii_kinds = kinds
        return result


# ── Factory ───────────────────────────────────────────────────────────────────

def governed_haystack_pipeline(
    haystack_pipeline: Any,
    node_id: str = "haystack",
    compliance_profile: str = "gdpr",
    pii_scan: bool = True,
    mask_pii: bool = True,
    block_on_pii: bool = False,
    query_key: str = "query",
    answer_key: str = "answers",
) -> HaystackStepAdapter:
    """Wrap a Haystack pipeline as a governed MeshNode.

    The returned adapter is a MeshNode you can add directly to a
    ``Workflow`` or ``WorkflowDefinition``.

    Example::

        from meshflow.integrations.haystack import governed_haystack_pipeline
        from meshflow import Workflow

        adapter = governed_haystack_pipeline(
            haystack_pipeline=my_pipeline,
            compliance_profile="gdpr",
            pii_scan=True,
        )
        result = Workflow().add(adapter).run("What are the drug interactions for aspirin?")
    """
    return HaystackStepAdapter(
        pipeline=haystack_pipeline,
        node_id=node_id,
        compliance_profile=compliance_profile,
        pii_scan=pii_scan,
        mask_pii=mask_pii,
        block_on_pii=block_on_pii,
        query_key=query_key,
        answer_key=answer_key,
    )


__all__ = [
    "HaystackStepAdapter",
    "HaystackResult",
    "governed_haystack_pipeline",
]
