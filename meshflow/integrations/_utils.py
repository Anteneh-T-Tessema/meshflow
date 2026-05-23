"""Shared helpers for framework integration modules."""
from __future__ import annotations

import asyncio
import inspect
from typing import Any


def run_sync(coro: Any) -> Any:
    """Run an async coroutine from a synchronous call-site.

    Safe in any context:
    - No running loop  → asyncio.run()
    - Running loop     → dispatch to a new thread so we never call
                          run_until_complete() on an active loop (which
                          raises RuntimeError on Python 3.10+).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def extract_tokens(result: Any) -> tuple[int, float]:
    """Best-effort token/cost extraction from LLM framework result objects.

    Returns (tokens_used, cost_usd). Falls back to (0, 0.0) when the
    framework doesn't expose usage metadata.
    """
    if isinstance(result, dict):
        usage = result.get("usage") or result.get("token_usage") or {}
        if isinstance(usage, dict):
            total = usage.get("total_tokens") or (
                (usage.get("prompt_tokens") or usage.get("input_tokens", 0))
                + (usage.get("completion_tokens") or usage.get("output_tokens", 0))
            )
            cost = usage.get("total_cost", 0.0)
            return int(total), float(cost)
    # CrewOutput (crewai ≥ 0.70)
    usage = getattr(result, "token_usage", None)
    if usage is not None:
        if isinstance(usage, dict):
            total = usage.get("total_tokens", 0)
        else:
            total = getattr(usage, "total_tokens", 0)
        return int(total), 0.0
    # AutoGen v0.4 TaskResult
    messages = getattr(result, "messages", None)
    if messages:
        total = 0
        for m in messages:
            meta = getattr(m, "models_usage", None) or {}
            if hasattr(meta, "prompt_tokens"):
                total += getattr(meta, "prompt_tokens", 0) + getattr(meta, "completion_tokens", 0)
            elif isinstance(meta, dict):
                total += meta.get("total_tokens", 0)
        return total, 0.0
    return 0, 0.0


def first_nonempty(*values: Any) -> str:
    """Return the first value that is a non-empty string."""
    for v in values:
        if v and isinstance(v, str):
            return v
        if v is not None and not isinstance(v, str):
            s = str(v)
            if s:
                return s
    return ""
