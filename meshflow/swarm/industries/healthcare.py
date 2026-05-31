"""Healthcare & Life Sciences verifiers — ICD-10, Drug Interaction, HIPAA, Prior Auth, Clinical Trial."""
from typing import Any, Dict
from meshflow.swarm.verifiers import DeterministicVerifier, VerificationResult


class ICD10BillingVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        dx = output.get("diagnosis_codes", [])
        px = output.get("procedure_codes", [])
        mods = output.get("modifiers", [])
        pos = output.get("place_of_service", "")
        bilateral = output.get("is_bilateral", False)
        necessity_map = context.get("medical_necessity_map", {})
        bundling_rules = context.get("bundling_rules", {})
        valid_pos = context.get("valid_place_of_service", [])

        for code in px:
            allowed_dx = necessity_map.get(code, [])
            if allowed_dx and not any(d in allowed_dx for d in dx):
                v.append(f"Medical Necessity: procedure {code} not supported by diagnosis codes {dx}.")

        if bilateral and "50" not in mods and "LT" not in mods and "RT" not in mods:
            v.append("Modifier Missing: bilateral procedure requires modifier 50, LT, or RT.")

        for code in px:
            comprehensive = bundling_rules.get(code)
            if comprehensive and comprehensive in px:
                v.append(f"Unbundling: component code {code} billed with comprehensive code {comprehensive}.")

        if valid_pos and pos not in valid_pos:
            v.append(f"Place of Service: '{pos}' invalid for billed procedure.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class DrugInteractionVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        meds = output.get("medications", [])
        dose = output.get("dose_mg", 0)
        crcl = output.get("crcl", 120)
        allergies = output.get("known_allergies", [])
        contraindicated = context.get("contraindicated_pairs", [])
        max_dose = context.get("max_dose_mg", float("inf"))
        renal_threshold = context.get("renal_threshold_crcl", 30)
        renal_max_dose = context.get("renal_max_dose_mg", max_dose)
        cross_reactive = context.get("cross_reactive_map", {})

        for pair in contraindicated:
            if len(pair) == 2 and pair[0] in meds and pair[1] in meds:
                v.append(f"Contraindication: {pair[0]} and {pair[1]} must not be co-administered.")
        if dose > max_dose:
            v.append(f"Overdose: {dose}mg exceeds maximum dose {max_dose}mg.")
        if crcl < renal_threshold and dose > renal_max_dose:
            v.append(f"Renal Dosing: CrCl {crcl} requires dose ≤ {renal_max_dose}mg; prescribed {dose}mg.")
        for drug in meds:
            cross = cross_reactive.get(drug, [])
            for allergen in allergies:
                if allergen in cross:
                    v.append(f"Allergy Cross-Reactivity: {drug} cross-reacts with documented allergy {allergen}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class HIPAAVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        purpose = output.get("purpose", "")
        phi_fields = output.get("phi_fields_included", [])
        auth = output.get("authorization_present", False)
        tpo = output.get("tpo_exception", False)
        audit_logged = output.get("audit_logged", False)
        de_id = output.get("de_identified", False)
        de_id_method = output.get("de_id_method", "")

        treatment_purposes = {"treatment", "payment", "healthcare_operations"}
        phi_identifiers = {"ssn", "dob", "address", "phone", "mrn", "full_name", "email", "ip_address"}

        exposed_phi = [f for f in phi_fields if f.lower() in phi_identifiers]
        if exposed_phi and not de_id:
            if purpose not in treatment_purposes and not auth and not tpo:
                v.append(f"HIPAA Violation: PHI fields {exposed_phi} disclosed without authorization or TPO exception.")

        if phi_fields and not audit_logged:
            v.append("HIPAA Req 164.312(b): PHI access not recorded in audit log.")

        if de_id and de_id_method not in {"safe_harbor", "expert_determination", ""}:
            v.append(f"De-identification: method '{de_id_method}' not recognized.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class PriorAuthVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        criteria_met = output.get("criteria_met", [])
        step_completed = output.get("step_therapy_completed", False)
        service_date = output.get("service_date", "")
        auth_start = output.get("auth_start_date", "")
        auth_end = output.get("auth_end_date", "")
        req_qty = output.get("requested_qty", 0)
        auth_qty = output.get("authorized_qty", 0)
        required_criteria = context.get("required_criteria", [])
        step_required = context.get("step_therapy_required", False)

        missing = [c for c in required_criteria if c not in criteria_met]
        if missing:
            v.append(f"Clinical Criteria: required criteria not met: {missing}.")
        if step_required and not step_completed:
            v.append("Step Therapy: required step therapy not documented as completed.")
        if service_date and auth_start and auth_end:
            if not (auth_start <= service_date <= auth_end):
                v.append(f"Authorization Window: service date {service_date} outside authorized period {auth_start}–{auth_end}.")
        if req_qty > auth_qty > 0:
            v.append(f"Quantity Exceeded: requested {req_qty} units; only {auth_qty} authorized.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class ClinicalTrialVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        inclusion_met = output.get("inclusion_criteria_met", [])
        exclusion_triggered = output.get("exclusion_criteria_triggered", [])
        consent_date = output.get("consent_date", "")
        washout_days = output.get("prior_therapy_washout_days", 0)
        required_washout = output.get("required_washout_days", 0)
        age = output.get("age", 0)
        age_min = output.get("age_min", 18)
        age_max = output.get("age_max", 99)
        required_inclusion = context.get("required_inclusion_criteria", [])

        missing = [c for c in required_inclusion if c not in inclusion_met]
        if missing:
            v.append(f"Inclusion Criteria: subject does not meet criteria: {missing}.")
        if exclusion_triggered:
            v.append(f"Exclusion Criteria: subject triggers exclusion criteria: {exclusion_triggered}.")
        if not consent_date:
            v.append("Consent: informed consent date not documented.")
        if washout_days < required_washout:
            v.append(f"Washout Period: {washout_days}d < required {required_washout}d for prior therapy.")
        if not (age_min <= age <= age_max):
            v.append(f"Age Eligibility: age {age} outside protocol range {age_min}–{age_max}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)
