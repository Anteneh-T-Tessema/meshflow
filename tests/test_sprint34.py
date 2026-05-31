"""Sprint 34 — LLM response cache: exact + semantic caching."""

from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.cache.core import (
    CacheEntry,
    InMemoryCache,
    SQLiteCache,
    _make_key,
    _prompt_text,
)
from meshflow.cache.provider import CachedProvider


# ── CacheEntry ────────────────────────────────────────────────────────────────

class TestCacheEntry:
    def test_round_trip_dict(self):
        entry = CacheEntry(
            key="abc", model="claude", response="hello", tokens=10,
            cost_usd=0.001, prompt_text="What is 2+2?"
        )
        entry2 = CacheEntry.from_dict(entry.to_dict())
        assert entry2.key == "abc"
        assert entry2.model == "claude"
        assert entry2.tokens == 10
        assert entry2.prompt_text == "What is 2+2?"

    def test_defaults(self):
        entry = CacheEntry(key="k", model="m", response="r", tokens=1, cost_usd=0.0)
        assert entry.hits == 0
        assert entry.prompt_text == ""

    def test_created_at_auto(self):
        before = time.time()
        entry = CacheEntry(key="k", model="m", response="r", tokens=1, cost_usd=0.0)
        after = time.time()
        assert before <= entry.created_at <= after


# ── Key derivation ─────────────────────────────────────────────────────────────

class TestMakeKey:
    def test_deterministic(self):
        k1 = _make_key("gpt-4", "system", [{"role": "user", "content": "hi"}])
        k2 = _make_key("gpt-4", "system", [{"role": "user", "content": "hi"}])
        assert k1 == k2

    def test_different_model_different_key(self):
        k1 = _make_key("gpt-4", "sys", [{"role": "user", "content": "hi"}])
        k2 = _make_key("gpt-3.5", "sys", [{"role": "user", "content": "hi"}])
        assert k1 != k2

    def test_different_content_different_key(self):
        k1 = _make_key("m", "s", [{"role": "user", "content": "hello"}])
        k2 = _make_key("m", "s", [{"role": "user", "content": "goodbye"}])
        assert k1 != k2

    def test_returns_hex_string(self):
        key = _make_key("m", "s", [])
        assert all(c in "0123456789abcdef" for c in key)


class TestPromptText:
    def test_extracts_last_user_message(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "second"},
        ]
        assert _prompt_text(msgs) == "second"

    def test_returns_empty_on_empty(self):
        assert _prompt_text([]) == ""

    def test_multipart_content(self):
        msgs = [{"role": "user", "content": [
            {"type": "image", "source": {}},
            {"type": "text", "text": "describe this"},
        ]}]
        assert _prompt_text(msgs) == "describe this"


# ── InMemoryCache ─────────────────────────────────────────────────────────────

class TestInMemoryCache:
    def _entry(self, key: str = "k", content: str = "result") -> CacheEntry:
        return CacheEntry(key=key, model="test-model", response=content,
                         tokens=5, cost_usd=0.001, prompt_text="What?")

    def test_put_and_get(self):
        cache = InMemoryCache()
        cache.put(self._entry("k1", "answer"))
        entry = cache.get("k1")
        assert entry is not None
        assert entry.response == "answer"

    def test_miss_returns_none(self):
        cache = InMemoryCache()
        assert cache.get("nonexistent") is None

    def test_invalidate(self):
        cache = InMemoryCache()
        cache.put(self._entry("k1"))
        cache.invalidate("k1")
        assert cache.get("k1") is None

    def test_clear(self):
        cache = InMemoryCache()
        cache.put(self._entry("k1"))
        cache.put(self._entry("k2"))
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None

    def test_hit_increments_counter(self):
        cache = InMemoryCache()
        cache.put(self._entry("k1"))
        cache.get("k1")
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 0

    def test_miss_increments_counter(self):
        cache = InMemoryCache()
        cache.get("missing")
        stats = cache.stats()
        assert stats["misses"] == 1

    def test_lru_eviction(self):
        cache = InMemoryCache(max_size=2)
        cache.put(self._entry("k1"))
        cache.put(self._entry("k2"))
        cache.put(self._entry("k3"))  # evicts k1 (LRU)
        assert cache.get("k1") is None
        assert cache.get("k2") is not None
        assert cache.get("k3") is not None

    def test_stats_hit_rate(self):
        cache = InMemoryCache()
        cache.put(self._entry("k"))
        cache.get("k")   # hit
        cache.get("missing")  # miss
        stats = cache.stats()
        assert stats["hit_rate"] == pytest.approx(0.5)

    def test_size_in_stats(self):
        cache = InMemoryCache()
        cache.put(self._entry("k1"))
        cache.put(self._entry("k2"))
        assert cache.stats()["size"] == 2


# ── SQLiteCache ───────────────────────────────────────────────────────────────

class TestSQLiteCache:
    def _cache(self) -> SQLiteCache:
        return SQLiteCache(":memory:", semantic=False)

    def _entry(self, key: str = "k", content: str = "result") -> CacheEntry:
        return CacheEntry(key=key, model="test-model", response=content,
                         tokens=5, cost_usd=0.001, prompt_text="What?")

    def test_put_and_get(self):
        cache = self._cache()
        cache.put(self._entry("k1", "answer"))
        entry = cache.get("k1")
        assert entry is not None
        assert entry.response == "answer"

    def test_miss_returns_none(self):
        assert self._cache().get("no") is None

    def test_invalidate(self):
        cache = self._cache()
        cache.put(self._entry("k1"))
        cache.invalidate("k1")
        assert cache.get("k1") is None

    def test_clear(self):
        cache = self._cache()
        cache.put(self._entry("k1"))
        cache.clear()
        assert cache.get("k1") is None

    def test_stats_backend_label(self):
        cache = self._cache()
        assert cache.stats()["backend"] == "sqlite"

    def test_ttl_expiry(self):
        cache = SQLiteCache(":memory:", ttl_s=0.01, semantic=False)
        cache.put(self._entry("k"))
        time.sleep(0.02)
        assert cache.get("k") is None  # expired

    def test_persists_to_file(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            cache1 = SQLiteCache(path, semantic=False)
            cache1.put(self._entry("k", "persistent"))

            cache2 = SQLiteCache(path, semantic=False)
            entry = cache2.get("k")
            assert entry is not None
            assert entry.response == "persistent"
        finally:
            os.unlink(path)

    def test_max_size_eviction(self):
        cache = SQLiteCache(":memory:", max_size=2, semantic=False)
        cache.put(self._entry("k1"))
        cache.put(self._entry("k2"))
        cache.put(self._entry("k3"))
        stats = cache.stats()
        assert stats["size"] <= 2


# ── CachedProvider ────────────────────────────────────────────────────────────

class TestCachedProvider:
    @pytest.mark.asyncio
    async def test_cache_miss_calls_through(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.base import EchoProvider

        provider = EchoProvider()
        cache = InMemoryCache(semantic=False)
        cached = CachedProvider(provider, cache)
        result, tokens, cost = await cached.complete(
            model="echo", messages=[{"role": "user", "content": "hello"}], system=""
        )
        assert isinstance(result, str)
        assert tokens > 0

    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_response(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.base import EchoProvider

        provider = EchoProvider()
        cache = InMemoryCache(semantic=False)
        cached = CachedProvider(provider, cache)
        messages = [{"role": "user", "content": "deterministic query"}]

        r1, t1, c1 = await cached.complete(model="echo", messages=messages, system="")
        r2, t2, c2 = await cached.complete(model="echo", messages=messages, system="")

        assert r1 == r2
        assert c2 == 0.0  # free on cache hit

    @pytest.mark.asyncio
    async def test_cache_hit_zero_cost(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.base import EchoProvider

        provider = EchoProvider()
        cache = InMemoryCache(semantic=False)
        cached = CachedProvider(provider, cache)
        msgs = [{"role": "user", "content": "test"}]

        await cached.complete(model="echo", messages=msgs, system="")
        _, _, cost = await cached.complete(model="echo", messages=msgs, system="")
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_different_content_different_keys(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.base import EchoProvider

        provider = EchoProvider()
        cache = InMemoryCache(semantic=False)
        cached = CachedProvider(provider, cache)

        await cached.complete(model="echo", messages=[{"role": "user", "content": "A"}], system="")
        await cached.complete(model="echo", messages=[{"role": "user", "content": "B"}], system="")
        assert cache.stats()["size"] == 2

    @pytest.mark.asyncio
    async def test_proxies_other_attributes(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.base import EchoProvider

        provider = EchoProvider()
        cache = InMemoryCache()
        cached = CachedProvider(provider, cache)
        # EchoProvider has stream_complete
        assert hasattr(cached, "stream_complete")


# ── Agent.cache= integration ──────────────────────────────────────────────────

class TestAgentCacheIntegration:
    @pytest.mark.asyncio
    async def test_agent_cache_true_uses_in_memory(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from meshflow.cache.provider import CachedProvider

        agent = Agent(name="cached-agent", role="executor", cache=True)
        built = agent._build()
        assert isinstance(built._provider, CachedProvider)

    @pytest.mark.asyncio
    async def test_agent_cache_instance(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from meshflow.cache.provider import CachedProvider

        cache = SQLiteCache(":memory:", semantic=False)
        agent = Agent(name="cache-inst-agent", role="executor", cache=cache)
        built = agent._build()
        assert isinstance(built._provider, CachedProvider)

    @pytest.mark.asyncio
    async def test_agent_no_cache_no_wrapper(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from meshflow.cache.provider import CachedProvider

        agent = Agent(name="no-cache", role="executor")
        built = agent._build()
        assert not isinstance(built._provider, CachedProvider)

    @pytest.mark.asyncio
    async def test_agent_cache_false_no_wrapper(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent
        from meshflow.cache.provider import CachedProvider

        agent = Agent(name="cache-false", role="executor", cache=False)
        built = agent._build()
        assert not isinstance(built._provider, CachedProvider)

    @pytest.mark.asyncio
    async def test_cached_agent_run_twice_second_is_free(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        cache = InMemoryCache(semantic=False)
        agent = Agent(name="double-run", role="executor", cache=cache)

        r1 = await agent.run("constant task")
        r2 = await agent.run("constant task")

        # Both runs should succeed
        assert r1["result"] == r2["result"]
        # Second run cost should be 0
        assert r2["cost_usd"] == 0.0


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_cache_imports(self):
        from meshflow.cache import (
            CacheEntry, LLMCache, InMemoryCache, SQLiteCache, CachedProvider
        )
        assert all(x is not None for x in [
            CacheEntry, LLMCache, InMemoryCache, SQLiteCache, CachedProvider
        ])

    def test_agent_has_cache_field(self):
        import dataclasses
        from meshflow.agents.builder import Agent
        fields = {f.name for f in dataclasses.fields(Agent)}
        assert "cache" in fields
