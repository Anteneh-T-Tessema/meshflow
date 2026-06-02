"""MeshFlow OIDC / SSO Middleware — stdlib-only JWT validation with JWKS caching.

Supports any OIDC-compliant provider: Okta, Auth0, Azure AD, Google Workspace, Keycloak.

Configuration (env vars)
------------------------
MESHFLOW_OIDC_ISSUER          — e.g. https://dev-abc.okta.com
MESHFLOW_OIDC_AUDIENCE        — e.g. meshflow-api
MESHFLOW_OIDC_ROLE_CLAIM      — JWT claim holding role/group info (default: groups)
MESHFLOW_OIDC_ADMIN_GROUP     — group name → admin role
MESHFLOW_OIDC_OPERATOR_GROUP  — group name → operator role
MESHFLOW_OIDC_VIEWER_GROUP    — group name → viewer role
MESHFLOW_OIDC_JWKS_CACHE_TTL  — JWKS cache lifetime in seconds (default: 3600)

Usage::

    from meshflow.security.oidc import OIDCConfig, OIDCValidator, OIDCMiddleware

    cfg = OIDCConfig.from_env()
    validator = OIDCValidator(cfg)
    principal = validator.validate("eyJ...")   # raises on invalid token
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# ── Role constants ─────────────────────────────────────────────────────────────

_ROLES = frozenset({"admin", "operator", "viewer"})
_DEFAULT_ROLE = "viewer"


# ── Config ─────────────────────────────────────────────────────────────────────


@dataclass
class OIDCConfig:
    """All OIDC configuration for a single provider."""

    issuer: str
    """OIDC Issuer URL, e.g. https://dev-abc.okta.com  (no trailing slash)."""

    audience: str = "meshflow-api"
    """Expected `aud` claim value in tokens."""

    role_claim: str = "groups"
    """JWT claim that contains the user's groups or roles."""

    admin_group: str = "meshflow-admins"
    """Group value that maps to the *admin* MeshFlow role."""

    operator_group: str = "meshflow-operators"
    """Group value that maps to the *operator* MeshFlow role."""

    viewer_group: str = "meshflow-viewers"
    """Group value that maps to the *viewer* MeshFlow role."""

    jwks_cache_ttl: int = 3600
    """How long (seconds) to cache the JWKS before re-fetching."""

    # Optional: explicit JWKS URI override (auto-discovered via .well-known if empty)
    jwks_uri: str = ""

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        """Construct an OIDCConfig from environment variables.

        Raises ``ValueError`` if ``MESHFLOW_OIDC_ISSUER`` is not set.
        """
        issuer = os.environ.get("MESHFLOW_OIDC_ISSUER", "").rstrip("/")
        if not issuer:
            raise ValueError(
                "MESHFLOW_OIDC_ISSUER environment variable is required for OIDC auth"
            )
        return cls(
            issuer=issuer,
            audience=os.environ.get("MESHFLOW_OIDC_AUDIENCE", "meshflow-api"),
            role_claim=os.environ.get("MESHFLOW_OIDC_ROLE_CLAIM", "groups"),
            admin_group=os.environ.get("MESHFLOW_OIDC_ADMIN_GROUP", "meshflow-admins"),
            operator_group=os.environ.get("MESHFLOW_OIDC_OPERATOR_GROUP", "meshflow-operators"),
            viewer_group=os.environ.get("MESHFLOW_OIDC_VIEWER_GROUP", "meshflow-viewers"),
            jwks_cache_ttl=int(os.environ.get("MESHFLOW_OIDC_JWKS_CACHE_TTL", "3600")),
        )


# ── Principal ──────────────────────────────────────────────────────────────────


@dataclass
class OIDCPrincipal:
    """Authenticated OIDC principal extracted from a validated JWT."""

    sub: str
    """Subject identifier (unique user ID from the IdP)."""

    email: str
    """User's email address (empty string if not present in token)."""

    role: str
    """Mapped MeshFlow role: admin | operator | viewer."""

    claims: dict[str, Any] = field(default_factory=dict)
    """Full decoded JWT payload for downstream use."""

    # Compatibility shim: server code reads .tenant_id from KeyRecord
    @property
    def tenant_id(self) -> str:
        return self.claims.get("tenant_id", "") or self.claims.get("tid", "") or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "email": self.email,
            "role": self.role,
            "tenant_id": self.tenant_id,
            "auth_method": "oidc",
        }


# ── JWT utilities (stdlib-only: no PyJWT) ─────────────────────────────────────


def _b64url_decode(s: str) -> bytes:
    """Decode a Base64URL string (with or without padding)."""
    # Restore padding
    rem = len(s) % 4
    if rem:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s)


def _decode_jwt_unverified(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    """Split a JWT into (header_dict, payload_dict, signing_input_bytes, signature_bytes).

    Does NOT verify the signature — callers must do that separately.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT: expected 3 dot-separated parts")
    header_raw, payload_raw, sig_raw = parts
    try:
        header: dict[str, Any] = json.loads(_b64url_decode(header_raw))
        payload: dict[str, Any] = json.loads(_b64url_decode(payload_raw))
        signature = _b64url_decode(sig_raw)
    except Exception as exc:
        raise ValueError(f"Failed to decode JWT: {exc}") from exc
    # signing_input is the raw ASCII bytes of "header.payload"
    signing_input = f"{header_raw}.{payload_raw}".encode("ascii")
    return header, payload, signing_input, signature


def _int_from_bytes_be(data: bytes) -> int:
    """Convert big-endian bytes to a Python int."""
    result = 0
    for b in data:
        result = (result << 8) | b
    return result


def _verify_rs256(signing_input: bytes, signature: bytes, jwk: dict[str, Any]) -> bool:
    """Verify an RS256 (RSASSA-PKCS1-v1_5 SHA-256) JWT signature using a JWK.

    Uses only stdlib — no cryptography package required.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        n_bytes = _b64url_decode(jwk["n"])
        e_bytes = _b64url_decode(jwk["e"])
        n_int = _int_from_bytes_be(n_bytes)
        e_int = _int_from_bytes_be(e_bytes)

        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        public_numbers = RSAPublicNumbers(e=e_int, n=n_int)
        public_key = public_numbers.public_key(default_backend())

        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        return True
    except ImportError:
        # Fall back to pure-Python RSA PKCS#1 v1.5 verify (constant-time enough for our purposes)
        return _verify_rs256_stdlib(signing_input, signature, jwk)
    except Exception:
        return False


def _verify_rs256_stdlib(signing_input: bytes, signature: bytes, jwk: dict[str, Any]) -> bool:
    """Pure-stdlib RS256 verification via pow() for environments without cryptography lib."""
    try:
        n = _int_from_bytes_be(_b64url_decode(jwk["n"]))
        e = _int_from_bytes_be(_b64url_decode(jwk["e"]))

        # PKCS#1 v1.5 decryption: m = sig^e mod n
        sig_int = _int_from_bytes_be(signature)
        m = pow(sig_int, e, n)

        # Re-encode to bytes (same byte length as modulus)
        k = (n.bit_length() + 7) // 8
        m_bytes = m.to_bytes(k, "big")

        # Expected PKCS#1 v1.5 DigestInfo for SHA-256
        # 0x00 0x01 [0xff padding] 0x00 [SHA-256 AlgorithmIdentifier] [digest]
        digest = hashlib.sha256(signing_input).digest()
        sha256_oid_prefix = bytes([
            0x30, 0x31, 0x30, 0x0d, 0x06, 0x09,
            0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01,
            0x05, 0x00, 0x04, 0x20,
        ])
        digest_info = sha256_oid_prefix + digest

        # Build expected EM
        ps_len = k - len(digest_info) - 3
        if ps_len < 8:
            return False
        em = b"\x00\x01" + b"\xff" * ps_len + b"\x00" + digest_info

        # Constant-time comparison
        return _ct_compare(m_bytes, em)
    except Exception:
        return False


def _ct_compare(a: bytes, b: bytes) -> bool:
    """Constant-time bytes comparison."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= x ^ y
    return result == 0


def _verify_hs256(signing_input: bytes, signature: bytes, jwk: dict[str, Any]) -> bool:
    """Verify HS256 (HMAC-SHA256) — for symmetric JWTs."""
    import hmac
    try:
        key = _b64url_decode(jwk.get("k", ""))
        expected = hmac.new(key, signing_input, hashlib.sha256).digest()
        return _ct_compare(signature, expected)
    except Exception:
        return False


# ── JWKS cache ─────────────────────────────────────────────────────────────────


class JWKSCache:
    """Thread-safe JWKS fetcher with configurable TTL.

    Fetches keys from the issuer's OIDC discovery endpoint on first use and
    whenever the TTL expires.  Keys are indexed by ``kid`` for O(1) lookup.
    """

    def __init__(self, issuer: str, ttl: int = 3600, jwks_uri: str = "") -> None:
        self._issuer = issuer.rstrip("/")
        self._ttl = ttl
        self._explicit_jwks_uri = jwks_uri
        self._lock = threading.Lock()
        self._keys: dict[str, dict[str, Any]] = {}  # kid → JWK
        self._fetched_at: float = 0.0

    # ── Discovery ──────────────────────────────────────────────────────────────

    def _discover_jwks_uri(self) -> str:
        if self._explicit_jwks_uri:
            return self._explicit_jwks_uri
        url = f"{self._issuer}/.well-known/openid-configuration"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
                doc: dict[str, Any] = json.loads(resp.read().decode())
            return str(doc["jwks_uri"])
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"OIDC discovery failed for issuer '{self._issuer}': {exc}"
            ) from exc

    def _fetch_keys(self) -> dict[str, dict[str, Any]]:
        jwks_uri = self._discover_jwks_uri()
        try:
            with urllib.request.urlopen(jwks_uri, timeout=10) as resp:  # noqa: S310
                doc: dict[str, Any] = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Failed to fetch JWKS from '{jwks_uri}': {exc}") from exc
        keys: dict[str, dict[str, Any]] = {}
        for jwk in doc.get("keys", []):
            kid = jwk.get("kid", "")
            keys[kid] = jwk
            # Also index by empty string to handle JWTs with no kid
            if not kid:
                keys[""] = jwk
        return keys

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, kid: str) -> dict[str, Any] | None:
        """Return the JWK for *kid*, refreshing from provider if TTL expired."""
        now = time.monotonic()
        if now - self._fetched_at > self._ttl:
            with self._lock:
                # Double-checked locking
                if time.monotonic() - self._fetched_at > self._ttl:
                    self._keys = self._fetch_keys()
                    self._fetched_at = time.monotonic()
        return self._keys.get(kid)

    def invalidate(self) -> None:
        """Force a re-fetch on the next call to ``get()``."""
        with self._lock:
            self._fetched_at = 0.0

    def inject(self, keys: dict[str, dict[str, Any]]) -> None:
        """Inject keys directly (used in tests to bypass network)."""
        with self._lock:
            self._keys = keys
            self._fetched_at = time.monotonic()


# ── Token validation errors ────────────────────────────────────────────────────


class OIDCError(Exception):
    """Base class for all OIDC validation errors."""


class TokenExpiredError(OIDCError):
    """Raised when the JWT's ``exp`` claim is in the past."""


class TokenAudienceMismatchError(OIDCError):
    """Raised when the JWT's ``aud`` claim does not match the expected audience."""


class TokenIssuerMismatchError(OIDCError):
    """Raised when the JWT's ``iss`` claim does not match the configured issuer."""


class TokenSignatureError(OIDCError):
    """Raised when the JWT signature cannot be verified."""


class TokenMissingKeyError(OIDCError):
    """Raised when no matching JWK is found for the JWT's ``kid``."""


# ── Validator ──────────────────────────────────────────────────────────────────


class OIDCValidator:
    """Validates OIDC Bearer tokens and returns an :class:`OIDCPrincipal`.

    Validates:
    - Signature (RS256 or HS256) using the issuer's JWKS
    - Expiry (``exp`` claim)
    - Audience (``aud`` claim)
    - Issuer (``iss`` claim)
    - Groups/roles → MeshFlow role mapping
    """

    def __init__(self, config: OIDCConfig, jwks_cache: JWKSCache | None = None) -> None:
        self._cfg = config
        self._cache = jwks_cache or JWKSCache(
            config.issuer,
            ttl=config.jwks_cache_ttl,
            jwks_uri=config.jwks_uri,
        )

    @property
    def jwks_cache(self) -> JWKSCache:
        return self._cache

    def validate(self, token: str) -> OIDCPrincipal:
        """Validate *token* and return an authenticated principal.

        Raises an :class:`OIDCError` subclass on any validation failure.
        """
        header, payload, signing_input, signature = _decode_jwt_unverified(token)

        # 1. Verify expiry first (fast path, no network)
        exp = payload.get("exp")
        if exp is not None and time.time() > exp:
            raise TokenExpiredError("JWT has expired")

        # 2. Verify issuer
        iss = payload.get("iss", "").rstrip("/")
        expected_iss = self._cfg.issuer.rstrip("/")
        if iss != expected_iss:
            raise TokenIssuerMismatchError(
                f"Issuer mismatch: got '{iss}', expected '{expected_iss}'"
            )

        # 3. Verify audience
        aud = payload.get("aud")
        if aud is not None:
            aud_list = [aud] if isinstance(aud, str) else list(aud)
            if self._cfg.audience not in aud_list:
                raise TokenAudienceMismatchError(
                    f"Audience mismatch: got {aud_list!r}, expected '{self._cfg.audience}'"
                )

        # 4. Verify signature
        alg = header.get("alg", "RS256")
        kid = header.get("kid", "")
        jwk = self._cache.get(kid)
        if jwk is None and kid:
            # kid not in cache — try without kid (e.g. single-key provider)
            jwk = self._cache.get("")
        if jwk is None:
            raise TokenMissingKeyError(
                f"No matching JWK found for kid='{kid}'"
            )

        if alg == "RS256":
            valid = _verify_rs256(signing_input, signature, jwk)
        elif alg == "HS256":
            valid = _verify_hs256(signing_input, signature, jwk)
        else:
            raise OIDCError(f"Unsupported JWT algorithm: {alg}")

        if not valid:
            raise TokenSignatureError("JWT signature verification failed")

        # 5. Extract claims → principal
        return self._build_principal(payload)

    def _build_principal(self, payload: dict[str, Any]) -> OIDCPrincipal:
        sub = str(payload.get("sub", ""))
        email = str(payload.get("email", "") or payload.get("upn", "") or "")
        role = self._map_role(payload)
        return OIDCPrincipal(sub=sub, email=email, role=role, claims=payload)

    def _map_role(self, payload: dict[str, Any]) -> str:
        """Map OIDC groups/roles claim to a MeshFlow role string."""
        claim_value = payload.get(self._cfg.role_claim)

        if claim_value is None:
            return _DEFAULT_ROLE

        # Normalize to list
        if isinstance(claim_value, str):
            groups: list[str] = [claim_value]
        elif isinstance(claim_value, list):
            groups = [str(g) for g in claim_value]
        else:
            groups = [str(claim_value)]

        # Highest privilege wins
        if self._cfg.admin_group and self._cfg.admin_group in groups:
            return "admin"
        if self._cfg.operator_group and self._cfg.operator_group in groups:
            return "operator"
        if self._cfg.viewer_group and self._cfg.viewer_group in groups:
            return "viewer"

        return _DEFAULT_ROLE


# ── Middleware ─────────────────────────────────────────────────────────────────


class OIDCMiddleware:
    """OIDC authentication middleware for the MeshFlow aiohttp server.

    Wraps the request principal resolution: first tries OIDC Bearer tokens,
    then falls back to API-key auth (KeyStore / static keys).

    Designed to be integrated directly into the aiohttp server's
    ``_get_principal`` / ``_require_auth`` pattern.
    """

    def __init__(
        self,
        config: OIDCConfig,
        validator: OIDCValidator | None = None,
    ) -> None:
        self._cfg = config
        self._validator = validator or OIDCValidator(config)

    def get_principal(self, headers: Any) -> OIDCPrincipal | None:
        """Extract and validate a Bearer token from *headers*.

        Returns an :class:`OIDCPrincipal` on success or ``None`` if no
        ``Authorization: Bearer`` header is present (so API-key fallback runs).

        Raises :class:`OIDCError` if a Bearer token is present but invalid.
        """
        auth = (
            headers.get("Authorization", "")
            or headers.get("authorization", "")
        )
        if not auth.startswith("Bearer "):
            return None  # No Bearer — caller should try API key auth
        token = auth[7:].strip()
        if not token:
            return None
        return self._validator.validate(token)

    @property
    def config(self) -> OIDCConfig:
        return self._cfg

    @property
    def validator(self) -> OIDCValidator:
        return self._validator


# ── Module-level singleton helpers ─────────────────────────────────────────────

_MIDDLEWARE: OIDCMiddleware | None = None


def get_oidc_middleware() -> OIDCMiddleware | None:
    """Return the process-level OIDCMiddleware singleton, or None if OIDC is not configured."""
    return _MIDDLEWARE


def setup_oidc_middleware(config: OIDCConfig) -> OIDCMiddleware:
    """Initialise (or replace) the process-level OIDCMiddleware singleton."""
    global _MIDDLEWARE
    _MIDDLEWARE = OIDCMiddleware(config)
    return _MIDDLEWARE


def reset_oidc_middleware() -> None:
    """Clear the singleton — used in tests."""
    global _MIDDLEWARE
    _MIDDLEWARE = None
