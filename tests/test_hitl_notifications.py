"""Tests for HITLNotifier and HITLTimeoutWatcher (meshflow/core/hitl.py)."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestHITLNotifier:
    def test_import(self) -> None:
        from meshflow.core.hitl import HITLNotifier
        assert HITLNotifier is not None

    @pytest.mark.asyncio
    async def test_empty_webhook_url_returns_false_immediately(self) -> None:
        from meshflow.core.hitl import HITLNotifier
        notifier = HITLNotifier(webhook_url="")
        result = await notifier.notify(run_id="r1", node_id="n1", context={})
        assert result is False

    @pytest.mark.asyncio
    async def test_sends_webhook_on_notify(self) -> None:
        from meshflow.core.hitl import HITLNotifier

        notifier = HITLNotifier(
            webhook_url="https://hooks.example.com/mesh",
            secret="test-secret",
        )

        mock_response = MagicMock()
        mock_response.is_success = True

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await notifier.notify(
                run_id="run-abc",
                node_id="approval",
                context={"task": "Review contract"},
                base_url="https://api.example.com",
            )

        assert result is True
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "https://hooks.example.com/mesh" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_webhook_includes_hmac_signature(self) -> None:
        from meshflow.core.hitl import HITLNotifier

        notifier = HITLNotifier(
            webhook_url="https://hooks.example.com/mesh",
            secret="my-secret",
        )

        captured_headers: dict = {}

        async def _fake_post(url: str, content: bytes, headers: dict) -> Any:
            captured_headers.update(headers)
            resp = MagicMock()
            resp.is_success = True
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _fake_post  # type: ignore

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notifier.notify(run_id="r1", node_id="n1", context={})

        assert "X-MeshFlow-Signature" in captured_headers
        assert captured_headers["X-MeshFlow-Signature"].startswith("sha256=")

    @pytest.mark.asyncio
    async def test_webhook_payload_contains_run_id(self) -> None:
        from meshflow.core.hitl import HITLNotifier
        import json

        notifier = HITLNotifier(webhook_url="https://hooks.example.com/mesh")

        captured_body: dict = {}

        async def _fake_post(url: str, content: bytes, headers: dict) -> Any:
            captured_body.update(json.loads(content))
            resp = MagicMock()
            resp.is_success = True
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _fake_post  # type: ignore

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notifier.notify(run_id="test-run-42", node_id="approval", context={"k": "v"})

        assert captured_body["run_id"] == "test-run-42"
        assert "expires_at" in captured_body

    @pytest.mark.asyncio
    async def test_approve_reject_urls_included_when_base_url_set(self) -> None:
        from meshflow.core.hitl import HITLNotifier
        import json

        notifier = HITLNotifier(
            webhook_url="https://hooks.example.com/mesh",
            server_base_url="https://api.mycompany.com",
        )
        captured: dict = {}

        async def _fake_post(url, content, headers):
            captured.update(json.loads(content))
            resp = MagicMock(); resp.is_success = True; return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _fake_post  # type: ignore

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notifier.notify(run_id="r1", node_id="n1", context={})

        assert "api.mycompany.com" in captured.get("approve_url", "")
        assert "api.mycompany.com" in captured.get("reject_url", "")

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self) -> None:
        from meshflow.core.hitl import HITLNotifier

        notifier = HITLNotifier(webhook_url="https://hooks.example.com/mesh")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await notifier.notify(run_id="r1", node_id="n1", context={})
        assert result is False


class TestHITLTimeoutWatcher:
    def test_import(self) -> None:
        from meshflow.core.hitl import HITLTimeoutWatcher
        assert HITLTimeoutWatcher is not None

    @pytest.mark.asyncio
    async def test_auto_rejects_expired_run(self) -> None:
        from meshflow.core.hitl import HITLTimeoutWatcher
        from datetime import datetime, timezone, timedelta

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        checkpoint = {"approved": None, "context": {}, "paused_at_node": "review"}

        mock_ledger = AsyncMock()
        mock_ledger.list_paused_runs = AsyncMock(return_value=[
            {"run_id": "old-run", "paused_at": old_ts}
        ])
        mock_ledger.load_checkpoint_data = AsyncMock(return_value=checkpoint)
        mock_ledger.save_checkpoint = AsyncMock()

        watcher = HITLTimeoutWatcher(
            ledger=mock_ledger, timeout_s=3600, on_timeout="reject"
        )
        await watcher._check()

        mock_ledger.save_checkpoint.assert_called_once()
        saved = mock_ledger.save_checkpoint.call_args[0][1]
        assert saved["approved"] is False

    @pytest.mark.asyncio
    async def test_auto_approves_when_configured(self) -> None:
        from meshflow.core.hitl import HITLTimeoutWatcher
        from datetime import datetime, timezone, timedelta

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        checkpoint = {"approved": None, "context": {}}

        mock_ledger = AsyncMock()
        mock_ledger.list_paused_runs = AsyncMock(return_value=[
            {"run_id": "run-2", "paused_at": old_ts}
        ])
        mock_ledger.load_checkpoint_data = AsyncMock(return_value=checkpoint)
        mock_ledger.save_checkpoint = AsyncMock()

        watcher = HITLTimeoutWatcher(
            ledger=mock_ledger, timeout_s=3600, on_timeout="approve"
        )
        await watcher._check()

        saved = mock_ledger.save_checkpoint.call_args[0][1]
        assert saved["approved"] is True

    @pytest.mark.asyncio
    async def test_recent_run_not_touched(self) -> None:
        from meshflow.core.hitl import HITLTimeoutWatcher
        from datetime import datetime, timezone, timedelta

        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

        mock_ledger = AsyncMock()
        mock_ledger.list_paused_runs = AsyncMock(return_value=[
            {"run_id": "fresh-run", "paused_at": recent_ts}
        ])
        mock_ledger.load_checkpoint_data = AsyncMock()
        mock_ledger.save_checkpoint = AsyncMock()

        watcher = HITLTimeoutWatcher(ledger=mock_ledger, timeout_s=86400)
        await watcher._check()

        mock_ledger.save_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_reviewed_not_touched(self) -> None:
        from meshflow.core.hitl import HITLTimeoutWatcher
        from datetime import datetime, timezone, timedelta

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        # Already approved
        checkpoint = {"approved": True}

        mock_ledger = AsyncMock()
        mock_ledger.list_paused_runs = AsyncMock(return_value=[
            {"run_id": "reviewed-run", "paused_at": old_ts}
        ])
        mock_ledger.load_checkpoint_data = AsyncMock(return_value=checkpoint)
        mock_ledger.save_checkpoint = AsyncMock()

        watcher = HITLTimeoutWatcher(ledger=mock_ledger, timeout_s=3600)
        await watcher._check()

        mock_ledger.save_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_escalate_sends_second_webhook(self) -> None:
        from meshflow.core.hitl import HITLTimeoutWatcher, HITLNotifier
        from datetime import datetime, timezone, timedelta

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        checkpoint = {"approved": None, "context": {}, "paused_at_node": "review"}

        mock_ledger = AsyncMock()
        mock_ledger.list_paused_runs = AsyncMock(return_value=[
            {"run_id": "esc-run", "paused_at": old_ts}
        ])
        mock_ledger.load_checkpoint_data = AsyncMock(return_value=checkpoint)
        mock_ledger.save_checkpoint = AsyncMock()

        mock_notifier = AsyncMock(spec=HITLNotifier)
        mock_notifier.notify = AsyncMock(return_value=True)

        watcher = HITLTimeoutWatcher(
            ledger=mock_ledger,
            timeout_s=3600,
            on_timeout="escalate",
            notifier=mock_notifier,
        )
        await watcher._check()

        mock_notifier.notify.assert_called_once()
        call_ctx = mock_notifier.notify.call_args[1]["context"]
        assert call_ctx.get("escalation") is True
