"""Sprint 48 — Per-agent and per-team sliding-window rate limiting."""

from __future__ import annotations

import argparse
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.ratelimit.window import (
    RateLimitPolicy,
    RateLimitStore,
    _Window,
    get_rate_limit_store,
    reset_rate_limit_store,
)
from meshflow.ratelimit.store_db import RateLimitPolicyDB
from meshflow.ratelimit.guardrail import RateLimitGuardrail, TeamRateLimitGuardrail
from meshflow.security.guardrails import GuardrailStack, GuardrailViolation


# ── RateLimitPolicy ───────────────────────────────────────────────────────────

class TestRateLimitPolicy:
    def test_defaults(self):
        p = RateLimitPolicy()
        assert p.max_requests == 0
        assert p.max_tokens == 0
        assert p.window_s == 60.0
        assert p.warn_at == 0.80

    def test_has_request_limit(self):
        assert RateLimitPolicy(max_requests=10).has_request_limit()
        assert not RateLimitPolicy(max_requests=0).has_request_limit()

    def test_has_token_limit(self):
        assert RateLimitPolicy(max_tokens=1000).has_token_limit()
        assert not RateLimitPolicy(max_tokens=0).has_token_limit()

    def test_is_unlimited(self):
        assert RateLimitPolicy().is_unlimited()
        assert not RateLimitPolicy(max_requests=10).is_unlimited()
        assert not RateLimitPolicy(max_tokens=100).is_unlimited()


# ── _Window (internal) ────────────────────────────────────────────────────────

class TestWindow:
    def _w(self, max_requests=5, max_tokens=100, window_s=60.0):
        return _Window(policy=RateLimitPolicy(
            max_requests=max_requests, max_tokens=max_tokens, window_s=window_s
        ))

    def test_check_passes_when_empty(self):
        w = self._w()
        result = w.check(tokens=10)
        assert result.allowed

    def test_request_limit_blocks(self):
        w = self._w(max_requests=2)
        now = time.monotonic()
        w.record(now=now)
        w.record(now=now + 1)
        result = w.check(now=now + 2)
        assert not result.allowed
        assert "request rate exceeded" in result.reason

    def test_token_limit_blocks(self):
        w = self._w(max_tokens=100)
        now = time.monotonic()
        w.record(tokens=80, now=now)
        result = w.check(tokens=30, now=now + 1)
        assert not result.allowed
        assert "token rate exceeded" in result.reason

    def test_window_slides(self):
        w = self._w(max_requests=2, window_s=10.0)
        now = time.monotonic()
        w.record(now=now)
        w.record(now=now + 1)
        # 15 seconds later — old events evicted
        result = w.check(now=now + 15)
        assert result.allowed

    def test_near_limit_flag(self):
        w = self._w(max_requests=10)
        now = time.monotonic()
        for i in range(9):  # 90% used
            w.record(now=now + i * 0.1)
        result = w.check(now=now + 1)
        assert result.allowed
        assert result.near_limit

    def test_record_increments_count(self):
        w = self._w()
        now = time.monotonic()
        w.record(tokens=50, now=now)
        _, tc = w._count()
        assert tc == 50


# ── RateLimitStore ────────────────────────────────────────────────────────────

class TestRateLimitStore:
    def _store(self):
        return RateLimitStore()

    def test_no_policy_always_allowed(self):
        store = self._store()
        result = store.check("any-agent", tokens=9999)
        assert result.allowed
        assert result.reason == "no policy"

    def test_set_and_check(self):
        store = self._store()
        store.set_policy("billing-agent", RateLimitPolicy(max_requests=5, window_s=60))
        for _ in range(5):
            r = store.check_and_record("billing-agent")
            assert r.allowed
        blocked = store.check("billing-agent")
        assert not blocked.allowed

    def test_wildcard_applies_to_unknown_key(self):
        store = self._store()
        store.set_policy("*", RateLimitPolicy(max_requests=2, window_s=60))
        store.check_and_record("unknown-agent")
        store.check_and_record("unknown-agent")
        result = store.check("unknown-agent")
        assert not result.allowed

    def test_exact_key_takes_precedence_over_wildcard(self):
        store = self._store()
        store.set_policy("*", RateLimitPolicy(max_requests=1, window_s=60))
        store.set_policy("vip-agent", RateLimitPolicy(max_requests=100, window_s=60))
        for _ in range(5):
            r = store.check_and_record("vip-agent")
            assert r.allowed

    def test_remove_policy(self):
        store = self._store()
        store.set_policy("bot", RateLimitPolicy(max_requests=1, window_s=60))
        store.check_and_record("bot")
        store.check_and_record("bot")
        assert store.remove_policy("bot")
        # After removal, no policy → unlimited
        assert store.check("bot").allowed

    def test_remove_missing_returns_false(self):
        store = self._store()
        assert not store.remove_policy("ghost")

    def test_reset_clears_window(self):
        store = self._store()
        store.set_policy("bot", RateLimitPolicy(max_requests=2, window_s=60))
        store.check_and_record("bot")
        store.check_and_record("bot")
        assert not store.check("bot").allowed
        store.reset("bot")
        assert store.check("bot").allowed

    def test_status_returns_dict(self):
        store = self._store()
        store.set_policy("bot", RateLimitPolicy(max_requests=10, window_s=60))
        store.check_and_record("bot")
        st = store.status("bot")
        assert st is not None
        assert st["requests_used"] == 1
        assert st["requests_limit"] == 10

    def test_status_unknown_key_returns_none(self):
        store = self._store()
        assert store.status("unknown") is None

    def test_token_window_tracks_tokens(self):
        store = self._store()
        store.set_policy("bot", RateLimitPolicy(max_tokens=100, window_s=60))
        store.check_and_record("bot", tokens=60)
        result = store.check("bot", tokens=50)
        assert not result.allowed

    def test_check_and_record_atomic(self):
        store = self._store()
        store.set_policy("bot", RateLimitPolicy(max_requests=3, window_s=60))
        results = [store.check_and_record("bot") for _ in range(5)]
        allowed = [r.allowed for r in results]
        assert allowed[:3] == [True, True, True]
        assert allowed[3:] == [False, False]

    def test_global_singleton(self):
        reset_rate_limit_store()
        s1 = get_rate_limit_store()
        s2 = get_rate_limit_store()
        assert s1 is s2

    def test_policies_returns_copy(self):
        store = self._store()
        store.set_policy("a", RateLimitPolicy(max_requests=5))
        p = store.policies()
        assert "a" in p


# ── RateLimitPolicyDB ─────────────────────────────────────────────────────────

class TestRateLimitPolicyDB:
    def _db(self):
        return RateLimitPolicyDB(":memory:")

    def test_save_and_load(self):
        db = self._db()
        policy = RateLimitPolicy(max_requests=60, max_tokens=10000, window_s=60.0, warn_at=0.75)
        db.save("billing-agent", policy)
        loaded = db.load("billing-agent")
        assert loaded is not None
        assert loaded.max_requests == 60
        assert loaded.max_tokens == 10000
        assert loaded.warn_at == 0.75

    def test_load_missing_returns_none(self):
        db = self._db()
        assert db.load("ghost") is None

    def test_delete(self):
        db = self._db()
        db.save("bot", RateLimitPolicy(max_requests=10))
        assert db.delete("bot")
        assert db.load("bot") is None

    def test_delete_missing_returns_false(self):
        db = self._db()
        assert not db.delete("ghost")

    def test_list(self):
        db = self._db()
        db.save("a", RateLimitPolicy(max_requests=10))
        db.save("b", RateLimitPolicy(max_tokens=1000))
        rows = db.list()
        assert len(rows) == 2
        keys = {r["key"] for r in rows}
        assert keys == {"a", "b"}

    def test_list_empty(self):
        db = self._db()
        assert db.list() == []

    def test_count(self):
        db = self._db()
        assert db.count() == 0
        db.save("x", RateLimitPolicy())
        assert db.count() == 1

    def test_upsert_overwrites(self):
        db = self._db()
        db.save("bot", RateLimitPolicy(max_requests=10))
        db.save("bot", RateLimitPolicy(max_requests=99))
        assert db.load("bot").max_requests == 99


# ── RateLimitGuardrail ────────────────────────────────────────────────────────

class TestRateLimitGuardrail:
    def _guardrail(self, **policy_kwargs):
        store = RateLimitStore()
        policy = RateLimitPolicy(**policy_kwargs)
        return RateLimitGuardrail("test-agent", policy=policy, store=store)

    def test_check_passes_initially(self):
        g = self._guardrail(max_requests=5, window_s=60)
        result = g.check("hello")
        assert result.passed

    def test_check_blocks_after_limit(self):
        g = self._guardrail(max_requests=2, window_s=60)
        g.check_and_record(tokens=0)
        g.check_and_record(tokens=0)
        result = g.check("hello")
        assert not result.passed
        assert "request rate exceeded" in result.reason

    def test_record_debits_window(self):
        store = RateLimitStore()
        store.set_policy("a", RateLimitPolicy(max_requests=3, window_s=60))
        g = RateLimitGuardrail("a", store=store)
        g.record()
        g.record()
        g.record()
        assert not g.check("x").passed

    def test_check_and_record_atomic(self):
        g = self._guardrail(max_requests=2, window_s=60)
        r1 = g.check_and_record()
        r2 = g.check_and_record()
        r3 = g.check_and_record()
        assert r1.passed
        assert r2.passed
        assert not r3.passed

    def test_near_limit_metadata(self):
        g = self._guardrail(max_requests=10, window_s=60)
        for _ in range(9):
            g.check_and_record()
        result = g.check("x")
        assert result.passed
        assert result.metadata.get("near_limit") is True
        assert "warning" in result.metadata

    def test_token_limit(self):
        g = self._guardrail(max_tokens=100, window_s=60)
        g.check_and_record(tokens=80)
        result = g.check("x", tokens=30)
        assert not result.passed

    def test_status_returns_dict(self):
        g = self._guardrail(max_requests=10, window_s=60)
        g.check_and_record()
        st = g.status()
        assert st is not None
        assert st["requests_used"] == 1

    def test_reset_clears_window(self):
        g = self._guardrail(max_requests=2, window_s=60)
        g.check_and_record()
        g.check_and_record()
        assert not g.check("x").passed
        g.reset()
        assert g.check("x").passed

    def test_no_policy_check_always_passes(self):
        store = RateLimitStore()
        g = RateLimitGuardrail("no-policy-agent", store=store)
        result = g.check("hello")
        assert result.passed

    def test_guardrail_name(self):
        g = self._guardrail(max_requests=5)
        assert "rate_limit" in g.name


# ── TeamRateLimitGuardrail ────────────────────────────────────────────────────

class TestTeamRateLimitGuardrail:
    def test_team_guardrail_shares_window(self):
        store = RateLimitStore()
        policy = RateLimitPolicy(max_requests=3, window_s=60)
        g1 = TeamRateLimitGuardrail("billing-team", policy=policy, store=store)
        g2 = TeamRateLimitGuardrail("billing-team", store=store)
        g1.check_and_record()
        g1.check_and_record()
        g2.check_and_record()
        # 3 requests used → 4th should be blocked
        assert not g2.check("x").passed

    def test_team_name_attribute(self):
        g = TeamRateLimitGuardrail("my-team")
        assert g.team_name == "my-team"
        assert g.agent_name == "my-team"


# ── GuardrailStack integration ────────────────────────────────────────────────

class TestGuardrailStackIntegration:
    def test_stack_blocks_when_rate_limited(self):
        store = RateLimitStore()
        policy = RateLimitPolicy(max_requests=1, window_s=60)
        g = RateLimitGuardrail("agent", policy=policy, store=store)
        stack = GuardrailStack([g], mode="collect")

        r1_pass, _, _ = stack.run("first call")
        g.record()  # debit after first call

        r2_pass, reason, results = stack.run("second call")
        assert not r2_pass
        assert "exceeded" in reason or "exceeded" in results[0].reason

    def test_stack_passes_initially(self):
        store = RateLimitStore()
        policy = RateLimitPolicy(max_requests=10, window_s=60)
        g = RateLimitGuardrail("agent", policy=policy, store=store)
        stack = GuardrailStack([g], mode="collect")
        passed, _, _ = stack.run("hello")
        assert passed

    def test_strict_mode_raises_on_rate_limit(self):
        store = RateLimitStore()
        policy = RateLimitPolicy(max_requests=1, window_s=60)
        g = RateLimitGuardrail("agent", policy=policy, store=store)
        stack = GuardrailStack([g], mode="strict")

        stack.run("first")
        g.record()

        with pytest.raises(GuardrailViolation):
            stack.run("second blocked call")


# ── CLI: ratelimit subcommand ─────────────────────────────────────────────────

def _rl_ns(**kwargs):
    defaults = dict(db=":memory:", ratelimit_cmd="list", key="test-agent",
                    max_requests=0, max_tokens=0, window_s=60.0, warn_at=0.80)
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


from meshflow.cli.main import _cmd_ratelimit


class TestRateLimitCLIList:
    def _patch(self, db):
        import meshflow.ratelimit.store_db as _sdb
        old = _sdb.RateLimitPolicyDB
        _sdb.RateLimitPolicyDB = lambda path: db
        return old, _sdb

    def test_empty_list(self, capsys):
        db = RateLimitPolicyDB(":memory:")
        old, _sdb = self._patch(db)
        try:
            _cmd_ratelimit(_rl_ns(ratelimit_cmd="list"))
        finally:
            _sdb.RateLimitPolicyDB = old
        assert "No rate limit policies" in capsys.readouterr().out

    def test_list_shows_policies(self, capsys):
        db = RateLimitPolicyDB(":memory:")
        db.save("billing-agent", RateLimitPolicy(max_requests=60, window_s=60))
        old, _sdb = self._patch(db)
        try:
            _cmd_ratelimit(_rl_ns(ratelimit_cmd="list"))
        finally:
            _sdb.RateLimitPolicyDB = old
        out = capsys.readouterr().out
        assert "billing-agent" in out


class TestRateLimitCLISet:
    def _patch(self, db):
        import meshflow.ratelimit.store_db as _sdb
        old = _sdb.RateLimitPolicyDB
        _sdb.RateLimitPolicyDB = lambda path: db
        return old, _sdb

    def test_set_saves_policy(self, capsys):
        db = RateLimitPolicyDB(":memory:")
        old, _sdb = self._patch(db)
        try:
            _cmd_ratelimit(_rl_ns(
                ratelimit_cmd="set", key="my-agent",
                max_requests=100, max_tokens=50000,
                window_s=60.0, warn_at=0.90,
            ))
        finally:
            _sdb.RateLimitPolicyDB = old
        loaded = db.load("my-agent")
        assert loaded is not None
        assert loaded.max_requests == 100
        assert "saved" in capsys.readouterr().out.lower()

    def test_set_wildcard_key(self, capsys):
        db = RateLimitPolicyDB(":memory:")
        old, _sdb = self._patch(db)
        try:
            _cmd_ratelimit(_rl_ns(
                ratelimit_cmd="set", key="*",
                max_requests=500, max_tokens=0,
                window_s=60.0, warn_at=0.80,
            ))
        finally:
            _sdb.RateLimitPolicyDB = old
        assert db.load("*") is not None


class TestRateLimitCLIRemove:
    def _patch(self, db):
        import meshflow.ratelimit.store_db as _sdb
        old = _sdb.RateLimitPolicyDB
        _sdb.RateLimitPolicyDB = lambda path: db
        return old, _sdb

    def test_remove_existing(self, capsys):
        db = RateLimitPolicyDB(":memory:")
        db.save("bot", RateLimitPolicy(max_requests=10))
        old, _sdb = self._patch(db)
        try:
            _cmd_ratelimit(_rl_ns(ratelimit_cmd="remove", key="bot"))
        finally:
            _sdb.RateLimitPolicyDB = old
        assert db.load("bot") is None
        assert "removed" in capsys.readouterr().out

    def test_remove_missing_exits(self):
        db = RateLimitPolicyDB(":memory:")
        old, _sdb = self._patch(db)
        try:
            with pytest.raises(SystemExit):
                _cmd_ratelimit(_rl_ns(ratelimit_cmd="remove", key="ghost"))
        finally:
            _sdb.RateLimitPolicyDB = old


class TestRateLimitCLIStatus:
    def _patch(self, db):
        import meshflow.ratelimit.store_db as _sdb
        old = _sdb.RateLimitPolicyDB
        _sdb.RateLimitPolicyDB = lambda path: db
        return old, _sdb

    def test_status_shows_policy(self, capsys):
        db = RateLimitPolicyDB(":memory:")
        db.save("billing", RateLimitPolicy(max_requests=60, max_tokens=100000, window_s=60))
        old, _sdb = self._patch(db)
        try:
            _cmd_ratelimit(_rl_ns(ratelimit_cmd="status", key="billing"))
        finally:
            _sdb.RateLimitPolicyDB = old
        out = capsys.readouterr().out
        assert "60" in out

    def test_status_no_policy(self, capsys):
        db = RateLimitPolicyDB(":memory:")
        old, _sdb = self._patch(db)
        try:
            _cmd_ratelimit(_rl_ns(ratelimit_cmd="status", key="ghost"))
        finally:
            _sdb.RateLimitPolicyDB = old
        assert "No rate limit policy" in capsys.readouterr().out


# ── CLI subcommand registered ─────────────────────────────────────────────────

class TestRateLimitCLIRegistration:
    def test_ratelimit_subcommand_registered(self):
        import subprocess
        r = subprocess.run(
            ["meshflow", "ratelimit", "--help"],
            capture_output=True, text=True,
        )
        combined = r.stdout + r.stderr
        assert "list" in combined
        assert "set" in combined
        assert "remove" in combined

    def test_ratelimit_set_requires_key(self):
        import subprocess
        r = subprocess.run(
            ["meshflow", "ratelimit", "set"],
            capture_output=True, text=True,
        )
        assert r.returncode != 0


# ── Public API exports ────────────────────────────────────────────────────────

class TestPublicExports:
    def test_ratelimit_in_init(self):
        import meshflow
        for name in [
            "RateLimitPolicy", "RateLimitResult", "RateLimitStore",
            "RateLimitPolicyDB", "RateLimitGuardrail", "TeamRateLimitGuardrail",
            "get_rate_limit_store", "reset_rate_limit_store",
        ]:
            assert hasattr(meshflow, name), f"Missing export: {name}"

    def test_version_bumped(self):
        import meshflow
        assert meshflow.__version__ >= "0.77.0"
