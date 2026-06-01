"""Tests for meshflow.cloud reporter module."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

from meshflow.cloud.reporter import is_enabled, report_run
from meshflow.core.schemas import RunResult, RunStatus


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_result(**overrides: object) -> RunResult:
    defaults: dict = dict(
        run_id="run-test-001",
        status=RunStatus.COMPLETED,
        output="done",
        agent_states={"agent-a": MagicMock(), "agent-b": MagicMock()},
        total_cost_usd=0.0042,
        total_tokens=1800,
        total_carbon_g=0.003,
        duration_s=1.25,
        checkpoints=[],
        ledger_entries=5,
        trace_id="trace-abc",
        collusion_alerts=0,
        human_approvals_required=0,
        drift_alerts=0,
    )
    defaults.update(overrides)
    return RunResult(**defaults)


# ── is_enabled ────────────────────────────────────────────────────────────────

class TestIsEnabled(unittest.TestCase):
    def test_false_when_key_absent(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MESHFLOW_CLOUD_KEY", None)
            self.assertFalse(is_enabled())

    def test_false_when_key_empty(self) -> None:
        with patch.dict(os.environ, {"MESHFLOW_CLOUD_KEY": "   "}):
            self.assertFalse(is_enabled())

    def test_true_when_key_present(self) -> None:
        with patch.dict(os.environ, {"MESHFLOW_CLOUD_KEY": "mfc_testkey"}):
            self.assertTrue(is_enabled())


# ── report_run — no-op without key ───────────────────────────────────────────

class TestReportRunNoKey(unittest.TestCase):
    def test_returns_immediately_no_thread(self) -> None:
        before = threading.active_count()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MESHFLOW_CLOUD_KEY", None)
            report_run(_make_result())
        after = threading.active_count()
        # No new cloud-reporter thread should be spawned
        self.assertEqual(before, after)


# ── report_run — fires HTTP POST ─────────────────────────────────────────────

class TestReportRunHTTP(unittest.TestCase):
    """Spin up a real local HTTP server and verify the reporter POST."""

    received: list[dict]

    @classmethod
    def setUpClass(cls) -> None:
        cls.received = []

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                TestReportRunHTTP.received.append(json.loads(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def setUp(self) -> None:
        self.received.clear()

    def test_posts_correct_payload(self) -> None:
        endpoint = f"http://127.0.0.1:{self.port}/api/ingest/run"
        result = _make_result(
            run_id="run-xyz",
            total_cost_usd=1.2345,
            total_tokens=5000,
            duration_s=2.5,
            collusion_alerts=1,
            human_approvals_required=2,
        )

        with patch.dict(os.environ, {
            "MESHFLOW_CLOUD_KEY": "mfc_testkey123",
            "MESHFLOW_CLOUD_ENDPOINT": endpoint,
        }):
            report_run(
                result,
                workflow_name="test-workflow",
                agent_count=3,
                policy_mode="regulated",
                compliance="hipaa",
            )

        # Wait for the daemon thread to finish (max 3s)
        deadline = time.time() + 3.0
        while not self.received and time.time() < deadline:
            time.sleep(0.05)

        self.assertEqual(len(self.received), 1)
        payload = self.received[0]
        self.assertEqual(payload["run_id"], "run-xyz")
        self.assertEqual(payload["workflow_name"], "test-workflow")
        self.assertEqual(payload["agent_count"], 3)
        self.assertAlmostEqual(payload["total_cost_usd"], 1.2345, places=4)
        self.assertEqual(payload["total_tokens"], 5000)
        self.assertEqual(payload["duration_ms"], 2500)
        self.assertEqual(payload["policy"], "regulated")
        self.assertEqual(payload["compliance"], "hipaa")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["violations"], 1)
        self.assertEqual(payload["human_approvals_required"], 2)

    def test_header_contains_api_key(self) -> None:
        """Verify x-meshflow-key header is sent — uses its own server instance."""
        captured: list[str] = []

        class _KeyHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                captured.append(self.headers.get("x-meshflow-key", ""))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        srv = HTTPServer(("127.0.0.1", 0), _KeyHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            endpoint = f"http://127.0.0.1:{port}/api/ingest/run"
            with patch.dict(os.environ, {
                "MESHFLOW_CLOUD_KEY": "mfc_mykey",
                "MESHFLOW_CLOUD_ENDPOINT": endpoint,
            }):
                report_run(_make_result())

            deadline = time.time() + 3.0
            while not captured and time.time() < deadline:
                time.sleep(0.05)

            self.assertTrue(captured)
            self.assertEqual(captured[0], "mfc_mykey")
        finally:
            srv.shutdown()

    def test_silently_ignores_server_error(self) -> None:
        """Reporter must not raise when the server returns 500."""
        with patch.dict(os.environ, {
            "MESHFLOW_CLOUD_KEY": "mfc_testkey",
            "MESHFLOW_CLOUD_ENDPOINT": "http://127.0.0.1:1",  # nothing listening
        }):
            # Should not raise
            report_run(_make_result())
        time.sleep(0.1)  # give thread a moment


# ── Mesh.run() wires reporter ─────────────────────────────────────────────────

class TestMeshIntegration(unittest.TestCase):
    """Verify report_run is called by Mesh.run() when key is set."""

    def test_mesh_run_calls_reporter(self) -> None:
        import asyncio
        from unittest.mock import patch as mpatch

        from meshflow.core.mesh import Mesh

        fake_result = _make_result(run_id="mesh-run-001")

        with mpatch("meshflow.cloud.reporter.report_run") as mock_report:
            with mpatch.dict(os.environ, {"MESHFLOW_CLOUD_KEY": "mfc_key"}):
                mesh = Mesh(name="my-test-mesh")

                # Patch Mesh.stream to yield a run_complete event with the fake result
                async def _fake_stream(*args: object, **kwargs: object):  # type: ignore[return]
                    from meshflow.core.mesh import MeshEvent
                    yield MeshEvent(
                        event_type="run_complete",
                        agent_id="orchestrator",
                        role="orchestrator",
                        run_id="run-test-001",
                        step=0,
                        data={"_run_result": fake_result, "output": "done"},
                    )

                with mpatch.object(mesh, "stream", _fake_stream):
                    result = asyncio.run(mesh.run("test task"))

        self.assertEqual(result.run_id, "mesh-run-001")
        mock_report.assert_called_once()
        call_kwargs = mock_report.call_args
        self.assertEqual(call_kwargs.kwargs["workflow_name"], "my-test-mesh")

    def test_mesh_run_no_reporter_without_key(self) -> None:
        """Reporter must not fire when MESHFLOW_CLOUD_KEY is absent."""
        import asyncio
        from unittest.mock import patch as mpatch

        from meshflow.core.mesh import Mesh

        fake_result = _make_result()
        threads_before = threading.active_count()

        with mpatch.dict(os.environ, {}, clear=False):
            os.environ.pop("MESHFLOW_CLOUD_KEY", None)
            mesh = Mesh(name="no-key-mesh")

            async def _fake_stream(*args: object, **kwargs: object):  # type: ignore[return]
                from meshflow.core.mesh import MeshEvent
                yield MeshEvent(
                    event_type="run_complete",
                    agent_id="orchestrator",
                    role="orchestrator",
                    run_id="run-test-001",
                    step=0,
                    data={"_run_result": fake_result, "output": "done"},
                )

            with mpatch.object(mesh, "stream", _fake_stream):
                asyncio.run(mesh.run("test"))

        time.sleep(0.05)
        # No new reporter threads should have been spawned
        self.assertLessEqual(threading.active_count(), threads_before + 1)


# ── Mesh.name ─────────────────────────────────────────────────────────────────

class TestMeshName(unittest.TestCase):
    def test_name_defaults_to_empty_string(self) -> None:
        from meshflow.core.mesh import Mesh
        self.assertEqual(Mesh().name, "")

    def test_name_stored_correctly(self) -> None:
        from meshflow.core.mesh import Mesh
        self.assertEqual(Mesh(name="my-pipeline").name, "my-pipeline")


if __name__ == "__main__":
    unittest.main()
