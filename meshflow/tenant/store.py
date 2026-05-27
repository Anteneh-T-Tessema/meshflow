"""Sprint 61 — Tenant Isolation.

Hard data boundaries between customers/tenants for multi-tenant SaaS.

Tenant          — tenant record with metadata and status.
TenantContext   — thread-local active-tenant binding.
TenantStore     — SQLite CRUD for tenant registry.
scoped_db_path  — derive per-tenant DB paths for any store.
TenantGuard     — middleware: rejects requests without a valid tenant context.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

_DDL = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    slug        TEXT    NOT NULL UNIQUE,
    plan        TEXT    NOT NULL DEFAULT 'free',
    status      TEXT    NOT NULL DEFAULT 'active',
    metadata    TEXT    NOT NULL DEFAULT '{}',
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ten_slug   ON tenants(slug);
CREATE INDEX IF NOT EXISTS idx_ten_status ON tenants(status);
"""

_VALID_PLANS    = frozenset({"free", "pro", "enterprise"})
_VALID_STATUSES = frozenset({"active", "suspended", "deleted"})


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class Tenant:
    tenant_id:  str
    name:       str
    slug:       str
    plan:       str
    status:     str
    metadata:   dict[str, Any]
    created_at: float

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":  self.tenant_id,
            "name":       self.name,
            "slug":       self.slug,
            "plan":       self.plan,
            "status":     self.status,
            "metadata":   self.metadata,
            "created_at": self.created_at,
        }


# ── Thread-local tenant context ───────────────────────────────────────────────

class TenantContext:
    """Thread-local binding of the currently active tenant."""

    _local = threading.local()

    @classmethod
    def set(cls, tenant_id: str) -> None:
        cls._local.tenant_id = tenant_id

    @classmethod
    def get(cls) -> Optional[str]:
        return getattr(cls._local, "tenant_id", None)

    @classmethod
    def clear(cls) -> None:
        cls._local.tenant_id = None

    @classmethod
    def require(cls) -> str:
        tid = cls.get()
        if not tid:
            raise RuntimeError("No tenant context set — call TenantContext.set(tenant_id) first")
        return tid


def scoped_db_path(base: str, tenant_id: str) -> str:
    """Return a per-tenant DB path: ``meshflow_flags.db`` → ``meshflow_flags_<slug>.db``."""
    if base == ":memory:":
        return ":memory:"
    if base.endswith(".db"):
        return base[:-3] + f"_{tenant_id[:8]}.db"
    return f"{base}_{tenant_id[:8]}"


# ── TenantStore ───────────────────────────────────────────────────────────────

class TenantStore:
    """SQLite-backed tenant registry."""

    def __init__(self, db_path: str = "meshflow_tenants.db") -> None:
        self._db_path = db_path
        if db_path == ":memory:":
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    def create(
        self,
        name: str,
        slug: str,
        plan: str = "free",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Tenant:
        if plan not in _VALID_PLANS:
            raise ValueError(f"plan must be one of {sorted(_VALID_PLANS)}")
        import json
        tenant = Tenant(
            tenant_id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            plan=plan,
            status="active",
            metadata=metadata or {},
            created_at=time.time(),
        )
        self._conn().execute(
            "INSERT INTO tenants (tenant_id,name,slug,plan,status,metadata,created_at) VALUES (?,?,?,?,?,?,?)",
            (tenant.tenant_id, tenant.name, tenant.slug, tenant.plan,
             tenant.status, json.dumps(tenant.metadata), tenant.created_at),
        )
        self._conn().commit()
        return tenant

    def get(self, tenant_id: str) -> Optional[Tenant]:
        row = self._conn().execute(
            "SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_slug(self, slug: str) -> Optional[Tenant]:
        row = self._conn().execute(
            "SELECT * FROM tenants WHERE slug=?", (slug,)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_tenants(self, status: str = "") -> list[Tenant]:
        if status:
            rows = self._conn().execute(
                "SELECT * FROM tenants WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM tenants ORDER BY created_at DESC"
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def update_status(self, tenant_id: str, status: str) -> bool:
        if status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        cur = self._conn().execute(
            "UPDATE tenants SET status=? WHERE tenant_id=?", (status, tenant_id)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def update_plan(self, tenant_id: str, plan: str) -> bool:
        if plan not in _VALID_PLANS:
            raise ValueError(f"plan must be one of {sorted(_VALID_PLANS)}")
        cur = self._conn().execute(
            "UPDATE tenants SET plan=? WHERE tenant_id=?", (plan, tenant_id)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def delete(self, tenant_id: str) -> bool:
        cur = self._conn().execute("DELETE FROM tenants WHERE tenant_id=?", (tenant_id,))
        self._conn().commit()
        return cur.rowcount > 0

    def count(self, status: str = "") -> int:
        if status:
            return self._conn().execute(
                "SELECT COUNT(*) FROM tenants WHERE status=?", (status,)
            ).fetchone()[0]
        return self._conn().execute("SELECT COUNT(*) FROM tenants").fetchone()[0]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Tenant:
        import json
        d = dict(row)
        return Tenant(
            tenant_id=d["tenant_id"],
            name=d["name"],
            slug=d["slug"],
            plan=d["plan"],
            status=d["status"],
            metadata=json.loads(d["metadata"]),
            created_at=d["created_at"],
        )


# ── TenantGuard ───────────────────────────────────────────────────────────────

class TenantGuard:
    """Validates active tenant context against the tenant registry."""

    def __init__(self, store: TenantStore) -> None:
        self._store = store

    def check(self, tenant_id: Optional[str] = None) -> Tenant:
        tid = tenant_id or TenantContext.get()
        if not tid:
            raise PermissionError("No tenant context — multi-tenant isolation violated")
        tenant = self._store.get(tid)
        if tenant is None:
            raise PermissionError(f"Tenant {tid!r} not found")
        if not tenant.is_active:
            raise PermissionError(f"Tenant {tid!r} is {tenant.status}")
        return tenant
