"""MeshFlow API Key Store — SQLite-backed key management with roles and tenants.

Roles
-----
admin     — full access including key management and destructive operations
operator  — can run tasks, manage webhooks, approve HITL
viewer    — read-only access to traces, compliance reports, metrics

Usage::

    from meshflow.security.api_keys import KeyStore

    store = KeyStore("meshflow_runs.db")
    key_id, raw_key = store.create("ci-bot", role="operator", tenant_id="acme")
    principal = store.verify(raw_key)   # → KeyRecord or None
    store.revoke(key_id)

Environment override
--------------------
Set MESHFLOW_API_KEYS=key1,key2 to add static keys (role=operator, no tenant).
These co-exist with the database-backed keys.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_ROLES = frozenset({"admin", "operator", "viewer"})
_KEY_PREFIX = "mfk_"
_KEY_BYTES = 32  # 256 bits of entropy


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class KeyRecord:
    key_id: str
    name: str
    role: str
    tenant_id: str
    created_at: str
    last_used_at: str
    revoked: bool

    def to_dict(self, *, include_sensitive: bool = False) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "name": self.name,
            "role": self.role,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
        }


# ── Key store ─────────────────────────────────────────────────────────────────


class KeyStore:
    """SQLite-backed API key store with PBKDF2-hashed secrets.

    Thread-safe for concurrent reads; writes serialise through Python's GIL
    and SQLite's WAL mode.
    """

    def __init__(self, db_path: str = "meshflow_runs.db") -> None:
        self._db_path = db_path
        self._static_keys: set[str] = _load_static_keys()
        # Cache the connection for :memory: so the schema persists across calls
        if db_path == ":memory:":
            self._mem_conn: sqlite3.Connection | None = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self._mem_conn = None
        self._ensure_table()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_table(self) -> None:
        con = self._conn()
        con.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id      TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                key_hash    TEXT NOT NULL,
                role        TEXT NOT NULL DEFAULT 'operator',
                tenant_id   TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT '',
                revoked     INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.commit()

    # ── Public API ────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        role: str = "operator",
        tenant_id: str = "",
    ) -> tuple[str, str]:
        """Create a new API key.

        Returns ``(key_id, raw_key)``.  ``raw_key`` is shown only once —
        it is not stored.  Persist it immediately.
        """
        if role not in _ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {sorted(_ROLES)}")
        raw_key = _KEY_PREFIX + secrets.token_urlsafe(_KEY_BYTES)
        key_id = "kid_" + secrets.token_hex(8)
        key_hash = _hash_key(raw_key)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as con:
            con.execute(
                "INSERT INTO api_keys (key_id, name, key_hash, role, tenant_id, created_at) VALUES (?,?,?,?,?,?)",
                (key_id, name, key_hash, role, tenant_id, now),
            )
        return key_id, raw_key

    def verify(self, raw_key: str) -> KeyRecord | None:
        """Verify a raw key and return its record, or None if invalid/revoked.

        Also handles static keys from MESHFLOW_API_KEYS (returned as operator).
        Updates last_used_at on success.
        """
        if raw_key in self._static_keys:
            return KeyRecord(
                key_id="static",
                name="static",
                role="operator",
                tenant_id="",
                created_at="",
                last_used_at="",
                revoked=False,
            )
        key_hash = _hash_key(raw_key)
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0",
                (key_hash,),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            con.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (now, row["key_id"]),
            )
        return KeyRecord(
            key_id=row["key_id"],
            name=row["name"],
            role=row["role"],
            tenant_id=row["tenant_id"],
            created_at=row["created_at"],
            last_used_at=now,
            revoked=False,
        )

    def revoke(self, key_id: str) -> bool:
        """Revoke a key by key_id. Returns True if found, False if not."""
        with self._conn() as con:
            cur = con.execute(
                "UPDATE api_keys SET revoked = 1 WHERE key_id = ? AND revoked = 0",
                (key_id,),
            )
            return cur.rowcount > 0

    def list(self, tenant_id: str | None = None) -> list[KeyRecord]:
        """List all non-revoked keys, optionally filtered by tenant."""
        sql = "SELECT * FROM api_keys WHERE revoked = 0"
        params: list[Any] = []
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC"
        with self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [
            KeyRecord(
                key_id=r["key_id"],
                name=r["name"],
                role=r["role"],
                tenant_id=r["tenant_id"],
                created_at=r["created_at"],
                last_used_at=r["last_used_at"],
                revoked=bool(r["revoked"]),
            )
            for r in rows
        ]

    def list_all(self) -> list[KeyRecord]:
        """List all keys including revoked ones."""
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM api_keys ORDER BY created_at DESC"
            ).fetchall()
        return [
            KeyRecord(
                key_id=r["key_id"],
                name=r["name"],
                role=r["role"],
                tenant_id=r["tenant_id"],
                created_at=r["created_at"],
                last_used_at=r["last_used_at"],
                revoked=bool(r["revoked"]),
            )
            for r in rows
        ]

    def reload_static(self) -> None:
        """Reload static keys from MESHFLOW_API_KEYS env var."""
        self._static_keys = _load_static_keys()

    @property
    def open_mode(self) -> bool:
        """True when no keys are configured — server allows all requests."""
        return not self._static_keys and not self.list()


# ── Module-level singleton ────────────────────────────────────────────────────

_STORE: KeyStore | None = None


def get_key_store(db_path: str = "meshflow_runs.db") -> KeyStore:
    global _STORE
    if _STORE is None:
        _STORE = KeyStore(db_path)
    return _STORE


def reset_key_store() -> None:
    global _STORE
    _STORE = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _hash_key(raw_key: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        raw_key.encode(),
        b"meshflow-key-salt",
        100_000,
    ).hex()


def _load_static_keys() -> set[str]:
    raw = os.environ.get("MESHFLOW_API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()} if raw.strip() else set()
