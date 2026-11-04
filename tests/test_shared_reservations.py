"""Cross-process accounting and crash behavior for durable request holds."""

from __future__ import annotations

import subprocess
import sys

import pytest

from meshflow import Policy
from meshflow.budget.reservations import ReservationStore
from meshflow.core.policy import BudgetExceededError, BudgetTracker


WORKER = r"""
import sys
from meshflow import Policy
from meshflow.core.policy import BudgetTracker, BudgetExceededError
tracker = BudgetTracker(Policy(strict_budget=True, budget_usd=.1, budget_tokens=1000,
    shared_budget_db=sys.argv[1], shared_budget_id="workers"))
sys.stdin.readline()
try:
    key = tracker.reserve(.04, 100)
except BudgetExceededError:
    print("blocked", flush=True)
else:
    print("reserved", flush=True)
    sys.stdin.readline()
    tracker.settle(key, .03, 80)
"""


def test_workers_cannot_reserve_the_same_remaining_capacity(tmp_path):
    path = str(tmp_path / "shared.db")
    store = ReservationStore(path, "workers", 0.1, 1000)
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(5)
    ]
    try:
        for worker in workers:
            worker.stdin.write("start\n")
            worker.stdin.flush()
        results = [worker.stdout.readline().strip() for worker in workers]
        assert results.count("reserved") == 2, results
        assert results.count("blocked") == 3, results
        assert store.summary()["reserved_usd"] == 0.08
        for worker, result in zip(workers, results):
            if result == "reserved":
                worker.stdin.write("settle\n")
                worker.stdin.flush()
        for worker in workers:
            _, stderr = worker.communicate(timeout=10)
            assert worker.returncode == 0, stderr
        summary = store.summary()
        assert summary["spent_usd"] == 0.06 and summary["spent_tokens"] == 160
        assert summary["reserved_usd"] == 0 and summary["pending"] == 0
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)


def test_process_crash_keeps_hold_until_explicit_reconciliation(tmp_path):
    path = str(tmp_path / "crash.db")
    script = r"""
import os, sys
from meshflow.budget.reservations import ReservationStore
store = ReservationStore(sys.argv[1], "crash", .05, 100)
print(store.reserve(.04, 80), flush=True)
os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, path], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 17
    key = result.stdout.strip()
    store = ReservationStore(path, "crash", 0.05, 100)
    assert store.summary()["pending"] == 1
    with pytest.raises(BudgetExceededError):
        store.reserve(0.02, 30)
    store.settle(key)  # Unknown billable outcome: retain the complete bound as spend.
    store.settle(key)  # Recovery retries must not debit twice.
    assert store.summary()["spent_usd"] == 0.04
    assert store.summary()["pending"] == 0
    with pytest.raises(ValueError):
        store.release(key)


def test_scopes_are_independent_and_limits_cannot_be_silently_changed(tmp_path):
    path = str(tmp_path / "scopes.db")
    first = ReservationStore(path, "tenant-a", 0.1, 100)
    second = ReservationStore(path, "tenant-b", 0.1, 100)
    first.reserve(0.1, 100)
    assert second.reserve(0.1, 100)
    with pytest.raises(ValueError, match="different limits"):
        ReservationStore(path, "tenant-a", 100, 10000)


def test_settlement_is_idempotent_and_overruns_block_future_dispatch(tmp_path):
    store = ReservationStore(str(tmp_path / "overrun.db"), "scope", 0.1, 100)
    key = store.reserve(0.05, 50)
    with pytest.raises(BudgetExceededError):
        store.settle(key, 0.11, 101)
    store.settle(key, 0.11, 101)
    assert store.summary()["spent_usd"] == 0.11
    with pytest.raises(BudgetExceededError):
        store.reserve(0, 0)
    with pytest.raises(ValueError, match="different usage"):
        store.settle(key, 0.01, 1)


def test_trackers_share_capacity_but_keep_per_run_usage(tmp_path):
    policy = Policy(
        strict_budget=True,
        budget_usd=0.1,
        budget_tokens=100,
        shared_budget_db=str(tmp_path / "trackers.db"),
        shared_budget_id="shared",
    )
    first, second = BudgetTracker(policy), BudgetTracker(policy)
    key = first.reserve(0.08, 80)
    with pytest.raises(BudgetExceededError):
        second.reserve(0.03, 30)
    first.settle(key, 0.07, 70)
    assert first._provider_usd == 0.07 and second._provider_usd == 0
    with pytest.raises(BudgetExceededError):
        second.reserve(0.04, 40)
    second.release(second.reserve(0.03, 30))
    assert second._shared_store.summary()["pending"] == 0


def test_invalid_usage_charges_the_reserved_bound(tmp_path):
    store = ReservationStore(str(tmp_path / "invalid.db"), "scope", 0.1, 100)
    key = store.reserve(0.05, 50)
    with pytest.raises(ValueError, match="Invalid usage"):
        store.settle(key, float("nan"), -1)
    assert store.summary()["spent_usd"] == 0.05
    assert store.summary()["spent_tokens"] == 50


def test_shared_store_requires_explicit_strict_configuration(tmp_path):
    with pytest.raises(ValueError, match="strict_budget"):
        BudgetTracker(
            Policy(shared_budget_db=str(tmp_path / "config.db"), shared_budget_id="scope")
        )
    with pytest.raises(ValueError, match="explicit scope"):
        ReservationStore(str(tmp_path / "config.db"), "", 0.1, 100)


@pytest.mark.asyncio
async def test_free_provider_still_debits_shared_token_budget(tmp_path):
    from meshflow.agents.base import EchoProvider
    from meshflow.core.request_budget import active_budget, budgeted_completion

    tracker = BudgetTracker(
        Policy(
            strict_budget=True,
            budget_usd=0.1,
            budget_tokens=10000,
            shared_budget_db=str(tmp_path / "free.db"),
            shared_budget_id="free",
        )
    )
    token = active_budget.set(tracker)
    try:
        result = await budgeted_completion(
            EchoProvider(),
            "complete",
            model="echo",
            messages=[{"role": "user", "content": "hello"}],
            system="",
            max_tokens=32,
        )
        summary = tracker._shared_store.summary()
        assert summary["spent_tokens"] == result[1] > 0
        assert summary["spent_usd"] == 0 and summary["pending"] == 0
    finally:
        active_budget.reset(token)


def test_sdk_retries_cannot_hide_multiple_dispatches_in_one_reservation(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import openai
    from meshflow.agents import base

    anthropic_constructor = Mock()
    monkeypatch.setattr(
        base, "_require_anthropic", lambda: SimpleNamespace(AsyncAnthropic=anthropic_constructor)
    )
    base.AnthropicProvider()
    assert anthropic_constructor.call_args.kwargs["max_retries"] == 0
    openai_constructor = Mock()
    monkeypatch.setattr(openai, "AsyncOpenAI", openai_constructor)
    base.OpenAICompatibleProvider(api_key="test-key")._client()
    assert openai_constructor.call_args.kwargs["max_retries"] == 0


def test_currency_rounding_never_increases_the_configured_cap(tmp_path):
    store = ReservationStore(str(tmp_path / "precision.db"), "scope", 0.0000000009, 100)
    assert store.summary()["limit_usd"] == 0
    with pytest.raises(BudgetExceededError):
        store.reserve(0.0000000001, 1)
    exact = ReservationStore(str(tmp_path / "precision.db"), "exact", 0.000000001, 100)
    key = exact.reserve(0.0000000001, 1)
    with pytest.raises(BudgetExceededError):
        exact.reserve(0.0000000001, 1)
    exact.settle(key, 0.0000000001, 1)
    assert exact.summary()["spent_usd"] == 0.000000001
