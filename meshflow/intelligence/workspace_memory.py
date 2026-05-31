"""WorkspaceMemory — cross-workspace, namespace-isolated persistent memory.

Extends the existing per-agent memory backends with a workspace_id dimension
so that memories can be scoped, shared, or federated across independent
deployment contexts (tenants, environments, projects).

Architecture
------------
- Each (workspace_id, agent_name) pair gets its own isolated memory namespace.
- Memories are stored in a shared SQLite file (or PostgreSQL for multi-node).
- Cross-workspace federation: an agent in workspace-A can query memories
  from workspace-B if given explicit read access.
- All existing AgentMemory / MemoryBackend APIs remain unchanged; this adds
  a new workspace-aware layer on top.

Usage::

    from meshflow.intelligence.workspace_memory import WorkspaceMemoryStore

    store = WorkspaceMemoryStore("meshflow_workspaces.db")

    # Store memory in workspace "prod"
    store.write(workspace_id="prod", agent_name="analyst",
                content="Q3 revenue was $12.4M, up 18% YoY.")

    # Retrieve relevant memories from the same workspace
    hits = store.search(workspace_id="prod", agent_name="analyst",
                        query="Q3 revenue growth")

    # Cross-workspace read (workspace-A reading from workspace-B)
    hits = store.search(workspace_id="staging", agent_name="analyst",
                        query="revenue", allowed_workspaces=["prod"])

    # Snapshot / restore for migration
    snap = store.snapshot(workspace_id="prod")
    store.restore(workspace_id="prod-copy", snapshot=snap)
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any


# ── Memory entry ──────────────────────────────────────────────────────────────

@dataclass
class WorkspaceMemoryEntry:
    workspace_id: str
    agent_name: str
    content: str
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    entry_id: str = ""

    def __post_init__(self) -> None:
        if not self.entry_id:
            self.entry_id = hashlib.md5(
                f"{self.workspace_id}:{self.agent_name}:{self.content}:{self.created_at}".encode()
            ).hexdigest()[:16]


# ── BM25 helpers ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _bm25_score(query_tokens: list[str], doc_tokens: list[str], avg_len: float, n: int, df: dict[str, int]) -> float:
    K1, B = 1.5, 0.75
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    dlen = len(doc_tokens)
    score = 0.0
    for qt in query_tokens:
        f = tf.get(qt, 0)
        if not f:
            continue
        df_qt = df.get(qt, 0)
        if not df_qt:
            continue
        idf = math.log((n - df_qt + 0.5) / (df_qt + 0.5) + 1)
        score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dlen / max(avg_len, 1)))
    return score


# ── WorkspaceMemoryStore ──────────────────────────────────────────────────────

class WorkspaceMemoryStore:
    """Workspace-namespaced persistent memory with BM25 retrieval.

    Parameters
    ----------
    path:           SQLite path.  Use ``":memory:"`` for tests.
    max_entries:    Maximum entries per (workspace_id, agent_name) pair.
                    Oldest/lowest-importance entries are evicted first.
    """

    def __init__(self, path: str = "meshflow_workspaces.db", max_entries: int = 5000) -> None:
        self._path = path
        self._max = max_entries
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_memories (
                entry_id     TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                agent_name   TEXT NOT NULL,
                content      TEXT NOT NULL,
                importance   REAL NOT NULL DEFAULT 0.5,
                tags         TEXT NOT NULL DEFAULT '',
                created_at   REAL NOT NULL,
                expires_at   REAL NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ws_agent ON workspace_memories (workspace_id, agent_name)")
        # Non-destructive migration
        try:
            conn.execute("ALTER TABLE workspace_memories ADD COLUMN expires_at REAL NOT NULL DEFAULT 0")
        except Exception:
            pass
        conn.commit()

    def purge_expired(self, workspace_id: str | None = None) -> int:
        """Delete expired workspace entries. Returns count removed."""
        import time as _time
        now = _time.time()
        with self._lock:
            conn = self._connect()
            if workspace_id:
                cur = conn.execute(
                    "DELETE FROM workspace_memories WHERE workspace_id=? AND expires_at>0 AND expires_at<?",
                    (workspace_id, now),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM workspace_memories WHERE expires_at>0 AND expires_at<?", (now,)
                )
            conn.commit()
        return cur.rowcount

    # ── Write ──────────────────────────────────────────────────────────────────

    def write(
        self,
        workspace_id: str,
        agent_name: str,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        *,
        ttl_seconds: float = 0,
    ) -> WorkspaceMemoryEntry:
        """Store a memory entry in the given workspace namespace.

        Parameters
        ----------
        ttl_seconds:
            Seconds until this entry expires (0 = never).
        """
        entry = WorkspaceMemoryEntry(
            workspace_id=workspace_id,
            agent_name=agent_name,
            content=content[:4000],
            importance=importance,
            tags=tags or [],
        )
        expires_at = (entry.created_at + ttl_seconds) if ttl_seconds > 0 else 0.0
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR REPLACE INTO workspace_memories
                   (entry_id, workspace_id, agent_name, content, importance, tags, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.entry_id,
                    workspace_id,
                    agent_name,
                    entry.content,
                    importance,
                    ",".join(tags or []),
                    entry.created_at,
                    expires_at,
                ),
            )
            conn.commit()
            self._evict_if_needed(conn, workspace_id, agent_name)
        return entry

    def _evict_if_needed(self, conn: sqlite3.Connection, workspace_id: str, agent_name: str) -> None:
        count = conn.execute(
            "SELECT COUNT(*) FROM workspace_memories WHERE workspace_id=? AND agent_name=?",
            (workspace_id, agent_name),
        ).fetchone()[0]
        if count > self._max:
            to_delete = count - self._max
            conn.execute(
                """DELETE FROM workspace_memories WHERE entry_id IN (
                    SELECT entry_id FROM workspace_memories
                    WHERE workspace_id=? AND agent_name=?
                    ORDER BY importance ASC, created_at ASC
                    LIMIT ?
                )""",
                (workspace_id, agent_name, to_delete),
            )
            conn.commit()

    # ── Read ───────────────────────────────────────────────────────────────────

    def search(
        self,
        workspace_id: str,
        agent_name: str,
        query: str,
        top_k: int = 5,
        allowed_workspaces: list[str] | None = None,
    ) -> list[WorkspaceMemoryEntry]:
        """BM25 retrieval over memories from *workspace_id* (+ optional extras).

        Parameters
        ----------
        allowed_workspaces: Additional workspace IDs to include in the search
                            (cross-workspace federation).
        """
        import time as _time
        now = _time.time()
        workspaces = [workspace_id] + (allowed_workspaces or [])
        placeholders = ",".join("?" * len(workspaces))

        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"""SELECT * FROM workspace_memories
                    WHERE workspace_id IN ({placeholders}) AND agent_name=?
                      AND (expires_at = 0 OR expires_at > ?)
                    ORDER BY created_at DESC LIMIT ?""",
                (*workspaces, agent_name, now, 500),
            ).fetchall()

        if not rows:
            return []

        docs = [r["content"] for r in rows]
        q_tokens = _tokenize(query)
        tokenized = [_tokenize(d) for d in docs]
        n = len(tokenized)
        avg_len = sum(len(t) for t in tokenized) / max(n, 1)
        df: dict[str, int] = {}
        for doc in tokenized:
            for tok in set(doc):
                df[tok] = df.get(tok, 0) + 1

        scored = [
            (_bm25_score(q_tokens, tok, avg_len, n, df), i)
            for i, tok in enumerate(tokenized)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, i in scored[:top_k]:
            if score <= 0:
                continue
            r = rows[i]
            results.append(WorkspaceMemoryEntry(
                workspace_id=r["workspace_id"],
                agent_name=r["agent_name"],
                content=r["content"],
                importance=r["importance"],
                tags=[t for t in r["tags"].split(",") if t],
                created_at=r["created_at"],
                entry_id=r["entry_id"],
            ))
        return results

    def list_workspaces(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT DISTINCT workspace_id FROM workspace_memories ORDER BY workspace_id"
            ).fetchall()
        return [r["workspace_id"] for r in rows]

    def count(self, workspace_id: str | None = None) -> int:
        with self._lock:
            conn = self._connect()
            if workspace_id:
                return conn.execute(
                    "SELECT COUNT(*) FROM workspace_memories WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM workspace_memories").fetchone()[0]

    # ── Snapshot / restore ─────────────────────────────────────────────────────

    def snapshot(self, workspace_id: str) -> list[dict[str, Any]]:
        """Export all entries for *workspace_id* as a list of dicts."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM workspace_memories WHERE workspace_id=? ORDER BY created_at",
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def restore(self, workspace_id: str, snapshot: list[dict[str, Any]]) -> int:
        """Import entries into *workspace_id*.  Returns number of entries written."""
        n = 0
        for entry in snapshot:
            self.write(
                workspace_id=workspace_id,
                agent_name=entry.get("agent_name", ""),
                content=entry.get("content", ""),
                importance=float(entry.get("importance", 0.5)),
                tags=[t for t in entry.get("tags", "").split(",") if t],
            )
            n += 1
        return n

    # ── Delete ─────────────────────────────────────────────────────────────────

    def delete_workspace(self, workspace_id: str) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM workspace_memories WHERE workspace_id=?", (workspace_id,)
            )
            conn.commit()
        return cur.rowcount


__all__ = ["WorkspaceMemoryStore", "WorkspaceMemoryEntry"]
