"""Tests for MeshFlowProxy — wire-level OpenAI-compatible enforcement."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch
from meshflow.proxy.openai_proxy import (
    MeshFlowProxy,
    ProxyToolCallEvent,
    ProxyDecision,
    _ProxiedResponse,
    _extract_tool_calls,
    _tc_name,
    _tc_args,
)
from meshflow.core.tool_intercept import (
    AllowListInterceptor,
    PolicyToolCallInterceptor,
    ToolCallDecision,
)


# ── Mock OpenAI response builder ──────────────────────────────────────────────

def _tool_call(name: str, args: dict, call_id: str = "call-1") -> MagicMock:
    tc = MagicMock()
    tc.id = call_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def _response(tool_calls: list | None = None, model: str = "gpt-4o") -> MagicMock:
    resp = MagicMock()
    resp.model = model
    msg = MagicMock()
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    resp.choices = [choice]
    return resp


def _deny_interceptor(rule: str = "deny-all") -> PolicyToolCallInterceptor:
    from meshflow.policy.engine import PolicyStore, PolicyEngine, PolicyAction
    store = PolicyStore()
    engine = PolicyEngine(store, audit=False)
    engine.evaluate = MagicMock(return_value=MagicMock(
        is_allowed=False, rule_name=rule, reason="blocked"
    ))
    return PolicyToolCallInterceptor(engine)


def _allow_interceptor() -> PolicyToolCallInterceptor:
    from meshflow.policy.engine import PolicyStore, PolicyEngine, PolicyAction
    store = PolicyStore()
    engine = PolicyEngine(store, audit=False)
    engine.evaluate = MagicMock(return_value=MagicMock(
        is_allowed=True, rule_name="", reason="allow", matched=False
    ))
    return PolicyToolCallInterceptor(engine)


# ── Helpers ───────────────────────────────────────────────────────────────────

def test_extract_tool_calls_from_response():
    tc = _tool_call("search", {"q": "test"})
    resp = _response([tc])
    calls = _extract_tool_calls(resp)
    assert len(calls) == 1


def test_extract_tool_calls_empty_when_none():
    resp = _response(tool_calls=None)
    assert _extract_tool_calls(resp) == []


def test_tc_name():
    tc = _tool_call("write_file", {})
    assert _tc_name(tc) == "write_file"


def test_tc_args_parses_json():
    tc = _tool_call("search", {"q": "hello"})
    assert _tc_args(tc) == {"q": "hello"}


# ── MeshFlowProxy construction ────────────────────────────────────────────────

def test_proxy_wraps_client():
    client = MagicMock()
    proxy = MeshFlowProxy(client)
    assert proxy._client is client


def test_proxy_chat_completions_attribute():
    proxy = MeshFlowProxy(MagicMock())
    assert hasattr(proxy.chat, "completions")
    assert hasattr(proxy.chat.completions, "create")


def test_proxy_stats_initial():
    proxy = MeshFlowProxy(MagicMock())
    assert proxy.stats() == {"allowed_tool_calls": 0, "blocked_tool_calls": 0}


# ── Pass-through when no interceptor ─────────────────────────────────────────

def test_proxy_passthrough_without_interceptor():
    client = MagicMock()
    tc = _tool_call("search", {"q": "test"})
    client.chat.completions.create.return_value = _response([tc])

    proxy = MeshFlowProxy(client)
    resp = proxy.chat.completions.create(model="gpt-4o", messages=[])

    # No interceptor — original response returned unchanged
    assert resp is client.chat.completions.create.return_value


# ── Allow path ────────────────────────────────────────────────────────────────

def test_proxy_allows_tool_call():
    client = MagicMock()
    tc = _tool_call("search", {"q": "test"})
    client.chat.completions.create.return_value = _response([tc])

    proxy = MeshFlowProxy(client, tool_call_interceptor=_allow_interceptor())
    resp = proxy.chat.completions.create(model="gpt-4o", messages=[])

    # All calls allowed — original response (no blocked calls = no wrapper)
    assert proxy.stats()["allowed_tool_calls"] == 1
    assert proxy.stats()["blocked_tool_calls"] == 0


# ── Block path ────────────────────────────────────────────────────────────────

def test_proxy_blocks_tool_call():
    client = MagicMock()
    tc = _tool_call("write_file", {"path": "/etc/passwd"})
    client.chat.completions.create.return_value = _response([tc])

    proxy = MeshFlowProxy(client, tool_call_interceptor=_deny_interceptor("no-write"))
    resp = proxy.chat.completions.create(model="gpt-4o", messages=[])

    assert proxy.stats()["blocked_tool_calls"] == 1
    assert isinstance(resp, _ProxiedResponse)


def test_proxied_response_removes_blocked_from_choices():
    client = MagicMock()
    tc = _tool_call("exec_shell", {"cmd": "rm -rf /"})
    client.chat.completions.create.return_value = _response([tc])

    proxy = MeshFlowProxy(client, tool_call_interceptor=_deny_interceptor())
    resp = proxy.chat.completions.create(model="gpt-4o", messages=[])

    # Blocked call removed from message.tool_calls
    tool_calls = resp.choices[0].message.tool_calls
    assert tool_calls is None or tool_calls == []


def test_proxy_blocked_calls_list():
    client = MagicMock()
    tc = _tool_call("delete_db", {})
    client.chat.completions.create.return_value = _response([tc])

    proxy = MeshFlowProxy(client, tool_call_interceptor=_deny_interceptor())
    proxy.chat.completions.create(model="gpt-4o", messages=[])

    blocked = proxy.blocked_calls()
    assert len(blocked) == 1
    assert blocked[0].tool_name == "delete_db"


# ── Mixed allow/block ─────────────────────────────────────────────────────────

def test_proxy_mixed_calls():
    """One allowed tool call + one blocked = only allowed survives."""
    client = MagicMock()
    tc_allow = _tool_call("search", {"q": "safe"}, "call-1")
    tc_block = _tool_call("exec_shell", {"cmd": "rm -rf /"}, "call-2")
    client.chat.completions.create.return_value = _response([tc_allow, tc_block])

    # Allow "search", block everything else
    interceptor = AllowListInterceptor(["search"])
    proxy = MeshFlowProxy(client, tool_call_interceptor=interceptor)
    resp = proxy.chat.completions.create(model="gpt-4o", messages=[])

    assert proxy.stats()["allowed_tool_calls"] == 1
    assert proxy.stats()["blocked_tool_calls"] == 1
    tool_calls = resp.choices[0].message.tool_calls
    assert len(tool_calls) == 1
    assert _tc_name(tool_calls[0]) == "search"


# ── on_block callback ─────────────────────────────────────────────────────────

def test_proxy_on_block_callback_fires():
    fired: list[ProxyToolCallEvent] = []
    client = MagicMock()
    tc = _tool_call("drop_table", {})
    client.chat.completions.create.return_value = _response([tc])

    proxy = MeshFlowProxy(
        client,
        tool_call_interceptor=_deny_interceptor(),
        on_block=fired.append,
    )
    proxy.chat.completions.create(model="gpt-4o", messages=[])

    assert len(fired) == 1
    assert fired[0].tool_name == "drop_table"


# ── ProxiedResponse passthrough ───────────────────────────────────────────────

def test_proxied_response_forwards_unknown_attrs():
    original = _response([_tool_call("x", {})])
    original.id = "chatcmpl-123"
    pr = _ProxiedResponse(original, allowed_tool_calls=[], blocked_tool_calls=[])
    assert pr.id == "chatcmpl-123"


# ── from meshflow top-level import ───────────────────────────────────────────

def test_proxy_importable_from_meshflow():
    from meshflow import MeshFlowProxy as _P
    assert _P is MeshFlowProxy


# ── Streaming ─────────────────────────────────────────────────────────────────

from meshflow.proxy.openai_proxy import _EnforcedStream, _assemble_tool_calls_from_chunks


def _stream_chunk(content: str | None = None, tool_calls: list | None = None,
                  finish_reason: str | None = None) -> MagicMock:
    """Build a mock streaming chunk."""
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    return chunk


def _tc_delta(index: int, name: str = "", args_fragment: str = "",
              call_id: str = "") -> MagicMock:
    """Build a mock tool call delta."""
    tc = MagicMock()
    tc.index = index
    tc.id = call_id
    fn = MagicMock()
    fn.name = name
    fn.arguments = args_fragment
    tc.function = fn
    return tc


def test_assemble_tool_calls_single_call():
    chunks = [
        _stream_chunk(tool_calls=[_tc_delta(0, "search", '{"q":', "call-1")]),
        _stream_chunk(tool_calls=[_tc_delta(0, "", '"hello"}')]),
        _stream_chunk(finish_reason="tool_calls"),
    ]
    assembled = _assemble_tool_calls_from_chunks(chunks)
    assert assembled[0]["name"] == "search"
    assert assembled[0]["id"] == "call-1"
    assert assembled[0]["arguments"] == '{"q":"hello"}'


def test_assemble_tool_calls_two_parallel_calls():
    chunks = [
        _stream_chunk(tool_calls=[_tc_delta(0, "search", '{"q":', "c-1"),
                                   _tc_delta(1, "read", '{"p":', "c-2")]),
        _stream_chunk(tool_calls=[_tc_delta(0, "", '"hi"}'),
                                   _tc_delta(1, "", '"/tmp"}')]),
    ]
    assembled = _assemble_tool_calls_from_chunks(chunks)
    assert assembled[0]["name"] == "search"
    assert assembled[1]["name"] == "read"
    assert assembled[1]["arguments"] == '{"p":"/tmp"}'


def test_assemble_ignores_content_chunks():
    chunks = [
        _stream_chunk(content="Hello"),
        _stream_chunk(content=" world"),
        _stream_chunk(finish_reason="stop"),
    ]
    assembled = _assemble_tool_calls_from_chunks(chunks)
    assert assembled == {}


def test_enforced_stream_passthrough_no_interceptor():
    chunks = [
        _stream_chunk(content="hi"),
        _stream_chunk(content=" there"),
    ]
    proxy = MeshFlowProxy(MagicMock())
    stream = _EnforcedStream(iter(chunks), proxy)
    yielded = list(stream)
    assert len(yielded) == 2


def test_enforced_stream_allows_tool_call():
    tc_chunk = _stream_chunk(tool_calls=[_tc_delta(0, "search", '{"q":"ok"}', "c-1")])
    finish = _stream_chunk(finish_reason="tool_calls")
    chunks = [tc_chunk, finish]

    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks)
    proxy = MeshFlowProxy(client, tool_call_interceptor=_allow_interceptor())

    yielded = list(proxy.chat.completions.create(stream=True, model="gpt-4o", messages=[]))
    assert proxy.stats()["allowed_tool_calls"] == 1
    assert proxy.stats()["blocked_tool_calls"] == 0
    # Both chunks (tc + finish) should be yielded
    assert len(yielded) == 2


def test_enforced_stream_blocks_tool_call():
    tc_chunk = _stream_chunk(tool_calls=[_tc_delta(0, "exec_shell", '{"cmd":"rm -rf /"}', "c-1")])
    finish = _stream_chunk(finish_reason="tool_calls")
    chunks = [tc_chunk, finish]

    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks)
    proxy = MeshFlowProxy(client, tool_call_interceptor=_deny_interceptor())

    yielded = list(proxy.chat.completions.create(stream=True, model="gpt-4o", messages=[]))
    assert proxy.stats()["blocked_tool_calls"] == 1
    # Tool call chunk dropped; only finish chunk yielded
    assert len(yielded) == 1


def test_enforced_stream_mixed_content_and_tool_calls():
    """Content chunks pass through; blocked tool call chunk is dropped."""
    text1 = _stream_chunk(content="I'll search for")
    text2 = _stream_chunk(content=" that.")
    tc_block = _stream_chunk(tool_calls=[_tc_delta(0, "exec_shell", '{}', "c-bad")])
    tc_allow = _stream_chunk(tool_calls=[_tc_delta(1, "search", '{"q":"safe"}', "c-ok")])
    finish = _stream_chunk(finish_reason="tool_calls")
    chunks = [text1, text2, tc_block, tc_allow, finish]

    interceptor = AllowListInterceptor(["search"])
    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks)
    proxy = MeshFlowProxy(client, tool_call_interceptor=interceptor)

    yielded = list(proxy.chat.completions.create(stream=True, model="gpt-4o", messages=[]))
    assert proxy.stats()["blocked_tool_calls"] == 1
    assert proxy.stats()["allowed_tool_calls"] == 1
    # text1, text2, tc_allow, finish — tc_block dropped
    assert len(yielded) == 4


def test_enforced_stream_on_block_callback():
    fired: list = []
    tc_chunk = _stream_chunk(tool_calls=[_tc_delta(0, "drop_db", '{}', "c-1")])
    chunks = [tc_chunk, _stream_chunk(finish_reason="tool_calls")]

    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks)
    proxy = MeshFlowProxy(
        client,
        tool_call_interceptor=_deny_interceptor(),
        on_block=fired.append,
    )
    list(proxy.chat.completions.create(stream=True, model="gpt-4o", messages=[]))
    assert len(fired) == 1
    assert fired[0].tool_name == "drop_db"
