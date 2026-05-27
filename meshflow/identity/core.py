"""Sprint 57 — Agent Identity: HMAC-signed tokens, identity store, zero-trust auth.

No external crypto dependencies — uses stdlib ``hmac`` + ``hashlib``.

Token format (inspired by JWT, stdlib-only):
    base64url(header_json).base64url(payload_json).base64url(hmac_sha256_hex)

AgentIdentity   — registered identity record (stored in SQLite).
AgentToken      — decoded token claims.
IdentityStore   — SQLite CRUD for identities.
sign_token()    — issue a signed token for an identity.
verify_token()  — verify signature and expiry; return AgentToken or None.
decode_token()  — decode without signature verification (for inspection only).

Usage
-----
    from meshflow.identity.core import IdentityStore, sign_token, verify_token

    store = IdentityStore(":memory:")
    identity = store.register("billing-agent", capabilities=["read", "write"])

    token = sign_token(identity, secret="shared-secret", ttl_s=3600)
    claims = verify_token(token, secret="shared-secret")
    # claims.agent_name == "billing-agent"
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Token encoding ─────────────────────────────────────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (padding % 4))


def _sign_payload(header_b64: str, payload_b64: str, secret: str) -> str:
    msg = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return _b64url_encode(sig)


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class AgentIdentity:
    agent_id:     str
    name:         str
    capabilities: list[str]
    issuer:       str
    created_at:   float
    revoked:      bool
    metadata:     dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id":     self.agent_id,
            "name":         self.name,
            "capabilities": self.capabilities,
            "issuer":       self.issuer,
            "created_at":   self.created_at,
            "revoked":      self.revoked,
            "metadata":     self.metadata,
        }


@dataclass
class AgentToken:
    token_id:     str
    agent_id:     str
    agent_name:   str
    capabilities: list[str]
    issuer:       str
    issued_at:    float
    expires_at:   float

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id":     self.token_id,
            "agent_id":     self.agent_id,
            "agent_name":   self.agent_name,
            "capabilities": self.capabilities,
            "issuer":       self.issuer,
            "issued_at":    self.issued_at,
            "expires_at":   self.expires_at,
        }


# ── Token operations ──────────────────────────────────────────────────────────

_HEADER = _b64url_encode(json.dumps({"alg": "HS256", "typ": "MFT"}).encode())


def sign_token(
    identity: AgentIdentity,
    secret: str,
    ttl_s: float = 3600.0,
    now: Optional[float] = None,
) -> str:
    """Issue a signed token for *identity*.  Returns the token string."""
    ts = now if now is not None else time.time()
    payload = {
        "tid": str(uuid.uuid4()),
        "aid": identity.agent_id,
        "nam": identity.name,
        "cap": identity.capabilities,
        "iss": identity.issuer,
        "iat": ts,
        "exp": ts + ttl_s,
    }
    payload_b64 = _b64url_encode(json.dumps(payload).encode())
    sig = _sign_payload(_HEADER, payload_b64, secret)
    return f"{_HEADER}.{payload_b64}.{sig}"


def decode_token(token: str) -> Optional[AgentToken]:
    """Decode token claims without verifying the signature."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = json.loads(_b64url_decode(parts[1]))
        return AgentToken(
            token_id=payload["tid"],
            agent_id=payload["aid"],
            agent_name=payload["nam"],
            capabilities=payload.get("cap", []),
            issuer=payload.get("iss", ""),
            issued_at=payload["iat"],
            expires_at=payload["exp"],
        )
    except Exception:
        return None


def verify_token(
    token: str,
    secret: str,
    now: Optional[float] = None,
) -> Optional[AgentToken]:
    """Verify signature and expiry.  Returns AgentToken on success, None otherwise."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        expected_sig = _sign_payload(header_b64, payload_b64, secret)
        if not hmac.compare_digest(expected_sig, sig_b64):
            return None
    except Exception:
        return None

    claims = decode_token(token)
    if claims is None:
        return None

    ts = now if now is not None else time.time()
    if ts >= claims.expires_at:
        return None  # expired

    return claims


# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS agent_identities (
    agent_id      TEXT    PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE,
    capabilities  TEXT    NOT NULL DEFAULT '[]',
    issuer        TEXT    NOT NULL DEFAULT 'meshflow',
    created_at    REAL    NOT NULL,
    revoked       INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ai_name ON agent_identities(name);
"""


# ── IdentityStore ─────────────────────────────────────────────────────────────

class IdentityStore:
    """SQLite-backed registry of agent identities."""

    def __init__(self, db_path: str = "meshflow_identity.db") -> None:
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

    # ── Write ──────────────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        capabilities: Optional[list[str]] = None,
        issuer: str = "meshflow",
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentIdentity:
        identity = AgentIdentity(
            agent_id=str(uuid.uuid4()),
            name=name,
            capabilities=capabilities or [],
            issuer=issuer,
            created_at=time.time(),
            revoked=False,
            metadata=metadata or {},
        )
        self._conn().execute(
            """
            INSERT INTO agent_identities
                (agent_id, name, capabilities, issuer, created_at, revoked, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity.agent_id, identity.name,
                json.dumps(identity.capabilities), identity.issuer,
                identity.created_at, int(identity.revoked),
                json.dumps(identity.metadata),
            ),
        )
        self._conn().commit()
        return identity

    def revoke(self, agent_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE agent_identities SET revoked=1 WHERE agent_id=?", (agent_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    def delete(self, agent_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM agent_identities WHERE agent_id=?", (agent_id,)
        )
        self._conn().commit()
        return cur.rowcount > 0

    # ── Query ──────────────────────────────────────────────────────────────────

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        row = self._conn().execute(
            "SELECT * FROM agent_identities WHERE agent_id=?", (agent_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_name(self, name: str) -> Optional[AgentIdentity]:
        row = self._conn().execute(
            "SELECT * FROM agent_identities WHERE name=?", (name,)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_identities(self, active_only: bool = False) -> list[AgentIdentity]:
        sql = "SELECT * FROM agent_identities"
        if active_only:
            sql += " WHERE revoked=0"
        sql += " ORDER BY created_at ASC"
        return [self._from_row(r) for r in self._conn().execute(sql).fetchall()]

    def count(self, active_only: bool = False) -> int:
        if active_only:
            return self._conn().execute(
                "SELECT COUNT(*) FROM agent_identities WHERE revoked=0"
            ).fetchone()[0]
        return self._conn().execute(
            "SELECT COUNT(*) FROM agent_identities"
        ).fetchone()[0]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AgentIdentity:
        d = dict(row)
        return AgentIdentity(
            agent_id=d["agent_id"],
            name=d["name"],
            capabilities=json.loads(d["capabilities"]),
            issuer=d["issuer"],
            created_at=d["created_at"],
            revoked=bool(d["revoked"]),
            metadata=json.loads(d["metadata_json"]),
        )
