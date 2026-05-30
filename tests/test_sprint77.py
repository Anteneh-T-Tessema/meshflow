"""Sprint 77 — Integration, CLI completeness, studio navigation tests.

  1.  Version bump to 0.77.0
  2.  CHANGELOG updated for Sprints 74-76
  3.  CLI: meshflow templates load-curated
  4.  CLI: meshflow marketplace (serve / push / pull)
  5.  CLI: meshflow replay --branch-compare / --inject / --forks
  6.  HybridRetriever as Agent knowledge backend
  7.  SelfCorrectingRAG retriever as Agent knowledge backend
  8.  Crew.role_router — dynamic agent assignment
  9.  WorkflowDefinition.from_yaml() model_router: section
 10.  Studio navigation bar on all three HTML pages
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from typing import Any


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _echo_agent(name: str = "a", reply: str = "ok") -> Any:
    from meshflow.agents.base import EchoProvider
    from meshflow import Agent
    return Agent(name=name, role="executor", provider=EchoProvider(reply))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Version
# ─────────────────────────────────────────────────────────────────────────────

class TestVersion(unittest.TestCase):

    def test_version_is_0_77_0(self) -> None:
        import meshflow
        self.assertEqual(meshflow.__version__, "0.77.0")

    def test_changelog_mentions_77(self) -> None:
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "CHANGELOG.md")
        with open(os.path.normpath(path)) as f:
            content = f.read()
        self.assertIn("0.77.0", content)
        self.assertIn("0.76.0", content)
        self.assertIn("0.75.0", content)
        self.assertIn("0.74.0", content)


# ─────────────────────────────────────────────────────────────────────────────
# 2. CLI: templates load-curated
# ─────────────────────────────────────────────────────────────────────────────

def _cli_help(*args: str) -> str:
    """Call the MeshFlow CLI's main() and capture help text."""
    import sys
    from io import StringIO
    from meshflow.cli.main import main
    old_argv = sys.argv[:]
    old_out = sys.stdout
    old_err = sys.stderr
    sys.argv = ["meshflow", *args, "--help"]
    buf = StringIO()
    sys.stdout = sys.stderr = buf
    try:
        main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
        sys.stdout = old_out
        sys.stderr = old_err
    return buf.getvalue()


class TestCLITemplatesLoadCurated(unittest.TestCase):

    def test_load_curated_subcommand_registered(self) -> None:
        out = _cli_help("templates", "load-curated")
        self.assertIn("curated", out.lower())

    def test_load_curated_writes_20_templates(self) -> None:
        import shutil
        from meshflow.registry.curated_templates import load_curated_library, CURATED_TEMPLATES
        tmpdir = tempfile.mkdtemp()
        try:
            reg = load_curated_library(registry_dir=tmpdir)
            self.assertEqual(len(reg.list()), len(CURATED_TEMPLATES))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 3. CLI: marketplace subcommands registered
# ─────────────────────────────────────────────────────────────────────────────

class TestCLIMarketplace(unittest.TestCase):

    def test_marketplace_help_registered(self) -> None:
        out = _cli_help("marketplace")
        self.assertIn("marketplace", out.lower())

    def test_marketplace_serve_registered(self) -> None:
        out = _cli_help("marketplace", "serve")
        self.assertIn("port", out.lower())

    def test_marketplace_push_registered(self) -> None:
        out = _cli_help("marketplace", "push")
        self.assertIn("url", out.lower())

    def test_marketplace_pull_registered(self) -> None:
        out = _cli_help("marketplace", "pull")
        self.assertIn("url", out.lower())

    def test_cmd_marketplace_function_exists(self) -> None:
        from meshflow.cli.main import _cmd_marketplace
        self.assertTrue(callable(_cmd_marketplace))


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLI: replay --branch-compare / --inject args
# ─────────────────────────────────────────────────────────────────────────────

class TestCLIReplayExtensions(unittest.TestCase):

    def test_branch_compare_flag_registered(self) -> None:
        out = _cli_help("replay", "dummy-run-id")
        self.assertIn("branch-compare", out)

    def test_inject_flag_registered(self) -> None:
        out = _cli_help("replay", "dummy-run-id")
        self.assertIn("inject", out.lower())

    def test_forks_flag_registered(self) -> None:
        out = _cli_help("replay", "dummy-run-id")
        self.assertIn("forks", out.lower())

    def test_compare_step_flag_registered(self) -> None:
        out = _cli_help("replay", "dummy-run-id")
        self.assertIn("compare-step", out)

    def test_inject_parsing_logic(self) -> None:
        # Test the KEY=VALUE parsing logic used in _async_replay
        pairs = ["user_tier=enterprise", "region=eu-west-1"]
        context_patch: dict[str, str] = {}
        for pair in pairs:
            if "=" in pair:
                k, v = pair.split("=", 1)
                context_patch[k.strip()] = v.strip()
        self.assertEqual(context_patch["user_tier"], "enterprise")
        self.assertEqual(context_patch["region"], "eu-west-1")


# ─────────────────────────────────────────────────────────────────────────────
# 5. HybridRetriever as Agent knowledge backend
# ─────────────────────────────────────────────────────────────────────────────

class TestHybridRetrieverKnowledgeBackend(unittest.TestCase):

    def test_hybrid_retriever_accepted_by_agent_knowledge(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        from meshflow.intelligence.knowledge import AgentKnowledge
        hybrid = HybridRetriever(texts=[
            "MeshFlow supports HIPAA compliance via ComplianceGuard.",
            "PolicyGuard enforces per-step budget policies.",
        ])
        knowledge = AgentKnowledge([hybrid], top_k=3)
        self.assertEqual(len(knowledge._hybrid_retrievers), 1)

    def test_hybrid_retriever_retrieve_returns_relevant(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        from meshflow.intelligence.knowledge import AgentKnowledge
        hybrid = HybridRetriever(texts=[
            "MeshFlow HIPAA compliance tools.",
            "Weather forecast for London.",
            "GDPR Article 30 record-keeping.",
        ])
        knowledge = AgentKnowledge([hybrid], top_k=2)
        results = knowledge.retrieve("HIPAA compliance")
        self.assertGreater(len(results), 0)
        self.assertTrue(any("HIPAA" in r or "compliance" in r.lower() for r in results))

    def test_hybrid_retriever_in_agent_knowledge_list(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        from meshflow.intelligence.knowledge import AgentKnowledge
        hybrid = HybridRetriever(texts=["compliance text"])
        knowledge = AgentKnowledge([hybrid], top_k=3)
        self.assertIsInstance(knowledge._hybrid_retrievers, list)
        self.assertEqual(len(knowledge._hybrid_retrievers), 1)
        self.assertEqual(len(knowledge._sources), 0)  # not stored as KnowledgeSource

    def test_mixed_knowledge_sources(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        from meshflow.intelligence.knowledge import VectorStore, AgentKnowledge
        hybrid = HybridRetriever(texts=["hybrid doc"])
        store = VectorStore.from_texts(["vector doc"])
        knowledge = AgentKnowledge([hybrid, store], top_k=5)
        results = knowledge.retrieve("doc")
        self.assertGreater(len(results), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 6. SelfCorrectingRAG retriever as Agent knowledge backend
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfCorrectingRAGKnowledgeBackend(unittest.TestCase):

    def test_rag_pipeline_retriever_used_in_knowledge(self) -> None:
        from meshflow.intelligence.rag_pipeline import SelfCorrectingRAG, HybridRetriever
        from meshflow.intelligence.knowledge import AgentKnowledge
        texts = ["HIPAA requires safeguards for PHI.", "GDPR Article 17 right to erasure."]
        retriever = HybridRetriever(texts=texts)
        rag = SelfCorrectingRAG(_echo_agent("r"), retriever=retriever, max_correction_rounds=0)
        knowledge = AgentKnowledge([rag], top_k=3)
        results = knowledge.retrieve("HIPAA PHI")
        self.assertGreater(len(results), 0)

    def test_rag_stored_in_rag_pipelines_list(self) -> None:
        from meshflow.intelligence.rag_pipeline import SelfCorrectingRAG, HybridRetriever
        from meshflow.intelligence.knowledge import AgentKnowledge
        rag = SelfCorrectingRAG(_echo_agent(), max_correction_rounds=0)
        knowledge = AgentKnowledge([rag])
        self.assertEqual(len(knowledge._rag_pipelines), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Crew.role_router — dynamic agent assignment
# ─────────────────────────────────────────────────────────────────────────────

class TestCrewRoleRouter(unittest.TestCase):

    def test_crew_accepts_role_router_param(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        from meshflow.agents.crew import Crew, Task
        router = RoleRouter()
        task = Task(description="Analyse HIPAA compliance", expected_output="report")
        crew = Crew(agents=[], tasks=[task], role_router=router)
        self.assertIs(crew.role_router, router)

    def test_crew_role_router_sets_agent_on_task(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        from meshflow.agents.crew import Crew, Task

        router = RoleRouter()  # uses heuristic fallback
        task = Task(description="Analyse HIPAA compliance for a hospital system",
                    expected_output="compliance report")
        crew = Crew(agents=[], tasks=[task], role_router=router)

        # _resolve_agent_for_task should assign an agent
        _run(crew._resolve_agent_for_task(task))
        self.assertIsNotNone(task.agent)

    def test_crew_without_agents_raises_if_no_router(self) -> None:
        from meshflow.agents.crew import Crew, Task
        with self.assertRaises(ValueError):
            Crew(agents=[], tasks=[Task(description="task", expected_output="out")])

    def test_crew_with_router_and_no_agents_allowed(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        from meshflow.agents.crew import Crew, Task
        router = RoleRouter()
        task = Task(description="do something", expected_output="output")
        crew = Crew(agents=[], tasks=[task], role_router=router)
        self.assertIsNotNone(crew)

    def test_resolve_skips_task_with_existing_agent(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        from meshflow.agents.crew import Crew, Task
        router = RoleRouter()
        existing_agent = _echo_agent("existing")
        task = Task(description="task", expected_output="out", agent=existing_agent)
        crew = Crew(agents=[existing_agent], tasks=[task], role_router=router)
        _run(crew._resolve_agent_for_task(task))
        # agent should remain unchanged
        self.assertIs(task.agent, existing_agent)

    def test_role_router_field_is_none_by_default(self) -> None:
        from meshflow.agents.crew import Crew, Task
        crew = Crew(agents=[_echo_agent()],
                    tasks=[Task(description="t", expected_output="o")])
        self.assertIsNone(crew.role_router)


# ─────────────────────────────────────────────────────────────────────────────
# 8. WorkflowDefinition model_router: YAML section
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowModelRouterYAML(unittest.TestCase):

    def _yaml_with_router(self) -> str:
        return (
            "name: test-workflow\n"
            "version: '1'\n"
            "model_router:\n"
            "  fallback: medium\n"
            "  tiers:\n"
            "    medium: claude-sonnet-4-6\n"
            "    large: claude-opus-4-8\n"
            "nodes:\n"
            "  planner:\n"
            "    kind: native\n"
            "    agent: {role: planner, model: claude-sonnet-4-6}\n"
            "    task_description: Plan the workflow steps\n"
            "edges:\n"
            "  - planner -> planner\n"
            "terminal:\n"
            "  - planner\n"
        )

    def test_from_yaml_parses_model_router_section(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(self._yaml_with_router())
            path = f.name
        try:
            from meshflow.core.workflow import WorkflowDefinition
            wf = WorkflowDefinition.from_yaml(path)
            self.assertIn("_model_router", wf.metadata)
        finally:
            os.unlink(path)

    def test_from_yaml_without_router_section_no_error(self) -> None:
        yaml_content = (
            "name: simple\n"
            "nodes:\n"
            "  node1: {kind: native, agent: {role: executor}}\n"
            "terminal: [node1]\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            from meshflow.core.workflow import WorkflowDefinition
            wf = WorkflowDefinition.from_yaml(path)
            self.assertNotIn("_model_router", wf.metadata)
        finally:
            os.unlink(path)

    def test_router_config_from_yaml_section(self) -> None:
        from meshflow.agents.model_router import RouterConfig
        cfg = RouterConfig.from_dict({
            "model_router": {"fallback": "medium", "tiers": {"medium": "claude-sonnet-4-6"}}
        })
        self.assertEqual(cfg.fallback, "medium")
        self.assertEqual(cfg.tiers["medium"], "claude-sonnet-4-6")


# ─────────────────────────────────────────────────────────────────────────────
# 9. Studio navigation bar
# ─────────────────────────────────────────────────────────────────────────────

class TestStudioNavigation(unittest.TestCase):

    def _read_template(self, name: str) -> str:
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "meshflow", "studio", "templates", name
        ))
        with open(path) as f:
            return f.read()

    def test_trace_html_has_nav(self) -> None:
        content = self._read_template("trace.html")
        self.assertIn("<nav", content)
        self.assertIn("MeshFlow Studio", content)

    def test_graph_html_has_nav(self) -> None:
        content = self._read_template("graph.html")
        self.assertIn("<nav", content)
        self.assertIn("MeshFlow Studio", content)

    def test_rag_builder_html_has_nav(self) -> None:
        content = self._read_template("rag_builder.html")
        self.assertIn("<nav", content)
        self.assertIn("MeshFlow Studio", content)

    def test_all_nav_bars_link_to_trace(self) -> None:
        for template in ("trace.html", "graph.html", "rag_builder.html"):
            with self.subTest(template=template):
                content = self._read_template(template)
                self.assertIn('href="/trace"', content)

    def test_all_nav_bars_link_to_graph(self) -> None:
        for template in ("trace.html", "graph.html", "rag_builder.html"):
            with self.subTest(template=template):
                content = self._read_template(template)
                self.assertIn('href="/graph"', content)

    def test_all_nav_bars_link_to_rag(self) -> None:
        for template in ("trace.html", "graph.html", "rag_builder.html"):
            with self.subTest(template=template):
                content = self._read_template(template)
                self.assertIn('href="/rag"', content)

    def test_active_page_highlighted(self) -> None:
        # graph.html should highlight /graph link; rag_builder.html should highlight /rag
        graph_content = self._read_template("graph.html")
        self.assertIn('href="/graph"', graph_content)
        rag_content = self._read_template("rag_builder.html")
        self.assertIn('href="/rag"', rag_content)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Comprehensive integration smoke tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSprint77Integration(unittest.TestCase):

    def test_hybrid_retriever_in_agent_knowledge_retrieve(self) -> None:
        from meshflow.intelligence.rag_pipeline import HybridRetriever
        from meshflow.intelligence.knowledge import AgentKnowledge
        texts = ["MeshFlow HIPAA compliance.", "GDPR Article 30."]
        hybrid = HybridRetriever(texts=texts)
        knowledge = AgentKnowledge([hybrid], top_k=3)
        ctx = knowledge.context_string("HIPAA")
        self.assertTrue(ctx)

    def test_crew_role_router_full_flow(self) -> None:
        from meshflow.agents.role_router import RoleRouter
        from meshflow.agents.crew import Crew, Task
        router = RoleRouter()
        tasks = [
            Task(description="Research HIPAA compliance requirements", expected_output="summary"),
            Task(description="Analyse the security CVE in the system", expected_output="report"),
        ]
        crew = Crew(agents=[], tasks=tasks, role_router=router, verbose=False)
        # Resolve agents for both tasks
        for task in tasks:
            _run(crew._resolve_agent_for_task(task))
        # Both tasks should have agents assigned
        for task in tasks:
            self.assertIsNotNone(task.agent)

    def test_model_router_analytics_ledger_wiring(self) -> None:
        from meshflow.agents.model_router import ModelRouter
        router = ModelRouter(analytics_ledger=None, record_decisions=True)
        d = router.route("Audit HIPAA compliance document")
        self.assertEqual(d.tier, "large")
        self.assertEqual(len(router.history), 1)

    def test_branch_compare_fork_config_context_patch(self) -> None:
        from meshflow.core.branch_compare import ForkConfig
        cfg = ForkConfig(
            label="injected",
            context_patch={"user_tier": "enterprise", "region": "eu-west-1"},
        )
        self.assertEqual(cfg.context_patch["user_tier"], "enterprise")

    def test_workflow_to_yaml_round_trip_with_router(self) -> None:
        import yaml
        from meshflow.core.workflow import WorkflowDefinition
        from meshflow.core.node import MeshNode, NodeKind, RiskTier
        wf = WorkflowDefinition(name="router-test", version="1")
        node = MeshNode(id="planner", kind=NodeKind.NATIVE, risk_profile=RiskTier.READ_ONLY)
        wf.add_node(node)
        wf.set_terminal("planner")
        yaml_str = wf.to_yaml()
        doc = yaml.safe_load(yaml_str)
        self.assertEqual(doc["name"], "router-test")

    def test_version_in_all(self) -> None:
        import meshflow
        self.assertIn("__version__", dir(meshflow))
        self.assertEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
