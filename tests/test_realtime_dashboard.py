"""Tests for the real-time WebSocket dashboard backend and ws_handler."""

from __future__ import annotations

import json
from unittest.mock import patch
import pytest
from meshflow.core.events import global_event_bus, WorkflowEvent, EventKind
from meshflow.streaming.ws_handler import make_ws_frame, handle_websocket_connection


class FakeHeaders:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name, default)


class FakeHandler:
    def __init__(self, key: str | None = "dGhlIHNhbXBsZSBub25jZQ==") -> None:
        headers_dict = {}
        if key:
            headers_dict["Sec-WebSocket-Key"] = key
        self.headers = FakeHeaders(headers_dict)
        self.wfile = FakeWfile()
        self.errors: list[tuple[int, str]] = []

    def send_error(self, code: int, message: str) -> None:
        self.errors.append((code, message))


class FakeWfile:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    def flush(self) -> None:
        pass


def test_make_ws_frame_small():
    frame = make_ws_frame("hello")
    assert frame[0] == 0x81
    assert frame[1] == 5
    assert frame[2:] == b"hello"


def test_make_ws_frame_medium():
    msg = "x" * 200
    frame = make_ws_frame(msg)
    assert frame[0] == 0x81
    assert frame[1] == 126
    length = int.from_bytes(frame[2:4], byteorder="big")
    assert length == 200
    assert frame[4:] == msg.encode()


@pytest.mark.anyio
async def test_websocket_handshake_and_filtering():
    global_event_bus.clear()

    evt1 = WorkflowEvent(EventKind.STEP_START, run_id="run-1", node_id="node-a")
    evt2 = WorkflowEvent(EventKind.STEP_START, run_id="run-2", node_id="node-b")

    await global_event_bus.emit(evt1)
    await global_event_bus.emit(evt2)

    handler = FakeHandler()

    with patch("time.sleep", side_effect=OSError("Break loop for test")):
        handle_websocket_connection(handler, run_id="run-1")

    # Handshake response checks
    assert b"HTTP/1.1 101 Switching Protocols" in handler.wfile.written
    assert b"Upgrade: websocket" in handler.wfile.written
    assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in handler.wfile.written

    # Check that only run-1's event was sent
    parts = handler.wfile.written.split(b"\r\n\r\n", 1)
    frames_data = parts[1]
    assert len(frames_data) > 0
    assert frames_data[0] == 0x81

    # Decode payload from frame (header is 2 bytes for small payload)
    payload_len = frames_data[1]
    payload = frames_data[2 : 2 + payload_len].decode()
    event_dict = json.loads(payload)
    assert event_dict["run_id"] == "run-1"
    assert event_dict["node_id"] == "node-a"


def test_websocket_handshake_missing_key():
    handler = FakeHandler(key=None)
    handle_websocket_connection(handler)
    assert len(handler.errors) == 1
    assert handler.errors[0][0] == 400


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def test_realtime_dashboard_html_route(tmp_path):
    import urllib.request
    import time
    from meshflow.studio.trace_server import TraceServer

    db = str(tmp_path / "test_ledger.db")
    # create empty file/db for server init
    with open(db, "w") as f:
        pass

    port = _free_port()
    srv = TraceServer(db=db, port=port)
    srv.start(daemon=True)
    time.sleep(0.2)

    try:
        # Request /realtime
        url = f"http://127.0.0.1:{port}/realtime"
        with urllib.request.urlopen(url, timeout=3) as r:
            content = r.read().decode()
            status = r.status
        assert status == 200
        assert "MeshFlow Real-Time Observability" in content
        assert "costChart" in content
    finally:
        srv.stop()


def test_realtime_dashboard_css_route(tmp_path):
    import urllib.request
    import time
    from meshflow.studio.trace_server import TraceServer

    db = str(tmp_path / "test_ledger.db")
    with open(db, "w") as f:
        pass

    port = _free_port()
    srv = TraceServer(db=db, port=port)
    srv.start(daemon=True)
    time.sleep(0.2)

    try:
        # Request /realtime/style.css
        url = f"http://127.0.0.1:{port}/realtime/style.css"
        with urllib.request.urlopen(url, timeout=3) as r:
            content = r.read().decode()
            status = r.status
        assert status == 200
        assert ":root" in content
        assert "--bg-primary" in content
    finally:
        srv.stop()

