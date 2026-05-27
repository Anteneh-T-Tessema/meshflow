"""Sprint 56 — Data Lineage Graph.

Tracks the provenance of data items as they flow through agents and tools.
Stores a directed acyclic graph (DAG) in SQLite — nodes are data artefacts
(sources, transformations, sinks) and edges are causal relationships between them.

GDPR Article 30 — records of processing activities — maps directly onto this graph:
  * trace_upstream(node_id) → where did this data come from?
  * impact_analysis(node_id) → who was affected by this data?
  * delete_subject(name) → right-to-erasure: purge all nodes matching an identity

Usage
-----
    from meshflow.lineage.graph import LineageGraph

    g = LineageGraph(":memory:")

    src  = g.add_node("source",    "user-upload",     run_id="r1", agent_name="ingest")
    xfm  = g.add_node("transform", "pii-redactor",    run_id="r1", agent_name="redactor")
    sink = g.add_node("sink",      "reports-db",      run_id="r1", agent_name="writer")

    g.add_edge(src.node_id,  xfm.node_id,  "transformed_by")
    g.add_edge(xfm.node_id,  sink.node_id, "written_to")

    upstream   = g.trace_upstream(sink.node_id)   # [xfm, src]
    downstream = g.impact_analysis(src.node_id)   # [xfm, sink]
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


_DDL = """
CREATE TABLE IF NOT EXISTS lineage_nodes (
    node_id     TEXT    PRIMARY KEY,
    kind        TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    run_id      TEXT    NOT NULL DEFAULT '',
    agent_name  TEXT    NOT NULL DEFAULT '',
    ts          REAL    NOT NULL,
    metadata_json TEXT  NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ln_run    ON lineage_nodes(run_id);
CREATE INDEX IF NOT EXISTS idx_ln_agent  ON lineage_nodes(agent_name);
CREATE INDEX IF NOT EXISTS idx_ln_name   ON lineage_nodes(name);

CREATE TABLE IF NOT EXISTS lineage_edges (
    edge_id     TEXT    PRIMARY KEY,
    source_id   TEXT    NOT NULL REFERENCES lineage_nodes(node_id) ON DELETE CASCADE,
    target_id   TEXT    NOT NULL REFERENCES lineage_nodes(node_id) ON DELETE CASCADE,
    relation    TEXT    NOT NULL,
    ts          REAL    NOT NULL,
    metadata_json TEXT  NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_le_source ON lineage_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_le_target ON lineage_edges(target_id);
"""

# Valid node kinds
NODE_KINDS = frozenset({"source", "transform", "sink", "agent", "tool", "dataset", "artifact"})
# Valid edge relations
RELATIONS = frozenset({
    "produced", "consumed", "transformed_by", "derived_from",
    "written_to", "read_from", "triggered_by", "sent_to",
})


@dataclass
class LineageNode:
    node_id:    str
    kind:       str
    name:       str
    run_id:     str
    agent_name: str
    ts:         float
    metadata:   dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id":    self.node_id,
            "kind":       self.kind,
            "name":       self.name,
            "run_id":     self.run_id,
            "agent_name": self.agent_name,
            "ts":         self.ts,
            "metadata":   self.metadata,
        }


@dataclass
class LineageEdge:
    edge_id:   str
    source_id: str
    target_id: str
    relation:  str
    ts:        float
    metadata:  dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id":   self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation":  self.relation,
            "ts":        self.ts,
            "metadata":  self.metadata,
        }


class LineageGraph:
    """SQLite-backed data lineage DAG.

    Parameters
    ----------
    db_path: Filesystem path or ``":memory:"``.
    """

    def __init__(self, db_path: str = "meshflow_lineage.db") -> None:
        self._db_path = db_path
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys=ON")
        else:
            self._mem_conn = None
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    def add_node(
        self,
        kind: str,
        name: str,
        run_id: str = "",
        agent_name: str = "",
        metadata: Optional[dict[str, Any]] = None,
        ts: Optional[float] = None,
        node_id: Optional[str] = None,
    ) -> LineageNode:
        """Add a lineage node.  Returns the created node."""
        node = LineageNode(
            node_id=node_id or str(uuid.uuid4()),
            kind=kind,
            name=name,
            run_id=run_id,
            agent_name=agent_name,
            ts=ts if ts is not None else time.time(),
            metadata=metadata or {},
        )
        self._conn().execute(
            """
            INSERT INTO lineage_nodes
                (node_id, kind, name, run_id, agent_name, ts, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (node.node_id, node.kind, node.name, node.run_id,
             node.agent_name, node.ts, json.dumps(node.metadata)),
        )
        self._conn().commit()
        return node

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str = "derived_from",
        metadata: Optional[dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> LineageEdge:
        """Add a directed edge from *source_id* → *target_id*."""
        edge = LineageEdge(
            edge_id=str(uuid.uuid4()),
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            ts=ts if ts is not None else time.time(),
            metadata=metadata or {},
        )
        self._conn().execute(
            """
            INSERT INTO lineage_edges
                (edge_id, source_id, target_id, relation, ts, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (edge.edge_id, edge.source_id, edge.target_id,
             edge.relation, edge.ts, json.dumps(edge.metadata)),
        )
        self._conn().commit()
        return edge

    # ── Traversal ──────────────────────────────────────────────────────────────

    def trace_upstream(self, node_id: str) -> list[LineageNode]:
        """Return all ancestor nodes (BFS over incoming edges)."""
        visited: set[str] = set()
        queue = [node_id]
        result: list[LineageNode] = []

        while queue:
            current = queue.pop(0)
            rows = self._conn().execute(
                "SELECT source_id FROM lineage_edges WHERE target_id=?", (current,)
            ).fetchall()
            for row in rows:
                src = row["source_id"]
                if src not in visited:
                    visited.add(src)
                    node = self.get_node(src)
                    if node:
                        result.append(node)
                        queue.append(src)
        return result

    def trace_downstream(self, node_id: str) -> list[LineageNode]:
        """Return all descendant nodes (BFS over outgoing edges)."""
        visited: set[str] = set()
        queue = [node_id]
        result: list[LineageNode] = []

        while queue:
            current = queue.pop(0)
            rows = self._conn().execute(
                "SELECT target_id FROM lineage_edges WHERE source_id=?", (current,)
            ).fetchall()
            for row in rows:
                tgt = row["target_id"]
                if tgt not in visited:
                    visited.add(tgt)
                    node = self.get_node(tgt)
                    if node:
                        result.append(node)
                        queue.append(tgt)
        return result

    def impact_analysis(self, node_id: str) -> list[LineageNode]:
        """Alias for trace_downstream — 'who is affected if this node changes?'"""
        return self.trace_downstream(node_id)

    def subgraph(self, node_id: str) -> dict[str, Any]:
        """Return {nodes: [...], edges: [...]} for the full connected component."""
        upstream   = self.trace_upstream(node_id)
        downstream = self.trace_downstream(node_id)
        root = self.get_node(node_id)

        all_nodes: dict[str, LineageNode] = {}
        if root:
            all_nodes[node_id] = root
        for n in upstream + downstream:
            all_nodes[n.node_id] = n

        node_ids = set(all_nodes.keys())
        rows = self._conn().execute(
            f"""
            SELECT * FROM lineage_edges
            WHERE source_id IN ({','.join('?' * len(node_ids))})
               OR target_id IN ({','.join('?' * len(node_ids))})
            """,
            list(node_ids) * 2,
        ).fetchall() if node_ids else []

        edges = [self._edge_from_row(r) for r in rows]
        return {
            "nodes": [n.to_dict() for n in all_nodes.values()],
            "edges": [e.to_dict() for e in edges],
        }

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> Optional[LineageNode]:
        row = self._conn().execute(
            "SELECT * FROM lineage_nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        return self._node_from_row(row) if row else None

    def get_edge(self, edge_id: str) -> Optional[LineageEdge]:
        row = self._conn().execute(
            "SELECT * FROM lineage_edges WHERE edge_id=?", (edge_id,)
        ).fetchone()
        return self._edge_from_row(row) if row else None

    def for_run(self, run_id: str) -> list[LineageNode]:
        rows = self._conn().execute(
            "SELECT * FROM lineage_nodes WHERE run_id=? ORDER BY ts ASC", (run_id,)
        ).fetchall()
        return [self._node_from_row(r) for r in rows]

    def for_agent(self, agent_name: str) -> list[LineageNode]:
        rows = self._conn().execute(
            "SELECT * FROM lineage_nodes WHERE agent_name=? ORDER BY ts ASC", (agent_name,)
        ).fetchall()
        return [self._node_from_row(r) for r in rows]

    def edges_from(self, node_id: str) -> list[LineageEdge]:
        rows = self._conn().execute(
            "SELECT * FROM lineage_edges WHERE source_id=?", (node_id,)
        ).fetchall()
        return [self._edge_from_row(r) for r in rows]

    def edges_to(self, node_id: str) -> list[LineageEdge]:
        rows = self._conn().execute(
            "SELECT * FROM lineage_edges WHERE target_id=?", (node_id,)
        ).fetchall()
        return [self._edge_from_row(r) for r in rows]

    def node_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM lineage_nodes").fetchone()[0]

    def edge_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM lineage_edges").fetchone()[0]

    # ── GDPR erasure ───────────────────────────────────────────────────────────

    def delete_subject(self, name: str) -> int:
        """Delete all nodes (and their edges) whose name matches *name*.

        This implements the GDPR right-to-erasure for a data subject.
        Returns the number of nodes deleted.
        """
        con = self._conn()
        node_rows = con.execute(
            "SELECT node_id FROM lineage_nodes WHERE name=?", (name,)
        ).fetchall()
        node_ids = [r["node_id"] for r in node_rows]
        if not node_ids:
            return 0
        placeholders = ",".join("?" * len(node_ids))
        con.execute(
            f"DELETE FROM lineage_edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            node_ids * 2,
        )
        cur = con.execute(
            f"DELETE FROM lineage_nodes WHERE node_id IN ({placeholders})", node_ids
        )
        con.commit()
        return cur.rowcount

    def delete_node(self, node_id: str) -> bool:
        """Delete a single node and all its incident edges."""
        con = self._conn()
        con.execute(
            "DELETE FROM lineage_edges WHERE source_id=? OR target_id=?",
            (node_id, node_id),
        )
        cur = con.execute("DELETE FROM lineage_nodes WHERE node_id=?", (node_id,))
        con.commit()
        return cur.rowcount > 0

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _node_from_row(row: sqlite3.Row) -> LineageNode:
        d = dict(row)
        return LineageNode(
            node_id=d["node_id"],
            kind=d["kind"],
            name=d["name"],
            run_id=d["run_id"],
            agent_name=d["agent_name"],
            ts=d["ts"],
            metadata=json.loads(d["metadata_json"]),
        )

    @staticmethod
    def _edge_from_row(row: sqlite3.Row) -> LineageEdge:
        d = dict(row)
        return LineageEdge(
            edge_id=d["edge_id"],
            source_id=d["source_id"],
            target_id=d["target_id"],
            relation=d["relation"],
            ts=d["ts"],
            metadata=json.loads(d["metadata_json"]),
        )
