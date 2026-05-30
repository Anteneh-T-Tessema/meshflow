"""TeamWorkspace — shared memory pool for all agents in a Team run.

Provides a context-manager that opens a named workspace in
:class:`~meshflow.intelligence.workspace_memory.WorkspaceMemoryStore`,
makes it available to every agent via a thread-local ref, and snapshots
it on exit so callers can inspect what the team learned.

Usage::

    from meshflow.intelligence.team_workspace import TeamWorkspace

    workspace = TeamWorkspace(workspace_id="research-run-001")

    async with workspace:
        # Inside a Team run, agents write to the shared workspace:
        workspace.write(agent_name="researcher", content="HIPAA requires PHI encryption.")
        workspace.write(agent_name="analyst",    content="SOC 2 Type II covers 6 months.")

        # Any agent can search across the whole workspace:
        hits = workspace.search(agent_name="researcher", query="encryption requirements")

    # After the run, inspect what was learned:
    print(workspace.snapshot())
    print(workspace.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkspaceSummary:
    """High-level summary of what a team workspace collected."""

    workspace_id: str
    agent_names: list[str]
    total_entries: int
    entries_by_agent: dict[str, int]
    top_entries: list[str]
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "agent_names": self.agent_names,
            "total_entries": self.total_entries,
            "entries_by_agent": self.entries_by_agent,
            "top_entries": self.top_entries[:5],
            "duration_s": round(self.duration_s, 3),
        }


class TeamWorkspace:
    """Shared memory pool for a single Team run.

    Parameters
    ----------
    workspace_id:
        Unique name for this workspace (e.g. ``"run-{run_id}"``).
    db_path:
        Path to the SQLite file (defaults to in-memory for tests).
    max_entries_per_agent:
        LRU eviction cap per agent.
    ttl_seconds:
        Default TTL for entries written without an explicit TTL (0 = never).
    """

    def __init__(
        self,
        workspace_id: str = "default",
        db_path: str = ":memory:",
        max_entries_per_agent: int = 500,
        ttl_seconds: float = 0,
    ) -> None:
        self._workspace_id = workspace_id
        self._db_path = db_path
        self._max = max_entries_per_agent
        self._default_ttl = ttl_seconds
        self._store: Any = None
        self._opened_at: float = 0.0

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "TeamWorkspace":
        self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass  # store is persistent; nothing to tear down

    def open(self) -> "TeamWorkspace":
        """Initialise the backing store (called automatically by ``async with``)."""
        from meshflow.intelligence.workspace_memory import WorkspaceMemoryStore
        self._store = WorkspaceMemoryStore(
            path=self._db_path,
            max_entries=self._max,
        )
        self._opened_at = time.monotonic()
        return self

    # ── Write ──────────────────────────────────────────────────────────────────

    def write(
        self,
        agent_name: str,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        *,
        ttl_seconds: float | None = None,
    ) -> Any:
        """Write a memory entry into the shared workspace.

        Parameters
        ----------
        agent_name:   The agent that produced this memory.
        content:      The memory text.
        importance:   0–1 importance score (used in BM25 ranking).
        tags:         Optional keyword tags for filtering.
        ttl_seconds:  Override the workspace-level TTL for this entry.
        """
        self._require_open()
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        return self._store.write(
            workspace_id=self._workspace_id,
            agent_name=agent_name,
            content=content,
            importance=importance,
            tags=tags,
            ttl_seconds=effective_ttl,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def search(
        self,
        agent_name: str,
        query: str,
        top_k: int = 5,
        *,
        cross_agent: bool = True,
    ) -> list[Any]:
        """Search the workspace.

        Parameters
        ----------
        agent_name:  Calling agent's name (used for cross-agent federation).
        query:       BM25 search query.
        top_k:       Maximum results.
        cross_agent: If True (default), search all agents' entries, not just
                     the caller's own.
        """
        self._require_open()
        if cross_agent:
            # List all agents and federation-search across them
            agents = self.agent_names()
            results: list[Any] = []
            seen_ids: set[str] = set()
            for ag in agents:
                for entry in self._store.search(
                    workspace_id=self._workspace_id,
                    agent_name=ag,
                    query=query,
                    top_k=top_k,
                ):
                    eid = getattr(entry, "entry_id", id(entry))
                    if eid not in seen_ids:
                        seen_ids.add(eid)
                        results.append(entry)
            # Sort by relevance (importance as proxy) and cap
            results.sort(key=lambda e: getattr(e, "importance", 0.5), reverse=True)
            return results[:top_k]
        return self._store.search(
            workspace_id=self._workspace_id,
            agent_name=agent_name,
            query=query,
            top_k=top_k,
        )

    def snapshot(self) -> list[dict[str, Any]]:
        """Return all entries in the workspace as a list of dicts."""
        self._require_open()
        return self._store.snapshot(self._workspace_id)

    def agent_names(self) -> list[str]:
        """Return all agent names that have written to this workspace."""
        self._require_open()
        snaps = self._store.snapshot(self._workspace_id)
        return sorted({s.get("agent_name", "") for s in snaps if s.get("agent_name")})

    def count(self, agent_name: str | None = None) -> int:
        """Count entries (optionally filtered by agent)."""
        self._require_open()
        if agent_name:
            snaps = self._store.snapshot(self._workspace_id)
            return sum(1 for s in snaps if s.get("agent_name") == agent_name)
        return self._store.count(self._workspace_id)

    def purge_expired(self) -> int:
        """Delete expired entries. Returns count removed."""
        self._require_open()
        return self._store.purge_expired(self._workspace_id)

    def summary(self) -> WorkspaceSummary:
        """Build a human-readable summary of the workspace contents."""
        self._require_open()
        snaps = self.snapshot()
        by_agent: dict[str, int] = {}
        for s in snaps:
            ag = s.get("agent_name", "unknown")
            by_agent[ag] = by_agent.get(ag, 0) + 1

        top = sorted(snaps, key=lambda s: s.get("importance", 0), reverse=True)
        top_entries = [s.get("content", "")[:120] for s in top[:5]]

        return WorkspaceSummary(
            workspace_id=self._workspace_id,
            agent_names=sorted(by_agent),
            total_entries=len(snaps),
            entries_by_agent=by_agent,
            top_entries=top_entries,
            duration_s=time.monotonic() - self._opened_at,
        )

    def clear(self) -> int:
        """Delete all entries in this workspace. Returns count removed."""
        self._require_open()
        return self._store.delete_workspace(self._workspace_id)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _require_open(self) -> None:
        if self._store is None:
            raise RuntimeError(
                "TeamWorkspace is not open. Call .open() or use it as an async context manager."
            )

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def store(self) -> Any:
        """Direct access to the underlying WorkspaceMemoryStore."""
        self._require_open()
        return self._store
