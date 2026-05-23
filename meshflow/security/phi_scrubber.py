"""PHI scrubber for HIPAA-compliant audit trails.

Replaces protected health information with [PHI-REDACTED] before ledger writes.
Patterns cover the 18 HIPAA Safe Harbor identifiers where detectable via regex.
"""

from __future__ import annotations

import re


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Names (basic heuristic: two or more capitalised words)
    ("NAME", re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b")),
    # SSN — 123-45-6789 or 123456789
    ("SSN", re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")),
    # US phone numbers (multiple formats)
    (
        "PHONE",
        re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"),
    ),
    # Dates — common formats: MM/DD/YYYY, YYYY-MM-DD, DD Month YYYY, Month DD YYYY
    (
        "DATE",
        re.compile(
            r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
            r"|\d{4}[/\-]\d{1,2}[/\-]\d{1,2}"
            r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
            r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
            re.IGNORECASE,
        ),
    ),
    # Age ≥ 90 (must be retained as-is per Safe Harbor only if < 90)
    ("AGE_GTE90", re.compile(r"\b(9\d|1[0-1]\d|120)\s*(?:years?\s+old|y/?o)\b", re.IGNORECASE)),
    # US ZIP codes (5-digit or 5+4)
    ("ZIP", re.compile(r"\b\d{5}(?:-\d{4})?\b")),
    # Email addresses
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # URLs that might contain patient IDs
    ("URL", re.compile(r"https?://[^\s]+")),
    # IP addresses
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # MRN — common labels followed by digits
    (
        "MRN",
        re.compile(
            r"(?:MRN|Medical\s+Record(?:\s+Number)?|Patient\s+ID|Acct(?:ount)?(?:\s+No\.?)?)\s*[:\-#]?\s*([A-Z0-9\-]+)",
            re.IGNORECASE,
        ),
    ),
    # NPI (10-digit national provider identifier)
    ("NPI", re.compile(r"\b(?:NPI\s*[:\-]?\s*)?\b(80[0-9]{9}|[1-9]\d{9})\b")),
    # DEA numbers — 2 letters + 7 digits
    ("DEA", re.compile(r"\b[A-Z]{2}\d{7}\b")),
    # Credit/debit card numbers
    ("CARD", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    # Biometric data labels
    (
        "BIOMETRIC",
        re.compile(
            r"\b(?:fingerprint|retinal scan|iris scan|voiceprint|genome|DNA)\b",
            re.IGNORECASE,
        ),
    ),
]

_REPLACEMENT = "[PHI-REDACTED]"


class PHIScrubber:
    """Scrubs PHI from text before it is persisted to the ledger.

    Safe-Harbor method: replaces all 18 identifier categories detectable by
    pattern matching. Names use a conservative heuristic (consecutive title-cased
    words) to reduce false negatives at the cost of some false positives.
    """

    def __init__(self, replacement: str = _REPLACEMENT) -> None:
        self._replacement = replacement

    def scrub(self, text: str) -> str:
        if not text:
            return text
        result = text
        for _label, pattern in _PATTERNS:
            result = pattern.sub(self._replacement, result)
        return result

    def scrub_dict(self, data: dict[str, object]) -> dict[str, object]:
        return {k: self.scrub(v) if isinstance(v, str) else v for k, v in data.items()}
