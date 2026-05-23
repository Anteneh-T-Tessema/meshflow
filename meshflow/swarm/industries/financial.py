"""Financial Services verifiers — AML, Covenant, 3-Way Match, PCI-DSS, Insurance, Settlement."""
from typing import Any, Dict, List
from meshflow.swarm.verifiers import DeterministicVerifier, VerificationResult


class AMLTransactionVerifier(DeterministicVerifier):
    """AML/KYC: structuring, CTR, SAR, EDD rules."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        threshold = context.get("reporting_threshold", 10000)
        amount = output.get("amount", 0)
        prior = output.get("prior_24h_amounts", [])
        ctr_filed = output.get("ctr_filed", False)
        sar_filed = output.get("sar_filed", False)
        edd_completed = output.get("edd_completed", False)
        sanctions_match = context.get("sanctions_match", False)
        high_risk = output.get("country_code", "") in context.get("high_risk_countries", [])

        if amount >= threshold and not ctr_filed:
            v.append(f"CTR Violation: cash transaction ${amount:,.0f} >= ${threshold:,.0f} requires CTR filing.")

        total_24h = sum(prior) + amount
        if amount < threshold and all(p < threshold for p in prior) and total_24h >= threshold and len(prior) >= 2:
            v.append(f"Structuring Violation: {len(prior)+1} transactions totalling ${total_24h:,.0f} indicate structured deposits.")

        if sanctions_match and not sar_filed:
            v.append("SAR Violation: sanctions list match detected without SAR filing.")

        if high_risk and not edd_completed:
            v.append(f"EDD Violation: high-risk country {output.get('country_code')} transaction requires Enhanced Due Diligence.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class CovenantComplianceVerifier(DeterministicVerifier):
    """Loan covenant: DSCR, LTV, current ratio, tangible net worth."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        noi = output.get("net_operating_income", 0)
        ads = output.get("annual_debt_service", 1)
        prop_val = output.get("property_value", 1)
        loan_bal = output.get("loan_balance", 0)
        curr_assets = output.get("current_assets", 0)
        curr_liab = output.get("current_liabilities", 1)
        tnw = output.get("tangible_net_worth", 0)

        min_dscr = context.get("min_dscr", 1.25)
        max_ltv = context.get("max_ltv", 0.80)
        min_cr = context.get("min_current_ratio", 1.10)
        min_tnw = context.get("min_tnw", 0)

        dscr = noi / max(ads, 1)
        ltv = loan_bal / max(prop_val, 1)
        cr = curr_assets / max(curr_liab, 1)

        if dscr < min_dscr:
            v.append(f"Covenant Breach: DSCR {dscr:.2f} below minimum {min_dscr}.")
        if ltv > max_ltv:
            v.append(f"Covenant Breach: LTV {ltv:.2%} exceeds maximum {max_ltv:.0%}.")
        if cr < min_cr:
            v.append(f"Covenant Breach: Current ratio {cr:.2f} below minimum {min_cr}.")
        if tnw < min_tnw:
            v.append(f"Covenant Breach: Tangible net worth ${tnw:,.0f} below minimum ${min_tnw:,.0f}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class ThreeWayMatchVerifier(DeterministicVerifier):
    """Procurement: PO/receipt/invoice three-way match."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        tol = context.get("tolerance_pct", 2.0) / 100.0
        po_qty = output.get("po_qty", 0)
        po_price = output.get("po_unit_price", 0)
        recv_qty = output.get("received_qty", 0)
        inv_qty = output.get("invoice_qty", 0)
        inv_price = output.get("invoice_unit_price", 0)
        inv_total = output.get("invoice_total", 0)

        if po_qty > 0 and abs(inv_qty - po_qty) / po_qty > tol:
            v.append(f"Quantity Mismatch: invoice qty {inv_qty} vs PO qty {po_qty} exceeds {tol:.0%} tolerance.")
        if po_price > 0 and abs(inv_price - po_price) / po_price > tol:
            v.append(f"Price Mismatch: invoice price ${inv_price} vs PO price ${po_price} exceeds {tol:.0%} tolerance.")
        if recv_qty < inv_qty:
            v.append(f"Receipt Shortfall: received qty {recv_qty} less than invoice qty {inv_qty}.")
        expected_total = round(inv_qty * inv_price, 2)
        if abs(inv_total - expected_total) > 0.02:
            v.append(f"Math Error: invoice total ${inv_total} ≠ qty×price ${expected_total}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class PCIDSSVerifier(DeterministicVerifier):
    """PCI-DSS: PAN encryption, CVV storage, audit log, MFA."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        max_rotation = context.get("max_key_rotation_days", 365)
        if not output.get("pan_encrypted", False):
            v.append("PCI-DSS Req 3: PAN stored without encryption.")
        if output.get("cvv_stored", True):
            v.append("PCI-DSS Req 3.2: CVV stored post-authorization (prohibited).")
        if not output.get("audit_log_present", False):
            v.append("PCI-DSS Req 10: Audit log missing.")
        if not output.get("mfa_enabled", False):
            v.append("PCI-DSS Req 8.3: Multi-factor authentication not enabled.")
        rotation = output.get("key_rotation_days", max_rotation + 1)
        if rotation > max_rotation:
            v.append(f"PCI-DSS Req 3.5: Encryption key rotation {rotation}d exceeds {max_rotation}d limit.")
        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class InsuranceClaimsVerifier(DeterministicVerifier):
    """Insurance: policy limits, deductible, coverage match, reporting window."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        gross = output.get("gross_loss", 0)
        ded = output.get("deductible", 0)
        prior = output.get("prior_paid", 0)
        limit = output.get("policy_limit", 0)
        net = output.get("net_payment", 0)
        cov_type = output.get("coverage_type", "")
        inc_type = output.get("incident_type", "")
        days = output.get("days_to_report", 0)
        max_days = context.get("reporting_days", 30)

        available = max(0, limit - prior)
        expected_net = round(max(0, min(gross - ded, available)), 2)
        if abs(net - expected_net) > 0.01:
            v.append(f"Payment Error: net payment ${net:,.2f} should be ${expected_net:,.2f}.")
        if cov_type and inc_type and cov_type != inc_type:
            v.append(f"Coverage Mismatch: incident type '{inc_type}' not covered under '{cov_type}' policy.")
        if days > max_days:
            v.append(f"Late Reporting: claim filed {days}d after loss; policy requires ≤{max_days}d.")
        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class TradeSettlementVerifier(DeterministicVerifier):
    """Capital markets: T+2 settlement, amount, CUSIP, counterparty."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        shares = output.get("shares", 0)
        price = output.get("price", 0)
        fees = output.get("fees", 0)
        settle_amt = output.get("settlement_amount", 0)
        cusip = str(output.get("cusip", ""))
        approved = output.get("counterparty_approved", False)
        business_days = output.get("business_days_to_settle", 0)

        if business_days > 2:
            v.append(f"Settlement Fail: {business_days} business days to settlement; T+2 required.")
        expected_amt = round(shares * price - fees, 2)
        if abs(settle_amt - expected_amt) > 0.01:
            v.append(f"Amount Error: settlement ${settle_amt:,.2f} ≠ shares×price-fees ${expected_amt:,.2f}.")
        if len(cusip) != 9 or not cusip.isalnum():
            v.append(f"Invalid CUSIP: '{cusip}' must be 9 alphanumeric characters.")
        if not approved:
            v.append("Counterparty Violation: counterparty not on approved list.")
        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)
