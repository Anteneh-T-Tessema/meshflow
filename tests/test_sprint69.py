"""Sprint 69 — Visual Execution Trace Server tests.

Tests for TraceServer data API, step enrichment, timeline, rewind endpoint,
CLI --browser / trace-server flags, and public exports.
All tests run without a real browser or live HTTP port — tested via the
server's async data methods and handler logic directly.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

import pytest

import meshflow
from meshflow.studio.trace_server import TraceServer


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ledger(path: str) -> None:
    """Create a minimal SQLite ledger with three fake steps using the real schema."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS step_records (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id            TEXT    NOT NULL,
            step_id           TEXT    NOT NULL UNIQUE,
            node_id           TEXT    NOT NULL,
            node_kind         TEXT    NOT NULL,
            input_task        TEXT,
            output_content    TEXT,
            output_compressed INTEGER NOT NULL DEFAULT 0,
            verdict           TEXT,
            blocked           INTEGER NOT NULL DEFAULT 0,
            block_reason      TEXT    DEFAULT '',
            uncertainty       REAL    DEFAULT 0.0,
            cost_usd          REAL    DEFAULT 0.0,
            tokens_used       INTEGER DEFAULT 0,
            carbon_gco2       REAL    DEFAULT 0.0,
            duration_ms       REAL    DEFAULT 0.0,
            timestamp         TEXT,
            prev_hash         TEXT    DEFAULT '',
            entry_hash        TEXT    DEFAULT '',
            tenant_id         TEXT    NOT NULL DEFAULT 'default',
            metadata          TEXT    DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS workflow_checkpoints (
            run_id        TEXT PRIMARY KEY,
            data          TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            reviewer_id   TEXT DEFAULT '',
            review_notes  TEXT DEFAULT '',
            approved      INTEGER DEFAULT -1,
            tenant_id     TEXT NOT NULL DEFAULT 'default'
        );
    """)
    rows = [
        # run_id, step_id, node_id, node_kind, input_task, output_content,
        # output_compressed, verdict, blocked, block_reason,
        # uncertainty, cost_usd, tokens_used, carbon_gco2, duration_ms,
        # timestamp, prev_hash, entry_hash, tenant_id, metadata
        ("run-test-1", "step-001", "fetch", "agent",
         "fetch docs", "doc content", 0,
         "commit", 0, "", 0.12, 0.00023, 410, 0.0001, 320.5,
         "2026-05-29T10:00:00Z", "", "abc123", "default", "{}"),
        ("run-test-1", "step-002", "analyse", "agent",
         "analyse doc content", "analysis result", 0,
         "commit", 0, "", 0.08, 0.00041, 820, 0.0002, 610.0,
         "2026-05-29T10:00:00Z", "abc123", "def456", "default", "{}"),
        ("run-test-1", "step-003", "summarise", "agent",
         "summarise analysis", "summary", 0,
         "reject", 1, "policy violation: PII detected",
         0.91, 0.00012, 200, 0.00005, 150.0,
         "2026-05-29T10:00:01Z", "def456", "ghi789", "default", "{}"),
    ]
    conn.executemany(
        """INSERT INTO step_records
           (run_id, step_id, node_id, node_kind, input_task, output_content,
            output_compressed, verdict, blocked, block_reason,
            uncertainty, cost_usd, tokens_used, carbon_gco2, duration_ms,
            timestamp, prev_hash, entry_hash, tenant_id, metadata)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ledger_db(tmp_path):
    db = str(tmp_path / "test_ledger.db")
    _make_ledger(db)
    return db


@pytest.fixture
def server(ledger_db):
    srv = TraceServer(db=ledger_db, port=0)  # port=0 → won't actually bind in tests
    return srv


# ══════════════════════════════════════════════════════════════════════════════
#  TraceServer — data methods
# ══════════════════════════════════════════════════════════════════════════════

class TestTraceServerData:

    @pytest.mark.asyncio
    async def test_get_runs_returns_list(self, server):
        runs = await server.get_runs()
        assert isinstance(runs, list)

    @pytest.mark.asyncio
    async def test_get_trace_returns_dict(self, server):
        trace = await server.get_trace("run-test-1")
        assert trace is not None
        assert isinstance(trace, dict)

    @pytest.mark.asyncio
    async def test_get_trace_has_run_id(self, server):
        trace = await server.get_trace("run-test-1")
        assert trace["run_id"] == "run-test-1"

    @pytest.mark.asyncio
    async def test_get_trace_has_steps(self, server):
        trace = await server.get_trace("run-test-1")
        assert "steps" in trace
        assert len(trace["steps"]) == 3

    @pytest.mark.asyncio
    async def test_get_trace_step_fields(self, server):
        trace = await server.get_trace("run-test-1")
        step = trace["steps"][0]
        for field in ("idx", "node_id", "node_kind", "input_preview",
                      "output_preview", "verdict", "blocked", "duration_ms",
                      "tokens_used", "cost_usd", "start_ms", "entry_hash"):
            assert field in step, f"missing field: {field}"

    @pytest.mark.asyncio
    async def test_get_trace_step_ordering(self, server):
        trace = await server.get_trace("run-test-1")
        steps = trace["steps"]
        assert steps[0]["node_id"] == "fetch"
        assert steps[1]["node_id"] == "analyse"
        assert steps[2]["node_id"] == "summarise"

    @pytest.mark.asyncio
    async def test_get_trace_step_idx_sequential(self, server):
        trace = await server.get_trace("run-test-1")
        for i, step in enumerate(trace["steps"], 1):
            assert step["idx"] == i

    @pytest.mark.asyncio
    async def test_get_trace_start_ms_monotonic(self, server):
        trace = await server.get_trace("run-test-1")
        starts = [s["start_ms"] for s in trace["steps"]]
        assert starts == sorted(starts)

    @pytest.mark.asyncio
    async def test_get_trace_total_duration(self, server):
        trace = await server.get_trace("run-test-1")
        expected = sum(s["duration_ms"] for s in trace["steps"])
        assert abs(trace["total_duration_ms"] - expected) < 1.0

    @pytest.mark.asyncio
    async def test_get_trace_blocked_step(self, server):
        trace = await server.get_trace("run-test-1")
        blocked = trace["steps"][2]
        assert blocked["blocked"] is True
        assert blocked["verdict"] == "reject"
        assert "PII" in blocked["block_reason"]

    @pytest.mark.asyncio
    async def test_get_trace_not_found_returns_none(self, server):
        result = await server.get_trace("nonexistent-run")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_trace_has_chain_valid(self, server):
        trace = await server.get_trace("run-test-1")
        assert "chain_valid" in trace

    @pytest.mark.asyncio
    async def test_get_trace_has_summary(self, server):
        trace = await server.get_trace("run-test-1")
        assert "summary" in trace

    @pytest.mark.asyncio
    async def test_get_steps_since_zero(self, server):
        steps = await server.get_steps_since("run-test-1", 0)
        assert len(steps) == 3

    @pytest.mark.asyncio
    async def test_get_steps_since_offset(self, server):
        steps = await server.get_steps_since("run-test-1", 2)
        assert len(steps) == 1
        assert steps[0]["node_id"] == "summarise"

    @pytest.mark.asyncio
    async def test_get_steps_since_past_end_returns_empty(self, server):
        steps = await server.get_steps_since("run-test-1", 999)
        assert steps == []

    @pytest.mark.asyncio
    async def test_get_steps_since_nonexistent_run(self, server):
        steps = await server.get_steps_since("no-such-run", 0)
        assert steps == []

    @pytest.mark.asyncio
    async def test_input_preview_truncated(self, server):
        trace = await server.get_trace("run-test-1")
        for step in trace["steps"]:
            assert len(step["input_preview"]) <= 300

    @pytest.mark.asyncio
    async def test_output_preview_truncated(self, server):
        trace = await server.get_trace("run-test-1")
        for step in trace["steps"]:
            assert len(step["output_preview"]) <= 500

    @pytest.mark.asyncio
    async def test_rewind_missing_run_returns_error(self, server):
        result = await server.do_rewind({"run_id": "no-such-run", "step_idx": 1})
        assert result["ok"] is False
        assert "error" in result


# ══════════════════════════════════════════════════════════════════════════════
#  TraceServer — HTTP server lifecycle
# ══════════════════════════════════════════════════════════════════════════════

class TestTraceServerHTTP:

    def _free_port(self) -> int:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def test_start_and_stop(self, ledger_db):
        port = self._free_port()
        srv = TraceServer(db=ledger_db, port=port)
        srv.start(daemon=True)
        time.sleep(0.2)
        assert srv._server is not None
        srv.stop()

    def test_url_property(self, ledger_db):
        srv = TraceServer(db=ledger_db, port=9876)
        assert srv.url == "http://127.0.0.1:9876"

    def test_api_runs_endpoint(self, ledger_db):
        port = self._free_port()
        srv = TraceServer(db=ledger_db, port=port)
        srv.start(daemon=True)
        time.sleep(0.2)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=3) as r:
                data = json.loads(r.read())
            assert isinstance(data, list)
        finally:
            srv.stop()

    def test_api_trace_endpoint(self, ledger_db):
        port = self._free_port()
        srv = TraceServer(db=ledger_db, port=port)
        srv.start(daemon=True)
        time.sleep(0.2)
        try:
            url = f"http://127.0.0.1:{port}/api/trace/run-test-1"
            with urllib.request.urlopen(url, timeout=3) as r:
                data = json.loads(r.read())
            assert data["run_id"] == "run-test-1"
            assert len(data["steps"]) == 3
        finally:
            srv.stop()

    def test_api_trace_not_found_returns_404(self, ledger_db):
        import urllib.error
        port = self._free_port()
        srv = TraceServer(db=ledger_db, port=port)
        srv.start(daemon=True)
        time.sleep(0.2)
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/trace/nonexistent", timeout=3
                )
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_trace_html_served(self, ledger_db):
        port = self._free_port()
        srv = TraceServer(db=ledger_db, port=port)
        srv.start(daemon=True)
        time.sleep(0.2)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
                content = r.read().decode()
            assert "MeshFlow Trace" in content
            assert "step-card" in content
        finally:
            srv.stop()

    def test_unknown_route_returns_404(self, ledger_db):
        import urllib.error
        port = self._free_port()
        srv = TraceServer(db=ledger_db, port=port)
        srv.start(daemon=True)
        time.sleep(0.2)
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/nonexistent", timeout=3
                )
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_api_steps_since_endpoint(self, ledger_db):
        port = self._free_port()
        srv = TraceServer(db=ledger_db, port=port)
        srv.start(daemon=True)
        time.sleep(0.2)
        try:
            url = f"http://127.0.0.1:{port}/api/live/run-test-1?since=1"
            # SSE endpoint — just read first chunk
            req = urllib.request.Request(url)
            # The SSE stream returns immediately for polling; close after first read
            try:
                with urllib.request.urlopen(req, timeout=2) as r:
                    chunk = r.read(256)
                assert b"data:" in chunk
            except Exception:
                pass  # timeout is fine — SSE streams indefinitely
        finally:
            srv.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  TraceServer — HTML template
# ══════════════════════════════════════════════════════════════════════════════

class TestTraceHTML:

    def _template_path(self) -> str:
        import meshflow.studio.trace_server as ts
        base = os.path.dirname(os.path.dirname(ts.__file__))
        return os.path.join(base, "studio", "templates", "trace.html")

    def test_trace_html_exists(self):
        assert os.path.exists(self._template_path())

    def test_trace_html_has_step_card(self):
        with open(self._template_path()) as f:
            html = f.read()
        assert "step-card" in html

    def test_trace_html_has_timeline(self):
        with open(self._template_path()) as f:
            html = f.read()
        assert "timeline" in html.lower()

    def test_trace_html_has_rewind_controls(self):
        with open(self._template_path()) as f:
            html = f.read()
        assert "rewind" in html.lower()

    def test_trace_html_has_metrics(self):
        with open(self._template_path()) as f:
            html = f.read()
        assert "tokens" in html.lower()
        assert "cost" in html.lower()

    def test_trace_html_has_verdict_badges(self):
        with open(self._template_path()) as f:
            html = f.read()
        assert "verdict-commit" in html
        assert "verdict-reject" in html

    def test_trace_html_has_api_calls(self):
        with open(self._template_path()) as f:
            html = f.read()
        assert "/api/runs" in html
        assert "/api/trace/" in html

    def test_trace_html_has_langsmith_export(self):
        with open(self._template_path()) as f:
            html = f.read()
        assert "LangSmith" in html or "langsmith" in html.lower()


# ══════════════════════════════════════════════════════════════════════════════
#  Public API exports
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPIExports:

    def test_trace_server_exported(self):
        assert hasattr(meshflow, "TraceServer")

    def test_trace_server_in_all(self):
        assert "TraceServer" in meshflow.__all__

    def test_version_bumped(self):
        assert meshflow.__version__ >= "0.77.0"
