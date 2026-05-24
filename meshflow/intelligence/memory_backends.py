"""Persistent memory backends for AgentMemory.

Backends serialize/deserialize the full 4-tier memory state so it
survives process restarts and can be shared across sessions.

Usage::

    from meshflow import Agent
    from meshflow.intelligence.memory_backends import SQLiteMemoryBackend

    # Persist memory to a local SQLite file
    agent = Agent(
        name="analyst",
        role="researcher",
        memory=True,
        memory_backend=SQLiteMemoryBackend("meshflow_memory.db"),
    )
    # Memory is loaded on first use and saved after each step.

    # Or use the string shorthand (Agent._build() resolves it):
    agent = Agent(name="a", memory=True, memory_backend="sqlite://meshflow_memory.db")
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from typing import Any


# ── Serialisable item ─────────────────────────────────────────────────────────

def _item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "content": item.content,
        "tier": item.tier,
        "timestamp": item.timestamp,
        "metadata": item.metadata,
        "access_count": item.access_count,
    }


def _dict_to_item(d: dict[str, Any]) -> Any:
    from meshflow.intelligence.memory import MemoryItem
    item = MemoryItem(
        content=d["content"],
        tier=d.get("tier", "working"),
        timestamp=d.get("timestamp", time.monotonic()),
        metadata=d.get("metadata", {}),
        access_count=d.get("access_count", 0),
    )
    return item


# ── Abstract base ─────────────────────────────────────────────────────────────

class MemoryBackend(ABC):
    """Abstract persistent memory backend.

    Implementors persist and reload a snapshot of all memory tiers.
    """

    @abstractmethod
    def save(self, session_id: str, snapshot: dict[str, Any]) -> None:
        """Persist *snapshot* for *session_id*."""

    @abstractmethod
    def load(self, session_id: str) -> dict[str, Any] | None:
        """Return snapshot for *session_id*, or ``None`` if not found."""

    @abstractmethod
    def delete(self, session_id: str) -> None:
        """Remove all stored data for *session_id*."""

    @abstractmethod
    def list_sessions(self) -> list[str]:
        """Return all stored session IDs."""


# ── In-memory backend (useful for testing) ────────────────────────────────────

class InMemoryBackend(MemoryBackend):
    """Thread-safe in-process backend. Useful for tests and short-lived runs."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def save(self, session_id: str, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._store[session_id] = json.loads(json.dumps(snapshot))

    def load(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._store.get(session_id)

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())


# ── SQLite backend ─────────────────────────────────────────────────────────────

class SQLiteMemoryBackend(MemoryBackend):
    """Persist memory tiers to a local SQLite database.

    Each ``session_id`` corresponds to one row; the snapshot is stored as JSON.
    Thread-safe via a per-instance mutex.

    Parameters
    ----------
    path:  Path to the SQLite file (e.g. ``"meshflow_memory.db"``).
           Use ``":memory:"`` for in-process tests.
    """

    def __init__(self, path: str = "meshflow_memory.db") -> None:
        self.path = path
        self._lock = threading.Lock()
        # For :memory: databases we must reuse the same connection object
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memory (
                    session_id TEXT PRIMARY KEY,
                    snapshot   TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, session_id: str, snapshot: dict[str, Any]) -> None:
        payload = json.dumps(snapshot)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_memory (session_id, snapshot, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE
                    SET snapshot = excluded.snapshot,
                        updated_at = excluded.updated_at
                """,
                (session_id, payload, time.time()),
            )
            conn.commit()

    def load(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot FROM agent_memory WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["snapshot"])

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM agent_memory WHERE session_id = ?", (session_id,)
            )
            conn.commit()

    def list_sessions(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id FROM agent_memory ORDER BY updated_at DESC"
            ).fetchall()
        return [r["session_id"] for r in rows]


# ── PostgreSQL backend ─────────────────────────────────────────────────────────

class PostgresMemoryBackend(MemoryBackend):
    """Persist memory tiers to a PostgreSQL database.

    Requires ``psycopg2`` (or ``psycopg2-binary``) to be installed.

    Parameters
    ----------
    dsn:  Connection string, e.g.
          ``"postgresql://user:pass@localhost:5432/meshflow"``.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> Any:
        try:
            import psycopg2  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "PostgresMemoryBackend requires psycopg2: pip install psycopg2-binary"
            ) from exc
        return psycopg2.connect(self.dsn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_memory (
                        session_id TEXT PRIMARY KEY,
                        snapshot   TEXT NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL
                    )
                    """
                )
            conn.commit()

    def save(self, session_id: str, snapshot: dict[str, Any]) -> None:
        payload = json.dumps(snapshot)
        with self._lock, self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_memory (session_id, snapshot, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE
                        SET snapshot = EXCLUDED.snapshot,
                            updated_at = EXCLUDED.updated_at
                    """,
                    (session_id, payload, time.time()),
                )
            conn.commit()

    def load(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT snapshot FROM agent_memory WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_memory WHERE session_id = %s", (session_id,)
                )
            conn.commit()

    def list_sessions(self) -> list[str]:
        with self._lock, self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_id FROM agent_memory ORDER BY updated_at DESC"
                )
                rows = cur.fetchall()
        return [r[0] for r in rows]


# ── Snapshot helpers ──────────────────────────────────────────────────────────

def snapshot_from_memory(memory: Any) -> dict[str, Any]:
    """Serialize an :class:`~meshflow.intelligence.memory.AgentMemory` to a dict."""
    return {
        "agent_id": memory._agent_id,
        "step_count": memory._step_count,
        "working": [_item_to_dict(i) for i in memory._working],
        "episodic": [_item_to_dict(i) for i in memory._episodic],
        "procedural": [_item_to_dict(i) for i in memory._procedural],
    }


def restore_memory(memory: Any, snapshot: dict[str, Any]) -> None:
    """Restore an :class:`~meshflow.intelligence.memory.AgentMemory` from *snapshot*."""
    from collections import deque

    memory._step_count = snapshot.get("step_count", 0)

    working_items = [_dict_to_item(d) for d in snapshot.get("working", [])]
    memory._working = deque(working_items, maxlen=memory._max_working)

    memory._episodic = [_dict_to_item(d) for d in snapshot.get("episodic", [])]
    memory._procedural = [_dict_to_item(d) for d in snapshot.get("procedural", [])]

    # Rebuild BM25 index from all restored content
    memory._index.__init__()
    for item in list(memory._working) + memory._episodic + memory._procedural:
        memory._index.add(item.content)
