"""Operations verifiers — Supply Chain, Manufacturing, Energy, Transportation."""
from typing import Any, Dict, List
from meshflow.swarm.verifiers import DeterministicVerifier, VerificationResult


class CustomsClassificationVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        hts = str(output.get("hts_code", ""))
        val_method = output.get("valuation_method", "")
        coo = output.get("country_of_origin", "")
        declared_val = output.get("declared_value", 0)
        transaction_val = output.get("transaction_value", 0)
        duty_paid = output.get("duty_paid", 0)
        duty_rate = output.get("duty_rate", 0.0)
        valid_methods = {"transaction_value", "deductive", "computed", "fallback"}
        restricted_coo = context.get("restricted_countries", [])
        fta_countries = context.get("fta_countries", [])

        if not hts or len(hts.replace(".", "")) < 8:
            v.append(f"HTS Classification: code '{hts}' must be at least 8 digits.")
        if val_method not in valid_methods:
            v.append(f"Valuation: method '{val_method}' not a recognized WTO customs valuation method.")
        if declared_val > 0 and transaction_val > 0 and abs(declared_val - transaction_val) / transaction_val > 0.10:
            v.append(f"Valuation Gap: declared value ${declared_val:,.0f} differs from transaction value ${transaction_val:,.0f} by >10%.")
        if coo in restricted_coo:
            v.append(f"Restricted Origin: country '{coo}' is subject to import restrictions or embargo.")
        expected_duty = round(transaction_val * duty_rate, 2)
        if duty_rate > 0 and coo not in fta_countries and abs(duty_paid - expected_duty) > 0.01:
            v.append(f"Duty Error: paid ${duty_paid:,.2f} but expected ${expected_duty:,.2f} at rate {duty_rate:.1%}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class FoodSafetyVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        ccp_passed = output.get("ccp_all_passed", False)
        ccp_failures = output.get("ccp_failures", [])
        temp_log = output.get("temperature_log_complete", False)
        max_temp = output.get("max_temp_celsius", 0.0)
        allergens_declared = set(output.get("allergens_declared", []))
        allergens_present = set(output.get("allergens_present", []))
        recall_active = output.get("recall_active", False)
        shelf_life_days = output.get("shelf_life_days", 0)
        min_shelf_life = context.get("min_shelf_life_days", 0)

        if not ccp_passed or ccp_failures:
            v.append(f"HACCP CCP Failure: critical control points failed: {ccp_failures}.")
        if not temp_log:
            v.append("Temperature Log: continuous temperature log not complete for this lot.")
        safe_max = context.get("max_safe_temp_celsius", 4.0)
        if max_temp > safe_max:
            v.append(f"Temperature Exceedance: max temp {max_temp}°C exceeds safe limit {safe_max}°C.")
        undeclared = allergens_present - allergens_declared
        if undeclared:
            v.append(f"Allergen Labeling: undeclared allergens present: {sorted(undeclared)}.")
        if recall_active:
            v.append("Active Recall: product or ingredient subject to an active FDA/USDA recall.")
        if min_shelf_life > 0 and shelf_life_days < min_shelf_life:
            v.append(f"Shelf Life: {shelf_life_days}d remaining below minimum {min_shelf_life}d required.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class HazmatVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        un_number = str(output.get("un_number", ""))
        packing_group = output.get("packing_group", "")
        net_qty_kg = output.get("net_quantity_kg", 0)
        labels_applied = set(output.get("hazard_labels_applied", []))
        labels_required = set(output.get("hazard_labels_required", []))
        sds_present = output.get("sds_present", False)
        transport_mode = output.get("transport_mode", "")
        quantity_limit = context.get("quantity_limit_kg", float("inf"))

        if not un_number.startswith("UN") or len(un_number) != 6 or not un_number[2:].isdigit():
            v.append(f"UN Number: '{un_number}' invalid format; must be UN followed by 4 digits.")
        if packing_group not in {"I", "II", "III", ""}:
            v.append(f"Packing Group: '{packing_group}' not valid (I, II, or III).")
        if net_qty_kg > quantity_limit:
            v.append(f"Quantity Limit: {net_qty_kg}kg exceeds {transport_mode} limit of {quantity_limit}kg.")
        missing_labels = labels_required - labels_applied
        if missing_labels:
            v.append(f"Hazard Labels: required labels not applied: {sorted(missing_labels)}.")
        if not sds_present:
            v.append("SDS Missing: Safety Data Sheet not included with shipment.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class SPCControlVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        measurements = output.get("measurements", [])
        ucl = output.get("ucl", float("inf"))
        lcl = output.get("lcl", float("-inf"))
        cpk = output.get("cpk", 0.0)
        cp = output.get("cp", 0.0)
        consecutive_same_side = output.get("consecutive_same_side", 0)
        min_cpk = context.get("min_cpk", 1.33)
        min_cp = context.get("min_cp", 1.33)

        out_of_control = [m for m in measurements if m > ucl or m < lcl]
        if out_of_control:
            v.append(f"Control Limit Violation: {len(out_of_control)} measurement(s) outside control limits: {out_of_control}.")
        if cpk < min_cpk:
            v.append(f"Process Capability: Cpk {cpk:.2f} below minimum {min_cpk} — process not capable.")
        if cp < min_cp:
            v.append(f"Process Potential: Cp {cp:.2f} below minimum {min_cp}.")
        if consecutive_same_side >= 8:
            v.append(f"Run Rule Violation: {consecutive_same_side} consecutive points on same side of centerline.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class OSHAIncidentVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        recordable = output.get("is_recordable", False)
        logged = output.get("logged_on_300", False)
        days_away = output.get("days_away_from_work", 0)
        reported_hours = output.get("hours_to_report", 0)
        fatality = output.get("is_fatality", False)
        hospitalized = output.get("hospitalized_count", 0)
        medical_treatment = output.get("medical_treatment_beyond_first_aid", False)
        first_aid_only = output.get("first_aid_only", True)

        if recordable and not logged:
            v.append("OSHA 300: recordable incident not logged on OSHA 300 log.")
        if fatality and reported_hours > 8:
            v.append(f"OSHA 1904.39: fatality must be reported within 8 hours; reported at {reported_hours}h.")
        if hospitalized >= 1 and reported_hours > 24:
            v.append(f"OSHA 1904.39: in-patient hospitalization must be reported within 24 hours; reported at {reported_hours}h.")
        if medical_treatment and first_aid_only:
            v.append("Classification Conflict: incident requires medical treatment but flagged as first-aid-only.")
        if days_away > 0 and not recordable:
            v.append(f"Classification Error: {days_away} days away from work makes incident recordable.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class BOMIntegrityVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        components = output.get("components", [])
        total_cost = output.get("total_cost", 0.0)
        computed_cost = output.get("computed_cost", 0.0)
        bom_level = output.get("bom_level", 1)
        max_level = context.get("max_bom_levels", 10)
        obsolete_parts = set(context.get("obsolete_part_numbers", []))

        for comp in components:
            pn = comp.get("part_number", "")
            qty = comp.get("quantity", 0)
            avail = comp.get("available_qty", 0)
            lead_days = comp.get("lead_time_days", 0)
            max_lead = context.get("max_lead_time_days", 90)
            if qty > avail:
                v.append(f"Availability: part {pn} requires {qty} units; only {avail} in stock.")
            if lead_days > max_lead:
                v.append(f"Lead Time: part {pn} lead time {lead_days}d exceeds maximum {max_lead}d.")
            if pn in obsolete_parts:
                v.append(f"Obsolete Part: {pn} is on the obsolete parts list.")

        if abs(total_cost - computed_cost) > 0.01:
            v.append(f"Cost Roll-Up Error: BOM total ${total_cost:,.2f} ≠ computed ${computed_cost:,.2f}.")
        if bom_level > max_level:
            v.append(f"BOM Depth: {bom_level} levels exceeds maximum {max_level}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class NERCReliabilityVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        asset_classified = output.get("critical_cyber_asset_classified", False)
        esp_enforced = output.get("electronic_security_perimeter_enforced", False)
        thermal_loading = output.get("thermal_loading_pct", 0.0)
        thermal_limit = context.get("thermal_limit_pct", 100.0)
        contingency_n1 = output.get("n1_contingency_passed", False)
        patch_days = output.get("patch_days_overdue", 0)
        max_patch = context.get("max_patch_overdue_days", 35)
        access_review_current = output.get("access_review_current", False)

        if not asset_classified:
            v.append("NERC CIP-002: critical cyber assets not properly classified.")
        if not esp_enforced:
            v.append("NERC CIP-005: Electronic Security Perimeter not enforced.")
        if thermal_loading > thermal_limit:
            v.append(f"NERC FAC-008: thermal loading {thermal_loading:.0f}% exceeds facility rating {thermal_limit:.0f}%.")
        if not contingency_n1:
            v.append("NERC TPL-001: N-1 contingency analysis not passed.")
        if patch_days > max_patch:
            v.append(f"NERC CIP-007: security patches {patch_days}d overdue; maximum allowed {max_patch}d.")
        if not access_review_current:
            v.append("NERC CIP-006: physical/logical access review not current.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class EPAEmissionsVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        nox_lbs = output.get("nox_lbs_per_hour", 0.0)
        so2_lbs = output.get("so2_lbs_per_hour", 0.0)
        pm25_ug_m3 = output.get("pm25_ug_per_m3", 0.0)
        cems_calibrated = output.get("cems_calibrated", False)
        excess_reported = output.get("excess_emissions_reported", False)
        excess_hours = output.get("excess_emission_hours", 0)
        nox_limit = context.get("nox_limit_lbs_hr", float("inf"))
        so2_limit = context.get("so2_limit_lbs_hr", float("inf"))
        pm25_naaqs = context.get("pm25_naaqs_ug_m3", 35.0)

        if nox_lbs > nox_limit:
            v.append(f"NOx Exceedance: {nox_lbs:.2f} lb/hr exceeds permit limit {nox_limit:.2f} lb/hr.")
        if so2_lbs > so2_limit:
            v.append(f"SO2 Exceedance: {so2_lbs:.2f} lb/hr exceeds permit limit {so2_limit:.2f} lb/hr.")
        if pm25_ug_m3 > pm25_naaqs:
            v.append(f"PM2.5 NAAQS: {pm25_ug_m3:.1f} µg/m³ exceeds 24-hour NAAQS of {pm25_naaqs:.1f} µg/m³.")
        if not cems_calibrated:
            v.append("CEMS: Continuous Emissions Monitoring System calibration not current.")
        if excess_hours > 0 and not excess_reported:
            v.append(f"Excess Emissions: {excess_hours}h of excess emissions not reported to EPA as required.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class HOSVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        driving_hours = output.get("driving_hours_today", 0.0)
        window_hours = output.get("on_duty_window_hours", 0.0)
        break_taken = output.get("30min_break_taken", False)
        weekly_hours = output.get("weekly_on_duty_hours", 0.0)
        driving_before_break = output.get("driving_hours_before_break", 0.0)
        sleeper_berth_split = output.get("sleeper_berth_split_valid", True)

        if driving_hours > 11:
            v.append(f"HOS 395.3(a)(3)(i): {driving_hours:.1f}h driving exceeds 11-hour limit.")
        if window_hours > 14:
            v.append(f"HOS 395.3(a)(2): on-duty window {window_hours:.1f}h exceeds 14-hour limit.")
        if driving_before_break > 8 and not break_taken:
            v.append(f"HOS 395.3(a)(3)(ii): {driving_before_break:.1f}h driven without required 30-minute break.")
        if weekly_hours > 70:
            v.append(f"HOS 395.3(b)(2): {weekly_hours:.1f}h weekly on-duty exceeds 70-hour/8-day limit.")
        if not sleeper_berth_split:
            v.append("HOS 395.1(g): sleeper berth split does not meet 8/2 or 7/3 requirement.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class AirworthinessVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        open_ads = output.get("open_airworthiness_directives", [])
        overdue_mx = output.get("overdue_maintenance_items", [])
        mel_items = output.get("mel_items_open", [])
        mel_days_open = output.get("mel_max_days_open", 0)
        mel_category = output.get("mel_category", "C")
        gross_weight = output.get("gross_weight_lbs", 0)
        max_gross = output.get("max_gross_weight_lbs", 0)
        cg_pct = output.get("cg_percent_mac", 0.0)
        cg_min = output.get("cg_min_pct_mac", 0.0)
        cg_max = output.get("cg_max_pct_mac", 100.0)
        certificate_current = output.get("airworthiness_certificate_current", False)
        mel_limits = {"A": 3, "B": 10, "C": 120, "D": 0}

        if open_ads:
            v.append(f"Airworthiness Directives: {len(open_ads)} AD(s) not complied with: {open_ads}.")
        if overdue_mx:
            v.append(f"Maintenance Overdue: {len(overdue_mx)} scheduled item(s) past due: {overdue_mx}.")
        if mel_items:
            limit = mel_limits.get(mel_category, 120)
            if limit == 0:
                v.append(f"MEL Category D: {len(mel_items)} item(s) open; Category D requires repair before flight.")
            elif mel_days_open > limit:
                v.append(f"MEL Expired: Category {mel_category} items open {mel_days_open}d; limit is {limit}d.")
        if max_gross > 0 and gross_weight > max_gross:
            v.append(f"Weight: gross weight {gross_weight:,.0f} lb exceeds maximum {max_gross:,.0f} lb.")
        if not (cg_min <= cg_pct <= cg_max):
            v.append(f"CG: center of gravity {cg_pct:.1f}% MAC outside envelope {cg_min}–{cg_max}%.")
        if not certificate_current:
            v.append("Airworthiness Certificate: standard airworthiness certificate not current.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)
