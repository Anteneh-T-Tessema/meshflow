from __future__ import annotations

import json

from meshflow import PolicyMode, core_contract_schemas, policy_for_mode
from meshflow.core.schemas import RiskTier


def test_policy_modes_are_progressive():
    dev = policy_for_mode(PolicyMode.DEV)
    standard = policy_for_mode(PolicyMode.STANDARD)
    regulated = policy_for_mode(PolicyMode.REGULATED)
    legal = policy_for_mode(PolicyMode.LEGAL_CRITICAL)

    assert dev.enable_guardian is False
    assert standard.enable_guardian is True
    assert regulated.immutable_audit is True
    assert regulated.require_human_review is True
    assert legal.require_citations is True
    assert legal.require_evidence is True
    assert legal.human_in_loop.enabled is True
    assert legal.human_in_loop.tier_threshold == RiskTier.EXTERNAL_IO


def test_policy_for_mode_allows_overrides():
    policy = policy_for_mode("legal-critical", budget_usd=9.0, max_steps=7)

    assert policy.mode == PolicyMode.LEGAL_CRITICAL
    assert policy.budget_usd == 9.0
    assert policy.max_steps == 7


def test_core_contract_schemas_are_json_serializable():
    schemas = core_contract_schemas()

    assert {"NodeInput", "NodeOutput", "MeshNode", "Policy", "RuntimeOutcome"} <= set(schemas)
    assert schemas["NodeOutput"]["properties"]["confidence"]["maximum"] == 1.0
    json.dumps(schemas)


def test_core_contract_schemas_return_defensive_copies():
    first = core_contract_schemas()
    first["NodeInput"]["title"] = "mutated"

    second = core_contract_schemas()

    assert second["NodeInput"]["title"] == "NodeInput"
