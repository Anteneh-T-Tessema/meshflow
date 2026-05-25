"""Sprint 47 — Cron scheduler: CronExpression, ScheduleStore, CronScheduler, CLI."""

from __future__ import annotations

import argparse
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.scheduler.cron import CronExpression, _parse_field
from meshflow.scheduler.store import ScheduleStore, ScheduledTask, ScheduleRun
from meshflow.scheduler.engine import CronScheduler


# ── _parse_field ───────────────────────────────────────────────────────────────

class TestParseField:
    def test_star(self):
        assert _parse_field("*", 0, 59) == set(range(60))

    def test_exact(self):
        assert _parse_field("5", 0, 59) == {5}

    def test_range(self):
        assert _parse_field("1-5", 0, 59) == {1, 2, 3, 4, 5}

    def test_list(self):
        assert _parse_field("1,3,5", 0, 59) == {1, 3, 5}

    def test_step_star(self):
        assert _parse_field("*/15", 0, 59) == {0, 15, 30, 45}

    def test_step_range(self):
        assert _parse_field("0-10/5", 0, 59) == {0, 5, 10}

    def test_list_and_range(self):
        result = _parse_field("1,10-12", 0, 59)
        assert result == {1, 10, 11, 12}


# ── CronExpression ────────────────────────────────────────────────────────────

class TestCronExpression:
    def test_wrong_field_count_raises(self):
        with pytest.raises(ValueError):
            CronExpression("* * *")

    def test_raw_stored(self):
        expr = CronExpression("0 9 * * 1-5")
        assert expr.raw == "0 9 * * 1-5"

    def test_matches_exact_minute(self):
        # every minute
        from datetime import datetime, timezone
        expr = CronExpression("* * * * *")
        dt = datetime(2026, 5, 24, 9, 30, 0, tzinfo=timezone.utc)
        assert expr.matches(dt)

    def test_matches_specific(self):
        from datetime import datetime, timezone
        expr = CronExpression("30 8 * * *")
        dt = datetime(2026, 5, 24, 8, 30, tzinfo=timezone.utc)
        assert expr.matches(dt)

    def test_no_match_wrong_minute(self):
        from datetime import datetime, timezone
        expr = CronExpression("30 8 * * *")
        dt = datetime(2026, 5, 24, 8, 31, tzinfo=timezone.utc)
        assert not expr.matches(dt)

    def test_next_after_returns_future(self):
        expr = CronExpression("* * * * *")
        now = time.time()
        nxt = expr.next_after(now)
        assert nxt > now

    def test_next_after_is_minute_aligned(self):
        expr = CronExpression("* * * * *")
        nxt = expr.next_after(time.time())
        assert nxt % 60 == 0

    def test_next_after_hourly(self):
        # "0 * * * *" — fires at :00 of every hour
        expr = CronExpression("0 * * * *")
        now = time.time()
        nxt = expr.next_after(now)
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(nxt, tz=timezone.utc)
        assert dt.minute == 0

    def test_next_after_daily_9am(self):
        expr = CronExpression("0 9 * * *")
        now = time.time()
        nxt = expr.next_after(now)
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(nxt, tz=timezone.utc)
        assert dt.hour == 9
        assert dt.minute == 0


# ── ScheduleStore ─────────────────────────────────────────────────────────────

class TestScheduleStore:
    def _store(self):
        return ScheduleStore(":memory:")

    def test_add_and_get(self):
        store = self._store()
        task = ScheduledTask(
            name="daily-billing",
            agent_name="billing-agent",
            cron="0 9 * * 1-5",
            task_payload="Generate daily report",
        )
        store.add(task)
        fetched = store.get(task.schedule_id)
        assert fetched is not None
        assert fetched.name == "daily-billing"
        assert fetched.cron == "0 9 * * 1-5"

    def test_get_missing_returns_none(self):
        store = self._store()
        assert store.get("does-not-exist") is None

    def test_list_empty(self):
        store = self._store()
        assert store.list() == []

    def test_list_returns_added(self):
        store = self._store()
        store.add(ScheduledTask(agent_name="a1", cron="* * * * *"))
        store.add(ScheduledTask(agent_name="a2", cron="* * * * *"))
        assert len(store.list()) == 2

    def test_list_filter_agent(self):
        store = self._store()
        store.add(ScheduledTask(agent_name="billing", cron="* * * * *"))
        store.add(ScheduledTask(agent_name="support", cron="* * * * *"))
        result = store.list(agent_name="billing")
        assert len(result) == 1
        assert result[0].agent_name == "billing"

    def test_delete_removes(self):
        store = self._store()
        task = ScheduledTask(agent_name="bot", cron="* * * * *")
        store.add(task)
        assert store.delete(task.schedule_id)
        assert store.get(task.schedule_id) is None

    def test_delete_missing_returns_false(self):
        store = self._store()
        assert not store.delete("ghost")

    def test_enable_disable(self):
        store = self._store()
        task = ScheduledTask(agent_name="bot", cron="* * * * *", enabled=True)
        store.add(task)
        store.enable(task.schedule_id, False)
        assert store.get(task.schedule_id).enabled is False
        store.enable(task.schedule_id, True)
        assert store.get(task.schedule_id).enabled is True

    def test_count(self):
        store = self._store()
        assert store.count() == 0
        store.add(ScheduledTask(agent_name="a", cron="* * * * *"))
        assert store.count() == 1

    def test_due_returns_overdue(self):
        store = self._store()
        past = time.time() - 300
        task = ScheduledTask(agent_name="bot", cron="* * * * *", next_fire_at=past)
        store.add(task)
        due = store.due()
        assert any(t.schedule_id == task.schedule_id for t in due)

    def test_due_excludes_future(self):
        store = self._store()
        future = time.time() + 9999
        task = ScheduledTask(agent_name="bot", cron="* * * * *", next_fire_at=future)
        store.add(task)
        assert store.due() == []

    def test_due_excludes_disabled(self):
        store = self._store()
        past = time.time() - 300
        task = ScheduledTask(agent_name="bot", cron="* * * * *", next_fire_at=past, enabled=False)
        store.add(task)
        assert store.due() == []

    def test_record_fire_updates_counts(self):
        store = self._store()
        task = ScheduledTask(agent_name="bot", cron="* * * * *")
        store.add(task)
        nxt = time.time() + 60
        run = store.record_fire(task.schedule_id, nxt, "task-abc")
        assert run.status == "dispatched"
        assert run.task_id == "task-abc"
        updated = store.get(task.schedule_id)
        assert updated.fire_count == 1
        assert updated.next_fire_at == nxt

    def test_runs_returns_history(self):
        store = self._store()
        task = ScheduledTask(agent_name="bot", cron="* * * * *")
        store.add(task)
        store.record_fire(task.schedule_id, time.time() + 60, "t1")
        store.record_fire(task.schedule_id, time.time() + 120, "t2")
        runs = store.runs(task.schedule_id)
        assert len(runs) == 2

    def test_metadata_roundtrip(self):
        store = self._store()
        task = ScheduledTask(agent_name="bot", cron="* * * * *", metadata={"env": "prod"})
        store.add(task)
        fetched = store.get(task.schedule_id)
        assert fetched.metadata == {"env": "prod"}


# ── CronScheduler ─────────────────────────────────────────────────────────────

class TestCronScheduler:
    def test_add_sets_next_fire(self):
        sched = CronScheduler()
        task = sched.add(ScheduledTask(agent_name="bot", cron="* * * * *"))
        assert task.next_fire_at > time.time()

    def test_add_invalid_cron_raises(self):
        sched = CronScheduler()
        with pytest.raises(ValueError):
            sched.add(ScheduledTask(agent_name="bot", cron="bad cron expression!!!"))

    def test_remove(self):
        sched = CronScheduler()
        task = sched.add(ScheduledTask(agent_name="bot", cron="* * * * *"))
        assert sched.remove(task.schedule_id)
        assert sched.get(task.schedule_id) is None

    def test_list(self):
        sched = CronScheduler()
        sched.add(ScheduledTask(agent_name="a1", cron="* * * * *"))
        sched.add(ScheduledTask(agent_name="a2", cron="* * * * *"))
        assert len(sched.list()) == 2

    def test_enable_disable(self):
        sched = CronScheduler()
        task = sched.add(ScheduledTask(agent_name="bot", cron="* * * * *"))
        sched.enable(task.schedule_id, False)
        assert sched.get(task.schedule_id).enabled is False

    def test_tick_fires_due_tasks(self):
        fired = []
        def dispatch(task):
            fired.append(task.schedule_id)
            return "task-123"

        store = ScheduleStore(":memory:")
        sched = CronScheduler(store=store, dispatch=dispatch)

        task = ScheduledTask(agent_name="bot", cron="* * * * *",
                             next_fire_at=time.time() - 1)
        store.add(task)

        runs = sched._tick()
        assert len(runs) == 1
        assert task.schedule_id in fired

    def test_tick_skips_disabled(self):
        fired = []
        def dispatch(task):
            fired.append(task.schedule_id)
            return None

        store = ScheduleStore(":memory:")
        sched = CronScheduler(store=store, dispatch=dispatch)

        task = ScheduledTask(agent_name="bot", cron="* * * * *",
                             next_fire_at=time.time() - 1, enabled=False)
        store.add(task)

        sched._tick()
        assert fired == []

    def test_tick_updates_next_fire_at(self):
        store = ScheduleStore(":memory:")
        sched = CronScheduler(store=store, dispatch=lambda t: None)

        task = ScheduledTask(agent_name="bot", cron="* * * * *",
                             next_fire_at=time.time() - 1)
        store.add(task)

        before_next = time.time()
        sched._tick()
        updated = store.get(task.schedule_id)
        assert updated.next_fire_at > before_next

    def test_start_stop(self):
        sched = CronScheduler(poll_s=0.05)
        sched.start()
        assert sched.is_running()
        sched.stop(timeout=2.0)
        assert not sched.is_running()

    def test_context_manager(self):
        with CronScheduler(poll_s=0.05) as sched:
            assert sched.is_running()
        assert not sched.is_running()

    def test_dispatch_error_does_not_crash_tick(self):
        def bad_dispatch(task):
            raise RuntimeError("network error")

        store = ScheduleStore(":memory:")
        sched = CronScheduler(store=store, dispatch=bad_dispatch)
        task = ScheduledTask(agent_name="bot", cron="* * * * *",
                             next_fire_at=time.time() - 1)
        store.add(task)
        # Should not raise
        sched._tick()


# ── CLI: schedule subcommand ──────────────────────────────────────────────────

def _schedule_ns(**kwargs):
    defaults = dict(db=":memory:", schedule_cmd="list", agent_name="", name="",
                    cron="* * * * *", task_payload="", schedule_id="", limit=20)
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


from meshflow.cli.main import _cmd_schedule


class TestScheduleCLIList:
    def _patch(self, store):
        import meshflow.scheduler.store as _ss
        old = _ss.ScheduleStore
        _ss.ScheduleStore = lambda db: store
        return old, _ss

    def test_empty(self, capsys):
        store = ScheduleStore(":memory:")
        old, _ss = self._patch(store)
        try:
            _cmd_schedule(_schedule_ns(schedule_cmd="list"))
        finally:
            _ss.ScheduleStore = old
        assert "No schedules found" in capsys.readouterr().out

    def test_shows_schedules(self, capsys):
        store = ScheduleStore(":memory:")
        task = ScheduledTask(name="my-schedule", agent_name="billing-agent",
                             cron="0 9 * * *", next_fire_at=time.time() + 3600)
        store.add(task)
        old, _ss = self._patch(store)
        try:
            _cmd_schedule(_schedule_ns(schedule_cmd="list"))
        finally:
            _ss.ScheduleStore = old
        out = capsys.readouterr().out
        assert "billing-agent" in out
        assert "my-schedule" in out


class TestScheduleCLIAdd:
    def _patch(self, store):
        import meshflow.scheduler.store as _ss
        old = _ss.ScheduleStore
        _ss.ScheduleStore = lambda db: store
        return old, _ss

    def test_add_creates_schedule(self, capsys):
        store = ScheduleStore(":memory:")
        old, _ss = self._patch(store)
        try:
            _cmd_schedule(_schedule_ns(
                schedule_cmd="add",
                agent_name="billing-agent",
                cron="0 9 * * 1-5",
                task_payload="Daily report",
                name="daily-report",
            ))
        finally:
            _ss.ScheduleStore = old
        assert store.count() == 1
        out = capsys.readouterr().out
        assert "Schedule created" in out
        assert "billing-agent" in out

    def test_add_invalid_cron_exits(self, capsys):
        store = ScheduleStore(":memory:")
        old, _ss = self._patch(store)
        try:
            with pytest.raises(SystemExit):
                _cmd_schedule(_schedule_ns(
                    schedule_cmd="add",
                    agent_name="billing-agent",
                    cron="bad cron",
                    task_payload="",
                    name="",
                ))
        finally:
            _ss.ScheduleStore = old


class TestScheduleCLIRemoveEnableDisable:
    def _patch(self, store):
        import meshflow.scheduler.store as _ss
        old = _ss.ScheduleStore
        _ss.ScheduleStore = lambda db: store
        return old, _ss

    def test_remove_existing(self, capsys):
        store = ScheduleStore(":memory:")
        task = ScheduledTask(agent_name="bot", cron="* * * * *")
        store.add(task)
        old, _ss = self._patch(store)
        try:
            _cmd_schedule(_schedule_ns(schedule_cmd="remove", schedule_id=task.schedule_id))
        finally:
            _ss.ScheduleStore = old
        assert store.get(task.schedule_id) is None
        assert "removed" in capsys.readouterr().out

    def test_remove_missing_exits(self):
        store = ScheduleStore(":memory:")
        old, _ss = self._patch(store)
        import meshflow.scheduler.store as _ss2
        old2 = _ss2.ScheduleStore
        _ss2.ScheduleStore = lambda db: store
        try:
            with pytest.raises(SystemExit):
                _cmd_schedule(_schedule_ns(schedule_cmd="remove", schedule_id="ghost"))
        finally:
            _ss2.ScheduleStore = old2

    def _patch(self, store):
        import meshflow.scheduler.store as _ss
        old = _ss.ScheduleStore
        _ss.ScheduleStore = lambda db: store
        return old, _ss

    def test_enable(self, capsys):
        store = ScheduleStore(":memory:")
        task = ScheduledTask(agent_name="bot", cron="* * * * *", enabled=False)
        store.add(task)
        old, _ss = self._patch(store)
        try:
            _cmd_schedule(_schedule_ns(schedule_cmd="enable", schedule_id=task.schedule_id))
        finally:
            _ss.ScheduleStore = old
        assert store.get(task.schedule_id).enabled is True
        assert "enabled" in capsys.readouterr().out

    def test_disable(self, capsys):
        store = ScheduleStore(":memory:")
        task = ScheduledTask(agent_name="bot", cron="* * * * *", enabled=True)
        store.add(task)
        old, _ss = self._patch(store)
        try:
            _cmd_schedule(_schedule_ns(schedule_cmd="disable", schedule_id=task.schedule_id))
        finally:
            _ss.ScheduleStore = old
        assert store.get(task.schedule_id).enabled is False
        assert "disabled" in capsys.readouterr().out


class TestScheduleCLIGet:
    def _patch(self, store):
        import meshflow.scheduler.store as _ss
        old = _ss.ScheduleStore
        _ss.ScheduleStore = lambda db: store
        return old, _ss

    def test_get_prints_json(self, capsys):
        import json
        store = ScheduleStore(":memory:")
        task = ScheduledTask(name="test-task", agent_name="bot", cron="0 6 * * *")
        store.add(task)
        old, _ss = self._patch(store)
        try:
            _cmd_schedule(_schedule_ns(schedule_cmd="get", schedule_id=task.schedule_id))
        finally:
            _ss.ScheduleStore = old
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["schedule_id"] == task.schedule_id
        assert data["cron"] == "0 6 * * *"

    def test_get_missing_exits(self):
        store = ScheduleStore(":memory:")
        old, _ss = self._patch(store)
        try:
            with pytest.raises(SystemExit):
                _cmd_schedule(_schedule_ns(schedule_cmd="get", schedule_id="ghost"))
        finally:
            _ss.ScheduleStore = old


# ── CLI subcommand registered ─────────────────────────────────────────────────

class TestScheduleCLIRegistration:
    def test_schedule_subcommand_registered(self):
        import subprocess
        r = subprocess.run(
            ["meshflow", "schedule", "--help"],
            capture_output=True, text=True,
        )
        combined = r.stdout + r.stderr
        assert "list" in combined
        assert "add" in combined
        assert "remove" in combined

    def test_schedule_add_requires_agent_and_cron(self):
        import subprocess
        r = subprocess.run(
            ["meshflow", "schedule", "add", "--cron", "* * * * *"],
            capture_output=True, text=True,
        )
        assert r.returncode != 0


# ── Public API exports ────────────────────────────────────────────────────────

class TestPublicExports:
    def test_scheduler_in_init(self):
        import meshflow
        assert hasattr(meshflow, "CronScheduler")
        assert hasattr(meshflow, "CronExpression")
        assert hasattr(meshflow, "ScheduledTask")
        assert hasattr(meshflow, "ScheduleStore")
        assert hasattr(meshflow, "ScheduleRun")

    def test_version_bumped(self):
        import meshflow
        assert meshflow.__version__ == "0.47.0"
