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


# ── Redis checkpoint store ────────────────────────────────────────────────────


class _RedisStore:
    """Redis-backed checkpoint store for cloud-managed durable execution.

    Enables DurableWorkflowExecutor to resume across process boundaries,
    regional failovers, and cold boots — closing the LangGraph Cloud parity gap.

    Install: pip install redis

    Keys are stored as: ``meshflow:checkpoint:<run_id>:<node_id>``
    Run index is stored as: ``meshflow:run_index:<run_id>``

    Parameters
    ----------
    url:
        Redis connection URL, e.g. ``redis://localhost:6379/0`` or
        ``rediss://user:pass@host:6380/0`` for TLS.
    ttl_seconds:
        Key TTL (default: 7 days). Set to 0 for no expiry.
    """

    _PREFIX = "meshflow:checkpoint"
    _INDEX_PREFIX = "meshflow:run_index"

    def __init__(self, url: str = "redis://localhost:6379/0", ttl_seconds: int = 604800) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._client: Any = None

    def _conn(self) -> Any:
        if self._client is None:
            try:
                import redis  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "_RedisStore requires redis: pip install redis"
                ) from exc
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    def _key(self, run_id: str, node_id: str) -> str:
        return f"{self._PREFIX}:{run_id}:{node_id}"

    def _index_key(self, run_id: str) -> str:
        return f"{self._INDEX_PREFIX}:{run_id}"

    def save(self, run_id: str, node_id: str, output: NodeOutput) -> None:
        payload = json.dumps({
            "content": output.content,
            "structured": output.structured,
            "tokens_used": output.tokens_used,
            "model": output.model,
            "confidence": output.confidence,
            "metadata": output.metadata,
        })
        r = self._conn()
        key = self._key(run_id, node_id)
        r.set(key, payload)
        if self._ttl > 0:
            r.expire(key, self._ttl)
        # Track node_ids in a per-run index
        idx_key = self._index_key(run_id)
        r.hset(idx_key, node_id, str(time.time()))
        if self._ttl > 0:
            r.expire(idx_key, self._ttl)

    def load(self, run_id: str, node_id: str) -> NodeOutput | None:
        raw = self._conn().get(self._key(run_id, node_id))
        if raw is None:
            return None
        data = json.loads(raw)
        return NodeOutput(
            content=data["content"],
            structured=data.get("structured", {}),
            tokens_used=data.get("tokens_used", 0),
            model=data.get("model", ""),
            confidence=data.get("confidence", 0.8),
            metadata=data.get("metadata", {}),
        )

    def all_completed(self, run_id: str) -> dict[str, float]:
        raw = self._conn().hgetall(self._index_key(run_id))
        return {nid: float(ts) for nid, ts in raw.items()}

    def delete(self, run_id: str) -> None:
        r = self._conn()
        idx_key = self._index_key(run_id)
        node_ids = list(r.hgetall(idx_key).keys())
        keys = [self._key(run_id, nid) for nid in node_ids] + [idx_key]
        if keys:
            r.delete(*keys)


# ── Postgres checkpoint store ─────────────────────────────────────────────────


class _PostgresStore:
    """PostgreSQL-backed checkpoint store for enterprise cloud deployments.

    Uses a single durable table compatible with managed Postgres services
    (AWS RDS, Azure Database, GCP Cloud SQL, Supabase, Neon).

    Install: pip install psycopg2-binary  (or psycopg2)

    Connection string is read from the ``url`` parameter or
    ``MESHFLOW_POSTGRES_URL`` environment variable.

    Parameters
    ----------
    url:
        PostgreSQL DSN, e.g.
        ``postgresql://user:pass@host:5432/dbname``
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS meshflow_durable_checkpoints (
        run_id       TEXT NOT NULL,
        node_id      TEXT NOT NULL,
        output_json  TEXT NOT NULL,
        completed_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (run_id, node_id)
    )
    """

    def __init__(self, url: str = "") -> None:
        import os
        self._url = url or os.environ.get("MESHFLOW_POSTGRES_URL", "")
        if not self._url:
            raise ValueError(
                "PostgresStore requires a connection URL via the url parameter "
                "or MESHFLOW_POSTGRES_URL environment variable."
            )
        self._conn_obj: Any = None
        self._ensure_table()

    def _conn(self) -> Any:
        if self._conn_obj is None or self._conn_obj.closed:
            try:
                import psycopg2  # type: ignore[import]
                import psycopg2.extras  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "_PostgresStore requires psycopg2: pip install psycopg2-binary"
                ) from exc
            self._conn_obj = psycopg2.connect(self._url)
            self._conn_obj.autocommit = False
        return self._conn_obj

    def _ensure_table(self) -> None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(self._DDL)
        conn.commit()

    def save(self, run_id: str, node_id: str, output: NodeOutput) -> None:
        payload = json.dumps({
            "content": output.content,
            "structured": output.structured,
            "tokens_used": output.tokens_used,
            "model": output.model,
            "confidence": output.confidence,
            "metadata": output.metadata,
        })
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meshflow_durable_checkpoints (run_id, node_id, output_json, completed_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id, node_id) DO UPDATE SET
                    output_json = EXCLUDED.output_json,
                    completed_at = EXCLUDED.completed_at
                """,
                (run_id, node_id, payload, time.time()),
            )
        conn.commit()

    def load(self, run_id: str, node_id: str) -> NodeOutput | None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT output_json FROM meshflow_durable_checkpoints WHERE run_id=%s AND node_id=%s",
                (run_id, node_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return NodeOutput(
            content=data["content"],
            structured=data.get("structured", {}),
            tokens_used=data.get("tokens_used", 0),
            model=data.get("model", ""),
            confidence=data.get("confidence", 0.8),
            metadata=data.get("metadata", {}),
        )

    def all_completed(self, run_id: str) -> dict[str, float]:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT node_id, completed_at FROM meshflow_durable_checkpoints WHERE run_id=%s",
                (run_id,),
            )
            rows = cur.fetchall()
        return {r[0]: r[1] for r in rows}

    def delete(self, run_id: str) -> None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM meshflow_durable_checkpoints WHERE run_id=%s", (run_id,)
            )
        conn.commit()


# ── S3 checkpoint store ───────────────────────────────────────────────────────


class _S3Store:
    """AWS S3-backed checkpoint store for serverless / cloud-native deployments.

    Each checkpoint is stored as a JSON object under the key::

        <prefix>/<run_id>/<node_id>.json

    A lightweight index object tracks all completed nodes::

        <prefix>/<run_id>/_index.json

    Enables DurableWorkflowExecutor to survive Lambda cold starts, ECS task
    replacements, and cross-region failover — a full cloud-managed-resume
    solution without any database.

    Install: pip install boto3

    Parameters
    ----------
    bucket:
        S3 bucket name. Falls back to ``MESHFLOW_S3_BUCKET`` env var.
    prefix:
        Object key prefix (default: ``"meshflow/checkpoints"``).
    region:
        AWS region (default: ``"us-east-1"``). Falls back to
        ``AWS_DEFAULT_REGION`` env var.
    profile_name:
        AWS CLI profile to use (optional, falls back to env/instance role).
    """

    def __init__(
        self,
        bucket: str = "",
        prefix: str = "meshflow/checkpoints",
        region: str = "",
        profile_name: str = "",
    ) -> None:
        import os
        self._bucket = bucket or os.environ.get("MESHFLOW_S3_BUCKET", "")
        if not self._bucket:
            raise ValueError(
                "_S3Store requires a bucket via the bucket parameter or "
                "MESHFLOW_S3_BUCKET environment variable."
            )
        self._prefix = prefix.rstrip("/")
        self._region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self._profile = profile_name
        self._client: Any = None

    def _s3(self) -> Any:
        if self._client is None:
            try:
                import boto3  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ImportError("_S3Store requires boto3: pip install boto3") from exc
            if self._profile:
                session = boto3.Session(profile_name=self._profile)
            else:
                session = boto3.Session()
            self._client = session.client("s3", region_name=self._region)
        return self._client

    def _obj_key(self, run_id: str, node_id: str) -> str:
        return f"{self._prefix}/{run_id}/{node_id}.json"

    def _index_key(self, run_id: str) -> str:
        return f"{self._prefix}/{run_id}/_index.json"

    def _read_index(self, run_id: str) -> dict[str, float]:
        try:
            resp = self._s3().get_object(Bucket=self._bucket, Key=self._index_key(run_id))
            return json.loads(resp["Body"].read().decode())
        except Exception:
            return {}

    def _write_index(self, run_id: str, index: dict[str, float]) -> None:
        self._s3().put_object(
            Bucket=self._bucket,
            Key=self._index_key(run_id),
            Body=json.dumps(index).encode(),
            ContentType="application/json",
        )

    def save(self, run_id: str, node_id: str, output: NodeOutput) -> None:
        payload = json.dumps({
            "content": output.content,
            "structured": output.structured,
            "tokens_used": output.tokens_used,
            "model": output.model,
            "confidence": output.confidence,
            "metadata": output.metadata,
        })
        self._s3().put_object(
            Bucket=self._bucket,
            Key=self._obj_key(run_id, node_id),
            Body=payload.encode(),
            ContentType="application/json",
        )
        index = self._read_index(run_id)
        index[node_id] = time.time()
        self._write_index(run_id, index)

    def load(self, run_id: str, node_id: str) -> NodeOutput | None:
        try:
            resp = self._s3().get_object(
                Bucket=self._bucket, Key=self._obj_key(run_id, node_id)
            )
            data = json.loads(resp["Body"].read().decode())
        except Exception:
            return None
        return NodeOutput(
            content=data["content"],
            structured=data.get("structured", {}),
            tokens_used=data.get("tokens_used", 0),
            model=data.get("model", ""),
            confidence=data.get("confidence", 0.8),
            metadata=data.get("metadata", {}),
        )

    def all_completed(self, run_id: str) -> dict[str, float]:
        return self._read_index(run_id)

    def delete(self, run_id: str) -> None:
        s3 = self._s3()
        paginator = s3.get_paginator("list_objects_v2")
        prefix = f"{self._prefix}/{run_id}/"
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if objects:
                s3.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
                )


# ── DurableWorkflowExecutor ───────────────────────────────────────────────────

class DurableWorkflowExecutor:
    """Wraps a WorkflowDefinition with checkpoint-based durability.

    Parameters
    ----------
    run_id:
        Stable identifier for this workflow run. Use the same run_id to resume.
    backend:
        Persistence backend:
        - ``"memory"``   — in-process only (tests / local dev)
        - ``"sqlite"``   — cross-process, single-machine (default)
        - ``"redis"``    — cross-process, distributed (requires ``pip install redis``)
        - ``"postgres"`` — enterprise cloud (requires ``pip install psycopg2-binary``)
        - ``"s3"``       — AWS S3 / serverless / cross-region (requires ``pip install boto3``)
    db_path:
        Path to the SQLite database. Ignored for non-SQLite backends.
    redis_url:
        Redis connection URL (``redis://host:port/db``). Used when
        ``backend="redis"``. Falls back to ``MESHFLOW_REDIS_URL`` env var.
    postgres_url:
        PostgreSQL DSN. Used when ``backend="postgres"``. Falls back to
        ``MESHFLOW_POSTGRES_URL`` env var.
    s3_bucket:
        S3 bucket name. Used when ``backend="s3"``. Falls back to
        ``MESHFLOW_S3_BUCKET`` env var.
    s3_prefix:
        S3 key prefix (default: ``"meshflow/checkpoints"``).
    """

    def __init__(
        self,
        run_id: str | None = None,
        backend: str = "sqlite",
        db_path: str = ":memory:",
        redis_url: str = "",
        postgres_url: str = "",
        s3_bucket: str = "",
        s3_prefix: str = "meshflow/checkpoints",
    ) -> None:
        import os
        self._run_id = run_id or str(uuid.uuid4())
        if backend == "memory":
            self._store: Any = _MemoryStore()
        elif backend == "redis":
            url = redis_url or os.environ.get("MESHFLOW_REDIS_URL", "redis://localhost:6379/0")
            self._store = _RedisStore(url=url)
        elif backend == "postgres":
            url = postgres_url or os.environ.get("MESHFLOW_POSTGRES_URL", "")
            self._store = _PostgresStore(url=url)
        elif backend == "s3":
            bucket = s3_bucket or os.environ.get("MESHFLOW_S3_BUCKET", "")
            self._store = _S3Store(bucket=bucket, prefix=s3_prefix)
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
        elif isinstance(self._store, _RedisStore):
            forked = DurableWorkflowExecutor(run_id=new_id, backend="redis",
                                              redis_url=self._store._url)
        elif isinstance(self._store, _PostgresStore):
            forked = DurableWorkflowExecutor(run_id=new_id, backend="postgres",
                                              postgres_url=self._store._url)
        elif isinstance(self._store, _S3Store):
            forked = DurableWorkflowExecutor(run_id=new_id, backend="s3",
                                              s3_bucket=self._store._bucket,
                                              s3_prefix=self._store._prefix)
        else:
            forked = DurableWorkflowExecutor(run_id=new_id, backend="sqlite", db_path=self._store._path)
            if self._store._path == ":memory:":
                if hasattr(forked._store, "_conn_obj"):
                    try:
                        forked._store._conn_obj.close()
                    except Exception:
                        pass
                forked._store = self._store
        return forked


__all__ = ["DurableWorkflowExecutor", "_RedisStore", "_PostgresStore", "_S3Store"]
