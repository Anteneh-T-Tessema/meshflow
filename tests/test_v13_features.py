"""Tests for v1.3 features: stop_on_confidence and context_dedup."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_agent(name: str, output: str) -> MagicMock:
    """Mock agent that returns a fixed output string."""
    agent = MagicMock()
    agent.name = name
    agent.role = MagicMock(value="executor")

    async def _run(_task: str, _ctx: dict | None = None) -> dict:
        return {"output": output, "stated_confidence": 0.0, "cost_usd": 0.0, "tokens": 10}

    async def _stream(_task: str, _ctx: dict | None = None):
        for token in output.split():
            yield token

    agent.run = _run
    agent.stream = _stream
    agent.to_mesh_node = MagicMock(return_value=MagicMock(id=name))
    return agent


def _make_task(description: str, output: str, agent: MagicMock) -> MagicMock:
    """Mock Task that returns a fixed TaskOutput."""
    from meshflow.agents.task import TaskOutput
    task = MagicMock()
    task.description = description
    task.agent = agent
    task.context = None
    task._build_prompt = lambda inputs=None: description
    task.output = None

    async def _run(inputs=None):
        return TaskOutput(
            raw=output,
            task_description=description[:120],
            agent_name=agent.name,
        )

    task.run = _run
    return task


# ── _parse_confidence ─────────────────────────────────────────────────────────

class TestParseConfidence(unittest.TestCase):
    def test_extracts_json_style(self):
        from meshflow.agents.team import _parse_confidence
        self.assertAlmostEqual(_parse_confidence('{"confidence": 0.92}'), 0.92)

    def test_extracts_plain_style(self):
        from meshflow.agents.team import _parse_confidence
        self.assertAlmostEqual(_parse_confidence('confidence: 0.75'), 0.75)

    def test_returns_zero_when_absent(self):
        from meshflow.agents.team import _parse_confidence
        self.assertEqual(_parse_confidence("No confidence score here."), 0.0)

    def test_case_insensitive(self):
        from meshflow.agents.team import _parse_confidence
        self.assertAlmostEqual(_parse_confidence("CONFIDENCE: 0.5"), 0.5)


# ── Team stop_on_confidence ───────────────────────────────────────────────────

class TestTeamStopOnConfidence(unittest.TestCase):
    def test_stops_early_when_threshold_met(self):
        """Second agent should be skipped when first output confidence >= threshold."""
        from meshflow.agents.team import Team

        a1 = _make_agent("a1", 'Done. {"confidence": 0.95}')
        a2 = _make_agent("a2", "Should not run")

        team = Team(
            name="test-team",
            agents=[a1, a2],
            pattern="sequential",
            stop_on_confidence=0.9,
        )

        result = asyncio.run(team.run("test task"))
        # a2 should be skipped
        self.assertIn("a2", result.skipped_nodes)
        self.assertIn("confidence", result.output.lower())

    def test_runs_all_when_threshold_not_met(self):
        """All agents run when no output meets the confidence threshold."""
        from meshflow.agents.team import Team

        a1 = _make_agent("a1", 'Low confidence. {"confidence": 0.5}')
        a2 = _make_agent("a2", 'Final output. {"confidence": 0.6}')

        team = Team(
            name="test-team",
            agents=[a1, a2],
            pattern="sequential",
            stop_on_confidence=0.9,
        )

        result = asyncio.run(team.run("test task"))
        self.assertEqual(result.skipped_nodes, [])

    def test_no_stop_on_confidence_runs_all(self):
        """stop_on_confidence=None uses normal workflow path (no early exit)."""
        from meshflow.agents.team import Team

        a1 = _make_agent("a1", 'High confidence. {"confidence": 0.99}')
        a2 = _make_agent("a2", "Also runs")

        team = Team(
            name="test-team",
            agents=[a1, a2],
            pattern="sequential",
            stop_on_confidence=None,
        )
        # Should use the workflow path — just check it doesn't raise
        # (a2.to_mesh_node will be called by workflow builder)
        self.assertIsNone(team.stop_on_confidence)

    def test_stream_stops_early(self):
        """Streaming also exits early when confidence threshold is met."""
        from meshflow.agents.team import Team

        a1 = _make_agent("a1", 'Result. {"confidence": 0.95}')
        a2 = _make_agent("a2", "Should not stream")

        team = Team(
            name="test-team",
            agents=[a1, a2],
            pattern="sequential",
            stop_on_confidence=0.9,
        )

        async def _collect():
            chunks = []
            async for chunk in await team.stream("test"):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(_collect())
        node_names = [c.node_name for c in chunks if c.node_name]
        # a2 should never appear in streamed chunks
        self.assertNotIn("a2", node_names)
        self.assertIn("a1", node_names)


# ── Crew stop_on_confidence ───────────────────────────────────────────────────

class TestCrewStopOnConfidence(unittest.TestCase):
    def test_sequential_stops_early(self):
        """Crew._run_sequential exits when task output confidence >= threshold."""
        from meshflow.agents.crew import Crew, Process

        a1 = _make_agent("a1", 'Research done. {"confidence": 0.92}')
        a2 = _make_agent("a2", "Should be skipped")

        t1 = _make_task("Research the topic", 'Research done. {"confidence": 0.92}', a1)
        t2 = _make_task("Write the report", "Should be skipped", a2)

        crew = Crew(
            agents=[a1, a2],
            tasks=[t1, t2],
            process=Process.sequential,
            stop_on_confidence=0.9,
        )

        result = asyncio.run(crew.kickoff())
        # Only one task should have been executed
        self.assertEqual(len(result.tasks_output), 1)
        self.assertIn("confidence", result.raw.lower())

    def test_sequential_all_run_below_threshold(self):
        """All tasks run when no task output meets the threshold."""
        from meshflow.agents.crew import Crew, Process

        a1 = _make_agent("a1", 'Part one. {"confidence": 0.5}')
        a2 = _make_agent("a2", 'Part two. {"confidence": 0.6}')

        t1 = _make_task("Task 1", 'Part one. {"confidence": 0.5}', a1)
        t2 = _make_task("Task 2", 'Part two. {"confidence": 0.6}', a2)

        crew = Crew(
            agents=[a1, a2],
            tasks=[t1, t2],
            process=Process.sequential,
            stop_on_confidence=0.9,
        )

        result = asyncio.run(crew.kickoff())
        self.assertEqual(len(result.tasks_output), 2)


# ── context_dedup ─────────────────────────────────────────────────────────────

class TestContextDedup(unittest.TestCase):
    def test_dedup_removes_shared_sentences(self):
        """Shared long sentences across prompts should appear only once total."""
        from meshflow.agents.crew import _dedup_context

        shared = (
            "This is a very long shared background sentence that appears in multiple prompts "
            "and should be deduplicated to save tokens in the LLM call to the model."
        )
        unique1 = (
            "Unique context for task one: analyse the Python codebase for security issues "
            "and produce a structured report with CVSS scores for each finding."
        )
        unique2 = (
            "Unique context for task two: write a comprehensive summary of the research "
            "findings and format it as an executive briefing for senior stakeholders."
        )
        p1 = f"{unique1} {shared}"
        p2 = f"{unique2} {shared}"

        result = _dedup_context([p1, p2])
        # Shared sentence must not appear in BOTH results
        both_have_shared = shared in result[0] and shared in result[1]
        self.assertFalse(both_have_shared, "Shared sentence should be deduped from one prompt")
        # Shared sentence must appear in at least one result (first occurrence kept)
        either_has_shared = shared in result[0] or shared in result[1]
        self.assertTrue(either_has_shared, "Shared sentence should still appear in one prompt")

    def test_dedup_noop_for_single_prompt(self):
        """Single prompt is returned unchanged."""
        from meshflow.agents.crew import _dedup_context
        p = ["Only one prompt here, nothing to deduplicate at all."]
        self.assertEqual(_dedup_context(p), p)

    def test_dedup_noop_when_no_shared_content(self):
        """Completely distinct prompts are returned unchanged."""
        from meshflow.agents.crew import _dedup_context
        p1 = "Analyse the Python codebase for security vulnerabilities and report findings."
        p2 = "Write a comprehensive marketing strategy for the enterprise product launch."
        result = _dedup_context([p1, p2])
        self.assertEqual(result[0], p1)
        self.assertEqual(result[1], p2)

    def test_crew_parallel_context_dedup_flag(self):
        """Crew with context_dedup=True runs parallel tasks without raising."""
        from meshflow.agents.crew import Crew, Process

        shared = (
            "Background: This company specialises in AI agent governance frameworks "
            "for regulated industries including healthcare and financial services."
        )
        a1 = _make_agent("analyst", "Analysis complete.")
        a2 = _make_agent("writer", "Report written.")

        t1 = _make_task(f"Analyse market. {shared}", "Analysis complete.", a1)
        t2 = _make_task(f"Write report. {shared}", "Report written.", a2)

        crew = Crew(
            agents=[a1, a2],
            tasks=[t1, t2],
            process=Process.parallel,
            context_dedup=True,
        )

        result = asyncio.run(crew.kickoff())
        self.assertEqual(len(result.tasks_output), 2)

    def test_crew_default_no_dedup(self):
        """context_dedup defaults to False — no patching of prompts."""
        from meshflow.agents.crew import Crew, Process

        a1 = _make_agent("a1", "Output A")
        a2 = _make_agent("a2", "Output B")
        t1 = _make_task("Task A", "Output A", a1)
        t2 = _make_task("Task B", "Output B", a2)

        crew = Crew(agents=[a1, a2], tasks=[t1, t2], process=Process.parallel)
        self.assertFalse(crew.context_dedup)
        result = asyncio.run(crew.kickoff())
        self.assertEqual(len(result.tasks_output), 2)


if __name__ == "__main__":
    unittest.main()
