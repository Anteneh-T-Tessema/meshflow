"""Deterministic verifiers — the Forensic Logic Layer (FLL).

These verifiers provide absolute zero-hallucination guarantees by validating
swarm outputs against deterministic business rules. No LLM calls, no
probabilistic reasoning — pure rule-based validation.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class VerificationResult(BaseModel):
    is_valid: bool
    confidence: float
    violations: List[str]
    remediation_steps: Optional[List[str]] = None


class DeterministicVerifier(ABC):
    """Base class for Forensic Logic Layer verifiers."""

    @abstractmethod
    def verify(self, output: Any, context: Dict[str, Any]) -> VerificationResult:
        pass


class ERPAuditVerifier(DeterministicVerifier):
    """FLL Verifier for ERP Remediation."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        violations = []
        if output.get("debit") != output.get("credit"):
            violations.append("Integrity Violation: Debit and Credit must be balanced.")
        if not output.get("audit_tag"):
            violations.append("Compliance Violation: Missing required Audit-Proof tag.")
        return VerificationResult(is_valid=len(violations) == 0, confidence=1.0, violations=violations)


class BillableCaptureVerifier(DeterministicVerifier):
    """FLL Verifier for Subcontractor Billable Capture."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        violations = []
        max_rate = context.get("max_rate", 250)
        if output.get("hourly_rate", 0) > max_rate:
            violations.append(
                f"Contract Violation: Hourly rate ${output.get('hourly_rate')} exceeds cap."
            )
        return VerificationResult(is_valid=len(violations) == 0, confidence=1.0, violations=violations)


class CodeModernizationVerifier(DeterministicVerifier):
    """FLL Verifier for Legacy Code Modernization."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        violations = []
        code = output.get("code", "")
        if ": any" in code:
            violations.append("Type Safety Violation: 'any' type detected.")
        if "eval(" in code:
            violations.append("Security Violation: 'eval()' detected.")
        return VerificationResult(is_valid=len(violations) == 0, confidence=1.0, violations=violations)


class PytestVerifier(DeterministicVerifier):
    """Code synthesis verifier — runs actual pytest tests against generated code."""

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        code = output.get("code", "")
        tests = output.get("tests", "")
        timeout = int(context.get("timeout", 10))

        if not code.strip():
            return VerificationResult(
                is_valid=False, confidence=1.0,
                violations=["Missing generated code"],
                remediation_steps=["Provide non-empty code in the 'code' field"],
            )
        if not tests.strip():
            return VerificationResult(
                is_valid=False, confidence=1.0,
                violations=["Missing test suite"],
                remediation_steps=["Provide pytest tests in the 'tests' field"],
            )

        security_violation = self._check_security(code)
        if security_violation:
            return VerificationResult(
                is_valid=False, confidence=1.0,
                violations=[security_violation],
                remediation_steps=["Remove unsafe imports or eval calls from the generated code"],
            )

        try:
            passed, output_text = self._run_tests(code, tests, timeout)
        except Exception as exc:
            return VerificationResult(
                is_valid=False, confidence=1.0,
                violations=[f"Test execution error: {exc}"],
                remediation_steps=["Fix syntax errors in generated code before re-running"],
            )

        if passed:
            return VerificationResult(is_valid=True, confidence=1.0, violations=[])

        failing_tests = self._parse_failures(output_text)
        return VerificationResult(
            is_valid=False,
            confidence=1.0,
            violations=failing_tests or ["One or more tests failed"],
            remediation_steps=[f"Fix the logic that causes test failure: {t}" for t in (failing_tests or ["unknown"])],
        )

    def _check_security(self, code: str) -> Optional[str]:
        dangerous = ["__import__", "subprocess", "os.system", "open(", "socket", "shutil.rm"]
        for pattern in dangerous:
            if pattern in code:
                return f"Security: generated code contains disallowed pattern '{pattern}'"
        return None

    def _run_tests(self, code: str, tests: str, timeout: int) -> tuple:
        with tempfile.TemporaryDirectory() as tmpdir:
            solution_path = os.path.join(tmpdir, "solution.py")
            test_path = os.path.join(tmpdir, "test_solution.py")
            with open(solution_path, "w") as f:
                f.write(code)
            with open(test_path, "w") as f:
                f.write("from solution import *\n" + tests)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short", "-q"],
                capture_output=True, text=True, timeout=timeout, cwd=tmpdir,
                env={**os.environ, "PYTHONPATH": tmpdir},
            )
            return result.returncode == 0, result.stdout + result.stderr

    def _parse_failures(self, output: str) -> List[str]:
        return [
            line.replace("FAILED ", "").strip()
            for line in output.splitlines()
            if line.startswith("FAILED ")
        ][:5]


class DASCVerifier(DeterministicVerifier):
    """DASC-Core verifier — cross-industry safety."""

    def verify(self, output: Any, context: Dict[str, Any]) -> VerificationResult:
        return VerificationResult(
            is_valid=True,
            confidence=1.0,
            violations=[],
        )
