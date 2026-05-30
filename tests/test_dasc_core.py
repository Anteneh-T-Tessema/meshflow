"""Tests for DASC-core risk governance (dasc_gate.py)."""
import asyncio
import time
import unittest

import meshflow
from meshflow.core.schemas import (
    ActionVerdict, CompensationPlan, Evidence, Intent, LedgerEntry, RiskTier,
)
from meshflow.security.dasc_gate import (
    AuditLedger, AutoRiskClassifier, CompensationExecutor, DascGate, TaintGraph,
)
from meshflow.core.govern import Policy


def _intent(action: str, agent_id: str = "agent-1", tainted: bool = False,
            payload: dict | None = None, risk_tier: RiskTier = RiskTier.READ_ONLY) -> Intent:
    return Intent(
        action=action,
        payload=payload or {},
        evidence=[],
        agent_id=agent_id,
        risk_tier=risk_tier,
        tainted=tainted,
    )


# ── AutoRiskClassifier ────────────────────────────────────────────────────────

class TestAutoRiskClassifier(unittest.TestCase):

    def setUp(self):
        self.clf = AutoRiskClassifier()

    def test_read_only_default(self):
        tier = self.clf.classify(_intent("get user profile"))
        self.assertEqual(tier, RiskTier.READ_ONLY)

    def test_tier4_delete(self):
        self.assertEqual(self.clf.classify(_intent("delete record")), RiskTier.IRREVERSIBLE)

    def test_tier4_drop(self):
        self.assertEqual(self.clf.classify(_intent("drop table")), RiskTier.IRREVERSIBLE)

    def test_tier4_destroy(self):
        self.assertEqual(self.clf.classify(_intent("destroy container")), RiskTier.IRREVERSIBLE)

    def test_tier4_purge(self):
        self.assertEqual(self.clf.classify(_intent("purge cache")), RiskTier.IRREVERSIBLE)

    def test_tier4_deploy(self):
        self.assertEqual(self.clf.classify(_intent("deploy to production")), RiskTier.IRREVERSIBLE)

    def test_tier3_write(self):
        tier = self.clf.classify(_intent("write file"))
        self.assertEqual(tier, RiskTier.EXTERNAL_IO)

    def test_tier3_update(self):
        tier = self.clf.classify(_intent("update user record"))
        self.assertEqual(tier, RiskTier.EXTERNAL_IO)

    def test_tier3_create(self):
        tier = self.clf.classify(_intent("create new entry"))
        self.assertEqual(tier, RiskTier.EXTERNAL_IO)

    def test_tier2_compute(self):
        tier = self.clf.classify(_intent("compute aggregate stats"))
        self.assertEqual(tier, RiskTier.INTERNAL)

    def test_tier2_transform(self):
        tier = self.clf.classify(_intent("transform data"))
        self.assertEqual(tier, RiskTier.INTERNAL)

    def test_sensitive_payload_escalates_to_tier4(self):
        intent = _intent("write file", payload={"password": "s3cr3t"})
        tier = self.clf.classify(intent)
        self.assertEqual(tier, RiskTier.IRREVERSIBLE)

    def test_tainted_intent_escalates(self):
        tier = self.clf.classify(_intent("get data", tainted=True))
        self.assertGreaterEqual(int(tier), int(RiskTier.EXTERNAL_IO))

    def test_high_failure_rate_escalates(self):
        agent = "flaky-agent"
        for _ in range(10):
            self.clf.record_outcome(agent, success=False)
        tier = self.clf.classify(_intent("get data", agent_id=agent))
        self.assertGreaterEqual(int(tier), int(RiskTier.EXTERNAL_IO))

    def test_successful_outcomes_decrease_failure_rate(self):
        agent = "recovering-agent"
        for _ in range(5):
            self.clf.record_outcome(agent, success=False)
        for _ in range(20):
            self.clf.record_outcome(agent, success=True)
        tier = self.clf.classify(_intent("get data", agent_id=agent))
        self.assertEqual(tier, RiskTier.READ_ONLY)

    def test_case_insensitive(self):
        self.assertEqual(self.clf.classify(_intent("DELETE RECORD")), RiskTier.IRREVERSIBLE)
        self.assertEqual(self.clf.classify(_intent("WRITE FILE")), RiskTier.EXTERNAL_IO)


# ── TaintGraph ────────────────────────────────────────────────────────────────

class TestTaintGraph(unittest.TestCase):

    def setUp(self):
        self.graph = TaintGraph()

    def test_not_tainted_by_default(self):
        self.assertFalse(self.graph.is_tainted("agent-x"))

    def test_mark_tainted(self):
        self.graph.mark_tainted("agent-a")
        self.assertTrue(self.graph.is_tainted("agent-a"))

    def test_propagate_taint(self):
        self.graph.mark_tainted("agent-a")
        propagated = self.graph.propagate("agent-a", "agent-b")
        self.assertTrue(propagated)
        self.assertTrue(self.graph.is_tainted("agent-b"))

    def test_propagate_clean_does_not_taint(self):
        propagated = self.graph.propagate("clean-agent", "agent-c")
        self.assertFalse(propagated)
        self.assertFalse(self.graph.is_tainted("agent-c"))

    def test_clear_taint(self):
        self.graph.mark_tainted("agent-d")
        self.graph.clear("agent-d")
        self.assertFalse(self.graph.is_tainted("agent-d"))

    def test_taint_chain(self):
        self.graph.mark_tainted("root")
        self.graph.propagate("root", "mid")
        self.graph.propagate("mid", "leaf")
        self.assertTrue(self.graph.is_tainted("leaf"))

    def test_clear_mid_chain(self):
        self.graph.mark_tainted("root")
        self.graph.propagate("root", "mid")
        self.graph.clear("mid")
        self.assertFalse(self.graph.is_tainted("mid"))
        self.assertTrue(self.graph.is_tainted("root"))


# ── AuditLedger ───────────────────────────────────────────────────────────────

def _entry(action: str = "get_data", verdict: ActionVerdict = ActionVerdict.COMMIT,
           agent_id: str = "agent-1") -> LedgerEntry:
    from datetime import datetime
    return LedgerEntry(
        entry_id=f"entry-{time.time()}",
        run_id="run-1",
        intent_id="intent-1",
        agent_id=agent_id,
        agent_did="did:mesh:agent-1",
        action=action,
        effective_tier=RiskTier.READ_ONLY,
        verdict=verdict,
        reason="test",
        timestamp=datetime.utcnow(),
    )


class TestAuditLedger(unittest.TestCase):

    def setUp(self):
        self.ledger = AuditLedger(":memory:")

    def test_starts_empty(self):
        self.assertEqual(self.ledger.count(), 0)

    def test_append_increments_count(self):
        self.ledger.append(_entry())
        self.assertEqual(self.ledger.count(), 1)

    def test_multiple_entries(self):
        for _ in range(5):
            self.ledger.append(_entry())
        self.assertEqual(self.ledger.count(), 5)

    def test_verify_chain_empty(self):
        self.assertTrue(self.ledger.verify_chain())

    def test_verify_chain_valid(self):
        for _ in range(10):
            self.ledger.append(_entry())
        self.assertTrue(self.ledger.verify_chain())

    def test_verify_chain_tampered(self):
        for _ in range(5):
            self.ledger.append(_entry())
        self.ledger._conn.execute("UPDATE ledger SET prev_hash='tampered-hash' WHERE rowid=2")
        self.ledger._conn.commit()
        self.assertFalse(self.ledger.verify_chain())

    def test_hash_chaining(self):
        self.ledger.append(_entry("action-1"))
        self.ledger.append(_entry("action-2"))
        rows = self.ledger._conn.execute(
            "SELECT prev_hash, entry_hash FROM ledger ORDER BY rowid"
        ).fetchall()
        self.assertEqual(rows[0][0], "genesis")
        self.assertEqual(rows[1][0], rows[0][1])


# ── CompensationExecutor ──────────────────────────────────────────────────────

class TestCompensationExecutor(unittest.TestCase):

    def test_execute_empty_plan_returns_true(self):
        plan = CompensationPlan(steps=[], description="empty")
        ok = asyncio.run(CompensationExecutor().execute(plan, "nothing"))
        self.assertTrue(ok)

    def test_execute_with_rollback_fn(self):
        log = []
        plan = CompensationPlan(steps=[], rollback_fn=lambda: log.append("rolled-back"), description="")
        ok = asyncio.run(CompensationExecutor().execute(plan, "test"))
        self.assertTrue(ok)
        self.assertEqual(log, ["rolled-back"])

    def test_execute_rollback_fn_failure_returns_false(self):
        def bad_fn(): raise RuntimeError("rollback failed")
        plan = CompensationPlan(steps=[], rollback_fn=bad_fn, description="bad")
        ok = asyncio.run(CompensationExecutor().execute(plan, "test"))
        self.assertFalse(ok)

    def test_execute_no_rollback_fn_returns_true(self):
        plan = CompensationPlan(steps=["step1", "step2"], description="with steps")
        ok = asyncio.run(CompensationExecutor().execute(plan, "reason"))
        self.assertTrue(ok)


# ── DascGate ──────────────────────────────────────────────────────────────────

class TestDascGate(unittest.TestCase):

    def _gate(self, **kwargs):
        from meshflow.core.schemas import Policy, PolicyMode
        policy = Policy(mode=PolicyMode.LEGAL_CRITICAL)
        return DascGate(policy=policy, run_id="test-run", db_path=":memory:", **kwargs)

    def _evaluate(self, gate, intent):
        return asyncio.run(gate.evaluate(intent))

    def test_read_only_commits(self):
        gate = self._gate()
        verdict = self._evaluate(gate, _intent("get user profile"))
        self.assertEqual(verdict, ActionVerdict.COMMIT)

    def test_irreversible_with_hitl_escalates(self):
        from meshflow.core.schemas import Policy, PolicyMode, HumanInLoopConfig
        policy = Policy(mode=PolicyMode.LEGAL_CRITICAL,
                        human_in_loop=HumanInLoopConfig(enabled=True))
        gate = DascGate(policy=policy, run_id="test-run", db_path=":memory:")
        verdict = self._evaluate(gate, _intent("delete all records", risk_tier=RiskTier.IRREVERSIBLE))
        self.assertEqual(verdict, ActionVerdict.ESCALATE)

    def test_tainted_external_io_rejected(self):
        gate = self._gate()
        verdict = self._evaluate(gate, _intent("write file", tainted=True, risk_tier=RiskTier.EXTERNAL_IO))
        self.assertEqual(verdict, ActionVerdict.REJECT)

    def test_record_outcome_updates_classifier(self):
        gate = self._gate()
        gate.record_outcome("agent-x", success=False)
        gate.record_outcome("agent-x", success=True)

    def test_propagate_taint(self):
        gate = self._gate()
        gate._taint_graph.mark_tainted("agent-src")
        gate.propagate_taint("agent-src", "agent-dst")
        self.assertTrue(gate._taint_graph.is_tainted("agent-dst"))

    def test_ledger_count(self):
        gate = self._gate()
        self._evaluate(gate, _intent("read data"))
        self.assertGreaterEqual(gate.ledger_count(), 1)

    def test_verify_ledger(self):
        gate = self._gate()
        self._evaluate(gate, _intent("read data"))
        self.assertTrue(gate.verify_ledger())


# ── Public exports ────────────────────────────────────────────────────────────

class TestDascPublicExports(unittest.TestCase):

    def test_auto_risk_classifier_exported(self):
        self.assertTrue(hasattr(meshflow, "AutoRiskClassifier"))

    def test_taint_graph_exported(self):
        self.assertTrue(hasattr(meshflow, "TaintGraph"))

    def test_audit_ledger_exported(self):
        self.assertTrue(hasattr(meshflow, "AuditLedger"))

    def test_compensation_executor_exported(self):
        self.assertTrue(hasattr(meshflow, "CompensationExecutor"))

    def test_dasc_gate_exported(self):
        self.assertTrue(hasattr(meshflow, "DascGate"))

    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
