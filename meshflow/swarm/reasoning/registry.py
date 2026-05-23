"""Registry for general reasoning domains."""
from meshflow.swarm.reasoning.verifiers import (
    LinearSystemVerifier, MultiCalcVerifier, LogicRulesVerifier,
    SchedulePlanVerifier, DataQualityVerifier,
    CausalChainVerifier, ConstraintCSPVerifier, BudgetAllocVerifier,
)

REGISTRY = {
    "linear_system": {
        "verifier": LinearSystemVerifier,
        "difficulty": "medium", "n_agents": 4, "max_depth": 6,
        "roles": ["x_solver", "y_solver", "equation_checker", "consensus_auditor"],
    },
    "multi_calc": {
        "verifier": MultiCalcVerifier,
        "difficulty": "easy", "n_agents": 5, "max_depth": 4,
        "roles": ["sum_checker", "product_checker", "ratio_checker", "max_checker", "consensus_auditor"],
    },
    "logic_rules": {
        "verifier": LogicRulesVerifier,
        "difficulty": "medium", "n_agents": 5, "max_depth": 4,
        "roles": ["rule_1_applier", "rule_2_applier", "rule_3_applier", "logic_checker", "consensus_auditor"],
    },
    "schedule_plan": {
        "verifier": SchedulePlanVerifier,
        "difficulty": "hard", "n_agents": 5, "max_depth": 8,
        "roles": ["slot_scheduler", "capacity_fixer", "teacher_assigner", "priority_optimizer", "consensus_auditor"],
    },
    "data_quality": {
        "verifier": DataQualityVerifier,
        "difficulty": "medium", "n_agents": 5, "max_depth": 6,
        "roles": ["email_validator", "age_validator", "status_validator", "phone_normalizer", "consensus_auditor"],
    },
    "causal_chain": {
        "verifier": CausalChainVerifier,
        "difficulty": "hard", "n_agents": 6, "max_depth": 6,
        "roles": ["cause_0_analyst", "cause_1_analyst", "cause_2_analyst", "cause_3_analyst",
                  "general_diagnostician", "consensus_auditor"],
    },
    "constraint_csp": {
        "verifier": ConstraintCSPVerifier,
        "difficulty": "medium", "n_agents": 5, "max_depth": 6,
        "roles": ["neq_fixer", "ordering_fixer", "range_fixer", "sum_fixer", "consensus_auditor"],
    },
    "budget_alloc": {
        "verifier": BudgetAllocVerifier,
        "difficulty": "medium", "n_agents": 5, "max_depth": 6,
        "roles": ["min_enforcer", "max_capper", "total_balancer", "proportionality_checker", "consensus_auditor"],
    },
}


def get_verifier(domain: str):
    return REGISTRY[domain]["verifier"]()


def get_roles(domain: str):
    return REGISTRY[domain]["roles"]


def get_domains():
    return sorted(REGISTRY.keys())
