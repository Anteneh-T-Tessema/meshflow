"""HR, Technology & Retail verifiers — Labor, Benefits, Security, SLA, Pricing, Consumer."""
from typing import Any, Dict
from meshflow.swarm.verifiers import DeterministicVerifier, VerificationResult


class FLSAOvertimeVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        hours_worked = output.get("hours_worked", 0.0)
        ot_pay = output.get("overtime_pay", 0.0)
        hourly_rate = output.get("hourly_rate", 0.0)
        is_exempt = output.get("flsa_exempt", False)
        exempt_basis = output.get("exempt_basis", "")
        salary_level = output.get("weekly_salary", 0.0)
        tip_credit_claimed = output.get("tip_credit_claimed", False)
        tipped_wages = output.get("tipped_employee_total_wages", 0.0)

        federal_min_wage = context.get("federal_min_wage", 7.25)
        min_exempt_salary = context.get("min_exempt_salary_weekly", 684.0)
        valid_exempt_bases = {"executive", "administrative", "professional", "computer", "highly_compensated", "outside_sales"}

        ot_hours = max(0, hours_worked - 40)
        if not is_exempt and ot_hours > 0:
            expected_ot = round(ot_hours * hourly_rate * 1.5, 2)
            if abs(ot_pay - expected_ot) > 0.01:
                v.append(f"FLSA OT: paid ${ot_pay:.2f} for {ot_hours:.1f}h OT; owed ${expected_ot:.2f} at 1.5× rate.")
        if is_exempt and exempt_basis not in valid_exempt_bases:
            v.append(f"FLSA Exemption: basis '{exempt_basis}' not a recognized white-collar exemption.")
        if is_exempt and salary_level < min_exempt_salary:
            v.append(f"FLSA Salary Basis: ${salary_level:.2f}/wk below ${min_exempt_salary:.2f} minimum for exemption.")
        if tip_credit_claimed and tipped_wages < federal_min_wage:
            v.append(f"Tip Credit: total wages ${tipped_wages:.2f}/hr below minimum wage ${federal_min_wage}/hr after tips.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class ACAAffordabilityVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        ee_premium = output.get("employee_premium_monthly", 0.0)
        w2_wages = output.get("w2_wages_annual", 0.0)
        min_value_pct = output.get("plan_min_value_pct", 0.0)
        fte_count = output.get("fte_count", 0)
        filed_1094c = output.get("filed_1094c", False)
        offer_made = output.get("offer_of_coverage_made", False)

        affordability_threshold = context.get("affordability_threshold_pct", 9.12) / 100.0
        min_value_required = context.get("min_value_required_pct", 60.0)
        ale_threshold = context.get("ale_threshold_fte", 50)

        if w2_wages > 0 and (ee_premium * 12) / w2_wages > affordability_threshold:
            annualized = ee_premium * 12
            v.append(f"ACA Affordability: employee premium ${annualized:,.0f}/yr is {annualized/w2_wages:.1%} of W-2 wages.")
        if min_value_pct < min_value_required:
            v.append(f"ACA Min Value: plan covers {min_value_pct:.0f}% of costs; minimum value requires {min_value_required:.0f}%.")
        if fte_count >= ale_threshold and not filed_1094c:
            v.append(f"ACA 1094-C: applicable large employer ({fte_count} FTEs) must file Form 1094-C.")
        if fte_count >= ale_threshold and not offer_made:
            v.append(f"ACA Employer Mandate: ALE ({fte_count} FTEs) must offer minimum essential coverage.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class EEOCPayEquityVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        comp_ratio = output.get("compensation_ratio", 1.0)
        job_title_same = output.get("same_job_title", False)
        skill_equiv = output.get("skills_equivalent", False)
        explained_factors = output.get("explained_by_legitimate_factors", False)
        eeo1_filed = output.get("eeo1_filed", False)
        employee_count = output.get("employee_count", 0)
        max_gap_threshold = context.get("max_unexplained_gap_pct", 5.0) / 100.0
        eeo1_threshold = context.get("eeo1_filing_threshold", 100)

        if job_title_same and skill_equiv and not explained_factors:
            gap = abs(1.0 - comp_ratio)
            if gap > max_gap_threshold:
                v.append(f"Pay Gap: {gap:.1%} compensation disparity for comparable work not explained by legitimate factors.")
        if employee_count >= eeo1_threshold and not eeo1_filed:
            v.append(f"EEO-1 Filing: employer with {employee_count} employees must file annual EEO-1 report.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class OWASPSecurityVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        findings = output.get("security_findings", [])
        critical_open = output.get("critical_vulns_open", 0)
        high_open = output.get("high_vulns_open", 0)
        last_scan_days = output.get("days_since_last_scan", 0)
        waf_enabled = output.get("waf_enabled", False)
        secrets_in_code = output.get("secrets_in_code", False)
        tls_version = output.get("min_tls_version", "1.3")
        max_scan_days = context.get("max_days_between_scans", 30)
        owasp_critical = {"A01:2021", "A02:2021", "A03:2021", "A07:2021"}

        critical_findings = [f for f in findings if f in owasp_critical]
        if critical_findings:
            v.append(f"OWASP Critical: unresolved OWASP Top-10 critical findings: {critical_findings}.")
        if critical_open > 0:
            v.append(f"Vulnerability: {critical_open} critical vulnerability(ies) open and unmitigated.")
        if high_open > 5:
            v.append(f"Vulnerability: {high_open} high-severity vulnerabilities open; remediation required.")
        if last_scan_days > max_scan_days:
            v.append(f"Scan Currency: last security scan {last_scan_days}d ago; required every {max_scan_days}d.")
        if not waf_enabled:
            v.append("WAF: Web Application Firewall not enabled for public-facing application.")
        if secrets_in_code:
            v.append("Secrets Exposure: credentials or API keys detected in source code repository.")
        if tls_version in {"1.0", "1.1"}:
            v.append(f"TLS: minimum TLS version {tls_version} is deprecated; require TLS 1.2 or higher.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class SLAComplianceVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        uptime_pct = output.get("uptime_pct", 100.0)
        rto_hours = output.get("actual_rto_hours", 0.0)
        rpo_hours = output.get("actual_rpo_hours", 0.0)
        p1_response_mins = output.get("p1_response_time_mins", 0)
        penalty_credited = output.get("penalty_credited", 0.0)
        sla_uptime = context.get("sla_uptime_pct", 99.9)
        max_rto = context.get("max_rto_hours", 4.0)
        max_rpo = context.get("max_rpo_hours", 1.0)
        max_p1_response = context.get("max_p1_response_mins", 15)
        penalty_rate = context.get("penalty_per_pct_below", 10.0)

        if uptime_pct < sla_uptime:
            gap = sla_uptime - uptime_pct
            expected_penalty = round(gap * penalty_rate, 2)
            v.append(f"SLA Breach: uptime {uptime_pct:.3f}% below SLA {sla_uptime:.3f}%.")
            if abs(penalty_credited - expected_penalty) > 0.01:
                v.append(f"Penalty Error: credit ${penalty_credited:.2f}; owed ${expected_penalty:.2f}.")
        if rto_hours > max_rto:
            v.append(f"RTO Breach: recovery time {rto_hours:.1f}h exceeds SLA maximum {max_rto:.1f}h.")
        if rpo_hours > max_rpo:
            v.append(f"RPO Breach: recovery point {rpo_hours:.1f}h exceeds SLA maximum {max_rpo:.1f}h.")
        if p1_response_mins > max_p1_response:
            v.append(f"P1 Response: {p1_response_mins}min exceeds {max_p1_response}min SLA for critical incidents.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class SBOMLicenseVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        components = output.get("components", [])
        sbom_complete = output.get("sbom_complete", False)
        proprietary_product = output.get("is_proprietary_product", True)
        copyleft_licenses = context.get("copyleft_licenses", ["GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1"])
        incompatible_pairs = context.get("incompatible_license_pairs", [])

        if not sbom_complete:
            v.append("SBOM: software bill of materials is incomplete — all dependencies must be listed.")
        all_licenses = []
        for comp in components:
            lic = comp.get("license", "")
            pn = comp.get("name", "")
            all_licenses.append(lic)
            if proprietary_product and lic in copyleft_licenses:
                v.append(f"Copyleft Contamination: component '{pn}' uses {lic}; incompatible with proprietary distribution.")
        for pair in incompatible_pairs:
            if len(pair) == 2 and pair[0] in all_licenses and pair[1] in all_licenses:
                v.append(f"License Conflict: {pair[0]} and {pair[1]} cannot be combined in the same binary.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class MAPPricingVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        sku = output.get("sku", "")
        advertised_price = output.get("advertised_price", 0.0)
        map_price = output.get("map_price", 0.0)
        channel = output.get("channel", "")
        promo_authorized = output.get("promotional_exception_authorized", False)
        channels = output.get("channel_prices", {})

        if map_price > 0 and advertised_price < map_price and not promo_authorized:
            v.append(f"MAP Violation: SKU {sku} advertised at ${advertised_price:.2f} below MAP ${map_price:.2f} on {channel}.")
        if channels:
            prices = list(channels.values())
            if max(prices) > 0 and (max(prices) - min(prices)) / max(prices) > 0.10:
                v.append(f"Price Parity: SKU {sku} price spread exceeds 10% parity threshold.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class ProductSafetyVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        age_grade = output.get("age_grade_years", 0)
        small_parts = output.get("contains_small_parts", False)
        standards_passed = set(output.get("standards_passed", []))
        standards_required = set(output.get("standards_required", []))
        recall_active = output.get("recall_active", False)
        lead_ppm = output.get("lead_content_ppm", 0)
        phthalate_pct = output.get("phthalate_pct", 0.0)
        lead_limit = context.get("lead_limit_ppm", 100)
        phthalate_limit = context.get("phthalate_limit_pct", 0.1)

        if age_grade <= 3 and small_parts:
            v.append("CPSC 16 CFR 1501: product graded ≤3 years contains small parts — choking hazard.")
        missing_std = standards_required - standards_passed
        if missing_std:
            v.append(f"Standards: product has not passed required standards: {sorted(missing_std)}.")
        if recall_active:
            v.append("CPSC Recall: product subject to an active CPSC recall — cannot be sold.")
        if lead_ppm > lead_limit:
            v.append(f"Lead Content: {lead_ppm}ppm exceeds CPSC limit of {lead_limit}ppm.")
        if phthalate_pct > phthalate_limit:
            v.append(f"Phthalates: {phthalate_pct:.2f}% exceeds CPSC limit of {phthalate_limit:.2f}%.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class LoyaltyProgramVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        spend = output.get("qualifying_spend", 0.0)
        points_awarded = output.get("points_awarded", 0)
        points_per_dollar = context.get("points_per_dollar", 1)
        tier = output.get("tier_assigned", "")
        tier_spend = output.get("tier_qualifying_spend_ytd", 0.0)
        redeemed = output.get("points_redeemed", 0)
        balance_before = output.get("points_balance_before", 0)
        balance_after = output.get("points_balance_after", 0)
        expired_points = output.get("expired_points", 0)
        months_inactive = output.get("months_inactive", 0)
        expiry_months = context.get("expiry_after_inactive_months", 12)
        tier_thresholds = context.get("tier_thresholds", {"silver": 500, "gold": 1000, "platinum": 5000})

        expected_points = int(spend * points_per_dollar)
        if abs(points_awarded - expected_points) > 1:
            v.append(f"Points Math: awarded {points_awarded} points; expected {expected_points} for ${spend:.2f} spend.")
        if tier and tier.lower() in tier_thresholds:
            required = tier_thresholds[tier.lower()]
            if tier_spend < required:
                v.append(f"Tier Qualification: assigned {tier} tier but YTD spend ${tier_spend:.0f} < required ${required:.0f}.")
        expected_after = balance_before + points_awarded - redeemed - expired_points
        if abs(balance_after - expected_after) > 1:
            v.append(f"Balance Error: balance {balance_after} ≠ expected {expected_after}.")
        if redeemed > balance_before:
            v.append(f"Redemption Limit: attempted to redeem {redeemed} points but balance was only {balance_before}.")
        if months_inactive >= expiry_months and expired_points == 0 and balance_before > 0:
            v.append(f"Expiry: account inactive {months_inactive} months (≥{expiry_months}); points should have expired.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)
