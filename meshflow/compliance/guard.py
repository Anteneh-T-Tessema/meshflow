"""ComplianceGuard — real-time mid-run compliance enforcement.

Unlike ComplianceReporter (post-hoc), ComplianceGuard runs BEFORE each step
executes and can block it immediately if it would violate an active compliance
rule.  It integrates with StepRuntime as an optional governance layer.

Usage::

    from meshflow.compliance.guard import ComplianceGuard, ComplianceViolation

    guard = ComplianceGuard(frameworks=["hipaa", "sox"])

    # In your StepRuntime or Mesh setup:
    outcome = await runtime.run(node, node_input, context, compliance_guard=guard)

    # Standalone pre-check:
    try:
        guard.pre_check(node_id="agent_a", input_task="...", policy=pol, context={})
    except ComplianceViolation as exc:
        print(exc.control_id, exc.detail)

Supported frameworks:  hipaa, sox, gdpr, pci, nerc
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from meshflow.compliance.reporter import SUPPORTED_FRAMEWORKS


# ── Violation exception ───────────────────────────────────────────────────────


class ComplianceViolation(Exception):
    """Raised when a step is blocked by a real-time compliance rule.

    Attributes
    ----------
    framework:   the regulation that triggered the block ("hipaa", "sox", …)
    control_id:  the specific control that was violated
    detail:      human-readable explanation
    """

    def __init__(self, framework: str, control_id: str, detail: str) -> None:
        super().__init__(f"[{control_id}] {detail}")
        self.framework = framework
        self.control_id = control_id
        self.detail = detail


# ── Rule definitions ──────────────────────────────────────────────────────────


@dataclass
class ComplianceRule:
    framework: str
    control_id: str
    description: str
    enabled: bool = True

    def check(
        self,
        node_id: str,
        input_task: str,
        policy: Any,
        context: dict[str, Any],
        guard: "ComplianceGuard",
    ) -> str | None:
        """Return a violation detail string, or None if the rule passes."""
        raise NotImplementedError


# ── HIPAA rules ───────────────────────────────────────────────────────────────


class HIPAAMinimumNecessary(ComplianceRule):
    """HIPAA §164.502(b) — Minimum necessary standard.

    Blocks tasks whose token estimate exceeds the per-step cap, since
    processing more data than necessary violates minimum-necessity.
    """

    def __init__(self, max_input_chars: int = 50_000) -> None:
        super().__init__(
            framework="hipaa",
            control_id="HIPAA-§164.502(b)",
            description="Minimum necessary — input size cap",
        )
        self.max_input_chars = max_input_chars

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        if len(input_task) > self.max_input_chars:
            return (
                f"Input size {len(input_task):,} chars exceeds HIPAA minimum-necessary "
                f"cap of {self.max_input_chars:,} chars for node '{node_id}'."
            )
        return None


class HIPAAPHIKeywordBlock(ComplianceRule):
    """HIPAA §164.312(e) — Block steps that appear to transmit raw PHI keywords
    without scrubbing enabled."""

    _PHI_PATTERNS = (
        "ssn:", "social security", "date of birth", "dob:", "patient id",
        "medical record", "diagnosis:", "icd-10", "npi:", "insurance id",
    )

    def __init__(self) -> None:
        super().__init__(
            framework="hipaa",
            control_id="HIPAA-§164.312(e)",
            description="PHI keyword detection",
        )

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        if getattr(policy, "scrub_phi", False):
            return None  # scrubbing is active — rule satisfied
        lower = input_task.lower()
        hits = [p for p in self._PHI_PATTERNS if p in lower]
        if hits:
            return (
                f"Potential PHI detected in input to '{node_id}' "
                f"({', '.join(hits[:3])}) but PHI scrubbing is disabled.  "
                "Set scrub_phi=True in the compliance profile."
            )
        return None


# ── SOX rules ────────────────────────────────────────────────────────────────


class SOXDualControl(ComplianceRule):
    """SOX §302 — Segregation of duties: a single node cannot run more than
    max_consecutive_steps in a row without another node interposing."""

    def __init__(self, max_consecutive: int = 5) -> None:
        super().__init__(
            framework="sox",
            control_id="SOX-§302",
            description="Segregation of duties — consecutive step cap",
        )
        self.max_consecutive = max_consecutive

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        consecutive = guard._consecutive_steps.get(node_id, 0)
        if consecutive >= self.max_consecutive:
            return (
                f"Node '{node_id}' has executed {consecutive} consecutive steps "
                f"without interposition (SOX max: {self.max_consecutive}).  "
                "Route through a different agent to maintain segregation of duties."
            )
        return None


class SOXAuditLogRequired(ComplianceRule):
    """SOX §404 — Require that a ledger is configured before any step runs."""

    def __init__(self) -> None:
        super().__init__(
            framework="sox",
            control_id="SOX-§404",
            description="Audit log must be active",
        )

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        if not context.get("_ledger_active", True):
            return (
                "SOX §404 requires a tamper-evident audit ledger. "
                "No ledger is active for this run."
            )
        return None


# ── GDPR rules ───────────────────────────────────────────────────────────────


class GDPRDataMinimisation(ComplianceRule):
    """GDPR Art. 5(1)(c) — data minimisation: block if context carries excessive data."""

    def __init__(self, max_context_keys: int = 50) -> None:
        super().__init__(
            framework="gdpr",
            control_id="GDPR-Art5(1)(c)",
            description="Data minimisation — context size cap",
        )
        self.max_context_keys = max_context_keys

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        if len(context) > self.max_context_keys:
            return (
                f"Context carries {len(context)} keys for node '{node_id}'.  "
                f"GDPR data minimisation requires ≤{self.max_context_keys} context items."
            )
        return None


class GDPRPurposeLimitation(ComplianceRule):
    """GDPR Art. 5(1)(b) — purpose limitation: task must not reference forbidden scopes."""

    _FORBIDDEN = ("marketing profile", "behavioural tracking", "shadow profile")

    def __init__(self) -> None:
        super().__init__(
            framework="gdpr",
            control_id="GDPR-Art5(1)(b)",
            description="Purpose limitation",
        )

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        lower = input_task.lower()
        hits = [f for f in self._FORBIDDEN if f in lower]
        if hits:
            return (
                f"Task for '{node_id}' references purpose-limited activity: {hits}.  "
                "Ensure the processing purpose aligns with the original lawful basis."
            )
        return None


# ── PCI rules ────────────────────────────────────────────────────────────────


class PCICardDataBlock(ComplianceRule):
    """PCI DSS Req 3 — block tasks that appear to contain raw PANs or CVVs."""

    _PATTERNS = ("cvv", "cvv2", "card number", "pan:", "primary account number")

    def __init__(self) -> None:
        super().__init__(
            framework="pci",
            control_id="PCI-DSS-Req3",
            description="Cardholder data protection",
        )

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        lower = input_task.lower()
        hits = [p for p in self._PATTERNS if p in lower]
        if hits:
            return (
                f"Potential cardholder data detected in input to '{node_id}' "
                f"({', '.join(hits[:3])}).  PCI DSS Req 3 prohibits processing raw PANs."
            )
        return None


# ── NERC rules ────────────────────────────────────────────────────────────────


class NERCAccessControl(ComplianceRule):
    """NERC CIP-007 — block nodes not in the approved BES cyber asset list."""

    def __init__(self, approved_nodes: list[str] | None = None) -> None:
        super().__init__(
            framework="nerc",
            control_id="NERC-CIP-007",
            description="BES cyber asset access control",
        )
        self.approved_nodes: set[str] = set(approved_nodes or [])

    def check(self, node_id: str, input_task: str, policy: Any, context: dict, guard: "ComplianceGuard") -> str | None:
        if self.approved_nodes and node_id not in self.approved_nodes:
            return (
                f"Node '{node_id}' is not in the approved BES cyber asset list.  "
                "Add it to ComplianceGuard approved_nodes before running."
            )
        return None


# ── Default rule sets ─────────────────────────────────────────────────────────

_DEFAULT_RULES: dict[str, list[ComplianceRule]] = {
    "hipaa": [HIPAAMinimumNecessary(), HIPAAPHIKeywordBlock()],
    "sox":   [SOXDualControl(), SOXAuditLogRequired()],
    "gdpr":  [GDPRDataMinimisation(), GDPRPurposeLimitation()],
    "pci":   [PCICardDataBlock()],
    "nerc":  [NERCAccessControl()],
}


# ── ComplianceGuard ───────────────────────────────────────────────────────────


@dataclass
class GuardViolationRecord:
    framework: str
    control_id: str
    node_id: str
    detail: str
    timestamp: str
    blocked: bool


class ComplianceGuard:
    """Real-time compliance enforcement that hooks into StepRuntime.

    Parameters
    ----------
    frameworks:
        List of active frameworks.  Each framework's default rules are loaded
        automatically; pass ``extra_rules`` to add custom rules.
    extra_rules:
        Additional ComplianceRule instances to evaluate for every step.
    block_on_violation:
        If True (default), violations raise ComplianceViolation and block the
        step.  If False, violations are recorded but execution continues.
    """

    def __init__(
        self,
        frameworks: list[str] | None = None,
        extra_rules: list[ComplianceRule] | None = None,
        block_on_violation: bool = True,
    ) -> None:
        fws = [f.lower() for f in (frameworks or [])]
        unknown = [f for f in fws if f not in SUPPORTED_FRAMEWORKS]
        if unknown:
            raise ValueError(f"Unknown frameworks: {unknown}.  Supported: {SUPPORTED_FRAMEWORKS}")

        self._frameworks = fws
        self._rules: list[ComplianceRule] = []
        for fw in fws:
            self._rules.extend(_DEFAULT_RULES.get(fw, []))
        if extra_rules:
            self._rules.extend(extra_rules)

        self._block_on_violation = block_on_violation
        self._violations: list[GuardViolationRecord] = []
        self._consecutive_steps: dict[str, int] = {}  # node_id → consecutive count
        self._last_node: str | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def pre_check(
        self,
        node_id: str,
        input_task: str,
        policy: Any,
        context: dict[str, Any],
    ) -> None:
        """Evaluate all rules before a step executes.

        Raises ComplianceViolation on the first failing rule (if block_on_violation=True).
        Always records violations regardless of the block setting.
        """
        self._update_consecutive(node_id)

        for rule in self._rules:
            if not rule.enabled:
                continue
            detail = rule.check(node_id, input_task, policy, context, self)
            if detail is not None:
                rec = GuardViolationRecord(
                    framework=rule.framework,
                    control_id=rule.control_id,
                    node_id=node_id,
                    detail=detail,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    blocked=self._block_on_violation,
                )
                self._violations.append(rec)
                if self._block_on_violation:
                    raise ComplianceViolation(rule.framework, rule.control_id, detail)

    def post_step(self, node_id: str, blocked: bool) -> None:
        """Called after a step completes.  Resets consecutive counter on blocked steps."""
        if blocked:
            self._consecutive_steps[node_id] = 0

    def violations(self) -> list[GuardViolationRecord]:
        return list(self._violations)

    def clear_violations(self) -> None:
        self._violations.clear()

    def violation_count(self) -> int:
        return len(self._violations)

    def summary(self) -> dict[str, Any]:
        return {
            "frameworks": self._frameworks,
            "rules": len(self._rules),
            "violations": self.violation_count(),
            "block_on_violation": self._block_on_violation,
            "by_framework": {
                fw: sum(1 for v in self._violations if v.framework == fw)
                for fw in self._frameworks
            },
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _update_consecutive(self, node_id: str) -> None:
        if self._last_node == node_id:
            self._consecutive_steps[node_id] = self._consecutive_steps.get(node_id, 0) + 1
        else:
            self._consecutive_steps[node_id] = 1
            if self._last_node is not None:
                self._consecutive_steps[self._last_node] = 0
        self._last_node = node_id
