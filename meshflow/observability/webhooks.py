"""Webhook alerting — push signed event notifications to external systems.

Supported event types:
  policy_violation  — a step was blocked by the governance kernel
  budget_exceeded   — a run exceeded its cost or token budget
  hitl_pending      — a run is paused waiting for human approval
  run_failed        — a run completed with status=failed
  run_completed     — a run completed successfully
  collusion_alert   — collusion detection raised an alert
  *                 — wildcard: receive all events

Webhook payloads are HMAC-SHA256 signed using the per-registration secret
(or the global MESHFLOW_WEBHOOK_SECRET env var).  Recipients verify with:

    import hashlib, hmac
    expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, request.headers["X-MeshFlow-Signature"])

Usage::

    from meshflow.observability.webhooks import get_webhook_manager

    mgr = get_webhook_manager()
    reg = mgr.register("https://hooks.example.com/meshflow", events=["policy_violation"])
    await mgr.deliver("policy_violation", {"run_id": "abc", "node_id": "analysis"})
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

VALID_EVENTS = frozenset({
    "policy_violation",
    "budget_exceeded",
    "hitl_pending",
    "run_failed",
    "run_completed",
    "collusion_alert",
    "*",
})

_MAX_RETRIES = 3
_RETRY_DELAYS = (1.0, 3.0, 10.0)  # seconds
_DELIVERY_TIMEOUT = 10.0
_HISTORY_LIMIT = 200


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class WebhookRegistration:
    id: str
    url: str
    events: list[str]
    secret: str
    created_at: str
    tenant_id: str = ""
    delivery_count: int = 0
    failure_count: int = 0
    last_delivery_at: str | None = None
    last_error: str | None = None

    def matches(self, event_type: str) -> bool:
        return "*" in self.events or event_type in self.events

    def to_dict(self, include_secret: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "events": self.events,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "delivery_count": self.delivery_count,
            "failure_count": self.failure_count,
            "last_delivery_at": self.last_delivery_at,
            "last_error": self.last_error,
        }
        if include_secret:
            d["secret"] = self.secret
        return d


@dataclass
class DeliveryRecord:
    webhook_id: str
    event_type: str
    timestamp: str
    success: bool
    status_code: int | None
    error: str | None
    attempt: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "webhook_id": self.webhook_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "success": self.success,
            "status_code": self.status_code,
            "error": self.error,
            "attempt": self.attempt,
        }


# ── Manager ───────────────────────────────────────────────────────────────────


class WebhookManager:
    """In-memory webhook registry with async HMAC-signed delivery and retry."""

    def __init__(self, default_secret: str = "") -> None:
        self._hooks: dict[str, WebhookRegistration] = {}
        self._history: list[DeliveryRecord] = []
        self._default_secret = default_secret or os.environ.get("MESHFLOW_WEBHOOK_SECRET", "")
        self._lock = asyncio.Lock()

    def register(
        self,
        url: str,
        events: list[str] | None = None,
        secret: str = "",
        tenant_id: str = "",
    ) -> WebhookRegistration:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Webhook URL must start with http:// or https://: {url!r}")
        ev = events or ["*"]
        unknown = [e for e in ev if e not in VALID_EVENTS]
        if unknown:
            raise ValueError(f"Unknown event types: {unknown}. Valid: {sorted(VALID_EVENTS)}")
        reg = WebhookRegistration(
            id=str(uuid.uuid4()),
            url=url,
            events=list(ev),
            secret=secret or self._default_secret,
            created_at=datetime.now(timezone.utc).isoformat(),
            tenant_id=tenant_id,
        )
        self._hooks[reg.id] = reg
        return reg

    def unregister(self, webhook_id: str, tenant_id: str = "") -> bool:
        hook = self._hooks.get(webhook_id)
        if hook is None:
            return False
        # Tenant isolation: can only delete own hooks unless no tenant scope
        if tenant_id and hook.tenant_id and hook.tenant_id != tenant_id:
            return False
        del self._hooks[webhook_id]
        return True

    def list(self, tenant_id: str = "") -> list[WebhookRegistration]:
        """List webhooks, optionally filtered to a tenant."""
        if not tenant_id:
            return list(self._hooks.values())
        return [h for h in self._hooks.values() if not h.tenant_id or h.tenant_id == tenant_id]

    def get(self, webhook_id: str, tenant_id: str = "") -> WebhookRegistration | None:
        hook = self._hooks.get(webhook_id)
        if hook is None:
            return None
        if tenant_id and hook.tenant_id and hook.tenant_id != tenant_id:
            return None
        return hook

    def delivery_history(self, webhook_id: str | None = None, tenant_id: str = "") -> list[DeliveryRecord]:
        records = self._history if webhook_id is None else [r for r in self._history if r.webhook_id == webhook_id]
        if not tenant_id:
            return list(records)
        # Filter to records whose webhook belongs to this tenant
        tenant_hooks = {h.id for h in self.list(tenant_id=tenant_id)}
        return [r for r in records if r.webhook_id in tenant_hooks]

    def _sign(self, secret: str, body: bytes) -> str:
        key = (secret or "unsigned").encode()
        return hmac.new(key, body, hashlib.sha256).hexdigest()

    async def deliver(self, event_type: str, payload: dict[str, Any], tenant_id: str = "") -> None:
        """Deliver event to all matching webhooks concurrently.

        When tenant_id is provided, only webhooks belonging to that tenant
        (or global webhooks with no tenant) receive the event.
        """
        targets = [h for h in self._hooks.values() if h.matches(event_type)]
        if tenant_id:
            targets = [h for h in targets if not h.tenant_id or h.tenant_id == tenant_id]
        if not targets:
            return
        envelope = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        body = json.dumps(envelope).encode()
        await asyncio.gather(
            *[self._deliver_one(h, event_type, body) for h in targets],
            return_exceptions=True,
        )

    async def _deliver_one(
        self, hook: WebhookRegistration, event_type: str, body: bytes
    ) -> None:
        sig = self._sign(hook.secret, body)
        headers = {
            "Content-Type": "application/json",
            "X-MeshFlow-Event": event_type,
            "X-MeshFlow-Signature": sig,
            "X-MeshFlow-Delivery": str(uuid.uuid4()),
        }

        last_err: str | None = None
        status_code: int | None = None

        for attempt, delay in enumerate(_RETRY_DELAYS[:_MAX_RETRIES], start=1):
            try:
                import urllib.request
                req = urllib.request.Request(
                    hook.url, data=body, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=_DELIVERY_TIMEOUT) as resp:
                    status_code = resp.status
                    if 200 <= status_code < 300:
                        hook.delivery_count += 1
                        hook.last_delivery_at = datetime.now(timezone.utc).isoformat()
                        hook.last_error = None
                        self._record(hook.id, event_type, True, status_code, None, attempt)
                        return
                    last_err = f"HTTP {status_code}"
            except Exception as exc:
                last_err = str(exc)

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(delay)

        hook.failure_count += 1
        hook.last_error = last_err
        self._record(hook.id, event_type, False, status_code, last_err, _MAX_RETRIES)

    def _record(
        self,
        webhook_id: str,
        event_type: str,
        success: bool,
        status_code: int | None,
        error: str | None,
        attempt: int,
    ) -> None:
        rec = DeliveryRecord(
            webhook_id=webhook_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            success=success,
            status_code=status_code,
            error=error,
            attempt=attempt,
        )
        self._history.append(rec)
        if len(self._history) > _HISTORY_LIMIT:
            self._history = self._history[-_HISTORY_LIMIT:]

    def stats(self) -> dict[str, Any]:
        return {
            "registered": len(self._hooks),
            "total_deliveries": sum(h.delivery_count for h in self._hooks.values()),
            "total_failures": sum(h.failure_count for h in self._hooks.values()),
            "history_size": len(self._history),
        }


# ── Global singleton ──────────────────────────────────────────────────────────

_manager: WebhookManager | None = None


def get_webhook_manager() -> WebhookManager:
    global _manager
    if _manager is None:
        _manager = WebhookManager()
    return _manager


def reset_webhook_manager() -> None:
    """Reset the global singleton (test isolation)."""
    global _manager
    _manager = None
