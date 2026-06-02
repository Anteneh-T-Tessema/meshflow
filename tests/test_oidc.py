"""Tests for meshflow.security.oidc and meshflow.security.sso_providers.

All tests are self-contained: JWT signing uses a freshly generated RSA-2048 key
pair (via the `cryptography` package if available, otherwise a small built-in
RSA key) so no real OIDC provider is required.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import time
from typing import Any

import pytest

# ── Helpers: minimal in-process RSA key generation for test JWTs ──────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_to_bytes_be(n: int, length: int | None = None) -> bytes:
    hex_str = f"{n:x}"
    if len(hex_str) % 2:
        hex_str = "0" + hex_str
    raw = bytes.fromhex(hex_str)
    if length is not None:
        raw = raw.rjust(length, b"\x00")
    return raw


# We use the `cryptography` package for key generation (it's already a transitive
# dep of many MeshFlow modules).  If it is somehow absent we skip the RS256 tests.
try:
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.backends import default_backend

    def _generate_rsa_keypair() -> tuple[Any, Any]:
        """Return (private_key, public_key)."""
        priv = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        return priv, priv.public_key()

    def _public_key_to_jwk(pub: Any, kid: str = "test-kid") -> dict[str, Any]:
        nums = pub.public_key().public_numbers() if hasattr(pub, "private_numbers") else pub.public_numbers()
        n_bytes = _int_to_bytes_be(nums.n)
        e_bytes = _int_to_bytes_be(nums.e)
        return {
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "kid": kid,
            "n": _b64url(n_bytes),
            "e": _b64url(e_bytes),
        }

    def _sign_rs256(header: dict[str, Any], payload: dict[str, Any], priv: Any) -> str:
        h_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        sig = priv.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{h_b64}.{p_b64}.{_b64url(sig)}"

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

requires_crypto = pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography package not installed")


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def rsa_keypair():
    """Fresh RSA-2048 key pair for each test."""
    return _generate_rsa_keypair()


@pytest.fixture()
def base_config():
    """Minimal OIDCConfig pointing at a fake issuer."""
    from meshflow.security.oidc import OIDCConfig
    return OIDCConfig(
        issuer="https://test.example.com",
        audience="meshflow-api",
        role_claim="groups",
        admin_group="meshflow-admins",
        operator_group="meshflow-operators",
        viewer_group="meshflow-viewers",
    )


@pytest.fixture()
def jwks_cache(rsa_keypair, base_config):
    """JWKSCache pre-loaded with the test key (no network calls)."""
    from meshflow.security.oidc import JWKSCache
    priv, pub = rsa_keypair
    jwk = _public_key_to_jwk(pub, kid="test-kid")
    cache = JWKSCache(issuer=base_config.issuer)
    cache.inject({"test-kid": jwk})
    return cache


@pytest.fixture()
def validator(base_config, jwks_cache):
    from meshflow.security.oidc import OIDCValidator
    return OIDCValidator(base_config, jwks_cache=jwks_cache)


def _make_token(
    priv: Any,
    *,
    sub: str = "user123",
    email: str = "user@example.com",
    iss: str = "https://test.example.com",
    aud: str = "meshflow-api",
    groups: list[str] | None = None,
    role_claim: str = "groups",
    exp_offset: int = 3600,
    kid: str = "test-kid",
    extra_claims: dict[str, Any] | None = None,
) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "iss": iss,
        "aud": aud,
        "iat": now,
        "exp": now + exp_offset,
    }
    if groups is not None:
        payload[role_claim] = groups
    if extra_claims:
        payload.update(extra_claims)
    return _sign_rs256(header, payload, priv)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. OIDCConfig.from_env() parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestOIDCConfigFromEnv:
    def test_basic_parsing(self, monkeypatch):
        monkeypatch.setenv("MESHFLOW_OIDC_ISSUER", "https://dev.okta.com")
        monkeypatch.setenv("MESHFLOW_OIDC_AUDIENCE", "my-api")
        monkeypatch.setenv("MESHFLOW_OIDC_ROLE_CLAIM", "roles")
        monkeypatch.setenv("MESHFLOW_OIDC_ADMIN_GROUP", "super-admins")
        monkeypatch.setenv("MESHFLOW_OIDC_OPERATOR_GROUP", "ops")
        monkeypatch.setenv("MESHFLOW_OIDC_VIEWER_GROUP", "readers")
        monkeypatch.setenv("MESHFLOW_OIDC_JWKS_CACHE_TTL", "1800")

        from meshflow.security.oidc import OIDCConfig
        cfg = OIDCConfig.from_env()

        assert cfg.issuer == "https://dev.okta.com"
        assert cfg.audience == "my-api"
        assert cfg.role_claim == "roles"
        assert cfg.admin_group == "super-admins"
        assert cfg.operator_group == "ops"
        assert cfg.viewer_group == "readers"
        assert cfg.jwks_cache_ttl == 1800

    def test_defaults_applied(self, monkeypatch):
        monkeypatch.setenv("MESHFLOW_OIDC_ISSUER", "https://issuer.example.com")
        # Clear any stale overrides
        for k in ("MESHFLOW_OIDC_AUDIENCE", "MESHFLOW_OIDC_ROLE_CLAIM",
                   "MESHFLOW_OIDC_ADMIN_GROUP", "MESHFLOW_OIDC_OPERATOR_GROUP",
                   "MESHFLOW_OIDC_VIEWER_GROUP", "MESHFLOW_OIDC_JWKS_CACHE_TTL"):
            monkeypatch.delenv(k, raising=False)

        from meshflow.security.oidc import OIDCConfig
        cfg = OIDCConfig.from_env()

        assert cfg.audience == "meshflow-api"
        assert cfg.role_claim == "groups"
        assert cfg.admin_group == "meshflow-admins"
        assert cfg.jwks_cache_ttl == 3600

    def test_missing_issuer_raises(self, monkeypatch):
        monkeypatch.delenv("MESHFLOW_OIDC_ISSUER", raising=False)
        from meshflow.security.oidc import OIDCConfig
        with pytest.raises(ValueError, match="MESHFLOW_OIDC_ISSUER"):
            OIDCConfig.from_env()

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("MESHFLOW_OIDC_ISSUER", "https://issuer.example.com/")
        from meshflow.security.oidc import OIDCConfig
        cfg = OIDCConfig.from_env()
        assert not cfg.issuer.endswith("/")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. JWT validation with mocked JWKS (RS256)
# ═══════════════════════════════════════════════════════════════════════════════

@requires_crypto
class TestJWTValidation:
    def test_valid_token_returns_principal(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, groups=["meshflow-operators"])
        principal = validator.validate(token)
        assert principal.sub == "user123"
        assert principal.email == "user@example.com"

    def test_valid_token_admin_role(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, groups=["meshflow-admins"])
        principal = validator.validate(token)
        assert principal.role == "admin"

    def test_valid_token_operator_role(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, groups=["meshflow-operators"])
        principal = validator.validate(token)
        assert principal.role == "operator"

    def test_valid_token_viewer_role(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, groups=["meshflow-viewers"])
        principal = validator.validate(token)
        assert principal.role == "viewer"

    def test_no_groups_defaults_to_viewer(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, groups=None)
        principal = validator.validate(token)
        assert principal.role == "viewer"

    def test_highest_privilege_wins(self, validator, rsa_keypair):
        """When a user is in both operator and admin groups, admin wins."""
        priv, _ = rsa_keypair
        token = _make_token(priv, groups=["meshflow-operators", "meshflow-admins"])
        principal = validator.validate(token)
        assert principal.role == "admin"

    def test_tampered_token_rejected(self, validator, rsa_keypair):
        """Flipping a bit in the signature must fail verification."""
        from meshflow.security.oidc import TokenSignatureError
        priv, _ = rsa_keypair
        token = _make_token(priv)
        parts = token.split(".")
        sig_bytes = bytearray(base64.urlsafe_b64decode(parts[2] + "=="))
        sig_bytes[0] ^= 0xFF
        parts[2] = base64.urlsafe_b64encode(bytes(sig_bytes)).rstrip(b"=").decode()
        tampered = ".".join(parts)
        with pytest.raises(TokenSignatureError):
            validator.validate(tampered)

    def test_wrong_key_rejected(self, base_config):
        """Token signed with a different key must fail."""
        from meshflow.security.oidc import OIDCValidator, JWKSCache, TokenSignatureError
        # Sign with key A, validate against key B
        priv_a, pub_a = _generate_rsa_keypair()
        _, pub_b = _generate_rsa_keypair()
        jwk_b = _public_key_to_jwk(pub_b, kid="test-kid")
        cache = JWKSCache(base_config.issuer)
        cache.inject({"test-kid": jwk_b})
        v = OIDCValidator(base_config, jwks_cache=cache)
        token = _make_token(priv_a)
        with pytest.raises(TokenSignatureError):
            v.validate(token)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Expiry validation
# ═══════════════════════════════════════════════════════════════════════════════

@requires_crypto
class TestExpiryValidation:
    def test_expired_token_rejected(self, validator, rsa_keypair):
        from meshflow.security.oidc import TokenExpiredError
        priv, _ = rsa_keypair
        # Token expired 60 seconds ago
        token = _make_token(priv, exp_offset=-60)
        with pytest.raises(TokenExpiredError):
            validator.validate(token)

    def test_just_expired_token_rejected(self, validator, rsa_keypair):
        from meshflow.security.oidc import TokenExpiredError
        priv, _ = rsa_keypair
        token = _make_token(priv, exp_offset=-1)
        with pytest.raises(TokenExpiredError):
            validator.validate(token)

    def test_future_token_accepted(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, exp_offset=7200)
        principal = validator.validate(token)
        assert principal.sub == "user123"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Audience mismatch
# ═══════════════════════════════════════════════════════════════════════════════

@requires_crypto
class TestAudienceValidation:
    def test_wrong_audience_rejected(self, validator, rsa_keypair):
        from meshflow.security.oidc import TokenAudienceMismatchError
        priv, _ = rsa_keypair
        token = _make_token(priv, aud="some-other-api")
        with pytest.raises(TokenAudienceMismatchError):
            validator.validate(token)

    def test_correct_audience_accepted(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, aud="meshflow-api")
        principal = validator.validate(token)
        assert principal is not None

    def test_audience_as_list_accepted(self, validator, rsa_keypair):
        """aud claim can be a list per spec."""
        priv, _ = rsa_keypair
        header = {"alg": "RS256", "typ": "JWT", "kid": "test-kid"}
        now = int(time.time())
        payload = {
            "sub": "u1", "iss": "https://test.example.com",
            "aud": ["meshflow-api", "other-api"],
            "iat": now, "exp": now + 3600,
        }
        h_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        priv_key, _ = rsa_keypair
        sig = priv_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        token = f"{h_b64}.{p_b64}.{_b64url(sig)}"
        principal = validator.validate(token)
        assert principal is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Issuer mismatch
# ═══════════════════════════════════════════════════════════════════════════════

@requires_crypto
class TestIssuerValidation:
    def test_wrong_issuer_rejected(self, validator, rsa_keypair):
        from meshflow.security.oidc import TokenIssuerMismatchError
        priv, _ = rsa_keypair
        token = _make_token(priv, iss="https://evil.example.com")
        with pytest.raises(TokenIssuerMismatchError):
            validator.validate(token)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Role claim mapping
# ═══════════════════════════════════════════════════════════════════════════════

@requires_crypto
class TestRoleMapping:
    def test_custom_role_claim_name(self, rsa_keypair):
        from meshflow.security.oidc import OIDCConfig, OIDCValidator, JWKSCache
        priv, pub = rsa_keypair
        cfg = OIDCConfig(
            issuer="https://test.example.com",
            audience="meshflow-api",
            role_claim="custom_roles",
            admin_group="admins",
            operator_group="operators",
            viewer_group="viewers",
        )
        jwk = _public_key_to_jwk(pub, kid="test-kid")
        cache = JWKSCache(cfg.issuer)
        cache.inject({"test-kid": jwk})
        v = OIDCValidator(cfg, jwks_cache=cache)

        token = _make_token(priv, role_claim="custom_roles", groups=["admins"])
        principal = v.validate(token)
        assert principal.role == "admin"

    def test_string_group_claim(self, validator, rsa_keypair):
        """groups claim can be a plain string (single group)."""
        from meshflow.security.oidc import OIDCConfig, OIDCValidator, JWKSCache
        priv, pub = rsa_keypair
        cfg = validator._cfg
        cache = JWKSCache(cfg.issuer)
        jwk = _public_key_to_jwk(pub, kid="test-kid")
        cache.inject({"test-kid": jwk})
        v = OIDCValidator(cfg, jwks_cache=cache)

        # Build token manually with string (not list) groups
        header = {"alg": "RS256", "typ": "JWT", "kid": "test-kid"}
        now = int(time.time())
        payload = {
            "sub": "u2", "iss": "https://test.example.com",
            "aud": "meshflow-api", "iat": now, "exp": now + 3600,
            "groups": "meshflow-admins",  # string, not list
        }
        h_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        sig = priv.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        token = f"{h_b64}.{p_b64}.{_b64url(sig)}"
        principal = v.validate(token)
        assert principal.role == "admin"

    def test_unknown_group_maps_to_viewer(self, validator, rsa_keypair):
        priv, _ = rsa_keypair
        token = _make_token(priv, groups=["totally-unknown-group"])
        principal = validator.validate(token)
        assert principal.role == "viewer"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Fallback to API key when no Bearer token
# ═══════════════════════════════════════════════════════════════════════════════

class TestOIDCMiddlewareFallback:
    def test_no_auth_header_returns_none(self, base_config):
        """No Authorization header → OIDCMiddleware returns None (triggers fallback)."""
        from meshflow.security.oidc import OIDCMiddleware
        mw = OIDCMiddleware(base_config)
        headers: dict[str, str] = {}
        assert mw.get_principal(headers) is None

    def test_x_api_key_header_returns_none(self, base_config):
        """X-API-Key header → OIDCMiddleware returns None (API key fallback)."""
        from meshflow.security.oidc import OIDCMiddleware
        mw = OIDCMiddleware(base_config)
        headers = {"X-API-Key": "mfk_someapikey"}
        assert mw.get_principal(headers) is None

    def test_empty_bearer_returns_none(self, base_config):
        from meshflow.security.oidc import OIDCMiddleware
        mw = OIDCMiddleware(base_config)
        headers = {"Authorization": "Bearer "}
        assert mw.get_principal(headers) is None

    def test_non_bearer_scheme_returns_none(self, base_config):
        from meshflow.security.oidc import OIDCMiddleware
        mw = OIDCMiddleware(base_config)
        headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        assert mw.get_principal(headers) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. JWKSCache TTL behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestJWKSCacheTTL:
    def test_inject_bypasses_network(self):
        from meshflow.security.oidc import JWKSCache
        cache = JWKSCache("https://issuer.example.com", ttl=3600)
        cache.inject({"kid1": {"kty": "RSA", "kid": "kid1"}})
        assert cache.get("kid1") is not None

    def test_missing_kid_returns_none(self):
        from meshflow.security.oidc import JWKSCache
        cache = JWKSCache("https://issuer.example.com", ttl=3600)
        cache.inject({"kid1": {"kty": "RSA"}})
        assert cache.get("nonexistent") is None

    def test_invalidate_resets_fetch_time(self, monkeypatch):
        """After invalidate(), next get() triggers a re-fetch (we mock the fetch)."""
        from meshflow.security.oidc import JWKSCache

        fetched: list[int] = []

        cache = JWKSCache("https://issuer.example.com", ttl=3600)
        # Pre-load so _fetched_at is non-zero
        cache.inject({"k": {"kty": "RSA"}})
        original_fetch = cache._fetch_keys

        def mock_fetch() -> dict[str, Any]:
            fetched.append(1)
            return {"k": {"kty": "RSA", "refreshed": True}}

        monkeypatch.setattr(cache, "_fetch_keys", mock_fetch)

        cache.invalidate()
        # Calling get() now should trigger a re-fetch
        cache.get("k")
        assert len(fetched) == 1

    def test_ttl_expired_triggers_refetch(self, monkeypatch):
        """TTL=0 means every call triggers a refetch."""
        from meshflow.security.oidc import JWKSCache

        fetched: list[int] = []

        cache = JWKSCache("https://issuer.example.com", ttl=0)

        def mock_fetch() -> dict[str, Any]:
            fetched.append(1)
            return {"k": {"kty": "RSA"}}

        monkeypatch.setattr(cache, "_fetch_keys", mock_fetch)
        cache.get("k")
        cache.get("k")
        assert len(fetched) >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# 9. SSO provider helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestSSOProviderHelpers:
    def test_okta_issuer_url(self):
        from meshflow.security.sso_providers import OktaConfig
        cfg = OktaConfig("dev-123456.okta.com", audience="my-api")
        assert cfg.issuer == "https://dev-123456.okta.com/oauth2/default"
        assert cfg.audience == "my-api"

    def test_okta_custom_auth_server(self):
        from meshflow.security.sso_providers import OktaConfig
        cfg = OktaConfig("dev-123456.okta.com", authorization_server="aus1abc")
        assert "aus1abc" in cfg.issuer

    def test_auth0_issuer_url(self):
        from meshflow.security.sso_providers import Auth0Config
        cfg = Auth0Config("my-tenant.auth0.com", audience="meshflow-api")
        assert cfg.issuer == "https://my-tenant.auth0.com/"
        assert cfg.audience == "meshflow-api"

    def test_auth0_role_claim_namespaced(self):
        from meshflow.security.sso_providers import Auth0Config
        cfg = Auth0Config("t.auth0.com")
        # Default role claim for Auth0 should be a namespaced URL
        assert cfg.role_claim.startswith("https://")

    def test_azure_ad_v2_issuer(self):
        from meshflow.security.sso_providers import AzureADConfig
        tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        cfg = AzureADConfig(tid, client_id="my-app-id")
        assert tid in cfg.issuer
        assert "v2.0" in cfg.issuer
        assert cfg.audience == "my-app-id"

    def test_azure_ad_v1_issuer(self):
        from meshflow.security.sso_providers import AzureADConfig
        tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        cfg = AzureADConfig(tid, client_id="app", v2=False)
        assert "sts.windows.net" in cfg.issuer
        assert "v2.0" not in cfg.issuer

    def test_google_workspace_issuer(self):
        from meshflow.security.sso_providers import GoogleWorkspaceConfig
        cfg = GoogleWorkspaceConfig(client_id="xxx.apps.googleusercontent.com")
        assert cfg.issuer == "https://accounts.google.com"
        assert cfg.audience == "xxx.apps.googleusercontent.com"

    def test_keycloak_issuer(self):
        from meshflow.security.sso_providers import KeycloakConfig
        cfg = KeycloakConfig("https://keycloak.example.com", realm="myrealm")
        assert cfg.issuer == "https://keycloak.example.com/realms/myrealm"

    def test_keycloak_trailing_slash_stripped(self):
        from meshflow.security.sso_providers import KeycloakConfig
        cfg = KeycloakConfig("https://keycloak.example.com/", realm="myrealm")
        assert not cfg.issuer.startswith("https://keycloak.example.com//")

    def test_all_providers_return_oidcconfig(self):
        from meshflow.security.oidc import OIDCConfig
        from meshflow.security.sso_providers import (
            OktaConfig, Auth0Config, AzureADConfig,
            GoogleWorkspaceConfig, KeycloakConfig,
        )
        configs = [
            OktaConfig("dev.okta.com"),
            Auth0Config("tenant.auth0.com"),
            AzureADConfig("tid", "cid"),
            GoogleWorkspaceConfig("cid"),
            KeycloakConfig("https://kc.example.com", "realm"),
        ]
        for cfg in configs:
            assert isinstance(cfg, OIDCConfig)
            assert cfg.issuer.startswith("https://")


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Singleton helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingletonHelpers:
    def test_setup_and_get(self, base_config):
        from meshflow.security.oidc import (
            setup_oidc_middleware, get_oidc_middleware, reset_oidc_middleware,
        )
        reset_oidc_middleware()
        assert get_oidc_middleware() is None
        mw = setup_oidc_middleware(base_config)
        assert get_oidc_middleware() is mw
        reset_oidc_middleware()
        assert get_oidc_middleware() is None

    def test_reset_clears_singleton(self, base_config):
        from meshflow.security.oidc import (
            setup_oidc_middleware, reset_oidc_middleware, get_oidc_middleware,
        )
        setup_oidc_middleware(base_config)
        reset_oidc_middleware()
        assert get_oidc_middleware() is None


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Top-level __init__ exports
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublicExports:
    def test_oidc_symbols_in_all(self):
        import meshflow
        expected = [
            "OIDCConfig", "OIDCPrincipal", "OIDCValidator", "OIDCMiddleware",
            "OIDCError", "TokenExpiredError", "TokenAudienceMismatchError",
            "TokenIssuerMismatchError", "TokenSignatureError", "JWKSCache",
            "get_oidc_middleware", "setup_oidc_middleware", "reset_oidc_middleware",
            "OktaConfig", "Auth0Config", "AzureADConfig",
            "GoogleWorkspaceConfig", "KeycloakConfig",
        ]
        for name in expected:
            assert name in meshflow.__all__, f"{name!r} missing from meshflow.__all__"
            assert hasattr(meshflow, name), f"meshflow.{name} not accessible"
