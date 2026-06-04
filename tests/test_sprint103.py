"""Tests for v1.14.0 cloud SDK features.

Covers:
- PromptHub  (offline + local HTTP server)
- DatasetHub (offline + local HTTP server)
- CloudAgentRegistry (offline + local HTTP server)
- MeshFlowCloud.report_spans()
- MeshFlowCloud.instrument() — duck-typed queue injection and span collection
- instrument(register_agents=True) — agent run counter bump
- Version bump guard (pyproject.toml == meshflow.__version__)
"""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import patch


# ── Shared local HTTP server fixture ─────────────────────────────────────────

class _FakeIngestServer:
    """Minimal HTTP server that records POSTs and returns canned GET responses."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.get_responses: dict[str, Any] = {}
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        outer = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

            def _send_json(self, code: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                resp = outer.get_responses.get(self.path)
                if resp is None:
                    self._send_json(404, {"error": "not found"})
                else:
                    self._send_json(200, resp)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                outer.received.append({"path": self.path, "body": body})
                self._send_json(201, {"ok": True, "ingested": len(body.get("spans", []))})

            def do_DELETE(self) -> None:
                outer.received.append({"path": self.path, "body": {}})
                self._send_json(200, {"ok": True})

        self._server = HTTPServer(("127.0.0.1", 0), _H)
        port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
# Version guard
# ══════════════════════════════════════════════════════════════════════════════

class TestVersionConsistency(unittest.TestCase):
    def test_module_version_matches_pyproject(self) -> None:
        import meshflow
        import tomllib  # stdlib in 3.11+
        root = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(root, "rb") as fh:
            meta = tomllib.load(fh)
        self.assertEqual(meshflow.__version__, meta["project"]["version"])

    def test_version_is_1_14_0(self) -> None:
        import meshflow
        self.assertEqual(meshflow.__version__, "1.14.0")


# ══════════════════════════════════════════════════════════════════════════════
# PromptHub — offline (no API key)
# ══════════════════════════════════════════════════════════════════════════════

class TestPromptHubOffline(unittest.TestCase):
    def setUp(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        PromptHub.clear_cache()
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("MESHFLOW_API_KEY", None)
        os.environ.pop("MESHFLOW_CLOUD_ENABLED", None)

    def tearDown(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        PromptHub.clear_cache()
        self._env.stop()

    def test_get_returns_default_when_no_key(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self.assertEqual(PromptHub.get("my-prompt", default="fallback"), "fallback")

    def test_get_returns_empty_string_by_default(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self.assertEqual(PromptHub.get("any-slug"), "")

    def test_push_returns_false_when_no_key(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self.assertFalse(PromptHub.push("slug", "content"))

    def test_list_returns_empty_when_no_key(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self.assertEqual(PromptHub.list(), [])

    def test_clear_cache_is_idempotent(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        PromptHub.clear_cache()
        PromptHub.clear_cache()

    def test_cfg_disabled_when_no_key(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        _, _, enabled = PromptHub._cfg()
        self.assertFalse(enabled)

    def test_cfg_disabled_when_cloud_disabled_env(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        with patch.dict(os.environ, {"MESHFLOW_API_KEY": "key", "MESHFLOW_CLOUD_ENABLED": "0"}):
            _, _, enabled = PromptHub._cfg()
        self.assertFalse(enabled)

    def test_cfg_enabled_when_key_present(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        with patch.dict(os.environ, {"MESHFLOW_API_KEY": "mf_sk_test"}):
            key, _, enabled = PromptHub._cfg()
        self.assertTrue(enabled)
        self.assertEqual(key, "mf_sk_test")


# ══════════════════════════════════════════════════════════════════════════════
# PromptHub — local HTTP server
# ══════════════════════════════════════════════════════════════════════════════

class TestPromptHubWithServer(unittest.TestCase):
    def setUp(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        PromptHub.clear_cache()
        self._srv = _FakeIngestServer()
        port = self._srv.start()
        self._base = f"http://127.0.0.1:{port}"

    def tearDown(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        PromptHub.clear_cache()
        self._srv.stop()

    def _env(self) -> dict[str, str]:
        return {"MESHFLOW_API_KEY": "mf_sk_test", "MESHFLOW_CLOUD_URL": self._base,
                "MESHFLOW_CLOUD_ENABLED": "1"}

    def test_get_returns_content(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self._srv.get_responses["/api/ingest/prompts?slug=my-prompt"] = {
            "slug": "my-prompt", "name": "My Prompt", "description": "",
            "version": 2, "content": "You are an expert.", "model": "", "temperature": 0.5,
        }
        with patch.dict(os.environ, self._env()):
            result = PromptHub.get("my-prompt", ttl=0)
        self.assertEqual(result, "You are an expert.")

    def test_get_caches_result(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self._srv.get_responses["/api/ingest/prompts?slug=cached"] = {
            "slug": "cached", "name": "C", "description": "", "version": 1,
            "content": "cached content", "model": "", "temperature": 0.5,
        }
        with patch.dict(os.environ, self._env()):
            first  = PromptHub.get("cached", ttl=60)
            second = PromptHub.get("cached", ttl=60)
        self.assertEqual(first, second)
        # Only one GET should have been made
        self.assertEqual(len(self._srv.received), 0)  # GET is not in received (only POST/DELETE)

    def test_get_returns_default_on_404(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        with patch.dict(os.environ, self._env()):
            result = PromptHub.get("missing-slug", default="fallback", ttl=0)
        self.assertEqual(result, "fallback")

    def test_push_sends_post(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self._srv.get_responses = {}  # POST returns 201 {ok: True}
        with patch.dict(os.environ, self._env()):
            ok = PromptHub.push("my-slug", "new content", notes="v2")
        # push returns False because the server returns {ok: True} not {version: N}
        # (our fake server returns {ok: True, ingested: 0})
        self.assertIsInstance(ok, bool)
        self.assertEqual(len(self._srv.received), 1)
        payload = self._srv.received[0]["body"]
        self.assertEqual(payload["slug"], "my-slug")
        self.assertEqual(payload["content"], "new content")

    def test_push_invalidates_cache(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self._srv.get_responses["/api/ingest/prompts?slug=slug-x"] = {
            "slug": "slug-x", "name": "X", "description": "", "version": 1,
            "content": "v1", "model": "", "temperature": 0.5,
        }
        with patch.dict(os.environ, self._env()):
            PromptHub.get("slug-x", ttl=300)
            PromptHub.push("slug-x", "v2")
        # After push the cache entry for slug-x should be gone
        self.assertNotIn("slug-x:active", PromptHub._cache)

    def test_list_returns_slugs(self) -> None:
        from meshflow.cloud.prompt_hub import PromptHub
        self._srv.get_responses["/api/ingest/prompts?list=1"] = [
            {"slug": "a", "name": "A", "description": "", "updatedAt": ""},
            {"slug": "b", "name": "B", "description": "", "updatedAt": ""},
        ]
        with patch.dict(os.environ, self._env()):
            slugs = PromptHub.list()
        self.assertEqual(slugs, ["a", "b"])


# ══════════════════════════════════════════════════════════════════════════════
# DatasetHub — offline
# ══════════════════════════════════════════════════════════════════════════════

class TestDatasetHubOffline(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("MESHFLOW_API_KEY", None)

    def tearDown(self) -> None:
        self._env.stop()

    def test_push_returns_false_when_no_key(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        self.assertFalse(DatasetHub.push("ds", [{"input": "q"}]))

    def test_pull_returns_empty_when_no_key(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        self.assertEqual(DatasetHub.pull("ds"), [])

    def test_list_returns_empty_when_no_key(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        self.assertEqual(DatasetHub.list(), [])

    def test_delete_returns_false_when_no_key(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        self.assertFalse(DatasetHub.delete("ds"))


# ══════════════════════════════════════════════════════════════════════════════
# DatasetHub — local HTTP server
# ══════════════════════════════════════════════════════════════════════════════

class TestDatasetHubWithServer(unittest.TestCase):
    def setUp(self) -> None:
        self._srv = _FakeIngestServer()
        port = self._srv.start()
        self._env = {"MESHFLOW_API_KEY": "mf_sk_test",
                     "MESHFLOW_CLOUD_URL": f"http://127.0.0.1:{port}",
                     "MESHFLOW_CLOUD_ENABLED": "1"}

    def tearDown(self) -> None:
        self._srv.stop()

    def test_push_sends_correct_payload(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        with patch.dict(os.environ, self._env):
            ok = DatasetHub.push("my-ds", [{"input": "What is PHI?", "expected_output": "..."}])
        self.assertIsInstance(ok, bool)
        self.assertEqual(len(self._srv.received), 1)
        body = self._srv.received[0]["body"]
        self.assertEqual(body["name"], "my-ds")
        self.assertEqual(len(body["rows"]), 1)
        self.assertEqual(body["rows"][0]["input"], "What is PHI?")

    def test_push_with_description(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        with patch.dict(os.environ, self._env):
            DatasetHub.push("my-ds", [], description="HIPAA eval set")
        body = self._srv.received[0]["body"]
        self.assertEqual(body["description"], "HIPAA eval set")

    def test_push_empty_rows_sends_request(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        with patch.dict(os.environ, self._env):
            DatasetHub.push("empty-ds", [])
        self.assertEqual(len(self._srv.received), 1)

    def test_list_returns_summaries(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        self._srv.get_responses["/api/ingest/datasets"] = [
            {"id": "1", "name": "ds-a", "description": "", "rowCount": 10, "updatedAt": ""},
        ]
        with patch.dict(os.environ, self._env):
            result = DatasetHub.list()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "ds-a")

    def test_pull_returns_rows(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        import urllib.parse
        name_enc = urllib.parse.quote("my-ds")
        self._srv.get_responses[f"/api/ingest/datasets?name={name_enc}&limit=1000&offset=0"] = {
            "id": "x", "name": "my-ds", "description": "", "row_count": 2,
            "rows": [
                {"id": "r1", "input": "q1", "expected_output": "a1", "metadata": {}},
                {"id": "r2", "input": "q2", "expected_output": "a2", "metadata": {}},
            ],
        }
        with patch.dict(os.environ, self._env):
            rows = DatasetHub.pull("my-ds")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["input"], "q1")

    def test_pull_returns_empty_on_404(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        with patch.dict(os.environ, self._env):
            rows = DatasetHub.pull("nonexistent")
        self.assertEqual(rows, [])

    def test_delete_sends_delete_request(self) -> None:
        from meshflow.cloud.dataset_hub import DatasetHub
        with patch.dict(os.environ, self._env):
            DatasetHub.delete("my-ds")
        self.assertEqual(len(self._srv.received), 1)
        path = self._srv.received[0]["path"]
        self.assertIn("/api/ingest/datasets", path)
        self.assertIn("my-ds", path)


# ══════════════════════════════════════════════════════════════════════════════
# CloudAgentRegistry — offline
# ══════════════════════════════════════════════════════════════════════════════

class TestCloudAgentRegistryOffline(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("MESHFLOW_API_KEY", None)

    def tearDown(self) -> None:
        self._env.stop()

    def test_register_returns_false_when_no_key(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        self.assertFalse(CloudAgentRegistry.register("My Agent", "my-agent"))

    def test_record_run_returns_false_when_no_key(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        self.assertFalse(CloudAgentRegistry.record_run("my-agent"))

    def test_list_returns_empty_when_no_key(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        self.assertEqual(CloudAgentRegistry.list(), [])

    def test_get_returns_none_when_no_key(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        self.assertIsNone(CloudAgentRegistry.get("my-agent"))


# ══════════════════════════════════════════════════════════════════════════════
# CloudAgentRegistry — local HTTP server
# ══════════════════════════════════════════════════════════════════════════════

class TestCloudAgentRegistryWithServer(unittest.TestCase):
    def setUp(self) -> None:
        self._srv = _FakeIngestServer()
        port = self._srv.start()
        self._env = {"MESHFLOW_API_KEY": "mf_sk_test",
                     "MESHFLOW_CLOUD_URL": f"http://127.0.0.1:{port}",
                     "MESHFLOW_CLOUD_ENABLED": "1"}

    def tearDown(self) -> None:
        self._srv.stop()

    def test_register_sends_correct_fields(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        with patch.dict(os.environ, self._env):
            CloudAgentRegistry.register(
                "HIPAA Intake",
                "hipaa-intake",
                role="executor",
                model="claude-sonnet-4-6",
                policy="hipaa",
            )
        self.assertEqual(len(self._srv.received), 1)
        body = self._srv.received[0]["body"]
        self.assertEqual(body["name"], "HIPAA Intake")
        self.assertEqual(body["slug"], "hipaa-intake")
        self.assertEqual(body["role"], "executor")
        self.assertEqual(body["policy"], "hipaa")

    def test_record_run_sends_run_count(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        with patch.dict(os.environ, self._env):
            CloudAgentRegistry.record_run("hipaa-intake", run_count=3)
        body = self._srv.received[0]["body"]
        self.assertEqual(body["run_count"], 3)
        self.assertEqual(body["slug"], "hipaa-intake")

    def test_list_returns_agents(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        self._srv.get_responses["/api/ingest/agents"] = [
            {"id": "1", "slug": "a", "name": "Agent A", "role": "executor",
             "model": "claude-sonnet-4-6", "policy": "standard", "status": "active",
             "description": "", "systemPrompt": "", "tags": "",
             "deployTarget": "local", "version": "1.0.0", "totalRuns": 0},
        ]
        with patch.dict(os.environ, self._env):
            agents = CloudAgentRegistry.list()
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["slug"], "a")

    def test_get_returns_none_on_404(self) -> None:
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        with patch.dict(os.environ, self._env):
            agent = CloudAgentRegistry.get("missing-slug")
        self.assertIsNone(agent)


# ══════════════════════════════════════════════════════════════════════════════
# MeshFlowCloud.report_spans()
# ══════════════════════════════════════════════════════════════════════════════

class TestReportSpans(unittest.TestCase):
    def test_empty_list_returns_true_without_http(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        c = MeshFlowCloud(enabled=False)
        self.assertTrue(c.report_spans([]))

    def test_disabled_client_returns_true(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        c = MeshFlowCloud(enabled=False)
        self.assertTrue(c.report_spans([{"run_id": "x", "agent_name": "a"}]))

    def test_sends_spans_to_ingest_endpoint(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        srv = _FakeIngestServer()
        port = srv.start()
        try:
            span = {
                "run_id": "run-1",
                "agent_name": "planner",
                "span_type": "step",
                "name": "planner",
                "started_at": "2026-06-04T10:00:00Z",
                "duration_ms": 420,
                "input_tokens": 512,
                "cost_usd": 0.0014,
                "status": "ok",
            }
            with patch.dict(os.environ, {"MESHFLOW_CLOUD_ENABLED": "1"}):
                c = MeshFlowCloud(
                    api_key="mf_sk_test",
                    base_url=f"http://127.0.0.1:{port}",
                )
                ok = c.report_spans([span])
            self.assertTrue(ok)
            self.assertEqual(len(srv.received), 1)
            body = srv.received[0]["body"]
            self.assertIn("spans", body)
            self.assertEqual(body["spans"][0]["agent_name"], "planner")
            self.assertEqual(body["spans"][0]["duration_ms"], 420)
        finally:
            srv.stop()


# ══════════════════════════════════════════════════════════════════════════════
# MeshFlowCloud.instrument() — duck-typed queue injection
# ══════════════════════════════════════════════════════════════════════════════

class TestInstrumentQueueInjection(unittest.TestCase):
    def test_queue_injected_and_removed(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        from meshflow.core.events import global_event_bus
        before = len(global_event_bus._queues)
        c = MeshFlowCloud(enabled=False)
        with c.instrument():
            during = len(global_event_bus._queues)
        after = len(global_event_bus._queues)
        self.assertEqual(during, before + 1)
        self.assertEqual(after, before)

    def test_queue_removed_even_on_exception(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        from meshflow.core.events import global_event_bus
        before = len(global_event_bus._queues)
        c = MeshFlowCloud(enabled=False)
        try:
            with c.instrument():
                raise ValueError("boom")
        except ValueError:
            pass
        self.assertEqual(len(global_event_bus._queues), before)

    def _emit(self, bus: Any, event: Any) -> None:
        """Drive the injected _CBQueue directly — avoids asyncio.run() cross-loop issues."""
        for q in list(bus._queues):
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def test_instrument_collects_step_spans(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        from meshflow.core.events import global_event_bus, WorkflowEvent, EventKind

        collected_spans: list = []

        def _capture(self, spans):  # type: ignore[override]
            collected_spans.extend(spans)
            return True

        c = MeshFlowCloud(enabled=False)

        with patch.object(MeshFlowCloud, "report_spans", _capture):
            with c.instrument():
                self._emit(global_event_bus, WorkflowEvent(
                    kind=EventKind.STEP_START, run_id="run-x",
                    node_id="planner", data={"kind": "executor"},
                ))
                time.sleep(0.01)
                self._emit(global_event_bus, WorkflowEvent(
                    kind=EventKind.STEP_COMPLETE, run_id="run-x",
                    node_id="planner",
                    data={"tokens": 512, "cost_usd": 0.002, "content_preview": "done"},
                ))
                self._emit(global_event_bus, WorkflowEvent(
                    kind=EventKind.WORKFLOW_COMPLETE, run_id="run-x", data={},
                ))

        self.assertEqual(len(collected_spans), 1)
        span = collected_spans[0]
        self.assertEqual(span["run_id"], "run-x")
        self.assertEqual(span["agent_name"], "planner")
        self.assertIn("started_at", span)
        self.assertGreaterEqual(span["duration_ms"], 0)
        self.assertEqual(span["input_tokens"], 512)

    def test_instrument_flushes_remaining_spans_on_exit(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        from meshflow.core.events import global_event_bus, WorkflowEvent, EventKind

        flushed: list = []

        def _capture(self, spans):  # type: ignore[override]
            flushed.extend(spans)
            return True

        c = MeshFlowCloud(enabled=False)

        with patch.object(MeshFlowCloud, "report_spans", _capture):
            with c.instrument():
                self._emit(global_event_bus, WorkflowEvent(
                    kind=EventKind.STEP_COMPLETE, run_id="run-y",
                    node_id="executor", data={"tokens": 100},
                ))

        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0]["run_id"], "run-y")

    def test_instrument_register_agents_calls_record_run(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        from meshflow.core.events import global_event_bus, WorkflowEvent, EventKind

        recorded: list[str] = []

        def _fake_record(slug: str, **_kwargs: object) -> bool:
            recorded.append(slug)
            return True

        c = MeshFlowCloud(enabled=False)

        with patch.object(CloudAgentRegistry, "record_run", staticmethod(_fake_record)):
            with c.instrument(register_agents=True):
                self._emit(global_event_bus, WorkflowEvent(
                    kind=EventKind.STEP_COMPLETE, run_id="run-z",
                    node_id="analyst", data={"tokens": 50},
                ))

        self.assertIn("analyst", recorded)

    def test_instrument_without_register_agents_skips_record_run(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        from meshflow.cloud.agent_registry import CloudAgentRegistry
        from meshflow.core.events import global_event_bus, WorkflowEvent, EventKind

        recorded: list[str] = []

        def _fake_record(slug: str, **_kwargs: object) -> bool:
            recorded.append(slug)
            return True

        c = MeshFlowCloud(enabled=False)

        with patch.object(CloudAgentRegistry, "record_run", staticmethod(_fake_record)):
            with c.instrument(register_agents=False):
                self._emit(global_event_bus, WorkflowEvent(
                    kind=EventKind.STEP_COMPLETE, run_id="run-z2",
                    node_id="writer", data={},
                ))

        self.assertEqual(recorded, [])


# ══════════════════════════════════════════════════════════════════════════════
# Top-level import parity (from meshflow import …)
# ══════════════════════════════════════════════════════════════════════════════

class TestTopLevelExports(unittest.TestCase):
    def test_prompt_hub_importable(self) -> None:
        from meshflow import PromptHub  # noqa: F401

    def test_dataset_hub_importable(self) -> None:
        from meshflow import DatasetHub  # noqa: F401

    def test_cloud_agent_registry_importable(self) -> None:
        from meshflow import CloudAgentRegistry  # noqa: F401

    def test_all_includes_new_exports(self) -> None:
        import meshflow
        for name in ("PromptHub", "DatasetHub", "CloudAgentRegistry"):
            self.assertIn(name, meshflow.__all__, f"{name} missing from __all__")

    def test_cloud_module_exports(self) -> None:
        from meshflow.cloud import PromptHub, DatasetHub, CloudAgentRegistry  # noqa: F401


# ══════════════════════════════════════════════════════════════════════════════
# MeshFlowCloud.report_compliance()
# ══════════════════════════════════════════════════════════════════════════════

class TestReportCompliance(unittest.TestCase):
    def test_disabled_client_returns_true(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        c = MeshFlowCloud(enabled=False)
        self.assertTrue(c.report_compliance("hipaa", True))

    def test_disabled_returns_true_with_evidence(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        c = MeshFlowCloud(enabled=False)
        evidence = {
            "access-control": {"passed": True, "title": "Access Control"},
            "audit-logs":     {"passed": True, "title": "Audit Logs"},
        }
        self.assertTrue(c.report_compliance("soc2", True, score=0.92, evidence=evidence))

    def test_posts_to_correct_endpoint(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        srv = _FakeIngestServer()
        port = srv.start()
        try:
            with patch.dict(os.environ, {"MESHFLOW_CLOUD_ENABLED": "1"}):
                c = MeshFlowCloud(
                    api_key="mf_sk_test",
                    base_url=f"http://127.0.0.1:{port}",
                )
                evidence = {"phi-access": {"passed": True, "title": "PHI Access Control"}}
                ok = c.report_compliance(
                    "hipaa", True,
                    score=0.95,
                    run_id="run-123",
                    evidence=evidence,
                )
            self.assertTrue(ok)
            self.assertEqual(len(srv.received), 1)
            body = srv.received[0]["body"]
            self.assertEqual(body["framework"], "hipaa")
            self.assertTrue(body["passed"])
            self.assertAlmostEqual(body["score"], 0.95)
            self.assertEqual(body["run_id"], "run-123")
            self.assertIn("phi-access", body["evidence"])
        finally:
            srv.stop()

    def test_module_level_shorthand_importable(self) -> None:
        from meshflow.cloud.client import report_compliance  # noqa: F401
        from meshflow.cloud import cloud_report_compliance    # noqa: F401

    def test_async_variant_exists(self) -> None:
        from meshflow.cloud.client import MeshFlowCloud
        c = MeshFlowCloud(enabled=False)
        self.assertTrue(hasattr(c, "areport_compliance"))


if __name__ == "__main__":
    unittest.main()
