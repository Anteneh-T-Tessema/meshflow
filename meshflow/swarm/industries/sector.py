"""Sector-specific verifiers — Government, Agriculture, Real Estate, Automotive, Media, Sports."""
from typing import Any, Dict
from meshflow.swarm.verifiers import DeterministicVerifier, VerificationResult


class GrantComplianceVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        costs = output.get("costs", [])
        effort_pct = output.get("effort_reported_pct", 0.0)
        actual_effort_pct = output.get("actual_effort_pct", 0.0)
        carryover_requested = output.get("carryover_amount", 0.0)
        carryover_approved = output.get("carryover_approved", False)
        ffr_expenditures = output.get("ffr_expenditures", 0.0)
        actual_expenditures = output.get("actual_expenditures", 0.0)
        unallowable = set(context.get("unallowable_cost_categories", [
            "alcoholic_beverages", "fines_penalties", "entertainment", "lobbying",
        ]))

        for cost in costs:
            cat = cost.get("category", "")
            amt = cost.get("amount", 0)
            if cat in unallowable:
                v.append(f"Unallowable Cost: '{cat}' (${amt:,.0f}) is unallowable under 2 CFR 200.")
        if abs(effort_pct - actual_effort_pct) > 5.0:
            v.append(f"Effort Reporting: reported {effort_pct:.0f}% effort vs actual {actual_effort_pct:.0f}%; >5% deviation requires correction.")
        if carryover_requested > 0 and not carryover_approved:
            v.append(f"Carryover: ${carryover_requested:,.0f} carried over without prior awarding agency approval.")
        if abs(ffr_expenditures - actual_expenditures) > 0.01:
            v.append(f"FFR Error: reported expenditures ${ffr_expenditures:,.2f} ≠ actual ${actual_expenditures:,.2f}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class FARContractingVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        contract_value = output.get("contract_value", 0)
        competition_held = output.get("full_and_open_competition", False)
        j_a_documented = output.get("justification_approval_documented", False)
        sole_source = output.get("is_sole_source", False)
        small_biz_setaside = output.get("small_business_set_aside", False)
        naics_size_met = output.get("naics_size_standard_met", False)
        cost_data_certified = output.get("certified_cost_pricing_data", False)
        sat = context.get("simplified_acquisition_threshold", 250000)
        cost_cert_threshold = context.get("cost_pricing_data_threshold", 2000000)
        small_biz_threshold = context.get("small_biz_setaside_threshold", 250000)

        if contract_value > sat and not competition_held and not sole_source:
            v.append(f"FAR 6.1: contract ${contract_value:,.0f} above SAT requires full and open competition.")
        if sole_source and not j_a_documented:
            v.append("FAR 6.3: sole-source award requires documented Justification & Approval.")
        if sat < contract_value <= small_biz_threshold and not small_biz_setaside and not sole_source:
            v.append(f"FAR 19: contract ${contract_value:,.0f} requires small business set-aside evaluation.")
        if small_biz_setaside and not naics_size_met:
            v.append("FAR 19.5: vendor does not meet NAICS size standard for small business set-aside.")
        if contract_value > cost_cert_threshold and not cost_data_certified:
            v.append(f"FAR 15.403: contract ${contract_value:,.0f} requires certified cost or pricing data (TINA).")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class SAPVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        purchase_value = output.get("purchase_value", 0)
        quotes_obtained = output.get("quotes_obtained", 0)
        award_documented = output.get("award_rationale_documented", False)
        vendor_debarred = output.get("vendor_debarred", False)
        sam_verified = output.get("sam_gov_verified", False)
        micro_threshold = context.get("micro_purchase_threshold", 10000)
        min_quotes = context.get("min_quotes_required", 3)

        if purchase_value > micro_threshold and quotes_obtained < min_quotes:
            v.append(f"SAP: ${purchase_value:,.0f} purchase above micro-purchase threshold requires {min_quotes} quotes; obtained {quotes_obtained}.")
        if purchase_value > micro_threshold and not award_documented:
            v.append("SAP Documentation: award rationale not documented.")
        if vendor_debarred:
            v.append("Debarment: vendor is debarred or suspended per SAM.gov — cannot receive award.")
        if not sam_verified and purchase_value > micro_threshold:
            v.append("SAM.gov: vendor not verified in System for Award Management prior to award.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class ResearchEffortVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        irb_approved = output.get("irb_approved", False)
        involves_human_subjects = output.get("involves_human_subjects", False)
        data_sharing_plan = output.get("data_sharing_plan_present", False)
        coi_disclosed = output.get("coi_disclosed", False)
        coi_exists = output.get("coi_exists", False)
        authorship_criteria_met = output.get("all_authors_meet_icmje_criteria", True)
        guest_author = output.get("guest_author_present", False)
        ghost_author = output.get("ghost_author_present", False)

        if involves_human_subjects and not irb_approved:
            v.append("Research Ethics: study involves human subjects but lacks IRB/ethics board approval.")
        if not data_sharing_plan:
            v.append("Data Sharing: federally funded research requires a data sharing/management plan.")
        if coi_exists and not coi_disclosed:
            v.append("COI: conflict of interest exists but not disclosed — violates research integrity policy.")
        if not authorship_criteria_met:
            v.append("Authorship: one or more authors do not meet ICMJE authorship criteria.")
        if guest_author:
            v.append("Guest Authorship: individual listed as author who did not make substantive contributions.")
        if ghost_author:
            v.append("Ghost Authorship: individual who made substantive contributions omitted from authorship.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class OrganicCertVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        inputs = output.get("inputs_used", [])
        buffer_ft = output.get("buffer_zone_feet", 0)
        prohibited_found = output.get("prohibited_substances_detected", [])
        audit_trail = output.get("audit_trail_complete", False)
        certifier_approved = output.get("certifier_approved", False)
        transition_years = output.get("transition_years_completed", 0)
        min_buffer = context.get("min_buffer_zone_feet", 50)
        required_transition = context.get("required_transition_years", 3)
        approved_inputs = set(context.get("approved_inputs", []))

        if not certifier_approved:
            v.append("USDA NOP: operation not certified by an accredited certifying agent.")
        if transition_years < required_transition:
            v.append(f"Transition: {transition_years} year(s) completed; {required_transition} required before organic certification.")
        if buffer_ft < min_buffer:
            v.append(f"Buffer Zone: {buffer_ft}ft buffer insufficient; minimum {min_buffer}ft required.")
        if prohibited_found:
            v.append(f"Prohibited Substances: detected on certified land: {prohibited_found}.")
        if approved_inputs:
            for inp in inputs:
                if inp not in approved_inputs:
                    v.append(f"Input Compliance: '{inp}' not on approved inputs list.")
        if not audit_trail:
            v.append("Audit Trail: complete input/output audit trail not maintained.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class FoodLabelVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        serving_size_g = output.get("serving_size_g", 0)
        rdi_serving_size = output.get("reference_amount_customarily_consumed_g", 0)
        nutrients = output.get("nutrients_declared", {})
        net_qty_claimed = output.get("net_quantity_claimed_g", 0)
        net_qty_measured = output.get("net_quantity_measured_g", 0)
        health_claim = output.get("health_claim", "")
        authorized_claims = set(context.get("authorized_health_claims", []))
        required_nutrients = context.get("required_nutrients", [
            "calories", "total_fat", "saturated_fat", "trans_fat", "cholesterol",
            "sodium", "total_carbohydrate", "dietary_fiber", "total_sugars", "protein",
        ])

        if rdi_serving_size > 0 and abs(serving_size_g - rdi_serving_size) / rdi_serving_size > 0.20:
            v.append(f"Serving Size: {serving_size_g}g deviates >20% from RACC of {rdi_serving_size}g.")
        for nut in required_nutrients:
            if nut not in nutrients:
                v.append(f"Label Requirement: required nutrient '{nut}' missing from nutrition facts panel.")
        if net_qty_measured > 0 and abs(net_qty_claimed - net_qty_measured) / net_qty_measured > 0.10:
            v.append(f"Net Quantity: declared {net_qty_claimed}g but measured {net_qty_measured}g — >10% deviation.")
        if health_claim and health_claim not in authorized_claims:
            v.append(f"Health Claim: '{health_claim}' is not an FDA-authorized health claim.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class DSCRVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        noi = output.get("net_operating_income", 0)
        debt_service = output.get("annual_debt_service", 1)
        loan_amount = output.get("loan_amount", 0)
        appraised_value = output.get("appraised_value", 1)
        exit_cap = output.get("exit_cap_rate", 0.0)
        stressed_noi = output.get("stressed_noi", noi)
        min_dscr = context.get("min_dscr", 1.25)
        max_ltv = context.get("max_ltv", 0.75)
        min_stressed_dscr = context.get("min_stressed_dscr", 1.10)
        max_exit_cap = context.get("max_exit_cap_rate", 0.08)

        dscr = noi / max(debt_service, 1)
        ltv = loan_amount / max(appraised_value, 1)
        stressed_dscr = stressed_noi / max(debt_service, 1)

        if dscr < min_dscr:
            v.append(f"DSCR Underwriting: {dscr:.2f}x below minimum {min_dscr}x.")
        if ltv > max_ltv:
            v.append(f"LTV: {ltv:.1%} exceeds maximum {max_ltv:.0%} for this property type.")
        if stressed_dscr < min_stressed_dscr:
            v.append(f"Stress Test: stressed DSCR {stressed_dscr:.2f}x below {min_stressed_dscr}x floor.")
        if exit_cap > max_exit_cap:
            v.append(f"Exit Cap Rate: {exit_cap:.2%} exceeds underwriting maximum {max_exit_cap:.2%}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class AIAG702Verifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        ppap_elements_submitted = set(output.get("ppap_elements_submitted", []))
        submission_level = output.get("submission_level", 3)
        max_rpn = output.get("max_pfmea_rpn", 0)
        dimensional_pass_rate = output.get("dimensional_pass_rate_pct", 100.0)
        warrant_signed = output.get("psa_warrant_signed", False)
        cpk_min = output.get("min_cpk_reported", 0.0)
        level_requirements = context.get("level_requirements", {
            "3": {"design_records", "pfmea", "control_plan", "measurement_system_analysis",
                  "dimensional_results", "material_test_results", "initial_process_study",
                  "lab_documentation", "appearance_approval", "sample_parts",
                  "master_sample", "checking_aids", "customer_specific_requirements",
                  "part_submission_warrant"},
        })
        required = level_requirements.get(str(submission_level), set())
        rpn_limit = context.get("max_acceptable_rpn", 100)
        min_dim_pass = context.get("min_dimensional_pass_rate_pct", 100.0)
        min_cpk = context.get("min_cpk", 1.67)

        missing = required - ppap_elements_submitted
        if missing:
            v.append(f"PPAP Level {submission_level}: missing required elements: {sorted(missing)}.")
        if max_rpn > rpn_limit:
            v.append(f"PFMEA RPN: maximum RPN {max_rpn} exceeds acceptable limit {rpn_limit}.")
        if dimensional_pass_rate < min_dim_pass:
            v.append(f"Dimensional: {dimensional_pass_rate:.0f}% pass rate below {min_dim_pass:.0f}% required.")
        if not warrant_signed:
            v.append("PPAP Warrant: Part Submission Warrant not signed by authorized supplier representative.")
        if cpk_min < min_cpk:
            v.append(f"Process Capability: minimum Cpk {cpk_min:.2f} below AIAG requirement of {min_cpk}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class MechanicalRoyaltyVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        streams = output.get("stream_count", 0)
        royalty_paid = output.get("royalty_paid", 0.0)
        statutory_rate = output.get("statutory_rate_per_stream", 0.00069)
        sync_licensed = output.get("sync_license_obtained", False)
        used_in_video = output.get("used_in_audiovisual_work", False)
        pro_registered = output.get("pro_registration_current", False)
        split_sheet_filed = output.get("split_sheet_filed", False)
        co_writers = output.get("co_writer_count", 0)

        expected_royalty = round(streams * statutory_rate, 4)
        if abs(royalty_paid - expected_royalty) / max(expected_royalty, 0.01) > 0.02:
            v.append(f"Mechanical Royalty: paid ${royalty_paid:.4f}; owed ${expected_royalty:.4f}.")
        if used_in_video and not sync_licensed:
            v.append("Sync License: song used in audiovisual work without synchronization license.")
        if not pro_registered:
            v.append("PRO Registration: composition not registered with a Performing Rights Organization.")
        if co_writers > 0 and not split_sheet_filed:
            v.append(f"Split Sheet: {co_writers} co-writer(s) involved but ownership split sheet not filed.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class SportsCapVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        total_payroll = output.get("total_payroll", 0)
        luxury_tax_paid = output.get("luxury_tax_paid", 0)
        roster_count = output.get("active_roster_count", 0)
        deferred_pct = output.get("deferred_compensation_pct", 0.0)
        contract_years = output.get("contract_years", 0)
        hard_cap = context.get("hard_cap", float("inf"))
        tax_threshold = context.get("luxury_tax_threshold", float("inf"))
        tax_rate = context.get("luxury_tax_rate", 1.5)
        max_roster = context.get("max_roster_size", 53)
        min_roster = context.get("min_roster_size", 0)
        max_contract_years = context.get("max_contract_years", 5)
        max_deferred_pct = context.get("max_deferred_pct", 25.0)

        if total_payroll > hard_cap:
            v.append(f"Hard Cap: team payroll ${total_payroll:,.0f} exceeds hard cap ${hard_cap:,.0f}.")
        if total_payroll > tax_threshold:
            expected_tax = round((total_payroll - tax_threshold) * tax_rate, 0)
            if abs(luxury_tax_paid - expected_tax) > 1:
                v.append(f"Luxury Tax: owed ${expected_tax:,.0f}; paid ${luxury_tax_paid:,.0f}.")
        if roster_count > max_roster:
            v.append(f"Roster: {roster_count} players exceeds maximum active roster of {max_roster}.")
        if min_roster > 0 and roster_count < min_roster:
            v.append(f"Roster: {roster_count} players below minimum required {min_roster}.")
        if contract_years > max_contract_years:
            v.append(f"Contract Length: {contract_years}-year contract exceeds CBA maximum of {max_contract_years} years.")
        if deferred_pct > max_deferred_pct:
            v.append(f"Deferred Compensation: {deferred_pct:.0f}% deferred exceeds CBA limit of {max_deferred_pct:.0f}%.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)
