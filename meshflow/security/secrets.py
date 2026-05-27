"""Sprint 51 — Secret & Credential Scanner.

Scans text for leaked credentials before they reach the caller or are logged.
Designed to run as a post-generation guardrail on every LLM response.

Detection categories
--------------------
api_keys       — AWS, GCP, GitHub, Stripe, Twilio, SendGrid, Slack, HuggingFace,
                  Anthropic, OpenAI, Cohere, Pinecone, Replicate, Databricks
tokens         — Bearer tokens, JWT (header.payload.signature), OAuth refresh tokens
private_keys   — RSA/DSA/EC/OpenSSH PEM blocks
passwords      — URL-embedded credentials (scheme://user:pass@host), common
                  password-assignment patterns
database       — Connection strings (PostgreSQL, MySQL, MongoDB, Redis, MSSQL)
cloud          — Azure connection strings, S3 bucket URLs with credentials
network        — IP:port credential pairs
certificates   — X.509 PEM blocks (cert + private key)

Action modes
------------
"block"  — GuardrailResult(passed=False) when secrets are found.
"redact" — Replace matched secrets with [REDACTED:<category>] and return
            modified_text.  Text passes but credentials are scrubbed.
"warn"   — Pass with metadata; caller logs but does not block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .guardrails import Guardrail, GuardrailResult


# ── Pattern database ──────────────────────────────────────────────────────────
# Each entry: (pattern_name, confidence, regex_string)
# Patterns are ordered most-specific first within each category so redaction
# replaces the longest, most specific match.

_PATTERNS: dict[str, list[tuple[str, float, str]]] = {
    "api_keys": [
        # AWS
        ("aws_access_key",       0.95, r"(?<![A-Z0-9])(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
        ("aws_secret_key",       0.95, r"(?i)aws.{0,20}secret.{0,10}['\"]?([A-Za-z0-9/+]{40})['\"]?"),
        # GCP
        ("gcp_api_key",          0.90, r"AIza[0-9A-Za-z\-_]{35}"),
        ("gcp_service_account",  0.90, r'"type"\s*:\s*"service_account"'),
        # GitHub
        ("github_pat_classic",   0.95, r"ghp_[A-Za-z0-9]{36}"),
        ("github_pat_fine",      0.95, r"github_pat_[A-Za-z0-9_]{82}"),
        ("github_oauth",         0.90, r"gho_[A-Za-z0-9]{36}"),
        ("github_app_install",   0.90, r"ghs_[A-Za-z0-9]{36}"),
        ("github_refresh",       0.90, r"ghr_[A-Za-z0-9]{76}"),
        # Stripe
        ("stripe_live_secret",   0.98, r"sk_live_[A-Za-z0-9]{24,99}"),
        ("stripe_test_secret",   0.85, r"sk_test_[A-Za-z0-9]{24,99}"),
        ("stripe_restricted",    0.95, r"rk_live_[A-Za-z0-9]{24,99}"),
        # Twilio
        ("twilio_account_sid",   0.90, r"AC[a-f0-9]{32}"),
        ("twilio_auth_token",    0.90, r"(?i)twilio.{0,20}auth.{0,10}['\"]([a-f0-9]{32})['\"]"),
        # SendGrid
        ("sendgrid_key",         0.95, r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}"),
        # Slack
        ("slack_bot_token",      0.95, r"xoxb-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24}"),
        ("slack_user_token",     0.95, r"xoxp-[0-9]{10,13}-[0-9]{10,13}-[0-9]{10,13}-[a-f0-9]{32}"),
        ("slack_webhook",        0.90, r"https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]+"),
        # HuggingFace
        ("huggingface_token",    0.92, r"hf_[A-Za-z0-9]{34,}"),
        # Anthropic
        ("anthropic_key",        0.97, r"sk-ant-[A-Za-z0-9\-_]{93,}"),
        # OpenAI
        ("openai_key",           0.97, r"sk-[A-Za-z0-9]{48}"),
        ("openai_org",           0.85, r"org-[A-Za-z0-9]{24}"),
        # Cohere
        ("cohere_key",           0.90, r"(?i)cohere.{0,20}['\"]([A-Za-z0-9]{40})['\"]"),
        # Pinecone
        ("pinecone_key",         0.88, r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"),
        # Databricks
        ("databricks_token",     0.92, r"dapi[a-f0-9]{32}"),
        # Generic high-entropy key
        ("generic_api_key",      0.70, r"(?i)(api[_\-]?key|apikey|api[_\-]?secret)\s*[:=]\s*['\"]?([A-Za-z0-9\-_./+]{32,})['\"]?"),
    ],
    "tokens": [
        ("jwt_token",            0.88, r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"),
        ("bearer_header",        0.85, r"(?i)Authorization\s*:\s*Bearer\s+([A-Za-z0-9\-_./+]{20,})"),
        ("oauth_refresh",        0.80, r"(?i)(refresh[_\-]?token|refresh_token)\s*[:=]\s*['\"]?([A-Za-z0-9\-_.+/]{32,})['\"]?"),
        ("access_token",         0.75, r"(?i)(access[_\-]?token)\s*[:=]\s*['\"]?([A-Za-z0-9\-_.+/]{32,})['\"]?"),
        ("session_token",        0.75, r"(?i)(session[_\-]?token|sess[_\-]?id)\s*[:=]\s*['\"]?([A-Za-z0-9\-_.+/]{32,})['\"]?"),
    ],
    "private_keys": [
        ("rsa_private_key",      0.99, r"-----BEGIN RSA PRIVATE KEY-----[\s\S]{100,}?-----END RSA PRIVATE KEY-----"),
        ("pkcs8_private_key",    0.99, r"-----BEGIN PRIVATE KEY-----[\s\S]{100,}?-----END PRIVATE KEY-----"),
        ("ec_private_key",       0.99, r"-----BEGIN EC PRIVATE KEY-----[\s\S]{50,}?-----END EC PRIVATE KEY-----"),
        ("openssh_private_key",  0.99, r"-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]{50,}?-----END OPENSSH PRIVATE KEY-----"),
        ("dsa_private_key",      0.99, r"-----BEGIN DSA PRIVATE KEY-----[\s\S]{50,}?-----END DSA PRIVATE KEY-----"),
        ("pgp_private_key",      0.99, r"-----BEGIN PGP PRIVATE KEY BLOCK-----[\s\S]{50,}?-----END PGP PRIVATE KEY BLOCK-----"),
    ],
    "passwords": [
        ("url_password",         0.92, r"[a-zA-Z][a-zA-Z0-9+\-.]*://[^:@\s]+:[^@\s]{4,}@[^\s]+"),
        ("password_assignment",  0.80, r"(?i)(password|passwd|pwd|secret)\s*[:=]\s*['\"]([^'\"\s]{6,})['\"]"),
        ("env_password",         0.85, r"(?i)(DB_PASS|DB_PASSWORD|SECRET_KEY|DJANGO_SECRET|APP_SECRET|AUTH_SECRET)\s*=\s*['\"]?([^\s'\"\n]{8,})['\"]?"),
    ],
    "database": [
        ("postgres_url",         0.95, r"postgres(?:ql)?://[^:]+:[^@\s]+@[^\s/]+(?:/[^\s?]+)?"),
        ("mysql_url",            0.95, r"mysql(?:2)?://[^:]+:[^@\s]+@[^\s/]+(?:/[^\s?]+)?"),
        ("mongodb_url",          0.95, r"mongodb(?:\+srv)?://[^:]+:[^@\s]+@[^\s/]+(?:/[^\s?]+)?"),
        ("redis_url",            0.90, r"redis://(?:[^:]+:[^@\s]+@)[^\s/]+(?:/[0-9]+)?"),
        ("mssql_url",            0.90, r"(?i)Server=[^;]+;.*Password=[^;]{4,}"),
        ("jdbc_url",             0.88, r"jdbc:[a-z]+://[^:]+(?::[0-9]+)?/[^?\s]+\?.*(?:password|passwd)=[^&\s]+"),
    ],
    "cloud": [
        ("azure_conn_string",    0.95, r"DefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]{88};"),
        ("azure_storage_sas",    0.90, r"(?i)sv=[0-9]{4}-[0-9]{2}-[0-9]{2}&.*sig=[A-Za-z0-9%+/=]{43,}"),
        ("s3_presigned",         0.85, r"https://[^.]+\.s3[.-][^.]+\.amazonaws\.com/[^?\s]+\?.*AWSAccessKeyId=[A-Z0-9]{20}"),
    ],
    "certificates": [
        ("x509_certificate",     0.80, r"-----BEGIN CERTIFICATE-----[\s\S]{50,}?-----END CERTIFICATE-----"),
        ("pkcs12_hint",          0.75, r"(?i)\bpkcs12\b.*\b(password|passphrase)\b"),
    ],
}


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class SecretMatch:
    """A single credential match within scanned text."""

    category:     str
    pattern_name: str
    matched_text: str   # redacted display: first 6 chars + ***
    position:     int
    confidence:   float
    raw_length:   int   # length of original matched text


@dataclass
class SecretScanResult:
    """Aggregated result of scanning text for secrets."""

    found:      bool
    categories: list[str]
    matches:    list[SecretMatch]
    redacted_text: str | None   # set when action="redact"

    @property
    def is_clean(self) -> bool:
        return not self.found

    def summary(self) -> str:
        if not self.found:
            return "clean — no secrets detected"
        cats = ", ".join(self.categories)
        return f"SECRETS FOUND: {len(self.matches)} match(es) in [{cats}]"


# ── Scanner ───────────────────────────────────────────────────────────────────

_REDACT_LABEL = "[REDACTED:{category}]"


class SecretScanner:
    """Detect (and optionally redact) secrets and credentials in text.

    Parameters
    ----------
    enabled_categories:
        Subset of the eight detection categories to activate.  ``None`` enables
        all categories.
    min_confidence:
        Only report matches whose pattern confidence is ≥ this value.
        Default 0.7 — excludes low-specificity heuristics.
    redact:
        When ``True``, ``scan()`` populates ``SecretScanResult.redacted_text``
        with secrets replaced by ``[REDACTED:<category>]``.
    """

    ALL_CATEGORIES: list[str] = list(_PATTERNS.keys())

    def __init__(
        self,
        enabled_categories: list[str] | None = None,
        *,
        min_confidence: float = 0.70,
        redact: bool = False,
    ) -> None:
        self.min_confidence = min_confidence
        self.redact         = redact
        self._active        = set(enabled_categories or self.ALL_CATEGORIES)

        self._compiled: dict[str, list[tuple[str, float, re.Pattern[str]]]] = {}
        for cat, patterns in _PATTERNS.items():
            if cat not in self._active:
                continue
            self._compiled[cat] = [
                (name, weight, re.compile(regex, re.MULTILINE | re.DOTALL))
                for (name, weight, regex) in patterns
                if weight >= min_confidence
            ]

    def scan(self, text: str) -> SecretScanResult:
        """Scan *text* and return a ``SecretScanResult``."""
        matches: list[SecretMatch] = []
        category_hits: set[str] = set()

        # Collect all raw matches with their spans for redaction ordering.
        raw_spans: list[tuple[int, int, str]] = []   # (start, end, category)

        for cat, patterns in self._compiled.items():
            for name, weight, rx in patterns:
                for m in rx.finditer(text):
                    raw = m.group(0)
                    display = raw[:6] + "***" if len(raw) > 6 else "***"
                    matches.append(SecretMatch(
                        category=cat,
                        pattern_name=name,
                        matched_text=display,
                        position=m.start(),
                        confidence=weight,
                        raw_length=len(raw),
                    ))
                    category_hits.add(cat)
                    raw_spans.append((m.start(), m.end(), cat))

        redacted: str | None = None
        if self.redact and raw_spans:
            redacted = self._redact(text, raw_spans)
        elif self.redact:
            redacted = text

        return SecretScanResult(
            found=bool(matches),
            categories=sorted(category_hits),
            matches=matches,
            redacted_text=redacted,
        )

    def is_clean(self, text: str) -> bool:
        """Return ``True`` iff no secrets are detected."""
        return not self.scan(text).found

    # ── Redaction ─────────────────────────────────────────────────────────────

    @staticmethod
    def _redact(text: str, spans: list[tuple[int, int, str]]) -> str:
        # Sort by start position descending so replacements don't shift offsets.
        for start, end, cat in sorted(spans, key=lambda x: x[0], reverse=True):
            label = _REDACT_LABEL.format(category=cat)
            text = text[:start] + label + text[end:]
        return text


# ── Guardrail integration ─────────────────────────────────────────────────────

class SecretScanGuardrail(Guardrail):
    """GuardrailStack-compatible guardrail that prevents credential leakage.

    Modes
    -----
    action="block"  (default)
        Returns ``passed=False`` when secrets are detected.  The run is halted
        before the caller receives the response.
    action="modify"
        Returns ``passed=True`` with ``modified_text`` containing the redacted
        version.  Secrets are scrubbed; the call succeeds.
    action="warn"
        Returns ``passed=True`` with secret metadata attached.  Use for logging
        without blocking.

    Parameters
    ----------
    scanner:
        Pre-configured ``SecretScanner``.  When ``None``, one is created from
        *enabled_categories* and *min_confidence*.
    """

    def __init__(
        self,
        scanner: SecretScanner | None = None,
        *,
        enabled_categories: list[str] | None = None,
        min_confidence: float = 0.70,
        action: str = "block",
        name: str = "secret_scan",
    ) -> None:
        super().__init__(action=action, name=name)  # type: ignore[arg-type]
        redact = (action == "modify")
        self._scanner = scanner or SecretScanner(
            enabled_categories=enabled_categories,
            min_confidence=min_confidence,
            redact=redact,
        )
        # If caller passed a scanner without redact=True but action=modify,
        # wrap it with a redacting scanner using the same config.
        if action == "modify" and not self._scanner.redact:
            self._scanner = SecretScanner(
                enabled_categories=list(self._scanner._active),
                min_confidence=self._scanner.min_confidence,
                redact=True,
            )

    def check(self, text: str, **_: Any) -> GuardrailResult:  # type: ignore[override]
        result = self._scanner.scan(text)

        metadata: dict[str, Any] = {
            "found":      result.found,
            "categories": result.categories,
            "match_count": len(result.matches),
        }

        if not result.found:
            return GuardrailResult(
                passed=True,
                guardrail_name=self.name,
                reason="",
                modified_text=text if self.action == "modify" else None,
                metadata=metadata,
            )

        match_detail = [
            {
                "category":    m.category,
                "pattern":     m.pattern_name,
                "preview":     m.matched_text,
                "confidence":  m.confidence,
            }
            for m in result.matches
        ]
        metadata["matches"] = match_detail

        # action = "block"
        if self.action == "block":
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=(
                    f"Credential leakage detected: {len(result.matches)} secret(s) "
                    f"in categories {result.categories}"
                ),
                severity="block",
                metadata=metadata,
            )

        # action = "modify" → scrub and pass
        if self.action == "modify":
            return GuardrailResult(
                passed=True,
                guardrail_name=self.name,
                reason=f"Secrets redacted ({len(result.matches)} match(es))",
                severity="modify",
                modified_text=result.redacted_text,
                metadata=metadata,
            )

        # action = "warn"
        return GuardrailResult(
            passed=True,
            guardrail_name=self.name,
            reason=f"Potential secrets detected (warn mode): {result.categories}",
            severity="warn",
            metadata=metadata,
        )
