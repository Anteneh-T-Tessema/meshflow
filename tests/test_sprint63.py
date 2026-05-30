"""Sprint 63 — Policy-as-Code Engine tests."""
import subprocess
import sys
import unittest

import meshflow
from meshflow.policy.engine import (
    ConditionOp, PolicyAction, PolicyCondition, PolicyDecision,
    PolicyEngine, PolicyLoader, PolicyRule, PolicyStore,
)


# ── PolicyCondition ───────────────────────────────────────────────────────────

class TestPolicyCondition(unittest.TestCase):

    def _cond(self, field, op, value):
        return PolicyCondition(field=field, op=ConditionOp(op), value=value)

    def test_eq_true(self):
        self.assertTrue(self._cond("role", "eq", "admin").evaluate({"role": "admin"}))

    def test_eq_false(self):
        self.assertFalse(self._cond("role", "eq", "admin").evaluate({"role": "user"}))

    def test_neq_true(self):
        self.assertTrue(self._cond("env", "neq", "prod").evaluate({"env": "dev"}))

    def test_neq_false(self):
        self.assertFalse(self._cond("env", "neq", "prod").evaluate({"env": "prod"}))

    def test_in_true(self):
        self.assertTrue(self._cond("region", "in", "us-east-1,eu-west-1").evaluate({"region": "us-east-1"}))

    def test_in_false(self):
        self.assertFalse(self._cond("region", "in", "us-east-1,eu-west-1").evaluate({"region": "ap-south-1"}))

    def test_not_in_true(self):
        self.assertTrue(self._cond("env", "not_in", "prod,staging").evaluate({"env": "dev"}))

    def test_not_in_false(self):
        self.assertFalse(self._cond("env", "not_in", "prod,staging").evaluate({"env": "prod"}))

    def test_gt_true(self):
        self.assertTrue(self._cond("age", "gt", "18").evaluate({"age": "21"}))

    def test_gt_false(self):
        self.assertFalse(self._cond("age", "gt", "18").evaluate({"age": "10"}))

    def test_lt_true(self):
        self.assertTrue(self._cond("score", "lt", "100").evaluate({"score": "50"}))

    def test_gte_true(self):
        self.assertTrue(self._cond("x", "gte", "5").evaluate({"x": "5"}))

    def test_lte_true(self):
        self.assertTrue(self._cond("x", "lte", "5").evaluate({"x": "5"}))

    def test_contains_true(self):
        self.assertTrue(self._cond("msg", "contains", "error").evaluate({"msg": "runtime error occurred"}))

    def test_contains_false(self):
        self.assertFalse(self._cond("msg", "contains", "fatal").evaluate({"msg": "all good"}))

    def test_exists_true(self):
        self.assertTrue(self._cond("user_id", "exists", None).evaluate({"user_id": "123"}))

    def test_exists_false(self):
        self.assertFalse(self._cond("user_id", "exists", None).evaluate({}))

    def test_missing_field_returns_false(self):
        self.assertFalse(self._cond("x", "eq", "5").evaluate({}))

    def test_type_coercion_numeric(self):
        self.assertTrue(self._cond("count", "gt", "0").evaluate({"count": "1"}))

    def test_to_dict(self):
        c = self._cond("role", "eq", "admin")
        d = c.to_dict()
        self.assertEqual(d["field"], "role")
        self.assertEqual(d["op"], "eq")


# ── PolicyRule ────────────────────────────────────────────────────────────────

class TestPolicyRule(unittest.TestCase):

    def _rule(self, conditions, action=PolicyAction.DENY):
        import uuid, time
        return PolicyRule(
            rule_id=str(uuid.uuid4()),
            name="test-rule",
            action=action,
            conditions=[PolicyCondition(f, ConditionOp(op), v) for f, op, v in conditions],
        )

    def test_empty_conditions_matches_all(self):
        rule = self._rule([])
        self.assertTrue(rule.matches({}))
        self.assertTrue(rule.matches({"anything": "value"}))

    def test_single_condition_match(self):
        rule = self._rule([("role", "eq", "admin")])
        self.assertTrue(rule.matches({"role": "admin"}))
        self.assertFalse(rule.matches({"role": "user"}))

    def test_and_logic_all_must_match(self):
        rule = self._rule([("role", "eq", "admin"), ("env", "eq", "prod")])
        self.assertTrue(rule.matches({"role": "admin", "env": "prod"}))
        self.assertFalse(rule.matches({"role": "admin", "env": "dev"}))
        self.assertFalse(rule.matches({"role": "user", "env": "prod"}))

    def test_to_dict(self):
        rule = self._rule([("x", "eq", "1")])
        d = rule.to_dict()
        for key in ("rule_id", "name", "action", "conditions", "framework", "priority", "enabled"):
            self.assertIn(key, d)


# ── PolicyStore ───────────────────────────────────────────────────────────────

class TestPolicyStore(unittest.TestCase):

    def setUp(self):
        self.store = PolicyStore(":memory:")

    def _rule(self, name="r", action=PolicyAction.DENY, framework="custom", priority=0):
        import uuid, time
        return PolicyRule(
            rule_id=str(uuid.uuid4()),
            name=name,
            action=action,
            conditions=[],
            framework=framework,
            priority=priority,
        )

    def test_add_and_get(self):
        r = self._rule("my-rule")
        self.store.add_rule(r)
        fetched = self.store.get_rule(r.rule_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "my-rule")

    def test_get_by_name(self):
        r = self._rule("named-rule")
        self.store.add_rule(r)
        fetched = self.store.get_by_name("named-rule")
        self.assertIsNotNone(fetched)

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.store.get_rule("no-such-id"))

    def test_list_rules(self):
        self.store.add_rule(self._rule("r1"))
        self.store.add_rule(self._rule("r2"))
        rules = self.store.list_rules()
        self.assertEqual(len(rules), 2)

    def test_list_by_framework(self):
        self.store.add_rule(self._rule("h1", framework="hipaa"))
        self.store.add_rule(self._rule("s1", framework="sox"))
        hipaa = self.store.list_rules(framework="hipaa")
        self.assertEqual(len(hipaa), 1)
        self.assertEqual(hipaa[0].name, "h1")

    def test_enable_disable(self):
        r = self._rule("toggle")
        self.store.add_rule(r)
        self.store.disable_rule(r.rule_id)
        fetched = self.store.get_rule(r.rule_id)
        self.assertFalse(fetched.enabled)
        self.store.enable_rule(r.rule_id)
        fetched = self.store.get_rule(r.rule_id)
        self.assertTrue(fetched.enabled)

    def test_list_enabled_only(self):
        r1 = self._rule("e1"); self.store.add_rule(r1)
        r2 = self._rule("e2"); self.store.add_rule(r2)
        self.store.disable_rule(r2.rule_id)
        enabled = self.store.list_rules(enabled_only=True)
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].name, "e1")

    def test_delete_rule(self):
        r = self._rule("deletable")
        self.store.add_rule(r)
        self.store.delete_rule(r.rule_id)
        self.assertIsNone(self.store.get_rule(r.rule_id))

    def test_count(self):
        self.assertEqual(self.store.count(), 0)
        self.store.add_rule(self._rule("x"))
        self.assertEqual(self.store.count(), 1)

    def test_count_enabled_only(self):
        r1 = self._rule("y"); self.store.add_rule(r1)
        r2 = self._rule("z"); self.store.add_rule(r2)
        self.store.disable_rule(r2.rule_id)
        self.assertEqual(self.store.count(enabled_only=True), 1)


# ── PolicyEngine ──────────────────────────────────────────────────────────────

class TestPolicyEngine(unittest.TestCase):

    def setUp(self):
        self.store = PolicyStore(":memory:")
        self.engine = PolicyEngine(self.store, audit=False)

    def test_no_rules_default_allow(self):
        decision = self.engine.evaluate({"role": "user"})
        self.assertEqual(decision.action, PolicyAction.ALLOW)
        self.assertFalse(decision.matched)

    def test_deny_rule_blocks(self):
        self.engine.add_rule("block-admin", PolicyAction.DENY,
                             [("role", "eq", "admin")])
        decision = self.engine.evaluate({"role": "admin"})
        self.assertEqual(decision.action, PolicyAction.DENY)
        self.assertTrue(decision.matched)

    def test_allow_rule_passes(self):
        self.engine.add_rule("allow-user", PolicyAction.ALLOW,
                             [("role", "eq", "user")])
        decision = self.engine.evaluate({"role": "user"})
        self.assertEqual(decision.action, PolicyAction.ALLOW)

    def test_deny_wins_over_allow(self):
        self.engine.add_rule("deny-role", PolicyAction.DENY,
                             [("role", "eq", "bad")], priority=10)
        self.engine.add_rule("allow-role", PolicyAction.ALLOW,
                             [("role", "eq", "bad")], priority=0)
        decision = self.engine.evaluate({"role": "bad"})
        self.assertEqual(decision.action, PolicyAction.DENY)

    def test_framework_filter(self):
        self.engine.add_rule("hipaa-deny", PolicyAction.DENY,
                             [("phi", "eq", "true")], framework="hipaa")
        self.engine.add_rule("sox-deny", PolicyAction.DENY,
                             [("financial", "eq", "true")], framework="sox")
        # Only evaluate HIPAA rules
        d = self.engine.evaluate({"financial": "true"}, framework="hipaa")
        self.assertEqual(d.action, PolicyAction.ALLOW)  # sox rule not in scope
        d2 = self.engine.evaluate({"financial": "true"}, framework="sox")
        self.assertEqual(d2.action, PolicyAction.DENY)

    def test_is_allowed(self):
        self.assertTrue(self.engine.is_allowed({"role": "user"}))
        self.engine.add_rule("block-all", PolicyAction.DENY, [])
        self.assertFalse(self.engine.is_allowed({"role": "user"}))

    def test_decision_reason(self):
        self.engine.add_rule("reason-rule", PolicyAction.DENY, [])
        d = self.engine.evaluate({})
        self.assertIn("reason-rule", d.reason)

    def test_log_rule_action(self):
        self.engine.add_rule("log-rule", PolicyAction.LOG,
                             [("event", "eq", "login")])
        d = self.engine.evaluate({"event": "login"})
        self.assertEqual(d.action, PolicyAction.LOG)

    def test_alert_rule_action(self):
        self.engine.add_rule("alert-rule", PolicyAction.ALERT,
                             [("severity", "eq", "critical")])
        d = self.engine.evaluate({"severity": "critical"})
        self.assertEqual(d.action, PolicyAction.ALERT)


# ── PolicyLoader ──────────────────────────────────────────────────────────────

class TestPolicyLoader(unittest.TestCase):

    def setUp(self):
        self.store = PolicyStore(":memory:")

    def test_from_dict_basic(self):
        data = {
            "rules": [
                {
                    "name": "dict-rule",
                    "action": "deny",
                    "framework": "hipaa",
                    "conditions": [{"field": "phi", "op": "eq", "value": "true"}],
                }
            ]
        }
        rules = PolicyLoader.from_dict(data, self.store)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].name, "dict-rule")

    def test_from_dict_empty(self):
        rules = PolicyLoader.from_dict({}, self.store)
        self.assertEqual(rules, [])

    def test_from_yaml_string(self):
        yaml_str = """
rules:
  - name: yaml-rule
    action: allow
    conditions:
      - field: role
        op: eq
        value: admin
"""
        try:
            rules = PolicyLoader.from_yaml(yaml_str, self.store)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].action, PolicyAction.ALLOW)
        except ImportError:
            self.skipTest("pyyaml not installed")

    def test_loaded_rules_evaluatable(self):
        data = {
            "rules": [
                {"name": "block", "action": "deny",
                 "conditions": [{"field": "env", "op": "eq", "value": "prod"}]}
            ]
        }
        PolicyLoader.from_dict(data, self.store)
        engine = PolicyEngine(self.store, audit=False)
        d = engine.evaluate({"env": "prod"})
        self.assertEqual(d.action, PolicyAction.DENY)


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestPolicyCLI(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run(
            ["meshflow", *args],
            capture_output=True, text=True,
        )

    def test_policy_add_cli(self):
        result = self._run("policy", "add", "test-rule",
                           "--action", "deny",
                           "--condition", "role:eq:admin",
                           "--db", ":memory:")
        self.assertEqual(result.returncode, 0)
        self.assertIn("test-rule", result.stdout)

    def test_policy_list_cli(self):
        result = self._run("policy", "list", "--db", ":memory:")
        self.assertEqual(result.returncode, 0)

    def test_policy_evaluate_cli(self):
        result = self._run("policy", "evaluate",
                           "--context", '{"role": "user"}',
                           "--db", ":memory:")
        self.assertEqual(result.returncode, 0)
        self.assertIn("allow", result.stdout.lower())


# ── Public exports ────────────────────────────────────────────────────────────

class TestPolicyExports(unittest.TestCase):

    def test_policy_action_exported(self):
        self.assertTrue(hasattr(meshflow, "PolicyAction"))

    def test_condition_op_exported(self):
        self.assertTrue(hasattr(meshflow, "ConditionOp"))

    def test_policy_condition_exported(self):
        self.assertTrue(hasattr(meshflow, "PolicyCondition"))

    def test_policy_rule_exported(self):
        self.assertTrue(hasattr(meshflow, "PolicyRule"))

    def test_policy_decision_exported(self):
        self.assertTrue(hasattr(meshflow, "PolicyDecision"))

    def test_policy_store_exported(self):
        self.assertTrue(hasattr(meshflow, "PolicyStore"))

    def test_policy_engine_exported(self):
        self.assertTrue(hasattr(meshflow, "PolicyEngine"))

    def test_policy_loader_exported(self):
        self.assertTrue(hasattr(meshflow, "PolicyLoader"))

    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")


if __name__ == "__main__":
    unittest.main()
