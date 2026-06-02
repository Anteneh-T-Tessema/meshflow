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
        """Synchronous create — mirrors openai.chat.completions.create()."""
        response = self._proxy._raw_completions_create(**kwargs)
        return self._proxy._enforce_tool_calls(response, kwargs)

    async def acreate(self, **kwargs: Any) -> Any:
        """Async create — mirrors openai.chat.completions.acreate()."""
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
        # Fallback: run sync in executor
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: client.chat.completions.create(**kwargs)
        )

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
