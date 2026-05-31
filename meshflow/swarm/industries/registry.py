"""Central registry: maps domain keys → verifier class, roles, and agent configs."""
from typing import Any, Dict, List

from meshflow.swarm.verifiers import DeterministicVerifier
from meshflow.swarm.industries.financial import (
    AMLTransactionVerifier, CovenantComplianceVerifier, ThreeWayMatchVerifier,
    PCIDSSVerifier, InsuranceClaimsVerifier, TradeSettlementVerifier,
)
from meshflow.swarm.industries.healthcare import (
    ICD10BillingVerifier, DrugInteractionVerifier, HIPAAVerifier,
    PriorAuthVerifier, ClinicalTrialVerifier,
)
from meshflow.swarm.industries.legal import (
    SOXControlVerifier, GDPRConsentVerifier, ContractObligationVerifier,
    SANCtionsScreeningVerifier,
)
from meshflow.swarm.industries.operations import (
    CustomsClassificationVerifier, FoodSafetyVerifier, HazmatVerifier,
    SPCControlVerifier, OSHAIncidentVerifier, BOMIntegrityVerifier,
    NERCReliabilityVerifier, EPAEmissionsVerifier, HOSVerifier, AirworthinessVerifier,
)
from meshflow.swarm.industries.hr_tech import (
    FLSAOvertimeVerifier, ACAAffordabilityVerifier, EEOCPayEquityVerifier,
    OWASPSecurityVerifier, SLAComplianceVerifier, SBOMLicenseVerifier,
    MAPPricingVerifier, ProductSafetyVerifier, LoyaltyProgramVerifier,
)
from meshflow.swarm.industries.sector import (
    GrantComplianceVerifier, FARContractingVerifier, SAPVerifier,
    ResearchEffortVerifier, OrganicCertVerifier, FoodLabelVerifier,
    DSCRVerifier, AIAG702Verifier, MechanicalRoyaltyVerifier, SportsCapVerifier,
)


def _cfg(difficulty: str, agents: int, depth: int, roles: List[str]) -> Dict[str, Any]:
    return {"difficulty": difficulty, "n_agents": agents, "max_depth": depth, "roles": roles}


REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── Financial Services ───────────────────────────────────────────────
    "aml": {
        "verifier": AMLTransactionVerifier,
        **_cfg("hard", 5, 10, ["aml_analyst", "sanctions_officer", "compliance_lead", "risk_manager", "reporting_officer"]),
    },
    "covenant": {
        "verifier": CovenantComplianceVerifier,
        **_cfg("medium", 3, 6, ["credit_analyst", "covenant_monitor", "portfolio_manager"]),
    },
    "three_way_match": {
        "verifier": ThreeWayMatchVerifier,
        **_cfg("easy", 2, 3, ["ap_clerk", "procurement_auditor"]),
    },
    "pci_dss": {
        "verifier": PCIDSSVerifier,
        **_cfg("hard", 5, 10, ["security_analyst", "pci_assessor", "network_engineer", "data_custodian", "compliance_officer"]),
    },
    "insurance": {
        "verifier": InsuranceClaimsVerifier,
        **_cfg("medium", 3, 6, ["claims_adjuster", "actuary", "fraud_analyst"]),
    },
    "trade_settlement": {
        "verifier": TradeSettlementVerifier,
        **_cfg("hard", 5, 10, ["settlement_agent", "operations_analyst", "compliance_officer", "risk_analyst", "back_office"]),
    },
    # ── Healthcare & Life Sciences ───────────────────────────────────────
    "icd10": {
        "verifier": ICD10BillingVerifier,
        **_cfg("hard", 5, 10, ["medical_coder", "billing_specialist", "clinical_reviewer", "compliance_auditor", "physician_advisor"]),
    },
    "drug_interaction": {
        "verifier": DrugInteractionVerifier,
        **_cfg("very_hard", 8, 16, ["pharmacist", "clinical_pharmacologist", "physician", "allergy_specialist",
                                     "pharmacy_technician", "drug_safety_officer", "formulary_manager", "toxicologist"]),
    },
    "hipaa": {
        "verifier": HIPAAVerifier,
        **_cfg("hard", 5, 10, ["privacy_officer", "security_officer", "compliance_analyst", "data_steward", "audit_coordinator"]),
    },
    "prior_auth": {
        "verifier": PriorAuthVerifier,
        **_cfg("medium", 3, 6, ["utilization_manager", "clinical_reviewer", "appeals_coordinator"]),
    },
    "clinical_trial": {
        "verifier": ClinicalTrialVerifier,
        **_cfg("very_hard", 8, 16, ["principal_investigator", "irb_coordinator", "clinical_monitor", "regulatory_affairs",
                                     "data_manager", "pharmacist", "safety_officer", "biostatistician"]),
    },
    # ── Legal & Compliance ───────────────────────────────────────────────
    "sox": {
        "verifier": SOXControlVerifier,
        **_cfg("hard", 5, 10, ["internal_auditor", "controller", "sox_analyst", "external_auditor", "cfo_delegate"]),
    },
    "gdpr": {
        "verifier": GDPRConsentVerifier,
        **_cfg("hard", 5, 10, ["dpo", "privacy_engineer", "legal_counsel", "data_steward", "compliance_officer"]),
    },
    "contract": {
        "verifier": ContractObligationVerifier,
        **_cfg("medium", 3, 6, ["contract_manager", "legal_reviewer", "procurement_officer"]),
    },
    "sanctions": {
        "verifier": SANCtionsScreeningVerifier,
        **_cfg("hard", 5, 10, ["sanctions_analyst", "compliance_officer", "kyc_specialist", "risk_officer", "bsa_officer"]),
    },
    # ── Supply Chain & Operations ────────────────────────────────────────
    "customs": {
        "verifier": CustomsClassificationVerifier,
        **_cfg("hard", 5, 10, ["customs_broker", "trade_compliance", "import_specialist", "valuation_analyst", "coo_officer"]),
    },
    "food_safety": {
        "verifier": FoodSafetyVerifier,
        **_cfg("hard", 5, 10, ["food_safety_officer", "haccp_auditor", "quality_manager", "allergen_specialist", "recall_coordinator"]),
    },
    "hazmat": {
        "verifier": HazmatVerifier,
        **_cfg("hard", 5, 10, ["hazmat_specialist", "safety_officer", "transport_compliance", "packaging_engineer", "emergency_coordinator"]),
    },
    "spc": {
        "verifier": SPCControlVerifier,
        **_cfg("medium", 3, 6, ["quality_engineer", "process_engineer", "statistician"]),
    },
    "osha": {
        "verifier": OSHAIncidentVerifier,
        **_cfg("medium", 3, 6, ["safety_manager", "ehs_coordinator", "hr_representative"]),
    },
    "bom": {
        "verifier": BOMIntegrityVerifier,
        **_cfg("hard", 5, 10, ["bom_engineer", "procurement_specialist", "supply_chain_analyst", "configuration_manager", "cost_estimator"]),
    },
    "nerc": {
        "verifier": NERCReliabilityVerifier,
        **_cfg("very_hard", 8, 16, ["reliability_engineer", "cip_compliance", "operations_engineer", "cyber_security",
                                     "transmission_planner", "protection_engineer", "scada_specialist", "grid_operator"]),
    },
    "epa": {
        "verifier": EPAEmissionsVerifier,
        **_cfg("hard", 5, 10, ["environmental_engineer", "air_quality_specialist", "cems_technician", "permit_manager", "reporting_analyst"]),
    },
    "hos": {
        "verifier": HOSVerifier,
        **_cfg("medium", 3, 6, ["dot_compliance_officer", "fleet_manager", "driver_supervisor"]),
    },
    "airworthiness": {
        "verifier": AirworthinessVerifier,
        **_cfg("very_hard", 8, 16, ["airworthiness_inspector", "a_p_mechanic", "avionics_technician", "weight_balance_specialist",
                                     "mel_coordinator", "qms_auditor", "flight_ops", "faa_designee"]),
    },
    # ── HR, Technology & Retail ──────────────────────────────────────────
    "flsa": {
        "verifier": FLSAOvertimeVerifier,
        **_cfg("medium", 3, 6, ["payroll_specialist", "hr_compliance", "wage_hour_analyst"]),
    },
    "aca": {
        "verifier": ACAAffordabilityVerifier,
        **_cfg("medium", 3, 6, ["benefits_analyst", "hr_manager", "tax_specialist"]),
    },
    "eeoc": {
        "verifier": EEOCPayEquityVerifier,
        **_cfg("hard", 5, 10, ["hr_analyst", "compensation_specialist", "legal_counsel", "dei_officer", "data_analyst"]),
    },
    "owasp": {
        "verifier": OWASPSecurityVerifier,
        **_cfg("hard", 5, 10, ["security_engineer", "appsec_analyst", "penetration_tester", "devsecops", "ciso_delegate"]),
    },
    "sla": {
        "verifier": SLAComplianceVerifier,
        **_cfg("medium", 3, 6, ["service_manager", "operations_analyst", "customer_success"]),
    },
    "sbom": {
        "verifier": SBOMLicenseVerifier,
        **_cfg("medium", 3, 6, ["open_source_counsel", "software_engineer", "compliance_officer"]),
    },
    "map": {
        "verifier": MAPPricingVerifier,
        **_cfg("easy", 2, 3, ["pricing_analyst", "channel_manager"]),
    },
    "product_safety": {
        "verifier": ProductSafetyVerifier,
        **_cfg("hard", 5, 10, ["product_safety_engineer", "regulatory_specialist", "quality_manager", "toxicologist", "recall_coordinator"]),
    },
    "loyalty": {
        "verifier": LoyaltyProgramVerifier,
        **_cfg("easy", 2, 3, ["loyalty_analyst", "crm_specialist"]),
    },
    # ── Government, Agriculture, Real Estate, Auto, Media, Sports ────────
    "grant": {
        "verifier": GrantComplianceVerifier,
        **_cfg("hard", 5, 10, ["grants_manager", "cost_analyst", "pi_representative", "sponsored_programs", "auditor"]),
    },
    "far": {
        "verifier": FARContractingVerifier,
        **_cfg("hard", 5, 10, ["contracting_officer", "small_business_advisor", "cost_analyst", "legal_counsel", "pco_delegate"]),
    },
    "sap_gov": {
        "verifier": SAPVerifier,
        **_cfg("easy", 2, 3, ["contracting_officer", "procurement_analyst"]),
    },
    "research": {
        "verifier": ResearchEffortVerifier,
        **_cfg("medium", 3, 6, ["research_compliance", "irb_coordinator", "grants_manager"]),
    },
    "organic": {
        "verifier": OrganicCertVerifier,
        **_cfg("medium", 3, 6, ["certifier", "agronomist", "food_safety_auditor"]),
    },
    "food_label": {
        "verifier": FoodLabelVerifier,
        **_cfg("medium", 3, 6, ["regulatory_affairs", "food_scientist", "label_reviewer"]),
    },
    "cre_dscr": {
        "verifier": DSCRVerifier,
        **_cfg("hard", 5, 10, ["credit_analyst", "underwriter", "cre_appraiser", "risk_manager", "loan_officer"]),
    },
    "ppap": {
        "verifier": AIAG702Verifier,
        **_cfg("very_hard", 8, 16, ["quality_engineer", "apqp_coordinator", "supplier_quality", "design_engineer",
                                     "process_engineer", "metrology_specialist", "pfmea_analyst", "customer_rep"]),
    },
    "music_royalty": {
        "verifier": MechanicalRoyaltyVerifier,
        **_cfg("medium", 3, 6, ["royalty_analyst", "music_publisher", "rights_manager"]),
    },
    "sports_cap": {
        "verifier": SportsCapVerifier,
        **_cfg("hard", 5, 10, ["cap_analyst", "legal_counsel", "gm_delegate", "agent_liaison", "finance_officer"]),
    },
}


def get_verifier(domain: str) -> DeterministicVerifier:
    entry = REGISTRY.get(domain)
    if entry is None:
        raise KeyError(f"Unknown domain '{domain}'. Available: {sorted(REGISTRY.keys())}")
    return entry["verifier"]()


def get_roles(domain: str) -> list:
    return REGISTRY[domain]["roles"]


def get_config(domain: str) -> Dict[str, Any]:
    entry = REGISTRY[domain]
    return {k: entry[k] for k in ("difficulty", "n_agents", "max_depth")}


def get_domains() -> list:
    return sorted(REGISTRY.keys())
