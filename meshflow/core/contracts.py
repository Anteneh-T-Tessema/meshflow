"""Public contract schemas for MeshFlow adapters and governance boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_SCHEMA_BASE = "https://json-schema.org/draft/2020-12/schema"


def node_input_schema() -> dict[str, Any]:
    return {
        "$schema": _SCHEMA_BASE,
        "$id": "https://meshflow.dev/schemas/node-input.json",
        "title": "NodeInput",
        "type": "object",
        "additionalProperties": False,
        "required": ["task", "context", "metadata"],
        "properties": {
            "task": {"type": "string"},
            "context": {"type": "object"},
            "metadata": {"type": "object"},
        },
    }


def node_output_schema() -> dict[str, Any]:
    return {
        "$schema": _SCHEMA_BASE,
        "$id": "https://meshflow.dev/schemas/node-output.json",
        "title": "NodeOutput",
        "type": "object",
        "additionalProperties": False,
        "required": ["content", "structured", "tokens_used", "model", "confidence", "metadata"],
        "properties": {
            "content": {"type": "string"},
            "structured": {"type": "object"},
            "tokens_used": {"type": "integer", "minimum": 0},
            "model": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "metadata": {"type": "object"},
        },
    }


def mesh_node_schema() -> dict[str, Any]:
    return {
        "$schema": _SCHEMA_BASE,
        "$id": "https://meshflow.dev/schemas/mesh-node.json",
        "title": "MeshNode",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id",
            "kind",
            "risk_profile",
            "capabilities",
            "input_schema",
            "output_schema",
            "metadata",
        ],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "kind": {
                "type": "string",
                "enum": [
                    "native",
                    "langgraph",
                    "crewai",
                    "autogen",
                    "mcp",
                    "human",
                    "http",
                    "python",
                ],
            },
            "risk_profile": {"type": "integer", "minimum": 1, "maximum": 4},
            "capabilities": {"type": "array", "items": {"type": "string"}},
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "metadata": {"type": "object"},
        },
    }


def policy_schema() -> dict[str, Any]:
    return {
        "$schema": _SCHEMA_BASE,
        "$id": "https://meshflow.dev/schemas/policy.json",
        "title": "Policy",
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["dev", "standard", "regulated", "legal-critical"],
            },
            "budget_usd": {"type": "number", "minimum": 0},
            "budget_tokens": {"type": "integer", "minimum": 0},
            "timeout_s": {"type": "number", "minimum": 0},
            "max_steps": {"type": "integer", "minimum": 1},
            "deterministic_gate": {"type": "boolean"},
            "validate_handoffs": {"type": "boolean"},
            "enable_guardian": {"type": "boolean"},
            "enable_collusion_audit": {"type": "boolean"},
            "enable_uncertainty": {"type": "boolean"},
            "enable_environmental": {"type": "boolean"},
            "enable_cross_run_learning": {"type": "boolean"},
            "require_citations": {"type": "boolean"},
            "require_evidence": {"type": "boolean"},
            "require_human_review": {"type": "boolean"},
            "immutable_audit": {"type": "boolean"},
        },
    }


def runtime_outcome_schema() -> dict[str, Any]:
    return {
        "$schema": _SCHEMA_BASE,
        "$id": "https://meshflow.dev/schemas/runtime-outcome.json",
        "title": "RuntimeOutcome",
        "type": "object",
        "additionalProperties": False,
        "required": ["ok", "node_id", "node_kind", "output", "blocked_by", "paused_for_human"],
        "properties": {
            "ok": {"type": "boolean"},
            "node_id": {"type": "string"},
            "node_kind": {"type": "string"},
            "output": node_output_schema(),
            "blocked_by": {"type": "string"},
            "paused_for_human": {"type": "boolean"},
            "human_context": {"type": "object"},
        },
    }


def core_contract_schemas() -> dict[str, dict[str, Any]]:
    """Return defensive copies of all public JSON Schemas."""
    schemas = {
        "NodeInput": node_input_schema(),
        "NodeOutput": node_output_schema(),
        "MeshNode": mesh_node_schema(),
        "Policy": policy_schema(),
        "RuntimeOutcome": runtime_outcome_schema(),
    }
    return deepcopy(schemas)
