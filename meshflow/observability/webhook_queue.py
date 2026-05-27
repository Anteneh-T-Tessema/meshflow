"""Sprint 53 — Durable Webhook Retry Queue.

Adds persistence, exponential-backoff retry, dead-letter store, and replay to
the existing in-memory ``WebhookManager``.

Architecture
------------
WebhookDelivery        — immutable record for one delivery attempt series.
WebhookRetryQueue      — SQLite store; enqueue, fetch-due, update status.
WebhookReliableDeliverer — background thread that processes due deliveries,
                           retries failures with exponential backoff, and
                           moves exhausted deliveries to dead-letter.

Backoff schedule (attempt → wait before next try)
--------------------------------------------------
  attempt 0 → immediate
  attempt 1 → 10 s
  attempt 2 → 60 s
  attempt 3 → 5 min
  attempt 4 → 30 min
  attempt 5 → dead-letter

Usage
-----
    from meshflow.observability.webhook_queue import (
        WebhookRetryQueue, WebhookReliableDeliverer,
    )
    from meshflow.observability.webhooks import get_webhook_manager

    queue = WebhookRetryQueue(":memory:")
    mgr   = get_webhook_manager()

    reg = mgr.register("https://hooks.example.com/events", events=["run_failed"])
    queue.enqueue(reg.id, reg.url, "run_failed", {"run_id": "abc"}, reg.secret)

    with WebhookReliableDeliverer(queue, poll_s=5) as deliverer:
        # background thread processes and retries deliveries
        ...
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Optional

# ── Backoff schedule ──────────────────────────────────────────────────────────

_BACKOFF_S: list[float] = [0, 10, 60, 300, 1800]  # wait before attempt N+1
_DELIVERY_TIMEOUT = 10.0

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id      TEXT PRIMARY KEY,
    webhook_id       TEXT NOT NULL,
    url              TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    secret           TEXT NOT NULL DEFAULT '',
    attempt          INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 5,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       REAL NOT NULL,
    next_retry_at    REAL NOT NULL,
    last_error       TEXT,
    last_attempt_at  REAL,
    succeeded_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_wdq_status_retry
    ON webhook_deliveries(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_wdq_webhook_id
    ON webhook_deliveries(webhook_id);
"""


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class WebhookDelivery:
    """A single queued delivery with its full retry history."""

    delivery_id:     str
    webhook_id:      str
    url:             str
    event_type:      str
    payload_json:    str
    secret:          str
    attempt:         int
    max_attempts:    int
    status:          str          # pending | success | failed | dead
    created_at:      float
    next_retry_at:   float
    last_error:      Optional[str]
    last_attempt_at: Optional[float]
    succeeded_at:    Optional[float]

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    def is_dead(self) -> bool:
        return self.status == "dead"

    def is_success(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_id":     self.delivery_id,
            "webhook_id":      self.webhook_id,
            "url":             self.url,
            "event_type":      self.event_type,
            "attempt":         self.attempt,
            "max_attempts":    self.max_attempts,
            "status":          self.status,
            "created_at":      self.created_at,
            "next_retry_at":   self.next_retry_at,
            "last_error":      self.last_error,
            "last_attempt_at": self.last_attempt_at,
            "succeeded_at":    self.succeeded_at,
        }


# ── Queue store ───────────────────────────────────────────────────────────────

class WebhookRetryQueue:
    """SQLite-backed durable retry queue for webhook deliveries.

    Parameters
    ----------
    db_path:      Filesystem path or ``":memory:"``.
    max_attempts: Default maximum attempts per delivery.  Can be overridden
                  per-delivery in ``enqueue()``.
    """

    def __init__(
        self,
        db_path: str = "meshflow_webhooks.db",
        max_attempts: int = 5,
    ) -> None:
        self._db_path     = db_path
        self._max_attempts = max_attempts
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None
        self._ensure_schema()

    # ── Connection ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    # ── Enqueue ───────────────────────────────────────────────────────────────

    def enqueue(
        self,
        webhook_id: str,
        url: str,
        event_type: str,
        payload: dict[str, Any],
        secret: str = "",
        max_attempts: Optional[int] = None,
    ) -> WebhookDelivery:
        """Add a new delivery to the queue.  Returns the created record."""
        now = time.time()
        delivery = WebhookDelivery(
            delivery_id=str(uuid.uuid4()),
            webhook_id=webhook_id,
            url=url,
            event_type=event_type,
            payload_json=json.dumps(payload),
            secret=secret,
            attempt=0,
            max_attempts=max_attempts if max_attempts is not None else self._max_attempts,
            status="pending",
            created_at=now,
            next_retry_at=now,   # due immediately
            last_error=None,
            last_attempt_at=None,
            succeeded_at=None,
        )
        con = self._conn()
        con.execute(
            """
            INSERT INTO webhook_deliveries
                (delivery_id, webhook_id, url, event_type, payload_json, secret,
                 attempt, max_attempts, status, created_at, next_retry_at,
                 last_error, last_attempt_at, succeeded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delivery.delivery_id, delivery.webhook_id, delivery.url,
                delivery.event_type, delivery.payload_json, delivery.secret,
                delivery.attempt, delivery.max_attempts, delivery.status,
                delivery.created_at, delivery.next_retry_at,
                delivery.last_error, delivery.last_attempt_at, delivery.succeeded_at,
            ),
        )
        con.commit()
        return delivery

    # ── Fetch due ─────────────────────────────────────────────────────────────

    def due(self, now: Optional[float] = None, limit: int = 50) -> list[WebhookDelivery]:
        """Return pending deliveries whose ``next_retry_at`` ≤ *now*."""
        ts = now if now is not None else time.time()
        rows = self._conn().execute(
            """
            SELECT * FROM webhook_deliveries
            WHERE status = 'pending' AND next_retry_at <= ?
            ORDER BY next_retry_at ASC
            LIMIT ?
            """,
            (ts, limit),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    # ── Status updates ────────────────────────────────────────────────────────

    def mark_success(self, delivery_id: str) -> None:
        now = time.time()
        con = self._conn()
        con.execute(
            """
            UPDATE webhook_deliveries
            SET status='success', succeeded_at=?, last_attempt_at=?,
                attempt=attempt+1
            WHERE delivery_id=?
            """,
            (now, now, delivery_id),
        )
        con.commit()

    def mark_retry(self, delivery_id: str, error: str) -> bool:
        """Schedule the next retry using exponential backoff.

        Returns ``True`` if rescheduled, ``False`` if max_attempts exhausted
        (delivery is moved to dead-letter automatically).
        """
        now = time.time()
        con = self._conn()
        row = con.execute(
            "SELECT attempt, max_attempts FROM webhook_deliveries WHERE delivery_id=?",
            (delivery_id,),
        ).fetchone()
        if row is None:
            return False

        new_attempt = row["attempt"] + 1
        if new_attempt >= row["max_attempts"]:
            con.execute(
                """
                UPDATE webhook_deliveries
                SET status='dead', attempt=?, last_error=?, last_attempt_at=?
                WHERE delivery_id=?
                """,
                (new_attempt, error[:2000], now, delivery_id),
            )
            con.commit()
            return False

        # Pick backoff delay for this attempt index
        delay = _BACKOFF_S[min(new_attempt, len(_BACKOFF_S) - 1)]
        next_retry = now + delay
        con.execute(
            """
            UPDATE webhook_deliveries
            SET attempt=?, last_error=?, last_attempt_at=?,
                next_retry_at=?, status='pending'
            WHERE delivery_id=?
            """,
            (new_attempt, error[:2000], now, next_retry, delivery_id),
        )
        con.commit()
        return True

    def mark_dead(self, delivery_id: str, error: str = "") -> None:
        """Force a delivery to dead-letter status regardless of attempt count."""
        con = self._conn()
        con.execute(
            """
            UPDATE webhook_deliveries
            SET status='dead', last_error=?, last_attempt_at=?
            WHERE delivery_id=?
            """,
            (error[:2000], time.time(), delivery_id),
        )
        con.commit()

    # ── Replay ────────────────────────────────────────────────────────────────

    def replay(self, delivery_id: str) -> bool:
        """Reset a dead or failed delivery to pending so it is retried.

        Returns ``True`` if the delivery existed and was reset.
        """
        con = self._conn()
        cur = con.execute(
            """
            UPDATE webhook_deliveries
            SET status='pending', attempt=0, next_retry_at=?,
                last_error=NULL, succeeded_at=NULL
            WHERE delivery_id=? AND status IN ('dead', 'failed', 'pending')
            """,
            (time.time(), delivery_id),
        )
        con.commit()
        return cur.rowcount > 0

    # ── Queries ───────────────────────────────────────────────────────────────

    def get(self, delivery_id: str) -> Optional[WebhookDelivery]:
        row = self._conn().execute(
            "SELECT * FROM webhook_deliveries WHERE delivery_id=?", (delivery_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def pending(self, limit: int = 100) -> list[WebhookDelivery]:
        rows = self._conn().execute(
            "SELECT * FROM webhook_deliveries WHERE status='pending' ORDER BY next_retry_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def dead_letters(self, limit: int = 100) -> list[WebhookDelivery]:
        rows = self._conn().execute(
            "SELECT * FROM webhook_deliveries WHERE status='dead' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def for_webhook(self, webhook_id: str, limit: int = 50) -> list[WebhookDelivery]:
        rows = self._conn().execute(
            """
            SELECT * FROM webhook_deliveries WHERE webhook_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (webhook_id, limit),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def counts(self) -> dict[str, int]:
        """Return {status: count} for all statuses."""
        rows = self._conn().execute(
            "SELECT status, COUNT(*) as n FROM webhook_deliveries GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def delete(self, delivery_id: str) -> bool:
        con = self._conn()
        cur = con.execute(
            "DELETE FROM webhook_deliveries WHERE delivery_id=?", (delivery_id,)
        )
        con.commit()
        return cur.rowcount > 0

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WebhookDelivery:
        d = dict(row)
        return WebhookDelivery(
            delivery_id=d["delivery_id"],
            webhook_id=d["webhook_id"],
            url=d["url"],
            event_type=d["event_type"],
            payload_json=d["payload_json"],
            secret=d["secret"],
            attempt=d["attempt"],
            max_attempts=d["max_attempts"],
            status=d["status"],
            created_at=d["created_at"],
            next_retry_at=d["next_retry_at"],
            last_error=d["last_error"],
            last_attempt_at=d["last_attempt_at"],
            succeeded_at=d["succeeded_at"],
        )


# ── Reliable deliverer ────────────────────────────────────────────────────────

class WebhookReliableDeliverer:
    """Background thread that processes the ``WebhookRetryQueue``.

    On each poll tick it:
    1. Fetches all due pending deliveries.
    2. Attempts an HTTP POST for each (HMAC-signed).
    3. On 2xx: marks success.
    4. On failure: calls ``mark_retry()`` which applies backoff or promotes to
       dead-letter when ``max_attempts`` is exhausted.

    Parameters
    ----------
    queue:    The ``WebhookRetryQueue`` to process.
    poll_s:   Polling interval in seconds (default 30).
    """

    def __init__(self, queue: WebhookRetryQueue, poll_s: float = 30.0) -> None:
        self._queue  = queue
        self._poll_s = poll_s
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="webhook-deliverer"
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def __enter__(self) -> "WebhookReliableDeliverer":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ── Delivery loop ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.wait(timeout=self._poll_s):
            self._tick()

    def _tick(self, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.time()
        due = self._queue.due(now=ts)
        for delivery in due:
            self._attempt(delivery)

    def _attempt(self, delivery: WebhookDelivery) -> None:
        body = json.dumps({
            "event":       delivery.event_type,
            "delivery_id": delivery.delivery_id,
            "attempt":     delivery.attempt + 1,
            "payload":     delivery.payload,
        }).encode()

        sig = self._sign(delivery.secret, body)
        headers = {
            "Content-Type":          "application/json",
            "X-MeshFlow-Event":      delivery.event_type,
            "X-MeshFlow-Signature":  sig,
            "X-MeshFlow-Delivery":   delivery.delivery_id,
        }

        try:
            req = urllib.request.Request(
                delivery.url, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=_DELIVERY_TIMEOUT) as resp:
                if 200 <= resp.status < 300:
                    self._queue.mark_success(delivery.delivery_id)
                    return
                error = f"HTTP {resp.status}"
        except Exception as exc:
            error = str(exc)[:500]

        self._queue.mark_retry(delivery.delivery_id, error)

    @staticmethod
    def _sign(secret: str, body: bytes) -> str:
        key = (secret or "unsigned").encode()
        return hmac.new(key, body, hashlib.sha256).hexdigest()

    # ── Manual trigger ────────────────────────────────────────────────────────

    def flush(self) -> int:
        """Process all currently-due deliveries synchronously.

        Returns the number of deliveries processed.  Useful for tests.
        """
        due = self._queue.due()
        for d in due:
            self._attempt(d)
        return len(due)
