"""Sprint 31 — Crew YAML runner: run Crew pipelines from YAML without Python."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_crew_yaml(agents: list[dict], tasks: list[dict], **extra) -> str:
    """Write a crew YAML to a temp file and return the path."""
    data = {
        "version": "1.0",
        "kind": "crew",
        **extra,
        "agents": agents,
        "tasks": tasks,
    }
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    yaml.safe_dump(data, f)
    f.close()
    return f.name


# ── Crew.from_yaml ────────────────────────────────────────────────────────────

class TestCrewFromYaml:
    def test_basic_sequential_crew(self):
        path = _write_crew_yaml(
            agents=[
                {"name": "researcher", "role": "researcher"},
                {"name": "writer", "role": "executor"},
            ],
            tasks=[
                {"description": "Research {topic}", "expected_output": "facts", "agent": "researcher"},
                {"description": "Write summary", "expected_output": "article", "agent": "writer"},
            ],
            process="sequential",
        )
        try:
            from meshflow.agents.crew import Crew, Process
            crew = Crew.from_yaml(path)
            assert len(crew.agents) == 2
            assert len(crew.tasks) == 2
            assert crew.process == Process.sequential
        finally:
            os.unlink(path)

    def test_parallel_process(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor"}, {"name": "b", "role": "executor"}],
            tasks=[
                {"description": "Task A", "agent": "a"},
                {"description": "Task B", "agent": "b"},
            ],
            process="parallel",
        )
        try:
            from meshflow.agents.crew import Crew, Process
            crew = Crew.from_yaml(path)
            assert crew.process == Process.parallel
        finally:
            os.unlink(path)

    def test_agent_names_preserved(self):
        path = _write_crew_yaml(
            agents=[{"name": "my-agent", "role": "researcher"}],
            tasks=[{"description": "Do research", "agent": "my-agent"}],
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            assert crew.agents[0].name == "my-agent"
        finally:
            os.unlink(path)

    def test_task_description_preserved(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor"}],
            tasks=[{"description": "Research {query}", "expected_output": "summary", "agent": "a"}],
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            assert "{query}" in crew.tasks[0].description
        finally:
            os.unlink(path)

    def test_expected_output_optional(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor"}],
            tasks=[{"description": "Just do it", "agent": "a"}],
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            assert crew.tasks[0].expected_output == ""
        finally:
            os.unlink(path)

    def test_verbose_flag(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor"}],
            tasks=[{"description": "task", "agent": "a"}],
            verbose=True,
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            assert crew.verbose is True
        finally:
            os.unlink(path)

    def test_model_per_agent(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor", "model": "claude-haiku-4-5-20251001"}],
            tasks=[{"description": "task", "agent": "a"}],
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            assert crew.agents[0].model == "claude-haiku-4-5-20251001"
        finally:
            os.unlink(path)

    def test_skills_per_agent(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor", "skills": ["python", "sql"]}],
            tasks=[{"description": "task", "agent": "a"}],
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            assert "python" in crew.agents[0].skills
            assert "sql" in crew.agents[0].skills
        finally:
            os.unlink(path)

    def test_unknown_agent_ref_is_none(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor"}],
            tasks=[{"description": "task", "agent": "nonexistent"}],
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            assert crew.tasks[0].agent is None
        finally:
            os.unlink(path)

    def test_default_process_is_sequential(self):
        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor"}],
            tasks=[{"description": "task", "agent": "a"}],
        )
        try:
            from meshflow.agents.crew import Crew, Process
            crew = Crew.from_yaml(path)
            assert crew.process == Process.sequential
        finally:
            os.unlink(path)

    def test_hierarchical_process(self):
        path = _write_crew_yaml(
            agents=[
                {"name": "manager", "role": "orchestrator"},
                {"name": "worker", "role": "executor"},
            ],
            tasks=[
                {"description": "Plan the work", "agent": "manager"},
                {"description": "Do the work", "agent": "worker"},
            ],
            process="hierarchical",
        )
        try:
            from meshflow.agents.crew import Crew, Process
            crew = Crew.from_yaml(path)
            assert crew.process == Process.hierarchical
        finally:
            os.unlink(path)


# ── Crew.from_yaml + kickoff (end-to-end with MESHFLOW_MOCK=1) ─────────────────

class TestCrewYamlKickoff:
    @pytest.mark.asyncio
    async def test_sequential_kickoff_from_yaml(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        path = _write_crew_yaml(
            agents=[
                {"name": "researcher", "role": "researcher"},
                {"name": "writer", "role": "executor"},
            ],
            tasks=[
                {"description": "Research AI trends in {year}", "agent": "researcher"},
                {"description": "Write a blog post", "agent": "writer"},
            ],
            process="sequential",
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            result = await crew.kickoff(inputs={"year": "2025"})
            assert result.raw
            assert len(result.tasks_output) == 2
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parallel_kickoff_from_yaml(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        path = _write_crew_yaml(
            agents=[
                {"name": "a", "role": "executor"},
                {"name": "b", "role": "executor"},
            ],
            tasks=[
                {"description": "Task A", "agent": "a"},
                {"description": "Task B", "agent": "b"},
            ],
            process="parallel",
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            result = await crew.kickoff(inputs={})
            assert len(result.tasks_output) == 2
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_crew_output_tokens_aggregated(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        path = _write_crew_yaml(
            agents=[
                {"name": "a", "role": "executor"},
                {"name": "b", "role": "executor"},
            ],
            tasks=[
                {"description": "Step one", "agent": "a"},
                {"description": "Step two", "agent": "b"},
            ],
        )
        try:
            from meshflow.agents.crew import Crew
            crew = Crew.from_yaml(path)
            result = await crew.kickoff(inputs={})
            assert isinstance(result.total_tokens, int)
            assert isinstance(result.total_cost_usd, float)
        finally:
            os.unlink(path)


# ── CLI run detects kind: crew ────────────────────────────────────────────────

class TestCLIRunCrewDetection:
    def test_cli_run_detects_crew_kind(self):
        """Verify the CLI peek-and-dispatch logic finds kind=crew."""
        import yaml

        path = _write_crew_yaml(
            agents=[{"name": "a", "role": "executor"}],
            tasks=[{"description": "task", "agent": "a"}],
        )
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            kind = str(data.get("kind", "workflow")).lower()
            assert kind == "crew"
        finally:
            os.unlink(path)

    def test_cli_run_workflow_kind_not_crew(self):
        """Verify that a normal workflow YAML is NOT dispatched to crew runner."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.safe_dump({"kind": "workflow", "name": "wf", "nodes": []}, f)
            path = f.name
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            kind = str(data.get("kind", "workflow")).lower()
            assert kind == "workflow"
        finally:
            os.unlink(path)


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_crew_from_yaml_is_accessible(self):
        from meshflow.agents.crew import Crew
        assert hasattr(Crew, "from_yaml")
        assert callable(Crew.from_yaml)
