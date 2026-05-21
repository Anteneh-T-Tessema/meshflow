"""L2.10 — Agent Identity: W3C DIDs + Verifiable Credentials.

Every agent gets a cryptographic DID at spawn. VCs encode capabilities.
Delegation enforces strict subsets — an agent cannot grant what it doesn't own.
CAEP triggers immediate revocation when risk score exceeds threshold.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)


@dataclass
class DIDDocument:
    did: str
    public_key_pem: str
    capabilities: list[str]
    created_at: str
    run_id: str
    agent_id: str
    revoked: bool = False
    revoked_at: str = ""
    revocation_reason: str = ""


@dataclass
class VerifiableCredential:
    """VC issued to an agent encoding a specific capability claim."""
    vc_id: str = field(default_factory=lambda: f"vc:{uuid.uuid4().hex}")
    issuer_did: str = ""
    subject_did: str = ""
    capability: str = ""
    scope: dict[str, Any] = field(default_factory=dict)
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = ""
    proof: str = ""                 # ECDSA signature hex
    revoked: bool = False


class AgentIdentityProvider:
    """Manages DID lifecycle for all agents in a run.

    Key decisions:
    - secp256k1 (same curve as Ethereum DIDs) for forward compatibility
    - JIT provisioning: DIDs minted at spawn, revoked at run completion
    - All DIDs are run-scoped — no cross-run identity leakage
    """

    REVOKE_THRESHOLD = 0.85   # risk score above which CAEP triggers

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._dids: dict[str, DIDDocument] = {}
        self._keys: dict[str, ec.EllipticCurvePrivateKey] = {}
        self._vcs: dict[str, list[VerifiableCredential]] = {}
        self._capability_registry: dict[str, set[str]] = {}

    # ── DID lifecycle ─────────────────────────────────────────────────────────

    def provision(self, agent_id: str, capabilities: list[str]) -> DIDDocument:
        """Mint a new DID for an agent — called once at agent spawn."""
        private_key = ec.generate_private_key(ec.SECP256K1())
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        did = f"did:meshflow:{self.run_id}:{agent_id}"
        doc = DIDDocument(
            did=did,
            public_key_pem=public_pem,
            capabilities=list(capabilities),
            created_at=datetime.now(timezone.utc).isoformat(),
            run_id=self.run_id,
            agent_id=agent_id,
        )
        self._dids[agent_id] = doc
        self._keys[agent_id] = private_key
        self._capability_registry[agent_id] = set(capabilities)
        self._vcs[agent_id] = []
        return doc

    def revoke(self, agent_id: str, reason: str = "run_complete") -> None:
        doc = self._dids.get(agent_id)
        if doc:
            doc.revoked = True
            doc.revoked_at = datetime.now(timezone.utc).isoformat()
            doc.revocation_reason = reason
            # Destroy private key material immediately
            self._keys.pop(agent_id, None)

    def revoke_all(self, reason: str = "run_complete") -> None:
        for agent_id in list(self._dids):
            self.revoke(agent_id, reason)

    def caep_check(self, agent_id: str, risk_score: float) -> bool:
        """Continuous Access Evaluation Profile — revoke if risk spikes."""
        if risk_score >= self.REVOKE_THRESHOLD:
            self.revoke(agent_id, reason=f"caep_risk_score:{risk_score:.3f}")
            return True   # was revoked
        return False

    def is_active(self, agent_id: str) -> bool:
        doc = self._dids.get(agent_id)
        return doc is not None and not doc.revoked

    # ── Verifiable Credentials ────────────────────────────────────────────────

    def issue_vc(
        self,
        issuer_id: str,
        subject_id: str,
        capability: str,
        scope: dict[str, Any] | None = None,
    ) -> VerifiableCredential:
        """Issue a VC — enforces capability subset rule on delegation."""
        issuer_caps = self._capability_registry.get(issuer_id, set())
        if capability not in issuer_caps:
            raise PermissionError(
                f"Agent '{issuer_id}' cannot delegate '{capability}' — not in its own capability set"
            )

        issuer_doc = self._dids[issuer_id]
        subject_doc = self._dids.get(subject_id)
        if not subject_doc or subject_doc.revoked:
            raise ValueError(f"Subject agent '{subject_id}' has no active DID")

        vc = VerifiableCredential(
            issuer_did=issuer_doc.did,
            subject_did=subject_doc.did,
            capability=capability,
            scope=scope or {},
        )

        # Sign the VC with issuer's private key
        private_key = self._keys.get(issuer_id)
        if private_key:
            payload = json.dumps({
                "vc_id": vc.vc_id,
                "issuer": vc.issuer_did,
                "subject": vc.subject_did,
                "capability": vc.capability,
                "issued_at": vc.issued_at,
            }, sort_keys=True).encode()
            signature = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
            vc.proof = signature.hex()

        self._vcs[subject_id].append(vc)
        return vc

    def verify_vc(self, subject_id: str, capability: str) -> bool:
        """Verify that an agent holds a valid VC for a capability."""
        vcs = self._vcs.get(subject_id, [])
        for vc in vcs:
            if vc.capability == capability and not vc.revoked:
                return True
        # Also check native capabilities
        return capability in self._capability_registry.get(subject_id, set())

    # ── Signatures ────────────────────────────────────────────────────────────

    def sign(self, agent_id: str, payload: bytes) -> str:
        """Sign arbitrary bytes with an agent's private key."""
        if not self.is_active(agent_id):
            raise PermissionError(f"Agent '{agent_id}' DID is revoked or not provisioned")
        key = self._keys[agent_id]
        sig = key.sign(payload, ec.ECDSA(hashes.SHA256()))
        return sig.hex()

    def verify_signature(self, agent_id: str, payload: bytes, signature_hex: str) -> bool:
        """Verify a signature using an agent's public key."""
        doc = self._dids.get(agent_id)
        if not doc or doc.revoked:
            return False
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            pub = load_pem_public_key(doc.public_key_pem.encode())
            pub.verify(bytes.fromhex(signature_hex), payload, ec.ECDSA(hashes.SHA256()))  # type: ignore[arg-type]
            return True
        except Exception:
            return False

    def get_did(self, agent_id: str) -> str:
        doc = self._dids.get(agent_id)
        return doc.did if doc else ""

    def audit_report(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_agents": len(self._dids),
            "active": sum(1 for d in self._dids.values() if not d.revoked),
            "revoked": sum(1 for d in self._dids.values() if d.revoked),
            "vcs_issued": sum(len(v) for v in self._vcs.values()),
        }
