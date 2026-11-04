"""Reserve every supported SDK request, including individual tool-loop rounds.

Reservations use configured pricing and conservative text byte bounds. They are
not a billing guarantee for unknown model prices, media, or external services.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Callable

from meshflow.core.policy import BudgetExceededError, BudgetTracker

active_budget: ContextVar[BudgetTracker | None] = ContextVar("request_budget", default=None)
request_usage: ContextVar[dict[str, Any] | None] = ContextVar("request_usage", default=None)


def _settle(
    budget: BudgetTracker, key: str, usd: float | None = None, tokens: int | None = None
) -> None:
    reserved = budget._reserved[key]
    stats = request_usage.get()
    if stats is not None:
        stats["cost_usd"] += reserved[0] if usd is None else usd
        stats["tokens"] += reserved[1] if tokens is None else tokens
    budget.settle(key, usd, tokens)


def _serialize(value: Any) -> str:
    return json.dumps(
        value, default=lambda obj: obj.model_dump() if hasattr(obj, "model_dump") else str(obj)
    )


async def budgeted_request(
    create: Callable[..., Any], *, rates: tuple[float, float] | None = None, **kwargs: Any
) -> Any:
    """Reserve before SDK dispatch; settle every response or ambiguous failure."""
    budget = active_budget.get()
    if budget is None:
        return await create(**kwargs)
    if kwargs.get("stream") and stream_request_active.get():
        return await create(**kwargs)
    if kwargs.get("stream"):
        raise BudgetExceededError("Use governed completion streaming for budgeted requests")
    model = kwargs.get("model", "")
    if rates is None:
        from meshflow.agents.base import _PRICING

        match = next(
            (
                v
                for k, v in sorted(_PRICING.items(), key=lambda item: -len(item[0]))
                if k in model.lower()
            ),
            None,
        )
        if match is None:
            raise BudgetExceededError(f"No request pricing registered for {model}")
        rates = match
    # A UTF-8 byte bound is deliberately more conservative than chars/4.
    prompt = {
        key: value for key, value in kwargs.items() if key not in {"max_tokens", "max_completion_tokens", "extra_headers"}
    }
    serialized = _serialize(prompt)
    if any(
        marker in serialized
        for marker in ('"type": "image"', '"type": "image_url"', '"type": "document"')
    ):
        raise BudgetExceededError("Media requests require a provider-specific token bound")
    input_bound = len(serialized.encode("utf-8")) + 256
    output_limit = int(kwargs.get("max_tokens", kwargs.get("max_completion_tokens", 0)))
    if output_limit <= 0:
        raise BudgetExceededError("Budgeted requests require a positive max_tokens")
    # Reserve full configured output; never start a request on the last dollars.
    usd_bound = (input_bound * rates[0] * 1.25 + output_limit * rates[1]) / 1000
    key = budget.reserve(usd_bound, input_bound + output_limit)
    try:
        response = await create(**kwargs)
    except BaseException:
        _settle(budget, key)
        raise
    usage = getattr(response, "usage", None)
    if usage is None:
        _settle(budget, key)
        raise BudgetExceededError("Provider response omitted usage; reservation retained")
    in_tokens = int(getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", 0)) or 0)
    out_tokens = int(getattr(usage, "output_tokens", getattr(usage, "completion_tokens", 0)) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cost = (
        (in_tokens + cache_read * 0.1 + cache_write * 1.25) * rates[0] + out_tokens * rates[1]
    ) / 1000
    _settle(budget, key, cost, in_tokens + out_tokens + cache_read + cache_write)
    return response


async def _budgeted_completion_impl(provider: Any, method: str, **kwargs: Any) -> Any:
    """Enforce explicit bounds for providers without SDK request reservations.

    Custom providers advertise ``request_cost_bound(**kwargs) -> (usd, tokens)``.
    The bound must cover the whole operation, including retries and tool rounds.
    """
    budget = active_budget.get()
    call = getattr(provider, method)
    if budget is None or not getattr(budget.policy, "strict_budget", False):
        return await call(**kwargs)
    from meshflow.agents.base import (
        AnthropicProvider,
        OpenAICompatibleProvider,
        EchoProvider,
        SandboxProvider,
    )
    from meshflow.agents.providers import OllamaProvider
    from meshflow.cache.provider import CachedProvider

    if isinstance(provider, CachedProvider):
        return await call(**kwargs)
    if isinstance(provider, (EchoProvider, SandboxProvider)) and budget._shared_store is not None:
        token_bound = len(_serialize(kwargs).encode()) + int(kwargs.get("max_tokens", 0)) + 256
        key = budget.reserve(0.0, token_bound)
        try:
            result = await call(**kwargs)
        except BaseException:
            _settle(budget, key)
            raise
        _settle(budget, key, float(result[2]), int(result[1]))
        return result
    if isinstance(
        provider,
        (
            AnthropicProvider,
            OpenAICompatibleProvider,
            EchoProvider,
            SandboxProvider,
            OllamaProvider,
        ),
    ):
        return await call(**kwargs)
    bound = getattr(provider, "request_cost_bound", None)
    if bound is None:
        raise BudgetExceededError("Provider must declare request_cost_bound for a strict cost cap")
    usd, tokens = bound(**kwargs)
    key = budget.reserve(float(usd), int(tokens))
    try:
        result = await call(**kwargs)
    except BaseException:
        _settle(budget, key)
        raise
    _settle(budget, key, float(result[2]), int(result[1]))
    return result


stream_request_active: ContextVar[bool] = ContextVar("stream_request_active", default=False)
stream_usage: ContextVar[dict[str, Any] | None] = ContextVar("stream_usage", default=None)


def report_stream_usage(usage: Any, rates: tuple[float, float]) -> None:
    """Capture terminal provider usage without treating text chunks as token counts."""
    stats = stream_usage.get()
    if stats is None or usage is None:
        return
    incoming = int(getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", 0)) or 0)
    outgoing = int(getattr(usage, "output_tokens", getattr(usage, "completion_tokens", 0)) or 0)
    read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    stats.update(
        tokens=incoming + outgoing + read + written,
        cost_usd=((incoming + read * 0.1 + written * 1.25) * rates[0] + outgoing * rates[1]) / 1000,
    )


async def budgeted_provider_stream(provider: Any, **kwargs: Any) -> Any:
    """Reserve a streaming request and settle terminal usage or its conservative bound."""
    from meshflow.agents.base import (
        AnthropicProvider,
        OpenAICompatibleProvider,
        EchoProvider,
        SandboxProvider,
        _PRICING,
    )
    from meshflow.agents.providers import OllamaProvider
    from meshflow.cache.provider import CachedProvider
    import contextlib

    underlying = provider
    while isinstance(underlying, CachedProvider):
        underlying = underlying._provider
    if isinstance(underlying, OllamaProvider):
        underlying = underlying._inner
    budget = active_budget.get()
    key = None
    if budget is not None:
        bound = getattr(underlying, "request_cost_bound", None)
        if callable(bound):
            usd, tokens = bound(**kwargs)
            key = budget.reserve(float(usd), int(tokens))
        elif isinstance(
            underlying, (AnthropicProvider, OpenAICompatibleProvider, EchoProvider, SandboxProvider)
        ):
            if isinstance(underlying, OpenAICompatibleProvider):
                rates = (underlying._in_rate, underlying._out_rate)
            elif isinstance(underlying, (EchoProvider, SandboxProvider)):
                rates = (0.0, 0.0)
            else:
                model = kwargs.get("model", "").lower()
                matched_rates = next(
                    (
                        v
                        for k, v in sorted(_PRICING.items(), key=lambda item: -len(item[0]))
                        if k in model
                    ),
                    None,
                )
                if matched_rates is None:
                    raise BudgetExceededError(f"No streaming pricing registered for {model}")
                rates = matched_rates
            serialized = _serialize(
                {
                    k: v
                    for k, v in kwargs.items()
                    if k not in {"max_tokens", "agent_id", "step_id", "run_id"}
                }
            )
            if any(
                marker in serialized
                for marker in ('"type": "image"', '"type": "image_url"', '"type": "document"')
            ):
                raise BudgetExceededError("Media streams require a provider-specific token bound")
            incoming = len(serialized.encode()) + 256
            outgoing = int(kwargs["max_tokens"])
            key = budget.reserve(
                (incoming * rates[0] * 1.25 + outgoing * rates[1]) / 1000, incoming + outgoing
            )
        elif budget.policy.strict_budget:
            raise BudgetExceededError("Streaming provider must declare request_cost_bound")
    stats: dict[str, Any] = {}
    usage_token = stream_usage.set(stats)
    reservation_token = stream_request_active.set(key is not None)
    try:
        iterator = provider.stream_complete(**kwargs)
        async with contextlib.aclosing(iterator):
            async for chunk in iterator:
                yield chunk
    finally:
        stream_request_active.reset(reservation_token)
        stream_usage.reset(usage_token)
        if key is not None and budget is not None:
            if stats:
                _settle(budget, key, stats["cost_usd"], stats["tokens"])
            else:
                _settle(budget, key)


async def budgeted_completion(provider: Any, method: str, **kwargs: Any) -> Any:
    """Account successful custom/cache calls as well as instrumented SDK requests."""
    stats = request_usage.get()
    before = (stats["cost_usd"], stats["tokens"]) if stats is not None else None
    result = await _budgeted_completion_impl(provider, method, **kwargs)
    if stats is not None and (stats["cost_usd"], stats["tokens"]) == before:
        stats["cost_usd"] += float(result[2])
        stats["tokens"] += int(result[1])
    return result
