"""Sprint 40 — A2A task lifecycle: full state machine + SSE streaming."""

from __future__ import annotations

import os
import sys
import time
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.a2a.tasks import A2ATask, A2ATaskStore, TaskEventQueue, TaskState


# ── TaskState ─────────────────────────────────────────────────────────────────

class TestTaskState:
    def test_values(self):
        assert TaskState.submitted == "submitted"
        assert TaskState.working == "working"
        assert TaskState.input_required == "input_required"
        assert TaskState.completed == "completed"
        assert TaskState.failed == "failed"

    def test_from_string(self):
        assert TaskState("working") is TaskState.working

    def test_terminal_states(self):
        t = A2ATask()
        t.state = TaskState.completed
        assert t.is_terminal()
        t.state = TaskState.failed
        assert t.is_terminal()

    def test_non_terminal_states(self):
        t = A2ATask()
        for state in (TaskState.submitted, TaskState.working, TaskState.input_required):
            t.state = state
            assert not t.is_terminal()


# ── A2ATask ───────────────────────────────────────────────────────────────────

class TestA2ATask:
    def test_auto_task_id(self):
        t = A2ATask()
        assert len(t.task_id) > 0

    def test_default_state(self):
        t = A2ATask()
        assert t.state == TaskState.submitted

    def test_transition_updates_state(self):
        t = A2ATask()
        t.transition(TaskState.working)
        assert t.state == TaskState.working

    def test_transition_updates_timestamp(self):
        t = A2ATask()
        old_ts = t.updated_at
        time.sleep(0.01)
        t.transition(TaskState.completed)
        assert t.updated_at > old_ts

    def test_to_dict_keys(self):
        t = A2ATask(content="hello", agent_name="bot")
        d = t.to_dict()
        assert d["content"] == "hello"
        assert d["agent_name"] == "bot"
        assert d["state"] == "submitted"
        assert "task_id" in d
        assert "created_at" in d
        assert "updated_at" in d

    def test_round_trip(self):
        t = A2ATask(content="hello", agent_name="bot", tokens=42)
        t2 = A2ATask.from_dict(t.to_dict())
        assert t2.task_id == t.task_id
        assert t2.content == "hello"
        assert t2.tokens == 42
        assert t2.state == TaskState.submitted

    def test_from_dict_state(self):
        t = A2ATask.from_dict({"task_id": "x", "state": "completed", "content": "q"})
        assert t.state == TaskState.completed


# ── A2ATaskStore ──────────────────────────────────────────────────────────────

class TestA2ATaskStore:
    def test_put_and_get(self):
        store = A2ATaskStore()
        t = A2ATask(content="hello")
        store.put(t)
        assert store.get(t.task_id) is t

    def test_get_missing(self):
        store = A2ATaskStore()
        assert store.get("nonexistent") is None

    def test_list_ordered_newest_first(self):
        store = A2ATaskStore()
        t1 = A2ATask(content="first")
        time.sleep(0.005)
        t2 = A2ATask(content="second")
        store.put(t1)
        store.put(t2)
        tasks = store.list()
        assert tasks[0].task_id == t2.task_id

    def test_list_limit(self):
        store = A2ATaskStore()
        for i in range(10):
            store.put(A2ATask(content=f"t{i}"))
        assert len(store.list(limit=3)) == 3

    def test_subscribe_receives_updates(self):
        store = A2ATaskStore()
        t = A2ATask(content="watch me")
        store.put(t)
        eq = store.subscribe(t.task_id)

        t.transition(TaskState.working)
        store.put(t)

        event = eq.next_event(timeout=1.0)
        assert event is not None
        assert event.state == TaskState.working
        eq.close()

    def test_subscribe_multiple_listeners(self):
        store = A2ATaskStore()
        t = A2ATask(content="broadcast")
        store.put(t)
        eq1 = store.subscribe(t.task_id)
        eq2 = store.subscribe(t.task_id)

        t.transition(TaskState.completed)
        store.put(t)

        assert eq1.next_event(timeout=1.0) is not None
        assert eq2.next_event(timeout=1.0) is not None
        eq1.close()
        eq2.close()

    def test_unsubscribe_stops_delivery(self):
        store = A2ATaskStore()
        t = A2ATask(content="quiet")
        store.put(t)
        eq = store.subscribe(t.task_id)
        eq.close()  # unsubscribe

        t.transition(TaskState.completed)
        store.put(t)

        # Should timeout — no more events
        event = eq.next_event(timeout=0.1)
        assert event is None


# ── TaskEventQueue ────────────────────────────────────────────────────────────

class TestTaskEventQueue:
    def test_iter_until_done(self):
        store = A2ATaskStore()
        t = A2ATask(content="progress")
        store.put(t)
        eq = store.subscribe(t.task_id)

        states_seen = []

        def _drive():
            time.sleep(0.05)
            t.transition(TaskState.working)
            store.put(t)
            time.sleep(0.05)
            t.transition(TaskState.completed)
            store.put(t)

        threading.Thread(target=_drive, daemon=True).start()

        for event in eq.iter_until_done(poll_timeout=0.5):
            states_seen.append(event.state)

        eq.close()
        assert TaskState.completed in states_seen

    def test_timeout_returns_none(self):
        store = A2ATaskStore()
        t = A2ATask(content="never completes")
        store.put(t)
        eq = store.subscribe(t.task_id)
        event = eq.next_event(timeout=0.05)
        assert event is None
        eq.close()


# ── A2AServer + A2AClient lifecycle ──────────────────────────────────────────

class TestA2AServerTaskLifecycle:
    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["MESHFLOW_MOCK"] = "1"

    def test_submit_returns_task_id(self):
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        from meshflow.a2a.client import A2AClient

        agent = Agent(name="task-agent", role="executor")
        with A2AServer(agent, port=19400) as _:
            client = A2AClient("http://127.0.0.1:19400")
            task_id = client.submit("What is HIPAA?")
            assert isinstance(task_id, str) and len(task_id) > 0

    def test_poll_shows_terminal_state(self):
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        from meshflow.a2a.client import A2AClient

        agent = Agent(name="poll-agent", role="executor")
        with A2AServer(agent, port=19401) as _:
            client = A2AClient("http://127.0.0.1:19401")
            task_id = client.submit("Quick question")
            task = client.wait(task_id, timeout=10.0)
            assert task.is_terminal()
            assert task.state in (TaskState.completed, TaskState.failed)

    def test_completed_task_has_result(self):
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        from meshflow.a2a.client import A2AClient

        agent = Agent(name="result-agent", role="executor")
        with A2AServer(agent, port=19402) as _:
            client = A2AClient("http://127.0.0.1:19402")
            task_id = client.submit("Hello")
            task = client.wait(task_id, timeout=10.0)
            assert task.state == TaskState.completed
            assert isinstance(task.result, str)

    def test_list_tasks(self):
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        from meshflow.a2a.client import A2AClient

        agent = Agent(name="list-agent", role="executor")
        with A2AServer(agent, port=19403) as _:
            client = A2AClient("http://127.0.0.1:19403")
            client.submit("task 1")
            client.submit("task 2")
            time.sleep(0.2)
            tasks = client.list_tasks()
            assert len(tasks) >= 2

    def test_sse_stream(self):
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        from meshflow.a2a.client import A2AClient

        agent = Agent(name="stream-agent", role="executor")
        with A2AServer(agent, port=19404) as _:
            client = A2AClient("http://127.0.0.1:19404")
            task_id = client.submit("Stream this")
            events = list(client.stream(task_id, timeout=10.0))
            assert len(events) >= 1
            final = events[-1]
            assert final.is_terminal()

    def test_legacy_run_still_works(self):
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        from meshflow.a2a.client import A2AClient

        agent = Agent(name="legacy-agent", role="executor")
        with A2AServer(agent, port=19405) as _:
            client = A2AClient("http://127.0.0.1:19405")
            resp = client.run("Hello legacy")
            assert resp.success
            assert isinstance(resp.content, str)

    def test_agent_card_version_updated(self):
        from meshflow.agents.builder import Agent
        from meshflow.a2a.server import A2AServer
        from meshflow.a2a.client import A2AClient

        agent = Agent(name="card-agent", role="executor")
        with A2AServer(agent, port=19406) as _:
            client = A2AClient("http://127.0.0.1:19406")
            card = client.card()
            assert "tasks" in card.capabilities
            assert "stream" in card.capabilities


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.a2a.tasks import A2ATask, A2ATaskStore, TaskState
        assert all(x is not None for x in [A2ATask, A2ATaskStore, TaskState, TaskEventQueue])

    def test_a2a_package_exports(self):
        from meshflow.a2a import A2ATask, A2ATaskStore, TaskState
        assert all(x is not None for x in [A2ATask, A2ATaskStore, TaskState])
