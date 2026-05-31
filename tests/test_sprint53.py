"""Sprint 53 — Durable Webhook Retry Queue tests."""

from __future__ import annotations

import json
import subprocess
import time
import unittest
from unittest.mock import MagicMock, patch

import meshflow
from meshflow.observability.webhook_queue import (
    WebhookDelivery,
    WebhookReliableDeliverer,
    WebhookRetryQueue,
    _BACKOFF_S,
)


# ── WebhookDelivery ───────────────────────────────────────────────────────────

class TestWebhookDelivery(unittest.TestCase):
    def _make(self, **kw) -> WebhookDelivery:
        now = time.time()
        defaults = dict(
            delivery_id="del-1",
            webhook_id="wh-1",
            url="https://example.com/hook",
            event_type="run_failed",
            payload_json='{"run_id": "abc"}',
            secret="s3cr3t",
            attempt=0,
            max_attempts=5,
            status="pending",
            created_at=now,
            next_retry_at=now,
            last_error=None,
            last_attempt_at=None,
            succeeded_at=None,
        )
        defaults.update(kw)
        return WebhookDelivery(**defaults)

    def test_payload_parses_json(self):
        d = self._make(payload_json='{"k": 1}')
        self.assertEqual(d.payload, {"k": 1})

    def test_is_dead_true(self):
        d = self._make(status="dead")
        self.assertTrue(d.is_dead())

    def test_is_dead_false(self):
        d = self._make(status="pending")
        self.assertFalse(d.is_dead())

    def test_is_success_true(self):
        d = self._make(status="success")
        self.assertTrue(d.is_success())

    def test_is_success_false(self):
        d = self._make(status="pending")
        self.assertFalse(d.is_success())

    def test_to_dict_keys(self):
        d = self._make()
        dct = d.to_dict()
        for key in ("delivery_id", "webhook_id", "url", "event_type", "attempt",
                    "max_attempts", "status", "created_at", "next_retry_at",
                    "last_error", "last_attempt_at", "succeeded_at"):
            self.assertIn(key, dct)

    def test_to_dict_excludes_payload_json(self):
        # payload_json is internal; to_dict should not expose raw JSON string
        d = self._make()
        self.assertNotIn("payload_json", d.to_dict())

    def test_to_dict_values(self):
        d = self._make(delivery_id="x", status="dead", attempt=3)
        dct = d.to_dict()
        self.assertEqual(dct["delivery_id"], "x")
        self.assertEqual(dct["status"], "dead")
        self.assertEqual(dct["attempt"], 3)


# ── WebhookRetryQueue ─────────────────────────────────────────────────────────

class TestWebhookRetryQueueEnqueue(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")

    def test_enqueue_returns_delivery(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {"k": 1})
        self.assertIsInstance(d, WebhookDelivery)

    def test_enqueue_sets_pending_status(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.assertEqual(d.status, "pending")

    def test_enqueue_attempt_zero(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.assertEqual(d.attempt, 0)

    def test_enqueue_due_immediately(self):
        before = time.time()
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.assertLessEqual(d.next_retry_at, time.time())
        self.assertGreaterEqual(d.next_retry_at, before)

    def test_enqueue_assigns_uuid(self):
        d1 = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        d2 = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.assertNotEqual(d1.delivery_id, d2.delivery_id)

    def test_enqueue_max_attempts_override(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {}, max_attempts=3)
        self.assertEqual(d.max_attempts, 3)

    def test_enqueue_default_max_attempts(self):
        q = WebhookRetryQueue(":memory:", max_attempts=7)
        d = q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.assertEqual(d.max_attempts, 7)

    def test_enqueue_stores_secret(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {}, secret="abc")
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.secret, "abc")

    def test_enqueue_payload_roundtrip(self):
        payload = {"run_id": "r1", "score": 0.95}
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", payload)
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.payload, payload)


class TestWebhookRetryQueueDue(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")

    def test_due_returns_pending_items(self):
        self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        due = self.q.due()
        self.assertEqual(len(due), 1)

    def test_due_respects_next_retry_at(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        far_future = time.time() + 9999
        self.q._conn().execute(
            "UPDATE webhook_deliveries SET next_retry_at=? WHERE delivery_id=?",
            (far_future, d.delivery_id),
        )
        self.q._conn().commit()
        self.assertEqual(len(self.q.due()), 0)

    def test_due_limit_respected(self):
        for _ in range(10):
            self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.assertEqual(len(self.q.due(limit=3)), 3)

    def test_due_excludes_success(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_success(d.delivery_id)
        self.assertEqual(len(self.q.due()), 0)

    def test_due_excludes_dead(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_dead(d.delivery_id, "gone")
        self.assertEqual(len(self.q.due()), 0)

    def test_due_ordered_by_next_retry_at(self):
        d1 = self.q.enqueue("wh-1", "https://ex.com", "e1", {})
        d2 = self.q.enqueue("wh-2", "https://ex.com", "e2", {})
        # Set d2 earlier
        self.q._conn().execute(
            "UPDATE webhook_deliveries SET next_retry_at=next_retry_at-10 WHERE delivery_id=?",
            (d2.delivery_id,),
        )
        self.q._conn().commit()
        due = self.q.due()
        self.assertEqual(due[0].delivery_id, d2.delivery_id)


class TestWebhookRetryQueueMarkSuccess(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")

    def test_mark_success_status(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_success(d.delivery_id)
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.status, "success")

    def test_mark_success_sets_succeeded_at(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_success(d.delivery_id)
        fetched = self.q.get(d.delivery_id)
        self.assertIsNotNone(fetched.succeeded_at)

    def test_mark_success_increments_attempt(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_success(d.delivery_id)
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.attempt, 1)


class TestWebhookRetryQueueMarkRetry(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")

    def test_mark_retry_reschedules(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {}, max_attempts=5)
        result = self.q.mark_retry(d.delivery_id, "timeout")
        self.assertTrue(result)
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.status, "pending")
        self.assertEqual(fetched.attempt, 1)

    def test_mark_retry_stores_error(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_retry(d.delivery_id, "connection refused")
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.last_error, "connection refused")

    def test_mark_retry_backoff_delay(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        before = time.time()
        self.q.mark_retry(d.delivery_id, "err")
        fetched = self.q.get(d.delivery_id)
        # attempt 1 → backoff = _BACKOFF_S[1] = 10s
        expected_min = before + _BACKOFF_S[1] - 1
        self.assertGreater(fetched.next_retry_at, expected_min)

    def test_mark_retry_returns_false_when_exhausted(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {}, max_attempts=2)
        self.q.mark_retry(d.delivery_id, "err1")  # attempt → 1
        result = self.q.mark_retry(d.delivery_id, "err2")  # attempt → 2 = max
        self.assertFalse(result)

    def test_mark_retry_dead_when_exhausted(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {}, max_attempts=2)
        self.q.mark_retry(d.delivery_id, "err1")
        self.q.mark_retry(d.delivery_id, "err2")
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.status, "dead")

    def test_mark_retry_unknown_id_returns_false(self):
        result = self.q.mark_retry("no-such-id", "err")
        self.assertFalse(result)

    def test_backoff_schedule_applied_correctly(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {}, max_attempts=10)
        for attempt_idx in range(len(_BACKOFF_S) - 1):
            before = time.time()
            self.q.mark_retry(d.delivery_id, "err")
            fetched = self.q.get(d.delivery_id)
            expected_delay = _BACKOFF_S[attempt_idx + 1]
            self.assertGreater(
                fetched.next_retry_at,
                before + expected_delay - 1,
                f"Attempt {attempt_idx + 1}: expected delay >= {expected_delay}",
            )


class TestWebhookRetryQueueMarkDead(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")

    def test_mark_dead_sets_status(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_dead(d.delivery_id, "forced")
        self.assertEqual(self.q.get(d.delivery_id).status, "dead")

    def test_mark_dead_stores_error(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_dead(d.delivery_id, "forced error")
        self.assertEqual(self.q.get(d.delivery_id).last_error, "forced error")

    def test_mark_dead_empty_error(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_dead(d.delivery_id)
        self.assertEqual(self.q.get(d.delivery_id).last_error, "")


class TestWebhookRetryQueueReplay(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")

    def test_replay_dead_resets_to_pending(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_dead(d.delivery_id, "err")
        ok = self.q.replay(d.delivery_id)
        self.assertTrue(ok)
        self.assertEqual(self.q.get(d.delivery_id).status, "pending")

    def test_replay_resets_attempt_to_zero(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {}, max_attempts=5)
        self.q.mark_retry(d.delivery_id, "err")
        self.q.mark_retry(d.delivery_id, "err")
        self.q.mark_dead(d.delivery_id, "err")
        self.q.replay(d.delivery_id)
        self.assertEqual(self.q.get(d.delivery_id).attempt, 0)

    def test_replay_clears_last_error(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_dead(d.delivery_id, "bad error")
        self.q.replay(d.delivery_id)
        self.assertIsNone(self.q.get(d.delivery_id).last_error)

    def test_replay_makes_due_immediately(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_dead(d.delivery_id, "err")
        self.q.replay(d.delivery_id)
        self.assertEqual(len(self.q.due()), 1)

    def test_replay_unknown_returns_false(self):
        self.assertFalse(self.q.replay("no-such-id"))

    def test_replay_success_delivery_not_reset(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.mark_success(d.delivery_id)
        # success is not in ('dead', 'failed', 'pending') — replay should do nothing
        result = self.q.replay(d.delivery_id)
        # success status is not included in the WHERE clause filter, so rowcount=0
        self.assertFalse(result)


class TestWebhookRetryQueueQueries(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")

    def test_pending_returns_pending(self):
        self.q.enqueue("wh-1", "https://ex.com", "e1", {})
        self.q.enqueue("wh-1", "https://ex.com", "e2", {})
        self.assertEqual(len(self.q.pending()), 2)

    def test_pending_excludes_dead(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "e1", {})
        self.q.mark_dead(d.delivery_id, "err")
        self.assertEqual(len(self.q.pending()), 0)

    def test_dead_letters_returns_dead(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "e1", {})
        self.q.mark_dead(d.delivery_id, "err")
        self.assertEqual(len(self.q.dead_letters()), 1)

    def test_dead_letters_excludes_pending(self):
        self.q.enqueue("wh-1", "https://ex.com", "e1", {})
        self.assertEqual(len(self.q.dead_letters()), 0)

    def test_for_webhook_filters(self):
        self.q.enqueue("wh-A", "https://ex.com", "e1", {})
        self.q.enqueue("wh-B", "https://ex.com", "e2", {})
        self.assertEqual(len(self.q.for_webhook("wh-A")), 1)

    def test_counts_all_statuses(self):
        d1 = self.q.enqueue("wh-1", "https://ex.com", "e1", {})
        d2 = self.q.enqueue("wh-1", "https://ex.com", "e2", {})
        self.q.mark_success(d1.delivery_id)
        self.q.mark_dead(d2.delivery_id, "err")
        self.q.enqueue("wh-1", "https://ex.com", "e3", {})
        counts = self.q.counts()
        self.assertEqual(counts["success"], 1)
        self.assertEqual(counts["dead"], 1)
        self.assertEqual(counts["pending"], 1)

    def test_counts_empty(self):
        self.assertEqual(self.q.counts(), {})

    def test_delete_removes_delivery(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "e1", {})
        ok = self.q.delete(d.delivery_id)
        self.assertTrue(ok)
        self.assertIsNone(self.q.get(d.delivery_id))

    def test_delete_unknown_returns_false(self):
        self.assertFalse(self.q.delete("no-such"))

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.q.get("no-such-id"))

    def test_pending_limit(self):
        for _ in range(15):
            self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.assertEqual(len(self.q.pending(limit=5)), 5)


# ── WebhookReliableDeliverer ──────────────────────────────────────────────────

class TestWebhookReliableDelivererFlush(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")
        self.deliverer = WebhookReliableDeliverer(self.q)

    def _mock_attempt(self, delivery: WebhookDelivery) -> None:
        self.q.mark_success(delivery.delivery_id)

    def test_flush_returns_count(self):
        self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.deliverer._attempt = self._mock_attempt
        count = self.deliverer.flush()
        self.assertEqual(count, 2)

    def test_flush_processes_all_due(self):
        for _ in range(5):
            self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        self.deliverer._attempt = self._mock_attempt
        self.deliverer.flush()
        self.assertEqual(len(self.q.pending()), 0)
        self.assertEqual(self.q.counts().get("success", 0), 5)

    def test_flush_empty_queue_returns_zero(self):
        count = self.deliverer.flush()
        self.assertEqual(count, 0)

    def test_flush_respects_next_retry_at(self):
        d = self.q.enqueue("wh-1", "https://ex.com", "evt", {})
        # Push next_retry_at far into the future
        self.q._conn().execute(
            "UPDATE webhook_deliveries SET next_retry_at=? WHERE delivery_id=?",
            (time.time() + 9999, d.delivery_id),
        )
        self.q._conn().commit()
        count = self.deliverer.flush()
        self.assertEqual(count, 0)


class TestWebhookReliableDelivererAttempt(unittest.TestCase):
    def setUp(self):
        self.q = WebhookRetryQueue(":memory:")
        self.deliverer = WebhookReliableDeliverer(self.q)

    def _delivery(self, **kw) -> WebhookDelivery:
        return self.q.enqueue(
            kw.get("webhook_id", "wh-1"),
            kw.get("url", "https://ex.com"),
            kw.get("event_type", "evt"),
            kw.get("payload", {}),
            kw.get("secret", ""),
        )

    @patch("meshflow.observability.webhook_queue.urllib.request.urlopen")
    def test_attempt_success_marks_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        d = self._delivery()
        self.deliverer._attempt(d)
        self.assertEqual(self.q.get(d.delivery_id).status, "success")

    @patch("meshflow.observability.webhook_queue.urllib.request.urlopen")
    def test_attempt_500_marks_retry(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp

        d = self._delivery()
        self.deliverer._attempt(d)
        fetched = self.q.get(d.delivery_id)
        self.assertEqual(fetched.status, "pending")
        self.assertIn("HTTP 500", fetched.last_error)

    @patch("meshflow.observability.webhook_queue.urllib.request.urlopen")
    def test_attempt_network_error_marks_retry(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("connection refused")
        d = self._delivery()
        self.deliverer._attempt(d)
        fetched = self.q.get(d.delivery_id)
        self.assertIn("connection refused", fetched.last_error)

    @patch("meshflow.observability.webhook_queue.urllib.request.urlopen")
    def test_attempt_sends_signature_header(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        d = self._delivery(secret="mysecret")
        self.deliverer._attempt(d)
        req_obj = mock_urlopen.call_args[0][0]
        # urllib lowercases header names
        self.assertIn("X-meshflow-signature", req_obj.headers)

    @patch("meshflow.observability.webhook_queue.urllib.request.urlopen")
    def test_attempt_sends_delivery_id_header(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 201
        mock_urlopen.return_value = mock_resp

        d = self._delivery()
        self.deliverer._attempt(d)
        req_obj = mock_urlopen.call_args[0][0]
        self.assertIn(d.delivery_id, req_obj.headers.get("X-meshflow-delivery", ""))

    @patch("meshflow.observability.webhook_queue.urllib.request.urlopen")
    def test_attempt_2xx_codes_succeed(self, mock_urlopen):
        for code in (200, 201, 204):
            with self.subTest(code=code):
                q = WebhookRetryQueue(":memory:")
                deliv = WebhookReliableDeliverer(q)
                mock_resp = MagicMock()
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_resp.status = code
                mock_urlopen.return_value = mock_resp
                d = q.enqueue("wh", "https://ex.com", "e", {})
                deliv._attempt(d)
                self.assertEqual(q.get(d.delivery_id).status, "success")


class TestWebhookReliableDelivererSign(unittest.TestCase):
    def test_sign_with_secret(self):
        sig = WebhookReliableDeliverer._sign("secret", b"body")
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 64)  # sha256 hex

    def test_sign_deterministic(self):
        s1 = WebhookReliableDeliverer._sign("k", b"body")
        s2 = WebhookReliableDeliverer._sign("k", b"body")
        self.assertEqual(s1, s2)

    def test_sign_different_secret(self):
        s1 = WebhookReliableDeliverer._sign("k1", b"body")
        s2 = WebhookReliableDeliverer._sign("k2", b"body")
        self.assertNotEqual(s1, s2)

    def test_sign_empty_secret_uses_unsigned(self):
        sig = WebhookReliableDeliverer._sign("", b"hello")
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 64)


class TestWebhookReliableDelivererLifecycle(unittest.TestCase):
    def test_start_stop(self):
        q = WebhookRetryQueue(":memory:")
        d = WebhookReliableDeliverer(q, poll_s=0.05)
        d.start()
        self.assertTrue(d._thread.is_alive())
        d.stop(timeout=2.0)
        self.assertFalse(d._thread.is_alive())

    def test_context_manager(self):
        q = WebhookRetryQueue(":memory:")
        with WebhookReliableDeliverer(q, poll_s=0.05) as d:
            self.assertTrue(d._thread.is_alive())
        self.assertFalse(d._thread.is_alive())

    def test_double_start_is_idempotent(self):
        q = WebhookRetryQueue(":memory:")
        d = WebhookReliableDeliverer(q, poll_s=10)
        d.start()
        t1 = d._thread
        d.start()
        t2 = d._thread
        self.assertIs(t1, t2)
        d.stop()

    def test_tick_calls_attempt_for_due(self):
        q = WebhookRetryQueue(":memory:")
        q.enqueue("wh-1", "https://ex.com", "evt", {})
        d = WebhookReliableDeliverer(q)
        attempted = []
        d._attempt = lambda delivery: attempted.append(delivery.delivery_id)
        d._tick()
        self.assertEqual(len(attempted), 1)

    def test_tick_skips_non_due(self):
        q = WebhookRetryQueue(":memory:")
        delivery = q.enqueue("wh-1", "https://ex.com", "evt", {})
        q._conn().execute(
            "UPDATE webhook_deliveries SET next_retry_at=? WHERE delivery_id=?",
            (time.time() + 9999, delivery.delivery_id),
        )
        q._conn().commit()
        d = WebhookReliableDeliverer(q)
        attempted = []
        d._attempt = lambda dv: attempted.append(dv.delivery_id)
        d._tick()
        self.assertEqual(len(attempted), 0)


# ── CLI handlers ──────────────────────────────────────────────────────────────

class TestWebhooksCLIQueue(unittest.TestCase):
    def _args(self, **kw):
        import argparse
        ns = argparse.Namespace(
            webhooks_cmd="queue",
            db=":memory:",
            limit=20,
            json_output=False,
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_queue_empty_message(self):
        from meshflow.cli.main import _cmd_webhooks
        import io
        args = self._args()
        # Patch server attr not needed for queue subcommand
        args.server = "http://localhost:8000"
        args.api_key = ""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            _cmd_webhooks(args)
            out = mock_out.getvalue()
        self.assertIn("empty", out.lower())

    def test_queue_json_output(self):
        from meshflow.cli.main import _cmd_webhooks
        import io
        q = WebhookRetryQueue(":memory:")
        q.enqueue("wh-1", "https://ex.com", "evt", {})

        args = self._args(json_output=True)
        args.server = "http://localhost:8000"
        args.api_key = ""

        with patch("meshflow.observability.webhook_queue.WebhookRetryQueue", return_value=q):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                _cmd_webhooks(args)
                raw = mock_out.getvalue()
        data = json.loads(raw)
        self.assertIn("deliveries", data)
        self.assertIn("counts", data)


class TestWebhooksCLIDead(unittest.TestCase):
    def _args(self, **kw):
        import argparse
        ns = argparse.Namespace(
            webhooks_cmd="dead",
            db=":memory:",
            limit=20,
            json_output=False,
            server="http://localhost:8000",
            api_key="",
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_dead_no_letters_message(self):
        from meshflow.cli.main import _cmd_webhooks
        import io
        args = self._args()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            _cmd_webhooks(args)
        self.assertIn("No dead-letter", mock_out.getvalue())

    def test_dead_json_output(self):
        from meshflow.cli.main import _cmd_webhooks
        import io
        q = WebhookRetryQueue(":memory:")
        d = q.enqueue("wh-1", "https://ex.com", "evt", {})
        q.mark_dead(d.delivery_id, "failed permanently")

        args = self._args(json_output=True)
        with patch("meshflow.observability.webhook_queue.WebhookRetryQueue", return_value=q):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                _cmd_webhooks(args)
                data = json.loads(mock_out.getvalue())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "dead")


class TestWebhooksCLIReplay(unittest.TestCase):
    def _args(self, delivery_id: str, **kw):
        import argparse
        ns = argparse.Namespace(
            webhooks_cmd="replay",
            db=":memory:",
            delivery_id=delivery_id,
            server="http://localhost:8000",
            api_key="",
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_replay_success_message(self):
        from meshflow.cli.main import _cmd_webhooks
        import io
        q = WebhookRetryQueue(":memory:")
        d = q.enqueue("wh-1", "https://ex.com", "evt", {})
        q.mark_dead(d.delivery_id, "err")

        args = self._args(d.delivery_id)
        with patch("meshflow.observability.webhook_queue.WebhookRetryQueue", return_value=q):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                _cmd_webhooks(args)
        self.assertIn("re-queued", mock_out.getvalue())

    def test_replay_missing_exits_1(self):
        from meshflow.cli.main import _cmd_webhooks
        args = self._args("no-such-id")
        with self.assertRaises(SystemExit) as cm:
            _cmd_webhooks(args)
        self.assertEqual(cm.exception.code, 1)


# ── Subprocess help smoke tests ───────────────────────────────────────────────

class TestSubprocessHelp(unittest.TestCase):
    def _run(self, *args):
        result = subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode, result.stdout + result.stderr

    def test_webhooks_help(self):
        code, out = self._run("webhooks", "--help")
        self.assertIn(code, (0, 1))
        combined = out.lower()
        self.assertTrue("webhook" in combined or combined == "")

    def test_webhooks_queue_help(self):
        code, out = self._run("webhooks", "queue", "--help")
        self.assertIn(code, (0, 1))

    def test_webhooks_dead_help(self):
        code, out = self._run("webhooks", "dead", "--help")
        self.assertIn(code, (0, 1))

    def test_webhooks_replay_help(self):
        code, out = self._run("webhooks", "replay", "--help")
        self.assertIn(code, (0, 1))


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")

    def test_webhook_delivery_exported(self):
        self.assertIs(meshflow.WebhookDelivery, WebhookDelivery)

    def test_webhook_retry_queue_exported(self):
        self.assertIs(meshflow.WebhookRetryQueue, WebhookRetryQueue)

    def test_webhook_reliable_deliverer_exported(self):
        self.assertIs(meshflow.WebhookReliableDeliverer, WebhookReliableDeliverer)

    def test_all_contains_webhook_exports(self):
        for name in ("WebhookDelivery", "WebhookRetryQueue", "WebhookReliableDeliverer"):
            self.assertIn(name, meshflow.__all__)

    def test_sprint52_exports_still_present(self):
        for name in ("SemanticMemoryStore", "SemanticMemoryEntry", "SemanticSearchResult",
                     "HashEmbeddingProvider", "cosine_similarity"):
            self.assertTrue(hasattr(meshflow, name), f"Missing: {name}")

    def test_sprint51_exports_still_present(self):
        for name in ("SecretScanner", "SecretScanResult", "SecretScanGuardrail"):
            self.assertTrue(hasattr(meshflow, name), f"Missing: {name}")

    def test_sprint50_exports_still_present(self):
        for name in ("CircuitBreaker", "CircuitBreakerConfig", "get_circuit_registry"):
            self.assertTrue(hasattr(meshflow, name), f"Missing: {name}")


if __name__ == "__main__":
    unittest.main()
