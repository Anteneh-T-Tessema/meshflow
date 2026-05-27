"""Integration tests for the meshflow.swarm package.

Verifier tests run without torch (deterministic-only).
SwarmNode / SwarmTRM tests mock the engine so torch is not required in CI.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Import without torch ──────────────────────────────────────────────────────

from meshflow.swarm.verifiers import (
    VerificationResult,
    DeterministicVerifier,
    ERPAuditVerifier,
    BillableCaptureVerifier,
    CodeModernizationVerifier,
    DASCVerifier,
    PytestVerifier,
)
from meshflow.swarm.node import SwarmNode, swarm_verifier, register_swarm_domain
from meshflow.swarm import available_domains
from meshflow.core.node import NodeInput


# ════════════════════════════════════════════════════════════════════════════════
# Base verifier contract
# ════════════════════════════════════════════════════════════════════════════════

class TestVerificationResult:
    def test_valid_result(self):
        r = VerificationResult(is_valid=True, confidence=1.0, violations=[])
        assert r.is_valid is True
        assert r.confidence == 1.0
        assert r.violations == []

    def test_invalid_result_carries_violations(self):
        r = VerificationResult(is_valid=False, confidence=0.4,
                               violations=["fee missing", "GL code blank"])
        assert not r.is_valid
        assert len(r.violations) == 2

    def test_remediation_steps_optional(self):
        r = VerificationResult(is_valid=True, confidence=0.9, violations=[])
        assert r.remediation_steps is None

        r2 = VerificationResult(is_valid=False, confidence=0.3,
                                violations=["x"], remediation_steps=["fix x"])
        assert r2.remediation_steps == ["fix x"]


# ════════════════════════════════════════════════════════════════════════════════
# Built-in FLL verifiers
# ════════════════════════════════════════════════════════════════════════════════

class TestERPAuditVerifier:
    def _v(self): return ERPAuditVerifier()

    def test_valid_erp_record(self):
        # ERPAuditVerifier checks: debit == credit AND audit_tag present
        r = self._v().verify({"debit": 1000, "credit": 1000, "audit_tag": "AT-001"}, {})
        assert r.is_valid
        assert r.violations == []

    def test_unbalanced_debit_credit(self):
        r = self._v().verify({"debit": 1000, "credit": 900, "audit_tag": "AT-001"}, {})
        assert not r.is_valid
        assert any("Debit" in v or "Credit" in v for v in r.violations)

    def test_missing_audit_tag(self):
        r = self._v().verify({"debit": 500, "credit": 500}, {})
        assert not r.is_valid
        assert any("Audit-Proof" in v or "audit" in v.lower() for v in r.violations)

    def test_both_errors_reported(self):
        r = self._v().verify({"debit": 100, "credit": 200}, {})
        assert not r.is_valid
        assert len(r.violations) == 2


class TestBillableCaptureVerifier:
    def _v(self): return BillableCaptureVerifier()

    def test_valid_billable_under_cap(self):
        r = self._v().verify({"hourly_rate": 200}, {"max_rate": 250})
        assert r.is_valid

    def test_rate_over_cap(self):
        r = self._v().verify({"hourly_rate": 300}, {"max_rate": 250})
        assert not r.is_valid
        assert any("rate" in v.lower() or "cap" in v.lower() for v in r.violations)

    def test_default_cap_250(self):
        # Rate exactly at default cap should pass
        r = self._v().verify({"hourly_rate": 250}, {})
        assert r.is_valid

    def test_zero_rate_passes(self):
        r = self._v().verify({"hourly_rate": 0}, {})
        assert r.is_valid


class TestCodeModernizationVerifier:
    def _v(self): return CodeModernizationVerifier()

    def test_clean_code_valid(self):
        r = self._v().verify({"code": "def add(x: int, y: int) -> int:\n    return x + y"}, {})
        assert r.is_valid

    def test_any_type_violation(self):
        r = self._v().verify({"code": "def foo(x: any) -> None: pass"}, {})
        assert not r.is_valid
        assert any("any" in v.lower() for v in r.violations)

    def test_eval_violation(self):
        r = self._v().verify({"code": "result = eval(user_input)"}, {})
        assert not r.is_valid
        assert any("eval" in v.lower() for v in r.violations)

    def test_both_violations(self):
        r = self._v().verify({"code": "def f(x: any): return eval(x)"}, {})
        assert not r.is_valid
        assert len(r.violations) == 2


class TestDASCVerifier:
    def _v(self): return DASCVerifier()

    def test_always_passes(self):
        # DASCVerifier is a passthrough that always returns is_valid=True
        r = self._v().verify({"anything": "goes"}, {})
        assert r.is_valid
        assert r.violations == []
        assert r.confidence == 1.0


# ════════════════════════════════════════════════════════════════════════════════
# Industry verifiers (no torch)
# ════════════════════════════════════════════════════════════════════════════════

class TestIndustryVerifiers:
    def test_aml_verifier_valid(self):
        # Amount below 10000 threshold, no CTR needed, no sanctions match in context
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("aml")
        r = v.verify({"amount": 5000, "ctr_filed": False, "sar_filed": False,
                       "edd_completed": False, "country_code": "US"}, {})
        assert r.is_valid

    def test_aml_verifier_ctr_required(self):
        # Amount >= 10000 without CTR filing should fail
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("aml")
        r = v.verify({"amount": 15000, "ctr_filed": False, "sar_filed": False,
                       "edd_completed": False}, {"reporting_threshold": 10000})
        assert not r.is_valid
        assert any("CTR" in vio for vio in r.violations)

    def test_aml_verifier_sanctions_triggers_sar(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("aml")
        r = v.verify({"amount": 1000, "ctr_filed": False, "sar_filed": False,
                       "edd_completed": False},
                      {"sanctions_match": True})
        assert not r.is_valid
        assert any("SAR" in vio or "sanction" in vio.lower() for vio in r.violations)

    def test_hipaa_verifier_valid_treatment_purpose(self):
        # PHI for treatment purpose doesn't need authorization
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("hipaa")
        r = v.verify({
            "purpose": "treatment",
            "phi_fields_included": ["ssn"],
            "authorization_present": False,
            "tpo_exception": False,
            "audit_logged": True,
            "de_identified": False,
        }, {})
        assert r.is_valid

    def test_hipaa_verifier_phi_without_auth(self):
        # PHI disclosed for non-treatment purpose without authorization
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("hipaa")
        r = v.verify({
            "purpose": "marketing",
            "phi_fields_included": ["ssn", "dob"],
            "authorization_present": False,
            "tpo_exception": False,
            "audit_logged": True,
            "de_identified": False,
        }, {})
        assert not r.is_valid
        assert any("HIPAA" in vio for vio in r.violations)

    def test_hipaa_verifier_phi_not_audit_logged(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("hipaa")
        r = v.verify({
            "purpose": "treatment",
            "phi_fields_included": ["mrn"],
            "authorization_present": False,
            "tpo_exception": False,
            "audit_logged": False,  # ← missing audit log
            "de_identified": False,
        }, {})
        assert not r.is_valid
        assert any("audit" in vio.lower() for vio in r.violations)

    def test_sox_verifier_valid(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("sox")
        r = v.verify({
            "amount": 5000,
            "initiator_id": "alice",
            "approver_id": "bob",
            "recorder_id": "carol",
            "approver_authority_limit": 10000,
            "documentation_present": True,
            "reconciliation_days": 5,
            "max_reconciliation_days": 30,
            "is_override": False,
        }, {})
        assert r.is_valid

    def test_sox_segregation_violation(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("sox")
        r = v.verify({
            "initiator_id": "alice",
            "approver_id": "alice",   # same person — SOD violation
            "recorder_id": "carol",
            "documentation_present": True,
        }, {})
        assert not r.is_valid
        assert any("Segregation" in vio for vio in r.violations)

    def test_gdpr_verifier_valid(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("gdpr")
        r = v.verify({
            "lawful_basis": "contract",
            "purpose_specified": True,
            "data_fields_collected": ["email"],
            "purpose_required_fields": ["email"],
            "consent_freely_given": True,
            "consent_unambiguous": True,
        }, {})
        assert r.is_valid

    def test_gdpr_verifier_invalid_basis(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("gdpr")
        r = v.verify({
            "lawful_basis": "",  # empty = invalid
            "purpose_specified": True,
            "data_fields_collected": [],
            "purpose_required_fields": [],
        }, {})
        assert not r.is_valid
        assert any("lawful basis" in vio.lower() or "Art 6" in vio for vio in r.violations)

    def test_drug_interaction_verifier_valid(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("drug_interaction")
        r = v.verify({
            "medications": ["lisinopril"],
            "dose_mg": 10,
            "crcl": 90,
            "known_allergies": [],
        }, {})
        assert r.is_valid

    def test_pci_dss_verifier_valid(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("pci_dss")
        r = v.verify({
            "pan_encrypted": True,
            "cvv_stored": False,
            "audit_log_present": True,
            "mfa_enabled": True,
            "key_rotation_days": 180,
        }, {"max_key_rotation_days": 365})
        assert r.is_valid

    def test_pci_dss_cvv_stored(self):
        from meshflow.swarm.industries.registry import get_verifier
        v = get_verifier("pci_dss")
        r = v.verify({
            "pan_encrypted": True,
            "cvv_stored": True,  # ← prohibited
            "audit_log_present": True,
            "mfa_enabled": True,
            "key_rotation_days": 180,
        }, {})
        assert not r.is_valid
        assert any("CVV" in vio for vio in r.violations)

    @pytest.mark.parametrize("domain", [
        "covenant", "three_way_match", "insurance", "trade_settlement",
        "icd10", "prior_auth", "clinical_trial",
        "contract", "sanctions",
        "customs", "food_safety", "hazmat", "spc", "osha", "bom",
        "epa", "hos",
        "flsa", "aca", "eeoc", "owasp", "sla", "sbom", "map",
        "product_safety", "loyalty",
        "grant", "far", "sap_gov", "research", "organic", "food_label",
        "cre_dscr", "ppap", "music_royalty", "sports_cap",
    ])
    def test_domain_verifier_instantiates(self, domain: str):
        from meshflow.swarm.industries.registry import get_verifier, REGISTRY
        assert domain in REGISTRY
        v = get_verifier(domain)
        assert hasattr(v, "verify")


# ════════════════════════════════════════════════════════════════════════════════
# Reasoning verifiers (no torch)
# ════════════════════════════════════════════════════════════════════════════════

class TestReasoningVerifiers:
    def test_linear_system_valid(self):
        from meshflow.swarm.reasoning.verifiers import LinearSystemVerifier
        v = LinearSystemVerifier()
        r = v.verify({
            "a11": 2, "a12": 1, "a21": 1, "a22": 3,
            "b1": 5, "b2": 10,
            "x": 1.0, "y": 3.0,
        }, {"tolerance": 0.01})
        assert r.is_valid

    def test_linear_system_wrong_solution(self):
        from meshflow.swarm.reasoning.verifiers import LinearSystemVerifier
        v = LinearSystemVerifier()
        r = v.verify({
            "a11": 2, "a12": 1, "a21": 1, "a22": 3,
            "b1": 5, "b2": 10,
            "x": 99.0, "y": 99.0,
        }, {"tolerance": 0.01})
        assert not r.is_valid

    def test_multi_calc_valid(self):
        from meshflow.swarm.reasoning.verifiers import MultiCalcVerifier
        v = MultiCalcVerifier()
        r = v.verify({
            "a": 3.0, "b": 4.0, "c": 2.0,
            "sum_abc": 9.0,
            "product_ab": 12.0,
            "ratio_ac": 1.5,
            "max_abc": 4.0,
        }, {})
        assert r.is_valid

    def test_logic_rules_valid(self):
        from meshflow.swarm.reasoning.verifiers import LogicRulesVerifier
        v = LogicRulesVerifier()
        r = v.verify({
            "facts": {"rain": True, "sunny": False},
            "rules": [{"if": "rain", "then": "carry_umbrella"}, {"if": "sunny", "then": "wear_sunscreen"}],
            "derived": {"carry_umbrella": True, "wear_sunscreen": False},
        }, {})
        assert r.is_valid

    def test_data_quality_valid(self):
        from meshflow.swarm.reasoning.verifiers import DataQualityVerifier
        v = DataQualityVerifier()
        r = v.verify({
            "records": [
                {"id": 1, "email": "alice@example.com", "age": 30, "status": "active", "phone": "5551234567"},
                {"id": 2, "email": "bob@test.org", "age": 45, "status": "inactive", "phone": "5559876543"},
            ]
        }, {})
        assert r.is_valid

    def test_data_quality_invalid_email(self):
        from meshflow.swarm.reasoning.verifiers import DataQualityVerifier
        v = DataQualityVerifier()
        r = v.verify({
            "records": [{"id": 1, "email": "not-an-email", "age": 30, "status": "active", "phone": "5551234567"}]
        }, {})
        assert not r.is_valid
        assert any("email" in vio.lower() for vio in r.violations)

    @pytest.mark.parametrize("domain", [
        "linear_system", "multi_calc", "logic_rules", "schedule_plan",
        "data_quality", "causal_chain", "constraint_csp", "budget_alloc",
    ])
    def test_reasoning_domain_instantiates(self, domain: str):
        from meshflow.swarm.reasoning.registry import get_verifier, REGISTRY
        assert domain in REGISTRY
        v = get_verifier(domain)
        assert hasattr(v, "verify")


# ════════════════════════════════════════════════════════════════════════════════
# Repair functions
# ════════════════════════════════════════════════════════════════════════════════

class TestRepairFunctions:
    def test_reasoning_repair_linear_system(self):
        from meshflow.swarm.reasoning.repair import repair
        output = {
            "a11": 2, "a12": 1, "a21": 1, "a22": 3,
            "b1": 5, "b2": 10,
            "x": 99.0, "y": 99.0,
        }
        fixed = repair("linear_system", output, "x_solver", {"tolerance": 0.01}, step=0)
        assert abs(fixed["x"] - 1.0) < 0.01

    def test_reasoning_repair_multi_calc(self):
        from meshflow.swarm.reasoning.repair import repair
        output = {"a": 3.0, "b": 4.0, "c": 2.0,
                  "sum_abc": 0.0, "product_ab": 0.0, "ratio_ac": 0.0, "max_abc": 0.0}
        fixed = repair("multi_calc", output, "sum_checker", {}, step=0)
        assert fixed["sum_abc"] == 9.0

    def test_industry_repair_aml(self):
        # repair_aml: aml_analyst at step 0 should auto-file CTR for amounts >= threshold
        from meshflow.swarm.industries.repair import repair, has_repair
        assert has_repair("aml")
        output = {"amount": 15000, "ctr_filed": False, "sar_filed": False}
        fixed = repair("aml", output, "aml_analyst", {"reporting_threshold": 10000}, step=0)
        assert fixed["ctr_filed"] is True

    def test_unknown_domain_passthrough(self):
        from meshflow.swarm.reasoning.repair import repair
        output = {"key": "value"}
        result = repair("nonexistent_domain", output, "some_role", {}, step=0)
        assert result == output


# ════════════════════════════════════════════════════════════════════════════════
# available_domains()
# ════════════════════════════════════════════════════════════════════════════════

class TestAvailableDomains:
    def test_returns_list(self):
        domains = available_domains()
        assert isinstance(domains, list)
        assert len(domains) >= 50  # 5 builtin + 40+ industry + 8 reasoning

    def test_builtin_domains_present(self):
        domains = available_domains()
        for d in ["erp", "billable", "modernize", "dasc", "qa"]:
            assert d in domains, f"'{d}' missing from available_domains()"

    def test_industry_domains_present(self):
        domains = available_domains()
        for d in ["aml", "hipaa", "sox", "gdpr", "pci_dss", "drug_interaction"]:
            assert d in domains, f"'{d}' missing from available_domains()"

    def test_reasoning_domains_present(self):
        domains = available_domains()
        for d in ["linear_system", "multi_calc", "logic_rules", "data_quality"]:
            assert d in domains, f"'{d}' missing from available_domains()"


# ════════════════════════════════════════════════════════════════════════════════
# swarm_verifier hook
# ════════════════════════════════════════════════════════════════════════════════

class TestSwarmVerifierHook:
    def test_erp_hook_valid(self):
        hook = swarm_verifier("erp")
        result = hook({
            "transaction_id": "T-001",
            "amount": 500,
            "gl_code": "6100",
            "approval_chain": ["mgr@co.com"],
        }, {})
        assert isinstance(result, VerificationResult)

    def test_aml_hook_instantiates(self):
        hook = swarm_verifier("aml")
        assert callable(hook)
        assert hook.__name__ == "swarm_verifier_aml"

    def test_linear_system_hook_valid(self):
        hook = swarm_verifier("linear_system")
        result = hook({
            "a11": 1, "a12": 0, "a21": 0, "a22": 1,
            "b1": 3, "b2": 7,
            "x": 3.0, "y": 7.0,
        }, {})
        assert result.is_valid

    def test_unknown_domain_raises(self):
        with pytest.raises(KeyError, match="Unknown verifier domain"):
            swarm_verifier("totally_fake_domain_xyz")


# ════════════════════════════════════════════════════════════════════════════════
# SwarmNode — mocked SwarmTRM (torch not required)
# ════════════════════════════════════════════════════════════════════════════════

def _make_fake_result(domain: str = "erp"):
    """Build a minimal fake SwarmInferenceResult."""
    from types import SimpleNamespace
    trace_step = SimpleNamespace(step=0, n_agents=3, consensus_conf=0.95,
                                 verified=True, topology="adaptive")
    accounting = SimpleNamespace(prompt_tokens=10, completion_tokens=5, wall_ms=42.0, agent_steps=3)
    return SimpleNamespace(
        answer={"status": "ok"},
        confidence=0.95,
        verified=True,
        low_confidence=False,
        violations=[],
        remediation_steps=None,
        steps=1,
        trace=[trace_step],
        accounting=accounting,
        recommendation="Approved",
    )


class TestSwarmNode:
    def test_create_returns_meshnode(self):
        from meshflow.core.node import MeshNode
        node = SwarmNode.create("test_node", verifier_type="erp")
        assert isinstance(node, MeshNode)
        assert node.id == "test_node"
        assert "swarm_inference" in node.capabilities

    def test_domain_in_capabilities(self):
        node = SwarmNode.create("my_node", verifier_type="hipaa")
        assert "domain_hipaa" in node.capabilities

    def test_metadata_has_verifier_type(self):
        node = SwarmNode.create("n", verifier_type="aml")
        assert node.metadata["verifier_type"] == "aml"
        assert node.metadata["swarm"] is True

    @pytest.mark.asyncio
    async def test_run_calls_swarm_engine(self):
        fake_result = _make_fake_result("erp")

        with patch("meshflow.swarm.node._load_engine") as mock_load:
            MockSwarmTRM = MagicMock()
            MockSwarmTRM.return_value.run.return_value = fake_result
            mock_load.return_value = (MockSwarmTRM, MagicMock(), MagicMock())

            node = SwarmNode.create("erp_node", verifier_type="erp")
            inp = NodeInput(task='{"transaction_id": "T-001", "amount": 500}')
            out = await node.run(inp)

        assert out.confidence == 0.95
        assert out.structured["verified"] is True
        assert out.structured["violations"] == []

    @pytest.mark.asyncio
    async def test_run_includes_trace_in_metadata(self):
        fake_result = _make_fake_result()

        with patch("meshflow.swarm.node._load_engine") as mock_load:
            MockSwarmTRM = MagicMock()
            MockSwarmTRM.return_value.run.return_value = fake_result
            mock_load.return_value = (MockSwarmTRM, MagicMock(), MagicMock())

            node = SwarmNode.create("erp_node", verifier_type="erp", record_trace=True)
            out = await node.run(NodeInput(task="test task"))

        assert "swarm_trace" in out.metadata
        assert out.metadata["swarm_trace"][0]["verified"] is True

    @pytest.mark.asyncio
    async def test_run_no_trace_when_disabled(self):
        fake_result = _make_fake_result()

        with patch("meshflow.swarm.node._load_engine") as mock_load:
            MockSwarmTRM = MagicMock()
            MockSwarmTRM.return_value.run.return_value = fake_result
            mock_load.return_value = (MockSwarmTRM, MagicMock(), MagicMock())

            node = SwarmNode.create("erp_node", verifier_type="erp", record_trace=False)
            out = await node.run(NodeInput(task="test task"))

        assert "swarm_trace" not in out.metadata

    @pytest.mark.asyncio
    async def test_context_merged_with_input(self):
        fake_result = _make_fake_result()
        captured: dict = {}

        def fake_run(task, verifier_type, context, config):
            captured["context"] = context
            return fake_result

        with patch("meshflow.swarm.node._load_engine") as mock_load:
            MockSwarmTRM = MagicMock()
            MockSwarmTRM.return_value.run.side_effect = fake_run
            mock_load.return_value = (MockSwarmTRM, MagicMock(), MagicMock())

            node = SwarmNode.create(
                "n", verifier_type="erp",
                context={"static_key": "static_val"},
            )
            out = await node.run(NodeInput(task="task", context={"dynamic_key": "dyn"}))

        assert captured["context"]["static_key"] == "static_val"
        assert captured["context"]["dynamic_key"] == "dyn"


# ════════════════════════════════════════════════════════════════════════════════
# register_swarm_domain (MCP integration)
# ════════════════════════════════════════════════════════════════════════════════

class TestRegisterSwarmDomain:
    def _make_mock_server(self):
        srv = MagicMock()
        srv._tools = {}
        return srv

    def test_registers_tool_on_server(self):
        srv = self._make_mock_server()
        register_swarm_domain(srv, "aml")
        assert "swarm_aml" in srv._tools

    def test_custom_tool_name(self):
        srv = self._make_mock_server()
        register_swarm_domain(srv, "hipaa", tool_name="phi_checker")
        assert "phi_checker" in srv._tools

    def test_tool_entry_has_fn(self):
        srv = self._make_mock_server()
        register_swarm_domain(srv, "sox")
        entry = srv._tools["swarm_sox"]
        assert callable(entry.fn)

    def test_tool_description_default(self):
        srv = self._make_mock_server()
        register_swarm_domain(srv, "gdpr")
        entry = srv._tools["swarm_gdpr"]
        assert "gdpr" in entry.description.lower() or "SwarmTRM" in entry.description

    def test_custom_description(self):
        srv = self._make_mock_server()
        register_swarm_domain(srv, "aml", description="Custom AML checker")
        entry = srv._tools["swarm_aml"]
        assert entry.description == "Custom AML checker"

    @pytest.mark.asyncio
    async def test_tool_fn_calls_swarm_node(self):
        fake_result = _make_fake_result("aml")
        srv = self._make_mock_server()
        register_swarm_domain(srv, "aml")
        entry = srv._tools["swarm_aml"]

        with patch("meshflow.swarm.node._load_engine") as mock_load:
            MockSwarmTRM = MagicMock()
            MockSwarmTRM.return_value.run.return_value = fake_result
            mock_load.return_value = (MockSwarmTRM, MagicMock(), MagicMock())

            raw = await entry.fn({"task": {"transaction_id": "T-001", "amount": 1000}})

        result = json.loads(raw)
        assert result["verified"] is True
        assert result["domain"] == "aml"


# ════════════════════════════════════════════════════════════════════════════════
# Top-level meshflow imports
# ════════════════════════════════════════════════════════════════════════════════

class TestTopLevelImports:
    def test_swarm_exports_importable(self):
        import meshflow
        assert hasattr(meshflow, "SwarmNode")
        assert hasattr(meshflow, "swarm_verifier")
        assert hasattr(meshflow, "register_swarm_domain")
        assert hasattr(meshflow, "swarm_available_domains")
        assert hasattr(meshflow, "VerificationResult")
        assert hasattr(meshflow, "DeterministicVerifier")

    def test_version_bumped(self):
        import meshflow
        assert meshflow.__version__ == "0.65.0"

    def test_swarm_available_domains_from_top(self):
        import meshflow
        domains = meshflow.swarm_available_domains()
        assert len(domains) >= 50
