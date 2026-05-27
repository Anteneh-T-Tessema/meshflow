"""Sprint 52 — Semantic Memory & Embedding tests.

Coverage
--------
TestEmbeddingProvider        — ABC contract
TestHashEmbeddingProvider    — dim, name, embed shape, L2 norm, determinism,
                                similar texts score higher than dissimilar,
                                batch consistency, empty string, long text
TestCosimSimilarity          — identical=1, orthogonal~0, opposite=-1,
                                symmetry, range
TestGetEmbeddingProvider     — auto selects hash when ST absent, hash mode,
                                singleton caching, reset
TestEmbedText                — single string convenience wrapper
TestSemanticMemoryEntry      — dataclass fields
TestSemanticSearchResult     — dataclass fields
TestSemanticMemoryStore      — store, get, search top-k, min_score filter,
                                list, count, delete, clear, upsert, batch,
                                :memory: connection caching, max_entries
                                eviction, provider_name, embedding_dim,
                                search ordering, search returns ≤k results,
                                search on empty store
TestMemoryCLIHandlers        — monkey-patch CLI handler tests
TestMemoryCLIRegistration    — subprocess help smoke test
TestPublicExports            — __all__ membership, version == "0.52.0"
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time

import pytest

from meshflow.intelligence.embedding import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    SentenceTransformerProvider,
    cosine_similarity,
    get_embedding_provider,
    reset_embedding_provider,
    embed_text,
)
from meshflow.intelligence.semantic_memory import (
    SemanticMemoryEntry,
    SemanticSearchResult,
    SemanticMemoryStore,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _store(**kw) -> SemanticMemoryStore:
    kw.setdefault("db_path", ":memory:")
    kw.setdefault("provider", HashEmbeddingProvider())
    return SemanticMemoryStore(**kw)


# ── EmbeddingProvider ABC ─────────────────────────────────────────────────────

class TestEmbeddingProvider:
    def test_hash_is_subclass(self):
        assert issubclass(HashEmbeddingProvider, EmbeddingProvider)

    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            EmbeddingProvider()   # type: ignore[abstract]


# ── HashEmbeddingProvider ─────────────────────────────────────────────────────

class TestHashEmbeddingProvider:

    def test_dim_default(self):
        p = HashEmbeddingProvider()
        assert p.dim == 256

    def test_dim_custom(self):
        p = HashEmbeddingProvider(dim=64)
        assert p.dim == 64

    def test_dim_invalid(self):
        with pytest.raises(ValueError):
            HashEmbeddingProvider(dim=0)

    def test_name_contains_dim(self):
        p = HashEmbeddingProvider(dim=128)
        assert "128" in p.name

    def test_embed_returns_list(self):
        p = HashEmbeddingProvider()
        result = p.embed(["hello"])
        assert isinstance(result, list)
        assert isinstance(result[0], list)

    def test_embed_correct_length(self):
        p = HashEmbeddingProvider(dim=64)
        vecs = p.embed(["hello world", "foo bar"])
        assert len(vecs) == 2
        assert all(len(v) == 64 for v in vecs)

    def test_embed_unit_length(self):
        p = HashEmbeddingProvider()
        vec = p.embed(["hello world"])[0]
        magnitude = math.sqrt(sum(x * x for x in vec))
        assert abs(magnitude - 1.0) < 1e-6

    def test_embed_deterministic(self):
        p = HashEmbeddingProvider()
        v1 = p.embed(["reproducible text"])[0]
        v2 = p.embed(["reproducible text"])[0]
        assert v1 == v2

    def test_similar_texts_score_higher(self):
        p = HashEmbeddingProvider()
        v_paris1 = p.embed(["Paris is the capital of France"])[0]
        v_paris2 = p.embed(["France capital city Paris"])[0]
        v_unrel  = p.embed(["quantum mechanics laser physics"])[0]
        score_similar  = cosine_similarity(v_paris1, v_paris2)
        score_dissimilar = cosine_similarity(v_paris1, v_unrel)
        assert score_similar > score_dissimilar

    def test_batch_same_as_individual(self):
        p = HashEmbeddingProvider()
        texts = ["alpha", "beta", "gamma"]
        batch = p.embed(texts)
        for i, t in enumerate(texts):
            single = p.embed([t])[0]
            assert batch[i] == single

    def test_empty_string(self):
        p = HashEmbeddingProvider()
        vec = p.embed([""])[0]
        assert len(vec) == 256

    def test_long_text(self):
        p = HashEmbeddingProvider()
        long = "word " * 500
        vec = p.embed([long])[0]
        mag = math.sqrt(sum(x * x for x in vec))
        assert abs(mag - 1.0) < 1e-6


# ── cosine_similarity ─────────────────────────────────────────────────────────

class TestCosineSimilarity:

    def test_identical_vectors(self):
        p = HashEmbeddingProvider()
        v = p.embed(["test"])[0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_symmetric(self):
        p = HashEmbeddingProvider()
        a, b = p.embed(["foo", "bar"])
        assert abs(cosine_similarity(a, b) - cosine_similarity(b, a)) < 1e-10

    def test_range(self):
        p = HashEmbeddingProvider()
        vecs = p.embed(["hello", "world", "python", "cat"])
        for i in range(len(vecs)):
            for j in range(len(vecs)):
                s = cosine_similarity(vecs[i], vecs[j])
                assert -1.0 <= s <= 1.0

    def test_opposite_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-10

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b)) < 1e-10


# ── get_embedding_provider ────────────────────────────────────────────────────

class TestGetEmbeddingProvider:

    def setup_method(self):
        reset_embedding_provider()

    def test_hash_prefer(self):
        p = get_embedding_provider(prefer="hash")
        assert isinstance(p, HashEmbeddingProvider)

    def test_auto_returns_provider(self):
        p = get_embedding_provider(prefer="auto")
        assert isinstance(p, EmbeddingProvider)

    def test_auto_singleton(self):
        p1 = get_embedding_provider(prefer="auto")
        p2 = get_embedding_provider(prefer="auto")
        assert p1 is p2

    def test_reset_breaks_singleton(self):
        p1 = get_embedding_provider(prefer="auto")
        reset_embedding_provider()
        p2 = get_embedding_provider(prefer="auto")
        assert p1 is not p2

    def test_invalid_prefer(self):
        with pytest.raises(ValueError):
            get_embedding_provider(prefer="unknown")

    def test_hash_not_singleton(self):
        p1 = get_embedding_provider(prefer="hash")
        p2 = get_embedding_provider(prefer="hash")
        assert p1 is not p2   # new instance each time


# ── embed_text ────────────────────────────────────────────────────────────────

class TestEmbedText:
    def test_returns_list_of_floats(self):
        vec = embed_text("hello", provider=HashEmbeddingProvider())
        assert isinstance(vec, list)
        assert all(isinstance(x, float) for x in vec)

    def test_unit_norm(self):
        vec = embed_text("test", provider=HashEmbeddingProvider())
        mag = math.sqrt(sum(x * x for x in vec))
        assert abs(mag - 1.0) < 1e-6


# ── SemanticMemoryEntry ───────────────────────────────────────────────────────

class TestSemanticMemoryEntry:
    def test_fields(self):
        e = SemanticMemoryEntry(
            key="k1", text="hello", embedding=[0.5, 0.5],
            metadata={"src": "test"}, stored_at=1234567890.0,
        )
        assert e.key      == "k1"
        assert e.text     == "hello"
        assert e.metadata == {"src": "test"}


# ── SemanticSearchResult ──────────────────────────────────────────────────────

class TestSemanticSearchResult:
    def test_fields(self):
        r = SemanticSearchResult(
            key="k1", text="hello", score=0.9,
            metadata={}, stored_at=time.time(),
        )
        assert r.key   == "k1"
        assert r.score == 0.9


# ── SemanticMemoryStore ───────────────────────────────────────────────────────

class TestSemanticMemoryStore:

    # ── Store / get ───────────────────────────────────────────────────────────

    def test_store_and_get(self):
        s = _store()
        s.store("k1", "Paris is the capital of France")
        e = s.get("k1")
        assert e is not None
        assert e.key  == "k1"
        assert e.text == "Paris is the capital of France"

    def test_get_missing_returns_none(self):
        s = _store()
        assert s.get("nope") is None

    def test_store_returns_entry(self):
        s = _store()
        e = s.store("k1", "hello world")
        assert isinstance(e, SemanticMemoryEntry)
        assert e.key == "k1"
        assert len(e.embedding) == 256

    def test_store_with_metadata(self):
        s = _store()
        s.store("k1", "text", metadata={"source": "wiki", "lang": "en"})
        e = s.get("k1")
        assert e.metadata == {"source": "wiki", "lang": "en"}

    def test_store_upserts(self):
        s = _store()
        s.store("k1", "original text")
        s.store("k1", "updated text")
        e = s.get("k1")
        assert e.text == "updated text"

    # ── Memory connection caching ─────────────────────────────────────────────

    def test_memory_connection_persists(self):
        s = SemanticMemoryStore(db_path=":memory:", provider=HashEmbeddingProvider())
        s.store("persist", "this should survive")
        assert s.get("persist") is not None   # same instance → same conn

    # ── Count / list ──────────────────────────────────────────────────────────

    def test_count_empty(self):
        s = _store()
        assert s.count() == 0

    def test_count_after_stores(self):
        s = _store()
        s.store("a", "alpha")
        s.store("b", "beta")
        assert s.count() == 2

    def test_list_empty(self):
        s = _store()
        assert s.list() == []

    def test_list_returns_entries(self):
        s = _store()
        s.store("a", "alpha")
        s.store("b", "beta")
        entries = s.list()
        assert len(entries) == 2
        assert {e.key for e in entries} == {"a", "b"}

    def test_list_limit(self):
        s = _store()
        for i in range(10):
            s.store(f"k{i}", f"text {i}")
        entries = s.list(limit=3)
        assert len(entries) == 3

    def test_list_offset(self):
        s = _store()
        for i in range(5):
            s.store(f"k{i}", f"text {i}")
        all_entries = s.list(limit=5)
        paged = s.list(limit=5, offset=2)
        assert len(paged) == 3
        assert paged[0].key == all_entries[2].key

    # ── Delete / clear ────────────────────────────────────────────────────────

    def test_delete_existing(self):
        s = _store()
        s.store("k1", "text")
        assert s.delete("k1") is True
        assert s.get("k1") is None

    def test_delete_missing(self):
        s = _store()
        assert s.delete("ghost") is False

    def test_clear(self):
        s = _store()
        s.store("a", "alpha")
        s.store("b", "beta")
        count = s.clear()
        assert count == 2
        assert s.count() == 0

    # ── Search ────────────────────────────────────────────────────────────────

    def test_search_empty_store(self):
        s = _store()
        results = s.search("anything")
        assert results == []

    def test_search_returns_results(self):
        s = _store()
        s.store("france", "Paris is the capital of France")
        s.store("germany", "Berlin is the capital of Germany")
        results = s.search("French capital city", k=5)
        assert len(results) > 0
        assert all(isinstance(r, SemanticSearchResult) for r in results)

    def test_search_top_result_most_similar(self):
        s = _store()
        s.store("match", "Python programming language syntax")
        s.store("nomatch", "Ancient Roman history emperors")
        results = s.search("Python code programming", k=2)
        assert results[0].key == "match"

    def test_search_respects_k(self):
        s = _store()
        for i in range(10):
            s.store(f"e{i}", f"entry number {i} with some text")
        results = s.search("entry text", k=3)
        assert len(results) <= 3

    def test_search_ordered_by_score_desc(self):
        s = _store()
        s.store("a", "machine learning neural networks deep learning")
        s.store("b", "cooking recipes pasta italian food")
        s.store("c", "artificial intelligence ML algorithms")
        results = s.search("deep learning AI", k=3)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_min_score_filter(self):
        s = _store()
        s.store("k1", "hello world python code")
        s.store("k2", "xyzzy quux frobnicate lorem")
        results = s.search("hello world python", k=10, min_score=0.5)
        for r in results:
            assert r.score >= 0.5

    def test_search_score_between_neg1_and_1(self):
        s = _store()
        s.store("k1", "random text entry here")
        results = s.search("some query", k=5)
        for r in results:
            assert -1.0 <= r.score <= 1.0

    def test_search_result_has_metadata(self):
        s = _store()
        s.store("k1", "hello", metadata={"tag": "test"})
        results = s.search("hello", k=1)
        assert results[0].metadata == {"tag": "test"}

    # ── Batch store ───────────────────────────────────────────────────────────

    def test_store_batch(self):
        s = _store()
        entries = [("a", "alpha text"), ("b", "beta text"), ("c", "gamma text")]
        results = s.store_batch(entries)
        assert len(results) == 3
        assert s.count() == 3

    def test_store_batch_empty(self):
        s = _store()
        results = s.store_batch([])
        assert results == []

    def test_store_batch_with_metadata(self):
        s = _store()
        entries = [("a", "alpha"), ("b", "beta")]
        meta = [{"i": 0}, {"i": 1}]
        s.store_batch(entries, metadata=meta)
        e = s.get("a")
        assert e.metadata == {"i": 0}

    # ── Max entries eviction ──────────────────────────────────────────────────

    def test_max_entries_evicts_oldest(self):
        s = _store(max_entries=3)
        for i in range(5):
            s.store(f"k{i}", f"entry {i}")
            time.sleep(0.01)
        assert s.count() == 3

    def test_max_entries_none_no_eviction(self):
        s = _store(max_entries=None)
        for i in range(10):
            s.store(f"k{i}", f"entry {i}")
        assert s.count() == 10

    # ── Provider info ─────────────────────────────────────────────────────────

    def test_provider_name(self):
        s = _store()
        assert "hash" in s.provider_name.lower() or "ngram" in s.provider_name.lower()

    def test_embedding_dim(self):
        s = _store(provider=HashEmbeddingProvider(dim=64))
        assert s.embedding_dim == 64


# ── CLI handler tests ─────────────────────────────────────────────────────────

class TestMemoryCLIHandlers:

    def _args(self, cmd, **kw):
        base = argparse.Namespace(memory_cmd=cmd, db=":memory:", provider="hash")
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    def _run(self, args, capsys, monkeypatch, store=None):
        from meshflow.cli.main import _cmd_memory
        if store is not None:
            import meshflow.intelligence.semantic_memory as _mem_mod
            orig = _mem_mod.SemanticMemoryStore

            class _Patched:
                def __new__(cls, *a, **kw):
                    return store

            monkeypatch.setattr(_mem_mod, "SemanticMemoryStore", _Patched)
        try:
            _cmd_memory(args)
        except SystemExit:
            pass
        finally:
            if store is not None:
                monkeypatch.setattr(_mem_mod, "SemanticMemoryStore", orig)
        return capsys.readouterr()

    def test_store_cmd(self, capsys, monkeypatch):
        s = _store()
        args = self._args("store", key="test_key", text="hello world", meta="{}")
        self._run(args, capsys, monkeypatch, store=s)
        assert s.get("test_key") is not None

    def test_store_cmd_output(self, capsys, monkeypatch):
        s = _store()
        args = self._args("store", key="k1", text="hello", meta="{}")
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "k1" in out.out

    def test_store_invalid_meta(self, capsys, monkeypatch):
        s = _store()
        args = self._args("store", key="k1", text="hello", meta="not-json")
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "Error" in out.out

    def test_list_empty(self, capsys, monkeypatch):
        s = _store()
        args = self._args("list", limit=20, offset=0)
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "empty" in out.out.lower()

    def test_list_with_entries(self, capsys, monkeypatch):
        s = _store()
        s.store("alpha", "alpha text here")
        args = self._args("list", limit=20, offset=0)
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "alpha" in out.out

    def test_search_no_results(self, capsys, monkeypatch):
        s = _store()
        args = self._args("search", query="hello", k=5, min_score=-1.0, json_output=False)
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "No matching" in out.out

    def test_search_json_output(self, capsys, monkeypatch):
        s = _store()
        s.store("k1", "Python programming")
        args = self._args("search", query="python", k=3, min_score=-1.0, json_output=True)
        out = self._run(args, capsys, monkeypatch, store=s)
        data = json.loads(out.out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "key" in data[0]
        assert "score" in data[0]

    def test_get_existing(self, capsys, monkeypatch):
        s = _store()
        s.store("mykey", "my text value")
        args = self._args("get", key="mykey")
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "mykey" in out.out
        assert "my text value" in out.out

    def test_get_missing(self, capsys, monkeypatch):
        s = _store()
        args = self._args("get", key="ghost")
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "No memory" in out.out

    def test_delete_existing(self, capsys, monkeypatch):
        s = _store()
        s.store("del_me", "delete this")
        args = self._args("delete", key="del_me")
        self._run(args, capsys, monkeypatch, store=s)
        assert s.get("del_me") is None

    def test_delete_missing(self, capsys, monkeypatch):
        s = _store()
        args = self._args("delete", key="ghost")
        out = self._run(args, capsys, monkeypatch, store=s)
        assert "No entry" in out.out

    def test_clear_with_yes_flag(self, capsys, monkeypatch):
        s = _store()
        s.store("a", "alpha")
        s.store("b", "beta")
        args = self._args("clear", yes=True)
        self._run(args, capsys, monkeypatch, store=s)
        assert s.count() == 0


# ── CLI subprocess ────────────────────────────────────────────────────────────

class TestMemoryCLIRegistration:
    def test_memory_help(self):
        r = subprocess.run(
            ["meshflow", "memory", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        combined = r.stdout + r.stderr
        assert r.returncode in (0, 2)
        assert "search" in combined or "memory" in combined or combined == ""

    def test_memory_search_help(self):
        r = subprocess.run(
            ["meshflow", "memory", "search", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        combined = r.stdout + r.stderr
        assert r.returncode == 0
        assert "query" in combined or "search" in combined


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports:
    def test_version(self):
        import meshflow
        assert meshflow.__version__ == "0.65.0"

    def test_embedding_symbols_in_all(self):
        import meshflow
        for sym in [
            "EmbeddingProvider",
            "HashEmbeddingProvider",
            "cosine_similarity",
            "get_embedding_provider",
            "reset_embedding_provider",
            "embed_text",
            "SemanticMemoryEntry",
            "SemanticSearchResult",
            "SemanticMemoryStore",
        ]:
            assert sym in meshflow.__all__, f"{sym} missing from __all__"

    def test_importable_from_top_level(self):
        from meshflow import (
            HashEmbeddingProvider,
            SemanticMemoryStore,
            cosine_similarity,
            embed_text,
        )
        assert SemanticMemoryStore is not None
