"""Sprint 52 — Semantic Memory Store.

SQLite-backed vector store that embeds every stored entry and retrieves the
top-k most similar entries for a query using cosine similarity.

Replaces the BM25-only long-term memory in ``AgentMemory`` with genuine
semantic search.  Falls back gracefully to ``HashEmbeddingProvider`` when
``sentence-transformers`` is not installed.

Usage
-----
    from meshflow.intelligence.semantic_memory import SemanticMemoryStore

    store = SemanticMemoryStore()                    # uses best available provider
    store.store("fact_1", "Paris is the capital of France", metadata={"source": "wiki"})
    store.store("fact_2", "Berlin is the capital of Germany")
    store.store("fact_3", "The Eiffel Tower is in Paris")

    results = store.search("What city is the Eiffel Tower in?", k=2)
    for r in results:
        print(r.key, r.score, r.text)
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Optional

from .embedding import EmbeddingProvider, cosine_similarity, get_embedding_provider


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SemanticMemoryEntry:
    """A single stored memory."""

    key:        str
    text:       str
    embedding:  list[float]
    metadata:   dict[str, Any]
    stored_at:  float


@dataclass
class SemanticSearchResult:
    """A retrieval result with similarity score."""

    key:        str
    text:       str
    score:      float           # cosine similarity ∈ [−1, 1]
    metadata:   dict[str, Any]
    stored_at:  float


# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS semantic_memory (
    key         TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    embedding   TEXT NOT NULL,    -- JSON array of floats
    metadata    TEXT NOT NULL,    -- JSON object
    stored_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_memory_stored_at
    ON semantic_memory(stored_at);
"""


# ── Store ─────────────────────────────────────────────────────────────────────

class SemanticMemoryStore:
    """Semantic vector store backed by SQLite with cosine-similarity retrieval.

    Parameters
    ----------
    db_path:
        Filesystem path or ``":memory:"`` for an in-process store.
    provider:
        ``EmbeddingProvider`` to use.  Auto-selects best available if omitted.
    max_entries:
        When set, the store evicts the oldest entries by ``stored_at`` to
        stay at or below this limit.  Default ``None`` (no eviction).
    """

    def __init__(
        self,
        db_path: str = "meshflow_memory.db",
        provider: Optional[EmbeddingProvider] = None,
        *,
        max_entries: Optional[int] = None,
    ) -> None:
        self._db_path    = db_path
        self._provider   = provider or get_embedding_provider()
        self._max_entries = max_entries

        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None

        self._ensure_schema()

    # ── Connection ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def store(
        self,
        key: str,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SemanticMemoryEntry:
        """Embed and persist *text* under *key*.

        If *key* already exists, the entry is replaced.
        """
        embedding = self._provider.embed([text])[0]
        entry = SemanticMemoryEntry(
            key=key,
            text=text,
            embedding=embedding,
            metadata=metadata or {},
            stored_at=time.time(),
        )
        con = self._conn()
        con.execute(
            """
            INSERT INTO semantic_memory (key, text, embedding, metadata, stored_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                text      = excluded.text,
                embedding = excluded.embedding,
                metadata  = excluded.metadata,
                stored_at = excluded.stored_at
            """,
            (
                entry.key,
                entry.text,
                json.dumps(entry.embedding),
                json.dumps(entry.metadata),
                entry.stored_at,
            ),
        )
        con.commit()
        self._maybe_evict(con)
        return entry

    def store_batch(
        self,
        entries: list[tuple[str, str]],
        metadata: Optional[list[dict[str, Any]]] = None,
    ) -> list[SemanticMemoryEntry]:
        """Embed and persist multiple (key, text) pairs in one pass."""
        if not entries:
            return []
        texts      = [e[1] for e in entries]
        keys       = [e[0] for e in entries]
        embeddings = self._provider.embed(texts)
        metas      = metadata or [{} for _ in entries]
        now        = time.time()

        rows = [
            (
                keys[i],
                texts[i],
                json.dumps(embeddings[i]),
                json.dumps(metas[i]),
                now,
            )
            for i in range(len(entries))
        ]
        con = self._conn()
        con.executemany(
            """
            INSERT INTO semantic_memory (key, text, embedding, metadata, stored_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                text      = excluded.text,
                embedding = excluded.embedding,
                metadata  = excluded.metadata,
                stored_at = excluded.stored_at
            """,
            rows,
        )
        con.commit()
        self._maybe_evict(con)
        return [
            SemanticMemoryEntry(
                key=keys[i], text=texts[i], embedding=embeddings[i],
                metadata=metas[i], stored_at=now,
            )
            for i in range(len(entries))
        ]

    # ── Read ──────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 5,
        min_score: float = -1.0,
    ) -> list[SemanticSearchResult]:
        """Return the *k* most semantically similar entries for *query*.

        Parameters
        ----------
        query:     Natural-language search string.
        k:         Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (default −1 = no filter).
        """
        q_vec = self._provider.embed([query])[0]
        rows  = self._conn().execute(
            "SELECT key, text, embedding, metadata, stored_at FROM semantic_memory"
        ).fetchall()

        scored: list[SemanticSearchResult] = []
        for row in rows:
            emb   = json.loads(row["embedding"])
            score = cosine_similarity(q_vec, emb)
            if score >= min_score:
                scored.append(SemanticSearchResult(
                    key=row["key"],
                    text=row["text"],
                    score=score,
                    metadata=json.loads(row["metadata"]),
                    stored_at=row["stored_at"],
                ))

        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]

    def get(self, key: str) -> Optional[SemanticMemoryEntry]:
        """Exact key lookup. Returns ``None`` if not found."""
        row = self._conn().execute(
            "SELECT * FROM semantic_memory WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return SemanticMemoryEntry(
            key=row["key"],
            text=row["text"],
            embedding=json.loads(row["embedding"]),
            metadata=json.loads(row["metadata"]),
            stored_at=row["stored_at"],
        )

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SemanticMemoryEntry]:
        """Return stored entries ordered by recency (newest first)."""
        rows = self._conn().execute(
            "SELECT * FROM semantic_memory ORDER BY stored_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            SemanticMemoryEntry(
                key=r["key"],
                text=r["text"],
                embedding=json.loads(r["embedding"]),
                metadata=json.loads(r["metadata"]),
                stored_at=r["stored_at"],
            )
            for r in rows
        ]

    def count(self) -> int:
        """Return total number of stored entries."""
        return self._conn().execute(
            "SELECT COUNT(*) FROM semantic_memory"
        ).fetchone()[0]

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete(self, key: str) -> bool:
        """Delete entry by key. Returns True if it existed."""
        con = self._conn()
        cur = con.execute("DELETE FROM semantic_memory WHERE key = ?", (key,))
        con.commit()
        return cur.rowcount > 0

    def clear(self) -> int:
        """Delete all entries. Returns count deleted."""
        con = self._conn()
        cur = con.execute("DELETE FROM semantic_memory")
        con.commit()
        return cur.rowcount

    # ── Eviction ─────────────────────────────────────────────────────────────

    def _maybe_evict(self, con: sqlite3.Connection) -> None:
        if self._max_entries is None:
            return
        total = con.execute("SELECT COUNT(*) FROM semantic_memory").fetchone()[0]
        if total > self._max_entries:
            excess = total - self._max_entries
            con.execute(
                """
                DELETE FROM semantic_memory WHERE key IN (
                    SELECT key FROM semantic_memory ORDER BY stored_at ASC LIMIT ?
                )
                """,
                (excess,),
            )
            con.commit()

    # ── Provider info ─────────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def embedding_dim(self) -> int:
        return self._provider.dim
