"""Legal & Compliance verifiers — SOX, GDPR, Contract Obligations, OFAC Sanctions."""
from typing import Any, Dict, List
from meshflow.swarm.verifiers import DeterministicVerifier, VerificationResult


class SOXControlVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        amount = output.get("amount", 0)
        initiator = output.get("initiator_id", "")
        approver = output.get("approver_id", "")
        recorder = output.get("recorder_id", "")
        authority = output.get("approver_authority_limit", 0)
        doc_present = output.get("documentation_present", False)
        recon_days = output.get("reconciliation_days", 0)
        max_recon = output.get("max_reconciliation_days", 30)
        is_override = output.get("is_override", False)
        override_approvers = output.get("override_approvers", [])

        if initiator and approver and initiator == approver:
            v.append(f"Segregation of Duties: initiator and approver are the same person ({initiator}).")
        if approver and recorder and approver == recorder:
            v.append(f"Segregation of Duties: approver and recorder are the same person ({approver}).")
        if initiator and recorder and initiator == recorder:
            v.append(f"Segregation of Duties: initiator and recorder are the same person ({initiator}).")
        if authority > 0 and amount > authority:
            v.append(f"Authorization Limit: ${amount:,.0f} exceeds approver limit of ${authority:,.0f}.")
        if not doc_present:
            v.append("SOX Documentation: supporting documentation not present.")
        if recon_days > max_recon:
            v.append(f"Reconciliation: completed {recon_days}d after period end; maximum is {max_recon}d.")
        if is_override and len(override_approvers) < 2:
            v.append("Management Override: requires dual approval; only one approver recorded.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class GDPRConsentVerifier(DeterministicVerifier):
    VALID_BASES = {
        "consent", "contract", "legal_obligation",
        "vital_interests", "public_task", "legitimate_interests",
    }

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        basis = output.get("lawful_basis", "")
        granular = output.get("consent_granular", False)
        purpose_specified = output.get("purpose_specified", False)
        withdrawal = output.get("withdrawal_mechanism", False)
        collected = set(output.get("data_fields_collected", []))
        required = set(output.get("purpose_required_fields", []))
        freely_given = output.get("consent_freely_given", True)
        unambiguous = output.get("consent_unambiguous", True)

        if basis not in self.VALID_BASES:
            v.append(f"GDPR Art 6: lawful basis '{basis}' not valid.")
        if basis == "consent":
            if not granular:
                v.append("GDPR Art 7: consent must be granular (separate for each purpose).")
            if not freely_given:
                v.append("GDPR Art 7: consent not freely given.")
            if not unambiguous:
                v.append("GDPR Art 7: consent not unambiguous (no pre-ticked boxes).")
            if not withdrawal:
                v.append("GDPR Art 7(3): no withdrawal mechanism as easy as giving consent.")
        if not purpose_specified:
            v.append("GDPR Art 5(1)(b): purpose not specified (purpose limitation).")
        extra_fields = collected - required
        if extra_fields:
            v.append(f"GDPR Art 5(1)(c): data minimisation — unnecessary fields collected: {sorted(extra_fields)}.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class ContractObligationVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        clauses = set(output.get("clauses_present", []))
        payment_days = output.get("payment_days", 0)
        max_payment = output.get("max_payment_days", 30)
        notice_days = output.get("notice_days", 0)
        min_notice = output.get("min_notice_days", 30)
        excl_conflict = output.get("exclusivity_conflict", False)

        required = set(context.get("required_clauses", [
            "indemnification", "limitation_of_liability", "governing_law",
        ]))
        missing = required - clauses
        if missing:
            v.append(f"Missing Clauses: required contract clauses absent: {sorted(missing)}.")
        if payment_days > max_payment:
            v.append(f"Payment Terms: {payment_days}-day payment terms exceed maximum {max_payment} days.")
        if notice_days < min_notice:
            v.append(f"Notice Period: {notice_days}-day notice below minimum {min_notice} days.")
        if excl_conflict:
            v.append("Exclusivity Conflict: contract contains conflicting exclusivity provisions.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)


class SANCtionsScreeningVerifier(DeterministicVerifier):
    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        v = []
        screened = output.get("screening_completed", False)
        sdn_hit = output.get("sdn_hit", False)
        fuzzy_score = output.get("fuzzy_score", 0.0)
        threshold = output.get("fuzzy_threshold", context.get("fuzzy_threshold", 0.85))
        ownership_pct = output.get("ownership_chain_pct", 0.0)
        blocked = output.get("transaction_blocked", False)

        if not screened:
            v.append("OFAC Screening: entity not screened against SDN list.")
        if sdn_hit and not blocked:
            v.append("OFAC Hit: SDN match confirmed but transaction not blocked.")
        if fuzzy_score >= threshold and not sdn_hit:
            v.append(f"OFAC Fuzzy Match: score {fuzzy_score:.2f} ≥ threshold {threshold} requires manual review.")
        if ownership_pct < 50.0 and ownership_pct > 0:
            v.append(f"OFAC 50% Rule: ownership chain only traced to {ownership_pct:.0f}%; must trace to ≥50% or clear.")

        return VerificationResult(is_valid=len(v) == 0, confidence=1.0, violations=v)
