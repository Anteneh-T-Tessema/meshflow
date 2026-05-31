"""Sprint 42 — Agent registry: publish, discover, and govern agents across teams.

Every team publishes their agents here.  Any other team can discover them by
name, role, tag, or free-text semantic search.  RBAC controls who can call what.

SQLite-backed (`:memory:` for tests, file path for production).

Usage::

    from meshflow.registry.core import AgentRegistry, AgentManifest

    reg = AgentRegistry()

    # Publish
    manifest = AgentManifest(
        name="billing-agent",
        role="executor",
        description="Handles invoice generation and payment queries.",
        tags=["billing", "finance", "hipaa"],
        capabilities=["generate_invoice", "refund", "track_payment"],
        version="1.2.0",
        url="http://billing-svc:8080",          # A2A endpoint
        owner="billing-team",
    )
    reg.publish(manifest)

    # Discover
    results = reg.search("invoice billing")      # keyword search
    agent   = reg.get("billing-agent")

    # RBAC
    reg.allow("billing-agent", caller="payments-team")
    assert reg.can_call("billing-agent", caller="payments-team")
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any


# ── Manifest ───────────────────────────────────────────────────────────────────

@dataclass
class AgentManifest:
    """Capability description of a registered agent.

    Attributes
    ----------
    name:          Unique agent identifier (slug, e.g. ``billing-agent``).
    role:          Agent role: planner / researcher / executor / …
    description:   Human-readable description used for semantic search.
    tags:          Searchable topic tags (e.g. ``["hipaa", "billing"]``).
    capabilities:  List of things this agent can do.
    version:       SemVer string (``"1.0.0"``).
    url:           A2A endpoint if the agent is deployed as a service.
    owner:         Team or user who published this agent.
    input_schema:  JSON Schema for the task input.
    output_schema: JSON Schema for the result output.
    created_at:    Unix timestamp (auto-set on publish).
    updated_at:    Unix timestamp (auto-set on update).
    """

    name: str
    role: str = "executor"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    url: str = ""
    owner: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":          self.name,
            "role":          self.role,
            "description":   self.description,
            "tags":          self.tags,
            "capabilities":  self.capabilities,
            "version":       self.version,
            "url":           self.url,
            "owner":         self.owner,
            "input_schema":  self.input_schema,
            "output_schema": self.output_schema,
            "created_at":    self.created_at,
            "updated_at":    self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentManifest":
        return cls(
            name=d.get("name", ""),
            role=d.get("role", "executor"),
            description=d.get("description", ""),
            tags=d.get("tags", []),
            capabilities=d.get("capabilities", []),
            version=d.get("version", "1.0.0"),
            url=d.get("url", ""),
            owner=d.get("owner", ""),
            input_schema=d.get("input_schema", {}),
            output_schema=d.get("output_schema", {}),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


# ── Registry ───────────────────────────────────────────────────────────────────

class AgentRegistry:
    """SQLite-backed agent registry with keyword search and RBAC.

    Parameters
    ----------
    path:  SQLite path.  Use ``":memory:"`` for in-process (tests).
           Defaults to ``"meshflow_registry.db"``.
    """

    def __init__(self, path: str = "meshflow_registry.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── DB ────────────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._conn
        return sqlite3.connect(self._path, check_same_thread=False)

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                name        TEXT PRIMARY KEY,
                data        TEXT NOT NULL,
                role        TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                tags        TEXT NOT NULL DEFAULT '',
                owner       TEXT NOT NULL DEFAULT '',
                updated_at  REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS rbac (
                agent_name  TEXT NOT NULL,
                caller      TEXT NOT NULL,
                PRIMARY KEY (agent_name, caller)
            );
        """)
        conn.commit()

    # ── Publish / update ──────────────────────────────────────────────────────

    def publish(self, manifest: AgentManifest) -> None:
        """Publish or update an agent manifest in the registry."""
        manifest.updated_at = time.time()
        if not manifest.created_at:
            manifest.created_at = manifest.updated_at
        conn = self._connect()
        conn.execute(
            """INSERT INTO agents (name, data, role, description, tags, owner, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   data=excluded.data,
                   role=excluded.role,
                   description=excluded.description,
                   tags=excluded.tags,
                   owner=excluded.owner,
                   updated_at=excluded.updated_at""",
            (
                manifest.name,
                json.dumps(manifest.to_dict()),
                manifest.role,
                manifest.description,
                json.dumps(manifest.tags),
                manifest.owner,
                manifest.updated_at,
            ),
        )
        conn.commit()

    def unpublish(self, name: str) -> bool:
        """Remove an agent from the registry.  Returns True if it existed."""
        conn = self._connect()
        cur = conn.execute("DELETE FROM agents WHERE name=?", (name,))
        conn.execute("DELETE FROM rbac WHERE agent_name=?", (name,))
        conn.commit()
        return cur.rowcount > 0

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, name: str) -> AgentManifest | None:
        """Return the manifest for *name*, or ``None`` if not found."""
        conn = self._connect()
        row = conn.execute("SELECT data FROM agents WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        return AgentManifest.from_dict(json.loads(row[0]))

    def list(
        self,
        *,
        role: str = "",
        owner: str = "",
        tag: str = "",
        limit: int = 50,
    ) -> list[AgentManifest]:
        """Return agents, optionally filtered by role, owner, or tag."""
        conn = self._connect()
        sql = "SELECT data FROM agents WHERE 1=1"
        params: list[Any] = []
        if role:
            sql += " AND role=?"
            params.append(role)
        if owner:
            sql += " AND owner=?"
            params.append(owner)
        if tag:
            sql += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [AgentManifest.from_dict(json.loads(r[0])) for r in rows]

    def search(
        self,
        query: str,
        *,
        role: str = "",
        tag: str = "",
        limit: int = 20,
    ) -> list[AgentManifest]:
        """Keyword search across name, description, tags, and capabilities.

        Scores results by number of query-term matches (descending).
        """
        terms = [t.lower() for t in query.split() if t]
        candidates = self.list(role=role, tag=tag, limit=500)
        if not terms:
            return candidates[:limit]

        scored: list[tuple[int, AgentManifest]] = []
        for m in candidates:
            parts = [m.name, m.description, m.role, m.owner] + m.tags + m.capabilities
            tokens = [tok for part in parts for tok in re.split(r'\s+', part.lower()) if tok]
            score = sum(
                sum(1 for tok in tokens if tok == t or tok.startswith(t))
                for t in terms
            )
            if score > 0:
                scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]

    # ── RBAC ──────────────────────────────────────────────────────────────────

    def allow(self, agent_name: str, *, caller: str) -> None:
        """Grant *caller* permission to invoke *agent_name*."""
        conn = self._connect()
        conn.execute(
            "INSERT OR IGNORE INTO rbac (agent_name, caller) VALUES (?, ?)",
            (agent_name, caller),
        )
        conn.commit()

    def revoke(self, agent_name: str, *, caller: str) -> None:
        """Revoke *caller*'s permission to invoke *agent_name*."""
        conn = self._connect()
        conn.execute(
            "DELETE FROM rbac WHERE agent_name=? AND caller=?",
            (agent_name, caller),
        )
        conn.commit()

    def can_call(self, agent_name: str, *, caller: str) -> bool:
        """Return True if *caller* is allowed to invoke *agent_name*.

        If no RBAC rules exist for *agent_name*, access is open to everyone.
        """
        conn = self._connect()
        rules = conn.execute(
            "SELECT 1 FROM rbac WHERE agent_name=?", (agent_name,)
        ).fetchall()
        if not rules:
            return True   # no rules → open access
        row = conn.execute(
            "SELECT 1 FROM rbac WHERE agent_name=? AND caller=?",
            (agent_name, caller),
        ).fetchone()
        return row is not None

    def allowed_callers(self, agent_name: str) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT caller FROM rbac WHERE agent_name=?", (agent_name,)
        ).fetchall()
        return [r[0] for r in rows]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        roles_raw = conn.execute("SELECT role, COUNT(*) FROM agents GROUP BY role").fetchall()
        return {
            "total_agents": total,
            "by_role": {r: c for r, c in roles_raw},
        }

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]


# ── Module-level default registry (lazy, in-memory for tests) ─────────────────

_default_registry: AgentRegistry | None = None


def get_registry(path: str = "") -> AgentRegistry:
    """Return the module-level default registry (lazy-init)."""
    global _default_registry
    if _default_registry is None:
        import os
        p = path or os.getenv("MESHFLOW_REGISTRY_PATH", "meshflow_registry.db")
        _default_registry = AgentRegistry(p)
    return _default_registry


__all__ = ["AgentManifest", "AgentRegistry", "get_registry"]
