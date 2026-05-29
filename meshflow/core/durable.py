"""DurableWorkflowExecutor — checkpoint-based resume across process restarts.

Every node's output is persisted to a SQLite database (or in-memory dict) under
a stable (run_id, node_id) key. On re-execution with the same run_id, completed
nodes are replayed from the checkpoint store — the LLM is never called again.

Usage::

    from meshflow import DurableWorkflowExecutor
    from meshflow.core.workflow import WorkflowDefinition

    executor = DurableWorkflowExecutor(run_id="hipaa-review-42", db_path="runs.db")
    result = await executor.run(wf, task="Audit this contract", mesh=Mesh())

    # Interrupted? Resume with the exact same call:
    result = await executor.run(wf, task="Audit this contract", mesh=Mesh())
    # Already-completed nodes are replayed from SQLite — no LLM calls.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from meshflow.core.node import MeshNode, NodeInput, NodeOutput


# ── Checkpoint stores ─────────────────────────────────────────────────────────

class _MemoryStore:
    """In-process checkpoint store — zero persistence, ideal for tests."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], dict[str, Any]] = {}

    def save(self, run_id: str, node_id: str, output: NodeOutput) -> None:
        self._data[(run_id, node_id)] = {
            "content": output.content,
            "structured": output.structured,
            "tokens_used": output.tokens_used,
            "model": output.model,
            "confidence": output.confidence,
            "metadata": output.metadata,
            "completed_at": time.time(),
        }

    def load(self, run_id: str, node_id: str) -> NodeOutput | None:
        row = self._data.get((run_id, node_id))
        if row is None:
            return None
        return NodeOutput(
            content=row["content"],
            structured=row.get("structured", {}),
            tokens_used=row.get("tokens_used", 0),
            model=row.get("model", ""),
            confidence=row.get("confidence", 0.8),
            metadata=row.get("metadata", {}),
        )

    def all_completed(self, run_id: str) -> dict[str, float]:
        return {
            node_id: row["completed_at"]
            for (rid, node_id), row in self._data.items()
            if rid == run_id
        }

    def delete(self, run_id: str) -> None:
        keys = [k for k in self._data if k[0] == run_id]
        for k in keys:
            del self._data[k]


class _SQLiteStore:
    """SQLite-backed checkpoint store for durable cross-process persistence."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS durable_checkpoints (
        run_id       TEXT NOT NULL,
        node_id      TEXT NOT NULL,
        output_json  TEXT NOT NULL,
        completed_at REAL NOT NULL,
        PRIMARY KEY (run_id, node_id)
    )
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        # Keep a single connection for :memory: databases (each new connection
        # to ":memory:" creates an isolated empty database). For file-backed
        # databases we still use a single connection to avoid WAL contention in
        # tests; production callers can share one executor per process.
        self._conn_obj = sqlite3.connect(db_path, check_same_thread=False)
        self._conn_obj.row_factory = sqlite3.Row
        self._conn_obj.execute(self._DDL)
        self._conn_obj.commit()

    def _conn(self) -> sqlite3.Connection:
        return self._conn_obj

    def save(self, run_id: str, node_id: str, output: NodeOutput) -> None:
        payload = json.dumps({
            "content": output.content,
            "structured": output.structured,
            "tokens_used": output.tokens_used,
            "model": output.model,
            "confidence": output.confidence,
            "metadata": output.metadata,
        })
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO durable_checkpoints "
                "(run_id, node_id, output_json, completed_at) VALUES (?,?,?,?)",
                (run_id, node_id, payload, time.time()),
            )

    def load(self, run_id: str, node_id: str) -> NodeOutput | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT output_json FROM durable_checkpoints WHERE run_id=? AND node_id=?",
                (run_id, node_id),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["output_json"])
        return NodeOutput(
            content=data["content"],
            structured=data.get("structured", {}),
            tokens_used=data.get("tokens_used", 0),
            model=data.get("model", ""),
            confidence=data.get("confidence", 0.8),
            metadata=data.get("metadata", {}),
        )

    def all_completed(self, run_id: str) -> dict[str, float]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT node_id, completed_at FROM durable_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchall()
        return {r["node_id"]: r["completed_at"] for r in rows}

    def delete(self, run_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM durable_checkpoints WHERE run_id=?", (run_id,)
            )


# ── DurableWorkflowExecutor ───────────────────────────────────────────────────

class DurableWorkflowExecutor:
    """Wraps a WorkflowDefinition with checkpoint-based durability.

    Parameters
    ----------
    run_id:
        Stable identifier for this workflow run. Use the same run_id to resume.
    backend:
        ``"sqlite"`` for cross-process persistence, ``"memory"`` for tests.
    db_path:
        Path to the SQLite database. Ignored when backend is ``"memory"``.
    """

    def __init__(
        self,
        run_id: str | None = None,
        backend: str = "sqlite",
        db_path: str = ":memory:",
    ) -> None:
        self._run_id = run_id or str(uuid.uuid4())
        if backend == "memory":
            self._store: _MemoryStore | _SQLiteStore = _MemoryStore()
        else:
            self._store = _SQLiteStore(db_path)

    @property
    def run_id(self) -> str:
        return self._run_id

    # ── Checkpoint helpers ────────────────────────────────────────────────────

    def status(self) -> dict[str, str]:
        """Return {node_id: "completed"} for all checkpointed nodes."""
        completed = self._store.all_completed(self._run_id)
        return {nid: "completed" for nid in completed}

    def clear(self) -> None:
        """Delete all checkpoints for this run_id (start fresh on next run)."""
        self._store.delete(self._run_id)

    def is_completed(self, node_id: str) -> bool:
        return self._store.load(self._run_id, node_id) is not None

    # ── Execution ─────────────────────────────────────────────────────────────

    def _wrap_node(self, node: MeshNode) -> MeshNode:
        """Return a new MeshNode whose runner checks the checkpoint store first."""
        original_runner = node._runner
        store = self._store
        run_id = self._run_id
        node_id = node.id

        async def _checkpointed_runner(node_input: NodeInput) -> NodeOutput:
            cached = store.load(run_id, node_id)
            if cached is not None:
                cached.metadata["_from_checkpoint"] = True
                return cached

            if original_runner is None:
                raise NotImplementedError(f"Node '{node_id}' has no runner")

            output = await original_runner(node_input)
            store.save(run_id, node_id, output)
            return output

        import dataclasses
        wrapped = dataclasses.replace(node, _runner=_checkpointed_runner)
        return wrapped

    async def run(
        self,
        workflow: Any,  # WorkflowDefinition
        task: str,
        mesh: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> Any:  # WorkflowResult
        """Execute *workflow* with durable checkpoints.

        If a ``Mesh`` instance is not provided, a default one is created.
        Completed nodes from a previous run are replayed from the store.

        Parameters
        ----------
        workflow:
            A ``WorkflowDefinition`` instance.
        task:
            The natural-language task prompt for the workflow.
        mesh:
            Optional ``Mesh`` control plane. A default instance is used if omitted.
        context:
            Optional initial context dict.
        """
        from meshflow.core.mesh import Mesh
        from meshflow.core.runtime import StepRuntime
        from meshflow.core.schemas import policy_for_mode
        from meshflow.security.identity import AgentIdentityProvider
        from meshflow.core.ledger import ReplayLedger

        _mesh = mesh or Mesh()

        # Wrap every node in the workflow with checkpoint-aware runners
        original_nodes = dict(workflow._nodes)
        for node_id, node in original_nodes.items():
            workflow._nodes[node_id] = self._wrap_node(node)

        try:
            pol = workflow.policy
            runtime = StepRuntime(
                policy=pol,
                run_id=self._run_id,
                identity=AgentIdentityProvider(self._run_id),
                ledger=ReplayLedger(":memory:"),
            )
            return await workflow.run(task, runtime, context=context)
        finally:
            # Restore original node runners so the workflow object is unmodified
            for node_id, node in original_nodes.items():
                workflow._nodes[node_id] = node

    def fork(
        self,
        parent_run_id: str,
        before_node_id: str,
        new_run_id: str | None = None,
    ) -> DurableWorkflowExecutor:
        """Create a new DurableWorkflowExecutor by copying checkpoints from parent_run_id.

        Only checkpoints completed strictly before before_node_id's completion time are copied.
        """
        new_id = new_run_id or str(uuid.uuid4())
        completed = self._store.all_completed(parent_run_id)
        if before_node_id not in completed:
            raise ValueError(f"Node '{before_node_id}' not found in checkpoints of run '{parent_run_id}'")
        t_fork = completed[before_node_id]
        for nid, t_comp in completed.items():
            if t_comp < t_fork:
                out = self._store.load(parent_run_id, nid)
                if out is not None:
                    self._store.save(new_id, nid, out)

        if isinstance(self._store, _MemoryStore):
            forked = DurableWorkflowExecutor(run_id=new_id, backend="memory")
            forked._store = self._store  # Share in-memory dict reference
        else:
            forked = DurableWorkflowExecutor(run_id=new_id, backend="sqlite", db_path=self._store._path)
        return forked


__all__ = ["DurableWorkflowExecutor"]
