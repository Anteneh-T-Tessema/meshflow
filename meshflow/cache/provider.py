"""CachedProvider — transparent LLM cache wrapper.

Wraps any LLMProvider.  On each ``complete()`` call:
1. Compute the exact cache key.
2. Exact-key lookup → return hit.
3. Semantic fuzzy lookup → return hit if similarity ≥ threshold.
4. Miss → call the underlying provider, store the result.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from .core import LLMCache, CacheEntry, _make_key, _prompt_text


class CachedProvider:
    """Wraps any LLMProvider with a transparent response cache.

    Parameters
    ----------
    provider:  The underlying LLMProvider to fall through to on cache miss.
    cache:     An :class:`~meshflow.cache.LLMCache` instance.
    """

    def __init__(self, provider: Any, cache: LLMCache) -> None:
        self._provider = provider
        self._cache = cache

    # Proxy all non-complete attributes to the underlying provider
    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> tuple[str, int, float]:
        key = _make_key(model, system, messages)

        # 1. Exact hit
        entry = self._cache.get(key)
        if entry is not None:
            return entry.response, entry.tokens, 0.0  # 0 cost on cache hit

        # 2. Semantic hit
        entry = self._cache.get_semantic(model, system, messages)
        if entry is not None:
            return entry.response, entry.tokens, 0.0

        # 3. Miss → call through
        response, tokens, cost = await self._provider.complete(
            model=model,
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            **kwargs,
        )

        self._cache.put(
            CacheEntry(
                key=key,
                model=model,
                response=response,
                tokens=tokens,
                cost_usd=cost,
                prompt_text=_prompt_text(messages),
            )
        )
        return response, tokens, cost

    async def stream_complete(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        # Streaming bypasses the cache (chunks cannot be reassembled cheaply)
        async for chunk in self._provider.stream_complete(*args, **kwargs):
            yield chunk
