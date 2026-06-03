"""Cross-thread shared key-value store — LangGraph BaseStore parity.

The store is a namespaced key-value database that persists across threads
and runs.  Unlike checkpointers (which save per-thread state), the store
is shared across all threads and is ideal for cross-session agent memory,
tool results caching, and user profile storage.

Usage::

    from meshflow.core.store import InMemoryStore, SQLiteStore

    store = InMemoryStore()

    # Put a value
    store.put(("user", "alice"), "profile", {"name": "Alice", "role": "admin"})

    # Get it back
    item = store.get(("user", "alice"), "profile")
    assert item.value == {"name": "Alice", "role": "admin"}

    # Search by namespace prefix
    items = store.search(("user",))          # all users
    items = store.search(("user",), query="alice")  # semantic search if embed_fn set

    # Delete
    store.delete(("user", "alice"), "profile")

Namespaces
----------
A namespace is a tuple of strings, e.g. ``("user", "alice")`` or
``("memory", "session_42", "facts")``.  This mirrors LangGraph's BaseStore
interface exactly so code written for LangGraph works unchanged.

InjectedStore
-------------
Use ``InjectedStore`` (from :mod:`meshflow.core.functional`) to inject the
store automatically into tools::

    from typing import Annotated
    from meshflow.core.functional import InjectedStore

    def save_fact(key: str, value: str,
                  store: Annotated[BaseStore, InjectedStore]) -> str:
        store.put(("facts",), key, {"value": value})
        return "saved"
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ── StoreItem ─────────────────────────────────────────────────────────────────

@dataclass
class StoreItem:
    """A single entry in a :class:`BaseStore`.

    Attributes
    ----------
    namespace:  Tuple of strings identifying the bucket.
    key:        Unique key within the namespace.
    value:      The stored payload (any JSON-serialisable object).
    created_at: Unix timestamp when the item was created.
    updated_at: Unix timestamp when the item was last written.
    score:      Optional relevance score set by :meth:`BaseStore.search`.
    """

    namespace: tuple[str, ...]
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    score: float | None = None

    def __repr__(self) -> str:
        ns = "/".join(self.namespace)
        preview = str(self.value)[:60]
        return f"StoreItem(ns={ns!r}, key={self.key!r}, value={preview!r})"


# ── Abstract base ─────────────────────────────────────────────────────────────

class BaseStore(ABC):
    """Abstract cross-thread key-value store (LangGraph BaseStore parity).

    All implementations must be thread-safe.
    """

    @abstractmethod
    def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: Any,
    ) -> None:
        """Write *value* at (*namespace*, *key*).  Overwrites if already present."""

    @abstractmethod
    def get(
        self,
        namespace: tuple[str, ...],
        key: str,
    ) -> StoreItem | None:
        """Return the :class:`StoreItem` at (*namespace*, *key*), or ``None``."""

    @abstractmethod
    def delete(
        self,
        namespace: tuple[str, ...],
        key: str,
    ) -> None:
        """Remove the entry at (*namespace*, *key*).  No-op if absent."""

    @abstractmethod
    def search(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: str | None = None,
        limit: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[StoreItem]:
        """Return items whose namespace starts with *namespace_prefix*.

        Parameters
        ----------
        namespace_prefix:
            Match any namespace that starts with this tuple.
        query:
            Optional free-text query.  Implementations with an embed_fn
            perform semantic search; others fall back to substring match.
        limit:
            Maximum number of results (default 10).
        filter:
            Optional ``{key: value}`` dict applied as exact-match filter on
            the top-level keys of each item's *value* dict.
        """

    @abstractmethod
    def list_namespaces(
        self,
        prefix: tuple[str, ...] | None = None,
    ) -> list[tuple[str, ...]]:
        """Return all distinct namespaces, optionally filtered by *prefix*."""

    # ── LangGraph-compat async shims ──────────────────────────────────────────

    async def aput(self, namespace: tuple[str, ...], key: str, value: Any) -> None:
        self.put(namespace, key, value)

    async def aget(self, namespace: tuple[str, ...], key: str) -> StoreItem | None:
        return self.get(namespace, key)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.delete(namespace, key)

    async def asearch(
        self,
        namespace_prefix: tuple[str, ...],
        **kwargs: Any,
    ) -> list[StoreItem]:
        return self.search(namespace_prefix, **kwargs)

    async def alist_namespaces(
        self,
        prefix: tuple[str, ...] | None = None,
    ) -> list[tuple[str, ...]]:
        return self.list_namespaces(prefix)


# ── InMemoryStore ─────────────────────────────────────────────────────────────

class InMemoryStore(BaseStore):
    """Thread-safe in-process store.  Data is lost when the process exits.

    Parameters
    ----------
    embed_fn:
        Optional ``Callable[[str], list[float]]`` for semantic search.
        When provided, :meth:`search` with a *query* uses cosine similarity.
    """

    def __init__(self, embed_fn: Any = None) -> None:
        # _data: namespace_key → StoreItem
        self._data: dict[tuple[tuple[str, ...], str], StoreItem] = {}
        self._embed_fn = embed_fn
        self._embeddings: dict[tuple[tuple[str, ...], str], list[float]] = {}
        self._lock = threading.Lock()

    def put(self, namespace: tuple[str, ...], key: str, value: Any) -> None:
        nk = (namespace, key)
        now = time.time()
        with self._lock:
            existing = self._data.get(nk)
            created = existing.created_at if existing else now
            self._data[nk] = StoreItem(
                namespace=namespace,
                key=key,
                value=value,
                created_at=created,
                updated_at=now,
            )
            if self._embed_fn is not None:
                try:
                    text = json.dumps(value) if not isinstance(value, str) else value
                    self._embeddings[nk] = self._embed_fn(text)
                except Exception:
                    pass

    def get(self, namespace: tuple[str, ...], key: str) -> StoreItem | None:
        with self._lock:
            return self._data.get((namespace, key))

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        nk = (namespace, key)
        with self._lock:
            self._data.pop(nk, None)
            self._embeddings.pop(nk, None)

    def search(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: str | None = None,
        limit: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[StoreItem]:
        with self._lock:
            candidates = [
                item for (ns, _), item in self._data.items()
                if ns[:len(namespace_prefix)] == namespace_prefix
            ]

        # Apply value filter
        if filter:
            def _matches(item: StoreItem) -> bool:
                v = item.value
                if not isinstance(v, dict):
                    return False
                return all(v.get(k) == fv for k, fv in filter.items())
            candidates = [c for c in candidates if _matches(c)]

        if not candidates:
            return []

        # Semantic search when embed_fn + query provided
        if query is not None and self._embed_fn is not None:
            try:
                q_emb = self._embed_fn(query)
                scored: list[tuple[float, StoreItem]] = []
                for item in candidates:
                    nk = (item.namespace, item.key)
                    emb = self._embeddings.get(nk)
                    if emb is not None:
                        sim = _cosine_sim(q_emb, emb)
                        scored.append((sim, item))
                    else:
                        scored.append((0.0, item))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [
                    StoreItem(**{**item.__dict__, "score": s})
                    for s, item in scored[:limit]
                ]
            except Exception:
                pass

        # Substring fallback when query but no embed_fn
        if query is not None:
            ql = query.lower()
            candidates = [
                c for c in candidates
                if ql in json.dumps(c.value).lower()
            ]

        return candidates[:limit]

    def list_namespaces(
        self,
        prefix: tuple[str, ...] | None = None,
    ) -> list[tuple[str, ...]]:
        with self._lock:
            namespaces = {ns for (ns, _) in self._data}
        if prefix:
            namespaces = {ns for ns in namespaces if ns[:len(prefix)] == prefix}
        return sorted(namespaces)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# ── SQLiteStore ───────────────────────────────────────────────────────────────

class SQLiteStore(BaseStore):
    """Persistent store backed by a local SQLite database.

    Parameters
    ----------
    path:
        Path to the SQLite file.  Use ``":memory:"`` for in-process tests.
    embed_fn:
        Optional embedding function for semantic search (same as InMemoryStore).
    """

    def __init__(
        self,
        path: str = "meshflow_store.db",
        embed_fn: Any = None,
    ) -> None:
        self.path = path
        self._embed_fn = embed_fn
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init()

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._conn
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS store (
                    namespace TEXT NOT NULL,
                    key       TEXT NOT NULL,
                    value     TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
            """)
            conn.commit()

    @staticmethod
    def _ns_to_str(namespace: tuple[str, ...]) -> str:
        return json.dumps(list(namespace))

    @staticmethod
    def _str_to_ns(s: str) -> tuple[str, ...]:
        return tuple(json.loads(s))

    def put(self, namespace: tuple[str, ...], key: str, value: Any) -> None:
        ns_str = self._ns_to_str(namespace)
        val_str = json.dumps(value)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT INTO store (namespace, key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE
                    SET value = excluded.value,
                        updated_at = excluded.updated_at
            """, (ns_str, key, val_str, now, now))
            conn.commit()

    def get(self, namespace: tuple[str, ...], key: str) -> StoreItem | None:
        ns_str = self._ns_to_str(namespace)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value, created_at, updated_at FROM store WHERE namespace=? AND key=?",
                (ns_str, key),
            ).fetchone()
        if row is None:
            return None
        return StoreItem(
            namespace=namespace,
            key=key,
            value=json.loads(row[0]),
            created_at=row[1],
            updated_at=row[2],
        )

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        ns_str = self._ns_to_str(namespace)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM store WHERE namespace=? AND key=?", (ns_str, key))
            conn.commit()

    def search(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: str | None = None,
        limit: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[StoreItem]:
        # Fetch all items with matching namespace prefix
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT namespace, key, value, created_at, updated_at FROM store"
            ).fetchall()

        items: list[StoreItem] = []
        prefix_list = list(namespace_prefix)
        for row in rows:
            ns = self._str_to_ns(row[0])
            if list(ns[:len(namespace_prefix)]) != prefix_list:
                continue
            value = json.loads(row[2])
            # Apply filter
            if filter and isinstance(value, dict):
                if not all(value.get(k) == v for k, v in filter.items()):
                    continue
            items.append(StoreItem(
                namespace=ns,
                key=row[1],
                value=value,
                created_at=row[3],
                updated_at=row[4],
            ))

        if query is not None:
            ql = query.lower()
            items = [i for i in items if ql in json.dumps(i.value).lower()]

        return items[:limit]

    def list_namespaces(
        self,
        prefix: tuple[str, ...] | None = None,
    ) -> list[tuple[str, ...]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT namespace FROM store").fetchall()
        namespaces = {self._str_to_ns(r[0]) for r in rows}
        if prefix:
            namespaces = {ns for ns in namespaces if ns[:len(prefix)] == prefix}
        return sorted(namespaces)

    def __len__(self) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM store").fetchone()[0]


# ── Cosine similarity helper ──────────────────────────────────────────────────

def _cosine_sim(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)
