"""Standalone schema definitions — zero external dependencies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Callable


class RiskTier(IntEnum):
    READ_ONLY   = 1
    INTERNAL    = 2
    EXTERNAL_IO = 3
    IRREVERSIBLE = 4


class ActionVerdict(str):
    COMMIT   = "COMMIT"
    REJECT   = "REJECT"
    ESCALATE = "ESCALATE"

    def __new__(cls, value: str) -> "ActionVerdict":
        obj = str.__new__(cls, value)
        return obj


ActionVerdict.COMMIT   = ActionVerdict("COMMIT")   # type: ignore[assignment]
ActionVerdict.REJECT   = ActionVerdict("REJECT")   # type: ignore[assignment]
ActionVerdict.ESCALATE = ActionVerdict("ESCALATE") # type: ignore[assignment]


@dataclass
class ForensicPolicy:
    """Governance policy for the forensic gate."""
    allow_tainted_external_io: bool = False
    require_hitl_for_irreversible: bool = True
    max_failure_rate: float = 0.5


@dataclass
class CompensationPlan:
    """Rollback plan executed on REJECT."""
    steps: list[str] = field(default_factory=list)
    rollback_fn: Callable[[], None] | None = None


@dataclass
class Intent:
    """Represents one agent action request submitted to the gate."""
    action: str
    agent_id: str = ""
    agent_did: str = ""
    intent_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    payload: dict[str, Any] = field(default_factory=dict)
    risk_tier: RiskTier = RiskTier.READ_ONLY
    effective_tier: RiskTier = RiskTier.READ_ONLY
    tainted: bool = False
    compensation: CompensationPlan | None = None


@dataclass
class LedgerEntry:
    """One immutable record in the audit ledger."""
    run_id: str
    intent_id: str
    agent_id: str
    action: str
    verdict: ActionVerdict
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    agent_did: str = ""
    effective_tier: int = 1
    prev_hash: str = ""
    entry_hash: str = ""
