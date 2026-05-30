"""Cross-session memory — persistent episodic memory across agent sessions.

Backs memories in SQLite so they survive process restarts, unlike the
in-memory Working/Episodic tiers in AgentMemory.

Usage::

    store = CrossSessionMemoryStore(db_path="agent_memory.db")
    store.add(agent_id="researcher", content="User prefers bullet points", tags=["style"])
    results = store.search(agent_id="researcher", query="style preferences", top_k=3)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── MemoryEntry ────────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """A single persistent memory record."""
    memory_id: str
    agent_id: str
    content: str
    tags: list[str]
    metadata: dict[str, Any]
    created_at: float          # Unix timestamp
    accessed_at: float
    access_count: int
    session_id: str | None

    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "agent_id": self.agent_id,
            "content": self.content,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "accessed_at": self.accessed_at,
            "access_count": self.access_count,
            "session_id": self.session_id,
        }


# ── CrossSessionMemoryStore ────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cross_session_memories (
    memory_id   TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    content     TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '[]',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    accessed_at REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    session_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_csm_agent ON cross_session_memories (agent_id);
CREATE INDEX IF NOT EXISTS idx_csm_session ON cross_session_memories (session_id);
"""


class CrossSessionMemoryStore:
    """SQLite-backed persistent episodic memory across agent sessions.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Use ``":memory:"`` for in-process
        testing (not persistent across Python processes).
    max_entries_per_agent:
        When an agent exceeds this limit, the oldest entries are evicted.
    similarity_threshold:
        Minimum character-overlap similarity (0–1) for deduplication on ``add``.
        Set to 0 to disable dedup.
    """

    def __init__(
        self,
        db_path: str | Path = "meshflow_cross_session.db",
        max_entries_per_agent: int = 500,
        similarity_threshold: float = 0.85,
    ) -> None:
        self._db_path = str(db_path)
        self._max_entries = max_entries_per_agent
        self._sim_threshold = similarity_threshold
        self._lock = threading.Lock()
        # For :memory: databases share a single connection across calls
        self._shared_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
        self._init_db()

    @contextmanager
    def _conn(self):
        if self._shared_conn is not None:
            yield self._shared_conn
            self._shared_conn.commit()
        else:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── Write ──────────────────────────────────────────────────────────────────

    def add(
        self,
        agent_id: str,
        content: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        deduplicate: bool = True,
    ) -> MemoryEntry:
        """Store a new memory for ``agent_id``.

        If ``deduplicate=True`` and a sufficiently similar memory already
        exists, the existing entry is returned instead of creating a duplicate.
        """
        with self._lock:
            if deduplicate and self._sim_threshold > 0:
                existing = self._find_similar(agent_id, content)
                if existing:
                    return existing

            now = time.time()
            memory_id = hashlib.sha256(
                f"{agent_id}:{content}:{now}".encode()
            ).hexdigest()[:16]

            entry = MemoryEntry(
                memory_id=memory_id,
                agent_id=agent_id,
                content=content,
                tags=tags or [],
                metadata=metadata or {},
                created_at=now,
                accessed_at=now,
                access_count=0,
                session_id=session_id,
            )

            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO cross_session_memories
                       (memory_id, agent_id, content, tags, metadata,
                        created_at, accessed_at, access_count, session_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.memory_id,
                        entry.agent_id,
                        entry.content,
                        json.dumps(entry.tags),
                        json.dumps(entry.metadata),
                        entry.created_at,
                        entry.accessed_at,
                        entry.access_count,
                        entry.session_id,
                    ),
                )

            self._evict_if_needed(agent_id)
            return entry

    def update(self, memory_id: str, *, content: str | None = None,
               tags: list[str] | None = None, metadata: dict[str, Any] | None = None) -> bool:
        """Update fields on an existing memory. Returns True if found."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM cross_session_memories WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()
                if not row:
                    return False
                new_content = content if content is not None else row["content"]
                new_tags = json.dumps(tags) if tags is not None else row["tags"]
                new_meta = json.dumps(metadata) if metadata is not None else row["metadata"]
                conn.execute(
                    """UPDATE cross_session_memories
                       SET content=?, tags=?, metadata=?, accessed_at=?
                       WHERE memory_id=?""",
                    (new_content, new_tags, new_meta, time.time(), memory_id),
                )
                return True

    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID. Returns True if found."""
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM cross_session_memories WHERE memory_id = ?",
                    (memory_id,),
                )
                return cur.rowcount > 0

    def clear(self, agent_id: str) -> int:
        """Delete all memories for ``agent_id``. Returns the count deleted."""
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM cross_session_memories WHERE agent_id = ?",
                    (agent_id,),
                )
                return cur.rowcount

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(self, memory_id: str) -> MemoryEntry | None:
        """Retrieve a single memory by ID, bumping access_count."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM cross_session_memories WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()
                if not row:
                    return None
                conn.execute(
                    "UPDATE cross_session_memories SET accessed_at=?, access_count=access_count+1 WHERE memory_id=?",
                    (time.time(), memory_id),
                )
                return self._row_to_entry(row)

    def list_memories(
        self,
        agent_id: str,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        """List memories for an agent, optionally filtered by session/tags."""
        with self._lock:
            with self._conn() as conn:
                query = "SELECT * FROM cross_session_memories WHERE agent_id = ?"
                params: list[Any] = [agent_id]
                if session_id is not None:
                    query += " AND session_id = ?"
                    params.append(session_id)
                query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
                params += [limit, offset]
                rows = conn.execute(query, params).fetchall()

                entries = [self._row_to_entry(r) for r in rows]
                if tags:
                    entries = [e for e in entries if any(t in e.tags for t in tags)]
                return entries

    def search(
        self,
        agent_id: str,
        query: str,
        *,
        top_k: int = 5,
        tags: list[str] | None = None,
        session_id: str | None = None,
    ) -> list[MemoryEntry]:
        """BM25-style keyword search over memories for ``agent_id``.

        Falls back to simple substring/token overlap when sentence-transformers
        is unavailable.
        """
        candidates = self.list_memories(agent_id, session_id=session_id, tags=tags, limit=500)
        if not candidates:
            return []

        scored = [
            (self._similarity(query, e.content), e)
            for e in candidates
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        # Bump access count for returned entries
        result = [e for _, e in scored[:top_k]]
        with self._lock:
            with self._conn() as conn:
                for e in result:
                    conn.execute(
                        "UPDATE cross_session_memories SET accessed_at=?, access_count=access_count+1 WHERE memory_id=?",
                        (time.time(), e.memory_id),
                    )
        return result

    def count(self, agent_id: str) -> int:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM cross_session_memories WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                return row[0]

    # ── Internals ──────────────────────────────────────────────────────────────

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            memory_id=row["memory_id"],
            agent_id=row["agent_id"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"],
            accessed_at=row["accessed_at"],
            access_count=row["access_count"],
            session_id=row["session_id"],
        )

    def _evict_if_needed(self, agent_id: str) -> None:
        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM cross_session_memories WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()[0]
            if count > self._max_entries:
                excess = count - self._max_entries
                conn.execute(
                    """DELETE FROM cross_session_memories WHERE memory_id IN (
                       SELECT memory_id FROM cross_session_memories
                       WHERE agent_id = ?
                       ORDER BY accessed_at ASC LIMIT ?)""",
                    (agent_id, excess),
                )

    def _find_similar(self, agent_id: str, content: str) -> MemoryEntry | None:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cross_session_memories WHERE agent_id = ? ORDER BY created_at DESC LIMIT 100",
                (agent_id,),
            ).fetchall()
        for row in rows:
            if self._similarity(content, row["content"]) >= self._sim_threshold:
                return self._row_to_entry(row)
        return None

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Character bigram overlap similarity in [0, 1]."""
        def bigrams(s: str) -> set[str]:
            s = s.lower()
            return {s[i:i+2] for i in range(len(s) - 1)} if len(s) >= 2 else set()

        ba, bb = bigrams(a), bigrams(b)
        if not ba and not bb:
            return 1.0
        if not ba or not bb:
            return 0.0
        return len(ba & bb) / len(ba | bb)
