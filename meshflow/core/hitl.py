"""HITL (Human-In-The-Loop) notification and timeout management.

When a workflow pauses for human approval, this module:
  1. POSTs a webhook notification to the configured URL with an HMAC signature.
  2. Watches paused runs for timeout — auto-approves or rejects when expired.
  3. Provides an async API for external systems to approve/reject via HTTP endpoints.

The webhook payload includes approve_url and reject_url so the reviewer can
act directly from an email or Slack message without logging into any dashboard.

Usage:
    notifier = HITLNotifier(webhook_url="https://hooks.example.com/mesh")
    await notifier.notify(run_id="abc", node_id="approval", context={...})

    watcher = HITLTimeoutWatcher(ledger=ledger, timeout_s=86400, on_timeout="reject")
    asyncio.create_task(watcher.run())   # background task
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass
class HITLNotifier:
    """POST a signed webhook when a run pauses for human review.

    The webhook body:
        {
          "run_id": "abc",
          "node_id": "approval",
          "context": {...},
          "approve_url": "https://yourserver/hitl/abc/approve",
          "reject_url":  "https://yourserver/hitl/abc/reject",
          "expires_at":  "2025-05-23T12:00:00Z"
        }

    The request includes an ``X-MeshFlow-Signature: sha256=<hmac>`` header so
    the receiver can verify the payload was sent by MeshFlow (not a replay attack).
    """

    webhook_url: str
    server_base_url: str = (
        ""  # e.g. "https://api.yourcompany.com" — used to build approve/reject URLs
    )
    secret: str = field(
        default_factory=lambda: os.environ.get("MESHFLOW_WEBHOOK_SECRET", "meshflow-hitl-secret")
    )
    timeout_s: int = 86400  # 24 hours default

    def _signature(self, body: bytes) -> str:
        mac = hmac.new(self.secret.encode(), body, hashlib.sha256)
        return f"sha256={mac.hexdigest()}"

    def _expires_at(self) -> str:
        from datetime import timedelta

        return (datetime.now(timezone.utc) + timedelta(seconds=self.timeout_s)).isoformat()

    async def notify(
        self,
        run_id: str,
        node_id: str,
        context: dict[str, Any],
        *,
        base_url: str = "",
    ) -> bool:
        """Send the webhook. Returns True on success, False on failure."""
        if not self.webhook_url:
            return False

        effective_base = base_url or self.server_base_url
        payload = {
            "run_id": run_id,
            "node_id": node_id,
            "context": {k: v for k, v in context.items() if not k.startswith("_")},
            "approve_url": f"{effective_base}/hitl/{run_id}/approve" if effective_base else "",
            "reject_url": f"{effective_base}/hitl/{run_id}/reject" if effective_base else "",
            "expires_at": self._expires_at(),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        body = json.dumps(payload).encode()
        sig = self._signature(body)

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.webhook_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-MeshFlow-Signature": sig,
                        "X-MeshFlow-Run-Id": run_id,
                    },
                )
                return response.is_success
        except Exception:
            return False


@dataclass
class HITLTimeoutWatcher:
    """Background task that auto-resolves paused runs after timeout_s.

    Poll the ledger every ``poll_interval_s`` seconds. When a checkpoint is
    older than ``timeout_s``, apply ``on_timeout`` decision and delete the
    checkpoint so the run is no longer listed as paused.

    ``on_timeout`` values:
      "reject"  — mark the checkpoint as rejected (confidence=0.0 on resume)
      "approve" — mark the checkpoint as approved
      "escalate"— send a second webhook notification and keep waiting
    """

    ledger: Any  # ReplayLedger
    timeout_s: int = 86400
    poll_interval_s: int = 60
    on_timeout: Literal["reject", "approve", "escalate"] = "reject"
    notifier: HITLNotifier | None = None  # for escalation webhooks

    async def run(self) -> None:
        """Run forever — call as asyncio.create_task(watcher.run())."""
        while True:
            await asyncio.sleep(self.poll_interval_s)
            try:
                await self._check()
            except Exception:
                pass

    async def _check(self) -> None:
        paused = await self.ledger.list_paused_runs()
        for entry in paused:
            paused_at_str = entry.get("paused_at", "")
            if not paused_at_str:
                continue
            try:
                paused_at = datetime.fromisoformat(paused_at_str.replace("Z", "+00:00"))
                age_s = (datetime.now(timezone.utc) - paused_at).total_seconds()
            except Exception:
                continue

            if age_s < self.timeout_s:
                continue

            run_id = entry["run_id"]
            checkpoint = await self.ledger.load_checkpoint_data(run_id)
            if not checkpoint:
                continue

            # Already reviewed
            if checkpoint.get("approved") in (True, False):
                continue

            if self.on_timeout == "escalate" and self.notifier:
                await self.notifier.notify(
                    run_id=run_id,
                    node_id=checkpoint.get("paused_at_node", "?"),
                    context={**checkpoint.get("context", {}), "escalation": True},
                )
                # Update timeout: extend by another timeout_s period
                checkpoint["escalated_at"] = datetime.now(timezone.utc).isoformat()
                await self.ledger.save_checkpoint(run_id, checkpoint)
            else:
                approved = self.on_timeout == "approve"
                checkpoint["approved"] = approved
                checkpoint["reviewed_by"] = "timeout_watcher"
                checkpoint["review_notes"] = (
                    f"Auto-{'approved' if approved else 'rejected'} after {self.timeout_s}s timeout"
                )
                await self.ledger.save_checkpoint(run_id, checkpoint)


# ── HITL SLA tracking (Gap B) ─────────────────────────────────────────────────

@dataclass
class HITLApprovalSLA:
    """SLA contract for HITL approval gates.

    Defines maximum acceptable pendency durations.  When a checkpoint exceeds
    a threshold, the matching action is triggered automatically by
    :class:`HITLSLAWatcher`.

    Parameters
    ----------
    warn_after_s:    Log a warning after this many seconds without approval.
    escalate_after_s: Send an escalation webhook after this many seconds.
    reject_after_s:  Auto-reject after this many seconds (hard deadline).
    reviewers:       Optional reviewer IDs to include in escalation payload.
    """

    warn_after_s: float = 3600.0        # 1 hour — log warning
    escalate_after_s: float = 14400.0   # 4 hours — escalation webhook
    reject_after_s: float = 86400.0     # 24 hours — auto-reject

    reviewers: list[str] = field(default_factory=list)


@dataclass
class HITLSLABreach:
    """A recorded SLA breach for a paused HITL checkpoint."""

    run_id: str
    node_id: str
    breach_type: str    # "warn" | "escalate" | "reject"
    pending_s: float
    threshold_s: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":       self.run_id,
            "node_id":      self.node_id,
            "breach_type":  self.breach_type,
            "pending_s":    round(self.pending_s, 1),
            "threshold_s":  self.threshold_s,
            "timestamp":    self.timestamp,
        }


class HITLSLAWatcher:
    """Background task that enforces SLA on HITL approval pendency.

    Unlike :class:`HITLTimeoutWatcher` (which has a single timeout), this
    watcher fires *three* progressive actions: warn → escalate → reject.

    Usage::

        sla = HITLApprovalSLA(warn_after_s=3600, escalate_after_s=14400, reject_after_s=86400)
        watcher = HITLSLAWatcher(ledger, sla=sla, notifier=notifier)
        asyncio.create_task(watcher.run())
    """

    def __init__(
        self,
        ledger: Any,
        sla: HITLApprovalSLA,
        notifier: HITLNotifier | None = None,
        poll_interval_s: float = 120.0,
        on_breach_callback: Any = None,
    ) -> None:
        self._ledger = ledger
        self._sla = sla
        self._notifier = notifier
        self._poll = poll_interval_s
        self._callback = on_breach_callback
        self._breaches: list[HITLSLABreach] = []
        # Track which actions have already been taken per run_id
        self._notified_escalate: set[str] = set()
        self._notified_warn: set[str] = set()

    async def run(self) -> None:
        """Run indefinitely — launch as asyncio.create_task(watcher.run())."""
        while True:
            await asyncio.sleep(self._poll)
            try:
                await self._tick()
            except Exception:
                pass

    async def _tick(self) -> None:
        paused = await self._ledger.list_paused_runs()
        for entry in paused:
            paused_at_str = entry.get("paused_at", "")
            if not paused_at_str:
                continue
            try:
                paused_at = datetime.fromisoformat(paused_at_str.replace("Z", "+00:00"))
                age_s = (datetime.now(timezone.utc) - paused_at).total_seconds()
            except Exception:
                continue

            run_id = entry["run_id"]
            checkpoint = await self._ledger.load_checkpoint_data(run_id)
            if not checkpoint or checkpoint.get("approved") in (True, False):
                continue  # already resolved

            node_id = checkpoint.get("paused_at_node", "?")

            # ── Hard reject deadline ──────────────────────────────────────────
            if age_s >= self._sla.reject_after_s:
                breach = HITLSLABreach(
                    run_id=run_id, node_id=node_id,
                    breach_type="reject",
                    pending_s=age_s, threshold_s=self._sla.reject_after_s,
                )
                self._breaches.append(breach)
                if self._callback:
                    self._callback(breach)
                checkpoint["approved"] = False
                checkpoint["reviewed_by"] = "sla_watcher"
                checkpoint["review_notes"] = (
                    f"Auto-rejected: pending {age_s:.0f}s exceeded SLA deadline "
                    f"{self._sla.reject_after_s:.0f}s"
                )
                await self._ledger.save_checkpoint(run_id, checkpoint)
                continue

            # ── Escalation notification ───────────────────────────────────────
            if age_s >= self._sla.escalate_after_s and run_id not in self._notified_escalate:
                self._notified_escalate.add(run_id)
                breach = HITLSLABreach(
                    run_id=run_id, node_id=node_id,
                    breach_type="escalate",
                    pending_s=age_s, threshold_s=self._sla.escalate_after_s,
                )
                self._breaches.append(breach)
                if self._callback:
                    self._callback(breach)
                if self._notifier:
                    await self._notifier.notify(
                        run_id=run_id, node_id=node_id,
                        context={
                            **checkpoint.get("context", {}),
                            "sla_breach": "escalate",
                            "pending_s": age_s,
                            "reviewers": self._sla.reviewers,
                        },
                    )
                continue

            # ── Warning ───────────────────────────────────────────────────────
            if age_s >= self._sla.warn_after_s and run_id not in self._notified_warn:
                self._notified_warn.add(run_id)
                breach = HITLSLABreach(
                    run_id=run_id, node_id=node_id,
                    breach_type="warn",
                    pending_s=age_s, threshold_s=self._sla.warn_after_s,
                )
                self._breaches.append(breach)
                if self._callback:
                    self._callback(breach)

    def recent_breaches(self, limit: int = 50) -> list[dict[str, Any]]:
        return [b.to_dict() for b in self._breaches[-limit:]]

    def pending_escalations(self) -> list[str]:
        """Return run IDs that have been escalated but not yet resolved."""
        return [r for r in self._notified_escalate if r not in self._notified_warn]
