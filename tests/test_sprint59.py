"""Sprint 59 — Feature Flags tests."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import time
import unittest
from unittest.mock import patch

import meshflow
from meshflow.flags.store import (
    FlagDefinition,
    FlagRule,
    FlagStore,
    FlagEvaluator,
)


# ── FlagDefinition ────────────────────────────────────────────────────────────

class TestFlagDefinition(unittest.TestCase):
    def _make(self, enabled=True) -> FlagDefinition:
        return FlagDefinition(
            flag_id="f-1", name="my-flag", flag_type="bool",
            default_val=False, description="test", enabled=enabled,
            rollout_pct=100.0, created_at=time.time(),
        )

    def test_is_enabled_true(self):
        self.assertTrue(self._make(True).is_enabled)

    def test_is_enabled_false(self):
        self.assertFalse(self._make(False).is_enabled)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ("flag_id", "name", "flag_type", "default_val",
                   "description", "enabled", "rollout_pct", "created_at"):
            self.assertIn(k, d)

    def test_to_dict_values(self):
        d = self._make().to_dict()
        self.assertEqual(d["name"], "my-flag")
        self.assertFalse(d["default_val"])


# ── FlagRule ──────────────────────────────────────────────────────────────────

class TestFlagRule(unittest.TestCase):
    def _rule(self, op, cv, rv=True) -> FlagRule:
        return FlagRule("r-1", "f-1", 0, "agent_name", op, cv, rv, time.time())

    def test_matches_eq_true(self):
        self.assertTrue(self._rule("eq", "billing").matches({"agent_name": "billing"}))

    def test_matches_eq_false(self):
        self.assertFalse(self._rule("eq", "billing").matches({"agent_name": "search"}))

    def test_matches_neq_true(self):
        self.assertTrue(self._rule("neq", "billing").matches({"agent_name": "search"}))

    def test_matches_neq_false(self):
        self.assertFalse(self._rule("neq", "billing").matches({"agent_name": "billing"}))

    def test_matches_in_true(self):
        self.assertTrue(self._rule("in", "billing,search").matches({"agent_name": "billing"}))

    def test_matches_in_false(self):
        self.assertFalse(self._rule("in", "billing,search").matches({"agent_name": "audit"}))

    def test_matches_gt_true(self):
        rule = FlagRule("r-1", "f-1", 0, "score", "gt", 0.5, True, time.time())
        self.assertTrue(rule.matches({"score": 0.8}))

    def test_matches_gt_false(self):
        rule = FlagRule("r-1", "f-1", 0, "score", "gt", 0.5, True, time.time())
        self.assertFalse(rule.matches({"score": 0.3}))

    def test_matches_lt_true(self):
        rule = FlagRule("r-1", "f-1", 0, "score", "lt", 0.5, True, time.time())
        self.assertTrue(rule.matches({"score": 0.2}))

    def test_matches_gte_boundary(self):
        rule = FlagRule("r-1", "f-1", 0, "score", "gte", 0.5, True, time.time())
        self.assertTrue(rule.matches({"score": 0.5}))

    def test_matches_lte_boundary(self):
        rule = FlagRule("r-1", "f-1", 0, "score", "lte", 0.5, True, time.time())
        self.assertTrue(rule.matches({"score": 0.5}))

    def test_matches_contains_true(self):
        rule = FlagRule("r-1", "f-1", 0, "env", "contains", "prod", True, time.time())
        self.assertTrue(rule.matches({"env": "production"}))

    def test_matches_contains_false(self):
        rule = FlagRule("r-1", "f-1", 0, "env", "contains", "prod", True, time.time())
        self.assertFalse(rule.matches({"env": "staging"}))

    def test_matches_missing_key_false(self):
        self.assertFalse(self._rule("eq", "billing").matches({}))

    def test_to_dict_keys(self):
        d = self._rule("eq", "x").to_dict()
        for k in ("rule_id", "flag_id", "priority", "condition_key",
                   "condition_op", "condition_value", "return_value", "created_at"):
            self.assertIn(k, d)


# ── FlagStore ─────────────────────────────────────────────────────────────────

class TestFlagStore(unittest.TestCase):
    def setUp(self):
        self.store = FlagStore(":memory:")

    def _flag(self, name="test-flag", flag_type="bool", default_value=False, **kw):
        return self.store.define(name=name, flag_type=flag_type,
                                 default_value=default_value, **kw)

    def test_define_returns_definition(self):
        f = self._flag()
        self.assertIsInstance(f, FlagDefinition)

    def test_define_stores_name(self):
        self._flag("feature-x")
        self.assertIsNotNone(self.store.get_by_name("feature-x"))

    def test_define_default_enabled(self):
        f = self._flag()
        self.assertTrue(f.enabled)

    def test_define_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            self._flag(flag_type="invalid")

    def test_define_invalid_rollout_raises(self):
        with self.assertRaises(ValueError):
            self._flag(rollout_pct=150.0)

    def test_define_invalid_rollout_negative_raises(self):
        with self.assertRaises(ValueError):
            self._flag(rollout_pct=-1.0)

    def test_define_string_flag(self):
        f = self._flag("theme", flag_type="string", default_value="light")
        fetched = self.store.get(f.flag_id)
        self.assertEqual(fetched.default_val, "light")

    def test_define_number_flag(self):
        f = self._flag("timeout", flag_type="number", default_value=30.0)
        fetched = self.store.get(f.flag_id)
        self.assertAlmostEqual(fetched.default_val, 30.0)

    def test_get_known(self):
        f = self._flag()
        fetched = self.store.get(f.flag_id)
        self.assertEqual(fetched.flag_id, f.flag_id)

    def test_get_unknown_none(self):
        self.assertIsNone(self.store.get("no-such"))

    def test_get_by_name_known(self):
        self._flag("my-flag")
        f = self.store.get_by_name("my-flag")
        self.assertIsNotNone(f)
        self.assertEqual(f.name, "my-flag")

    def test_get_by_name_unknown_none(self):
        self.assertIsNone(self.store.get_by_name("unknown"))

    def test_list_flags_all(self):
        self._flag("f1")
        self._flag("f2")
        self.assertEqual(len(self.store.list_flags()), 2)

    def test_list_flags_enabled_only(self):
        f = self._flag("f1")
        self._flag("f2")
        self.store.disable(f.flag_id)
        active = self.store.list_flags(enabled_only=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].name, "f2")

    def test_enable_sets_flag(self):
        f = self._flag()
        self.store.disable(f.flag_id)
        self.store.enable(f.flag_id)
        self.assertTrue(self.store.get(f.flag_id).enabled)

    def test_disable_clears_flag(self):
        f = self._flag()
        self.store.disable(f.flag_id)
        self.assertFalse(self.store.get(f.flag_id).enabled)

    def test_enable_unknown_returns_false(self):
        self.assertFalse(self.store.enable("no-such"))

    def test_disable_unknown_returns_false(self):
        self.assertFalse(self.store.disable("no-such"))

    def test_set_rollout(self):
        f = self._flag()
        ok = self.store.set_rollout(f.flag_id, 50.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(self.store.get(f.flag_id).rollout_pct, 50.0)

    def test_set_rollout_invalid_raises(self):
        f = self._flag()
        with self.assertRaises(ValueError):
            self.store.set_rollout(f.flag_id, 200.0)

    def test_delete_known(self):
        f = self._flag()
        ok = self.store.delete(f.flag_id)
        self.assertTrue(ok)
        self.assertIsNone(self.store.get(f.flag_id))

    def test_delete_unknown_returns_false(self):
        self.assertFalse(self.store.delete("no-such"))

    def test_count_total(self):
        self._flag("f1")
        self._flag("f2")
        self.assertEqual(self.store.count(), 2)

    def test_count_enabled_only(self):
        f = self._flag("f1")
        self._flag("f2")
        self.store.disable(f.flag_id)
        self.assertEqual(self.store.count(enabled_only=True), 1)

    def test_add_rule_returns_rule(self):
        f = self._flag()
        r = self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.assertIsInstance(r, FlagRule)

    def test_add_rule_invalid_op_raises(self):
        f = self._flag()
        with self.assertRaises(ValueError):
            self.store.add_rule(f.flag_id, "env", "like", "prod", True)

    def test_add_rule_unknown_flag_raises(self):
        with self.assertRaises(ValueError):
            self.store.add_rule("no-such", "env", "eq", "prod", True)

    def test_list_rules_ordered_by_priority(self):
        f = self._flag()
        self.store.add_rule(f.flag_id, "env", "eq", "staging", True, priority=5)
        self.store.add_rule(f.flag_id, "env", "eq", "prod",    True, priority=10)
        rules = self.store.list_rules(f.flag_id)
        self.assertEqual(rules[0].priority, 10)
        self.assertEqual(rules[1].priority, 5)

    def test_delete_rule(self):
        f = self._flag()
        r = self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        ok = self.store.delete_rule(r.rule_id)
        self.assertTrue(ok)
        self.assertEqual(self.store.rule_count(f.flag_id), 0)

    def test_delete_rule_unknown_returns_false(self):
        self.assertFalse(self.store.delete_rule("no-such"))

    def test_rule_count(self):
        f = self._flag()
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.store.add_rule(f.flag_id, "env", "eq", "staging", True)
        self.assertEqual(self.store.rule_count(f.flag_id), 2)

    def test_delete_flag_cascades_rules(self):
        f = self._flag()
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.store.delete(f.flag_id)
        self.assertEqual(self.store.rule_count(f.flag_id), 0)


# ── FlagEvaluator ─────────────────────────────────────────────────────────────

class TestFlagEvaluator(unittest.TestCase):
    def setUp(self):
        self.store = FlagStore(":memory:")
        self.ev = FlagEvaluator(self.store)

    def test_evaluate_unknown_flag_raises(self):
        with self.assertRaises(KeyError):
            self.ev.evaluate("no-such")

    def test_evaluate_disabled_returns_default(self):
        f = self.store.define("feat", "bool", False)
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.store.disable(f.flag_id)
        self.assertFalse(self.ev.evaluate("feat", {"env": "prod"}))

    def test_evaluate_no_rules_returns_default(self):
        self.store.define("feat", "bool", False)
        self.assertFalse(self.ev.evaluate("feat", {}))

    def test_evaluate_rule_matches_returns_rule_value(self):
        f = self.store.define("feat", "bool", False)
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.assertTrue(self.ev.evaluate("feat", {"env": "prod"}))

    def test_evaluate_rule_no_match_returns_default(self):
        f = self.store.define("feat", "bool", False)
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.assertFalse(self.ev.evaluate("feat", {"env": "staging"}))

    def test_evaluate_priority_order(self):
        f = self.store.define("theme", "string", "light")
        self.store.add_rule(f.flag_id, "env", "eq", "prod", "dark", priority=5)
        self.store.add_rule(f.flag_id, "env", "eq", "prod", "high-contrast", priority=10)
        result = self.ev.evaluate("theme", {"env": "prod"})
        self.assertEqual(result, "high-contrast")

    def test_evaluate_number_flag(self):
        f = self.store.define("timeout", "number", 30.0)
        self.store.add_rule(f.flag_id, "tier", "eq", "premium", 60.0)
        result = self.ev.evaluate("timeout", {"tier": "premium"})
        self.assertAlmostEqual(result, 60.0)

    def test_evaluate_string_flag(self):
        f = self.store.define("color", "string", "blue")
        self.store.add_rule(f.flag_id, "agent_name", "eq", "billing", "green")
        result = self.ev.evaluate("color", {"agent_name": "billing"})
        self.assertEqual(result, "green")

    def test_evaluate_all_returns_dict(self):
        self.store.define("f1", "bool", False)
        self.store.define("f2", "bool", True)
        result = self.ev.evaluate_all({})
        self.assertIn("f1", result)
        self.assertIn("f2", result)

    def test_is_enabled_true(self):
        f = self.store.define("feat", "bool", False)
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.assertTrue(self.ev.is_enabled("feat", {"env": "prod"}))

    def test_is_enabled_false(self):
        self.store.define("feat", "bool", False)
        self.assertFalse(self.ev.is_enabled("feat", {}))

    def test_rollout_100_pct_always_evaluates(self):
        f = self.store.define("feat", "bool", False, rollout_pct=100.0)
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.assertTrue(self.ev.evaluate("feat", {"env": "prod"}))

    def test_rollout_0_pct_always_default(self):
        f = self.store.define("feat", "bool", False, rollout_pct=0.0)
        self.store.add_rule(f.flag_id, "env", "eq", "prod", True)
        self.assertFalse(self.ev.evaluate("feat", {"env": "prod"}))

    def test_evaluate_empty_context(self):
        self.store.define("feat", "bool", True)
        self.assertTrue(self.ev.evaluate("feat"))

    def test_evaluate_in_operator(self):
        f = self.store.define("feat", "bool", False)
        self.store.add_rule(f.flag_id, "agent_name", "in", "billing,search,audit", True)
        self.assertTrue(self.ev.evaluate("feat", {"agent_name": "search"}))
        self.assertFalse(self.ev.evaluate("feat", {"agent_name": "unknown"}))


# ── CLI tests ─────────────────────────────────────────────────────────────────

def _args(cmd, **kw):
    ns = argparse.Namespace(flags_cmd=cmd, db=":memory:", json_output=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestFlagsCLI(unittest.TestCase):
    def test_define_prints_id(self):
        from meshflow.cli.main import _cmd_flags
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_flags(_args(
                "define", name="my-flag", flag_type="bool",
                default_value="false", description="", rollout_pct=100.0,
            ))
        self.assertIn("defined", out.getvalue())

    def test_list_empty(self):
        from meshflow.cli.main import _cmd_flags
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_flags(_args("list", enabled_only=False))
        self.assertIn("No feature flags", out.getvalue())

    def test_list_json(self):
        from meshflow.cli.main import _cmd_flags
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_flags(_args("list", enabled_only=False, json_output=True))
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)

    def test_get_unknown_exits(self):
        from meshflow.cli.main import _cmd_flags
        with self.assertRaises(SystemExit):
            _cmd_flags(_args("get", name="no-such"))

    def test_enable_unknown_exits(self):
        from meshflow.cli.main import _cmd_flags
        with self.assertRaises(SystemExit):
            _cmd_flags(_args("enable", name="no-such"))

    def test_disable_unknown_exits(self):
        from meshflow.cli.main import _cmd_flags
        with self.assertRaises(SystemExit):
            _cmd_flags(_args("disable", name="no-such"))

    def test_delete_unknown_exits(self):
        from meshflow.cli.main import _cmd_flags
        with self.assertRaises(SystemExit):
            _cmd_flags(_args("delete", name="no-such"))

    def test_evaluate_unknown_exits(self):
        from meshflow.cli.main import _cmd_flags
        with self.assertRaises(SystemExit):
            _cmd_flags(_args("evaluate", name="no-such", context="{}"))

    def test_evaluate_prints_value(self):
        from meshflow.cli.main import _cmd_flags
        from meshflow.flags.store import FlagStore

        store = FlagStore(":memory:")
        store.define("my-flag", "bool", False)

        with patch("meshflow.flags.store.FlagStore", return_value=store):
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                _cmd_flags(_args("evaluate", name="my-flag", context="{}"))
        self.assertIn("my-flag", out.getvalue())

    def test_add_rule_unknown_flag_exits(self):
        from meshflow.cli.main import _cmd_flags
        with self.assertRaises(SystemExit):
            _cmd_flags(_args(
                "add-rule", name="no-such",
                condition_key="env", condition_op="eq",
                condition_value="prod", return_value="true", priority=0,
            ))


# ── Subprocess ────────────────────────────────────────────────────────────────

class TestSubprocessHelp(unittest.TestCase):
    def test_flags_help(self):
        r = subprocess.run(
            ["meshflow", "flags", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertIn(r.returncode, (0, 1))

    def test_flags_define_help(self):
        r = subprocess.run(
            ["meshflow", "flags", "define", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertIn(r.returncode, (0, 1))


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports(unittest.TestCase):
    def test_version(self):
        self.assertEqual(meshflow.__version__, "0.65.0")

    def test_flag_definition_exported(self):
        self.assertIs(meshflow.FlagDefinition, FlagDefinition)

    def test_flag_rule_exported(self):
        self.assertIs(meshflow.FlagRule, FlagRule)

    def test_flag_store_exported(self):
        self.assertIs(meshflow.FlagStore, FlagStore)

    def test_flag_evaluator_exported(self):
        self.assertIs(meshflow.FlagEvaluator, FlagEvaluator)

    def test_all_contains_flags(self):
        for name in ("FlagDefinition", "FlagRule", "FlagStore", "FlagEvaluator"):
            self.assertIn(name, meshflow.__all__)

    def test_sprint58_exports_intact(self):
        for name in ("CanaryConfig", "CanaryStore", "CanaryRouter"):
            self.assertTrue(hasattr(meshflow, name), f"missing: {name}")

    def test_sprint57_exports_intact(self):
        for name in ("AgentIdentity", "IdentityStore", "sign_token"):
            self.assertTrue(hasattr(meshflow, name), f"missing: {name}")


if __name__ == "__main__":
    unittest.main()
