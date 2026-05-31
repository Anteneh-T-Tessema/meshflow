"""Sprint 45 — Cost budgets and quota enforcement."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.budget.store import (
    BudgetAccount,
    BudgetSpend,
    BudgetStore,
    period_key,
    VALID_PERIODS,
)
from meshflow.budget.guardrail import BudgetGuardrail


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    return BudgetStore(":memory:")


def _account(**kwargs) -> BudgetAccount:
    defaults = dict(
        account_id="billing-daily",
        name="Billing daily cap",
        agent_name="billing-agent",
        period="daily",
        limit_usd=5.00,
        limit_tokens=0,
    )
    defaults.update(kwargs)
    return BudgetAccount(**defaults)


# ── period_key ────────────────────────────────────────────────────────────────

class TestPeriodKey:
    def test_total_returns_all(self):
        assert period_key("total") == "all"

    def test_daily_format(self):
        pk = period_key("daily")
        assert len(pk) == 10  # YYYY-MM-DD
        assert pk.count("-") == 2

    def test_weekly_format(self):
        pk = period_key("weekly")
        assert "W" in pk

    def test_monthly_format(self):
        pk = period_key("monthly")
        assert len(pk) == 7  # YYYY-MM
        assert pk.count("-") == 1

    def test_valid_periods_constant(self):
        assert "daily" in VALID_PERIODS
        assert "weekly" in VALID_PERIODS
        assert "monthly" in VALID_PERIODS
        assert "total" in VALID_PERIODS


# ── BudgetAccount ─────────────────────────────────────────────────────────────

class TestBudgetAccount:
    def test_auto_account_id(self):
        a = BudgetAccount(agent_name="bot", period="daily", limit_usd=1.0)
        assert len(a.account_id) > 0

    def test_auto_name_fallback(self):
        a = BudgetAccount(agent_name="bot", period="daily", limit_usd=1.0)
        assert a.name == a.account_id

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            BudgetAccount(agent_name="bot", period="hourly", limit_usd=1.0)

    def test_round_trip(self):
        a = _account()
        a2 = BudgetAccount.from_dict(a.to_dict())
        assert a2.account_id == a.account_id
        assert a2.limit_usd == a.limit_usd
        assert a2.period == a.period

    def test_created_at_auto(self):
        a = _account()
        assert a.created_at > 0


# ── BudgetStore — CRUD ────────────────────────────────────────────────────────

class TestBudgetStoreCRUD:
    def test_create_and_get(self, store):
        a = _account()
        store.create(a)
        fetched = store.get("billing-daily")
        assert fetched is not None
        assert fetched.limit_usd == 5.00

    def test_get_missing(self, store):
        assert store.get("ghost") is None

    def test_create_overwrites(self, store):
        store.create(_account(limit_usd=5.0))
        store.create(_account(limit_usd=10.0))
        assert store.get("billing-daily").limit_usd == 10.0

    def test_delete_returns_true(self, store):
        store.create(_account())
        assert store.delete("billing-daily") is True

    def test_delete_removes(self, store):
        store.create(_account())
        store.delete("billing-daily")
        assert store.get("billing-daily") is None

    def test_delete_missing_returns_false(self, store):
        assert store.delete("ghost") is False

    def test_count(self, store):
        store.create(_account(account_id="a", agent_name="x"))
        store.create(_account(account_id="b", agent_name="y"))
        assert store.count() == 2

    def test_list_all(self, store):
        store.create(_account(account_id="a1"))
        store.create(_account(account_id="a2", agent_name="other"))
        assert len(store.list()) == 2

    def test_list_by_agent(self, store):
        store.create(_account(account_id="a1", agent_name="billing-agent"))
        store.create(_account(account_id="a2", agent_name="support-agent"))
        assert len(store.list(agent_name="billing-agent")) == 1

    def test_list_by_period(self, store):
        store.create(_account(account_id="d", period="daily"))
        store.create(_account(account_id="w", period="weekly"))
        assert len(store.list(period="daily")) == 1


# ── BudgetStore — spend tracking ──────────────────────────────────────────────

class TestBudgetStoreSpend:
    def test_record_spend_creates_row(self, store):
        store.create(_account())
        spend = store.record_spend("billing-daily", cost_usd=0.10, tokens=1000)
        assert spend.cost_usd == pytest.approx(0.10)
        assert spend.tokens_used == 1000
        assert spend.call_count == 1

    def test_record_spend_accumulates(self, store):
        store.create(_account())
        store.record_spend("billing-daily", cost_usd=0.10, tokens=500)
        store.record_spend("billing-daily", cost_usd=0.20, tokens=300)
        spend = store.current_spend("billing-daily")
        assert spend.cost_usd == pytest.approx(0.30)
        assert spend.tokens_used == 800
        assert spend.call_count == 2

    def test_record_spend_missing_account_raises(self, store):
        with pytest.raises(KeyError):
            store.record_spend("ghost", cost_usd=0.10)

    def test_get_spend_missing(self, store):
        store.create(_account())
        assert store.get_spend("billing-daily", "1990-01-01") is None

    def test_reset_spend(self, store):
        store.create(_account())
        store.record_spend("billing-daily", cost_usd=1.0)
        store.reset_spend("billing-daily")
        spend = store.current_spend("billing-daily")
        assert spend.cost_usd == 0.0

    def test_current_spend_zero_when_no_record(self, store):
        store.create(_account())
        spend = store.current_spend("billing-daily")
        assert spend.cost_usd == 0.0
        assert spend.tokens_used == 0


# ── BudgetStore — budget gate ─────────────────────────────────────────────────

class TestBudgetGate:
    def test_allowed_when_under_limit(self, store):
        store.create(_account(limit_usd=5.0))
        store.record_spend("billing-daily", cost_usd=1.0)
        result = store.check("billing-daily")
        assert result.allowed is True

    def test_blocked_when_at_limit(self, store):
        store.create(_account(limit_usd=5.0))
        store.record_spend("billing-daily", cost_usd=5.0)
        result = store.check("billing-daily")
        assert result.allowed is False
        assert "USD budget exhausted" in result.reason

    def test_blocked_when_over_limit(self, store):
        store.create(_account(limit_usd=1.0))
        store.record_spend("billing-daily", cost_usd=2.0)
        assert store.check("billing-daily").allowed is False

    def test_token_limit_blocks(self, store):
        store.create(_account(account_id="tok", limit_usd=0, limit_tokens=1000))
        store.record_spend("tok", tokens=1000)
        result = store.check("tok")
        assert result.allowed is False
        assert "Token budget exhausted" in result.reason

    def test_token_limit_allows_under(self, store):
        store.create(_account(account_id="tok", limit_usd=0, limit_tokens=1000))
        store.record_spend("tok", tokens=500)
        assert store.check("tok").allowed is True

    def test_missing_account_blocked(self, store):
        result = store.check("ghost")
        assert result.allowed is False
        assert "not found" in result.reason

    def test_remaining_usd_computed(self, store):
        store.create(_account(limit_usd=5.0))
        store.record_spend("billing-daily", cost_usd=2.0)
        result = store.check("billing-daily")
        assert result.remaining_usd == pytest.approx(3.0)

    def test_remaining_tokens_computed(self, store):
        store.create(_account(account_id="tok", limit_usd=0, limit_tokens=1000))
        store.record_spend("tok", tokens=400)
        result = store.check("tok")
        assert result.remaining_tokens == 600

    def test_percent_used(self, store):
        store.create(_account(limit_usd=10.0))
        store.record_spend("billing-daily", cost_usd=7.0)
        result = store.check("billing-daily")
        assert result.percent_used == pytest.approx(0.70)

    def test_no_cap_always_allowed(self, store):
        store.create(_account(account_id="free", limit_usd=0, limit_tokens=0))
        store.record_spend("free", cost_usd=999.0, tokens=999_999)
        assert store.check("free").allowed is True

    def test_check_result_to_dict(self, store):
        store.create(_account())
        result = store.check("billing-daily")
        d = result.to_dict()
        assert "allowed" in d
        assert "spent_usd" in d
        assert "remaining_usd" in d
        assert "percent_used" in d


# ── BudgetStore — is_agent_allowed ────────────────────────────────────────────

class TestAgentAllowed:
    def test_no_accounts_allowed(self, store):
        allowed, _ = store.is_agent_allowed("unknown-agent")
        assert allowed is True

    def test_all_within_budget_allowed(self, store):
        store.create(_account(account_id="a1", agent_name="bot", limit_usd=10.0))
        store.record_spend("a1", cost_usd=1.0)
        allowed, _ = store.is_agent_allowed("bot")
        assert allowed is True

    def test_one_exhausted_blocks_all(self, store):
        store.create(_account(account_id="a1", agent_name="bot", limit_usd=1.0))
        store.create(_account(account_id="a2", agent_name="bot", limit_usd=100.0))
        store.record_spend("a1", cost_usd=1.0)
        allowed, reason = store.is_agent_allowed("bot")
        assert allowed is False
        assert "exhausted" in reason


# ── BudgetStore — summary ─────────────────────────────────────────────────────

class TestBudgetSummary:
    def test_summary_structure(self, store):
        store.create(_account())
        store.record_spend("billing-daily", cost_usd=1.5, tokens=1500)
        s = store.summary("billing-daily")
        assert s["allowed"] is True
        assert s["spent_usd"] == pytest.approx(1.5)
        assert s["spent_tokens"] == 1500
        assert s["limit_usd"] == 5.0
        assert "remaining_usd" in s
        assert "percent_used" in s

    def test_summary_missing(self, store):
        s = store.summary("ghost")
        assert "error" in s


# ── BudgetSpend.to_dict ────────────────────────────────────────────────────────

class TestBudgetSpend:
    def test_to_dict(self):
        s = BudgetSpend(account_id="x", period_key="2026-05-24",
                        tokens_used=500, cost_usd=0.05, call_count=3)
        d = s.to_dict()
        assert d["tokens_used"] == 500
        assert d["call_count"] == 3
        assert "cost_usd" in d


# ── BudgetGuardrail ────────────────────────────────────────────────────────────

class TestBudgetGuardrail:
    def test_allows_within_budget(self, store):
        store.create(_account())
        g = BudgetGuardrail(agent_name="billing-agent", store=store)
        result = g.check("some task text")
        assert result.passed is True

    def test_blocks_when_exhausted(self, store):
        store.create(_account(limit_usd=1.0))
        store.record_spend("billing-daily", cost_usd=1.0)
        g = BudgetGuardrail(agent_name="billing-agent", store=store)
        result = g.check("some task text")
        assert result.passed is False
        assert "exhausted" in result.reason

    def test_allows_with_no_accounts(self, store):
        g = BudgetGuardrail(agent_name="unknown-agent", store=store)
        result = g.check("some task text")
        assert result.passed is True

    def test_record_spend_debits(self, store):
        store.create(_account(limit_usd=5.0))
        g = BudgetGuardrail(agent_name="billing-agent", store=store)
        g.record_spend(cost_usd=2.0, tokens=2000)
        spend = store.current_spend("billing-daily")
        assert spend.cost_usd == pytest.approx(2.0)
        assert spend.tokens_used == 2000

    def test_record_spend_multiple_accounts(self, store):
        store.create(_account(account_id="d", agent_name="bot", period="daily", limit_usd=10.0))
        store.create(_account(account_id="t", agent_name="bot", period="total", limit_usd=100.0))
        g = BudgetGuardrail(agent_name="bot", store=store)
        g.record_spend(cost_usd=1.0, tokens=100)
        assert store.current_spend("d").cost_usd == pytest.approx(1.0)
        assert store.current_spend("t").cost_usd == pytest.approx(1.0)

    def test_warns_near_limit(self, store):
        store.create(_account(limit_usd=10.0))
        store.record_spend("billing-daily", cost_usd=9.0)
        g = BudgetGuardrail(agent_name="billing-agent", store=store, warn_at=0.80)
        result = g.check("task")
        assert result.passed is True
        assert "near_limit" in result.metadata

    def test_no_warning_below_threshold(self, store):
        store.create(_account(limit_usd=10.0))
        store.record_spend("billing-daily", cost_usd=1.0)
        g = BudgetGuardrail(agent_name="billing-agent", store=store, warn_at=0.80)
        result = g.check("task")
        assert result.passed is True
        assert "near_limit" not in result.metadata

    def test_status_returns_list(self, store):
        store.create(_account())
        g = BudgetGuardrail(agent_name="billing-agent", store=store)
        statuses = g.status()
        assert len(statuses) == 1
        assert statuses[0]["agent_name"] == "billing-agent"

    def test_custom_name(self, store):
        g = BudgetGuardrail(agent_name="bot", store=store, name="my-cap")
        result = g.check("x")
        assert result.guardrail_name == "my-cap"


# ── Integration with GuardrailStack ───────────────────────────────────────────

class TestGuardrailStackIntegration:
    def test_stack_blocks_on_budget_exhaustion(self, store):
        from meshflow.security.guardrails import GuardrailStack
        store.create(_account(limit_usd=1.0))
        store.record_spend("billing-daily", cost_usd=1.0)
        g = BudgetGuardrail(agent_name="billing-agent", store=store)
        stack = GuardrailStack([g], mode="collect")
        passed, reason, results = stack.run("task")
        assert passed is False
        assert "exhausted" in results[0].reason

    def test_stack_passes_within_budget(self, store):
        from meshflow.security.guardrails import GuardrailStack
        store.create(_account(limit_usd=10.0))
        g = BudgetGuardrail(agent_name="billing-agent", store=store)
        stack = GuardrailStack([g], mode="collect")
        passed, _, _ = stack.run("task")
        assert passed is True


# ── Total period (never resets) ────────────────────────────────────────────────

class TestTotalPeriod:
    def test_total_period_key_is_all(self):
        assert period_key("total") == "all"

    def test_total_accumulates_across_time(self, store):
        store.create(_account(account_id="life", period="total", limit_usd=100.0))
        store.record_spend("life", cost_usd=30.0)
        store.record_spend("life", cost_usd=30.0)
        spend = store.current_spend("life")
        assert spend.cost_usd == pytest.approx(60.0)

    def test_total_blocks_when_exceeded(self, store):
        store.create(_account(account_id="life", period="total", limit_usd=50.0))
        store.record_spend("life", cost_usd=55.0)
        assert store.check("life").allowed is False


# ── Public API ─────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_budget_package_imports(self):
        from meshflow.budget import (
            BudgetAccount, BudgetSpend, BudgetCheckResult,
            BudgetStore, BudgetGuardrail,
            get_budget_store, reset_budget_store, period_key, VALID_PERIODS,
        )
        assert all(x is not None for x in [
            BudgetAccount, BudgetSpend, BudgetCheckResult,
            BudgetStore, BudgetGuardrail,
            get_budget_store, reset_budget_store, period_key, VALID_PERIODS,
        ])

    def test_default_store_lazy_init(self):
        from meshflow.budget.store import get_budget_store, reset_budget_store
        reset_budget_store()
        s = get_budget_store()
        assert s is not None
        reset_budget_store()
