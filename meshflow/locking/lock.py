"""Sprint 55 — DistributedLock context manager."""

from __future__ import annotations

import time
from typing import Optional

from meshflow.locking.store import LockRecord, LockStore


class LockAcquisitionError(Exception):
    """Raised when a lock cannot be acquired within the timeout."""

    def __init__(self, resource_id: str, owner: str) -> None:
        self.resource_id = resource_id
        self.owner = owner
        super().__init__(
            f"Could not acquire lock on '{resource_id}' for owner '{owner}'"
        )


class DistributedLock:
    """Context manager for distributed mutual exclusion via a :class:`LockStore`.

    Parameters
    ----------
    resource_id:  The resource to lock.
    owner:        Caller identity (used to authenticate release/extend).
    ttl_s:        Lock TTL.  The lock is auto-expired after this many seconds
                  even if not released explicitly.
    store:        ``LockStore`` instance.  A fresh ``:memory:`` store is created
                  if omitted (useful for unit tests of a single process).
    retry_interval_s: How often to retry when blocking=True.
    """

    def __init__(
        self,
        resource_id: str,
        owner: str = "default",
        ttl_s: float = 30.0,
        store: Optional[LockStore] = None,
        retry_interval_s: float = 0.05,
    ) -> None:
        self.resource_id      = resource_id
        self.owner            = owner
        self.ttl_s            = ttl_s
        self._store           = store if store is not None else LockStore(":memory:")
        self._retry_interval  = retry_interval_s
        self._record:         Optional[LockRecord] = None

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self) -> "DistributedLock":
        acquired = self.acquire(blocking=True, timeout=self.ttl_s)
        if not acquired:
            raise LockAcquisitionError(self.resource_id, self.owner)
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    # ── Acquire ────────────────────────────────────────────────────────────────

    def acquire(
        self,
        blocking: bool = True,
        timeout: Optional[float] = None,
    ) -> bool:
        """Try to acquire the lock.

        Parameters
        ----------
        blocking: If True, retries until the lock is obtained or timeout expires.
        timeout:  Maximum seconds to wait (None = wait forever).

        Returns True if the lock was acquired, False otherwise.
        """
        deadline = time.time() + timeout if timeout is not None else None

        while True:
            record = self._store.try_acquire(self.resource_id, self.owner, self.ttl_s)
            if record is not None:
                self._record = record
                return True

            if not blocking:
                return False

            if deadline is not None and time.time() >= deadline:
                return False

            time.sleep(self._retry_interval)

    # ── Release ────────────────────────────────────────────────────────────────

    def release(self) -> bool:
        """Release the lock.  Returns True if this owner held it."""
        self._record = None
        return self._store.release(self.resource_id, self.owner)

    # ── Extend ─────────────────────────────────────────────────────────────────

    def extend(self, additional_s: float) -> bool:
        """Extend the lease by *additional_s* seconds."""
        return self._store.extend(self.resource_id, self.owner, additional_s)

    # ── Introspection ──────────────────────────────────────────────────────────

    @property
    def is_held(self) -> bool:
        """True if this instance currently holds the lock."""
        return self._record is not None

    @property
    def record(self) -> Optional[LockRecord]:
        return self._record
