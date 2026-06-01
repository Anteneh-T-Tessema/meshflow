"""Just-In-Time (JIT) privilege manager — Zero Trust Advanced tier.

Implements the JIT/JEA (Just Enough Administration) pattern from the Anthropic
Zero Trust guide (Part III — Privilege Scoping, Advanced):

  "Grant permissions only at moment of need. Scope access to specific resources
   for specific durations. Automatically revoke permissions after task completion
   or timeout."

Usage::

    from meshflow.zero_trust.jit import JITPrivilegeManager

    mgr = JITPrivilegeManager(default_ttl_seconds=120)

    # Request a grant
    grant = mgr.request("agent-123", permissions=["read:contracts", "write:summary"],
                        resources=["s3://bucket/contracts/*"], reason="Summarise Q1 contracts")

    # Check before each action
    if mgr.is_allowed(grant.grant_id, "read:contracts"):
        result = fetch_document(...)

    # Revoke early on task completion
    mgr.revoke(grant.grant_id, reason="task_complete")

    # Expired grants raise PrivilegeExpiredError automatically
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class PrivilegeExpiredError(Exception):
    """Raised when a JIT grant has expired or been revoked."""


class MaxGrantsExceededError(Exception):
    """Raised when an agent already has too many concurrent JIT grants."""


@dataclass
class PrivilegeGrant:
    """A time-limited privilege grant for a specific agent."""

    grant_id: str = field(default_factory=lambda: f"jit-{uuid.uuid4().hex[:12]}")
    agent_id: str = ""
    permissions: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)   # glob patterns
    reason: str = ""
    granted_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    revoked: bool = False
    revoked_at: float = 0.0
    revocation_reason: str = ""
    actions_taken: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        if self.revoked:
            return False
        return time.time() < self.expires_at

    @property
    def ttl_remaining(self) -> float:
        """Seconds until expiry (0 if already expired)."""
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id":         self.grant_id,
            "agent_id":         self.agent_id,
            "permissions":      self.permissions,
            "resources":        self.resources,
            "reason":           self.reason,
            "granted_at":       self.granted_at,
            "expires_at":       self.expires_at,
            "ttl_remaining_s":  round(self.ttl_remaining, 1),
            "revoked":          self.revoked,
            "revocation_reason":self.revocation_reason,
            "actions_taken":    len(self.actions_taken),
        }


class JITPrivilegeManager:
    """Manages Just-In-Time privilege grants with automatic expiry.

    Thread-safe. Runs a background reaper thread that expires grants.

    Parameters
    ----------
    default_ttl_seconds: How long a grant lives if not explicitly revoked.
    max_grants_per_agent: Maximum concurrent active grants per agent.
    audit_callback:      Optional callable invoked on every grant/revoke/expiry.
    """

    def __init__(
        self,
        default_ttl_seconds: int = 120,
        max_grants_per_agent: int = 10,
        audit_callback: Any = None,
    ) -> None:
        self._ttl = default_ttl_seconds
        self._max_grants = max_grants_per_agent
        self._audit = audit_callback
        self._grants: dict[str, PrivilegeGrant] = {}
        self._lock = threading.Lock()
        self._start_reaper()

    # ── Public API ────────────────────────────────────────────────────────────

    def request(
        self,
        agent_id: str,
        permissions: list[str],
        *,
        resources: list[str] | None = None,
        ttl_seconds: int | None = None,
        reason: str = "",
    ) -> PrivilegeGrant:
        """Issue a time-limited privilege grant to *agent_id*.

        Raises
        ------
        MaxGrantsExceededError  If the agent already has too many active grants.
        """
        with self._lock:
            active = self._active_grants_for(agent_id)
            if len(active) >= self._max_grants:
                raise MaxGrantsExceededError(
                    f"Agent {agent_id!r} already has {len(active)} active grants "
                    f"(max {self._max_grants})"
                )
            ttl = ttl_seconds if ttl_seconds is not None else self._ttl
            grant = PrivilegeGrant(
                agent_id=agent_id,
                permissions=list(permissions),
                resources=list(resources or []),
                reason=reason,
                expires_at=time.time() + ttl,
            )
            self._grants[grant.grant_id] = grant
            self._emit("grant_issued", grant)
            return grant

    def is_allowed(
        self,
        grant_id: str,
        permission: str,
        resource: str = "",
    ) -> bool:
        """Return True iff the grant is valid and covers *permission*.

        Records the check in ``grant.actions_taken``.

        Raises
        ------
        PrivilegeExpiredError  If the grant has expired or been revoked.
        """
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                raise PrivilegeExpiredError(f"Grant {grant_id!r} not found")
            if not grant.is_valid:
                reason = "revoked" if grant.revoked else "expired"
                raise PrivilegeExpiredError(f"Grant {grant_id!r} is {reason}")
            allowed = permission in grant.permissions
            if not allowed:
                for perm in grant.permissions:
                    if perm.endswith(":*") and permission.startswith(perm[:-1]):
                        allowed = True
                        break
            grant.actions_taken.append({
                "permission": permission,
                "resource":   resource,
                "allowed":    allowed,
                "ts":         time.time(),
            })
            return allowed

    def revoke(self, grant_id: str, *, reason: str = "explicit_revoke") -> bool:
        """Revoke a grant immediately. Returns True if it was active."""
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None or not grant.is_valid:
                return False
            grant.revoked = True
            grant.revoked_at = time.time()
            grant.revocation_reason = reason
            self._emit("grant_revoked", grant)
            return True

    def revoke_all(self, agent_id: str, *, reason: str = "agent_containment") -> int:
        """Revoke all active grants for *agent_id*. Returns count revoked."""
        revoked = 0
        with self._lock:
            for grant in self._grants.values():
                if grant.agent_id == agent_id and grant.is_valid:
                    grant.revoked = True
                    grant.revoked_at = time.time()
                    grant.revocation_reason = reason
                    self._emit("grant_revoked", grant)
                    revoked += 1
        return revoked

    def active_grants(self) -> list[dict[str, Any]]:
        """Return all currently active grants as dicts."""
        with self._lock:
            return [g.to_dict() for g in self._grants.values() if g.is_valid]

    def grant_history(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Return all grants (including expired/revoked) optionally filtered by agent."""
        with self._lock:
            grants = self._grants.values()
            if agent_id:
                grants = [g for g in grants if g.agent_id == agent_id]  # type: ignore[assignment]
            return [g.to_dict() for g in grants]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            all_g = list(self._grants.values())
            return {
                "total_grants":    len(all_g),
                "active_grants":   sum(1 for g in all_g if g.is_valid),
                "expired_grants":  sum(1 for g in all_g if not g.is_valid and not g.revoked),
                "revoked_grants":  sum(1 for g in all_g if g.revoked),
                "agents_with_jit": len({g.agent_id for g in all_g if g.is_valid}),
            }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _active_grants_for(self, agent_id: str) -> list[PrivilegeGrant]:
        return [g for g in self._grants.values()
                if g.agent_id == agent_id and g.is_valid]

    def _emit(self, event: str, grant: PrivilegeGrant) -> None:
        if self._audit:
            try:
                self._audit(event, grant.to_dict())
            except Exception:
                pass

    def _reap_expired(self) -> None:
        """Background reaper — emits expiry events for grants that just expired."""
        now = time.time()
        with self._lock:
            for grant in self._grants.values():
                if not grant.revoked and grant.expires_at < now and grant.expires_at > 0:
                    if not grant.revoked:
                        grant.revoked = True
                        grant.revoked_at = now
                        grant.revocation_reason = "expired"
                        self._emit("grant_expired", grant)

    def _start_reaper(self) -> None:
        def _loop() -> None:
            while True:
                time.sleep(5)
                self._reap_expired()

        t = threading.Thread(target=_loop, daemon=True, name="jit-reaper")
        t.start()


# ── Module-level singleton (optional convenience) ─────────────────────────────

_DEFAULT_MANAGER: JITPrivilegeManager | None = None


def get_default_manager() -> JITPrivilegeManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = JITPrivilegeManager()
    return _DEFAULT_MANAGER


__all__ = [
    "JITPrivilegeManager",
    "PrivilegeGrant",
    "PrivilegeExpiredError",
    "MaxGrantsExceededError",
    "get_default_manager",
]
