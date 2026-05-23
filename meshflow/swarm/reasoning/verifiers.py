"""Verifiers for general reasoning task domains."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from meshflow.swarm.verifiers import DeterministicVerifier, VerificationResult


class LinearSystemVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        a11 = output.get("a11", 1.0); a12 = output.get("a12", 0.0)
        a21 = output.get("a21", 0.0); a22 = output.get("a22", 1.0)
        b1 = output.get("b1", 0.0); b2 = output.get("b2", 0.0)
        x = output.get("x", 0.0); y = output.get("y", 0.0)
        tol = context.get("tolerance", 0.01)
        violations = []
        r1 = abs(a11 * x + a12 * y - b1)
        r2 = abs(a21 * x + a22 * y - b2)
        if r1 > tol:
            violations.append(f"Equation-1 residual {r1:.4f} exceeds tolerance {tol}")
        if r2 > tol:
            violations.append(f"Equation-2 residual {r2:.4f} exceeds tolerance {tol}")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.2, 1.0 - 0.4 * len(violations)),
            violations=violations,
            remediation_steps=(["Apply Cramer's rule"] if violations else []),
        )


class MultiCalcVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        a = output.get("a", 0.0); b = output.get("b", 0.0); c = output.get("c", 1.0)
        tol = context.get("tolerance", 0.01)
        violations = []
        if abs(output.get("sum_abc", 0.0) - (a + b + c)) > tol:
            violations.append(f"sum_abc={output.get('sum_abc')} ≠ {a+b+c:.4f}")
        if abs(output.get("product_ab", 0.0) - (a * b)) > tol:
            violations.append(f"product_ab={output.get('product_ab')} ≠ {a*b:.4f}")
        if abs(c) > 1e-10:
            correct_ratio = round(a / c, 6)
            if abs(output.get("ratio_ac", 0.0) - correct_ratio) > tol:
                violations.append(f"ratio_ac={output.get('ratio_ac')} ≠ {correct_ratio:.6f}")
        if abs(output.get("max_abc", 0.0) - max(a, b, c)) > tol:
            violations.append(f"max_abc={output.get('max_abc')} ≠ {max(a, b, c)}")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.2, 1.0 - 0.2 * len(violations)),
            violations=violations,
        )


class LogicRulesVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        facts = output.get("facts", {}); rules = output.get("rules", [])
        derived = output.get("derived", {})
        violations = []
        for i, rule in enumerate(rules):
            ant = rule.get("if"); con = rule.get("then")
            expected = facts.get(ant, False)
            actual = derived.get(con)
            if actual != expected:
                violations.append(f"Rule {i+1}: '{ant}'={expected} → '{con}' should be {expected}, got {actual}")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.2, 1.0 - 0.3 * len(violations)),
            violations=violations,
        )


class SchedulePlanVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        events = output.get("events", []); violations = []
        for ev in events:
            eid = ev.get("id", "?")
            if ev.get("assigned_slot") != ev.get("required_slot"):
                violations.append(f"Event {eid}: slot {ev.get('assigned_slot')} ≠ required {ev.get('required_slot')}")
            if ev.get("n_attendees", 0) > ev.get("room_capacity", 0):
                violations.append(f"Event {eid}: {ev.get('n_attendees')} attendees > capacity {ev.get('room_capacity')}")
            if not ev.get("teacher_available", True):
                violations.append(f"Event {eid}: assigned teacher is unavailable")
            if ev.get("priority_met") is False:
                violations.append(f"Event {eid}: priority constraint not satisfied")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.1, 1.0 - 0.1 * len(violations)),
            violations=violations,
        )


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_VALID_STATUSES = {"active", "inactive", "pending", "suspended"}


class DataQualityVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        records = output.get("records", [])
        min_age = context.get("min_age", 0); max_age = context.get("max_age", 120)
        violations = []
        for rec in records:
            rid = rec.get("id", "?")
            email = rec.get("email", "")
            if not _EMAIL_RE.match(str(email)):
                violations.append(f"Record {rid}: invalid email '{email}'")
            age = rec.get("age", 0)
            if not (min_age <= age <= max_age):
                violations.append(f"Record {rid}: age {age} out of range [{min_age},{max_age}]")
            status = rec.get("status", "")
            if str(status).lower() not in _VALID_STATUSES:
                violations.append(f"Record {rid}: invalid status '{status}'")
            phone = str(rec.get("phone", "")).strip()
            digits = re.sub(r"\D", "", phone)
            if len(digits) not in (10, 11):
                violations.append(f"Record {rid}: malformed phone '{phone}'")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.1, 1.0 - 0.05 * len(violations)),
            violations=violations,
        )


class CausalChainVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        observed = output.get("observed", {}); causal_model = output.get("causal_model", {})
        active_causes = output.get("active_causes", {}); violations: List[str] = []
        for symptom, present in observed.items():
            if not present:
                continue
            explained = any(
                active_causes.get(cause, False)
                for cause, effects in causal_model.items()
                if symptom in effects
            )
            if not explained:
                violations.append(f"Symptom '{symptom}' is unexplained — no active cause produces it")
        for cause, active in active_causes.items():
            if not active:
                continue
            effects = causal_model.get(cause, [])
            if not any(observed.get(e, False) for e in effects):
                violations.append(f"Cause '{cause}' is active but none of its symptoms are observed")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.15, 1.0 - 0.25 * len(violations)),
            violations=violations,
        )


class ConstraintCSPVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        assignments = output.get("assignments", {}); constraints = output.get("constraints", [])
        violations: List[str] = []
        for c in constraints:
            ctype = c.get("type")
            if ctype == "not_equal":
                l, r = c["left"], c["right"]
                if assignments.get(l) == assignments.get(r):
                    violations.append(f"not_equal violated: {l}={assignments.get(l)} == {r}={assignments.get(r)}")
            elif ctype == "less_than":
                l, r = c["left"], c["right"]
                if not (assignments.get(l, 0) < assignments.get(r, 0)):
                    violations.append(f"less_than violated: {l}={assignments.get(l)} ≮ {r}={assignments.get(r)}")
            elif ctype == "in_range":
                v, lo, hi = c["var"], c["low"], c["high"]
                val = assignments.get(v, 0)
                if not (lo <= val <= hi):
                    violations.append(f"in_range violated: {v}={val} not in [{lo},{hi}]")
            elif ctype == "sum_bound":
                total = sum(assignments.get(v, 0) for v in c.get("vars", []))
                op, bound = c.get("op", "<="), c.get("bound", 0)
                ok = (op == "<=" and total <= bound) or (op == ">=" and total >= bound) or (op == "==" and total == bound)
                if not ok:
                    violations.append(f"sum_bound violated: sum({c.get('vars')})={total} not {op} {bound}")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.15, 1.0 - 0.2 * len(violations)),
            violations=violations,
        )


class BudgetAllocVerifier(DeterministicVerifier):
    def verify(self, output: Any, context: Dict) -> VerificationResult:
        categories = output.get("categories", {}); total_cap = output.get("total_cap", float("inf"))
        violations: List[str] = []; total = 0.0
        for name, cat in categories.items():
            alloc = cat.get("allocated", 0.0); mn = cat.get("min", 0.0); mx = cat.get("max", float("inf"))
            total += alloc
            if alloc < mn:
                violations.append(f"Category '{name}': allocated {alloc:.1f} < min {mn:.1f}")
            if alloc > mx:
                violations.append(f"Category '{name}': allocated {alloc:.1f} > max {mx:.1f}")
        if total > total_cap + 0.01:
            violations.append(f"Total allocated {total:.1f} exceeds budget cap {total_cap:.1f}")
        return VerificationResult(
            is_valid=not violations,
            confidence=1.0 if not violations else max(0.1, 1.0 - 0.15 * len(violations)),
            violations=violations,
        )
