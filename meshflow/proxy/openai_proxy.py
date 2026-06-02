"""MeshFlowProxy — wire-level OpenAI-compatible enforcement layer.

This is the abstraction level below StepRuntime and ToolCallInterceptor.
It wraps the OpenAI *client object* directly, so every tool call generated
by any LLM call — regardless of which framework orchestrates above — is
evaluated against policy before execution.

This closes the gap that the ToolCallInterceptor leaves open: nodes that
use LangGraph, CrewAI, or AutoGen internally manage their own LLM calls and
never pass through MeshFlow's governed execution path.  The proxy sits one
level lower — at the HTTP client — and intercepts universally.

Usage::

    from meshflow import MeshFlowProxy, PolicyToolCallInterceptor
    from meshflow.policy.engine import PolicyStore, PolicyEngine, PolicyAction
    import openai

    # Build your policy
    store = PolicyStore()
    store.add_rule("block-write-file", PolicyAction.DENY,
                   [("tool_name", "eq", "write_file")], framework="tool_calls")
    interceptor = PolicyToolCallInterceptor(PolicyEngine(store))

    # Wrap the OpenAI client — drop-in replacement
    client = MeshFlowProxy(openai.OpenAI(), tool_call_interceptor=interceptor)

    # Every framework that uses this client gets enforcement for free
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "delete the config file"}],
        tools=[{"type": "function", "function": {"name": "write_file", ...}}],
    )

LangGraph / CrewAI / AutoGen usage::

    # Pass the proxied client to the framework
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(openai_api_key="...", http_client=client._client._client)

    # Or use the lower-level openai client directly
    agent = autogen.AssistantAgent(
        "assistant",
        llm_config={"config_list": [{"model": "gpt-4o", "api_key": "..."}]},
    )
    # Then monkey-patch autogen's client:
    agent._oai_client = client
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ProxyToolCallEvent:
    """A tool call intercepted at the wire level."""
    tool_name: str
    args: dict[str, Any]
    call_id: str
    agent_id: str = "proxy"
    model: str = ""
    run_id: str = ""


@dataclass
class ProxyDecision:
    """Enforcement decision for a proxied tool call."""
    allowed: bool
    block_reason: str = ""
    modified_args: dict[str, Any] | None = None


# ── Proxy internals ───────────────────────────────────────────────────────────

class _ProxiedCompletions:
    """Intercepts ``client.chat.completions.create()`` calls."""

    def __init__(self, proxy: "MeshFlowProxy") -> None:
        self._proxy = proxy

    def create(self, **kwargs: Any) -> Any:
        """Synchronous create — mirrors openai.chat.completions.create().

        When ``stream=True`` the proxy buffers all chunks, assembles tool calls,
        enforces policy, then re-yields the filtered stream.  Text content
        chunks are passed through unmodified; tool call chunks for blocked
        calls are silently dropped.
        """
        if kwargs.get("stream"):
            raw_stream = self._proxy._raw_completions_create(**kwargs)
            return self._proxy._enforce_stream(raw_stream)
        response = self._proxy._raw_completions_create(**kwargs)
        return self._proxy._enforce_tool_calls(response, kwargs)

    async def acreate(self, **kwargs: Any) -> Any:
        """Async create — mirrors openai.chat.completions.acreate().

        When ``stream=True`` returns an async iterator with enforcement applied.
        The interceptor is awaited directly — no thread-pool hack needed.
        """
        if kwargs.get("stream"):
            raw_stream = await self._proxy._raw_completions_astream(**kwargs)
            return _AsyncEnforcedStream(raw_stream, self._proxy)
        response = await self._proxy._raw_completions_acreate(**kwargs)
        return self._proxy._enforce_tool_calls(response, kwargs)


class _ProxiedChat:
    def __init__(self, proxy: "MeshFlowProxy") -> None:
        self.completions = _ProxiedCompletions(proxy)


# ── Main proxy class ──────────────────────────────────────────────────────────

class MeshFlowProxy:
    """Drop-in OpenAI client wrapper with wire-level tool call enforcement.

    Parameters
    ----------
    client:
        An ``openai.OpenAI()`` instance (or any object with a
        ``chat.completions.create()`` method).  If ``None``, a real
        ``openai.OpenAI()`` client is constructed lazily when needed.
    tool_call_interceptor:
        Any ``ToolCallInterceptor`` — evaluated on every tool call the LLM
        generates before the call is returned to the caller.
    agent_id:
        Label used in interceptor audit logs to identify this proxy instance.
    on_block:
        Callback ``(event: ProxyToolCallEvent) -> None`` fired when a tool
        call is blocked.  Useful for alerting / SIEM integration.
    """

    def __init__(
        self,
        client: Any = None,
        tool_call_interceptor: Any = None,
        agent_id: str = "meshflow-proxy",
        on_block: Any = None,
    ) -> None:
        self._client = client
        self._interceptor = tool_call_interceptor
        self._agent_id = agent_id
        self._on_block = on_block
        self._blocked: list[ProxyToolCallEvent] = []
        self._allowed: list[ProxyToolCallEvent] = []
        self.chat = _ProxiedChat(self)

    # ── Raw client access ─────────────────────────────────────────────────────

    def _get_client(self) -> Any:
        if self._client is None:
            import openai
            self._client = openai.OpenAI()
        return self._client

    def _raw_completions_create(self, **kwargs: Any) -> Any:
        return self._get_client().chat.completions.create(**kwargs)

    async def _raw_completions_acreate(self, **kwargs: Any) -> Any:
        client = self._get_client()
        if hasattr(client.chat.completions, "acreate"):
            return await client.chat.completions.acreate(**kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: client.chat.completions.create(**kwargs)
        )

    async def _raw_completions_astream(self, **kwargs: Any) -> Any:
        """Return an async-iterable stream from the underlying client."""
        client = self._get_client()
        # openai SDK v1+ — AsyncOpenAI has async_generator via stream context manager
        if hasattr(client.chat.completions, "acreate"):
            return await client.chat.completions.acreate(**kwargs)
        # Sync client fallback: run in executor and wrap in async iterator
        loop = asyncio.get_event_loop()
        sync_stream = await loop.run_in_executor(
            None, lambda: client.chat.completions.create(**kwargs)
        )
        return _SyncToAsyncStream(sync_stream)

    # ── Streaming enforcement ─────────────────────────────────────────────────

    def _enforce_stream(self, raw_stream: Any) -> "_EnforcedStream":
        """Buffer a streaming response, enforce tool calls, return filtered stream.

        Strategy: collect every chunk; when the stream ends, assemble complete
        tool calls from deltas, run the interceptor, then re-yield chunks —
        passing content chunks through and dropping chunks that belong to
        blocked tool call indices.

        This preserves the streaming interface the caller expects while
        guaranteeing enforcement happens on complete, assembled tool call args
        (not on partial delta fragments where enforcement would be unreliable).
        """
        return _EnforcedStream(raw_stream, self)

    # ── Enforcement ───────────────────────────────────────────────────────────

    def _enforce_tool_calls(self, response: Any, request_kwargs: Any) -> Any:
        """Evaluate tool calls in *response* through the interceptor.

        Blocked tool calls are removed from the response's tool_calls list
        and a synthetic ``function`` message is injected explaining the block.
        The response object is modified in-place where possible; for immutable
        Pydantic models (openai SDK v1+) a wrapper is returned.
        """
        if self._interceptor is None:
            return response

        tool_calls = _extract_tool_calls(response)
        if not tool_calls:
            return response

        allowed_calls = []
        blocked_calls = []

        for tc in tool_calls:
            name = _tc_name(tc)
            args = _tc_args(tc)
            call_id = _tc_id(tc) or str(uuid.uuid4())[:8]

            event = ProxyToolCallEvent(
                tool_name=name,
                args=args,
                call_id=call_id,
                agent_id=self._agent_id,
                model=_response_model(response),
            )

            # Run interceptor synchronously (proxy is sync by default)
            decision = _run_interceptor_sync(self._interceptor, event)

            if decision.allowed:
                self._allowed.append(event)
                if decision.modified_args is not None:
                    tc = _replace_tc_args(tc, decision.modified_args)
                allowed_calls.append(tc)
            else:
                self._blocked.append(event)
                blocked_calls.append((tc, decision.block_reason))
                if self._on_block:
                    try:
                        self._on_block(event)
                    except Exception:
                        pass

        if not blocked_calls:
            return response

        return _ProxiedResponse(
            original=response,
            allowed_tool_calls=allowed_calls,
            blocked_tool_calls=blocked_calls,
        )

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        return {
            "allowed_tool_calls": len(self._allowed),
            "blocked_tool_calls": len(self._blocked),
        }

    def blocked_calls(self) -> list[ProxyToolCallEvent]:
        return list(self._blocked)

    # ── Pass-through for other client attributes ──────────────────────────────

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_client(), name)


# ── Response wrapper ──────────────────────────────────────────────────────────

class _EnforcedStream:
    """Iterator wrapper that buffers a streaming response, enforces tool calls,
    then re-yields only allowed chunks.

    Content (text) chunks are passed through immediately.
    Tool call delta chunks are buffered until the stream ends; the assembled
    tool calls are then evaluated by the interceptor and only allowed ones
    are re-yielded.
    """

    def __init__(self, raw_stream: Any, proxy: "MeshFlowProxy") -> None:
        self._raw = raw_stream
        self._proxy = proxy
        self._chunks: list[Any] = []
        self._blocked_indices: set[int] = set()
        self._consumed = False

    def __iter__(self) -> "Any":
        chunks = list(self._raw)
        self._chunks = chunks
        blocked_indices = self._compute_blocked_indices(chunks)
        for chunk in chunks:
            if self._is_tool_call_chunk(chunk):
                tc_index = self._chunk_tc_index(chunk)
                if tc_index in blocked_indices:
                    continue  # drop this chunk
            yield chunk

    def __next__(self) -> Any:
        raise StopIteration

    def _compute_blocked_indices(self, chunks: list[Any]) -> set[int]:
        """Assemble full tool calls from deltas; run interceptor; return blocked indices."""
        if self._proxy._interceptor is None:
            return set()

        assembled = _assemble_tool_calls_from_chunks(chunks)
        if not assembled:
            return set()

        blocked: set[int] = set()
        for idx, tc in assembled.items():
            name = tc.get("name", "")
            args_str = tc.get("arguments", "{}")
            import json as _json
            try:
                args = _json.loads(args_str)
            except Exception:
                args = {"_raw": args_str}

            event = ProxyToolCallEvent(
                tool_name=name,
                args=args,
                call_id=tc.get("id", str(idx)),
                agent_id=self._proxy._agent_id,
            )
            decision = _run_interceptor_sync(self._proxy._interceptor, event)
            if decision.allowed:
                self._proxy._allowed.append(event)
            else:
                self._proxy._blocked.append(event)
                blocked.add(idx)
                if self._proxy._on_block:
                    try:
                        self._proxy._on_block(event)
                    except Exception:
                        pass
        return blocked

    @staticmethod
    def _is_tool_call_chunk(chunk: Any) -> bool:
        try:
            delta = chunk.choices[0].delta
            return bool(getattr(delta, "tool_calls", None))
        except Exception:
            return False

    @staticmethod
    def _chunk_tc_index(chunk: Any) -> int:
        try:
            return chunk.choices[0].delta.tool_calls[0].index
        except Exception:
            return 0


class _SyncToAsyncStream:
    """Wraps a sync iterable so it can be used in ``async for``."""

    def __init__(self, sync_iter: Any) -> None:
        self._iter = iter(sync_iter)

    def __aiter__(self) -> "Any":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncEnforcedStream:
    """Async iterator wrapper — enforces tool calls from an async streaming response.

    Mirrors ``_EnforcedStream`` but is a proper async generator so the
    interceptor is awaited directly rather than run via a thread pool.
    """

    def __init__(self, raw_stream: Any, proxy: "MeshFlowProxy") -> None:
        self._raw = raw_stream
        self._proxy = proxy

    def __aiter__(self) -> "Any":
        return self._aiter_impl()

    async def _aiter_impl(self) -> Any:
        chunks: list[Any] = []
        async for chunk in self._raw:
            chunks.append(chunk)

        blocked_indices = await self._compute_blocked_indices(chunks)

        for chunk in chunks:
            if _EnforcedStream._is_tool_call_chunk(chunk):
                if _EnforcedStream._chunk_tc_index(chunk) in blocked_indices:
                    continue
            yield chunk

    async def _compute_blocked_indices(self, chunks: list[Any]) -> set[int]:
        if self._proxy._interceptor is None:
            return set()

        assembled = _assemble_tool_calls_from_chunks(chunks)
        if not assembled:
            return set()

        blocked: set[int] = set()
        for idx, tc in assembled.items():
            name = tc.get("name", "")
            args_str = tc.get("arguments", "{}")
            import json as _json
            try:
                args = _json.loads(args_str)
            except Exception:
                args = {"_raw": args_str}

            from meshflow.core.tool_intercept import ToolCallEvent
            event = ToolCallEvent(
                tool_name=name,
                args=args,
                agent_id=self._proxy._agent_id,
                call_id=tc.get("id", str(idx)),
                source="proxy_async_stream",
            )
            decision = await self._proxy._interceptor.before_call(event)

            proxy_event = ProxyToolCallEvent(
                tool_name=name,
                args=args,
                call_id=tc.get("id", str(idx)),
                agent_id=self._proxy._agent_id,
            )
            if decision.allowed:
                self._proxy._allowed.append(proxy_event)
            else:
                self._proxy._blocked.append(proxy_event)
                blocked.add(idx)
                if self._proxy._on_block:
                    try:
                        self._proxy._on_block(proxy_event)
                    except Exception:
                        pass

        return blocked


class _ProxiedResponse:
    """Wraps an OpenAI ChatCompletion, exposing only allowed tool calls."""

    def __init__(
        self,
        original: Any,
        allowed_tool_calls: list[Any],
        blocked_tool_calls: list[tuple[Any, str]],
    ) -> None:
        self._original = original
        self._allowed = allowed_tool_calls
        self._blocked = blocked_tool_calls

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    @property
    def choices(self) -> list[Any]:
        orig_choices = self._original.choices
        if not orig_choices:
            return orig_choices
        # Rebuild first choice with only allowed tool calls
        first = orig_choices[0]
        return [_ChoiceWrapper(first, self._allowed, self._blocked)] + list(orig_choices[1:])

    @property
    def blocked_tool_calls(self) -> list[tuple[Any, str]]:
        return self._blocked


class _ChoiceWrapper:
    def __init__(self, original: Any, allowed: list[Any], blocked: list[tuple[Any, str]]) -> None:
        self._original = original
        self._allowed = allowed
        self._blocked = blocked

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    @property
    def message(self) -> "_MessageWrapper":
        return _MessageWrapper(self._original.message, self._allowed, self._blocked)


class _MessageWrapper:
    def __init__(self, original: Any, allowed: list[Any], blocked: list[tuple[Any, str]]) -> None:
        self._original = original
        self._allowed = allowed
        self._blocked = blocked

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    @property
    def tool_calls(self) -> list[Any]:
        return self._allowed or None  # type: ignore[return-value]

    @property
    def blocked_tool_calls(self) -> list[tuple[Any, str]]:
        return self._blocked


# ── Helpers ───────────────────────────────────────────────────────────────────

def _assemble_tool_calls_from_chunks(chunks: list[Any]) -> dict[int, dict[str, Any]]:
    """Assemble complete tool calls from streaming delta chunks.

    Returns a dict keyed by tool call index::

        {0: {"id": "call_abc", "name": "search", "arguments": '{"q":"test"}'}}
    """
    assembled: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        try:
            delta = chunk.choices[0].delta
            tcs = getattr(delta, "tool_calls", None)
            if not tcs:
                continue
            for tc_delta in tcs:
                idx = getattr(tc_delta, "index", 0)
                entry = assembled.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc_delta, "id", None):
                    entry["id"] = tc_delta.id
                fn = getattr(tc_delta, "function", None)
                if fn:
                    if getattr(fn, "name", None):
                        entry["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        entry["arguments"] += fn.arguments
        except Exception:
            continue
    return assembled


def _extract_tool_calls(response: Any) -> list[Any]:
    try:
        choices = response.choices
        if choices:
            msg = choices[0].message
            tcs = getattr(msg, "tool_calls", None)
            if tcs:
                return list(tcs)
    except Exception:
        pass
    return []


def _tc_name(tc: Any) -> str:
    try:
        return tc.function.name
    except Exception:
        return str(getattr(tc, "name", "unknown"))


def _tc_args(tc: Any) -> dict[str, Any]:
    import json
    try:
        raw = tc.function.arguments
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


def _tc_id(tc: Any) -> str:
    return str(getattr(tc, "id", ""))


def _response_model(response: Any) -> str:
    return str(getattr(response, "model", ""))


def _replace_tc_args(tc: Any, new_args: dict[str, Any]) -> Any:
    """Return a copy of tc with modified args (best-effort)."""
    import json
    try:
        import copy
        tc_copy = copy.copy(tc)
        tc_copy.function = copy.copy(tc.function)
        tc_copy.function.arguments = json.dumps(new_args)
        return tc_copy
    except Exception:
        return tc


def _run_interceptor_sync(interceptor: Any, event: ProxyToolCallEvent) -> ProxyDecision:
    """Run async interceptor synchronously; fall back to allow on error."""
    from meshflow.core.tool_intercept import ToolCallEvent, ToolCallDecision
    try:
        mesh_event = ToolCallEvent(
            tool_name=event.tool_name,
            args=event.args,
            agent_id=event.agent_id,
            call_id=event.call_id,
            source="proxy",
            run_id=event.run_id,
        )
        # Run the coroutine synchronously
        coro = interceptor.before_call(mesh_event)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in an async context — schedule via thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(asyncio.run, interceptor.before_call(mesh_event))
                    decision: ToolCallDecision = future.result(timeout=2.0)
            else:
                decision = loop.run_until_complete(coro)
        except RuntimeError:
            decision = asyncio.run(coro)

        return ProxyDecision(
            allowed=decision.allowed,
            block_reason=decision.block_reason,
            modified_args=decision.modified_args,
        )
    except Exception:
        return ProxyDecision(allowed=True)


__all__ = ["MeshFlowProxy", "ProxyToolCallEvent", "ProxyDecision"]
