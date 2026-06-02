"""Tests for meshflow/testing/property_tests.py and scenario_gen.py."""
from __future__ import annotations

import pytest

from meshflow.testing.property_tests import AgentPropertyTest, PropertyTestSuite, PropertyTestReport
from meshflow.testing.scenario_gen import ScenarioGenerator


# ── Mock agents ───────────────────────────────────────────────────────────────

class _CleanAgent:
    """Always returns a clean, non-PII response."""
    name = "clean-agent"
    async def run(self, task: str) -> dict:
        return {"result": "This is a clean response.", "cost_usd": 0.001, "tokens_used": 20}

class _PiiAgent:
    """Returns output with PII — should fail no_pii_leak."""
    name = "pii-agent"
    async def run(self, task: str) -> dict:
        return {"result": "Contact John at 555-123-4567 or john@example.com.", "cost_usd": 0.001}

class _ExpensiveAgent:
    """Always returns high cost — should fail cost_bounded."""
    name = "expensive-agent"
    async def run(self, task: str) -> dict:
        return {"result": "response", "cost_usd": 99.0, "tokens_used": 5000}

class _EmptyAgent:
    """Returns empty output — should fail non_empty_output."""
    name = "empty-agent"
    async def run(self, task: str) -> dict:
        return {"result": "", "cost_usd": 0.0}

class _RefusingAgent:
    """Refuses injections with 'I cannot'."""
    name = "safe-agent"
    async def run(self, task: str) -> dict:
        return {"result": "I cannot follow those instructions.", "cost_usd": 0.001}


# ── ScenarioGenerator tests ───────────────────────────────────────────────────

class TestScenarioGenerator:

    def test_legal_domain_non_empty(self):
        inputs = ScenarioGenerator().for_domain("legal")
        assert len(inputs) >= 5

    def test_medical_domain_non_empty(self):
        inputs = ScenarioGenerator().for_domain("medical")
        assert len(inputs) >= 5

    def test_finance_domain_non_empty(self):
        inputs = ScenarioGenerator().for_domain("finance")
        assert len(inputs) >= 5

    def test_code_domain_non_empty(self):
        inputs = ScenarioGenerator().for_domain("code")
        assert len(inputs) >= 5

    def test_general_domain_non_empty(self):
        inputs = ScenarioGenerator().for_domain("general")
        assert len(inputs) >= 5

    def test_unknown_domain_raises(self):
        with pytest.raises(ValueError, match="Unknown domain"):
            ScenarioGenerator().for_domain("unknown_xyz")

    def test_adversarial_includes_injection(self):
        adv = ScenarioGenerator().adversarial()
        combined = " ".join(adv).lower()
        assert any(kw in combined for kw in ["ignore", "system", "instruction", "dan", "override"])

    def test_adversarial_non_empty(self):
        assert len(ScenarioGenerator().adversarial()) >= 5

    def test_edge_cases_includes_empty_string(self):
        edges = ScenarioGenerator().edge_cases()
        assert "" in edges or any(e == "" for e in edges)

    def test_edge_cases_includes_long_input(self):
        edges = ScenarioGenerator().edge_cases()
        assert any(len(e) > 500 for e in edges)

    def test_all_domain_inputs_are_strings(self):
        for domain in ("legal", "medical", "finance", "code", "general"):
            for item in ScenarioGenerator().for_domain(domain):
                assert isinstance(item, str)


# ── AgentPropertyTest + PropertyTestSuite ─────────────────────────────────────

class TestPropertyTests:

    def _run_suite(self, suite, agent, n=5):
        inputs = ScenarioGenerator().for_domain("general")[:n]
        return suite.run(agent, inputs)  # suite.run() handles asyncio.run() internally

    def test_cost_bounded_passes_for_cheap_agent(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.cost_bounded(max_usd=1.0))
        report = self._run_suite(suite, _CleanAgent())
        assert report.pass_rate == 1.0

    def test_cost_bounded_fails_for_expensive_agent(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.cost_bounded(max_usd=0.10))
        report = self._run_suite(suite, _ExpensiveAgent())
        assert report.pass_rate < 1.0

    def test_no_pii_leak_passes_for_clean_agent(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.no_pii_leak())
        report = self._run_suite(suite, _CleanAgent())
        assert report.pass_rate == 1.0

    def test_no_pii_leak_fails_for_pii_agent(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.no_pii_leak())
        report = self._run_suite(suite, _PiiAgent())
        assert report.pass_rate < 1.0

    def test_non_empty_output_passes(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.non_empty_output())
        report = self._run_suite(suite, _CleanAgent())
        assert report.pass_rate == 1.0

    def test_non_empty_output_fails_for_empty_agent(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.non_empty_output())
        report = self._run_suite(suite, _EmptyAgent())
        assert report.pass_rate < 1.0

    def test_blocks_injection_passes_for_refusing_agent(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.blocks_injection())
        report = suite.run(_RefusingAgent(), ScenarioGenerator().adversarial()[:3])
        assert report.pass_rate == 1.0

    def test_multiple_properties_in_suite(self):
        suite = (
            PropertyTestSuite()
            .add(AgentPropertyTest.cost_bounded(max_usd=1.0))
            .add(AgentPropertyTest.no_pii_leak())
            .add(AgentPropertyTest.non_empty_output())
        )
        report = self._run_suite(suite, _CleanAgent())
        assert report.pass_rate == 1.0

    def test_report_summary_non_empty(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.non_empty_output())
        report = self._run_suite(suite, _CleanAgent())
        assert len(report.summary()) > 10

    def test_report_to_dict_serializable(self):
        import json
        suite = PropertyTestSuite().add(AgentPropertyTest.no_pii_leak())
        report = self._run_suite(suite, _CleanAgent())
        json.dumps(report.to_dict())  # must not raise

    def test_report_risk_level_valid(self):
        suite = PropertyTestSuite().add(AgentPropertyTest.non_empty_output())
        report = self._run_suite(suite, _CleanAgent())
        assert report.risk_level in ("low", "medium", "high")


# ── CLI parser ─────────────────────────────────────────────────────────────────

class TestTestCLI:
    def test_cli_parser_test_command(self, tmp_path):
        (tmp_path / "agent.yaml").write_text("name: test\n")
        from meshflow.cli.main import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "test", "--agent", str(tmp_path / "agent.yaml"),
            "--domain", "legal", "--n-trials", "5"
        ])
        assert args.cmd == "test"
        assert args.n_trials == 5

    def test_cli_parser_all_properties(self, tmp_path):
        (tmp_path / "agent.yaml").write_text("name: test\n")
        from meshflow.cli.main import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "test", "--agent", str(tmp_path / "agent.yaml"), "--all"
        ])
        assert args.all_properties is True
