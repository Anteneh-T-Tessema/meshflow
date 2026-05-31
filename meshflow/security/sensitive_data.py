"""Sensitive data detection and masking — PHI, PII, and credential patterns.

SensitiveDataDetector goes beyond PHIScrubber by returning rich SensitiveMatch
objects so callers can audit *what* was found (type, location, masked value)
rather than just receiving a scrubbed string.

Credential detection covers common secrets that must never reach LLM context:
  - API keys (Anthropic, OpenAI, AWS, GCP, GitHub)
  - JWT tokens
  - Private/RSA keys
  - Database connection strings with embedded passwords
  - Generic high-entropy tokens (≥40 hex chars)

PHI detection reuses the 18 HIPAA Safe Harbor identifier categories from
phi_scrubber.py, adding structured reporting on top.

Usage::

    from meshflow.security.sensitive_data import SensitiveDataDetector

    detector = SensitiveDataDetector()
    matches = detector.detect("Patient John Smith SSN: 123-45-6789")
    report  = detector.audit_report("Patient John Smith SSN: 123-45-6789")
    masked  = detector.mask("John Smith 123-45-6789")
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ── Match model ───────────────────────────────────────────────────────────────


@dataclass
class SensitiveMatch:
    """A single detected sensitive data instance."""

    kind: str            # e.g. "SSN", "EMAIL", "API_KEY_ANTHROPIC"
    category: str        # "phi" | "pii" | "credential"
    value_preview: str   # first 6 chars + "…" — never the full value
    start: int           # character offset in source text
    end: int             # exclusive end offset
    confidence: float    # 0.0–1.0 (regex = 1.0; heuristic = 0.7)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "category": self.category,
            "value_preview": self.value_preview,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
        }


# ── Pattern definitions ───────────────────────────────────────────────────────

@dataclass
class _Pattern:
    kind: str
    category: str
    regex: re.Pattern[str]
    confidence: float = 1.0


# PHI / PII patterns (HIPAA Safe Harbor + common PII)
_PHI_PATTERNS: list[_Pattern] = [
    _Pattern("SSN",      "phi", re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")),
    _Pattern("EMAIL",    "pii", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    _Pattern("PHONE",    "pii", re.compile(
        r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
    )),
    _Pattern("DATE",     "pii", re.compile(
        r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
        r"|\d{4}[/\-]\d{1,2}[/\-]\d{1,2}"
        r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
        re.IGNORECASE,
    )),
    _Pattern("ZIP",      "pii", re.compile(r"\b\d{5}(?:-\d{4})?\b")),
    _Pattern("IP",       "pii", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    _Pattern("URL",      "pii", re.compile(r"https?://[^\s]+")),
    _Pattern("MRN",      "phi", re.compile(
        r"(?:MRN|Medical\s+Record(?:\s+Number)?|Patient\s+ID|Acct(?:ount)?(?:\s+No\.?)?)"
        r"\s*[:\-#]?\s*([A-Z0-9\-]+)",
        re.IGNORECASE,
    )),
    _Pattern("NPI",      "phi", re.compile(r"\b(?:NPI\s*[:\-]?\s*)?([1-9]\d{9})\b")),
    _Pattern("CREDIT_CARD", "pii", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    _Pattern("NAME",     "pii", re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b"),
             confidence=0.7),
]

# Credential patterns — these are critical: block before they reach LLM context
_CREDENTIAL_PATTERNS: list[_Pattern] = [
    _Pattern("API_KEY_ANTHROPIC", "credential",
             re.compile(r"\bsk-ant-[a-zA-Z0-9\-_]{20,}\b")),
    _Pattern("API_KEY_OPENAI",    "credential",
             re.compile(r"\bsk-[a-zA-Z0-9]{48}\b")),
    _Pattern("API_KEY_GENERIC",   "credential",
             re.compile(r"\b(?:api[_\-]?key|apikey|api_token)\s*[=:]\s*['\"]?([A-Za-z0-9\-_]{20,})['\"]?",
                        re.IGNORECASE), confidence=0.85),
    _Pattern("AWS_ACCESS_KEY",    "credential",
             re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    _Pattern("AWS_SECRET_KEY",    "credential",
             re.compile(r"\b(?:aws[_\-]?secret|secret[_\-]?access[_\-]?key)\s*[=:]\s*['\"]?([A-Za-z0-9/+]{40})['\"]?",
                        re.IGNORECASE), confidence=0.9),
    _Pattern("GITHUB_TOKEN",      "credential",
             re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
    _Pattern("JWT",               "credential",
             re.compile(r"\beyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b")),
    _Pattern("PRIVATE_KEY",       "credential",
             re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    _Pattern("DB_CONN_STRING",    "credential",
             re.compile(
                 r"(?:postgresql|mysql|mongodb|redis|sqlite)://[^\s:]+:[^\s@]+@[^\s]+",
                 re.IGNORECASE,
             )),
    _Pattern("HIGH_ENTROPY_HEX",  "credential",
             re.compile(r"\b[0-9a-f]{40,}\b"), confidence=0.6),
    _Pattern("GCP_KEY",           "credential",
             re.compile(r'"type"\s*:\s*"service_account"')),
    _Pattern("BEARER_TOKEN",      "credential",
             re.compile(r"\bBearer\s+[A-Za-z0-9\-_.~+/]{20,}\b", re.IGNORECASE), confidence=0.8),
]

_ALL_PATTERNS = _PHI_PATTERNS + _CREDENTIAL_PATTERNS

_MASK_CHAR = "[REDACTED]"
_CRED_MASK = "[CREDENTIAL-REDACTED]"


# ── Detector ──────────────────────────────────────────────────────────────────


class SensitiveDataDetector:
    """Detect and mask sensitive data (PHI, PII, credentials) in text.

    ``detect(text)`` returns all matches with position and kind info.
    ``mask(text)``   returns the text with sensitive data replaced.
    ``audit_report(text)`` returns a structured summary for compliance logging.

    Category-specific masking:
      - PHI/PII → "[REDACTED]"
      - credentials → "[CREDENTIAL-REDACTED]"  (stricter label for security teams)
    """

    def __init__(
        self,
        phi_enabled: bool = True,
        credential_enabled: bool = True,
        min_confidence: float = 0.6,
    ) -> None:
        self._patterns = [
            p for p in _ALL_PATTERNS
            if (phi_enabled and p.category in ("phi", "pii"))
            or (credential_enabled and p.category == "credential")
        ]
        self._min_confidence = min_confidence

    def detect(self, text: str) -> list[SensitiveMatch]:
        """Return all sensitive matches found in ``text``, ordered by position."""
        if not text:
            return []
        matches: list[SensitiveMatch] = []
        for pat in self._patterns:
            if pat.confidence < self._min_confidence:
                continue
            for m in pat.regex.finditer(text):
                raw = m.group(0)
                preview = raw[:6] + ("…" if len(raw) > 6 else "")
                matches.append(SensitiveMatch(
                    kind=pat.kind,
                    category=pat.category,
                    value_preview=preview,
                    start=m.start(),
                    end=m.end(),
                    confidence=pat.confidence,
                ))
        matches.sort(key=lambda x: x.start)
        return matches

    def mask(self, text: str) -> str:
        """Return ``text`` with all sensitive data replaced by category-specific tokens."""
        if not text:
            return text
        # Build a single replacement pass to avoid offset shifting
        replacements: list[tuple[int, int, str]] = []
        for pat in self._patterns:
            if pat.confidence < self._min_confidence:
                continue
            for m in pat.regex.finditer(text):
                label = _CRED_MASK if pat.category == "credential" else _MASK_CHAR
                replacements.append((m.start(), m.end(), label))

        if not replacements:
            return text

        # Merge overlapping spans (keep first)
        replacements.sort(key=lambda x: x[0])
        merged: list[tuple[int, int, str]] = []
        for start, end, label in replacements:
            if merged and start < merged[-1][1]:
                continue  # overlaps with previous — skip
            merged.append((start, end, label))

        # Reconstruct
        result = []
        prev = 0
        for start, end, label in merged:
            result.append(text[prev:start])
            result.append(label)
            prev = end
        result.append(text[prev:])
        return "".join(result)

    def has_credentials(self, text: str) -> bool:
        """Return True if text contains any credential pattern match."""
        for pat in self._patterns:
            if pat.category == "credential" and pat.regex.search(text):
                return True
        return False

    def has_phi(self, text: str) -> bool:
        """Return True if text contains any PHI/PII pattern match."""
        for pat in self._patterns:
            if pat.category in ("phi", "pii") and pat.regex.search(text):
                return True
        return False

    def audit_report(self, text: str) -> dict[str, Any]:
        """Return a compliance-ready summary of sensitive data found in text."""
        matches = self.detect(text)
        by_category: dict[str, list[str]] = {}
        for m in matches:
            by_category.setdefault(m.category, []).append(m.kind)

        return {
            "total_matches": len(matches),
            "has_phi": any(m.category == "phi" for m in matches),
            "has_pii": any(m.category == "pii" for m in matches),
            "has_credentials": any(m.category == "credential" for m in matches),
            "kinds_found": sorted({m.kind for m in matches}),
            "by_category": {k: sorted(set(v)) for k, v in by_category.items()},
            "high_confidence_matches": [
                m.to_dict() for m in matches if m.confidence >= 0.9
            ],
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_default_detector: SensitiveDataDetector | None = None


def get_detector() -> SensitiveDataDetector:
    global _default_detector
    if _default_detector is None:
        _default_detector = SensitiveDataDetector()
    return _default_detector


def reset_detector() -> None:
    global _default_detector
    _default_detector = None
