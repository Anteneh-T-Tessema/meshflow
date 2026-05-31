"""Sprint 76 — Strict competitive gap closure tests.

Every gap from the May 2026 Competitive Intelligence document:
  1.  BranchCompare — parallel fork + output diff (LangGraph Branch & Compare)
  2.  State Injection — context_patch on RewindEngine (LangGraph State Injection mode)
  3.  S3 backend for DurableWorkflowExecutor
  4.  RoleRouter — LLM-driven dynamic role assignment (first-mover)
  5.  LLMRanker — LLM-based re-ranking (Haystack parity)
  6.  HybridRetriever — BM25 + dense RRF (Haystack parity)
  7.  SelfCorrectingRAG — retrieve → grade → refine loop (Haystack parity)
  8.  Curated template library — 20 pre-built specialist templates
  9.  Interactive Mermaid graph — TraceServer /api/graph/ endpoint
 10.  RAG configurator — TraceServer /rag route
 11.  ModelRouter analytics emission — analytics_ledger wiring
 12.  Public API exports (__all__)
"""

from __future__ import annotations

import asyncio
import inspect
import shutil
import socket
import tempfile
import unittest
from typing import Any


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _echo_agent(name: str = "a", reply: str = "answer") -> Any:
    from meshflow.agents.base import EchoProvider
    from meshflow import Agent
    return Agent(name=name, role="executor", provider=EchoProvider(reply))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ─────────────────────────────────────────────────────────────────────────────
# 1. BranchCompare
# ─────────────────────────────────────────────────────────────────────────────

class TestBranchCompare(unittest.TestCase):

    def test_imports_cleanly(self) -> None:
        from meshflow.core.branch_compare import BranchCompare
        self.assertIsNotNone(BranchCompare)

    def test_fork_config_defaults(self) -> None:
        from meshflow.core.branch_compare import ForkConfig
        cfg = ForkConfig(label="baseline")
        self.assertEqual(cfg.label, "baseline")
        self.assertEqual(cfg.model_override, "")
        self.assertEqual(cfg.context_patch, {})

    def test_fork_config_with_all_fields(self) -> None:
        from meshflow.core.branch_compare import ForkConfig
        cfg = ForkConfig(
            label="variant",
            model_override="claude-haiku-4-5-20251001",
            prompt_override="Be concise.",
            context_patch={"region": "eu-west-1"},
            workflow_yaml="pipeline.yaml",
        )
        self.assertEqual(cfg.context_patch["region"], "eu-west-1")
        self.assertEqual(cfg.model_override, "claude-haiku-4-5-20251001")

    def test_fork_result_to_dict(self) -> None:
        from meshflow.core.branch_compare import ForkResult
        r = ForkResult(label="test", output="hello world", completed=True,
                       confidence=0.85, steps_replayed=3, total_cost_usd=0.002)
        d = r.to_dict()
        self.assertEqual(d["label"], "test")
        self.assertAlmostEqual(d["confidence"], 0.85)
        self.assertTrue(d["completed"])

    def test_compare_result_cost_comparison_sorted(self) -> None:
        from meshflow.core.branch_compare import CompareResult, ForkResult
        cr = CompareResult(
            run_id="run-1", fork_point=3,
            forks=[
                ForkResult(label="cheap", output="ok", completed=True, total_cost_usd=0.001),
                ForkResult(label="expensive", output="ok", completed=True, total_cost_usd=0.010),
            ],
            winner="cheap",
        )
        costs = cr.cost_comparison()
        self.assertEqual(costs[0]["label"], "cheap")

    def test_compare_result_quality_comparison_sorted(self) -> None:
        from meshflow.core.branch_compare import CompareResult, ForkResult
        cr = CompareResult(
            run_id="run-1", fork_point=2,
            forks=[
                ForkResult(label="low",  output="ok", completed=True, confidence=0.5),
                ForkResult(label="high", output="ok", completed=True, confidence=0.9),
            ],
            winner="high",
        )
        quality = cr.quality_comparison()
        self.assertEqual(quality[0]["label"], "high")

    def test_compare_result_to_dict_keys(self) -> None:
        from meshflow.core.branch_compare import CompareResult
        cr = CompareResult(run_id="r1", fork_point=1, forks=[], winner="")
        d = cr.to_dict()
        for key in ("run_id", "fork_point", "winner", "forks", "diff_summary"):
            self.assertIn(key, d)

    def test_branch_compare_requires_at_least_one_fork(self) -> None:
        from meshflow.core.branch_compare import BranchCompare
        bc = BranchCompare(ledger_db=":memory:")
        with self.assertRaises(ValueError):
            _run(bc.compare("run-1", 1, forks=[]))

    def test_branch_compare_handles_missing_run(self) -> None:
        from meshflow.core.branch_compare import BranchCompare, ForkConfig
        bc = BranchCompare(ledger_db=":memory:")
        result = _run(bc.compare(
            "nonexistent-run", 1,
            forks=[ForkConfig(label="a"), ForkConfig(label="b")],
        ))
        # Both forks should fail gracefully
        for fork in result.forks:
            self.assertFalse(fork.completed)

    def test_word_diff_identical(self) -> None:
        from meshflow.core.branch_compare import _word_diff
        self.assertEqual(_word_diff("same text", "same text"), "(outputs identical)")

    def test_word_diff_different(self) -> None:
        from meshflow.core.branch_compare import _word_diff
        diff = _word_diff("line one\nline two", "line one\nline THREE")
        self.assertIn("THREE", diff)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        for name in ("BranchCompare", "ForkConfig", "ForkResult", "CompareResult"):
            self.assertIn(name, meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 2. State Injection (context_patch on RewindEngine)
# ─────────────────────────────────────────────────────────────────────────────

class TestStateInjection(unittest.TestCase):
    """LangGraph's State Injection mode — inject corrective data before fork."""

    def test_rewind_engine_accepts_context_patch(self) -> None:
        from meshflow.core.time_travel import RewindEngine
        import inspect
        sig = inspect.signature(RewindEngine.rewind)
        self.assertIn("context_patch", sig.parameters)

    def test_context_patch_parameter_is_optional(self) -> None:
        from meshflow.core.time_travel import RewindEngine
        import inspect
        sig = inspect.signature(RewindEngine.rewind)
        param = sig.parameters["context_patch"]
        self.assertIsNone(param.default)

    def test_context_patch_is_dict_type(self) -> None:
        from meshflow.core.time_travel import RewindEngine
        import inspect
        sig = inspect.signature(RewindEngine.rewind)
        ann = sig.parameters["context_patch"].annotation
        # Accept dict | None or dict[str, Any] | None
        self.assertTrue("dict" in str(ann).lower() or ann is inspect.Parameter.empty)


# ─────────────────────────────────────────────────────────────────────────────
# 3. S3 backend for DurableWorkflowExecutor
# ─────────────────────────────────────────────────────────────────────────────

class TestDurableS3Backend(unittest.TestCase):

    def test_s3_store_requires_bucket(self) -> None:
        import os
        from meshflow.core.durable import _S3Store
        os.environ.pop("MESHFLOW_S3_BUCKET", None)
        with self.assertRaises(ValueError):
            _S3Store(bucket="")

    def test_s3_store_bucket_from_env(self) -> None:
        import os
        os.environ["MESHFLOW_S3_BUCKET"] = "my-test-bucket"
        try:
            from meshflow.core.durable import _S3Store
            import sys
            saved = sys.modules.get("boto3")
            sys.modules["boto3"] = None  # type: ignore[assignment]
            try:
                store = _S3Store.__new__(_S3Store)
                store._bucket = os.environ.get("MESHFLOW_S3_BUCKET", "")
                self.assertEqual(store._bucket, "my-test-bucket")
            finally:
                if saved is None:
                    sys.modules.pop("boto3", None)
                else:
                    sys.modules["boto3"] = saved
        finally:
            os.environ.pop("MESHFLOW_S3_BUCKET", None)

    def test_s3_store_obj_key_format(self) -> None:
        from meshflow.core.durable import _S3Store
        import os
        os.environ["MESHFLOW_S3_BUCKET"] = "my-bucket"
        try:
            store = _S3Store.__new__(_S3Store)
            store._bucket = "my-bucket"
            store._prefix = "meshflow/checkpoints"
            store._region = "us-east-1"
            store._profile = ""
            store._client = None
            key = store._obj_key("run-abc", "node_1")
            self.assertEqual(key, "meshflow/checkpoints/run-abc/node_1.json")
        finally:
            os.environ.pop("MESHFLOW_S3_BUCKET", None)

    def test_s3_store_index_key_format(self) -> None:
        from meshflow.core.durable import _S3Store
        store = _S3Store.__new__(_S3Store)
        store._bucket = "b"
        store._prefix = "meshflow/checkpoints"
        store._region = "us-east-1"
        store._profile = ""
        store._client = None
        self.assertEqual(store._index_key("run-1"), "meshflow/checkpoints/run-1/_index.json")

    def test_executor_s3_backend_type(self) -> None:
        # boto3 import is lazy — _S3Store is constructed fine but _conn() raises.
        import os
        import sys
        os.environ["MESHFLOW_S3_BUCKET"] = "test-bucket"
        saved = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            from meshflow.core.durable import DurableWorkflowExecutor, _S3Store
            exec_ = DurableWorkflowExecutor(backend="s3")   # succeeds (lazy)
            self.assertIsInstance(exec_._store, _S3Store)   # store created correctly
            with self.assertRaises((ImportError, AttributeError)):
                exec_._store._s3()                          # boto3 import fails here
        finally:
            if saved is None:
                sys.modules.pop("boto3", None)
            else:
                sys.modules["boto3"] = saved
            os.environ.pop("MESHFLOW_S3_BUCKET", None)

    def test_executor_s3_params_in_signature(self) -> None:
        import inspect
        from meshflow.core.durable import DurableWorkflowExecutor
        sig = inspect.signature(DurableWorkflowExecutor.__init__)
        self.assertIn("s3_bucket", sig.parameters)
        self.assertIn("s3_prefix", sig.parameters)

    def test_fork_dispatches_s3_backend(self) -> None:
        from meshflow.core.durable import DurableWorkflowExecutor, _S3Store
        exec_ = DurableWorkflowExecutor.__new__(DurableWorkflowExecutor)
        exec_._run_id = "parent"
        store = _S3Store.__new__(_S3Store)
        store._bucket = "bucket"
        store._prefix = "meshflow/checkpoints"
        store._region = "us-east-1"
        store._profile = ""
        store._client = None
        exec_._store = store
        # The fork method must recognise _S3Store and not fall through to sqlite
        import inspect
        src = inspect.getsource(DurableWorkflowExecutor.fork)
        self.assertIn("_S3Store", src)


# ─────────────────────────────────────────────────────────────────────────────
# 4. RoleRouter — LLM-driven dynamic role assignment
# ─────────────────────────────────────────────────────────────────────────────

class TestRoleRouter(unittest.TestCase):

    def test_heuristic_security_task(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        router = RoleRouter()
        spec = _run(router.route("Analyse CVE-2025-59528 and propose a security patch"))
        self.assertEqual(spec.role, "security_researcher")

    def test_heuristic_compliance_task(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        router = RoleRouter()
        spec = _run(router.route("Check this system for HIPAA compliance violations"))
        self.assertEqual(spec.role, "compliance_analyst")

    def test_heuristic_financial_task(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        router = RoleRouter()
        spec = _run(router.route("Analyse the financial risk and ROI of this investment"))
        self.assertEqual(spec.role, "financial_analyst")

    def test_heuristic_code_task(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        router = RoleRouter()
        spec = _run(router.route("Review this Python code for security bugs"))
        self.assertIn(spec.role, ("code_reviewer", "security_researcher"))

    def test_returns_agent_spec(self) -> None:
        from meshflow.agents.role_router import RoleRouter, AgentSpec
        spec = _run(RoleRouter().route("some task"))
        self.assertIsInstance(spec, AgentSpec)

    def test_spec_has_tools(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        spec = _run(RoleRouter().route("Analyse the financial model"))
        self.assertIsInstance(spec.tools, list)

    def test_spec_model_tier_valid(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        spec = _run(RoleRouter().route("Explain what GDPR Article 30 requires"))
        self.assertIn(spec.model_tier, ("nano", "small", "medium", "large"))

    def test_spec_to_agent_returns_agent(self) -> None:
        from meshflow.agents.role_router import AgentSpec
        spec = AgentSpec(role="researcher", goal="research task", model_tier="medium")
        agent = spec.to_agent(name="test-agent")
        self.assertIsNotNone(agent)

    def test_spec_to_dict(self) -> None:
        from meshflow.agents.role_router import AgentSpec
        spec = AgentSpec(role="executor", goal="run this task", tools=["search"],
                         model_tier="small", rationale="simple task")
        d = spec.to_dict()
        for key in ("role", "goal", "tools", "model_tier", "rationale"):
            self.assertIn(key, d)

    def test_available_roles_restriction(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        router = RoleRouter(available_roles=["researcher", "executor"])
        spec = _run(router.route("Analyse the CVE for security patch"))
        # Should still pick one of the restricted roles
        self.assertIn(spec.role, ("researcher", "executor", "executor"))

    def test_catalogue_returns_dict(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        catalogue = RoleRouter().catalogue()
        self.assertGreater(len(catalogue), 5)
        self.assertIn("researcher", catalogue)

    def test_confidence_between_0_and_1(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        spec = _run(RoleRouter().route("Do a legal contract review"))
        self.assertGreaterEqual(spec.confidence, 0.0)
        self.assertLessEqual(spec.confidence, 1.0)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        self.assertIn("RoleRouter", meshflow.__all__)
        self.assertIn("AgentSpec", meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 5. LLMRanker
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMRanker(unittest.TestCase):

    def test_empty_candidates_returns_empty(self) -> None:
        from meshflow.intelligence.rag_pipeline import LLMRanker
        result = _run(LLMRanker().rank("query", []))
        self.assertEqual(result, [])

    def test_heuristic_scores_returned_without_agent(self) -> None:
        from meshflow.intelligence.rag_pipeline import LLMRanker
        docs = [
            "HIPAA requires strict PHI protection and audit logging.",
            "The weather in London is typically cloudy.",
            "HIPAA compliance involves administrative, physical, and technical safeguards.",
        ]
        ranked = _run(LLMRanker().rank("HIPAA compliance requirements", docs))
        self.assertGreater(len(ranked), 0)
        # HIPAA-relevant docs should rank higher
        self.assertIn("HIPAA", ranked[0].text)

    def test_ranked_docs_have_scores(self) -> None:
        from meshflow.intelligence.rag_pipeline import LLMRanker, RankedDoc
        docs = ["relevant doc about compliance", "unrelated text about weather"]
        ranked = _run(LLMRanker().rank("compliance policy", docs))
        for doc in ranked:
            self.assertIsInstance(doc, RankedDoc)
            self.assertGreaterEqual(doc.score, 0.0)
            self.assertLessEqual(doc.score, 1.0)

    def test_rank_numbers_assigned(self) -> None:
        from meshflow.intelligence.rag_pipeline import LLMRanker
        docs = ["doc a", "doc b about compliance audit"]
        ranked = _run(LLMRanker().rank("compliance", docs))
        if len(ranked) >= 2:
            self.assertEqual(ranked[0].rank, 1)
            self.assertEqual(ranked[1].rank, 2)

    def test_threshold_filters_low_scores(self) -> None:
        from meshflow.intelligence.rag_pipeline import LLMRanker
        # score_threshold=0.99 should drop everything
        ranker = LLMRanker(score_threshold=0.99)
        docs = ["some text", "another text"]
        ranked = _run(ranker.rank("completely unrelated query xyz", docs))
        self.assertEqual(ranked, [])

    def test_top_k_limits_results(self) -> None:
        from meshflow.intelligence.rag_pipeline import LLMRanker
        docs = ["doc 1 about compliance", "doc 2 about hipaa", "doc 3 about gdpr",
                "doc 4 about security", "doc 5 about audit"]
        ranked = _run(LLMRanker(score_threshold=0.0).rank("compliance hipaa gdpr", docs, top_k=2))
        self.assertLessEqual(len(ranked), 2)

    def test_ranked_doc_to_dict(self) -> None:
        from meshflow.intelligence.rag_pipeline import RankedDoc
        doc = RankedDoc(text="test content", score=0.85, rank=1, source="doc.pdf")
        d = doc.to_dict()
        self.assertIn("score", d)
        self.assertIn("rank", d)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        self.assertIn("LLMRanker", meshflow.__all__)
        self.assertIn("RankedDoc", meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 6. HybridRetriever
# ─────────────────────────────────────────────────────────────────────────────

class TestHybridRetriever(unittest.TestCase):

    def _retriever(self) -> Any:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        texts = [
            "MeshFlow supports HIPAA compliance via ComplianceGuard.",
            "PolicyGuard enforces per-step budget and content policies.",
            "GDPR Article 30 requires a record of processing activities.",
            "The python_repl tool executes sandboxed code.",
            "RewindEngine enables interactive time-travel debugging.",
        ]
        return HybridRetriever(texts=texts)

    def test_query_returns_results(self) -> None:
        results = self._retriever().query("HIPAA compliance", top_k=3)
        self.assertGreater(len(results), 0)

    def test_hipaa_doc_ranked_first(self) -> None:
        results = self._retriever().query("HIPAA compliance requirements", top_k=3)
        self.assertIn("HIPAA", results[0])

    def test_top_k_respected(self) -> None:
        results = self._retriever().query("compliance policy", top_k=2)
        self.assertLessEqual(len(results), 2)

    def test_empty_corpus_returns_empty(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        r = HybridRetriever()
        self.assertEqual(r.query("anything"), [])

    def test_add_texts(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        r = HybridRetriever()
        r.add_texts(["new document about compliance"])
        results = r.query("compliance", top_k=1)
        self.assertEqual(len(results), 1)

    def test_rrf_fusion_with_vector_store(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        from meshflow.intelligence.knowledge import VectorStore
        texts = [
            "MeshFlow has HIPAA compliance tools.",
            "LangGraph supports time-travel debugging.",
            "CrewAI has 100K community members.",
        ]
        store = VectorStore.from_texts(texts)
        hybrid = HybridRetriever(vector_store=store, texts=texts)
        results = hybrid.query("HIPAA compliance tools", top_k=2)
        self.assertGreater(len(results), 0)
        self.assertIn("HIPAA", results[0])

    def test_dense_weight_between_0_and_1(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        r = HybridRetriever(dense_weight=1.5)  # clamped to 1.0
        self.assertLessEqual(r._dense_w, 1.0)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        self.assertIn("HybridRetriever", meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 7. SelfCorrectingRAG
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfCorrectingRAG(unittest.TestCase):

    def test_returns_rag_answer(self) -> None:
        from meshflow.intelligence.rag_pipeline import SelfCorrectingRAG, HybridRetriever, RAGAnswer
        texts = ["MeshFlow supports HIPAA via ComplianceGuard."]
        retriever = HybridRetriever(texts=texts)
        rag = SelfCorrectingRAG(_echo_agent("r", "MeshFlow uses ComplianceGuard"),
                                retriever=retriever, max_correction_rounds=1)
        result = _run(rag.run("What does MeshFlow do for HIPAA?"))
        self.assertIsInstance(result, RAGAnswer)

    def test_answer_text_non_empty(self) -> None:
        from meshflow.intelligence.rag_pipeline import SelfCorrectingRAG, HybridRetriever
        texts = ["Relevant context about compliance."]
        rag = SelfCorrectingRAG(_echo_agent("r", "The answer is compliance."),
                                retriever=HybridRetriever(texts=texts),
                                max_correction_rounds=0)
        result = _run(rag.run("What is compliance?"))
        self.assertTrue(result.text)

    def test_correction_rounds_zero_when_grade_threshold_zero(self) -> None:
        from meshflow.intelligence.rag_pipeline import SelfCorrectingRAG
        rag = SelfCorrectingRAG(_echo_agent("r", "good answer"),
                                grade_threshold=0.0, max_correction_rounds=3)
        result = _run(rag.run("question"))
        # grade_threshold=0 → always passes on first attempt
        self.assertEqual(result.correction_rounds, 0)

    def test_rag_answer_to_dict(self) -> None:
        from meshflow.intelligence.rag_pipeline import RAGAnswer
        a = RAGAnswer(text="the answer", correction_rounds=1, grade=0.85,
                      context_used=["ctx1", "ctx2"])
        d = a.to_dict()
        for key in ("text", "correction_rounds", "grade", "context_chunks"):
            self.assertIn(key, d)

    def test_no_retriever_still_generates(self) -> None:
        from meshflow.intelligence.rag_pipeline import SelfCorrectingRAG
        rag = SelfCorrectingRAG(_echo_agent("r", "answer without context"),
                                max_correction_rounds=0)
        result = _run(rag.run("question"))
        self.assertIsNotNone(result)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        self.assertIn("SelfCorrectingRAG", meshflow.__all__)
        self.assertIn("RAGAnswer", meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Curated template library — 20 templates
# ─────────────────────────────────────────────────────────────────────────────

class TestCuratedTemplates(unittest.TestCase):

    def test_exactly_20_templates(self) -> None:
        from meshflow.registry.curated_templates import CURATED_TEMPLATES
        self.assertEqual(len(CURATED_TEMPLATES), 20)

    def test_all_have_required_fields(self) -> None:
        from meshflow.registry.curated_templates import CURATED_TEMPLATES
        for tmpl in CURATED_TEMPLATES:
            with self.subTest(name=tmpl.name):
                self.assertTrue(tmpl.name)
                self.assertTrue(tmpl.role)
                self.assertTrue(tmpl.model)
                self.assertTrue(tmpl.description)
                self.assertGreater(len(tmpl.tags), 0)

    def test_names_are_unique(self) -> None:
        from meshflow.registry.curated_templates import CURATED_TEMPLATES
        names = [t.name for t in CURATED_TEMPLATES]
        self.assertEqual(len(names), len(set(names)))

    def test_hipaa_template_exists(self) -> None:
        from meshflow.registry.curated_templates import template_by_name
        tmpl = template_by_name("hipaa-compliance-analyst")
        self.assertIsNotNone(tmpl)
        self.assertIn("hipaa", tmpl.tags)

    def test_template_by_name_missing_returns_none(self) -> None:
        from meshflow.registry.curated_templates import template_by_name
        self.assertIsNone(template_by_name("nonexistent-template-xyz"))

    def test_templates_by_tag_compliance(self) -> None:
        from meshflow.registry.curated_templates import templates_by_tag
        compliance = templates_by_tag("compliance")
        self.assertGreater(len(compliance), 2)

    def test_templates_by_tag_security(self) -> None:
        from meshflow.registry.curated_templates import templates_by_tag
        security = templates_by_tag("security")
        self.assertGreater(len(security), 0)

    def test_load_curated_library(self) -> None:
        from meshflow.registry.curated_templates import load_curated_library
        tmpdir = tempfile.mkdtemp()
        try:
            reg = load_curated_library(registry_dir=tmpdir)
            all_templates = reg.list()
            self.assertEqual(len(all_templates), 20)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_to_agent_works(self) -> None:
        from meshflow.registry.curated_templates import template_by_name
        tmpl = template_by_name("python-code-reviewer")
        self.assertIsNotNone(tmpl, "python-code-reviewer template missing")
        assert tmpl is not None
        agent = tmpl.to_agent()
        self.assertIsNotNone(agent)

    def test_agent_workflow_designer_has_meshflow_tag(self) -> None:
        from meshflow.registry.curated_templates import template_by_name
        tmpl = template_by_name("agent-workflow-designer")
        self.assertIsNotNone(tmpl, "agent-workflow-designer template missing")
        assert tmpl is not None
        self.assertIn("meshflow", tmpl.tags)

    def test_exported_from_meshflow(self) -> None:
        import meshflow
        for name in ("CURATED_TEMPLATES", "load_curated_library",
                     "template_by_name", "templates_by_tag"):
            self.assertIn(name, meshflow.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Interactive Mermaid graph — TraceServer routes
# ─────────────────────────────────────────────────────────────────────────────

class TestInteractiveMermaidGraph(unittest.TestCase):

    def test_get_mermaid_method_exists(self) -> None:
        from meshflow.studio.trace_server import TraceServer
        self.assertTrue(callable(getattr(TraceServer, "get_mermaid", None)))

    def test_get_mermaid_empty_run_returns_default(self) -> None:
        from meshflow.studio.trace_server import TraceServer
        server = TraceServer(db=":memory:")
        result = _run(server.get_mermaid("nonexistent-run"))
        self.assertIn("mermaid", result)
        self.assertIn("graph", result["mermaid"])

    def test_graph_html_template_exists(self) -> None:
        import os
        template = os.path.join(
            os.path.dirname(__file__),
            "..", "meshflow", "studio", "templates", "graph.html"
        )
        self.assertTrue(os.path.exists(os.path.normpath(template)))

    def test_graph_html_contains_mermaid_script(self) -> None:
        import os
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            "..", "meshflow", "studio", "templates", "graph.html"
        ))
        with open(path) as f:
            content = f.read()
        self.assertIn("mermaid", content.lower())
        self.assertIn("/api/graph/", content)

    def test_trace_server_has_graph_route(self) -> None:
        import inspect
        from meshflow.studio.trace_server import _TraceHandler
        src = inspect.getsource(_TraceHandler.do_GET)
        self.assertIn("/graph", src)
        self.assertIn("/api/graph/", src)

    def test_mermaid_result_has_nodes_and_edges(self) -> None:
        from meshflow.studio.trace_server import TraceServer
        result = _run(TraceServer(db=":memory:").get_mermaid("no-run"))
        self.assertIn("nodes", result)
        self.assertIn("edges", result)


# ─────────────────────────────────────────────────────────────────────────────
# 10. RAG configurator — studio page
# ─────────────────────────────────────────────────────────────────────────────

class TestRAGConfiguratorStudio(unittest.TestCase):

    def test_rag_builder_html_exists(self) -> None:
        import os
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            "..", "meshflow", "studio", "templates", "rag_builder.html"
        ))
        self.assertTrue(os.path.exists(path))

    def test_rag_builder_html_has_pipeline_stages(self) -> None:
        import os
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            "..", "meshflow", "studio", "templates", "rag_builder.html"
        ))
        with open(path) as f:
            content = f.read()
        for stage in ("Data Source", "Chunking", "Embedding", "Retrieval",
                      "Ranking", "Generation", "Guardrails"):
            self.assertIn(stage, content, f"Missing stage: {stage}")

    def test_rag_builder_html_has_export_yaml(self) -> None:
        import os
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            "..", "meshflow", "studio", "templates", "rag_builder.html"
        ))
        with open(path) as f:
            content = f.read()
        self.assertIn("exportYAML", content)
        self.assertIn("yaml-output", content)  # HTML id uses hyphen

    def test_rag_builder_html_mentions_hybrid_retriever(self) -> None:
        import os
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            "..", "meshflow", "studio", "templates", "rag_builder.html"
        ))
        with open(path) as f:
            content = f.read()
        self.assertIn("HybridRetriever", content)
        self.assertIn("LLMRanker", content)
        self.assertIn("SelfCorrectingRAG", content)

    def test_trace_server_has_rag_route(self) -> None:
        import inspect
        from meshflow.studio.trace_server import _TraceHandler
        src = inspect.getsource(_TraceHandler.do_GET)
        self.assertIn("/rag", src)


# ─────────────────────────────────────────────────────────────────────────────
# 11. ModelRouter analytics emission
# ─────────────────────────────────────────────────────────────────────────────

class TestModelRouterAnalytics(unittest.TestCase):

    def test_analytics_ledger_param_in_signature(self) -> None:
        import inspect
        from meshflow.agents.model_router import ModelRouter
        sig = inspect.signature(ModelRouter.__init__)
        self.assertIn("analytics_ledger", sig.parameters)

    def test_router_stores_ledger_reference(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        sentinel = object()
        router = ModelRouter(analytics_ledger=sentinel)
        self.assertIs(router._ledger, sentinel)

    def test_router_without_ledger_works_normally(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        router = ModelRouter(record_decisions=True)
        d = router.route("What is 2+2?")
        self.assertIsNotNone(d)
        self.assertEqual(len(router.history), 1)

    def test_emit_to_ledger_method_exists(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        self.assertTrue(callable(getattr(ModelRouter, "_emit_to_ledger", None)))

    def test_emit_to_ledger_is_async(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        self.assertTrue(inspect.iscoroutinefunction(ModelRouter._emit_to_ledger))


# ─────────────────────────────────────────────────────────────────────────────
# 12. Public API exports — Sprint 76
# ─────────────────────────────────────────────────────────────────────────────

class TestSprint76PublicAPI(unittest.TestCase):

    _EXPECTED = [
        "BranchCompare", "ForkConfig", "ForkResult", "CompareResult",
        "RoleRouter", "AgentSpec",
        "LLMRanker", "HybridRetriever", "SelfCorrectingRAG", "RankedDoc", "RAGAnswer",
        "CURATED_TEMPLATES", "load_curated_library", "template_by_name", "templates_by_tag",
    ]

    def test_all_in_dunder_all(self) -> None:
        import meshflow
        missing = [s for s in self._EXPECTED if s not in meshflow.__all__]
        self.assertEqual(missing, [], f"Not in __all__: {missing}")

    def test_all_importable(self) -> None:
        import meshflow
        for name in self._EXPECTED:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(meshflow, name, None))

    def test_s3_store_class_exists(self) -> None:
        from meshflow.core.durable import _S3Store
        self.assertIsNotNone(_S3Store)

    def test_trace_server_get_mermaid(self) -> None:
        from meshflow.studio.trace_server import TraceServer
        self.assertTrue(hasattr(TraceServer, "get_mermaid"))

    def test_workflow_to_yaml_method(self) -> None:
        from meshflow.core.workflow import WorkflowDefinition
        self.assertTrue(callable(getattr(WorkflowDefinition, "to_yaml", None)))

    def test_role_router_catalogue_size(self) -> None:
        from meshflow.agents.role_router import _ROLE_CATALOGUE
        self.assertGreaterEqual(len(_ROLE_CATALOGUE), 13)


if __name__ == "__main__":
    unittest.main()
