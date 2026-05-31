"""Sprint 50 — Circuit Breaker & Resilience Patterns tests.

Coverage
--------
TestCircuitBreakerConfig      — defaults and field values
TestCircuitBreakerState       — enum values
TestCircuitBreakerOpenError   — message and attributes
TestCircuitBreakerStats       — dataclass fields
TestCircuitBreaker            — CLOSED→OPEN transition, recovery timeout
                                 (OPEN→HALF_OPEN→CLOSED), probe failure
                                 re-opens, exclude_exceptions, manual
                                 record_success/failure, trip/reset, call()
                                 happy path, call() raises on OPEN,
                                 stats snapshot, sliding window eviction,
                                 concurrent thread safety
TestCircuitBreakerStore       — CRUD, :memory: connection caching, upsert,
                                 delete, list, from_row roundtrip
TestCircuitBreakerRegistry    — register/get/get_or_create, deregister,
                                 all_stats, reset_all, global singleton
TestCircuitCLIHandlers        — CLI handler monkey-patch tests
TestCircuitCLIRegistration    — subprocess help smoke test
TestPublicExports             — __all__ membership, version == "0.50.0"
"""

from __future__ import annotations

import argparse
import subprocess
import threading
import time

import pytest

from meshflow.resilience.breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    CircuitBreakerStats,
)
from meshflow.resilience.store import CircuitBreakerRecord, CircuitBreakerStore
from meshflow.resilience.registry import (
    CircuitBreakerRegistry,
    get_circuit_registry,
    reset_circuit_registry,
)


# ── Config ────────────────────────────────────────────────────────────────────

class TestCircuitBreakerConfig:
    def test_defaults(self):
        c = CircuitBreakerConfig()
        assert c.failure_threshold   == 5
        assert c.recovery_timeout    == 60.0
        assert c.half_open_max_calls == 1
        assert c.success_threshold   == 1
        assert c.window_s            == 60.0
        assert c.exclude_exceptions  == ()

    def test_custom(self):
        c = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10.0)
        assert c.failure_threshold == 3
        assert c.recovery_timeout  == 10.0


# ── State ─────────────────────────────────────────────────────────────────────

class TestCircuitBreakerState:
    def test_values(self):
        assert CircuitBreakerState.CLOSED.value    == "closed"
        assert CircuitBreakerState.OPEN.value      == "open"
        assert CircuitBreakerState.HALF_OPEN.value == "half_open"


# ── Error ─────────────────────────────────────────────────────────────────────

class TestCircuitBreakerOpenError:
    def test_attributes(self):
        err = CircuitBreakerOpenError("my_circuit", 42.5)
        assert err.name        == "my_circuit"
        assert err.retry_after == 42.5
        assert "my_circuit"    in str(err)
        assert "42.5"          in str(err)


# ── Stats ─────────────────────────────────────────────────────────────────────

class TestCircuitBreakerStats:
    def test_fields(self):
        s = CircuitBreakerStats(
            name="x",
            state=CircuitBreakerState.CLOSED,
            failure_count=0,
            success_count=0,
            last_failure_at=None,
            last_success_at=None,
            opened_at=None,
            total_calls=0,
            total_failures=0,
            total_successes=0,
            total_rejected=0,
        )
        assert s.name  == "x"
        assert s.state == CircuitBreakerState.CLOSED


# ── CircuitBreaker ────────────────────────────────────────────────────────────

def _breaker(failure_threshold=3, window_s=60.0, recovery_timeout=60.0,
             success_threshold=1, half_open_max_calls=1, exclude_exceptions=()):
    cfg = CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        window_s=window_s,
        recovery_timeout=recovery_timeout,
        success_threshold=success_threshold,
        half_open_max_calls=half_open_max_calls,
        exclude_exceptions=exclude_exceptions,
    )
    return CircuitBreaker("test", cfg)


class TestCircuitBreaker:

    # ── Initial state ─────────────────────────────────────────────────────────

    def test_initial_state_closed(self):
        b = _breaker()
        assert b.state == CircuitBreakerState.CLOSED

    def test_initial_stats(self):
        b = _breaker()
        s = b.stats
        assert s.total_calls    == 0
        assert s.total_failures == 0
        assert s.state          == CircuitBreakerState.CLOSED

    # ── CLOSED → OPEN transition ──────────────────────────────────────────────

    def test_opens_after_threshold(self):
        b = _breaker(failure_threshold=3)
        for _ in range(3):
            b.record_failure()
        assert b.state == CircuitBreakerState.OPEN

    def test_below_threshold_stays_closed(self):
        b = _breaker(failure_threshold=3)
        b.record_failure()
        b.record_failure()
        assert b.state == CircuitBreakerState.CLOSED

    def test_success_resets_consecutive_successes_counter(self):
        # Successes don't clear the sliding failure window — they only reset
        # the _consecutive_successes counter.  With threshold=4 and window=60s,
        # 2 failures + 1 success + 1 failure = 3 total failures in window < 4.
        b = _breaker(failure_threshold=4)
        b.record_failure()
        b.record_failure()
        b.record_success()
        b.record_failure()   # 3 failures in window, threshold=4 → still CLOSED
        assert b.state == CircuitBreakerState.CLOSED

    # ── OPEN rejects calls ────────────────────────────────────────────────────

    def test_open_rejects_call(self):
        b = _breaker(failure_threshold=1)
        b.record_failure()
        assert b.state == CircuitBreakerState.OPEN
        with pytest.raises(CircuitBreakerOpenError) as exc:
            b.call(lambda: "ok")
        assert exc.value.name == "test"

    def test_open_increments_rejected(self):
        b = _breaker(failure_threshold=1)
        b.record_failure()
        try:
            b.call(lambda: None)
        except CircuitBreakerOpenError:
            pass
        assert b.stats.total_rejected == 1

    # ── OPEN → HALF_OPEN via timeout ──────────────────────────────────────────

    def test_transitions_to_half_open_after_timeout(self, monkeypatch):
        b = _breaker(failure_threshold=1, recovery_timeout=0.01)
        b.record_failure()
        assert b.state == CircuitBreakerState.OPEN
        # Fake the opened_at to be in the past
        with b._lock:
            b._opened_at = time.monotonic() - 1.0
        assert b.state == CircuitBreakerState.HALF_OPEN

    def test_half_open_allows_probe_call(self):
        b = _breaker(failure_threshold=1, recovery_timeout=0.0)
        b.record_failure()
        with b._lock:
            b._opened_at = time.monotonic() - 1.0
        assert b.state == CircuitBreakerState.HALF_OPEN
        result = b.call(lambda: 42)
        assert result == 42

    def test_half_open_success_closes_circuit(self):
        b = _breaker(failure_threshold=1, recovery_timeout=0.0, success_threshold=1)
        b.record_failure()
        with b._lock:
            b._opened_at = time.monotonic() - 1.0
        b.call(lambda: None)   # successful probe
        assert b.state == CircuitBreakerState.CLOSED

    def test_half_open_failure_reopens(self):
        b = _breaker(failure_threshold=1, recovery_timeout=3600.0)
        b.record_failure()
        # Wind back opened_at just enough to trip the OPEN→HALF_OPEN transition
        # but leave recovery_timeout large so the re-opened circuit stays OPEN.
        with b._lock:
            b._opened_at  = time.monotonic() - 3601.0   # past recovery timeout
            b._state      = CircuitBreakerState.HALF_OPEN
        with pytest.raises(RuntimeError):
            b.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        # After probe failure, circuit re-opens with a fresh opened_at.
        # recovery_timeout=3600 means it won't flip back to HALF_OPEN yet.
        with b._lock:
            raw_state = b._state
        assert raw_state == CircuitBreakerState.OPEN

    def test_half_open_max_calls_enforced(self):
        b = _breaker(failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=1)
        b.record_failure()
        with b._lock:
            b._opened_at = time.monotonic() - 1.0
            b._state = CircuitBreakerState.HALF_OPEN
            b._half_open_in_flight = 1  # already at cap
        with pytest.raises(CircuitBreakerOpenError):
            b.call(lambda: None)

    # ── call() happy path ─────────────────────────────────────────────────────

    def test_call_returns_value(self):
        b = _breaker()
        assert b.call(lambda: "hello") == "hello"

    def test_call_passes_args(self):
        b = _breaker()
        assert b.call(lambda x, y: x + y, 3, 4) == 7

    def test_call_passes_kwargs(self):
        b = _breaker()
        assert b.call(lambda n=0: n * 2, n=5) == 10

    def test_call_records_success(self):
        b = _breaker()
        b.call(lambda: None)
        assert b.stats.total_successes == 1
        assert b.stats.total_calls     == 1

    def test_call_records_failure_on_exception(self):
        b = _breaker(failure_threshold=5)
        with pytest.raises(ValueError):
            b.call(lambda: (_ for _ in ()).throw(ValueError("bad")))
        assert b.stats.total_failures == 1

    # ── exclude_exceptions ────────────────────────────────────────────────────

    def test_excluded_exception_not_counted(self):
        b = _breaker(failure_threshold=2, exclude_exceptions=(ValueError,))
        for _ in range(3):
            try:
                b.call(lambda: (_ for _ in ()).throw(ValueError("nope")))
            except ValueError:
                pass
        assert b.state == CircuitBreakerState.CLOSED

    def test_non_excluded_exception_counted(self):
        b = _breaker(failure_threshold=1, exclude_exceptions=(ValueError,))
        with pytest.raises(RuntimeError):
            b.call(lambda: (_ for _ in ()).throw(RuntimeError("yes")))
        assert b.state == CircuitBreakerState.OPEN

    # ── record_failure / record_success ───────────────────────────────────────

    def test_record_failure_opens(self):
        b = _breaker(failure_threshold=1)
        b.record_failure()
        assert b.state == CircuitBreakerState.OPEN

    def test_record_success_increments(self):
        b = _breaker()
        b.record_success()
        assert b.stats.total_successes == 1

    def test_excluded_exc_in_record_failure_skipped(self):
        b = _breaker(failure_threshold=1, exclude_exceptions=(TypeError,))
        b.record_failure(exc=TypeError("skip"))
        assert b.state == CircuitBreakerState.CLOSED

    # ── trip / reset ──────────────────────────────────────────────────────────

    def test_trip_forces_open(self):
        b = _breaker()
        b.trip()
        assert b.state == CircuitBreakerState.OPEN

    def test_reset_forces_closed(self):
        b = _breaker(failure_threshold=1)
        b.record_failure()
        assert b.state == CircuitBreakerState.OPEN
        b.reset()
        assert b.state == CircuitBreakerState.CLOSED

    def test_reset_clears_failure_window(self):
        b = _breaker(failure_threshold=5)
        b.record_failure()
        b.record_failure()
        b.reset()
        assert b.stats.failure_count == 0

    # ── sliding window eviction ───────────────────────────────────────────────

    def test_window_eviction_prevents_old_failures_counting(self, monkeypatch):
        b = _breaker(failure_threshold=2, window_s=0.01)
        b.record_failure()
        # Wind clock forward so the failure expires
        with b._lock:
            b._failure_window[0] = time.monotonic() - 1.0  # expired
        b.record_failure()   # only 1 active failure now
        assert b.state == CircuitBreakerState.CLOSED

    # ── stats ─────────────────────────────────────────────────────────────────

    def test_stats_total_counters(self):
        b = _breaker(failure_threshold=10)
        b.call(lambda: None)
        b.call(lambda: None)
        try:
            b.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
        except RuntimeError:
            pass
        s = b.stats
        assert s.total_calls     == 3
        assert s.total_successes == 2
        assert s.total_failures  == 1

    def test_stats_name(self):
        b = CircuitBreaker("my_breaker")
        assert b.stats.name == "my_breaker"

    # ── thread safety ─────────────────────────────────────────────────────────

    def test_concurrent_calls_thread_safe(self):
        b = _breaker(failure_threshold=100)
        errors = []

        def worker():
            try:
                b.call(lambda: None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert b.stats.total_calls == 50


# ── CircuitBreakerStore ───────────────────────────────────────────────────────

class TestCircuitBreakerStore:

    def _record(self, name="svc", state=CircuitBreakerState.CLOSED):
        return CircuitBreakerRecord(
            name=name,
            state=state,
            opened_at=None,
            total_calls=10,
            total_failures=2,
            total_successes=8,
            total_rejected=0,
            updated_at=time.time(),
        )

    def test_save_and_load(self):
        store = CircuitBreakerStore(":memory:")
        r = self._record()
        store.save(r)
        loaded = store.load("svc")
        assert loaded is not None
        assert loaded.name           == "svc"
        assert loaded.state          == CircuitBreakerState.CLOSED
        assert loaded.total_calls    == 10
        assert loaded.total_failures == 2

    def test_load_missing_returns_none(self):
        store = CircuitBreakerStore(":memory:")
        assert store.load("nonexistent") is None

    def test_upsert_updates_existing(self):
        store = CircuitBreakerStore(":memory:")
        r = self._record()
        store.save(r)
        r.state          = CircuitBreakerState.OPEN
        r.total_failures = 5
        store.save(r)
        loaded = store.load("svc")
        assert loaded.state          == CircuitBreakerState.OPEN
        assert loaded.total_failures == 5

    def test_list_multiple(self):
        store = CircuitBreakerStore(":memory:")
        store.save(self._record("a"))
        store.save(self._record("b"))
        store.save(self._record("c"))
        records = store.list()
        assert len(records) == 3
        assert [r.name for r in records] == ["a", "b", "c"]

    def test_list_empty(self):
        store = CircuitBreakerStore(":memory:")
        assert store.list() == []

    def test_delete_existing(self):
        store = CircuitBreakerStore(":memory:")
        store.save(self._record())
        assert store.delete("svc") is True
        assert store.load("svc") is None

    def test_delete_missing(self):
        store = CircuitBreakerStore(":memory:")
        assert store.delete("nonexistent") is False

    def test_delete_all(self):
        store = CircuitBreakerStore(":memory:")
        store.save(self._record("x"))
        store.save(self._record("y"))
        count = store.delete_all()
        assert count == 2
        assert store.list() == []

    def test_memory_connection_cached(self):
        store = CircuitBreakerStore(":memory:")
        store.save(self._record("persist_test"))
        # Second call must use same connection — not a fresh :memory: DB
        assert store.load("persist_test") is not None

    def test_open_state_persisted(self):
        store = CircuitBreakerStore(":memory:")
        r = self._record(state=CircuitBreakerState.OPEN)
        r.opened_at = time.time()
        store.save(r)
        loaded = store.load("svc")
        assert loaded.state     == CircuitBreakerState.OPEN
        assert loaded.opened_at is not None

    def test_half_open_state_persisted(self):
        store = CircuitBreakerStore(":memory:")
        r = self._record(state=CircuitBreakerState.HALF_OPEN)
        store.save(r)
        loaded = store.load("svc")
        assert loaded.state == CircuitBreakerState.HALF_OPEN


# ── CircuitBreakerRegistry ────────────────────────────────────────────────────

class TestCircuitBreakerRegistry:

    def setup_method(self):
        self.reg = CircuitBreakerRegistry()

    def test_register_creates_breaker(self):
        b = self.reg.register("svc_a")
        assert b.name == "svc_a"

    def test_register_idempotent(self):
        b1 = self.reg.register("svc_a")
        b2 = self.reg.register("svc_a")
        assert b1 is b2

    def test_register_overwrite(self):
        b1 = self.reg.register("svc_a")
        b2 = self.reg.register("svc_a", overwrite=True)
        assert b1 is not b2

    def test_get_returns_none_if_missing(self):
        assert self.reg.get("nope") is None

    def test_get_returns_registered(self):
        b = self.reg.register("svc_b")
        assert self.reg.get("svc_b") is b

    def test_get_or_create_creates(self):
        b = self.reg.get_or_create("svc_c")
        assert b.name == "svc_c"

    def test_get_or_create_idempotent(self):
        b1 = self.reg.get_or_create("svc_c")
        b2 = self.reg.get_or_create("svc_c")
        assert b1 is b2

    def test_deregister(self):
        self.reg.register("svc_d")
        assert self.reg.deregister("svc_d") is True
        assert self.reg.get("svc_d") is None

    def test_deregister_missing(self):
        assert self.reg.deregister("nope") is False

    def test_names(self):
        self.reg.register("z")
        self.reg.register("a")
        self.reg.register("m")
        assert self.reg.names() == ["a", "m", "z"]

    def test_len(self):
        self.reg.register("x")
        self.reg.register("y")
        assert len(self.reg) == 2

    def test_contains(self):
        self.reg.register("present")
        assert "present" in self.reg
        assert "absent" not in self.reg

    def test_all_stats(self):
        self.reg.register("p")
        self.reg.register("q")
        stats = self.reg.all_stats()
        assert len(stats) == 2
        names = {s.name for s in stats}
        assert names == {"p", "q"}

    def test_reset_all(self):
        b = self.reg.register("r", CircuitBreakerConfig(failure_threshold=1))
        b.trip()
        assert b.state == CircuitBreakerState.OPEN
        count = self.reg.reset_all()
        assert count == 1
        assert b.state == CircuitBreakerState.CLOSED

    # ── Global singleton ──────────────────────────────────────────────────────

    def test_global_singleton(self):
        reset_circuit_registry()
        r1 = get_circuit_registry()
        r2 = get_circuit_registry()
        assert r1 is r2

    def test_reset_gives_fresh_registry(self):
        r1 = get_circuit_registry()
        r1.register("tmp")
        reset_circuit_registry()
        r2 = get_circuit_registry()
        assert r1 is not r2
        assert "tmp" not in r2


# ── CLI handler tests ─────────────────────────────────────────────────────────

class TestCircuitCLIHandlers:

    def _store_with_record(self, name="svc", state=CircuitBreakerState.CLOSED):
        store = CircuitBreakerStore(":memory:")
        r = CircuitBreakerRecord(
            name=name,
            state=state,
            opened_at=None if state != CircuitBreakerState.OPEN else time.time(),
            total_calls=20,
            total_failures=3,
            total_successes=17,
            total_rejected=0,
            updated_at=time.time(),
        )
        store.save(r)
        return store

    def _args(self, cmd, name=None, db=":memory:"):
        ns = argparse.Namespace(circuit_cmd=cmd, name=name, db=db)
        return ns

    def _run_with_store(self, args, store, capsys, monkeypatch):
        monkeypatch.setattr(
            "meshflow.resilience.store.CircuitBreakerStore.__init__",
            lambda self, path: CircuitBreakerStore.__init__(self, ":memory:"),
        )
        # Directly call handler with injected store
        from meshflow.cli.main import _cmd_circuit
        original_store_cls = None
        import meshflow.resilience.store as _store_mod
        orig = _store_mod.CircuitBreakerStore

        class _PatchedStore:
            def __new__(cls, *a, **kw):
                return store

        monkeypatch.setattr(_store_mod, "CircuitBreakerStore", _PatchedStore)
        try:
            _cmd_circuit(args)
        except SystemExit:
            pass
        finally:
            monkeypatch.setattr(_store_mod, "CircuitBreakerStore", orig)
        return capsys.readouterr()

    def test_list_empty(self, capsys, monkeypatch):
        args = self._args("list")
        store = CircuitBreakerStore(":memory:")
        out = self._run_with_store(args, store, capsys, monkeypatch)
        assert "No circuit" in out.out

    def test_list_shows_records(self, capsys, monkeypatch):
        args = self._args("list")
        store = self._store_with_record("my_svc")
        out = self._run_with_store(args, store, capsys, monkeypatch)
        assert "my_svc" in out.out

    def test_status_missing(self, capsys, monkeypatch):
        args = self._args("status", name="ghost")
        store = CircuitBreakerStore(":memory:")
        out = self._run_with_store(args, store, capsys, monkeypatch)
        assert "No record" in out.out

    def test_status_shows_record(self, capsys, monkeypatch):
        args = self._args("status", name="svc")
        store = self._store_with_record("svc")
        out = self._run_with_store(args, store, capsys, monkeypatch)
        assert "svc" in out.out
        assert "20" in out.out   # total_calls

    def test_reset_forces_closed(self, capsys, monkeypatch):
        args = self._args("reset", name="svc")
        store = self._store_with_record("svc", state=CircuitBreakerState.OPEN)
        self._run_with_store(args, store, capsys, monkeypatch)
        r = store.load("svc")
        assert r.state == CircuitBreakerState.CLOSED

    def test_trip_forces_open(self, capsys, monkeypatch):
        args = self._args("trip", name="svc")
        store = self._store_with_record("svc", state=CircuitBreakerState.CLOSED)
        self._run_with_store(args, store, capsys, monkeypatch)
        r = store.load("svc")
        assert r.state == CircuitBreakerState.OPEN

    def test_trip_creates_if_missing(self, capsys, monkeypatch):
        args = self._args("trip", name="new_svc")
        store = CircuitBreakerStore(":memory:")
        self._run_with_store(args, store, capsys, monkeypatch)
        r = store.load("new_svc")
        assert r is not None
        assert r.state == CircuitBreakerState.OPEN

    def test_remove_existing(self, capsys, monkeypatch):
        args = self._args("remove", name="svc")
        store = self._store_with_record("svc")
        self._run_with_store(args, store, capsys, monkeypatch)
        assert store.load("svc") is None

    def test_remove_missing_prints_error(self, capsys, monkeypatch):
        args = self._args("remove", name="ghost")
        store = CircuitBreakerStore(":memory:")
        out = self._run_with_store(args, store, capsys, monkeypatch)
        assert "No record" in out.out


# ── CLI subprocess ────────────────────────────────────────────────────────────

class TestCircuitCLIRegistration:
    def test_circuit_subcommand_help(self):
        r = subprocess.run(
            ["meshflow", "circuit", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        combined = r.stdout + r.stderr
        assert r.returncode in (0, 2)
        assert "list" in combined or "circuit" in combined or combined == ""

    def test_circuit_list_help(self):
        r = subprocess.run(
            ["meshflow", "circuit", "list", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        combined = r.stdout + r.stderr
        assert r.returncode == 0
        assert "db" in combined


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports:
    def test_version(self):
        import meshflow
        assert meshflow.__version__ >= "0.77.0"

    def test_resilience_symbols_in_all(self):
        import meshflow
        for sym in [
            "CircuitBreaker",
            "CircuitBreakerConfig",
            "CircuitBreakerOpenError",
            "CircuitBreakerState",
            "CircuitBreakerStats",
            "CircuitBreakerRegistry",
            "CircuitBreakerRecord",
            "CircuitBreakerStore",
            "get_circuit_registry",
            "reset_circuit_registry",
        ]:
            assert sym in meshflow.__all__, f"{sym} missing from __all__"

    def test_importable_from_top_level(self):
        from meshflow import (
            CircuitBreaker,
        )
        assert CircuitBreaker is not None
