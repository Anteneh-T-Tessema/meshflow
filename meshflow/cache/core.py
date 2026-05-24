"""LLM response cache — exact-match and semantic (near-duplicate) caching.

Wraps any LLMProvider so the agent layer sees a transparent cache.
Cache key = (model, system_prompt, task_text_hash).  Semantic fuzzy
matching uses the same embedding chain as VectorStore (no extra deps).

Usage::

    from meshflow import Agent
    from meshflow.cache import SQLiteCache

    agent = Agent(
        name="analyst",
        role="researcher",
        cache=SQLiteCache("meshflow_cache.db"),   # persist across restarts
    )

    # Or in-memory (per-process):
    agent = Agent(name="a", cache=True)           # uses InMemoryCache

    # Or with a similarity threshold (0.0–1.0; default 0.95):
    agent = Agent(name="a", cache=SQLiteCache("c.db", similarity_threshold=0.90))
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


# ── Cache entry ───────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    """A single cached LLM response."""

    key: str
    model: str
    response: str
    tokens: int
    cost_usd: float
    created_at: float = field(default_factory=time.time)
    hits: int = 0
    prompt_text: str = ""  # stored for semantic lookup

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "model": self.model,
            "response": self.response,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "created_at": self.created_at,
            "hits": self.hits,
            "prompt_text": self.prompt_text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CacheEntry":
        return cls(
            key=d["key"],
            model=d["model"],
            response=d["response"],
            tokens=d["tokens"],
            cost_usd=d["cost_usd"],
            created_at=d.get("created_at", 0.0),
            hits=d.get("hits", 0),
            prompt_text=d.get("prompt_text", ""),
        )


# ── Key derivation ─────────────────────────────────────────────────────────────

def _make_key(model: str, system: str, messages: list[dict[str, Any]]) -> str:
    """Deterministic hash of (model, system, messages)."""
    payload = json.dumps(
        {"model": model, "system": system, "messages": messages},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _prompt_text(messages: list[dict[str, Any]]) -> str:
    """Extract the last user message text for semantic matching."""
    for msg in reversed(messages):
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
    return ""


# ── Embedding helper (reuses VectorStore chain) ───────────────────────────────

def _embed(text: str) -> Any:
    """Embed *text* using the best available method (same chain as VectorStore)."""
    try:
        from meshflow.intelligence.knowledge import _embed_texts_default
        vecs = _embed_texts_default([text])
        return vecs[0]
    except Exception:
        return None


def _cosine(a: Any, b: Any) -> float:
    if a is None or b is None:
        return 0.0
    try:
        import numpy as np
        na = np.linalg.norm(a)
        nb = np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))
    except ImportError:
        # pure-python fallback
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x * x for x in a) ** 0.5
        mag_b = sum(x * x for x in b) ** 0.5
        return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


# ── Abstract base ─────────────────────────────────────────────────────────────

class LLMCache(ABC):
    """Abstract LLM response cache."""

    similarity_threshold: float = 0.95

    @abstractmethod
    def get(self, key: str) -> CacheEntry | None:
        """Exact-key lookup. Returns ``None`` on miss."""

    @abstractmethod
    def put(self, entry: CacheEntry) -> None:
        """Store an entry."""

    @abstractmethod
    def invalidate(self, key: str) -> None:
        """Remove one entry."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all entries."""

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        """Return hit/miss statistics."""

    def get_semantic(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
    ) -> CacheEntry | None:
        """Fuzzy lookup using embedding similarity (optional; subclass may override)."""
        return None


# ── In-memory LRU cache ───────────────────────────────────────────────────────

class InMemoryCache(LLMCache):
    """Thread-safe in-process LRU cache.

    Parameters
    ----------
    max_size:             Maximum number of entries (LRU eviction).
    similarity_threshold: Cosine threshold for semantic fuzzy matching (0–1).
    """

    def __init__(
        self,
        max_size: int = 1000,
        similarity_threshold: float = 0.95,
        semantic: bool = True,
    ) -> None:
        self.max_size = max_size
        self.similarity_threshold = similarity_threshold
        self._semantic = semantic
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._store.move_to_end(key)
            entry.hits += 1
            self._hits += 1
            return entry

    def put(self, entry: CacheEntry) -> None:
        with self._lock:
            self._store[entry.key] = entry
            self._store.move_to_end(entry.key)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0

    def get_semantic(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
    ) -> CacheEntry | None:
        if not self._semantic:
            return None
        query_text = _prompt_text(messages)
        if not query_text:
            return None
        query_vec = _embed(query_text)
        if query_vec is None:
            return None
        best_score = 0.0
        best_entry: CacheEntry | None = None
        with self._lock:
            for entry in self._store.values():
                if entry.model != model:
                    continue
                if not entry.prompt_text:
                    continue
                score = _cosine(query_vec, _embed(entry.prompt_text))
                if score > best_score:
                    best_score = score
                    best_entry = entry
        if best_score >= self.similarity_threshold and best_entry is not None:
            with self._lock:
                best_entry.hits += 1
                self._hits += 1
            return best_entry
        with self._lock:
            self._misses += 1
        return None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "in_memory",
                "size": len(self._store),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / max(1, self._hits + self._misses),
            }


# ── SQLite cache ──────────────────────────────────────────────────────────────

class SQLiteCache(LLMCache):
    """Persistent LLM cache backed by SQLite.

    Parameters
    ----------
    path:                 SQLite file path (``":memory:"`` for in-process tests).
    max_size:             Maximum rows kept (oldest evicted when exceeded).
    similarity_threshold: Cosine threshold for semantic fuzzy matching (0–1).
    ttl_s:                Optional time-to-live in seconds (``None`` = no expiry).
    semantic:             Enable semantic fuzzy matching (needs embedding support).
    """

    def __init__(
        self,
        path: str = "meshflow_cache.db",
        max_size: int = 10_000,
        similarity_threshold: float = 0.95,
        ttl_s: float | None = None,
        semantic: bool = True,
    ) -> None:
        self.path = path
        self.max_size = max_size
        self.similarity_threshold = similarity_threshold
        self.ttl_s = ttl_s
        self._semantic = semantic
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        # For :memory: databases we must reuse the same connection object
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_cache (
                    key         TEXT PRIMARY KEY,
                    model       TEXT NOT NULL,
                    response    TEXT NOT NULL,
                    tokens      INTEGER NOT NULL,
                    cost_usd    REAL NOT NULL,
                    created_at  REAL NOT NULL,
                    hits        INTEGER NOT NULL DEFAULT 0,
                    prompt_text TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model ON llm_cache(model)")
            conn.commit()

    def _is_expired(self, created_at: float) -> bool:
        return self.ttl_s is not None and (time.time() - created_at) > self.ttl_s

    def get(self, key: str) -> CacheEntry | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM llm_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            self._misses += 1
            return None
        entry = CacheEntry.from_dict(dict(row))
        if self._is_expired(entry.created_at):
            self.invalidate(key)
            self._misses += 1
            return None
        entry.hits += 1
        self._hits += 1
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE llm_cache SET hits = ? WHERE key = ?", (entry.hits, key))
            conn.commit()
        return entry

    def put(self, entry: CacheEntry) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_cache
                    (key, model, response, tokens, cost_usd, created_at, hits, prompt_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    response = excluded.response,
                    tokens = excluded.tokens,
                    cost_usd = excluded.cost_usd,
                    created_at = excluded.created_at,
                    prompt_text = excluded.prompt_text
                """,
                (
                    entry.key,
                    entry.model,
                    entry.response,
                    entry.tokens,
                    entry.cost_usd,
                    entry.created_at,
                    entry.hits,
                    entry.prompt_text,
                ),
            )
            # Evict oldest rows beyond max_size
            conn.execute(
                """
                DELETE FROM llm_cache WHERE key IN (
                    SELECT key FROM llm_cache
                    ORDER BY created_at ASC
                    LIMIT MAX(0, (SELECT COUNT(*) FROM llm_cache) - ?)
                )
                """,
                (self.max_size,),
            )
            conn.commit()

    def invalidate(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM llm_cache WHERE key = ?", (key,))
            conn.commit()

    def clear(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM llm_cache")
            conn.commit()
        self._hits = 0
        self._misses = 0

    def get_semantic(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
    ) -> CacheEntry | None:
        if not self._semantic:
            return None
        query_text = _prompt_text(messages)
        if not query_text:
            return None
        query_vec = _embed(query_text)
        if query_vec is None:
            return None

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_cache WHERE model = ? AND prompt_text != ''",
                (model,),
            ).fetchall()

        best_score = 0.0
        best_entry: CacheEntry | None = None
        for row in rows:
            entry = CacheEntry.from_dict(dict(row))
            if self._is_expired(entry.created_at):
                continue
            score = _cosine(query_vec, _embed(entry.prompt_text))
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_score >= self.similarity_threshold and best_entry is not None:
            best_entry.hits += 1
            self._hits += 1
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE llm_cache SET hits = ? WHERE key = ?",
                    (best_entry.hits, best_entry.key),
                )
                conn.commit()
            return best_entry

        self._misses += 1
        return None

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
            total_hits = conn.execute("SELECT SUM(hits) FROM llm_cache").fetchone()[0] or 0
        return {
            "backend": "sqlite",
            "path": self.path,
            "size": count,
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "total_entry_hits": total_hits,
            "hit_rate": self._hits / max(1, self._hits + self._misses),
        }
