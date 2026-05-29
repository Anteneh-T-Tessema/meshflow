"""EntityMemory — Tier 5 structured fact store (CrewAI parity gap).

Tracks named entities (people, organisations, concepts) alongside
typed fact dictionaries.  Backed by SQLite so facts survive process
restarts.  Integrated into AgentMemory as a new Tier 5.

Usage::

    from meshflow.intelligence.entity_memory import EntityMemory

    em = EntityMemory()
    em.remember("Alice", "role", "CTO")
    em.remember("Alice", "company", "Acme Corp")

    print(em.recall_entity("Alice"))          # {"role": "CTO", "company": "Acme Corp"}
    print(em.search_entities("Acme"))         # ["Alice"]
    em.forget("Alice")                        # removes all Alice facts
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EntityFact:
    entity: str
    fact_key: str
    fact_value: str
    updated_at: float = field(default_factory=time.time)


class EntityMemory:
    """Structured store mapping entity names → typed fact dictionaries.

    Parameters
    ----------
    path:
        Path to the SQLite file.  Use ``":memory:"`` for tests.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── Internal helpers ───────────────────────────────────────────────────────

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_facts (
                entity     TEXT NOT NULL,
                fact_key   TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (entity, fact_key)
            )
            """
        )
        conn.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    def remember(self, entity: str, fact_key: str, fact_value: str) -> None:
        """Store (or update) a typed fact about *entity*."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO entity_facts (entity, fact_key, fact_value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity, fact_key) DO UPDATE
                    SET fact_value = excluded.fact_value,
                        updated_at = excluded.updated_at
                """,
                (entity, fact_key, str(fact_value), time.time()),
            )
            conn.commit()

    def forget(self, entity: str) -> None:
        """Remove all facts for *entity*."""
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM entity_facts WHERE entity = ?", (entity,))
            conn.commit()

    def forget_fact(self, entity: str, fact_key: str) -> None:
        """Remove one specific fact for *entity*."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "DELETE FROM entity_facts WHERE entity = ? AND fact_key = ?",
                (entity, fact_key),
            )
            conn.commit()

    # ── Read ───────────────────────────────────────────────────────────────────

    def recall_entity(self, entity: str) -> dict[str, str]:
        """Return all known facts for *entity* as {fact_key: fact_value}."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT fact_key, fact_value FROM entity_facts WHERE entity = ? ORDER BY updated_at DESC",
                (entity,),
            ).fetchall()
        return {r["fact_key"]: r["fact_value"] for r in rows}

    def search_entities(self, query: str) -> list[str]:
        """Return entity names that contain any token from *query* (fuzzy match)."""
        tokens = re.findall(r"[a-z0-9]+", query.lower())
        if not tokens:
            return self.list_entities()
        with self._lock:
            conn = self._connect()
            all_entities = [r["entity"] for r in conn.execute(
                "SELECT DISTINCT entity FROM entity_facts"
            ).fetchall()]
        return [e for e in all_entities if any(t in e.lower() for t in tokens)]

    def list_entities(self) -> list[str]:
        """Return all tracked entity names, alphabetically."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT DISTINCT entity FROM entity_facts ORDER BY entity"
            ).fetchall()
        return [r["entity"] for r in rows]

    # ── Context helpers ────────────────────────────────────────────────────────

    def entities_in_text(self, text: str) -> list[str]:
        """Return tracked entity names that appear (case-insensitive) in *text*."""
        text_lower = text.lower()
        return [e for e in self.list_entities() if e.lower() in text_lower]

    def to_context_string(self, entities: list[str] | None = None, max_chars: int = 400) -> str:
        """Format facts for *entities* (or all tracked if None) as an LLM context block."""
        targets = entities if entities is not None else self.list_entities()
        parts: list[str] = []
        total = 0
        for entity in targets:
            facts = self.recall_entity(entity)
            if not facts:
                continue
            block = f"[{entity}] " + "; ".join(f"{k}={v}" for k, v in facts.items())
            if total + len(block) > max_chars:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            n_entities = conn.execute(
                "SELECT COUNT(DISTINCT entity) FROM entity_facts"
            ).fetchone()[0]
            n_facts = conn.execute(
                "SELECT COUNT(*) FROM entity_facts"
            ).fetchone()[0]
            n_rels = 0
            try:
                n_rels = conn.execute(
                    "SELECT COUNT(*) FROM entity_relations"
                ).fetchone()[0]
            except Exception:
                pass
        return {"entities": n_entities, "total_facts": n_facts, "total_relations": n_rels}


# ── Knowledge graph — relationship edges ─────────────────────────────────────

class KnowledgeGraph(EntityMemory):
    """Extends EntityMemory with typed relationship edges between entities.

    Stores (subject, predicate, object) triples in a dedicated table, enabling
    semantic graph queries: "who works_at Acme?", "traverse from Alice depth 2".

    Usage::

        kg = KnowledgeGraph()
        kg.remember("Alice", "role", "CTO")
        kg.relate("Alice", "works_at", "Acme Corp")
        kg.relate("Acme Corp", "is_a", "company")
        kg.relate("Alice", "reports_to", "Bob")

        kg.find_related("Alice", "works_at")     # ["Acme Corp"]
        kg.find_incoming("Acme Corp", "works_at")  # ["Alice"]
        kg.traverse("Alice", depth=2)            # all entities 2 hops away
    """

    def __init__(self, path: str = ":memory:") -> None:
        super().__init__(path)
        self._init_graph_db()

    def _init_graph_db(self) -> None:
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_relations (
                subject    TEXT NOT NULL,
                predicate  TEXT NOT NULL,
                object     TEXT NOT NULL,
                weight     REAL NOT NULL DEFAULT 1.0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (subject, predicate, object)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_subj ON entity_relations (subject)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_obj ON entity_relations (object)"
        )
        conn.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    def relate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        weight: float = 1.0,
    ) -> None:
        """Store a (subject, predicate, object) relationship edge."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO entity_relations (subject, predicate, object, weight, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(subject, predicate, object) DO UPDATE
                       SET weight = excluded.weight, updated_at = excluded.updated_at""",
                (subject, predicate, obj, weight, time.time()),
            )
            conn.commit()

    def unrelate(self, subject: str, predicate: str, obj: str) -> None:
        """Remove a specific relationship edge."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "DELETE FROM entity_relations WHERE subject=? AND predicate=? AND object=?",
                (subject, predicate, obj),
            )
            conn.commit()

    # ── Read ───────────────────────────────────────────────────────────────────

    def find_related(self, subject: str, predicate: str | None = None) -> list[str]:
        """Return all objects connected FROM *subject* via *predicate* (or any predicate)."""
        with self._lock:
            conn = self._connect()
            if predicate:
                rows = conn.execute(
                    "SELECT object FROM entity_relations WHERE subject=? AND predicate=? ORDER BY weight DESC",
                    (subject, predicate),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT object FROM entity_relations WHERE subject=? ORDER BY weight DESC",
                    (subject,),
                ).fetchall()
        return [r["object"] for r in rows]

    def find_incoming(self, obj: str, predicate: str | None = None) -> list[str]:
        """Return all subjects that point TO *obj* via *predicate*."""
        with self._lock:
            conn = self._connect()
            if predicate:
                rows = conn.execute(
                    "SELECT subject FROM entity_relations WHERE object=? AND predicate=? ORDER BY weight DESC",
                    (obj, predicate),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT subject FROM entity_relations WHERE object=? ORDER BY weight DESC",
                    (obj,),
                ).fetchall()
        return [r["subject"] for r in rows]

    def relations_of(self, entity: str) -> list[dict[str, Any]]:
        """Return all edges where *entity* is subject OR object."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT subject, predicate, object, weight FROM entity_relations
                   WHERE subject=? OR object=? ORDER BY weight DESC""",
                (entity, entity),
            ).fetchall()
        return [{"subject": r["subject"], "predicate": r["predicate"],
                 "object": r["object"], "weight": r["weight"]} for r in rows]

    def traverse(self, start: str, depth: int = 2, predicate: str | None = None) -> dict[str, int]:
        """BFS traversal from *start*, returning {entity: hop_distance}.

        Parameters
        ----------
        start:     Entity to start from.
        depth:     Maximum number of hops (default 2).
        predicate: If set, only follow edges with this predicate.
        """
        visited: dict[str, int] = {start: 0}
        frontier = [start]
        for hop in range(1, depth + 1):
            next_frontier: list[str] = []
            for entity in frontier:
                related = self.find_related(entity, predicate)
                for neighbour in related:
                    if neighbour not in visited:
                        visited[neighbour] = hop
                        next_frontier.append(neighbour)
            frontier = next_frontier
            if not frontier:
                break
        return visited

    def shortest_path(self, start: str, end: str, max_depth: int = 5) -> list[str] | None:
        """Return the shortest relationship path from *start* to *end*, or None."""
        from collections import deque
        queue: deque[list[str]] = deque([[start]])
        visited: set[str] = {start}
        while queue:
            path = queue.popleft()
            if len(path) > max_depth + 1:
                return None
            current = path[-1]
            for neighbour in self.find_related(current):
                if neighbour == end:
                    return path + [neighbour]
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(path + [neighbour])
        return None

    def subgraph(self, entities: list[str]) -> dict[str, Any]:
        """Return all edges within the specified entity set."""
        entity_set = set(entities)
        edges: list[dict[str, Any]] = []
        with self._lock:
            conn = self._connect()
            for e in entities:
                rows = conn.execute(
                    "SELECT subject, predicate, object, weight FROM entity_relations WHERE subject=?",
                    (e,),
                ).fetchall()
                for r in rows:
                    if r["object"] in entity_set:
                        edges.append({"subject": r["subject"], "predicate": r["predicate"],
                                      "object": r["object"], "weight": r["weight"]})
        return {"nodes": list(entity_set), "edges": edges}


__all__ = ["EntityFact", "EntityMemory", "KnowledgeGraph"]
