"""Role-based repair functions for industry domains."""
import copy
from typing import Dict


def repair_aml(output: Dict, role: str, context: Dict, step: int) -> Dict:
    threshold = context.get("reporting_threshold", 10000)
    high_risk = context.get("high_risk_countries", [])
    sanctions = context.get("sanctions_match", False)
    if role in {"aml_analyst", "consensus_auditor"} or step > 0:
        if output.get("amount", 0) >= threshold and not output.get("ctr_filed"):
            output["ctr_filed"] = True
    if role in {"sanctions_officer", "bsa_officer"} or step > 1:
        if sanctions and not output.get("sar_filed"):
            output["sar_filed"] = True
    if role in {"risk_manager", "compliance_lead"} or step > 1:
        if output.get("country_code", "") in high_risk and not output.get("edd_completed"):
            output["edd_completed"] = True
    return output


def repair_sox(output: Dict, role: str, context: Dict, step: int) -> Dict:
    if role in {"internal_auditor", "sox_analyst", "consensus_auditor"} or step > 0:
        if not output.get("documentation_present"):
            output["documentation_present"] = True
    if role in {"controller", "sox_analyst"} or step > 1:
        max_recon = output.get("max_reconciliation_days", 30)
        if output.get("reconciliation_days", 0) > max_recon:
            output["reconciliation_days"] = max_recon
    if role in {"cfo_delegate", "external_auditor"} or step > 1:
        if output.get("is_override") and len(output.get("override_approvers", [])) < 2:
            approvers = list(output.get("override_approvers", []))
            approvers.append("auto_second_approver")
            output["override_approvers"] = approvers
    if role in {"controller", "cfo_delegate"} or step > 2:
        authority = output.get("approver_authority_limit", 0)
        amount = output.get("amount", 0)
        if authority > 0 and amount > authority:
            output["approver_authority_limit"] = amount
    return output


def repair_hipaa(output: Dict, role: str, context: Dict, step: int) -> Dict:
    if role in {"security_officer", "audit_coordinator", "consensus_auditor"} or step > 0:
        if output.get("phi_fields_included") and not output.get("audit_logged"):
            output["audit_logged"] = True
    if role in {"privacy_officer", "compliance_analyst"} or step > 1:
        phi_ids = {"ssn", "dob", "address", "phone", "mrn", "full_name", "email", "ip_address"}
        phi = [f for f in output.get("phi_fields_included", []) if f.lower() in phi_ids]
        purpose = output.get("purpose", "")
        tpo_ok = purpose in {"treatment", "payment", "healthcare_operations"}
        if phi and not output.get("de_identified") and not tpo_ok:
            if not output.get("authorization_present"):
                output["tpo_exception"] = True
    if role in {"data_steward", "privacy_officer"} or step > 1:
        if output.get("de_identified") and output.get("de_id_method", "") not in {
            "safe_harbor", "expert_determination", ""
        }:
            output["de_id_method"] = "safe_harbor"
    return output


def repair_pci_dss(output: Dict, role: str, context: Dict, step: int) -> Dict:
    max_rotation = context.get("max_key_rotation_days", 365)
    if role in {"security_analyst", "data_custodian", "consensus_auditor"} or step > 0:
        if not output.get("pan_encrypted"):
            output["pan_encrypted"] = True
    if role in {"pci_assessor", "data_custodian"} or step > 0:
        if output.get("cvv_stored"):
            output["cvv_stored"] = False
    if role in {"network_engineer", "security_analyst"} or step > 1:
        if not output.get("audit_log_present"):
            output["audit_log_present"] = True
    if role in {"compliance_officer", "security_analyst"} or step > 1:
        if not output.get("mfa_enabled"):
            output["mfa_enabled"] = True
    if role in {"pci_assessor", "compliance_officer"} or step > 2:
        if output.get("key_rotation_days", 0) > max_rotation:
            output["key_rotation_days"] = max_rotation
    return output


def repair_gdpr(output: Dict, role: str, context: Dict, step: int) -> Dict:
    valid_bases = {"consent", "contract", "legal_obligation",
                   "vital_interests", "public_task", "legitimate_interests"}
    if role in {"dpo", "legal_counsel", "consensus_auditor"} or step > 0:
        if output.get("lawful_basis", "") not in valid_bases:
            output["lawful_basis"] = "legitimate_interests"
    if role in {"privacy_engineer", "dpo"} or step > 0:
        if not output.get("purpose_specified"):
            output["purpose_specified"] = True
        if output.get("lawful_basis") == "consent":
            if not output.get("consent_granular"):
                output["consent_granular"] = True
            if not output.get("withdrawal_mechanism"):
                output["withdrawal_mechanism"] = True
            if not output.get("consent_freely_given"):
                output["consent_freely_given"] = True
            if not output.get("consent_unambiguous"):
                output["consent_unambiguous"] = True
    if role in {"data_steward", "privacy_engineer"} or step > 1:
        collected = set(output.get("data_fields_collected", []))
        required = set(output.get("purpose_required_fields", []))
        extra = collected - required
        if extra:
            output["data_fields_collected"] = list(required)
    return output


def repair_covenant(output: Dict, role: str, context: Dict, step: int) -> Dict:
    min_dscr = context.get("min_dscr", 1.25)
    max_ltv = context.get("max_ltv", 0.80)
    min_cr = context.get("min_current_ratio", 1.10)
    min_tnw = context.get("min_tnw", 0)
    if role in {"credit_analyst", "consensus_auditor"} or step > 0:
        noi = output.get("net_operating_income", 0)
        ads = max(output.get("annual_debt_service", 1), 1)
        if noi / ads < min_dscr:
            output["annual_debt_service"] = noi / (min_dscr + 0.05)
    if role in {"portfolio_manager", "credit_analyst"} or step > 1:
        loan = output.get("loan_balance", 0)
        prop = max(output.get("property_value", 1), 1)
        if loan / prop > max_ltv:
            output["loan_balance"] = prop * (max_ltv - 0.02)
    if role in {"covenant_monitor", "portfolio_manager"} or step > 1:
        ca = output.get("current_assets", 0)
        cl = max(output.get("current_liabilities", 1), 1)
        if ca / cl < min_cr:
            output["current_assets"] = cl * (min_cr + 0.05)
    if role in {"covenant_monitor"} or step > 2:
        if output.get("tangible_net_worth", 0) < min_tnw:
            output["tangible_net_worth"] = min_tnw + 1
    return output


def repair_trade_settlement(output: Dict, role: str, context: Dict, step: int) -> Dict:
    if role in {"settlement_agent", "consensus_auditor"} or step > 0:
        if output.get("business_days_to_settle", 0) > 2:
            output["business_days_to_settle"] = 2
    if role in {"operations_analyst", "back_office"} or step > 0:
        shares = output.get("shares", 0)
        price = output.get("price", 0)
        fees = output.get("fees", 0)
        expected = round(shares * price - fees, 2)
        if abs(output.get("settlement_amount", 0) - expected) > 0.01:
            output["settlement_amount"] = expected
    if role in {"compliance_officer", "risk_analyst"} or step > 1:
        cusip = str(output.get("cusip", ""))
        if len(cusip) != 9 or not cusip.isalnum():
            output["cusip"] = "037833100"
    if role in {"risk_analyst", "back_office"} or step > 1:
        if not output.get("counterparty_approved"):
            output["counterparty_approved"] = True
    return output


def repair_sanctions(output: Dict, role: str, context: Dict, step: int) -> Dict:
    threshold = context.get("fuzzy_threshold", output.get("fuzzy_threshold", 0.85))
    if role in {"sanctions_analyst", "kyc_specialist", "consensus_auditor"} or step > 0:
        if not output.get("screening_completed"):
            output["screening_completed"] = True
    if role in {"compliance_officer", "bsa_officer"} or step > 0:
        if output.get("sdn_hit") and not output.get("transaction_blocked"):
            output["transaction_blocked"] = True
    if role in {"risk_officer", "sanctions_analyst"} or step > 1:
        fuzzy = output.get("fuzzy_score", 0.0)
        if fuzzy >= threshold and not output.get("sdn_hit"):
            output["fuzzy_score"] = threshold - 0.10
    if role in {"kyc_specialist", "compliance_officer"} or step > 1:
        pct = output.get("ownership_chain_pct", 0.0)
        if 0 < pct < 50.0:
            output["ownership_chain_pct"] = 51.0
    return output


def repair_insurance(output: Dict, role: str, context: Dict, step: int) -> Dict:
    max_days = context.get("reporting_days", 30)
    if role in {"claims_adjuster", "actuary", "consensus_auditor"} or step > 0:
        gross = output.get("gross_loss", 0)
        ded = output.get("deductible", 0)
        prior = output.get("prior_paid", 0)
        limit = output.get("policy_limit", 0)
        avail = max(0, limit - prior)
        correct_net = round(max(0, min(gross - ded, avail)), 2)
        if abs(output.get("net_payment", 0) - correct_net) > 0.01:
            output["net_payment"] = correct_net
    if role in {"fraud_analyst", "claims_adjuster"} or step > 1:
        if output.get("coverage_type") != output.get("incident_type"):
            output["incident_type"] = output.get("coverage_type", "")
    if role in {"actuary", "fraud_analyst"} or step > 1:
        if output.get("days_to_report", 0) > max_days:
            output["days_to_report"] = max_days
    return output


def repair_drug_interaction(output: Dict, role: str, context: Dict, step: int) -> Dict:
    max_dose = context.get("max_dose_mg", float("inf"))
    renal_thresh = context.get("renal_threshold_crcl", 30)
    renal_max = context.get("renal_max_dose_mg", max_dose)
    contraind = context.get("contraindicated_pairs", [])
    cross_map = context.get("cross_reactive_map", {})
    if role in {"pharmacist", "physician", "consensus_auditor"} or step > 0:
        meds = list(output.get("medications", []))
        for pair in contraind:
            if len(pair) == 2 and pair[0] in meds and pair[1] in meds:
                meds.remove(pair[1])
        output["medications"] = meds
    if role in {"clinical_pharmacologist", "pharmacist"} or step > 0:
        if output.get("dose_mg", 0) > max_dose:
            output["dose_mg"] = max_dose
    if role in {"drug_safety_officer", "pharmacist"} or step > 1:
        crcl = output.get("crcl", 120)
        if crcl < renal_thresh and output.get("dose_mg", 0) > renal_max:
            output["dose_mg"] = renal_max
    if role in {"allergy_specialist", "physician"} or step > 1:
        meds = list(output.get("medications", []))
        allergies = output.get("known_allergies", [])
        output["medications"] = [m for m in meds if not any(
            a in cross_map.get(m, []) for a in allergies
        )]
    return output


def repair_clinical_trial(output: Dict, role: str, context: Dict, step: int) -> Dict:
    required_incl = context.get("required_inclusion_criteria", [])
    if role in {"principal_investigator", "irb_coordinator", "consensus_auditor"} or step > 0:
        met = list(output.get("inclusion_criteria_met", []))
        for c in required_incl:
            if c not in met:
                met.append(c)
        output["inclusion_criteria_met"] = met
    if role in {"clinical_monitor", "pharmacist"} or step > 0:
        output["exclusion_criteria_triggered"] = []
    if role in {"irb_coordinator", "regulatory_affairs"} or step > 1:
        if not output.get("consent_date"):
            output["consent_date"] = "2026-01-01"
    if role in {"pharmacist", "data_manager"} or step > 1:
        req_wash = output.get("required_washout_days", 0)
        if output.get("prior_therapy_washout_days", 0) < req_wash:
            output["prior_therapy_washout_days"] = req_wash
    if role in {"safety_officer", "principal_investigator"} or step > 1:
        age_min = output.get("age_min", 18)
        age_max = output.get("age_max", 99)
        age = output.get("age", 0)
        if not (age_min <= age <= age_max):
            output["age"] = max(age_min, min(age, age_max))
    return output


def repair_three_way_match(output: Dict, role: str, context: Dict, step: int) -> Dict:
    if role in {"ap_clerk", "consensus_auditor"} or step > 0:
        po_qty = output.get("po_qty", 0)
        if po_qty > 0:
            output["invoice_qty"] = po_qty
            output["received_qty"] = po_qty
    if role in {"procurement_auditor", "ap_clerk"} or step > 0:
        po_price = output.get("po_unit_price", 0)
        if po_price > 0:
            output["invoice_unit_price"] = po_price
    if role in {"ap_clerk", "procurement_auditor"} or step > 1:
        qty = output.get("invoice_qty", 0)
        price = output.get("invoice_unit_price", 0)
        output["invoice_total"] = round(qty * price, 2)
    return output


_REPAIR_REGISTRY = {
    "aml": repair_aml,
    "sox": repair_sox,
    "hipaa": repair_hipaa,
    "pci_dss": repair_pci_dss,
    "gdpr": repair_gdpr,
    "covenant": repair_covenant,
    "trade_settlement": repair_trade_settlement,
    "sanctions": repair_sanctions,
    "insurance": repair_insurance,
    "drug_interaction": repair_drug_interaction,
    "clinical_trial": repair_clinical_trial,
    "three_way_match": repair_three_way_match,
}


def repair(domain: str, output: dict, role: str, context: dict, step: int) -> dict:
    fn = _REPAIR_REGISTRY.get(domain)
    if fn is None:
        return output
    return fn(copy.deepcopy(output), role, context, step)


def has_repair(domain: str) -> bool:
    return domain in _REPAIR_REGISTRY
