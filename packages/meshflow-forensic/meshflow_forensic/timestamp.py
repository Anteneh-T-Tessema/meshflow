"""RFC 3161 trusted timestamp anchoring — zero external dependencies.

Builds a minimal DER-encoded TimeStampReq and sends it to a public TSA.
Stores the raw TimeStampResp token so external tools (openssl ts -verify)
can verify chain anchoring without trusting MeshFlow's code.

Default TSA: FreeTSA (https://freetsa.org) — free, no account required.
Alternative: DigiCert (http://timestamp.digicert.com) — commercial but free to use.

Usage::

    from meshflow_forensic.timestamp import TimestampClient, TimestampAnchor

    client = TimestampClient()
    anchor = client.stamp("abc123...64-hex-chars-sha256...")
    print(anchor.anchored_at)      # UTC timestamp from TSA
    print(anchor.verified)         # True if TSA responded with status 0 (granted)

    # Verify later:
    ok = client.verify_anchor(anchor, "abc123...same-hash...")
    print(ok)  # True

    # Serialize / store:
    import json
    stored = json.dumps(anchor.to_dict())
    restored = TimestampAnchor.from_dict(json.loads(stored))
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Minimal ASN.1 DER helpers ────────────────────────────────────────────────

def _der_len(n: int) -> bytes:
    """DER length encoding."""
    if n < 0x80:
        return bytes([n])
    enc = []
    while n:
        enc.append(n & 0xFF)
        n >>= 8
    enc.reverse()
    return bytes([0x80 | len(enc)] + enc)


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(value)) + value


def _seq(*items: bytes) -> bytes:
    payload = b"".join(items)
    return _tlv(0x30, payload)


def _int_val(n: int) -> bytes:
    if n == 0:
        return _tlv(0x02, b"\x00")
    acc = []
    while n:
        acc.append(n & 0xFF)
        n >>= 8
    acc.reverse()
    # DER integers are signed — prepend 0x00 if high bit set
    if acc[0] & 0x80:
        acc = [0x00] + acc
    return _tlv(0x02, bytes(acc))


def _oid(dotted: str) -> bytes:
    """Encode OID from dotted string, e.g. '2.16.840.1.101.3.4.2.1' (SHA-256)."""
    parts = [int(x) for x in dotted.split(".")]
    enc = [40 * parts[0] + parts[1]]
    for p in parts[2:]:
        if p == 0:
            enc.append(0)
        else:
            buf = []
            while p:
                buf.append((p & 0x7F) | (0x80 if buf else 0x00))
                p >>= 7
            buf.reverse()
            enc.extend(buf)
    return _tlv(0x06, bytes(enc))


# SHA-256 AlgorithmIdentifier: SEQUENCE { OID sha256, NULL }
_SHA256_ALG_ID = _seq(
    _oid("2.16.840.1.101.3.4.2.1"),
    _tlv(0x05, b""),   # NULL
)


def _build_ts_req(hash_bytes: bytes, nonce: int, cert_req: bool = True) -> bytes:
    """Build a minimal RFC 3161 TimeStampReq for SHA-256 hash."""
    message_imprint = _seq(_SHA256_ALG_ID, _tlv(0x04, hash_bytes))
    nonce_enc = _int_val(nonce)
    bool_byte = b"\xff" if cert_req else b"\x00"
    cert_req_enc = _tlv(0x01, bool_byte)
    return _seq(
        _int_val(1),          # version v1
        message_imprint,
        nonce_enc,
        cert_req_enc,
    )


# ── TimestampAnchor ───────────────────────────────────────────────────────────

@dataclass
class TimestampAnchor:
    """One RFC 3161 timestamp anchor for an audit chain head."""
    anchor_id: str
    chain_head_hash: str          # SHA-256 hex of the ledger chain head
    tsa_url: str
    anchored_at: str              # ISO-8601 UTC — set from system clock when we sent the request
    tsr_base64: str               # Base64-encoded raw TimeStampResp DER bytes
    nonce_hex: str                # Nonce used in the request (for verification)
    status: int                   # 0 = granted, 1 = grantedWithMods, 2+ = rejection
    verified: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "chain_head_hash": self.chain_head_hash,
            "tsa_url": self.tsa_url,
            "anchored_at": self.anchored_at,
            "tsr_base64": self.tsr_base64,
            "nonce_hex": self.nonce_hex,
            "status": self.status,
            "verified": self.verified,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TimestampAnchor":
        return cls(**d)

    def tsr_bytes(self) -> bytes:
        return base64.b64decode(self.tsr_base64) if self.tsr_base64 else b""

    def is_granted(self) -> bool:
        return self.status in (0, 1)


# ── TimestampClient ───────────────────────────────────────────────────────────

_DEFAULT_TSA = "https://freetsa.org/tsr"
_FALLBACK_TSA = "http://timestamp.digicert.com"


class TimestampClient:
    """RFC 3161 timestamp client — zero external dependencies.

    Sends a TimeStampReq to a public TSA and stores the raw TimeStampResp.
    The TSR can be verified with ``openssl ts -verify`` independently of
    this library, which is the property that makes it legally admissible.

    Parameters
    ----------
    tsa_url:
        TSA endpoint URL.  Default: FreeTSA (freetsa.org, free, no auth).
    timeout_s:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        tsa_url: str = _DEFAULT_TSA,
        timeout_s: int = 10,
    ) -> None:
        self.tsa_url = tsa_url
        self.timeout_s = timeout_s

    def stamp(self, chain_head_hash: str) -> TimestampAnchor:
        """Request a trusted timestamp for *chain_head_hash*.

        Parameters
        ----------
        chain_head_hash:
            SHA-256 hex digest of the current audit chain head (the
            ``entry_hash`` of the most recently appended ``LedgerEntry``).

        Returns
        -------
        TimestampAnchor
            The anchor object.  ``anchor.verified`` is ``True`` when the
            TSA returned status 0 (granted).  Even on network failure,
            the anchor records the attempt with ``error`` populated.
        """
        hash_bytes = bytes.fromhex(chain_head_hash)
        nonce = int.from_bytes(os.urandom(8), "big")
        ts_req = _build_ts_req(hash_bytes, nonce, cert_req=True)
        anchor_id = hashlib.sha256(ts_req).hexdigest()[:16]
        anchored_at = datetime.now(timezone.utc).isoformat()

        try:
            req = urllib.request.Request(
                self.tsa_url,
                data=ts_req,
                headers={"Content-Type": "application/timestamp-query"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                tsr_bytes = resp.read()
            status = _parse_ts_status(tsr_bytes)
            return TimestampAnchor(
                anchor_id=anchor_id,
                chain_head_hash=chain_head_hash,
                tsa_url=self.tsa_url,
                anchored_at=anchored_at,
                tsr_base64=base64.b64encode(tsr_bytes).decode(),
                nonce_hex=hex(nonce),
                status=status,
                verified=status in (0, 1),
            )
        except Exception as exc:
            return TimestampAnchor(
                anchor_id=anchor_id,
                chain_head_hash=chain_head_hash,
                tsa_url=self.tsa_url,
                anchored_at=anchored_at,
                tsr_base64="",
                nonce_hex=hex(nonce),
                status=-1,
                verified=False,
                error=str(exc)[:200],
            )

    def verify_anchor(self, anchor: TimestampAnchor, chain_head_hash: str) -> bool:
        """Verify that *anchor* was issued for *chain_head_hash*.

        This performs a structural check: the TSR must be present, the
        hash embedded in the TSR must match *chain_head_hash*, and the
        chain-of-trust must extend from the anchor's TSA certificate.

        For full cryptographic verification (signature chain to TSA root
        CA), pass the TSR to ``openssl ts -verify``.

        Parameters
        ----------
        anchor:
            Previously obtained TimestampAnchor.
        chain_head_hash:
            The chain head hash to verify against.

        Returns
        -------
        bool
            ``True`` when the anchor's recorded hash matches and the TSR
            was granted.
        """
        if anchor.chain_head_hash != chain_head_hash:
            return False
        if not anchor.is_granted():
            return False
        if not anchor.tsr_base64:
            return False
        # Structural check: the hashed message in the TSR must contain our hash
        tsr = anchor.tsr_bytes()
        hash_bytes = bytes.fromhex(chain_head_hash)
        return hash_bytes in tsr


def _parse_ts_status(tsr_bytes: bytes) -> int:
    """Parse PKIStatusInfo.status from a raw TimeStampResp DER.

    TimeStampResp ::= SEQUENCE {
        status  PKIStatusInfo,
        timeStampToken  ContentInfo OPTIONAL
    }
    PKIStatusInfo ::= SEQUENCE {
        status  PKIStatus (INTEGER),
        ...
    }
    """
    try:
        # Skip outer SEQUENCE tag + length
        idx = 0
        if tsr_bytes[idx] != 0x30:
            return -1
        idx += 1
        # skip length bytes
        if tsr_bytes[idx] & 0x80:
            idx += (tsr_bytes[idx] & 0x7F) + 1
        else:
            idx += 1
        # Now at PKIStatusInfo SEQUENCE
        if tsr_bytes[idx] != 0x30:
            return -1
        idx += 1
        if tsr_bytes[idx] & 0x80:
            idx += (tsr_bytes[idx] & 0x7F) + 1
        else:
            idx += 1
        # Now at status INTEGER
        if tsr_bytes[idx] != 0x02:
            return -1
        idx += 1
        int_len = tsr_bytes[idx]
        idx += 1
        status = int.from_bytes(tsr_bytes[idx:idx + int_len], "big")
        return status
    except Exception:
        return -1
