"""Role-based repair functions for general reasoning domains."""
import copy
import re
from typing import Any, Dict

_VALID_STATUSES = {"active", "inactive", "pending", "suspended"}
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def repair_linear_system(output: Dict, role: str, context: Dict, step: int) -> Dict:
    a11 = output.get("a11", 1.0); a12 = output.get("a12", 0.0)
    a21 = output.get("a21", 0.0); a22 = output.get("a22", 1.0)
    b1 = output.get("b1", 0.0); b2 = output.get("b2", 0.0)
    tol = context.get("tolerance", 0.01)
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-10:
        return output
    correct_x = (b1 * a22 - b2 * a12) / det
    correct_y = (a11 * b2 - a21 * b1) / det
    if role in {"x_solver", "consensus_auditor"} or step > 0:
        if abs(output.get("x", 0.0) - correct_x) > tol:
            output["x"] = round(correct_x, 6)
    if role in {"y_solver", "equation_checker"} or step > 0:
        if abs(output.get("y", 0.0) - correct_y) > tol:
            output["y"] = round(correct_y, 6)
    return output


def repair_multi_calc(output: Dict, role: str, context: Dict, step: int) -> Dict:
    a = output.get("a", 0.0); b = output.get("b", 0.0); c = output.get("c", 1.0)
    tol = context.get("tolerance", 0.01)
    if role in {"sum_checker", "consensus_auditor"} or step > 0:
        correct = a + b + c
        if abs(output.get("sum_abc", 0.0) - correct) > tol:
            output["sum_abc"] = correct
    if role in {"product_checker"} or step > 0:
        correct = a * b
        if abs(output.get("product_ab", 0.0) - correct) > tol:
            output["product_ab"] = correct
    if role in {"ratio_checker"} or step > 1:
        if abs(c) > 1e-10:
            correct = round(a / c, 6)
            if abs(output.get("ratio_ac", 0.0) - correct) > tol:
                output["ratio_ac"] = correct
    if role in {"max_checker", "equation_checker"} or step > 1:
        correct = max(a, b, c)
        if abs(output.get("max_abc", 0.0) - correct) > tol:
            output["max_abc"] = correct
    return output


def repair_logic_rules(output: Dict, role: str, context: Dict, step: int) -> Dict:
    facts = output.get("facts", {}); rules = output.get("rules", [])
    derived = dict(output.get("derived", {}))
    _ownership = {
        "rule_1_applier": {0}, "rule_2_applier": {1}, "rule_3_applier": {2},
        "logic_checker": {0, 1, 2}, "consensus_auditor": set(range(len(rules))),
    }
    owned = _ownership.get(role, set())
    if step > 0:
        owned = set(range(len(rules)))
    for i in owned:
        if i < len(rules):
            ant = rules[i].get("if"); con = rules[i].get("then")
            expected = facts.get(ant, False)
            if derived.get(con) != expected:
                derived[con] = expected
    output["derived"] = derived
    return output


def repair_schedule_plan(output: Dict, role: str, context: Dict, step: int) -> Dict:
    events = output.get("events", [])
    if role in {"slot_scheduler", "consensus_auditor"} or step > 0:
        for ev in events:
            if ev.get("assigned_slot") != ev.get("required_slot"):
                ev["assigned_slot"] = ev["required_slot"]
    if role in {"capacity_fixer"} or step > 0:
        for ev in events:
            if ev.get("n_attendees", 0) > ev.get("room_capacity", 0):
                ev["room_capacity"] = ev["n_attendees"]
    if role in {"teacher_assigner"} or step > 1:
        for ev in events:
            if not ev.get("teacher_available", True):
                ev["teacher_available"] = True
    if role in {"priority_optimizer", "equation_checker"} or step > 1:
        for ev in events:
            if ev.get("priority_met") is False:
                ev["priority_met"] = True
    output["events"] = events
    return output


def repair_data_quality(output: Dict, role: str, context: Dict, step: int) -> Dict:
    records = output.get("records", [])
    min_age = context.get("min_age", 0); max_age = context.get("max_age", 120)
    if role in {"email_validator", "consensus_auditor"} or step > 0:
        for rec in records:
            email = str(rec.get("email", ""))
            if not _EMAIL_RE.match(email):
                name_slug = str(rec.get("name", "user")).lower().replace(" ", ".")
                rec["email"] = f"{name_slug}@example.com"
    if role in {"age_validator"} or step > 0:
        for rec in records:
            age = rec.get("age", 0)
            if not (min_age <= age <= max_age):
                rec["age"] = max(min_age, min(age, max_age))
    if role in {"status_validator"} or step > 1:
        for rec in records:
            if str(rec.get("status", "")).lower() not in _VALID_STATUSES:
                rec["status"] = "active"
    if role in {"phone_normalizer", "equation_checker"} or step > 1:
        for rec in records:
            phone = str(rec.get("phone", ""))
            digits = re.sub(r"\D", "", phone)
            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]
            if len(digits) != 10:
                digits = (digits + "0" * 10)[:10]
            rec["phone"] = digits
    output["records"] = records
    return output


def repair_causal_chain(output: Dict, role: str, context: Dict, step: int) -> Dict:
    observed = output.get("observed", {}); causal_model = output.get("causal_model", {})
    active = dict(output.get("active_causes", {})); causes = list(causal_model.keys())
    _owned: Dict[str, set] = {
        "cause_0_analyst": {0}, "cause_1_analyst": {1}, "cause_2_analyst": {2},
        "cause_3_analyst": {3}, "general_diagnostician": set(range(len(causes))),
        "consensus_auditor": set(range(len(causes))),
    }
    owned_indices = _owned.get(role, set())
    if step > 0:
        owned_indices = set(range(len(causes)))
    for i in owned_indices:
        if i >= len(causes):
            continue
        cause = causes[i]; effects = causal_model.get(cause, [])
        active[cause] = any(observed.get(e, False) for e in effects)
    output["active_causes"] = active
    return output


def repair_constraint_csp(output: Dict, role: str, context: Dict, step: int) -> Dict:
    assignments = dict(output.get("assignments", {})); constraints = output.get("constraints", [])

    def _fix_not_equal(c):
        l, r = c["left"], c["right"]
        if assignments.get(l) == assignments.get(r):
            assignments[l] = assignments.get(l, 0) + 1

    def _fix_less_than(c):
        l, r = c["left"], c["right"]
        if not (assignments.get(l, 0) < assignments.get(r, 0)):
            assignments[l] = assignments.get(r, 1) - 1

    def _fix_in_range(c):
        v, lo, hi = c["var"], c["low"], c["high"]
        assignments[v] = max(lo, min(assignments.get(v, lo), hi))

    def _fix_sum_bound(c):
        vs = c.get("vars", []); op = c.get("op", "<="); bound = c.get("bound", 0)
        total = sum(assignments.get(v, 0) for v in vs)
        if op == "<=" and total > bound and total > 0:
            scale = bound / total
            for v in vs:
                assignments[v] = round(assignments.get(v, 0) * scale, 4)
        elif op == ">=" and total < bound and len(vs) > 0:
            delta = (bound - total) / len(vs)
            for v in vs:
                assignments[v] = round(assignments.get(v, 0) + delta, 4)

    _FIXERS = {"not_equal": _fix_not_equal, "less_than": _fix_less_than,
               "in_range": _fix_in_range, "sum_bound": _fix_sum_bound}
    _ROLE_TYPES = {
        "neq_fixer": {"not_equal"}, "ordering_fixer": {"less_than"},
        "range_fixer": {"in_range"}, "sum_fixer": {"sum_bound"},
        "general_solver": set(_FIXERS.keys()), "consensus_auditor": set(_FIXERS.keys()),
    }
    owned_types = _ROLE_TYPES.get(role, set())
    if step > 0:
        owned_types = set(_FIXERS.keys())
    for c in constraints:
        ctype = c.get("type")
        if ctype in owned_types and ctype in _FIXERS:
            _FIXERS[ctype](c)
    output["assignments"] = assignments
    return output


def repair_budget_alloc(output: Dict, role: str, context: Dict, step: int) -> Dict:
    categories = {k: dict(v) for k, v in output.get("categories", {}).items()}
    total_cap = output.get("total_cap", float("inf"))

    def _enforce_min():
        for cat in categories.values():
            if cat.get("allocated", 0) < cat.get("min", 0):
                cat["allocated"] = cat["min"]

    def _enforce_max():
        for cat in categories.values():
            if cat.get("allocated", 0) > cat.get("max", float("inf")):
                cat["allocated"] = cat["max"]

    def _balance_total():
        total = sum(c.get("allocated", 0) for c in categories.values())
        if total > total_cap + 0.01 and total > 0:
            scale = total_cap / total
            for cat in categories.values():
                cat["allocated"] = round(cat.get("allocated", 0) * scale, 2)
                cat["allocated"] = max(cat.get("min", 0), cat["allocated"])

    if role in {"min_enforcer", "consensus_auditor"} or step > 0:
        _enforce_min()
    if role in {"max_capper"} or step > 0:
        _enforce_max()
    if role in {"total_balancer"} or step > 1:
        _balance_total()
    if role in {"proportionality_checker", "consensus_auditor"} or step > 1:
        _enforce_min(); _enforce_max(); _balance_total()

    output["categories"] = categories
    return output


_REPAIR_REGISTRY: Dict[str, Any] = {
    "linear_system": repair_linear_system, "multi_calc": repair_multi_calc,
    "logic_rules": repair_logic_rules, "schedule_plan": repair_schedule_plan,
    "data_quality": repair_data_quality, "causal_chain": repair_causal_chain,
    "constraint_csp": repair_constraint_csp, "budget_alloc": repair_budget_alloc,
}


def repair(domain: str, output: dict, role: str, context: dict, step: int) -> dict:
    fn = _REPAIR_REGISTRY.get(domain)
    if fn is None:
        return output
    return fn(copy.deepcopy(output), role, context, step)


def has_repair(domain: str) -> bool:
    return domain in _REPAIR_REGISTRY
