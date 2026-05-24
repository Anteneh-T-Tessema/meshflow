"""Prompt management — versioned prompts, variable substitution, A/B testing.

Usage::

    from meshflow.prompts import PromptRegistry, PromptTemplate, PromptABTest

    registry = PromptRegistry("prompts.db")   # SQLite-backed; ":memory:" for tests

    # Create and version prompts
    v1 = registry.create("hipaa-summary", "Summarise the HIPAA clause: {clause}")
    v2 = registry.update("hipaa-summary", "Concisely explain HIPAA §{section}: {clause}")

    # Retrieve by name (latest) or pinned version
    tmpl = registry.get("hipaa-summary")
    tmpl_v1 = registry.get("hipaa-summary", version=v1.version_id)

    # Render with variables
    prompt_text = tmpl.render(section="164.502", clause="minimum-necessary rule")

    # A/B test two variants
    ab = PromptABTest(registry, "hipaa-summary", variant_a=v1.version_id, variant_b=v2.version_id)
    variant, prompt = ab.pick()                      # 50/50 split
    ab.record_outcome(variant, score=0.9)
    stats = ab.stats()                               # {"a": {...}, "b": {...}}

    # Wire into an Agent
    agent = Agent(name="a", role="researcher", prompt="hipaa-summary")
    # Agent._build() calls registry.get("hipaa-summary").render() as system_prompt
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any


# ── PromptVersion ──────────────────────────────────────────────────────────────

@dataclass
class PromptVersion:
    """One versioned snapshot of a named prompt."""

    name: str
    content: str
    version_id: str = ""
    variables: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.version_id:
            self.version_id = self._derive_version_id()
        if not self.variables:
            self.variables = _extract_variables(self.content)

    def _derive_version_id(self) -> str:
        digest = hashlib.md5(
            f"{self.name}:{self.content}:{self.created_at}".encode()
        ).hexdigest()[:12]
        return digest

    def render(self, **kwargs: Any) -> str:
        """Substitute ``{variable}`` placeholders with *kwargs* values."""
        text = self.content
        for key, value in kwargs.items():
            text = text.replace(f"{{{key}}}", str(value))
        missing = [v for v in self.variables if f"{{{v}}}" in text]
        if missing:
            raise ValueError(f"Missing prompt variables: {missing}")
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "content": self.content,
            "version_id": self.version_id,
            "variables": self.variables,
            "tags": self.tags,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PromptVersion":
        return cls(
            name=d["name"],
            content=d["content"],
            version_id=d.get("version_id", ""),
            variables=d.get("variables", []),
            tags=d.get("tags", []),
            created_at=d.get("created_at", time.time()),
            metadata=d.get("metadata", {}),
        )


def _extract_variables(content: str) -> list[str]:
    """Return unique ``{variable}`` names found in *content*."""
    return list(dict.fromkeys(re.findall(r"\{(\w+)\}", content)))


# ── PromptTemplate ─────────────────────────────────────────────────────────────

class PromptTemplate:
    """A named prompt pinned to a specific version.

    Thin wrapper that exposes :meth:`render` and the underlying
    :class:`PromptVersion` for inspection.
    """

    def __init__(self, version: PromptVersion) -> None:
        self._version = version

    @property
    def name(self) -> str:
        return self._version.name

    @property
    def version_id(self) -> str:
        return self._version.version_id

    @property
    def content(self) -> str:
        return self._version.content

    @property
    def variables(self) -> list[str]:
        return self._version.variables

    def render(self, **kwargs: Any) -> str:
        return self._version.render(**kwargs)

    def __str__(self) -> str:
        return self._version.content

    def __repr__(self) -> str:
        return (
            f"PromptTemplate(name={self.name!r}, "
            f"version={self.version_id!r}, "
            f"vars={self.variables})"
        )


# ── PromptRegistry ─────────────────────────────────────────────────────────────

class PromptRegistry:
    """SQLite-backed versioned prompt store.

    Parameters
    ----------
    path:  SQLite file path. Use ``":memory:"`` for tests.
    """

    def __init__(self, path: str = "meshflow_prompts.db") -> None:
        self.path = path
        self._lock = threading.Lock()
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
                CREATE TABLE IF NOT EXISTS prompts (
                    version_id TEXT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    variables  TEXT NOT NULL DEFAULT '[]',
                    tags       TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    metadata   TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON prompts(name)")
            conn.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        content: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PromptVersion:
        """Create the first (or a new) version of a named prompt."""
        version = PromptVersion(
            name=name,
            content=content,
            tags=tags or [],
            metadata=metadata or {},
        )
        self._save(version)
        return version

    def update(
        self,
        name: str,
        content: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PromptVersion:
        """Create a new version of an existing prompt (non-destructive)."""
        return self.create(name, content, tags=tags, metadata=metadata)

    def _save(self, version: PromptVersion) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO prompts
                    (version_id, name, content, variables, tags, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.version_id,
                    version.name,
                    version.content,
                    json.dumps(version.variables),
                    json.dumps(version.tags),
                    version.created_at,
                    json.dumps(version.metadata),
                ),
            )
            conn.commit()

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(
        self,
        name: str,
        *,
        version: str | None = None,
    ) -> PromptTemplate:
        """Return a :class:`PromptTemplate` for *name*.

        If *version* is ``None``, returns the latest (newest ``created_at``).
        Raises :exc:`KeyError` if the prompt or version is not found.
        """
        row = self._fetch(name, version)
        if row is None:
            raise KeyError(f"Prompt {name!r} not found" + (f" (version {version!r})" if version else ""))
        return PromptTemplate(self._row_to_version(row))

    def get_or_none(self, name: str, *, version: str | None = None) -> PromptTemplate | None:
        row = self._fetch(name, version)
        return PromptTemplate(self._row_to_version(row)) if row else None

    def _fetch(self, name: str, version: str | None) -> Any:
        with self._lock, self._connect() as conn:
            if version:
                return conn.execute(
                    "SELECT * FROM prompts WHERE name = ? AND version_id = ?",
                    (name, version),
                ).fetchone()
            return conn.execute(
                "SELECT * FROM prompts WHERE name = ? ORDER BY created_at DESC LIMIT 1",
                (name,),
            ).fetchone()

    def _row_to_version(self, row: Any) -> PromptVersion:
        return PromptVersion(
            name=row["name"],
            content=row["content"],
            version_id=row["version_id"],
            variables=json.loads(row["variables"]),
            tags=json.loads(row["tags"]),
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"]),
        )

    def list_versions(self, name: str) -> list[PromptVersion]:
        """All versions of *name*, newest first."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM prompts WHERE name = ? ORDER BY created_at DESC",
                (name,),
            ).fetchall()
        return [self._row_to_version(r) for r in rows]

    def list_names(self) -> list[str]:
        """All distinct prompt names in the registry."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT name FROM prompts ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    def delete(self, name: str, *, version: str | None = None) -> int:
        """Delete all versions (or a specific version) of *name*. Returns deleted count."""
        with self._lock, self._connect() as conn:
            if version:
                cur = conn.execute(
                    "DELETE FROM prompts WHERE name = ? AND version_id = ?",
                    (name, version),
                )
            else:
                cur = conn.execute("DELETE FROM prompts WHERE name = ?", (name,))
            conn.commit()
        return cur.rowcount


# ── PromptABTest ───────────────────────────────────────────────────────────────

@dataclass
class _VariantStats:
    picks: int = 0
    total_score: float = 0.0
    outcomes: int = 0

    @property
    def avg_score(self) -> float:
        return self.total_score / self.outcomes if self.outcomes > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "picks": self.picks,
            "outcomes": self.outcomes,
            "avg_score": round(self.avg_score, 4),
            "total_score": round(self.total_score, 4),
        }


class PromptABTest:
    """A/B test two prompt versions and track outcome scores.

    Parameters
    ----------
    registry:  The :class:`PromptRegistry` to load versions from.
    name:      Prompt name.
    variant_a: Version ID for variant A.
    variant_b: Version ID for variant B.
    split:     Fraction of picks sent to variant A (default 0.5).
    """

    def __init__(
        self,
        registry: PromptRegistry,
        name: str,
        *,
        variant_a: str | None = None,
        variant_b: str | None = None,
        split: float = 0.5,
    ) -> None:
        self.registry = registry
        self.name = name
        self.split = split

        versions = registry.list_versions(name)
        if len(versions) < 1:
            raise ValueError(f"No versions found for prompt {name!r}")

        self._a_id = variant_a or (versions[1].version_id if len(versions) > 1 else versions[0].version_id)
        self._b_id = variant_b or versions[0].version_id

        self._stats: dict[str, _VariantStats] = {
            "a": _VariantStats(),
            "b": _VariantStats(),
        }
        self._lock = threading.Lock()

    def pick(self) -> tuple[str, PromptTemplate]:
        """Randomly select a variant using the configured *split*.

        Returns ``("a", template)`` or ``("b", template)``.
        """
        variant = "a" if random.random() < self.split else "b"
        version_id = self._a_id if variant == "a" else self._b_id
        tmpl = self.registry.get(self.name, version=version_id)
        with self._lock:
            self._stats[variant].picks += 1
        return variant, tmpl

    def record_outcome(self, variant: str, *, score: float) -> None:
        """Record a quality score (0–1) for a completed *variant* run."""
        if variant not in ("a", "b"):
            raise ValueError(f"variant must be 'a' or 'b', got {variant!r}")
        with self._lock:
            self._stats[variant].total_score += score
            self._stats[variant].outcomes += 1

    def stats(self) -> dict[str, Any]:
        """Return per-variant stats dict."""
        with self._lock:
            return {
                "a": {**self._stats["a"].to_dict(), "version_id": self._a_id},
                "b": {**self._stats["b"].to_dict(), "version_id": self._b_id},
            }

    def winner(self) -> str | None:
        """Return ``'a'`` or ``'b'`` if one variant has meaningfully higher avg_score,
        or ``None`` if there is insufficient data."""
        with self._lock:
            sa = self._stats["a"]
            sb = self._stats["b"]
        if sa.outcomes == 0 or sb.outcomes == 0:
            return None
        diff = abs(sa.avg_score - sb.avg_score)
        if diff < 0.05:  # not meaningful
            return None
        return "a" if sa.avg_score > sb.avg_score else "b"
