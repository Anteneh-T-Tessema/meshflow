"""Tests for meshflow.migration — detector, transformer, and CLI plumbing."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from meshflow.migration.detector import DetectionResult, ProjectDetector
from meshflow.migration.transformer import Change, CodeTransformer, TransformResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write *content* to *tmp_path/name* and return the Path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ProjectDetector — framework detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestProjectDetectorFrameworks:
    def test_detects_langgraph_from_import(self, tmp_path: Path) -> None:
        _write(tmp_path, "agent.py", "from langgraph.graph import StateGraph\n")
        result = ProjectDetector(tmp_path).detect()
        assert "langgraph" in result.frameworks

    def test_detects_langgraph_import_statement(self, tmp_path: Path) -> None:
        _write(tmp_path, "agent.py", "import langgraph\n")
        result = ProjectDetector(tmp_path).detect()
        assert "langgraph" in result.frameworks

    def test_detects_crewai(self, tmp_path: Path) -> None:
        _write(tmp_path, "crew.py", "from crewai import Agent, Task, Crew\n")
        result = ProjectDetector(tmp_path).detect()
        assert "crewai" in result.frameworks

    def test_detects_autogen(self, tmp_path: Path) -> None:
        _write(tmp_path, "bot.py", "from autogen import AssistantAgent\n")
        result = ProjectDetector(tmp_path).detect()
        assert "autogen" in result.frameworks

    def test_detects_pyautogen_alias(self, tmp_path: Path) -> None:
        _write(tmp_path, "bot.py", "import pyautogen as autogen\n")
        result = ProjectDetector(tmp_path).detect()
        assert "autogen" in result.frameworks

    def test_detects_openai_agents(self, tmp_path: Path) -> None:
        _write(tmp_path, "bot.py", "from agents import Agent\n")
        result = ProjectDetector(tmp_path).detect()
        assert "openai-agents" in result.frameworks

    def test_no_frameworks_on_plain_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "utils.py", "def add(a, b):\n    return a + b\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.frameworks == []

    def test_detects_multiple_frameworks(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.py", "from langgraph.graph import StateGraph\n")
        _write(tmp_path, "b.py", "from crewai import Agent\n")
        result = ProjectDetector(tmp_path).detect()
        assert "langgraph" in result.frameworks
        assert "crewai" in result.frameworks

    def test_file_count(self, tmp_path: Path) -> None:
        for i in range(4):
            _write(tmp_path, f"file{i}.py", "x = 1\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.file_count == 4


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ProjectDetector — migration_path classification
# ═══════════════════════════════════════════════════════════════════════════════


class TestProjectDetectorMigrationPath:
    def test_zero_rewrite_for_simple_langgraph_stategraph(self, tmp_path: Path) -> None:
        _write(
            tmp_path, "graph.py",
            "from langgraph.graph import StateGraph\nsg = StateGraph(dict)\n"
        )
        result = ProjectDetector(tmp_path).detect()
        assert result.migration_path == "zero_rewrite"

    def test_wrapper_for_simple_crewai(self, tmp_path: Path) -> None:
        _write(tmp_path, "crew.py", "from crewai import Agent\nresearcher = Agent(role='r')\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.migration_path == "wrapper"

    def test_wrapper_for_simple_autogen(self, tmp_path: Path) -> None:
        _write(tmp_path, "bot.py", "from autogen import AssistantAgent\na = AssistantAgent('a')\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.migration_path == "wrapper"

    def test_native_for_multi_framework(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.py", "from langgraph.graph import StateGraph\n")
        _write(tmp_path, "b.py", "from crewai import Agent\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.migration_path == "native"

    def test_native_for_no_frameworks(self, tmp_path: Path) -> None:
        _write(tmp_path, "utils.py", "def foo(): pass\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.migration_path == "native"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ProjectDetector — effort & complexity
# ═══════════════════════════════════════════════════════════════════════════════


class TestProjectDetectorEffort:
    def test_effort_under_1_hour_for_zero_rewrite(self, tmp_path: Path) -> None:
        _write(tmp_path, "g.py", "from langgraph.graph import StateGraph\nsg = StateGraph(dict)\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.estimated_effort == "< 1 hour"

    def test_effort_1_4_hours_for_wrapper(self, tmp_path: Path) -> None:
        _write(tmp_path, "c.py", "from crewai import Agent\na = Agent(role='r')\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.estimated_effort == "1-4 hours"

    def test_complexity_simple_for_few_patterns(self, tmp_path: Path) -> None:
        _write(tmp_path, "f.py", "from langgraph.graph import StateGraph\n")
        result = ProjectDetector(tmp_path).detect()
        assert result.complexity == "simple"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CodeTransformer — import rewrites
# ═══════════════════════════════════════════════════════════════════════════════


class TestCodeTransformerImports:
    def test_suggests_meshflow_stategraph_import(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "g.py", "from langgraph.graph import StateGraph\nsg = StateGraph(dict)\n")
        tr = CodeTransformer().transform(f)
        replacements = [c.replacement for c in tr.suggested_changes]
        assert any("meshflow" in r and "StateGraph" in r for r in replacements)

    def test_suggests_crewai_integration_import(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "c.py", "from crewai import Agent\ndef run_agent(): pass\n")
        tr = CodeTransformer().transform(f)
        replacements = [c.replacement for c in tr.suggested_changes]
        assert any("meshflow" in r and "crewai" in r.lower() for r in replacements)

    def test_suggests_autogen_integration_import(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "a.py", "from autogen import AssistantAgent\ndef run_agent(): pass\n")
        tr = CodeTransformer().transform(f)
        replacements = [c.replacement for c in tr.suggested_changes]
        assert any("meshflow" in r and "autogen" in r.lower() for r in replacements)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CodeTransformer — govern() wrapper
# ═══════════════════════════════════════════════════════════════════════════════


class TestCodeTransformerGovern:
    def test_suggests_govern_for_agent_function(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "a.py", "def run_agent(task):\n    return 'ok'\n")
        tr = CodeTransformer().transform(f)
        descriptions = [c.description for c in tr.suggested_changes]
        assert any("govern" in d.lower() for d in descriptions)

    def test_suggests_govern_decorator_insertion(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "a.py", "def run_agent(task):\n    return 'ok'\n")
        tr = CodeTransformer().transform(f)
        replacements = [c.replacement for c in tr.suggested_changes]
        assert any("@govern" in r for r in replacements)

    def test_no_govern_suggestion_when_already_present(self, tmp_path: Path) -> None:
        f = _write(
            tmp_path, "a.py",
            "from meshflow.governance import govern\n@govern()\ndef run_agent(task):\n    return 'ok'\n"
        )
        tr = CodeTransformer().transform(f)
        descriptions = [c.description for c in tr.suggested_changes]
        # govern is already present — should not suggest it again
        assert not any("@govern" in c.replacement for c in tr.suggested_changes
                       if c.change_type == "insert_before")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CodeTransformer — CostCap
# ═══════════════════════════════════════════════════════════════════════════════


class TestCodeTransformerCostCap:
    def test_suggests_cost_cap_import(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "a.py", "def run_agent(task):\n    return 'ok'\n")
        tr = CodeTransformer().transform(f)
        descriptions = [c.description for c in tr.suggested_changes]
        assert any("cost" in d.lower() or "budget" in d.lower() for d in descriptions)

    def test_no_cost_cap_when_already_present(self, tmp_path: Path) -> None:
        f = _write(
            tmp_path, "a.py",
            "from meshflow.core.schemas import Policy\npolicy = Policy(budget_usd=1.0)\n"
            "CostCap(limit=1.00)\ndef run_agent(task):\n    return 'ok'\n"
        )
        tr = CodeTransformer().transform(f)
        # CostCap already present — no suggestion specifically for CostCap/budget_usd
        descriptions = [c.description for c in tr.suggested_changes]
        assert not any("CostCap" in d or "budget_usd" in d.lower()
                       for d in descriptions)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. TransformResult — dry-run does not write
# ═══════════════════════════════════════════════════════════════════════════════


class TestTransformResultDryRun:
    def test_dry_run_does_not_write_file(self, tmp_path: Path) -> None:
        original = "from langgraph.graph import StateGraph\nsg = StateGraph(dict)\n"
        f = _write(tmp_path, "g.py", original)
        tr = CodeTransformer().transform(f)
        # dry_run=True must not modify the file
        tr.apply(dry_run=True)
        assert f.read_text() == textwrap.dedent(original)

    def test_apply_writes_file_when_not_dry_run(self, tmp_path: Path) -> None:
        original = "from langgraph.graph import StateGraph\nsg = StateGraph(dict)\n"
        f = _write(tmp_path, "g.py", original)
        tr = CodeTransformer().transform(f)
        if tr.has_changes():
            tr.apply(dry_run=False)
            # File should now differ from original
            assert f.read_text() != textwrap.dedent(original)

    def test_dry_run_returns_transformed_source(self, tmp_path: Path) -> None:
        original = "from langgraph.graph import StateGraph\nsg = StateGraph(dict)\n"
        f = _write(tmp_path, "g.py", original)
        tr = CodeTransformer().transform(f)
        if tr.has_changes():
            new_source = tr.apply(dry_run=True)
            assert isinstance(new_source, str)
            assert len(new_source) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. DetectionResult string repr
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectionResultRepr:
    def test_str_contains_key_fields(self, tmp_path: Path) -> None:
        _write(tmp_path, "g.py", "from langgraph.graph import StateGraph\n")
        result = ProjectDetector(tmp_path).detect()
        s = str(result)
        assert "langgraph" in s
        assert "migration_path" in s.lower() or "path" in s.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CLI parser sanity — migrate subcommands are registered
# ═══════════════════════════════════════════════════════════════════════════════


class TestCLIParser:
    def test_migrate_detect_parses(self) -> None:
        from meshflow.cli.main import build_parser
        parser = build_parser()
        ns = parser.parse_args(["migrate", "detect", "--path", "/tmp"])
        assert ns.cmd == "migrate"
        assert ns.migrate_cmd == "detect"
        assert ns.path == "/tmp"

    def test_migrate_plan_parses(self) -> None:
        from meshflow.cli.main import build_parser
        parser = build_parser()
        ns = parser.parse_args(["migrate", "plan"])
        assert ns.migrate_cmd == "plan"

    def test_migrate_apply_dry_run_parses(self) -> None:
        from meshflow.cli.main import build_parser
        parser = build_parser()
        ns = parser.parse_args(["migrate", "apply", "--dry-run"])
        assert ns.migrate_cmd == "apply"
        assert ns.dry_run is True

    def test_migrate_apply_default_path(self) -> None:
        from meshflow.cli.main import build_parser
        parser = build_parser()
        ns = parser.parse_args(["migrate", "apply"])
        assert ns.path == "."
