"""DascGate — standalone, zero-dependency forensic gate."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from meshflow_forensic.timestamp import TimestampAnchor, TimestampClient
from meshflow_forensic.schemas import (
    ActionVerdict,
    CompensationPlan,
    ForensicPolicy,
    Intent,
    LedgerEntry,
    RiskTier,
)


class AutoRiskClassifier:
    """Overrides agent self-declared risk tiers — agents cannot lie about risk."""

    _TIER4 = {"delete","drop","destroy","purge","wipe","format","deploy","publish",
               "send_payment","transfer_funds","rm -rf","truncate","deactivate_account"}
    _TIER3 = {"write","update","create","insert","patch","post","upload","send",
               "email","notify","request"}
    _TIER2 = {"compute","transform","aggregate","cache","store_temp"}
    _SENSITIVE = {"password","secret","token","api_key","credential","ssn",
                  "credit_card","private_key","auth"}

    def __init__(self) -> None:
        self._failure_rates: dict[str, float] = {}

    def record_outcome(self, agent_id: str, success: bool) -> None:
        prev = self._failure_rates.get(agent_id, 0.0)
        self._failure_rates[agent_id] = 0.3 * (0.0 if success else 1.0) + 0.7 * prev

    def classify(self, intent: Intent) -> RiskTier:
        action = intent.action.lower()
        if any(kw in action for kw in self._TIER4):
            return RiskTier.IRREVERSIBLE
        if any(kw in action for kw in self._TIER3):
            if {k.lower() for k in intent.payload} & self._SENSITIVE:
                return RiskTier.IRREVERSIBLE
            return RiskTier.EXTERNAL_IO
        if self._failure_rates.get(intent.agent_id, 0.0) > 0.5:
            return RiskTier.EXTERNAL_IO
        if intent.tainted:
            return RiskTier.EXTERNAL_IO
        if any(kw in action for kw in self._TIER2):
            return RiskTier.INTERNAL
        return RiskTier.READ_ONLY


class TaintGraph:
    """IFC taint propagation across agents."""

    def __init__(self) -> None:
        self._tainted: set[str] = set()

    def mark_tainted(self, agent_id: str) -> None:
        self._tainted.add(agent_id)

    def is_tainted(self, agent_id: str) -> bool:
        return agent_id in self._tainted

    def propagate(self, source_id: str, target_id: str) -> bool:
        if source_id in self._tainted:
            self._tainted.add(target_id)
            return True
        return False

    def clear(self, agent_id: str) -> None:
        self._tainted.discard(agent_id)


class CompensationExecutor:
    """Executes rollback plans on REJECT."""

    async def execute(self, plan: CompensationPlan, reason: str = "") -> bool:
        if plan.rollback_fn:
            try:
                plan.rollback_fn()
                return True
            except Exception:
                return False
        return True


class AuditLedger:
    """SHA-256 hash-chained append-only ledger with RFC 3161 timestamp anchoring."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._last_hash = "genesis"
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                entry_id TEXT PRIMARY KEY, run_id TEXT, intent_id TEXT,
                agent_id TEXT, agent_did TEXT, action TEXT,
                effective_tier INTEGER, verdict TEXT, reason TEXT,
                timestamp TEXT, prev_hash TEXT, entry_hash TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS anchors (
                anchor_id TEXT PRIMARY KEY,
                chain_head_hash TEXT NOT NULL,
                tsa_url TEXT,
                anchored_at TEXT,
                tsr_base64 TEXT,
                nonce_hex TEXT,
                status INTEGER,
                verified INTEGER,
                error TEXT
            )
        """)
        self._conn.commit()

    def append(self, entry: LedgerEntry) -> None:
        entry.prev_hash = self._last_hash
        content = json.dumps({
            "entry_id": entry.entry_id, "run_id": entry.run_id,
            "intent_id": entry.intent_id, "action": entry.action,
            "verdict": str(entry.verdict), "timestamp": entry.timestamp.isoformat(),
            "prev_hash": entry.prev_hash,
        }, sort_keys=True)
        entry.entry_hash = hashlib.sha256(content.encode()).hexdigest()
        self._last_hash = entry.entry_hash
        self._conn.execute(
            "INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry.entry_id, entry.run_id, entry.intent_id, entry.agent_id,
             entry.agent_did, entry.action, entry.effective_tier,
             str(entry.verdict), entry.reason, entry.timestamp.isoformat(),
             entry.prev_hash, entry.entry_hash),
        )
        self._conn.commit()

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0])

    def verify_chain(self) -> bool:
        rows = self._conn.execute(
            "SELECT prev_hash, entry_hash FROM ledger ORDER BY rowid"
        ).fetchall()
        prev = "genesis"
        for row_prev, row_hash in rows:
            if row_prev != prev:
                return False
            prev = row_hash
        return True

    def anchor(
        self,
        tsa_url: str = "https://freetsa.org/tsr",
        timeout_s: int = 10,
    ) -> TimestampAnchor:
        """Request an RFC 3161 trusted timestamp for the current chain head.

        Sends the current ``entry_hash`` chain head to a public TSA and
        stores the raw ``TimeStampResp`` DER token in the ``anchors`` table.
        The stored TSR can be verified independently with::

            openssl ts -verify -in anchor.tsr -data <hash> -CAfile tsa.crt

        Parameters
        ----------
        tsa_url:
            TSA endpoint.  Default: FreeTSA (free, no account required).
        timeout_s:
            HTTP timeout in seconds.

        Returns
        -------
        TimestampAnchor
            The anchor.  ``anchor.verified`` is ``True`` when the TSA
            returned PKIStatus 0 (granted).  On network failure the anchor
            is stored with ``error`` populated and ``verified=False`` so the
            run can continue — the chain integrity is unaffected.
        """
        client = TimestampClient(tsa_url=tsa_url, timeout_s=timeout_s)
        anchor = client.stamp(self._last_hash)
        self._conn.execute(
            "INSERT OR REPLACE INTO anchors VALUES (?,?,?,?,?,?,?,?,?)",
            (
                anchor.anchor_id, anchor.chain_head_hash, anchor.tsa_url,
                anchor.anchored_at, anchor.tsr_base64, anchor.nonce_hex,
                anchor.status, int(anchor.verified), anchor.error,
            ),
        )
        self._conn.commit()
        return anchor

    def all_anchors(self) -> list[TimestampAnchor]:
        """Return all stored timestamp anchors ordered by insertion time."""
        cols = ["anchor_id", "chain_head_hash", "tsa_url", "anchored_at",
                "tsr_base64", "nonce_hex", "status", "verified", "error"]
        rows = self._conn.execute(
            "SELECT * FROM anchors ORDER BY rowid"
        ).fetchall()
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["verified"] = bool(d["verified"])
            result.append(TimestampAnchor(**d))
        return result

    def verify_with_timestamps(self) -> dict[str, Any]:
        """Verify both the hash chain and all RFC 3161 anchors.

        Returns
        -------
        dict
            ``{"chain_valid": bool, "anchors_checked": int,
               "anchors_verified": int, "broken_anchors": list[str]}``
        """
        chain_ok = self.verify_chain()
        anchors = self.all_anchors()
        client = TimestampClient()
        broken: list[str] = []
        verified_count = 0
        for a in anchors:
            ok = client.verify_anchor(a, a.chain_head_hash)
            if ok:
                verified_count += 1
            else:
                broken.append(a.anchor_id)
        return {
            "chain_valid": chain_ok,
            "anchors_checked": len(anchors),
            "anchors_verified": verified_count,
            "broken_anchors": broken,
        }

    def all_entries(self) -> list[dict[str, Any]]:
        cols = ["entry_id","run_id","intent_id","agent_id","agent_did","action",
                "effective_tier","verdict","reason","timestamp","prev_hash","entry_hash"]
        rows = self._conn.execute("SELECT * FROM ledger ORDER BY rowid").fetchall()
        return [dict(zip(cols, row)) for row in rows]


class DascGate:
    """Deterministic evaluation kernel — same input → same verdict."""

    def __init__(
        self,
        policy: ForensicPolicy | None = None,
        run_id: str = "default",
        db_path: str = ":memory:",
    ) -> None:
        self.policy = policy or ForensicPolicy()
        self.run_id = run_id
        self._classifier = AutoRiskClassifier()
        self._taint_graph = TaintGraph()
        self._ledger = AuditLedger(db_path)
        self._compensation = CompensationExecutor()

    @classmethod
    def create(
        cls,
        run_id: str = "default",
        db_path: str = ":memory:",
        **policy_kwargs: Any,
    ) -> "DascGate":
        return cls(ForensicPolicy(**policy_kwargs), run_id=run_id, db_path=db_path)

    async def evaluate(self, intent: Intent) -> ActionVerdict:
        effective = self._classifier.classify(intent)
        intent.effective_tier = effective
        if intent.tainted:
            self._taint_graph.mark_tainted(intent.agent_id)

        verdict = self._policy_eval(intent)

        entry = LedgerEntry(
            run_id=self.run_id, intent_id=intent.intent_id,
            agent_id=intent.agent_id, agent_did=intent.agent_did,
            action=intent.action, effective_tier=int(effective),
            verdict=verdict, reason=self._reason(intent, verdict),
            timestamp=datetime.now(timezone.utc),
        )
        self._ledger.append(entry)

        if verdict == ActionVerdict.REJECT and intent.compensation:
            await self._compensation.execute(intent.compensation)

        return verdict

    def _policy_eval(self, intent: Intent) -> ActionVerdict:
        tier = intent.effective_tier
        if tier in (RiskTier.READ_ONLY, RiskTier.INTERNAL):
            return ActionVerdict.COMMIT
        if tier == RiskTier.EXTERNAL_IO:
            return ActionVerdict.REJECT if intent.tainted else ActionVerdict.COMMIT
        if tier == RiskTier.IRREVERSIBLE:
            if self.policy.require_hitl_for_irreversible:
                return ActionVerdict.ESCALATE
            return ActionVerdict.REJECT if intent.tainted else ActionVerdict.COMMIT
        return ActionVerdict.REJECT

    def _reason(self, intent: Intent, verdict: ActionVerdict) -> str:
        parts = [f"tier={int(intent.effective_tier)}"]
        if intent.tainted:
            parts.append("tainted=true")
        parts.append(f"verdict={verdict}")
        return ", ".join(parts)

    def record_outcome(self, agent_id: str, success: bool) -> None:
        self._classifier.record_outcome(agent_id, success)

    def propagate_taint(self, source_id: str, target_id: str) -> None:
        self._taint_graph.propagate(source_id, target_id)

    def ledger_count(self) -> int:
        return self._ledger.count()

    def verify_ledger(self) -> bool:
        return self._ledger.verify_chain()

    def anchor(
        self,
        tsa_url: str = "https://freetsa.org/tsr",
        timeout_s: int = 10,
    ) -> TimestampAnchor:
        """Request an RFC 3161 trusted timestamp for the current chain head.

        Call this at the end of a run (or at any checkpoint) to anchor the
        ledger's hash chain to a trusted external clock.  The raw TSR token
        is stored in the ledger's ``anchors`` table and included in
        :meth:`ForensicReport.from_gate`.

        Parameters
        ----------
        tsa_url:
            TSA endpoint.  Default: FreeTSA (free, no account required).
        timeout_s:
            HTTP timeout.

        Returns
        -------
        TimestampAnchor
            ``anchor.verified`` is ``True`` when the TSA returned
            PKIStatus 0 (granted).
        """
        return self._ledger.anchor(tsa_url=tsa_url, timeout_s=timeout_s)

    def all_anchors(self) -> list[TimestampAnchor]:
        """Return all RFC 3161 timestamp anchors stored for this run."""
        return self._ledger.all_anchors()

    def verify_with_timestamps(self) -> dict[str, Any]:
        """Verify the hash chain and all RFC 3161 anchors.

        Returns
        -------
        dict
            ``{"chain_valid": bool, "anchors_checked": int,
               "anchors_verified": int, "broken_anchors": list[str]}``
        """
        return self._ledger.verify_with_timestamps()
