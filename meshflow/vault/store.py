"""Sprint 60 — Secret Vault.

Encrypted at-rest storage for agent credentials and API keys.
Every read is access-logged. Secrets are AES-128 (Fernet) encrypted;
the vault master key is derived via PBKDF2-SHA256 from a passphrase.

VaultSecret    — plaintext secret dataclass (never persisted as-is).
VaultEntry     — encrypted DB row (ciphertext + metadata).
VaultAuditLog  — who accessed what, and when.
VaultStore     — SQLite-backed encrypted secret store.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

# ── Crypto helpers (Fernet from stdlib cryptography) ──────────────────────────

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except ImportError:
    _HAS_FERNET = False


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte key via PBKDF2-SHA256, then base64url-encode for Fernet."""
    raw = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iterations=100_000)
    # Fernet requires 32 bytes url-safe base64
    return base64.urlsafe_b64encode(raw)


def _xor_encrypt(data: bytes, key: bytes) -> bytes:
    """Fallback XOR cipher (for envs without cryptography)."""
    stream = hashlib.shake_256(key).digest(len(data))  # type: ignore[attr-defined]
    return bytes(a ^ b for a, b in zip(data, stream))


_DDL = """
CREATE TABLE IF NOT EXISTS vault_secrets (
    secret_id   TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    category    TEXT    NOT NULL DEFAULT 'generic',
    ciphertext  BLOB    NOT NULL,
    salt        BLOB    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    created_by  TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL,
    rotated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_vs_name ON vault_secrets(name);

CREATE TABLE IF NOT EXISTS vault_audit (
    audit_id    TEXT    PRIMARY KEY,
    secret_id   TEXT    NOT NULL,
    secret_name TEXT    NOT NULL,
    accessed_by TEXT    NOT NULL,
    operation   TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_va_secret ON vault_audit(secret_id, ts DESC);
"""

_VALID_OPS = frozenset({"read", "write", "rotate", "delete"})


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class VaultSecret:
    secret_id:   str
    name:        str
    value:       str
    category:    str
    description: str
    created_by:  str
    created_at:  float
    rotated_at:  Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "secret_id":   self.secret_id,
            "name":        self.name,
            "category":    self.category,
            "description": self.description,
            "created_by":  self.created_by,
            "created_at":  self.created_at,
            "rotated_at":  self.rotated_at,
        }


@dataclass
class VaultAuditLog:
    audit_id:    str
    secret_id:   str
    secret_name: str
    accessed_by: str
    operation:   str
    ts:          float

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id":    self.audit_id,
            "secret_id":   self.secret_id,
            "secret_name": self.secret_name,
            "accessed_by": self.accessed_by,
            "operation":   self.operation,
            "ts":          self.ts,
        }


# ── VaultStore ────────────────────────────────────────────────────────────────

class VaultStore:
    """Encrypted secret store backed by SQLite.

    Parameters
    ----------
    db_path:    SQLite path or ``":memory:"``.
    passphrase: Master passphrase used to derive the encryption key.
                Each secret uses a unique random salt so the same passphrase
                produces different ciphertext per secret.
    """

    def __init__(
        self,
        db_path: str = "meshflow_vault.db",
        passphrase: str = "meshflow-default-passphrase",
    ) -> None:
        self._db_path = db_path
        self._passphrase = passphrase
        con = sqlite3.connect(
            db_path if db_path == ":memory:" else db_path,
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        if db_path != ":memory:":
            con.execute("PRAGMA journal_mode=WAL")
        self._mem_conn: sqlite3.Connection = con
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        return self._mem_conn

    def _ensure_schema(self) -> None:
        con = self._conn()
        con.executescript(_DDL)
        con.commit()

    # ── Encryption / decryption ───────────────────────────────────────────────

    def _encrypt(self, plaintext: str, salt: bytes) -> bytes:
        key = _derive_key(self._passphrase, salt)
        data = plaintext.encode()
        if _HAS_FERNET:
            return Fernet(key).encrypt(data)
        return _xor_encrypt(data, key)

    def _decrypt(self, ciphertext: bytes, salt: bytes) -> str:
        key = _derive_key(self._passphrase, salt)
        if _HAS_FERNET:
            try:
                return Fernet(key).decrypt(ciphertext).decode()
            except Exception:
                raise ValueError("Decryption failed — wrong passphrase or corrupted data")
        return _xor_encrypt(ciphertext, key).decode()

    # ── Audit helper ──────────────────────────────────────────────────────────

    def _audit(self, secret_id: str, secret_name: str, accessed_by: str, operation: str) -> None:
        self._conn().execute(
            "INSERT INTO vault_audit (audit_id,secret_id,secret_name,accessed_by,operation,ts) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), secret_id, secret_name, accessed_by, operation, time.time()),
        )
        self._conn().commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def store(
        self,
        name: str,
        value: str,
        category: str = "generic",
        description: str = "",
        created_by: str = "cli",
    ) -> VaultSecret:
        salt = os.urandom(16)
        ciphertext = self._encrypt(value, salt)
        secret_id = str(uuid.uuid4())
        now = time.time()
        self._conn().execute(
            """INSERT INTO vault_secrets
               (secret_id, name, category, ciphertext, salt, description, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (secret_id, name, category, ciphertext, salt, description, created_by, now),
        )
        self._conn().commit()
        self._audit(secret_id, name, created_by, "write")
        return VaultSecret(secret_id, name, value, category, description, created_by, now, None)

    def retrieve(self, name: str, accessed_by: str = "cli") -> Optional[VaultSecret]:
        row = self._conn().execute(
            "SELECT * FROM vault_secrets WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        value = self._decrypt(d["ciphertext"], d["salt"])
        self._audit(d["secret_id"], name, accessed_by, "read")
        return VaultSecret(
            secret_id=d["secret_id"], name=name, value=value,
            category=d["category"], description=d["description"],
            created_by=d["created_by"], created_at=d["created_at"],
            rotated_at=d["rotated_at"],
        )

    def rotate(self, name: str, new_value: str, rotated_by: str = "cli") -> bool:
        row = self._conn().execute(
            "SELECT secret_id FROM vault_secrets WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            return False
        secret_id = row[0]
        salt = os.urandom(16)
        ciphertext = self._encrypt(new_value, salt)
        self._conn().execute(
            "UPDATE vault_secrets SET ciphertext=?, salt=?, rotated_at=? WHERE name=?",
            (ciphertext, salt, time.time(), name),
        )
        self._conn().commit()
        self._audit(secret_id, name, rotated_by, "rotate")
        return True

    def delete(self, name: str, deleted_by: str = "cli") -> bool:
        row = self._conn().execute(
            "SELECT secret_id FROM vault_secrets WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            return False
        secret_id = row[0]
        self._audit(secret_id, name, deleted_by, "delete")
        cur = self._conn().execute("DELETE FROM vault_secrets WHERE name=?", (name,))
        self._conn().commit()
        return cur.rowcount > 0

    def list_secrets(self, category: str = "") -> list[dict[str, Any]]:
        if category:
            rows = self._conn().execute(
                "SELECT secret_id,name,category,description,created_by,created_at,rotated_at "
                "FROM vault_secrets WHERE category=? ORDER BY created_at DESC", (category,)
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT secret_id,name,category,description,created_by,created_at,rotated_at "
                "FROM vault_secrets ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def audit_log(self, name: str = "", limit: int = 50) -> list[VaultAuditLog]:
        if name:
            rows = self._conn().execute(
                "SELECT * FROM vault_audit WHERE secret_name=? ORDER BY ts DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM vault_audit ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            VaultAuditLog(
                audit_id=r["audit_id"], secret_id=r["secret_id"],
                secret_name=r["secret_name"], accessed_by=r["accessed_by"],
                operation=r["operation"], ts=r["ts"],
            )
            for r in rows
        ]

    def exists(self, name: str) -> bool:
        return self._conn().execute(
            "SELECT 1 FROM vault_secrets WHERE name=?", (name,)
        ).fetchone() is not None

    def count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM vault_secrets").fetchone()[0]
