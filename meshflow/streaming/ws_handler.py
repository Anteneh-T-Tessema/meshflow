"""WebSocket upgrade and event streaming handler for MeshFlow Studio."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
from meshflow.core.events import global_event_bus, WorkflowEvent


def make_ws_frame(message: str) -> bytes:
    """Encode a text message into a WebSocket frame (Server-to-Client, unmasked)."""
    payload = message.encode("utf-8")
    payload_len = len(payload)
    if payload_len <= 125:
        header = bytes([0x81, payload_len])
    elif payload_len <= 65535:
        header = bytes([0x81, 126]) + payload_len.to_bytes(2, byteorder="big")
    else:
        header = bytes([0x81, 127]) + payload_len.to_bytes(8, byteorder="big")
    return header + payload


def handle_websocket_connection(handler: Any, run_id: str | None = None) -> None:
    """Upgrade the connection to WebSocket and stream events from global_event_bus."""
    key = handler.headers.get("Sec-WebSocket-Key")
    if not key:
        handler.send_error(400, "Missing Sec-WebSocket-Key header")
        return

    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    accept_val = base64.b64encode(hashlib.sha1((key + guid).encode()).digest()).decode()

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_val}\r\n\r\n"
    ).encode()
    handler.wfile.write(response)
    handler.wfile.flush()

    # Stream history first
    history = global_event_bus.history(run_id=run_id)
    for event in history:
        try:
            frame = make_ws_frame(json.dumps(event.to_dict()))
            handler.wfile.write(frame)
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    last_idx = len(global_event_bus._history)

    # Stream new events
    while True:
        try:
            current_history = list(global_event_bus._history)
            if len(current_history) > last_idx:
                new_events = current_history[last_idx:]
                for event in new_events:
                    if run_id is None or event.run_id == run_id:
                        frame = make_ws_frame(json.dumps(event.to_dict()))
                        handler.wfile.write(frame)
                        handler.wfile.flush()
                last_idx = len(current_history)
            time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, OSError):
            break
        except Exception:
            break
