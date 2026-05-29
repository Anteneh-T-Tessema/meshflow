"""Multi-approver HITL gates — require N signatures before a workflow resumes.

Closes the HITL "partly" gap: the existing system only has a single boolean
approve/reject.  This module adds:

- **ApprovalGate** — defines how many approvers are required and in what mode.
- **ApprovalCollector** — collects and persists approval decisions from multiple
  reviewers (stored in the checkpoint alongside the existing ledger).
- **Sequential mode** — approvals must arrive in order (A approves → notify B → B approves).
- **Parallel mode** — notify all reviewers at once; wait for *required_count*.
- **Quorum mode** — majority (> 50%) must approve; minority can still reject.

Usage::

    from meshflow.core.multi_approver import ApprovalGate, ApprovalCollector

    # Require 2 of 3 approvers (parallel)
    gate = ApprovalGate(
        required_count=2,
        reviewers=["alice@co.com", "bob@co.com", "carol@co.com"],
        mode="parallel",
    )

    collector = ApprovalCollector(gate)
    collector.submit("alice@co.com", approved=True, comment="LGTM")
    collector.submit("bob@co.com",   approved=False, comment="Needs revision")

    print(collector.is_resolved())   # False — only 1 approve, 1 reject
    collector.submit("carol@co.com", approved=True, comment="OK")
    print(collector.is_resolved())   # True — 2 approvals meet required_count
    print(collector.final_verdict()) # "approved"

Integration with workflow HITL checkpoint::

    # In WorkflowDefinition.resume(), check multi-approver gate:
    from meshflow.core.multi_approver import approval_gate_from_checkpoint

    gate_config = checkpoint.get("approval_gate", {})
    if gate_config:
        collector = ApprovalCollector.from_checkpoint(checkpoint)
        collector.submit(reviewer_id, approved, comment)
        checkpoint["approval_decisions"] = collector.to_dict()
        if not collector.is_resolved():
            return  # still waiting — save updated checkpoint
        decision = HumanDecision(approved=collector.final_verdict() == "approved",
                                  decided_by=collector.deciding_reviewer())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal


# ── Individual decision ───────────────────────────────────────────────────────

@dataclass
class ApprovalDecision:
    reviewer_id: str
    approved: bool
    comment: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "approved":    self.approved,
            "comment":     self.comment,
            "timestamp":   self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApprovalDecision":
        return cls(
            reviewer_id=d["reviewer_id"],
            approved=bool(d["approved"]),
            comment=d.get("comment", ""),
            timestamp=float(d.get("timestamp", time.time())),
        )


# ── Gate configuration ────────────────────────────────────────────────────────

@dataclass
class ApprovalGate:
    """Defines how many and which reviewers must approve before the gate opens.

    Parameters
    ----------
    required_count:
        Minimum number of *positive* approvals needed to open the gate.
        For quorum mode this is ignored; majority determines the outcome.
    reviewers:
        Optional allowlist of reviewer IDs.  If non-empty, only reviewers in
        this list can submit decisions.  Leave empty to allow any reviewer.
    mode:
        ``"parallel"``   — notify all reviewers at once; collect required_count.
        ``"sequential"`` — reviewers are notified one at a time in order.
        ``"quorum"``     — majority wins (> 50% of submitted votes).
    reject_threshold:
        If this many *rejections* are received, gate is immediately denied
        regardless of remaining approvals.  Default 1 for sequential/quorum,
        infinite for parallel (use all votes).
    """

    required_count: int = 1
    reviewers: list[str] = field(default_factory=list)
    mode: Literal["parallel", "sequential", "quorum"] = "parallel"
    reject_threshold: int = 0  # 0 = auto-set based on mode

    def __post_init__(self) -> None:
        if self.reject_threshold == 0:
            if self.mode == "parallel":
                # Any single rejection kills the gate only if it would be impossible
                # to reach required_count — calculated dynamically in collector
                self.reject_threshold = 9999
            else:
                self.reject_threshold = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_count":   self.required_count,
            "reviewers":        self.reviewers,
            "mode":             self.mode,
            "reject_threshold": self.reject_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApprovalGate":
        return cls(
            required_count=int(d.get("required_count", 1)),
            reviewers=list(d.get("reviewers", [])),
            mode=d.get("mode", "parallel"),
            reject_threshold=int(d.get("reject_threshold", 0)),
        )


# ── Approval collector ────────────────────────────────────────────────────────

class ApprovalCollector:
    """Collects approval decisions against a gate configuration.

    Thread-safe for sequential use; for concurrent approvals use an async
    lock at the ledger layer (decisions are persisted as a checkpoint update).
    """

    def __init__(self, gate: ApprovalGate) -> None:
        self._gate = gate
        self._decisions: list[ApprovalDecision] = []

    # ── Submit ─────────────────────────────────────────────────────────────────

    def submit(
        self, reviewer_id: str, approved: bool, comment: str = ""
    ) -> "ApprovalCollector":
        """Record one reviewer's decision.

        Raises
        ------
        ValueError: if *reviewer_id* is not in the allowlist (when set).
        ValueError: if *reviewer_id* has already submitted.
        RuntimeError: if the gate is already resolved.
        """
        if self.is_resolved():
            raise RuntimeError("Gate is already resolved; no further decisions needed.")
        if self._gate.reviewers and reviewer_id not in self._gate.reviewers:
            raise ValueError(
                f"Reviewer {reviewer_id!r} is not in the allowed reviewer list. "
                f"Allowed: {self._gate.reviewers}"
            )
        if any(d.reviewer_id == reviewer_id for d in self._decisions):
            raise ValueError(f"Reviewer {reviewer_id!r} has already submitted a decision.")

        self._decisions.append(ApprovalDecision(
            reviewer_id=reviewer_id, approved=approved, comment=comment
        ))
        return self

    # ── Resolution ─────────────────────────────────────────────────────────────

    def is_resolved(self) -> bool:
        """Return True when the gate is definitively approved OR denied."""
        approvals = sum(1 for d in self._decisions if d.approved)
        rejections = len(self._decisions) - approvals
        mode = self._gate.mode

        if mode == "quorum":
            total_possible = len(self._gate.reviewers) if self._gate.reviewers else len(self._decisions)
            # Resolved when a quorum can't change: majority has voted for one side
            if approvals > total_possible // 2:
                return True
            if rejections > total_possible // 2:
                return True
            return False

        # Immediate rejection check
        if rejections >= self._gate.reject_threshold:
            return True

        # Enough approvals to open gate
        if approvals >= self._gate.required_count:
            return True

        if mode == "parallel":
            # Check if it's mathematically impossible to reach required_count
            remaining_slots = len(self._gate.reviewers) - len(self._decisions) if self._gate.reviewers else 9999
            if approvals + remaining_slots < self._gate.required_count:
                return True  # impossible to reach required — resolved as denied

        return False

    def final_verdict(self) -> str:
        """Return ``"approved"`` or ``"denied"`` (only valid when is_resolved())."""
        if not self.is_resolved():
            return "pending"
        approvals = sum(1 for d in self._decisions if d.approved)
        rejections = len(self._decisions) - approvals
        mode = self._gate.mode

        if mode == "quorum":
            return "approved" if approvals > rejections else "denied"

        if rejections >= self._gate.reject_threshold:
            return "denied"
        if approvals >= self._gate.required_count:
            return "approved"
        return "denied"

    def deciding_reviewer(self) -> str:
        """Return the reviewer_id whose decision tipped the gate to resolved."""
        if not self._decisions:
            return ""
        return self._decisions[-1].reviewer_id

    # ── Progress ───────────────────────────────────────────────────────────────

    def progress(self) -> dict[str, Any]:
        approvals  = sum(1 for d in self._decisions if d.approved)
        rejections = len(self._decisions) - approvals
        return {
            "mode":             self._gate.mode,
            "required_count":   self._gate.required_count,
            "approvals":        approvals,
            "rejections":       rejections,
            "pending_reviewers": self._pending_reviewers(),
            "resolved":         self.is_resolved(),
            "verdict":          self.final_verdict(),
            "decisions":        [d.to_dict() for d in self._decisions],
        }

    def _pending_reviewers(self) -> list[str]:
        if not self._gate.reviewers:
            return []
        submitted = {d.reviewer_id for d in self._decisions}
        return [r for r in self._gate.reviewers if r not in submitted]

    def next_sequential_reviewer(self) -> str | None:
        """For sequential mode: return the next reviewer to notify."""
        if self._gate.mode != "sequential" or not self._gate.reviewers:
            return None
        pending = self._pending_reviewers()
        return pending[0] if pending else None

    # ── Serialisation ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate":      self._gate.to_dict(),
            "decisions": [d.to_dict() for d in self._decisions],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApprovalCollector":
        gate = ApprovalGate.from_dict(d.get("gate", {}))
        collector = cls(gate)
        collector._decisions = [
            ApprovalDecision.from_dict(x) for x in d.get("decisions", [])
        ]
        return collector

    @classmethod
    def from_checkpoint(cls, checkpoint: dict[str, Any]) -> "ApprovalCollector":
        """Restore collector from a HITL checkpoint dict."""
        gate_data = checkpoint.get("approval_gate", {})
        decisions_data = checkpoint.get("approval_decisions", [])
        if not gate_data:
            # Legacy single-approver checkpoint — wrap it
            approved = checkpoint.get("approved")
            reviewed_by = checkpoint.get("reviewed_by", "")
            gate = ApprovalGate(required_count=1)
            collector = cls(gate)
            if approved is not None and reviewed_by:
                collector._decisions.append(ApprovalDecision(
                    reviewer_id=reviewed_by, approved=bool(approved)
                ))
            return collector
        gate = ApprovalGate.from_dict(gate_data)
        collector = cls(gate)
        collector._decisions = [ApprovalDecision.from_dict(x) for x in decisions_data]
        return collector


# ── Helpers ───────────────────────────────────────────────────────────────────

def approval_gate_from_checkpoint(checkpoint: dict[str, Any]) -> ApprovalGate | None:
    """Return the ApprovalGate configured in *checkpoint*, or None for single-approver."""
    data = checkpoint.get("approval_gate")
    if not data:
        return None
    return ApprovalGate.from_dict(data)


__all__ = ["ApprovalGate", "ApprovalDecision", "ApprovalCollector", "approval_gate_from_checkpoint"]
