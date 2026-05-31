"""Sprint 57 — Agent Identity & Zero-Trust tests."""

from __future__ import annotations

import subprocess
import time
import unittest

import meshflow
from meshflow.identity.core import (
    AgentIdentity, AgentToken, IdentityStore,
    sign_token, verify_token, decode_token,
    _b64url_encode, _b64url_decode,
)


# ── Encoding helpers ──────────────────────────────────────────────────────────

class TestB64Url(unittest.TestCase):
    def test_roundtrip(self):
        data = b"hello world"
        self.assertEqual(_b64url_decode(_b64url_encode(data)), data)

    def test_no_padding_chars(self):
        enc = _b64url_encode(b"test")
        self.assertNotIn("=", enc)

    def test_url_safe_chars(self):
        enc = _b64url_encode(bytes(range(256)))
        for c in enc:
            self.assertNotIn(c, "+/")


# ── AgentIdentity ─────────────────────────────────────────────────────────────

class TestAgentIdentity(unittest.TestCase):
    def _make(self) -> AgentIdentity:
        return AgentIdentity("id-1", "billing", ["read", "write"],
                             "meshflow", time.time(), False, {"env": "prod"})

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ("agent_id", "name", "capabilities", "issuer",
                   "created_at", "revoked", "metadata"):
            self.assertIn(k, d)

    def test_to_dict_revoked_false(self):
        self.assertFalse(self._make().to_dict()["revoked"])


# ── AgentToken ────────────────────────────────────────────────────────────────

class TestAgentToken(unittest.TestCase):
    def _make(self, expires_offset: float = 3600.0) -> AgentToken:
        now = time.time()
        return AgentToken("tid-1", "aid-1", "billing", ["read"],
                          "meshflow", now, now + expires_offset)

    def test_is_expired_false(self):
        self.assertFalse(self._make(3600).is_expired)

    def test_is_expired_true(self):
        self.assertTrue(self._make(-1).is_expired)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ("token_id", "agent_id", "agent_name", "capabilities",
                   "issuer", "issued_at", "expires_at"):
            self.assertIn(k, d)


# ── sign_token / verify_token / decode_token ──────────────────────────────────

class TestTokenOperations(unittest.TestCase):
    def setUp(self):
        self.store = IdentityStore(":memory:")
        self.identity = self.store.register("billing-agent", capabilities=["read", "write"])
        self.secret = "my-secret"

    def test_sign_returns_string(self):
        token = sign_token(self.identity, self.secret)
        self.assertIsInstance(token, str)

    def test_token_has_three_parts(self):
        token = sign_token(self.identity, self.secret)
        self.assertEqual(len(token.split(".")), 3)

    def test_verify_valid_token(self):
        token = sign_token(self.identity, self.secret)
        claims = verify_token(token, self.secret)
        self.assertIsNotNone(claims)

    def test_verify_returns_correct_agent_name(self):
        token = sign_token(self.identity, self.secret)
        claims = verify_token(token, self.secret)
        self.assertEqual(claims.agent_name, "billing-agent")

    def test_verify_returns_capabilities(self):
        token = sign_token(self.identity, self.secret)
        claims = verify_token(token, self.secret)
        self.assertEqual(set(claims.capabilities), {"read", "write"})

    def test_verify_wrong_secret_returns_none(self):
        token = sign_token(self.identity, self.secret)
        self.assertIsNone(verify_token(token, "wrong-secret"))

    def test_verify_tampered_payload_returns_none(self):
        token = sign_token(self.identity, self.secret)
        parts = token.split(".")
        # Replace payload with garbage
        parts[1] = _b64url_encode(b'{"hacked": true}')
        self.assertIsNone(verify_token(".".join(parts), self.secret))

    def test_verify_expired_token_returns_none(self):
        now = time.time()
        token = sign_token(self.identity, self.secret, ttl_s=1.0, now=now - 10)
        self.assertIsNone(verify_token(token, self.secret, now=now))

    def test_verify_not_expired_within_ttl(self):
        now = time.time()
        token = sign_token(self.identity, self.secret, ttl_s=3600, now=now)
        self.assertIsNotNone(verify_token(token, self.secret, now=now + 1800))

    def test_verify_malformed_token_returns_none(self):
        self.assertIsNone(verify_token("not.a.valid.token.here", self.secret))
        self.assertIsNone(verify_token("", self.secret))
        self.assertIsNone(verify_token("only-one-part", self.secret))

    def test_decode_without_verification(self):
        token = sign_token(self.identity, self.secret)
        claims = decode_token(token)
        self.assertIsNotNone(claims)
        self.assertEqual(claims.agent_id, self.identity.agent_id)

    def test_decode_malformed_returns_none(self):
        self.assertIsNone(decode_token("bad"))

    def test_sign_different_tokens_each_call(self):
        t1 = sign_token(self.identity, self.secret)
        t2 = sign_token(self.identity, self.secret)
        # token_id is random uuid → different payloads → different tokens
        self.assertNotEqual(t1, t2)

    def test_verify_agent_id_matches(self):
        token = sign_token(self.identity, self.secret)
        claims = verify_token(token, self.secret)
        self.assertEqual(claims.agent_id, self.identity.agent_id)

    def test_issuer_in_claims(self):
        token = sign_token(self.identity, self.secret)
        claims = verify_token(token, self.secret)
        self.assertEqual(claims.issuer, "meshflow")


# ── IdentityStore ─────────────────────────────────────────────────────────────

class TestIdentityStore(unittest.TestCase):
    def setUp(self):
        self.store = IdentityStore(":memory:")

    def test_register_returns_identity(self):
        i = self.store.register("agent-A")
        self.assertIsInstance(i, AgentIdentity)

    def test_register_stores(self):
        self.store.register("agent-A")
        self.assertEqual(self.store.count(), 1)

    def test_register_capabilities(self):
        i = self.store.register("agent-A", capabilities=["read", "write"])
        fetched = self.store.get(i.agent_id)
        self.assertEqual(set(fetched.capabilities), {"read", "write"})

    def test_register_not_revoked(self):
        i = self.store.register("agent-A")
        self.assertFalse(i.revoked)

    def test_get_by_name(self):
        self.store.register("agent-A")
        i = self.store.get_by_name("agent-A")
        self.assertIsNotNone(i)
        self.assertEqual(i.name, "agent-A")

    def test_get_by_name_unknown_none(self):
        self.assertIsNone(self.store.get_by_name("unknown"))

    def test_get_unknown_none(self):
        self.assertIsNone(self.store.get("no-such-id"))

    def test_revoke_sets_flag(self):
        i = self.store.register("agent-A")
        ok = self.store.revoke(i.agent_id)
        self.assertTrue(ok)
        self.assertTrue(self.store.get(i.agent_id).revoked)

    def test_revoke_unknown_returns_false(self):
        self.assertFalse(self.store.revoke("no-such"))

    def test_delete_removes(self):
        i = self.store.register("agent-A")
        ok = self.store.delete(i.agent_id)
        self.assertTrue(ok)
        self.assertIsNone(self.store.get(i.agent_id))

    def test_delete_unknown_returns_false(self):
        self.assertFalse(self.store.delete("no-such"))

    def test_list_all(self):
        self.store.register("agent-A")
        self.store.register("agent-B")
        self.assertEqual(len(self.store.list_identities()), 2)

    def test_list_active_only(self):
        i = self.store.register("agent-A")
        self.store.register("agent-B")
        self.store.revoke(i.agent_id)
        active = self.store.list_identities(active_only=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].name, "agent-B")

    def test_count_active_only(self):
        i = self.store.register("agent-A")
        self.store.register("agent-B")
        self.store.revoke(i.agent_id)
        self.assertEqual(self.store.count(active_only=True), 1)

    def test_metadata_stored(self):
        i = self.store.register("agent-A", metadata={"env": "prod"})
        self.assertEqual(self.store.get(i.agent_id).metadata, {"env": "prod"})

    def test_issuer_stored(self):
        i = self.store.register("agent-A", issuer="acme-corp")
        self.assertEqual(self.store.get(i.agent_id).issuer, "acme-corp")


# ── CLI tests ─────────────────────────────────────────────────────────────────

class TestIdentityCLI(unittest.TestCase):
    def _args(self, cmd, **kw):
        import argparse
        ns = argparse.Namespace(identity_cmd=cmd, db=":memory:",
                                json_output=False, active_only=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_list_empty(self):
        from meshflow.cli.main import _cmd_identity
        with patch_stdout() as out:
            _cmd_identity(self._args("list"))
        self.assertIn("No identities", out.getvalue())

    def test_create_prints_id(self):
        from meshflow.cli.main import _cmd_identity
        with patch_stdout() as out:
            _cmd_identity(self._args("create", name="my-agent", capabilities=[],
                                      issuer="meshflow"))
        self.assertIn("registered", out.getvalue())

    def test_get_missing_exits(self):
        from meshflow.cli.main import _cmd_identity
        with self.assertRaises(SystemExit):
            _cmd_identity(self._args("get", name="no-such"))

    def test_revoke_missing_exits(self):
        from meshflow.cli.main import _cmd_identity
        with self.assertRaises(SystemExit):
            _cmd_identity(self._args("revoke", agent_id="no-such"))

    def test_verify_invalid_token_exits(self):
        from meshflow.cli.main import _cmd_identity
        with self.assertRaises(SystemExit):
            _cmd_identity(self._args("verify", token="bad.token.here",
                                      secret="secret"))

    def test_verify_valid_token_prints_valid(self):
        from meshflow.cli.main import _cmd_identity
        store = IdentityStore(":memory:")
        identity = store.register("test-agent")
        token = sign_token(identity, "my-secret")
        with patch_stdout() as out:
            _cmd_identity(self._args("verify", token=token, secret="my-secret"))
        self.assertIn("VALID", out.getvalue())


def patch_stdout():
    import io
    from unittest.mock import patch
    return patch("sys.stdout", new_callable=io.StringIO)


# ── Subprocess ────────────────────────────────────────────────────────────────

class TestSubprocessHelp(unittest.TestCase):
    def test_identity_help(self):
        r = subprocess.run(["meshflow", "identity", "--help"],
                           capture_output=True, text=True, timeout=15)
        self.assertIn(r.returncode, (0, 1))


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")

    def test_agent_identity_exported(self):
        self.assertIs(meshflow.AgentIdentity, AgentIdentity)

    def test_agent_token_exported(self):
        self.assertIs(meshflow.AgentToken, AgentToken)

    def test_identity_store_exported(self):
        self.assertIs(meshflow.IdentityStore, IdentityStore)

    def test_sign_token_exported(self):
        self.assertIs(meshflow.sign_token, sign_token)

    def test_verify_token_exported(self):
        self.assertIs(meshflow.verify_token, verify_token)

    def test_decode_token_exported(self):
        self.assertIs(meshflow.decode_token, decode_token)

    def test_all_contains_identity(self):
        for name in ("AgentIdentity", "AgentToken", "IdentityStore",
                     "sign_token", "verify_token", "decode_token"):
            self.assertIn(name, meshflow.__all__)

    def test_sprint56_exports_intact(self):
        for name in ("LineageNode", "LineageEdge", "LineageGraph"):
            self.assertTrue(hasattr(meshflow, name))


if __name__ == "__main__":
    unittest.main()
