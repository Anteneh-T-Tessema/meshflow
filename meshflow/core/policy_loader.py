"""Policy-as-code — load MeshFlow policies from YAML files.

Allows compliance officers and ops teams to define governance rules in
version-controlled YAML instead of Python code.

Format::

    # meshflow.policy.yaml
    mode: legal-critical
    budget_usd: 5.0
    budget_tokens: 1_000_000
    timeout_s: 600
    max_steps: 100

    compliance:
      frameworks: [hipaa, sox]
      block_on_violation: true
      rules:
        hipaa_minimum_necessary:
          max_input_chars: 30000
        sox_dual_control:
          max_consecutive: 3
        nerc_access_control:
          approved_nodes: [agent_a, agent_b]

    hitl:
      risk_threshold: irreversible   # read_only | internal | external_io | irreversible

Usage::

    from meshflow.core.policy_loader import load_policy_yaml, load_guard_yaml

    policy = load_policy_yaml("meshflow.policy.yaml")
    guard  = load_guard_yaml("meshflow.policy.yaml")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_yaml(path: str | Path) -> dict[str, Any]:
    """Read YAML file using PyYAML if available, else fall back to a minimal subset."""
    text = Path(path).read_text()
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(text) or {}
    except ImportError:
        return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML parser for flat key: value structures (no PyYAML dependency).

    Supports: strings, ints, floats, booleans, bracketed lists [a, b, c],
    and one level of nesting via indented blocks.
    """
    def _cast(v: str) -> Any:
        v = v.strip().strip('"').strip("'")
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        if v.startswith("[") and v.endswith("]"):
            return [_cast(i) for i in v[1:-1].split(",") if i.strip()]
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v

    result: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None
    current_key: str = ""

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if line.startswith("  ") or line.startswith("\t"):
                if current_section is not None:
                    if val:
                        current_section[key] = _cast(val)
                    else:
                        current_section[key] = {}
            else:
                if val:
                    result[key] = _cast(val)
                    current_section = None
                    current_key = ""
                else:
                    current_key = key
                    result[key] = {}
                    current_section = result[key]
    return result


def load_policy_yaml(path: str | Path) -> "Policy":  # type: ignore[name-defined]
    """Load a ``Policy`` object from a YAML policy file.

    Parameters
    ----------
    path:
        Path to the YAML file (e.g. ``meshflow.policy.yaml``).

    Returns
    -------
    Policy
        A fully configured ``Policy`` ready to pass to ``Mesh.run()``.
    """
    from meshflow.core.schemas import policy_for_mode

    data = _read_yaml(path)
    mode = data.get("mode", "standard")
    return policy_for_mode(
        mode,
        budget_usd=float(data.get("budget_usd", 1.0)),
        budget_tokens=int(data.get("budget_tokens", 500_000)),
        timeout_s=float(data.get("timeout_s", 300.0)),
        max_steps=int(data.get("max_steps", 50)),
        deterministic_gate=bool(data.get("deterministic_gate", True)),
        enable_guardian=bool(data.get("enable_guardian", True)),
        enable_collusion_audit=bool(data.get("enable_collusion_audit", True)),
        enable_uncertainty=bool(data.get("enable_uncertainty", True)),
        enable_environmental=bool(data.get("enable_environmental", False)),
        enable_cross_run_learning=bool(data.get("enable_cross_run_learning", False)),
        carbon_budget_g=float(data.get("carbon_budget_g", 500.0)),
    )


def load_guard_yaml(path: str | Path) -> "ComplianceGuard | None":  # type: ignore[name-defined]
    """Load a ``ComplianceGuard`` from the ``compliance`` section of a YAML file.

    Returns ``None`` if no ``compliance`` section is present.
    """
    from meshflow.compliance.guard import (
        ComplianceGuard,
        ComplianceRule,
        HIPAAMinimumNecessary,
        HIPAAPHIKeywordBlock,
        SOXDualControl,
        SOXAuditLogRequired,
        GDPRDataMinimisation,
        GDPRPurposeLimitation,
        PCICardDataBlock,
        NERCAccessControl,
    )

    data = _read_yaml(path)
    comp = data.get("compliance")
    if not comp:
        return None

    frameworks: list[str] = comp.get("frameworks", [])
    if isinstance(frameworks, str):
        frameworks = [frameworks]
    block_on_violation: bool = comp.get("block_on_violation", True)

    rule_cfg: dict[str, Any] = comp.get("rules", {}) or {}

    extra_rules: list[ComplianceRule] = []

    # Override default rule parameters if specified in YAML
    if "hipaa_minimum_necessary" in rule_cfg:
        cfg = rule_cfg["hipaa_minimum_necessary"]
        extra_rules.append(HIPAAMinimumNecessary(
            max_input_chars=int(cfg.get("max_input_chars", 50_000))
        ))
    if "sox_dual_control" in rule_cfg:
        cfg = rule_cfg["sox_dual_control"]
        extra_rules.append(SOXDualControl(
            max_consecutive=int(cfg.get("max_consecutive", 5))
        ))
    if "gdpr_data_minimisation" in rule_cfg:
        cfg = rule_cfg["gdpr_data_minimisation"]
        extra_rules.append(GDPRDataMinimisation(
            max_context_keys=int(cfg.get("max_context_keys", 50))
        ))
    if "nerc_access_control" in rule_cfg:
        cfg = rule_cfg["nerc_access_control"]
        approved = cfg.get("approved_nodes", [])
        if isinstance(approved, str):
            approved = [n.strip() for n in approved.split(",") if n.strip()]
        extra_rules.append(NERCAccessControl(approved_nodes=list(approved)))

    return ComplianceGuard(
        frameworks=frameworks,
        extra_rules=extra_rules if extra_rules else None,
        block_on_violation=block_on_violation,
    )


def load_yaml(path: str | Path) -> tuple["Policy", "ComplianceGuard | None"]:  # type: ignore[name-defined]
    """Convenience: load both policy and guard from a single YAML file."""
    return load_policy_yaml(path), load_guard_yaml(path)


def validate_policy_yaml(path: str | Path) -> list[str]:
    """Validate a policy YAML file and return a list of issues (empty = valid)."""
    issues: list[str] = []
    try:
        data = _read_yaml(path)
    except Exception as exc:
        return [f"YAML parse error: {exc}"]

    valid_modes = {"dev", "standard", "regulated", "legal-critical", "hipaa"}
    mode = data.get("mode", "standard")
    if mode not in valid_modes:
        issues.append(f"Unknown mode '{mode}'. Valid: {sorted(valid_modes)}")

    budget = data.get("budget_usd", 1.0)
    if isinstance(budget, (int, float)) and float(budget) <= 0:
        issues.append("budget_usd must be > 0")

    comp = data.get("compliance", {}) or {}
    valid_frameworks = {"hipaa", "sox", "gdpr", "pci", "nerc"}
    for fw in comp.get("frameworks", []):
        if fw not in valid_frameworks:
            issues.append(f"Unknown compliance framework '{fw}'")

    return issues
