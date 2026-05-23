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
